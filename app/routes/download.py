import hmac
from datetime import datetime, timezone

import bcrypt
from fastapi import APIRouter, Form, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import get_db
from app.middleware.rate_limit import _client_ip, record_action
from app.services.storage import get_file_path

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return dict(row)


@router.get("/f/{file_id}")
async def download_page(request: Request, file_id: str):
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
