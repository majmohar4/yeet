import asyncio
import logging
import os
import sqlite3
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import close_db, init_db
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.security import SecurityHeadersMiddleware
from app.routes import admin, bundles, chunked, clipboard, download, files, upload
from app.services import virus_scan
from app.services.cleanup import cleanup_loop

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("yeet")
_templates = Jinja2Templates(directory="templates")

# Cache-bust static assets — recomputed on every container start.
_ASSET_VERSION = f"{settings.APP_VERSION}-{int(time.time())}"

def _filesize(n) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    n, i = float(n or 0), 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.0f} {units[i]}" if n >= 10 or n == int(n) else f"{n:.1f} {units[i]}"


def _share_globals():
    """Make `asset_version` available in every Jinja env that renders base.html."""
    targets = [_templates]
    for mod in (admin, bundles, chunked, clipboard, download, files, upload):
        t = getattr(mod, "templates", None)
        if t is not None:
            targets.append(t)
    for t in targets:
        t.env.globals["asset_version"] = _ASSET_VERSION
        t.env.globals["contact_email"] = settings.CONTACT_EMAIL
        t.env.globals["app_version"] = settings.APP_VERSION
        t.env.globals["chunk_mb"] = settings.CHUNK_SIZE // 1_048_576
        t.env.filters["filesize"] = _filesize

_share_globals()

_cleanup_task: asyncio.Task | None = None
_clamd_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate()
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    settings.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    (settings.BASE_DIR / "clipboard").mkdir(parents=True, exist_ok=True)
    settings.PARTIAL_DIR.mkdir(parents=True, exist_ok=True)

    await init_db()
    await chunked.recover_interrupted()
    logger.info("yeet %s started", settings.APP_VERSION)

    global _cleanup_task, _clamd_task
    _cleanup_task = asyncio.create_task(cleanup_loop())
    _clamd_task = asyncio.create_task(virus_scan.clamd_supervisor_loop())

    yield

    _cleanup_task.cancel()
    _clamd_task.cancel()
    await virus_scan.stop_clamd()
    await close_db()
    logger.info("yeet stopped")


app = FastAPI(
    title="yeet",
    version=settings.APP_VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(upload.router)
app.include_router(download.router)
app.include_router(files.router)
app.include_router(admin.router)
app.include_router(clipboard.router)
app.include_router(bundles.router)
app.include_router(chunked.router)


@app.get("/c/{item_id}/raw")
async def clipboard_raw(item_id: str):
    from app.database import get_db as _get_db
    from datetime import datetime, timezone
    from fastapi.responses import PlainTextResponse, Response as _Response

    db = await _get_db()
    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT type, content FROM clipboard_items WHERE id=? AND (pinned=1 OR expires_at > ?)",
        (item_id, now),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return _Response(status_code=404)
    if row[0] == "text":
        return PlainTextResponse(row[1])
    # image: redirect to image endpoint
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/api/clipboard/image/{item_id}")


@app.get("/c/{item_id}")
async def clipboard_view(request: Request, item_id: str):
    from app.database import get_db as _get_db
    from datetime import datetime, timezone

    db = await _get_db()
    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT id,type,content,preview,encrypted,pinned,created_at,expires_at "
        "FROM clipboard_items WHERE id=? AND (pinned=1 OR expires_at > ?)",
        (item_id, now),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return _templates.TemplateResponse(
            "error.html",
            {"request": request, "status": 404, "message": "Clipboard item not found or expired."},
            status_code=404,
        )
    item = dict(row)
    if item["type"] == "image":
        item["image_url"] = f"/api/clipboard/image/{item_id}"
    from app.services.config_manager import get_file_expiry_hours
    return _templates.TemplateResponse(
        "clipboard_view.html",
        {
            "request": request,
            "item": item,
            "expiry_hours": get_file_expiry_hours(),
            "max_file_mb": 100,
            "storage_blocked": False,
        },
    )


@app.get("/health")
async def health():
    """Liveness only. Storage totals and file counts are operational detail —
    they live behind admin auth at /admin, not on an unauthenticated endpoint."""
    return JSONResponse({"status": "ok", "version": settings.APP_VERSION})


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return _templates.TemplateResponse(
        "error.html",
        {"request": request, "status": 404, "message": "Not found."},
        status_code=404,
    )


async def _disk_full(request: Request, exc: Exception):
    from app.services.ingest import disk_full_message
    from app.services.storage import is_disk_full_error

    if not is_disk_full_error(exc):
        return await server_error(request, exc)
    logger.error("disk full: %s %s — %s", request.method, request.url.path, exc)
    return JSONResponse({"error": disk_full_message()}, status_code=507)


@app.exception_handler(500)
async def server_error(request: Request, exc):
    logger.error("unhandled error on %s %s: %r", request.method, request.url.path, exc)
    if "text/html" not in request.headers.get("accept", ""):
        return JSONResponse({"error": "Internal server error."}, status_code=500)
    return _templates.TemplateResponse(
        "error.html",
        {"request": request, "status": 500, "message": "Internal server error."},
        status_code=500,
    )


# A full disk used to surface as a bare 500 with a traceback in the logs. These
# map ENOSPC / "database or disk is full" to a 507 the UI can explain.
app.add_exception_handler(OSError, _disk_full)
app.add_exception_handler(sqlite3.OperationalError, _disk_full)
