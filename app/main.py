import asyncio
import logging
import os
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
from app.routes import admin, clipboard, download, files, upload
from app.services.cleanup import cleanup_loop

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("yeet")
_templates = Jinja2Templates(directory="templates")

_cleanup_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate()
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    settings.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    (settings.BASE_DIR / "clipboard").mkdir(parents=True, exist_ok=True)

    await init_db()
    logger.info("yeet %s started", settings.APP_VERSION)

    global _cleanup_task
    _cleanup_task = asyncio.create_task(cleanup_loop())

    yield

    _cleanup_task.cancel()
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
    from app.services.storage import get_total_storage
    from app.services.config_manager import get_storage_limit
    from app.database import get_db as _get_db
    from datetime import datetime, timezone

    total = get_total_storage()
    limit = get_storage_limit()
    db = await _get_db()
    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT COUNT(*) FROM files WHERE archived_at IS NULL AND expires_at > ?", (now,)
    ) as cur:
        active_files = (await cur.fetchone())[0]
    async with db.execute(
        "SELECT COUNT(*) FROM files WHERE archived_at IS NOT NULL"
    ) as cur:
        archived_files = (await cur.fetchone())[0]

    return JSONResponse({
        "status": "ok",
        "version": settings.APP_VERSION,
        "storage": {
            "used_bytes": total,
            "used_gb": round(total / 1_073_741_824, 3),
            "limit_gb": round(limit / 1_073_741_824, 1),
            "usage_pct": round(total / limit * 100, 1) if limit else 0,
        },
        "files": {
            "active": active_files,
            "archived": archived_files,
        },
    })


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return _templates.TemplateResponse(
        "error.html",
        {"request": request, "status": 404, "message": "Not found."},
        status_code=404,
    )


@app.exception_handler(500)
async def server_error(request: Request, exc):
    logger.exception("unhandled error: %s", exc)
    return _templates.TemplateResponse(
        "error.html",
        {"request": request, "status": 500, "message": "Internal server error."},
        status_code=500,
    )
