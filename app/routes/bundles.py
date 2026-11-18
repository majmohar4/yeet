"""
Bundle (folder) uploads — many files share one URL at /b/{id}.

Each member file is stored exactly like a normal upload (separate row in `files`,
own UUID on disk, own ClamAV scan, own expires_at). The `bundles` row holds the
shared metadata (name, password, expiry, owner). Member files carry `bundle_id`
and `bundle_path` so the listing can rebuild a directory tree.
"""
import logging
import os
import re
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import get_db
from app.middleware.rate_limit import (
    _client_ip,
    check_upload_bandwidth,
    consume_bypass_code,
    peek_bypass_code,
    record_bandwidth,
)
from app.routes.files import annotate_ownership, owns
from app.routes.upload import ALLOWED_EXTENSIONS, _check_extension
from app.services import concurrent, ingest, storage, tiers
from app.services.config_manager import (
    get_file_expiry_hours,
    get_max_file_size,
    get_storage_limit,
)

logger = logging.getLogger("yeet.bundles")
router = APIRouter()
templates = Jinja2Templates(directory="templates")

MAX_BUNDLE_FILES = 500
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

    bypassed = await peek_bypass_code(bypass_code)
    tier = await tiers.tier_for(request, ip)
    max_file = tier.max_file
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
            bypass_code, tier,
        )
    finally:
        await concurrent.release(ip)


async def _do_bundle_upload(
    request, files, paths_csv, bundle_name, password, ip, ua,
    max_file, storage_limit, bypassed, session_id, expiry_hours,
    bypass_code="", tier=None,
):
    raw_paths = [p for p in (paths_csv or "").split("\n") if p]
    if raw_paths and len(raw_paths) != len(files):
        return JSONResponse({"error": "paths/files length mismatch."}, status_code=400)
    rel_paths = [
        _sanitize_path(raw_paths[i] if raw_paths else (f.filename or f"file_{i}"))
        for i, f in enumerate(files)
    ]
    for f in files:
        err = _check_extension(f.filename or "")
        if err:
            return JSONResponse({"error": f"{f.filename}: {err}"}, status_code=400)

    # Spool every member to disk first (never into memory), then check limits
    # against the real total.
    from app.routes.upload import spool
    spooled = []
    try:
        for f in files:
            result, size = await spool(f, None if bypassed else max_file)
            if isinstance(result, JSONResponse):
                return result
            spooled.append((result, size))
        total = sum(size for _, size in spooled)
        rejected = await ingest.precheck(total, ip, bypassed, tier, per_file=False)
        if rejected is not None:
            return rejected

        bundle_id = uuid.uuid4().hex
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=ingest.parse_expiry(expiry_hours))
        ).isoformat()
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode() if password else None
        display_name = (bundle_name or "").strip()[:120]
        if not display_name:
            first = rel_paths[0].split("/", 1)[0] if rel_paths else ""
            if first and all(p.startswith(first + "/") or p == first for p in rel_paths):
                display_name = first
            else:
                display_name = f"Folder ({len(files)} files)"
        session = ingest.parse_session(session_id)

        db = await get_db()
        await db.execute(
            """INSERT INTO bundles
               (id, name, password_hash, expires_at, file_count, total_size,
                client_ip, uploader_session, pending)
               VALUES (?,?,?,?,?,?,?,?,1)""",
            (bundle_id, display_name, pw_hash, expires_at, 0, 0, ip, session),
        )
        await db.commit()

        base = ingest.base_url(request)
        for (tmp, _), f, rel in zip(spooled, files, rel_paths):
            code, body = await ingest.finalize(tmp, ingest.UploadMeta(
                orig_name=f.filename or rel.split("/")[-1], ip=ip, ua=ua, base_url=base,
                password_hash=pw_hash, session=session, bypassed=bypassed,
                bundle_id=bundle_id, bundle_path=rel, expires_at=expires_at,
                tier=tier.name, daily_limit=tier.daily,
            ))
            if code != 201:
                await _discard_bundle(bundle_id)
                if code == 422:
                    body["error"] = f"Folder rejected: '{f.filename}' — {body['error']}"
                return JSONResponse(body, status_code=code)

        await db.execute(
            "UPDATE bundles SET pending=0, file_count=?, total_size=? WHERE id=?",
            (len(files), total, bundle_id),
        )
        await db.commit()
    finally:
        for tmp, _ in spooled:
            tmp.unlink(missing_ok=True)

    if bypassed:
        await consume_bypass_code(bypass_code)
    await _audit(ip, ua, "upload_bundle", bundle_id,
                 f"files:{len(files)},size:{total},bypassed:{bypassed}")
    return JSONResponse(
        {
            "id": bundle_id,
            "url": f"{base}/b/{bundle_id}",
            "expires_at": expires_at,
            "file_count": len(files),
            "total_size": total,
            "bypassed": bypassed,
        },
        status_code=201,
    )


async def _discard_bundle(bundle_id: str) -> None:
    db = await get_db()
    async with db.execute("SELECT id FROM files WHERE bundle_id=?", (bundle_id,)) as cur:
        ids = [r[0] for r in await cur.fetchall()]
    for fid in ids:
        await storage.delete_file(fid)
    await db.execute("DELETE FROM files WHERE bundle_id=?", (bundle_id,))
    await db.execute("DELETE FROM bundles WHERE id=?", (bundle_id,))
    await db.commit()


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
        "SELECT * FROM bundles WHERE id=? AND archived_at IS NULL AND pending=0 "
        "AND expires_at > ?",
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
        "WHERE id=? AND archived_at IS NULL AND pending=0 AND expires_at > ?",
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

    entries = []
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
            entries.append((path, arc))

    await db.execute(
        "UPDATE bundles SET download_count = download_count + 1 WHERE id=?",
        (bundle_id,),
    )
    await db.commit()

    return StreamingResponse(
        _stream_zip(entries),
        media_type="application/zip",
        headers={"Content-Disposition": storage.content_disposition((b[1] or "folder") + ".zip")},
    )


# Already-compressed formats are stored as-is: deflating them burns CPU for ~0%.
_STORE_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".heic", ".heif",
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mp3", ".m4a", ".aac",
    ".flac", ".ogg", ".opus", ".zip", ".7z", ".rar", ".gz", ".xz", ".zst",
    ".bz2", ".pdf", ".docx", ".xlsx", ".pptx", ".jar", ".apk", ".dmg", ".iso",
}


class _ZipSink:
    """Write-only file object zipfile can target; we drain it after each write."""

    def __init__(self) -> None:
        self.parts: list[bytes] = []
        self.pos = 0

    def write(self, data) -> int:
        self.parts.append(bytes(data))
        self.pos += len(data)
        return len(data)

    def tell(self) -> int:
        return self.pos

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        out = b"".join(self.parts)
        self.parts.clear()
        return out


def _stream_zip(entries):
    """Build the zip while sending it — no temp copy, constant memory."""
    sink = _ZipSink()
    with zipfile.ZipFile(sink, "w", allowZip64=True) as zf:
        for path, arc in entries:
            ext = os.path.splitext(arc)[1].lower()
            method = zipfile.ZIP_STORED if ext in _STORE_EXTS else zipfile.ZIP_DEFLATED
            info = zipfile.ZipInfo.from_file(path, arcname=arc)
            info.compress_type = method
            with open(path, "rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                while chunk := src.read(1_048_576):
                    dst.write(chunk)
                    if sink.pos and sink.parts:
                        yield sink.drain()
            yield sink.drain()
    yield sink.drain()


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
    if not owns(session, row[0]):
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
async def list_bundles(request: Request):
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    session = request.headers.get("X-Session-ID", "").strip()

    scope = "" if settings.PUBLIC_LISTING else "AND uploader_session = ? "
    params: tuple = (now,) if settings.PUBLIC_LISTING else (now, session)
    if not settings.PUBLIC_LISTING and not session:
        return JSONResponse({"bundles": []})

    async with db.execute(
        "SELECT id, name, file_count, total_size, created_at, expires_at, "
        "download_count, uploader_session, "
        "(password_hash IS NOT NULL) AS has_password "
        "FROM bundles WHERE archived_at IS NULL AND pending=0 AND expires_at > ? " + scope +
        "ORDER BY created_at DESC",
        params,
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return JSONResponse({"bundles": annotate_ownership(rows, session)})


async def _audit(ip, ua, action, file_id, details=None):
    db = await get_db()
    await db.execute(
        "INSERT INTO audit_log (action, file_id, client_ip, user_agent, details) VALUES (?,?,?,?,?)",
        (action, file_id, ip, ua, details),
    )
    await db.commit()
