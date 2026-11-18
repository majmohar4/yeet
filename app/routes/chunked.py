"""
Resumable chunked uploads.

Cloudflare (and most proxies) cap a single request body at 100 MB, and one
dropped connection used to throw away an entire upload. Here the client:

  1. POST /api/uploads                       → {id, chunk_size, chunk_count}
  2. PUT  /api/uploads/{id}/chunks/{n}       (raw bytes, any order, retryable)
  3. POST /api/uploads/{id}/complete         → 201 result, or 202 while scanning
  4. GET  /api/uploads/{id}                  → progress / final result

Space for the whole file is allocated at step 1, so a full disk fails fast
with a clear message instead of halfway through. Finalizing (virus scan +
hash) runs as a background task: the client polls instead of holding a request
open, which would hit Cloudflare's 100 s origin timeout on big files or when
clamd has to cold-start.

Folders: POST /api/bundles creates a hidden bundle and a token; each member is
uploaded with `bundle_id` + `bundle_token` + `path`; POST /api/bundles/{id}/
complete publishes it.
"""
import asyncio
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import get_db
from app.middleware.rate_limit import _client_ip, consume_bypass_code, peek_bypass_code
from app.services import ingest, storage, tiers
from app.services.extensions import _check_extension

logger = logging.getLogger("yeet.chunked")
router = APIRouter()

MAX_BUNDLE_FILES = 1000
MAX_NAME_LEN = 255
_COMPLETE_WAIT = 20  # seconds the complete call waits before answering 202

# Strong refs so finalize tasks aren't garbage-collected mid-flight.
_tasks: dict[str, asyncio.Task] = {}
# Live finalize progress per upload: {"phase": str, "progress": 0..1}. Written
# from the scanner's worker thread; plain dict assignment is atomic enough.
_progress: dict[str, dict] = {}


def _safe_hex(value: str) -> str | None:
    if value and len(value) == 32 and all(c in "0123456789abcdef" for c in value):
        return value
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _int(value, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _hash_pw(password: str) -> str | None:
    if not password:
        return None
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


async def _load(upload_id: str) -> dict | None:
    db = await get_db()
    async with db.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def _received(upload_id: str) -> list[int]:
    db = await get_db()
    async with db.execute(
        "SELECT idx FROM upload_chunks WHERE upload_id=? ORDER BY idx", (upload_id,)
    ) as cur:
        return [r[0] for r in await cur.fetchall()]


def _public(row: dict, received: list[int] | None = None) -> dict:
    out = {
        "id": row["id"],
        "status": row["status"],
        "name": row["orig_name"],
        "size": row["file_size"],
        "chunk_size": row["chunk_size"],
        "chunk_count": row["chunk_count"],
    }
    if received is not None:
        out["received"] = received
    if row["status"] == "processing":
        p = _progress.get(row["id"])
        out.update(p or {"phase": "queued", "progress": 0.0})
    if row.get("result"):
        res = json.loads(row["result"])
        out["code"] = res["code"]
        out["result"] = res["body"]
    return out


# ── Single file ───────────────────────────────────────────────────────────────

@router.post("/api/uploads")
async def create_upload(request: Request):
    body = await _json(request)
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")[:300]

    if body.get("website"):  # honeypot — also marks the IP suspicious
        await ingest.audit(ip, ua, "bot_rejected", None, "honeypot")
        return ingest.error("Invalid request.", 400)

    name = str(body.get("name") or "").strip()[:MAX_NAME_LEN] or "file"
    size = _int(body.get("size"))
    if size < 0:
        return ingest.error("Missing or invalid file size.", 400)
    ext_error = _check_extension(name)
    if ext_error:
        return ingest.error(ext_error, 400)

    bypass_code = str(body.get("bypass_code") or "").strip()
    bypassed = await peek_bypass_code(bypass_code)

    db = await get_db()
    async with db.execute(
        "SELECT COUNT(*) FROM uploads WHERE client_ip=? AND status IN ('uploading','processing') "
        "AND updated_at > ?",
        (ip, (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()),
    ) as cur:
        active = (await cur.fetchone())[0]
    if active >= settings.MAX_ACTIVE_UPLOADS and not bypassed:
        return ingest.error(
            "Too many uploads in progress from your connection. Let some finish first.", 429
        )

    # Bundle member: inherit password/expiry/owner from the bundle envelope.
    bundle_id = None
    bundle_path = None
    expiry_hours = ingest.parse_expiry(body.get("expiry_hours"))
    pw_hash = _hash_pw(str(body.get("password") or ""))
    session = ingest.parse_session(body.get("session_id"))
    burn = ingest.parse_burn(body.get("burn"))
    max_dl = ingest.parse_max_downloads(body.get("max_downloads"), burn)

    if body.get("bundle_id"):
        bundle = await _bundle_for_member(body)
        if isinstance(bundle, JSONResponse):
            return bundle
        bundle_id = bundle["id"]
        pw_hash = bundle["password_hash"]
        session = bundle["uploader_session"]
        burn, max_dl = False, None
        bundle_path = _sanitize_path(str(body.get("path") or name))
        async with db.execute(
            "SELECT COUNT(*) FROM uploads WHERE bundle_id=?", (bundle_id,)
        ) as cur:
            if (await cur.fetchone())[0] >= MAX_BUNDLE_FILES:
                return ingest.error(f"Folders are limited to {MAX_BUNDLE_FILES} files.", 413)

    tier = await tiers.tier_for(request, ip)
    rejected = await ingest.precheck(size, ip, bypassed, tier)
    if rejected is not None:
        return rejected

    upload_id = uuid.uuid4().hex
    chunk_size = settings.CHUNK_SIZE
    chunk_count = max(1, -(-size // chunk_size))
    try:
        await storage.reserve(storage.partial_path(upload_id), size)
    except storage.DiskFullError:
        return ingest.disk_full_response()

    await db.execute(
        """INSERT INTO uploads
           (id, orig_name, file_size, chunk_size, chunk_count, password_hash,
            max_downloads, burn_after_read, expiry_hours, bypass_code, client_ip,
            user_agent, uploader_session, bundle_id, bundle_path, updated_at, tier)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (upload_id, name, size, chunk_size, chunk_count, pw_hash, max_dl,
         1 if burn else 0, expiry_hours, bypass_code if bypassed else None, ip, ua,
         session, bundle_id, bundle_path, _now(), tier.name),
    )
    await db.commit()
    return JSONResponse(
        {"id": upload_id, "chunk_size": chunk_size, "chunk_count": chunk_count,
         "received": [], "status": "uploading"},
        status_code=201,
    )


@router.get("/api/uploads")
async def my_uploads(request: Request):
    """Uploads this browser started that aren't finished, or finished in the
    last hour — so a reload or a later visit can pick them up again. Scoped to
    the caller's session id; nobody else's uploads are ever listed."""
    session = request.headers.get("X-Session-ID", "").strip()[:64]
    if not session:
        return JSONResponse({"uploads": []})
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    db = await get_db()
    async with db.execute(
        "SELECT * FROM uploads WHERE uploader_session=? AND bundle_id IS NULL "
        "AND (status IN ('uploading','processing') OR updated_at > ?) "
        "ORDER BY created_at",
        (session, recent),
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    out = []
    for row in rows:
        item = _public(row)
        if row["status"] == "uploading":
            item["received_count"] = len(await _received(row["id"]))
        out.append(item)
    return JSONResponse({"uploads": out})


@router.get("/api/uploads/{upload_id}")
async def upload_status(upload_id: str):
    upload_id = _safe_hex(upload_id)
    row = await _load(upload_id) if upload_id else None
    if not row:
        return ingest.error("Upload not found or expired.", 404)
    return JSONResponse(_public(row, await _received(upload_id)))


@router.put("/api/uploads/{upload_id}/chunks/{index}")
async def put_chunk(request: Request, upload_id: str, index: int):
    upload_id = _safe_hex(upload_id)
    row = await _load(upload_id) if upload_id else None
    if not row:
        return ingest.error("Upload not found or expired.", 404)
    if row["status"] != "uploading":
        return ingest.error(f"Upload is already {row['status']}.", 409)
    if not 0 <= index < row["chunk_count"]:
        return ingest.error("Chunk index out of range.", 400)

    offset = index * row["chunk_size"]
    expected = min(row["chunk_size"], row["file_size"] - offset)

    buf = bytearray()
    async for part in request.stream():
        buf += part
        if len(buf) > expected:
            return ingest.error("Chunk is larger than expected.", 400)
    if len(buf) != expected:
        # Connection dropped mid-chunk; the client just retries this index.
        return ingest.error(f"Incomplete chunk ({len(buf)} of {expected} bytes).", 400)

    try:
        await storage.write_at(storage.partial_path(upload_id), offset, bytes(buf))
    except FileNotFoundError:
        return ingest.error("Upload not found or expired.", 404)
    except OSError as exc:
        if storage.is_disk_full_error(exc):
            return ingest.disk_full_response()
        raise

    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO upload_chunks (upload_id, idx) VALUES (?, ?)", (upload_id, index)
    )
    await db.execute("UPDATE uploads SET updated_at=? WHERE id=?", (_now(), upload_id))
    await db.commit()
    return JSONResponse({"ok": True, "index": index})


@router.post("/api/uploads/{upload_id}/complete")
async def complete_upload(request: Request, upload_id: str):
    upload_id = _safe_hex(upload_id)
    row = await _load(upload_id) if upload_id else None
    if not row:
        return ingest.error("Upload not found or expired.", 404)

    if row["status"] == "uploading":
        received = await _received(upload_id)
        missing = sorted(set(range(row["chunk_count"])) - set(received))
        if missing:
            return JSONResponse(
                {"error": "Some chunks are missing.", "missing": missing[:100]},
                status_code=409,
            )
        db = await get_db()
        cur = await db.execute(
            "UPDATE uploads SET status='processing', updated_at=? "
            "WHERE id=? AND status='uploading'",
            (_now(), upload_id),
        )
        await db.commit()
        if cur.rowcount == 1:
            _tasks[upload_id] = asyncio.create_task(
                _finalize(upload_id, ingest.base_url(request))
            )

    task = _tasks.get(upload_id)
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_COMPLETE_WAIT)
        except asyncio.TimeoutError:
            pass

    row = await _load(upload_id)
    out = _public(row)
    if row["status"] in ("done", "failed"):
        return JSONResponse(out["result"], status_code=out["code"])
    return JSONResponse(out, status_code=202)


@router.delete("/api/uploads/{upload_id}")
async def abort_upload(upload_id: str):
    upload_id = _safe_hex(upload_id)
    row = await _load(upload_id) if upload_id else None
    if not row:
        return JSONResponse({"ok": True})
    if row["status"] in ("uploading", "failed", "done"):
        await _drop(upload_id)  # the stored file (if done) is untouched
    return JSONResponse({"ok": True})


async def _drop(upload_id: str) -> None:
    storage.partial_path(upload_id).unlink(missing_ok=True)
    db = await get_db()
    await db.execute("DELETE FROM upload_chunks WHERE upload_id=?", (upload_id,))
    await db.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
    await db.commit()


async def _finalize(upload_id: str, base_url: str) -> None:
    try:
        row = await _load(upload_id)
        expires_at = None
        if row["bundle_id"]:
            db = await get_db()
            async with db.execute(
                "SELECT expires_at FROM bundles WHERE id=?", (row["bundle_id"],)
            ) as cur:
                b = await cur.fetchone()
            expires_at = b[0] if b else None
        meta = ingest.UploadMeta(
            orig_name=row["orig_name"],
            ip=row["client_ip"],
            ua=row["user_agent"] or "",
            base_url=base_url,
            password_hash=row["password_hash"],
            max_downloads=row["max_downloads"],
            burn=bool(row["burn_after_read"]),
            expiry_hours=row["expiry_hours"],
            session=row["uploader_session"],
            bypassed=bool(row["bypass_code"]),
            # Bundle members share one code; the bundle spends it on publish.
            bypass_code="" if row["bundle_id"] else (row["bypass_code"] or ""),
            bundle_id=row["bundle_id"],
            bundle_path=row["bundle_path"],
            expires_at=expires_at,
            tier=row["tier"] or "standard",
            daily_limit=tiers.by_name(row["tier"]).daily,
        )

        def report(phase: str, fraction: float) -> None:
            _progress[upload_id] = {"phase": phase, "progress": round(fraction, 3)}

        code, body = await ingest.finalize(storage.partial_path(upload_id), meta, report)
    except Exception:
        logger.exception("finalize task crashed for %s", upload_id)
        code, body = 500, {"error": "Upload failed on the server. Please try again."}

    try:
        db = await get_db()
        await db.execute(
            "UPDATE uploads SET status=?, result=?, updated_at=? WHERE id=?",
            ("done" if code == 201 else "failed",
             json.dumps({"code": code, "body": body}), _now(), upload_id),
        )
        await db.execute("DELETE FROM upload_chunks WHERE upload_id=?", (upload_id,))
        await db.commit()
    finally:
        _tasks.pop(upload_id, None)
        _progress.pop(upload_id, None)


async def recover_interrupted() -> None:
    """A restart mid-finalize leaves rows in 'processing' with no task. If the
    partial is still there, put them back so the client's next complete call
    restarts the finalize; otherwise mark them failed."""
    db = await get_db()
    async with db.execute("SELECT id FROM uploads WHERE status='processing'") as cur:
        ids = [r[0] for r in await cur.fetchall()]
    for uid in ids:
        if storage.partial_path(uid).exists():
            await db.execute("UPDATE uploads SET status='uploading' WHERE id=?", (uid,))
        else:
            await db.execute(
                "UPDATE uploads SET status='failed', result=? WHERE id=?",
                (json.dumps({"code": 500, "body": {
                    "error": "The server restarted during processing. Please upload again."}}),
                 uid),
            )
    await db.commit()


# ── Bundles ───────────────────────────────────────────────────────────────────

def _sanitize_path(path: str) -> str:
    from app.routes.bundles import _sanitize_path as sp
    return sp(path)


async def _bundle_for_member(body: dict) -> dict | JSONResponse:
    bundle_id = _safe_hex(str(body.get("bundle_id") or ""))
    token = str(body.get("bundle_token") or "")
    if not bundle_id:
        return ingest.error("Folder not found.", 404)
    db = await get_db()
    async with db.execute(
        "SELECT * FROM bundles WHERE id=? AND pending=1 AND archived_at IS NULL", (bundle_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return ingest.error("Folder not found or already published.", 404)
    bundle = dict(row)
    if not bundle["upload_token"] or not secrets.compare_digest(
        token.encode(), bundle["upload_token"].encode()
    ):
        return ingest.error("Forbidden.", 403)
    return bundle


@router.post("/api/bundles")
async def create_bundle(request: Request):
    body = await _json(request)
    ip = _client_ip(request)
    if body.get("website"):
        return ingest.error("Invalid request.", 400)

    file_count = _int(body.get("file_count"), 0)
    total_size = _int(body.get("total_size"), 0)
    if file_count < 1:
        return ingest.error("The folder is empty.", 400)
    if file_count > MAX_BUNDLE_FILES:
        return ingest.error(f"Folders are limited to {MAX_BUNDLE_FILES} files.", 413)

    bypassed = await peek_bypass_code(str(body.get("bypass_code") or ""))
    tier = await tiers.tier_for(request, ip)
    rejected = await ingest.precheck(max(0, total_size), ip, bypassed, tier, per_file=False)
    if rejected is not None:
        return rejected

    bundle_id = uuid.uuid4().hex
    token = secrets.token_hex(24)
    hours = ingest.parse_expiry(body.get("expiry_hours"))
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    name = str(body.get("name") or "").strip()[:120] or f"Folder ({file_count} files)"

    db = await get_db()
    await db.execute(
        """INSERT INTO bundles
           (id, name, password_hash, expires_at, file_count, total_size, client_ip,
            uploader_session, pending, upload_token)
           VALUES (?,?,?,?,?,?,?,?,1,?)""",
        (bundle_id, name, _hash_pw(str(body.get("password") or "")), expires_at,
         0, 0, ip, ingest.parse_session(body.get("session_id")), token),
    )
    await db.commit()
    return JSONResponse({"id": bundle_id, "token": token, "expires_at": expires_at},
                        status_code=201)


@router.post("/api/bundles/{bundle_id}/complete")
async def complete_bundle(request: Request, bundle_id: str):
    body = await _json(request)
    bundle = await _bundle_for_member({**body, "bundle_id": bundle_id})
    if isinstance(bundle, JSONResponse):
        return bundle

    db = await get_db()
    async with db.execute(
        "SELECT COUNT(*), COALESCE(SUM(file_size), 0) FROM files "
        "WHERE bundle_id=? AND archived_at IS NULL",
        (bundle["id"],),
    ) as cur:
        count, total = await cur.fetchone()
    if not count:
        return ingest.error("None of the folder's files were uploaded.", 400)

    await db.execute(
        "UPDATE bundles SET pending=0, upload_token=NULL, file_count=?, total_size=? WHERE id=?",
        (count, total, bundle["id"]),
    )
    await db.execute("DELETE FROM uploads WHERE bundle_id=? AND status='done'", (bundle["id"],))
    await db.commit()

    code = str(body.get("bypass_code") or "")
    if code:
        await consume_bypass_code(code)
    await ingest.audit(_client_ip(request), request.headers.get("user-agent", ""),
                       "upload_bundle", bundle["id"], f"files:{count},size:{total}")
    return JSONResponse(
        {"id": bundle["id"], "url": f"{ingest.base_url(request)}/b/{bundle['id']}",
         "name": bundle["name"], "file_count": count, "total_size": total,
         "expires_at": bundle["expires_at"]},
        status_code=201,
    )
