import json
import logging
import uuid
from pathlib import Path

import bcrypt
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from app.middleware.rate_limit import _client_ip, peek_bypass_code
from app.services import concurrent, ingest, storage, tiers
from app.services.config_manager import (
    get_file_expiry_hours,
    get_storage_limit,
)

logger = logging.getLogger("yeet.upload")
router = APIRouter()
templates = Jinja2Templates(directory="templates")

from app.services.extensions import (  # noqa: F401  (re-exported for other routes)
    ALLOWED_EXTENSIONS,
    EXECUTABLE_EXTENSIONS,
    SAFE_EXTENSIONS,
    WEB_RENDERABLE_EXTENSIONS,
    _check_extension,
    is_dangerous_ext,
    is_web_renderable_ext,
)


@router.get("/")
async def index(request: Request):
    total = storage.get_total_storage()
    limit = get_storage_limit()
    pct = round(total / limit * 100, 1) if limit else 0
    disk_low = not storage.has_room_for(0)
    tier = await tiers.tier_for(request, _client_ip(request))
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "storage_pct": pct,
            "storage_warning": pct >= 80 or disk_low,
            "storage_blocked": (pct >= 90 and not tier.trusted) or disk_low,
            "max_file_mb": tier.max_file // 1_048_576,
            "max_file_bytes": tier.max_file,
            "tier": tier.name,
            "unlock_available": tiers.unlock_enabled(),
            "expiry_hours": get_file_expiry_hours(),
        },
    )


async def spool(upload: UploadFile, max_bytes: int | None) -> tuple[Path | JSONResponse, int]:
    """Copy a multipart UploadFile into PARTIAL_DIR without holding it in memory.

    Returns (path, size), or (JSONResponse, 0) when it doesn't fit.
    """
    path = storage.partial_path("spool-" + uuid.uuid4().hex)
    size = 0
    try:
        if not storage.has_room_for(0):
            return ingest.disk_full_response(), 0
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as out:
            while chunk := await upload.read(1_048_576):
                size += len(chunk)
                if max_bytes is not None and size > max_bytes:
                    out.close()
                    path.unlink(missing_ok=True)
                    return ingest.error(
                        f"File too large. Maximum is {max_bytes // 1_048_576} MB.", 413
                    ), 0
                out.write(chunk)
    except OSError as exc:
        path.unlink(missing_ok=True)
        if storage.is_disk_full_error(exc):
            return ingest.disk_full_response(), 0
        raise
    return path, size


async def handle_multipart(
    request: Request,
    upload: UploadFile,
    *,
    password: str = "",
    max_downloads: str = "",
    bypass_code: str = "",
    session_id: str = "",
    expiry_hours: str = "",
    burn: str = "",
) -> JSONResponse:
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")
    bypassed = await peek_bypass_code(bypass_code)

    acquired = await concurrent.acquire(ip)
    if not acquired and not bypassed:
        return ingest.error(
            "Please wait for your current uploads to finish (max 2 at once).", 429
        )
    try:
        tier = await tiers.tier_for(request, ip)
        declared = int(request.headers.get("content-length") or 0)
        early = await ingest.precheck(max(0, declared - 4096), ip, bypassed, tier)
        if early is not None:
            return early

        result, size = await spool(upload, None if bypassed else tier.max_file)
        if isinstance(result, JSONResponse):
            return result
        tmp = result
        late = await ingest.precheck(size, ip, bypassed, tier)
        if late is not None:
            tmp.unlink(missing_ok=True)
            return late

        is_burn = ingest.parse_burn(burn)
        meta = ingest.UploadMeta(
            orig_name=upload.filename or "file",
            ip=ip,
            ua=ua,
            base_url=ingest.base_url(request),
            password_hash=hash_pw(password),
            max_downloads=ingest.parse_max_downloads(max_downloads, is_burn),
            burn=is_burn,
            expiry_hours=ingest.parse_expiry(expiry_hours),
            session=ingest.parse_session(session_id),
            bypassed=bypassed,
            bypass_code=bypass_code,
            tier=tier.name,
            daily_limit=tier.daily,
        )
        status, body = await ingest.finalize(tmp, meta)
        return JSONResponse(body, status_code=status)
    finally:
        if acquired:
            await concurrent.release(ip)


def hash_pw(password: str) -> str | None:
    if not password:
        return None
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    password: str = Form(default=""),
    max_downloads: str = Form(default=""),
    bypass_code: str = Form(default=""),
    session_id: str = Form(default=""),
    expiry_hours: str = Form(default=""),
    burn: str = Form(default=""),
    website: str = Form(default=""),  # honeypot
):
    if website:
        ip = _client_ip(request)
        logger.warning("honeypot triggered: ip=%s", ip)
        await ingest.audit(ip, request.headers.get("user-agent", ""), "bot_rejected",
                           None, f"honeypot={website[:32]}")
        return ingest.error("Invalid request.", 400)

    return await handle_multipart(
        request, file, password=password, max_downloads=max_downloads,
        bypass_code=bypass_code, session_id=session_id,
        expiry_hours=expiry_hours, burn=burn,
    )


@router.post("/raw")
async def upload_raw(
    request: Request,
    f: UploadFile = File(...),
    burn: str = Form(default=""),
    expiry_hours: str = Form(default=""),
    bypass_code: str = Form(default=""),
):
    """curl-friendly: `curl -F f=@thing.txt https://yeet/raw` → URL on stdout."""
    result = await handle_multipart(
        request, f, burn=burn, expiry_hours=expiry_hours, bypass_code=bypass_code,
    )
    body = json.loads(result.body.decode() or "{}")
    if result.status_code != 201:
        return PlainTextResponse(
            f"error: {body.get('error', 'upload failed')}\n", status_code=result.status_code
        )
    return PlainTextResponse(body["url"] + "\n", status_code=201)
