import logging
import mimetypes
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from app.database import get_db
from app.middleware.rate_limit import (
    _client_ip,
    check_upload_bandwidth,
    record_bandwidth,
    validate_bypass_code,
)
from app.services import concurrent, storage, virus_scan
from app.services.config_manager import (
    get_file_expiry_hours,
    get_max_file_size,
    get_storage_limit,
)

logger = logging.getLogger("yeet.upload")
router = APIRouter()
templates = Jinja2Templates(directory="templates")

ALLOWED_EXTENSIONS = {
    # Images
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".ico", ".avif",
    # Documents
    ".pdf", ".txt", ".md", ".doc", ".docx", ".odt", ".rtf",
    # Spreadsheets
    ".xls", ".xlsx", ".csv", ".ods",
    # Presentations
    ".ppt", ".pptx", ".odp",
    # Archives
    ".zip", ".tar", ".gz", ".7z", ".rar", ".bz2", ".xz",
    # Video
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
    # Audio
    ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac",
    # Data / config
    ".json", ".xml", ".yaml", ".yml", ".toml",
    # Source code (safe to share, not executed server-side)
    ".py", ".java", ".cpp", ".c", ".rs", ".go", ".ts", ".css", ".sql",
    ".rb", ".php", ".swift", ".kt",
}


def _check_extension(filename: str) -> str | None:
    """Return error string if extension is not allowed, else None."""
    if "." not in filename:
        return "Files without an extension are not allowed. Add an extension or pack it into a .zip archive."
    ext = "." + filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return (
            f"Extension '{ext}' is not allowed. "
            "Supported: images, documents, archives, media, data files. "
            "Unusual files? Pack them into a .zip archive."
        )
    return None


@router.get("/")
async def index(request: Request):
    total = storage.get_total_storage()
    limit = get_storage_limit()
    pct = round(total / limit * 100, 1) if limit else 0
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "storage_pct": pct,
            "storage_warning": pct >= 80,
            "storage_blocked": pct >= 90,
            "max_file_mb": get_max_file_size() // 1_048_576,
            "expiry_hours": get_file_expiry_hours(),
        },
    )


@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    password: str = Form(default=""),
    max_downloads: str = Form(default=""),
    bypass_code: str = Form(default=""),
    session_id: str = Form(default=""),
    expiry_hours: str = Form(default=""),
    website: str = Form(default=""),  # honeypot
):
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")

    # Honeypot: real browsers leave this blank
    if website:
        logger.warning("honeypot triggered: ip=%s", ip)
        await _audit(ip, ua, "bot_rejected", None, f"honeypot={website[:32]}")
        return JSONResponse({"error": "Invalid request."}, status_code=400)

    max_file = get_max_file_size()
    storage_limit = get_storage_limit()

    # ── Bypass code ───────────────────────────────────────────────────────────
    bypassed = await validate_bypass_code(bypass_code)

    # ── Concurrent upload limit ───────────────────────────────────────────────
    if not bypassed:
        if not await concurrent.acquire(ip):
            return JSONResponse(
                {"error": "Please wait for your current uploads to finish (max 2 at once)."},
                status_code=429,
            )
    else:
        await concurrent.acquire(ip)  # still track so release works

    try:
        return await _do_upload(
            request, file, password, max_downloads,
            ip, ua, max_file, storage_limit, bypassed, session_id, expiry_hours,
        )
    finally:
        await concurrent.release(ip)


async def _do_upload(
    request, file, password, max_downloads,
    ip, ua, max_file, storage_limit, bypassed, session_id="", expiry_hours="",
):
    # ── File size check (before reading full body to fail fast) ───────────────
    content_length = int(request.headers.get("content-length", 0))
    if not bypassed and content_length > max_file:
        return JSONResponse(
            {"error": f"File too large. Maximum is {max_file // 1_048_576} MB."},
            status_code=413,
        )

    # ── Storage limit pre-check at 90% ───────────────────────────────────────
    total_used = storage.get_total_storage()
    if not bypassed and total_used >= storage_limit * 0.9:
        return JSONResponse(
            {"error": "Storage limit reached. Contact info@majmohar.eu"},
            status_code=507,
        )

    # ── Read file ─────────────────────────────────────────────────────────────
    data = await file.read(max_file + 1)
    if not bypassed and len(data) > max_file:
        return JSONResponse(
            {"error": f"File too large. Maximum is {max_file // 1_048_576} MB."},
            status_code=413,
        )

    # ── Upload bandwidth check (byte-based, 24h rolling) ─────────────────────
    if not bypassed:
        bw = await check_upload_bandwidth(ip, len(data))
        if not bw["allowed"]:
            hrs = bw["reset_in_hours"]
            return JSONResponse(
                {
                    "error": (
                        f"Daily upload limit reached "
                        f"({bw['limit'] // 1_048_576} MB/day). "
                        f"Resets in {hrs:.1f}h."
                    ),
                    "bandwidth": bw,
                },
                status_code=429,
                headers={"Retry-After": str(int(hrs * 3600))},
            )

    # ── Extension whitelist ───────────────────────────────────────────────────
    ext_error = _check_extension(file.filename or "")
    if ext_error:
        return JSONResponse({"error": ext_error}, status_code=400)

    # ── Virus scan ────────────────────────────────────────────────────────────
    scan_status, threat = virus_scan.scan_bytes(data)
    if scan_status == "infected":
        orig = file.filename or "unknown"
        logger.warning("virus detected: ip=%s file=%s threat=%s", ip, orig, threat)
        await _audit(ip, ua, "virus_detected", None, f"filename:{orig},threat:{threat}")
        return JSONResponse(
            {"error": f"File rejected: malware detected ({threat})."},
            status_code=422,
        )

    # ── Save file ─────────────────────────────────────────────────────────────
    file_id = uuid.uuid4().hex
    safe_name = storage.sanitize_filename(file.filename or "file")
    file_hash = storage.hash_bytes(data)
    mime_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    await storage.save_file(file_id, data)

    password_hash = None
    if password:
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    expiry_h = get_file_expiry_hours()
    if expiry_hours.strip().isdigit():
        requested = int(expiry_hours.strip())
        if 1 <= requested <= 168:
            expiry_h = requested
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=expiry_h)).isoformat()

    max_dl = None
    if max_downloads.strip().isdigit():
        max_dl = int(max_downloads.strip())

    db = await get_db()
    safe_session = session_id.strip()[:64] if session_id else None
    await db.execute(
        """INSERT INTO files
           (id, filename, orig_name, file_hash, file_size, mime_type,
            password_hash, expires_at, max_downloads, client_ip, scan_status,
            uploader_session)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            file_id, safe_name, file.filename or "file", file_hash,
            len(data), mime_type, password_hash, expires_at,
            max_dl, ip, scan_status, safe_session,
        ),
    )
    await db.commit()

    # ── Record bandwidth (even for bypassed uploads) ──────────────────────────
    await record_bandwidth(ip, len(data))
    await _audit(ip, ua, "upload", file_id,
                 f"size:{len(data)},scan:{scan_status},bypassed:{bypassed}")

    # ── Bandwidth warning in response ─────────────────────────────────────────
    bw_info = await check_upload_bandwidth(ip, 0)
    warning = None
    if not bypassed and bw_info["pct"] >= 80:
        warning = (
            f"You've used {bw_info['pct']}% of your daily upload limit "
            f"({bw_info['limit'] // 1_048_576} MB). "
            f"Resets in {bw_info['reset_in_hours']:.1f}h."
        )

    download_url = str(request.base_url).rstrip("/") + f"/f/{file_id}"
    return JSONResponse(
        {
            "id": file_id,
            "url": download_url,
            "expires_at": expires_at,
            "size": len(data),
            "scan_status": scan_status,
            "bypassed": bypassed,
            "warning": warning,
        },
        status_code=201,
    )


async def _audit(ip, ua, action, file_id, details=None):
    db = await get_db()
    await db.execute(
        "INSERT INTO audit_log (action, file_id, client_ip, user_agent, details) VALUES (?,?,?,?,?)",
        (action, file_id, ip, ua, details),
    )
    await db.commit()
