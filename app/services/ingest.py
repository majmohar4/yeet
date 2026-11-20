"""
The one place an uploaded file becomes a `files` row.

Every upload path — chunked browser/CLI uploads, the legacy multipart
`POST /upload`, curl's `POST /raw` and folder bundles — first gets the bytes
into a temp file on disk, then calls `finalize()`. That keeps scanning,
hashing, accounting and the response shape identical everywhere, and means no
path ever holds a whole upload in memory.
"""
import asyncio
import logging
import mimetypes
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.responses import JSONResponse

from app.config import settings
from app.database import get_db
from app.middleware.rate_limit import (
    check_upload_bandwidth,
    consume_bypass_code,
    record_bandwidth,
)
from app.services import media, storage, virus_scan
from app.services.config_manager import (
    get_file_expiry_hours,
    get_storage_limit,
)
from app.services.tiers import Tier
from app.services.extensions import (
    _check_extension,
    is_dangerous_ext,
    is_web_renderable_ext,
)

logger = logging.getLogger("yeet.ingest")

MAX_EXPIRY_HOURS = 168
CLAMAV_STREAM_MAX = 4000 * 1_048_576  # clamd StreamMaxLength (Dockerfile)


@dataclass
class UploadMeta:
    orig_name: str
    ip: str
    ua: str
    base_url: str
    password_hash: str | None = None
    max_downloads: int | None = None
    burn: bool = False
    expiry_hours: int = 24
    session: str | None = None
    bypassed: bool = False
    bypass_code: str = ""
    bundle_id: str | None = None
    bundle_path: str | None = None
    expires_at: str | None = None  # bundle members share the envelope's expiry
    tier: str = "standard"
    daily_limit: int | None = None


# ── Form parsing helpers (validate at the boundary) ──────────────────────────

def parse_burn(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "on", "yes")


def parse_expiry(value) -> int:
    default = get_file_expiry_hours()
    raw = str(value or "").strip()
    if raw.isdigit() and 1 <= int(raw) <= MAX_EXPIRY_HOURS:
        return int(raw)
    return default


def parse_max_downloads(value, burn: bool) -> int | None:
    raw = str(value or "").strip()
    max_dl = int(raw) if raw.isdigit() else None
    if max_dl is not None and max_dl < 1:
        max_dl = None  # 0 used to mean "unlimited"; store that explicitly
    if burn:
        max_dl = 1
    return max_dl


def parse_session(value) -> str | None:
    s = str(value or "").strip()[:64]
    return s or None


def error(message: str, status: int, **extra) -> JSONResponse:
    headers = extra.pop("headers", None)
    return JSONResponse({"error": message, **extra}, status_code=status, headers=headers)


def disk_full_message() -> str:
    contact = f" Contact {settings.CONTACT_EMAIL}." if settings.CONTACT_EMAIL else ""
    return ("The server is out of disk space right now, so the upload was not stored. "
            "Try a smaller file or try again later." + contact)


def disk_full_response() -> JSONResponse:
    return error(disk_full_message(), 507)


async def precheck(
    size: int, ip: str, bypassed: bool, tier: Tier, per_file: bool = True
) -> JSONResponse | None:
    """Everything that can be decided from the declared size alone.

    `per_file=False` for a whole folder: its total may exceed the per-file
    limit; each member is checked on its own when it is uploaded.
    """
    max_file = tier.max_file
    if size < 0:
        return error("Invalid file size.", 400)
    if per_file and not bypassed and size > max_file:
        hint = ""
        if tier.name == "suspicious":
            hint = " Uploads from your connection are temporarily limited."
        return error(f"File too large. Maximum is {_fmt_mb(max_file)}.{hint}", 413)

    if not bypassed and not tier.trusted:
        limit = get_storage_limit()
        if storage.get_total_storage() + size >= limit * 0.9:
            contact = f" Contact {settings.CONTACT_EMAIL}." if settings.CONTACT_EMAIL else ""
            return error("Storage limit reached — uploads are paused." + contact, 507)

    if not storage.has_room_for(size):
        return disk_full_response()

    if not bypassed:
        bw = await check_upload_bandwidth(ip, size, tier.daily)
        if not bw["allowed"]:
            hrs = bw["reset_in_hours"]
            return error(
                f"Daily upload limit reached ({_fmt_mb(bw['limit'])}/day). "
                f"Resets in {hrs:.1f}h.",
                429,
                bandwidth=bw,
                headers={"Retry-After": str(int(hrs * 3600))},
            )
    return None


def _fmt_mb(n: int) -> str:
    if n >= 1_073_741_824:
        return f"{n / 1_073_741_824:.1f} GB".replace(".0 GB", " GB")
    return f"{n // 1_048_576} MB"


# ── Finalize ─────────────────────────────────────────────────────────────────

async def finalize(tmp: Path, meta: UploadMeta, on_progress=None) -> tuple[int, dict]:
    """Scan, store and record `tmp`. Always consumes (moves or deletes) `tmp`.

    Returns (http_status, json_body). `on_progress(phase, fraction)` reports
    'checking' | 'starting' | 'scanning' | 'verifying' | 'finishing'.
    """
    try:
        return await _finalize(tmp, meta, on_progress or (lambda *_: None))
    except Exception as exc:
        if storage.is_disk_full_error(exc):
            logger.error("disk full while finalizing %s", meta.orig_name)
            return 507, {"error": disk_full_message()}
        logger.exception("finalize failed: %s", exc)
        return 500, {"error": "Upload failed on the server. Please try again."}
    finally:
        tmp.unlink(missing_ok=True)


async def _finalize(tmp: Path, meta: UploadMeta, on_progress) -> tuple[int, dict]:
    size = tmp.stat().st_size
    name = meta.orig_name or "file"

    ext_error = _check_extension(name)
    if ext_error:
        return 400, {"error": ext_error}

    # Photos/videos whose bytes really are a known media format skip ClamAV
    # (see app/services/media.py); anything that only *claims* to be media
    # gets the full scan.
    on_progress("checking", 0.0)
    kind = None
    if settings.SCAN_MEDIA == "light" and settings.CLAMAV_ENABLED:
        kind = await asyncio.to_thread(media.verify, tmp, name)
    if meta.tier == "admin":
        # The owner asked for their own uploads to skip the scan.
        scan_status, threat = "skipped-admin", None
    elif kind:
        scan_status, threat = "media", None
    elif size > CLAMAV_STREAM_MAX and meta.tier in ("trusted", "admin"):
        # clamd can't take streams over ~4 GB. Only unlocked uploaders can send
        # files this big; store them marked as unscanned rather than refuse.
        scan_status, threat = "unscanned-large", None
    else:
        scan_status, threat = await virus_scan.scan_path(tmp, on_progress)
    if scan_status == "infected":
        logger.warning("virus detected: ip=%s file=%s threat=%s", meta.ip, name, threat)
        await audit(meta.ip, meta.ua, "virus_detected", None,
                    f"filename:{name},threat:{threat}")
        return 422, {"error": f"File rejected: malware detected ({threat})."}
    if scan_status == "error" and settings.CLAMAV_FAIL_CLOSED:
        logger.error("virus scan unavailable — rejecting upload: ip=%s file=%s", meta.ip, name)
        await audit(meta.ip, meta.ua, "scan_unavailable", None, f"filename:{name}")
        return 503, {"error": "Virus scanner unavailable — upload rejected. Try again shortly."}

    on_progress("finishing", 1.0)
    file_id = uuid.uuid4().hex
    safe_name = storage.sanitize_filename(name)
    file_hash = await storage.hash_file(tmp)
    mime_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    expires_at = meta.expires_at or (
        datetime.now(timezone.utc) + timedelta(hours=meta.expiry_hours)
    ).isoformat()

    await storage.promote(tmp, file_id)
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO files
               (id, filename, orig_name, file_hash, file_size, mime_type,
                password_hash, expires_at, max_downloads, client_ip, scan_status,
                uploader_session, burn_after_read, bundle_id, bundle_path)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                file_id, safe_name, name, file_hash, size, mime_type,
                meta.password_hash, expires_at, meta.max_downloads, meta.ip,
                scan_status, meta.session, 1 if meta.burn else 0,
                meta.bundle_id, meta.bundle_path,
            ),
        )
        await db.commit()
    except Exception:
        await storage.delete_file(file_id)
        raise

    # Spend the code only now that the file is stored — a rejected upload
    # (too large, malware, disk full) must not burn it.
    if meta.bypassed and meta.bypass_code:
        await consume_bypass_code(meta.bypass_code)

    await record_bandwidth(meta.ip, size)
    await audit(meta.ip, meta.ua, "upload", file_id,
                f"size:{size},scan:{scan_status},bypassed:{meta.bypassed}")

    warning = None
    if not meta.bypassed and meta.tier not in ("trusted", "admin"):
        bw = await check_upload_bandwidth(meta.ip, 0, meta.daily_limit)
        if bw["pct"] >= 80:
            warning = (
                f"You've used {bw['pct']}% of your daily upload limit "
                f"({_fmt_mb(bw['limit'])}). Resets in {bw['reset_in_hours']:.1f}h."
            )
    advisory = None
    if is_dangerous_ext(name):
        advisory = ("This file is an executable or script. Recipients should "
                    "only run it if they trust the source.")
    elif is_web_renderable_ext(name):
        advisory = ("This file can be rendered by browsers (HTML/SVG/JS). "
                    "It will only be downloaded, never run, on yeet.")

    return 201, {
        "id": file_id,
        "url": f"{meta.base_url}/f/{file_id}",
        "name": name,
        "expires_at": expires_at,
        "size": size,
        "scan_status": scan_status,
        "bypassed": meta.bypassed,
        "burn": meta.burn,
        "warning": warning,
        "dangerous": is_dangerous_ext(name),
        "advisory": advisory,
    }


async def audit(ip, ua, action, file_id, details=None) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO audit_log (action, file_id, client_ip, user_agent, details) "
        "VALUES (?,?,?,?,?)",
        (action, file_id, ip, ua, details),
    )
    await db.commit()


def base_url(request) -> str:
    return str(request.base_url).rstrip("/")
