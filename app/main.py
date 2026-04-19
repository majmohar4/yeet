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
from app.routes import admin, download, files, upload
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


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "version": settings.APP_VERSION})


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
