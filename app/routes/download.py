import hashlib
import hmac
import re
import time
from datetime import datetime, timezone
from typing import Optional

import bcrypt
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.config import settings
from app.database import get_db
from app.middleware.rate_limit import _client_ip, check_preview_rate, record_action
from app.services.extensions import EXECUTABLE_EXTENSIONS, WEB_RENDERABLE_EXTENSIONS
from app.services.storage import content_disposition, delete_file, get_file_path

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Password-protected files: after the password is checked once, the client gets
# an HMAC token bound to the file id. It stays valid long enough for a big
# download to be resumed (browsers retry with Range on the same URL).
_TOKEN_TTL = 6 * 3600

# Link-preview crawlers fetch every URL pasted into a chat. For a burn-after-
# read file that fetch would be the one and only download, so they get the
# landing page instead of the bytes.
_PREVIEW_BOT_RE = re.compile(
    r"bot|crawler|spider|preview|facebookexternalhit|slack|discord|whatsapp|telegram|"
    r"twitter|linkedin|skype|embedly|vkshare|pinterest|redditbot|signal|mattermost|"
    r"teams|outlook|google-pagerenderer|iframely",
    re.IGNORECASE,
)

_RAW_CSP = "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline';"


def _make_dl_token(file_id: str) -> str:
    exp = int(time.time()) + _TOKEN_TTL
    msg = f"{file_id}:{exp}".encode()
    sig = hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()
    return f"{exp}:{sig}"


def _verify_dl_token(file_id: str, token: str | None) -> bool:
    if not token:
        return False
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


def _ext(name: str) -> str:
    name = (name or "").lower()
    return "." + name.rsplit(".", 1)[-1] if "." in name else ""


def _range_start(request: Request) -> int | None:
    """Start offset of a `Range: bytes=N-` request, or None without one."""
    m = re.match(r"\s*bytes=(\d*)-", request.headers.get("range", ""))
    if not m:
        return None
    return int(m.group(1)) if m.group(1) else -1  # suffix range "bytes=-500"


def _wants_page(request: Request) -> bool:
    """A browser navigating to the link gets a landing page; scripts get bytes."""
    if "dl" in request.query_params or request.headers.get("range"):
        return False
    return "text/html" in request.headers.get("accept", "")


def _preview_kind(record: dict) -> str | None:
    mime = record.get("mime_type") or ""
    ext = _ext(record.get("orig_name") or "")
    if ext in EXECUTABLE_EXTENSIONS:
        return None
    if mime.startswith("image/") and ext not in (".svg", ".svgz", ".tif", ".tiff", ".psd"):
        return "image"
    if mime.startswith("video/") or ext in (".mp4", ".webm", ".mov", ".m4v"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("text/") or ext in _TEXT_EXTS or ext in WEB_RENDERABLE_EXTENSIONS:
        return "text"
    return None


_TEXT_EXTS = {
    ".txt", ".md", ".log", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
    ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".css", ".scss", ".sql", ".rb",
    ".php", ".swift", ".dart", ".lua", ".srt", ".vtt", ".env", ".diff", ".patch",
}


def _landing(request: Request, record: dict, *, needs_password: bool,
             error: str | None = None, status: int = 200) -> Response:
    max_dl = record.get("max_downloads")
    left = None
    if max_dl:
        left = max(0, max_dl - (record.get("download_count") or 0))
    return templates.TemplateResponse(
        "download.html",
        {
            "request": request,
            "file": record,
            "needs_password": needs_password,
            "error": error,
            "preview_kind": None if record.get("burn_after_read") else _preview_kind(record),
            "downloads_left": left,
            "dangerous": _ext(record["orig_name"]) in EXECUTABLE_EXTENSIONS,
        },
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
    )


# ── /f/{id} — the public share link ──────────────────────────────────────────

@router.get("/f/{file_id}")
async def download_page(
    request: Request, file_id: str, token: Optional[str] = Query(default=None)
):
    file_id = _safe_id(file_id)
    if not file_id:
        return _error(request, 404, "File not found.")

    record = await _get_active_file(file_id)
    if record is None:
        return _error(request, 404, "This file doesn't exist or has expired.")

    unlocked = not record["password_hash"] or _verify_dl_token(file_id, token)
    is_bot = bool(_PREVIEW_BOT_RE.search(request.headers.get("user-agent", "")))

    if _wants_page(request) or (record.get("burn_after_read") and is_bot):
        return _landing(request, record, needs_password=not unlocked)
    if not unlocked:
        return _landing(request, record, needs_password=True, status=401)
    return await _serve_file(request, record)


@router.post("/f/{file_id}")
async def download_with_password(
    request: Request, file_id: str, password: str = Form(default="")
):
    """No-JS / CLI path: password in a form body, bytes in the response."""
    file_id = _safe_id(file_id)
    if not file_id:
        return _error(request, 404, "File not found.")

    record = await _get_active_file(file_id)
    if record is None:
        return _error(request, 404, "This file doesn't exist or has expired.")

    if record["password_hash"]:
        if not password or not bcrypt.checkpw(
            password.encode(), record["password_hash"].encode()
        ):
            await _audit(_client_ip(request), request, "download_bad_password", file_id)
            return _landing(request, record, needs_password=True,
                            error="Incorrect password.", status=403)
        if "text/html" in request.headers.get("accept", ""):
            # Browser form: redirect to a GET so the download can be resumed.
            token = _make_dl_token(file_id)
            return RedirectResponse(f"/f/{file_id}?dl=1&token={token}", status_code=303)

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
        return JSONResponse({"success": True, "download_url": f"/f/{file_id}?dl=1",
                             "token": ""})

    if not body.password or not bcrypt.checkpw(
        body.password.encode(), record["password_hash"].encode()
    ):
        await _audit(_client_ip(request), request, "download_bad_password", file_id)
        return JSONResponse({"success": False, "error": "Incorrect password."})

    token = _make_dl_token(file_id)
    return JSONResponse({
        "success": True,
        "token": token,
        "download_url": f"/f/{file_id}?dl=1&token={token}",
        "preview_url": f"/raw/{file_id}?token={token}",
    })


# ── /raw/{id} — inline previews ──────────────────────────────────────────────

_PREVIEWABLE_MIME_PREFIXES = ("image/", "video/", "audio/", "text/")


def _is_previewable(record: dict, ext: str) -> bool:
    if ext in _TEXT_EXTS or ext in WEB_RENDERABLE_EXTENSIONS:
        return True
    mime = record["mime_type"] or ""
    return mime == "application/pdf" or mime.startswith(_PREVIEWABLE_MIME_PREFIXES)


@router.get("/raw/{file_id}")
async def serve_raw(request: Request, file_id: str, token: Optional[str] = Query(default=None)):
    """Serve file content inline for previews (thumbnails, video, PDF, text).

    Safety:
      - Executables are refused — they only work via /f/ (forced attachment).
      - Web-renderable types (HTML, SVG, JS, WASM) are served as text/plain
        with nosniff + a strict CSP — the browser shows source, never runs it.
    Accounting: previews don't count as downloads (a grid render fires one
    request per thumbnail) but have their own per-IP ceiling, refuse spent
    files, and only serve types a preview can use; the rest redirects to /f/.
    Range requests are supported, which iOS Safari requires to play video.
    """
    file_id = _safe_id(file_id)
    if not file_id:
        return _error(request, 404, "File not found.")

    record = await _get_active_file(file_id)
    if record is None:
        return _error(request, 404, "File not found or has expired.")

    if record["password_hash"] and not _verify_dl_token(file_id, token):
        return _error(request, 403, "Password required.")

    if record.get("burn_after_read"):
        # Inline preview would silently consume the single view.
        return _error(request, 403, "Burn-after-read files must be downloaded via /f/.")

    ext = _ext(record.get("orig_name") or "")
    if ext in EXECUTABLE_EXTENSIONS:
        return _error(request, 403, "Executable files cannot be previewed; download via /f/.")

    max_dl = record["max_downloads"]
    if max_dl is not None and max_dl > 0 and record["download_count"] >= max_dl:
        return _error(request, 410, "Download limit reached.")

    if not _is_previewable(record, ext):
        return RedirectResponse(url=f"/f/{file_id}?dl=1", status_code=302)

    start = _range_start(request)
    if (start is None or start == 0) and not check_preview_rate(_client_ip(request)):
        return _error(request, 429, "Preview rate limit exceeded. Try again later.")

    path = get_file_path(file_id)
    if not path.exists():
        return _error(request, 404, "File data missing.")

    if ext in WEB_RENDERABLE_EXTENSIONS:
        media_type = "text/plain; charset=utf-8"
    elif (record["mime_type"] or "").startswith("text/") or ext in _TEXT_EXTS:
        media_type = "text/plain; charset=utf-8"
    else:
        media_type = record["mime_type"] or "application/octet-stream"

    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Content-Disposition": content_disposition(record["orig_name"], inline=True),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": _RAW_CSP,
            "Cache-Control": "private, max-age=300",
        },
    )


@router.get("/preview/{file_id}")
async def preview_file(file_id: str):
    """Old preview URL — the landing page at /f/ now shows previews."""
    file_id = _safe_id(file_id) or ""
    return RedirectResponse(url=f"/f/{file_id}", status_code=301)


# ── Serving ──────────────────────────────────────────────────────────────────

async def _serve_file(request: Request, record: dict) -> Response:
    file_id = record["id"]
    path = get_file_path(file_id)

    if not path.exists():
        return _error(request, 404, "File data missing.")

    max_dl = record["max_downloads"]
    start = _range_start(request)
    is_burn = bool(record.get("burn_after_read"))
    # A resumed download (Range starting past 0) is the same download, not a
    # new one — counting it would burn through max_downloads on a flaky line.
    continuation = start is not None and start != 0 and not is_burn

    if not continuation and max_dl is not None and max_dl > 0 \
            and record["download_count"] >= max_dl:
        return _error(request, 410, "Download limit reached.")

    ip = _client_ip(request)
    db = await get_db()
    headers = {
        "Content-Disposition": content_disposition(record["orig_name"]),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    mime = record["mime_type"] or "application/octet-stream"

    if is_burn and start is not None:
        # Partial fetches (probes, download managers) must never spend the one view.
        return _error(request, 409, "Single-download files can't be fetched in parts.")

    if is_burn:
        # Atomic claim: only one request can win. The winner streams the whole
        # file (no ranges) and it is wiped when the response finishes.
        now = datetime.now(timezone.utc).isoformat()
        cursor = await db.execute(
            "UPDATE files SET claimed_at=?, download_count=download_count+1 "
            "WHERE id=? AND claimed_at IS NULL",
            (now, file_id),
        )
        await db.commit()
        if cursor.rowcount == 0:
            return _error(request, 410, "This burn-after-read file has already been opened.")
        await record_action(ip, "download")
        await _audit(ip, request, "download_burn", file_id)
        # Mark archived now so the link is dead even while bytes are in flight.
        await db.execute("UPDATE files SET archived_at=? WHERE id=?", (now, file_id))
        await db.commit()
        size = path.stat().st_size
        fh = open(path, "rb")
        # Unlink immediately; the open handle keeps the data readable until the
        # stream finishes, and nothing else can reach it any more.
        path.unlink(missing_ok=True)

        def _iter():
            try:
                while chunk := fh.read(1_048_576):
                    yield chunk
            finally:
                fh.close()

        headers["Content-Length"] = str(size)
        return StreamingResponse(
            _iter(), media_type=mime, headers=headers,
            background=BackgroundTask(delete_file, file_id),
        )

    if not continuation:
        await db.execute(
            "UPDATE files SET download_count = download_count + 1 WHERE id=?", (file_id,)
        )
        await db.commit()
        await record_action(ip, "download")
        await _audit(ip, request, "download", file_id)

    return FileResponse(path, media_type=mime, headers=headers)


async def _get_active_file(file_id: str) -> dict | None:
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT * FROM files WHERE id=? AND archived_at IS NULL AND expires_at > ?",
        (file_id, now),
    ) as cursor:
        row = await cursor.fetchone()
    return dict(row) if row else None


def _safe_id(file_id: str) -> str | None:
    """Allow only hex file IDs (UUID without dashes)."""
    if file_id and len(file_id) == 32 and all(c in "0123456789abcdef" for c in file_id):
        return file_id
    return None


def _error(request: Request, status: int, message: str) -> Response:
    if "text/html" not in request.headers.get("accept", ""):
        return JSONResponse({"error": message}, status_code=status)
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "status": status, "message": message},
        status_code=status,
    )


async def _audit(ip, request, action, file_id, details=None):
    db = await get_db()
    await db.execute(
        "INSERT INTO audit_log (action, file_id, client_ip, user_agent, details) "
        "VALUES (?,?,?,?,?)",
        (action, file_id, ip, request.headers.get("user-agent", ""), details),
    )
    await db.commit()

