"""
Bundle (folder) uploads — many files share one URL at /b/{id}.

Each member file is stored exactly like a normal upload (separate row in `files`,
own UUID on disk, own ClamAV scan, own expires_at). The `bundles` row holds the
shared metadata (name, password, expiry, owner). Member files carry `bundle_id`
and `bundle_path` so the listing can rebuild a directory tree.
"""
import io
import logging
import mimetypes
import os
import re
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.database import get_db
from app.middleware.rate_limit import (
    _client_ip,
    check_upload_bandwidth,
    record_bandwidth,
    validate_bypass_code,
)
from app.routes.upload import ALLOWED_EXTENSIONS, _check_extension
from app.services import concurrent, storage, virus_scan
from app.services.config_manager import (
    get_file_expiry_hours,
    get_max_file_size,
    get_storage_limit,
)

logger = logging.getLogger("yeet.bundles")
router = APIRouter()
templates = Jinja2Templates(directory="templates")

MAX_BUNDLE_FILES = 200
_PATH_SAFE = re.compile(r"[^\w\s\-./]")


def _sanitize_path(path: str) -> str:
    """Turn an arbitrary client-supplied relative path into something safe."""
    if not path:
        return "file"
    parts = []
    for p in path.replace("\\", "/").split("/"):
        p = _PATH_SAFE.sub("", p).strip()
        if not p or p in (".", ".."):
            continue
        parts.append(p[:255])
    return "/".join(parts) or "file"


def _safe_id(bundle_id: str) -> str | None:
    if bundle_id and len(bundle_id) == 32 and all(c in "0123456789abcdef" for c in bundle_id):
        return bundle_id
    return None


@router.post("/upload-bundle")
async def upload_bundle(
    request: Request,
    files: list[UploadFile] = File(...),
    paths: str = Form(default=""),
    name: str = Form(default=""),
    password: str = Form(default=""),
    bypass_code: str = Form(default=""),
    session_id: str = Form(default=""),
    expiry_hours: str = Form(default=""),
    website: str = Form(default=""),
):
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")

    if website:
        logger.warning("honeypot: ip=%s", ip)
        return JSONResponse({"error": "Invalid request."}, status_code=400)

    if not files:
        return JSONResponse({"error": "No files provided."}, status_code=400)

    if len(files) > MAX_BUNDLE_FILES:
        return JSONResponse(
            {"error": f"Too many files. Max {MAX_BUNDLE_FILES} per folder."},
            status_code=413,
        )

    bypassed = await validate_bypass_code(bypass_code)
    max_file = get_max_file_size()
    storage_limit = get_storage_limit()

    if not bypassed:
        if not await concurrent.acquire(ip):
            return JSONResponse(
                {"error": "Please wait for your current uploads to finish (max 2 at once)."},
                status_code=429,
            )
    else:
        await concurrent.acquire(ip)

    try:
        return await _do_bundle_upload(
            request, files, paths, name, password, ip, ua,
            max_file, storage_limit, bypassed, session_id, expiry_hours,
        )
    finally:
        await concurrent.release(ip)


async def _do_bundle_upload(
    request, files, paths_csv, bundle_name, password, ip, ua,
    max_file, storage_limit, bypassed, session_id, expiry_hours,
):
    # ── Resolve relative paths ────────────────────────────────────────────────
    raw_paths = [p for p in (paths_csv or "").split("\n") if p]
    if raw_paths and len(raw_paths) != len(files):
        return JSONResponse({"error": "paths/files length mismatch."}, status_code=400)

    rel_paths: list[str] = []
    for i, f in enumerate(files):
        raw = raw_paths[i] if raw_paths else (f.filename or f"file_{i}")
        rel_paths.append(_sanitize_path(raw))

    # ── Read everything (fail fast on size) ───────────────────────────────────
    blobs: list[bytes] = []
    total = 0
    for f in files:
        data = await f.read(max_file + 1)
        if not bypassed and len(data) > max_file:
            return JSONResponse(
                {"error": f"File '{f.filename}' too large. Max {max_file // 1_048_576} MB per file."},
                status_code=413,
            )
        blobs.append(data)
        total += len(data)

    # ── Storage cap ───────────────────────────────────────────────────────────
    used = storage.get_total_storage()
    if not bypassed and (used + total) >= storage_limit * 0.9:
        return JSONResponse(
            {"error": "Storage limit reached. Contact info@majmohar.eu"},
            status_code=507,
        )

    # ── Bandwidth ─────────────────────────────────────────────────────────────
    if not bypassed:
        bw = await check_upload_bandwidth(ip, total)
        if not bw["allowed"]:
            return JSONResponse(
                {
                    "error": (
                        f"Daily upload limit reached "
                        f"({bw['limit'] // 1_048_576} MB/day). "
                        f"Resets in {bw['reset_in_hours']:.1f}h."
                    ),
                    "bandwidth": bw,
                },
                status_code=429,
                headers={"Retry-After": str(int(bw["reset_in_hours"] * 3600))},
            )

    # ── Per-file extension check + virus scan ─────────────────────────────────
    for f in files:
        err = _check_extension(f.filename or "")
        if err:
            return JSONResponse({"error": f"{f.filename}: {err}"}, status_code=400)

    scan_results = []
    for i, data in enumerate(blobs):
        status, threat = virus_scan.scan_bytes(data)
        if status == "infected":
            await _audit(ip, ua, "virus_detected", None,
                         f"filename:{files[i].filename},threat:{threat}")
            return JSONResponse(
                {"error": f"Bundle rejected: '{files[i].filename}' contains malware ({threat})."},
                status_code=422,
            )
        scan_results.append(status)

    # ── Save bundle + members ─────────────────────────────────────────────────
    bundle_id = uuid.uuid4().hex
    expiry_h = get_file_expiry_hours()
    if expiry_hours.strip().isdigit():
        requested = int(expiry_hours.strip())
        if 1 <= requested <= 168:
            expiry_h = requested
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=expiry_h)).isoformat()

    safe_session = session_id.strip()[:64] if session_id else None
    pw_hash = None
    if password:
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    # Bundle name: user-provided, else common path prefix, else generic
    display_name = (bundle_name or "").strip()[:120]
    if not display_name:
        first_seg = rel_paths[0].split("/", 1)[0] if rel_paths else ""
        if first_seg and all(p.startswith(first_seg + "/") or p == first_seg for p in rel_paths):
            display_name = first_seg
        else:
            display_name = f"Bundle ({len(files)} files)"

    db = await get_db()
    await db.execute(
        """INSERT INTO bundles
           (id, name, password_hash, expires_at, file_count, total_size,
            client_ip, uploader_session)
           VALUES (?,?,?,?,?,?,?,?)""",
        (bundle_id, display_name, pw_hash, expires_at, len(files), total,
         ip, safe_session),
    )

    for i, (f, data, rel) in enumerate(zip(files, blobs, rel_paths)):
        file_id = uuid.uuid4().hex
        safe_name = storage.sanitize_filename(f.filename or rel.split("/")[-1])
        file_hash = storage.hash_bytes(data)
        mime_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        await storage.save_file(file_id, data)

        await db.execute(
            """INSERT INTO files
               (id, filename, orig_name, file_hash, file_size, mime_type,
                password_hash, expires_at, client_ip, scan_status,
                uploader_session, bundle_id, bundle_path)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                file_id, safe_name, f.filename or rel.split("/")[-1],
                file_hash, len(data), mime_type,
                pw_hash, expires_at, ip, scan_results[i],
                safe_session, bundle_id, rel,
            ),
        )

    await db.commit()

    await record_bandwidth(ip, total)
    await _audit(ip, ua, "upload_bundle", bundle_id,
                 f"files:{len(files)},size:{total},bypassed:{bypassed}")

    bundle_url = str(request.base_url).rstrip("/") + f"/b/{bundle_id}"
    return JSONResponse(
        {
            "id": bundle_id,
            "url": bundle_url,
            "expires_at": expires_at,
            "file_count": len(files),
            "total_size": total,
            "bypassed": bypassed,
        },
        status_code=201,
    )


@router.get("/b/{bundle_id}")
async def view_bundle(request: Request, bundle_id: str):
    bundle_id = _safe_id(bundle_id)
    if not bundle_id:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "status": 404, "message": "Bundle not found."},
            status_code=404,
        )

    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT * FROM bundles WHERE id=? AND archived_at IS NULL AND expires_at > ?",
        (bundle_id, now),
    ) as cur:
        b = await cur.fetchone()

    if not b:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "status": 404,
             "message": "Bundle not found or has expired."},
            status_code=404,
        )

    bundle = dict(b)

    if bundle["password_hash"]:
        # Members are protected too — show password gate.
        return templates.TemplateResponse(
            "bundle_view.html",
            {"request": request, "bundle": bundle, "members": [],
             "needs_password": True, "expiry_hours": get_file_expiry_hours(),
             "max_file_mb": get_max_file_size() // 1_048_576,
             "storage_blocked": False},
        )

    async with db.execute(
        "SELECT id, orig_name, file_size, mime_type, bundle_path, scan_status "
        "FROM files WHERE bundle_id=? AND archived_at IS NULL "
        "ORDER BY bundle_path",
        (bundle_id,),
    ) as cur:
        members = [dict(r) for r in await cur.fetchall()]

    return templates.TemplateResponse(
        "bundle_view.html",
        {"request": request, "bundle": bundle, "members": members,
         "needs_password": False,
         "expiry_hours": get_file_expiry_hours(),
         "max_file_mb": get_max_file_size() // 1_048_576,
         "storage_blocked": False},
    )


@router.get("/b/{bundle_id}/zip")
async def download_bundle_zip(request: Request, bundle_id: str):
    bundle_id = _safe_id(bundle_id)
    if not bundle_id:
        return JSONResponse({"error": "Not found."}, status_code=404)

    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT id, name, password_hash FROM bundles "
        "WHERE id=? AND archived_at IS NULL AND expires_at > ?",
        (bundle_id, now),
    ) as cur:
        b = await cur.fetchone()
    if not b:
        return JSONResponse({"error": "Not found or expired."}, status_code=404)
    if b[2]:
        return JSONResponse({"error": "Password required."}, status_code=403)

    async with db.execute(
        "SELECT id, bundle_path, orig_name FROM files "
        "WHERE bundle_id=? AND archived_at IS NULL",
        (bundle_id,),
    ) as cur:
        members = await cur.fetchall()

    if not members:
        return JSONResponse({"error": "Empty bundle."}, status_code=404)

    # Write the zip to a temp file on disk so we don't blow memory on large
    # bundles. The temp file is unlinked immediately; the open fd keeps the
    # data alive until the response is fully streamed.
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip", prefix="yeet-bundle-")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            seen: dict[str, int] = {}
            for fid, bpath, oname in members:
                arc = bpath or oname or fid
                if arc in seen:
                    seen[arc] += 1
                    base, _, ext = arc.rpartition(".")
                    arc = f"{base}_{seen[arc]}.{ext}" if base else f"{arc}_{seen[arc]}"
                else:
                    seen[arc] = 0
                path = storage.get_file_path(fid)
                if path.exists():
                    zf.write(path, arcname=arc)
        tmp.flush()
        size = os.path.getsize(tmp.name)
        tmp.close()
    except Exception:
        try: tmp.close()
        except Exception: pass
        try: os.unlink(tmp.name)
        except Exception: pass
        raise

    safe_name = re.sub(r"[^\w\-.]", "_", b[1])[:80] or "bundle"
    await db.execute(
        "UPDATE bundles SET download_count = download_count + 1 WHERE id=?",
        (bundle_id,),
    )
    await db.commit()

    def _iter_and_clean(path: str):
        try:
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try: os.unlink(path)
            except Exception: pass

    return StreamingResponse(
        _iter_and_clean(tmp.name),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.zip"',
            "Content-Length": str(size),
        },
    )


@router.delete("/api/bundles/{bundle_id}")
async def delete_bundle(request: Request, bundle_id: str):
    bundle_id = _safe_id(bundle_id)
    if not bundle_id:
        return JSONResponse({"error": "Not found."}, status_code=404)

    session = request.headers.get("X-Session-ID", "").strip()
    if not session:
        return JSONResponse({"error": "Forbidden."}, status_code=403)

    db = await get_db()
    async with db.execute(
        "SELECT uploader_session FROM bundles WHERE id=? AND archived_at IS NULL",
        (bundle_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return JSONResponse({"error": "Not found."}, status_code=404)
    if not row[0] or row[0] != session:
        return JSONResponse({"error": "Forbidden."}, status_code=403)

    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT id FROM files WHERE bundle_id=? AND archived_at IS NULL",
        (bundle_id,),
    ) as cur:
        member_ids = [r[0] for r in await cur.fetchall()]

    for fid in member_ids:
        await storage.archive_file(fid)
        await db.execute("UPDATE files SET archived_at=? WHERE id=?", (now, fid))
    await db.execute("UPDATE bundles SET archived_at=? WHERE id=?", (now, bundle_id))
    await db.commit()
    return JSONResponse({"ok": True, "deleted_files": len(member_ids)})


@router.get("/api/bundles/all")
async def list_bundles():
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT id, name, file_count, total_size, created_at, expires_at, "
        "download_count, uploader_session, "
        "(password_hash IS NOT NULL) AS has_password "
        "FROM bundles WHERE archived_at IS NULL AND expires_at > ? "
        "ORDER BY created_at DESC",
        (now,),
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return JSONResponse({"bundles": rows})


async def _audit(ip, ua, action, file_id, details=None):
    db = await get_db()
    await db.execute(
        "INSERT INTO audit_log (action, file_id, client_ip, user_agent, details) VALUES (?,?,?,?,?)",
        (action, file_id, ip, ua, details),
    )
    await db.commit()
