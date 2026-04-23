import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Optional

import bcrypt
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import settings
from app.database import get_db
from app.middleware.rate_limit import _client_ip, record_action
from app.services.storage import get_file_path

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# ── Signed download tokens (5-minute single-use for password-protected files) ─

def _make_dl_token(file_id: str) -> str:
    exp = int(time.time()) + 300
    msg = f"{file_id}:{exp}".encode()
    sig = hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()
    return f"{exp}:{sig}"


def _verify_dl_token(file_id: str, token: str) -> bool:
    try:
        exp_s, sig = token.split(":", 1)
        exp = int(exp_s)
        if time.time() > exp:
            return False
        msg = f"{file_id}:{exp}".encode()
        expected = hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return dict(row)


@router.get("/f/{file_id}")
async def download_page(request: Request, file_id: str, token: Optional[str] = Query(default=None)):
    file_id = _safe_id(file_id)
    if not file_id:
        return _error(request, 404, "File not found.")

    record = await _get_active_file(file_id)
    if record is None:
        return _error(request, 404, "File not found or has expired.")

    if record["password_hash"]:
        # Accept valid signed token (from /api/verify-password)
        if token and _verify_dl_token(file_id, token):
            return await _serve_file(request, record)
        return templates.TemplateResponse(
            "download.html",
            {"request": request, "file": record, "needs_password": True, "error": None},
        )

    return await _serve_file(request, record)


@router.post("/f/{file_id}")
async def download_with_password(
    request: Request, file_id: str, password: str = Form(default="")
):
    file_id = _safe_id(file_id)
    if not file_id:
        return _error(request, 404, "File not found.")

    record = await _get_active_file(file_id)
    if record is None:
        return _error(request, 404, "File not found or has expired.")

    if record["password_hash"]:
        if not password or not bcrypt.checkpw(
            password.encode(), record["password_hash"].encode()
        ):
            await _audit(_client_ip(request), request, "download_bad_password", file_id)
            return templates.TemplateResponse(
                "download.html",
                {
                    "request": request,
                    "file": record,
                    "needs_password": True,
                    "error": "Incorrect password.",
                },
                status_code=403,
            )

    return await _serve_file(request, record)


class VerifyPasswordRequest(BaseModel):
    file_id: str
    password: str


@router.post("/api/verify-password")
async def verify_password(body: VerifyPasswordRequest, request: Request):
    """Verify password for a protected file and return a signed download URL."""
    file_id = _safe_id(body.file_id)
    if not file_id:
        return JSONResponse({"success": False, "error": "File not found."})

    record = await _get_active_file(file_id)
    if record is None:
        return JSONResponse({"success": False, "error": "File not found or has expired."})

    if not record["password_hash"]:
        return JSONResponse({"success": True, "download_url": f"/f/{file_id}"})

    if not body.password or not bcrypt.checkpw(body.password.encode(), record["password_hash"].encode()):
        ip = _client_ip(request)
        await _audit(ip, request, "download_bad_password", file_id)
        return JSONResponse({"success": False, "error": "Incorrect password."})

    token = _make_dl_token(file_id)
    return JSONResponse({"success": True, "download_url": f"/f/{file_id}?token={token}"})


@router.get("/raw/{file_id}")
async def serve_raw(request: Request, file_id: str):
    """Serve raw file content inline (used for preview embedding)."""
    file_id = _safe_id(file_id)
    if not file_id:
        return _error(request, 404, "File not found.")

    record = await _get_active_file(file_id)
    if record is None:
        return _error(request, 404, "File not found or has expired.")

    if record["password_hash"]:
        return _error(request, 403, "Password required.")

    path = get_file_path(file_id)
    if not path.exists():
        return _error(request, 404, "File data missing.")

    data = path.read_bytes()
    mime = record["mime_type"] or "application/octet-stream"
    return Response(
        content=data,
        media_type=mime,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline';",
        },
    )


_PREVIEW_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp"}


@router.get("/preview/{file_id}")
async def preview_file(request: Request, file_id: str):
    """Render a preview page for images and PDFs."""
    file_id = _safe_id(file_id)
    if not file_id:
        return _error(request, 404, "File not found.")

    record = await _get_active_file(file_id)
    if record is None:
        return _error(request, 404, "File not found or has expired.")

    if record["password_hash"]:
        return templates.TemplateResponse(
            "download.html",
            {"request": request, "file": record, "needs_password": True, "error": None},
        )

    orig = record["orig_name"].lower()
    ext = "." + orig.rsplit(".", 1)[-1] if "." in orig else ""
    if ext in _PREVIEW_IMAGE_EXTS:
        preview_type = "image"
    elif ext == ".pdf":
        preview_type = "pdf"
    else:
        preview_type = None

    return templates.TemplateResponse(
        "preview.html",
        {"request": request, "file": record, "preview_type": preview_type},
    )


async def _serve_file(request: Request, record: dict) -> Response:
    file_id = record["id"]
    path = get_file_path(file_id)

    if not path.exists():
        return _error(request, 404, "File data missing.")

    # Check max downloads
    if record["max_downloads"] and record["download_count"] >= record["max_downloads"]:
        return _error(request, 410, "Download limit reached.")

    ip = _client_ip(request)
    await record_action(ip, "download")
    await _audit(ip, request, "download", file_id)

    db = await get_db()
    await db.execute(
        "UPDATE files SET download_count = download_count + 1 WHERE id=?", (file_id,)
    )
    await db.commit()

    data = path.read_bytes()
    mime = record["mime_type"] or "application/octet-stream"

    safe_name = record["orig_name"].replace('"', "").replace("\\", "")

    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Content-Length": str(len(data)),
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _get_active_file(file_id: str) -> dict | None:
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT * FROM files WHERE id=? AND archived_at IS NULL AND expires_at > ?",
        (file_id, now),
    ) as cursor:
        row = await cursor.fetchone()
    return _row_to_dict(row)


def _safe_id(file_id: str) -> str | None:
    """Allow only hex file IDs (UUID without dashes)."""
    if file_id and len(file_id) == 32 and all(c in "0123456789abcdef" for c in file_id):
        return file_id
    return None


def _error(request: Request, status: int, message: str) -> Response:
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "status": status, "message": message},
        status_code=status,
    )


async def _audit(ip, request, action, file_id, details=None):
    db = await get_db()
    await db.execute(
        "INSERT INTO audit_log (action, file_id, client_ip, user_agent, details) VALUES (?,?,?,?,?)",
        (action, file_id, ip, request.headers.get("user-agent", ""), details),
    )
    await db.commit()
