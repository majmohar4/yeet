import asyncio
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings
from app.database import get_db
from app.middleware.rate_limit import _client_ip
from app.services import cleanup as cleanup_svc
from app.services.config_manager import get_all as cfg_all
from app.services.storage import delete_file, get_total_storage

logger = logging.getLogger("yeet.admin")
router = APIRouter(prefix="/admin")
security = HTTPBasic()

# Failed logins per IP: (attempts, first_attempt_ts, locked_until_ts). In memory
# on purpose — the panel is a single process and this must not cost a DB write
# per guess.
_failures: dict[str, tuple[int, float, float]] = {}


def _lockout_remaining(ip: str) -> int:
    entry = _failures.get(ip)
    if not entry:
        return 0
    return max(0, int(entry[2] - time.monotonic()))


def _record_failure(ip: str) -> None:
    now = time.monotonic()
    window = settings.ADMIN_LOCKOUT_SECONDS
    attempts, first, _ = _failures.get(ip, (0, now, 0.0))
    if now - first > window:  # window elapsed → start counting again
        attempts, first = 0, now
    attempts += 1
    locked_until = now + window if attempts >= settings.ADMIN_MAX_ATTEMPTS else 0.0
    _failures[ip] = (attempts, first, locked_until)
    if locked_until:
        logger.warning("admin lockout: ip=%s attempts=%d", ip, attempts)
    if len(_failures) > 10_000:
        for k, v in list(_failures.items()):
            if now - v[1] > window and v[2] < now:
                _failures.pop(k, None)


async def _require_admin(
    request: Request, credentials: HTTPBasicCredentials = Depends(security)
):
    if not settings.ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin panel not configured.")

    ip = _client_ip(request)
    remaining = _lockout_remaining(ip)
    if remaining:
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Try again later.",
            headers={"Retry-After": str(remaining)},
        )

    ok = secrets.compare_digest(
        credentials.password.encode(), settings.ADMIN_PASSWORD.encode()
    )
    if not ok:
        _record_failure(ip)
        db = await get_db()  # audited: repeated failures mark the IP suspicious
        await db.execute(
            "INSERT INTO audit_log (action, client_ip, user_agent) VALUES (?,?,?)",
            ("admin_bad_login", ip, request.headers.get("user-agent", "")),
        )
        await db.commit()
        # Deliberate delay: caps an online guessing loop at ~1 try/sec even
        # before the lockout kicks in.
        await asyncio.sleep(1)
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    _failures.pop(ip, None)
    return credentials.username


@router.get("")
async def admin_dashboard(_=Depends(_require_admin)):
    db = await get_db()
    async with db.execute(
        "SELECT * FROM files ORDER BY created_at DESC LIMIT 100"
    ) as cursor:
        files = [dict(r) for r in await cursor.fetchall()]

    async with db.execute("SELECT COUNT(*) FROM audit_log") as cursor:
        log_count = (await cursor.fetchone())[0]

    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT COUNT(*) FROM bypass_codes WHERE used=0 AND expires_at > ?", (now,)
    ) as cursor:
        active_codes = (await cursor.fetchone())[0]

    async with db.execute(
        "SELECT COUNT(*) FROM files WHERE archived_at IS NULL AND expires_at > ?", (now,)
    ) as cursor:
        active_files = (await cursor.fetchone())[0]

    async with db.execute("SELECT COUNT(*) FROM files WHERE archived_at IS NOT NULL") as cursor:
        archived_files = (await cursor.fetchone())[0]

    return JSONResponse(
        {
            "files": files,
            "storage_used": get_total_storage(),
            "storage_limit": cfg_all()["storage_limit"],
            "log_entries": log_count,
            "active_bypass_codes": active_codes,
            "file_counts": {"active": active_files, "archived": archived_files},
            "config": cfg_all(),
        }
    )


@router.delete("/files/{file_id}")
async def admin_delete(file_id: str, _=Depends(_require_admin)):
    if not (len(file_id) == 32 and all(c in "0123456789abcdef" for c in file_id)):
        raise HTTPException(status_code=404, detail="File not found.")
    db = await get_db()
    async with db.execute("SELECT id FROM files WHERE id=?", (file_id,)) as cursor:
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="File not found.")
    await delete_file(file_id)
    await db.execute(
        "UPDATE files SET archived_at=datetime('now') WHERE id=?", (file_id,)
    )
    await db.commit()
    return JSONResponse({"deleted": file_id})


@router.post("/cleanup")
async def admin_cleanup(_=Depends(_require_admin)):
    result = await cleanup_svc.run_cleanup()
    return JSONResponse(result)


@router.get("/logs")
async def admin_logs(_=Depends(_require_admin), limit: int = 200):
    limit = min(limit, 1000)
    db = await get_db()
    async with db.execute(
        "SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,)
    ) as cursor:
        logs = [dict(r) for r in await cursor.fetchall()]
    return JSONResponse({"logs": logs})


@router.get("/codes")
async def list_codes(_=Depends(_require_admin)):
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT code, created_at, expires_at, used FROM bypass_codes "
        "WHERE expires_at > ? AND used=0 ORDER BY expires_at",
        (now,),
    ) as cursor:
        codes = [dict(r) for r in await cursor.fetchall()]
    return JSONResponse({"codes": codes})


@router.delete("/codes/{code}")
async def revoke_code(code: str, _=Depends(_require_admin)):
    db = await get_db()
    await db.execute("DELETE FROM bypass_codes WHERE code=?", (code.upper(),))
    await db.commit()
    return JSONResponse({"revoked": code.upper()})


@router.get("/health")
async def health():
    return JSONResponse({"status": "ok", "version": settings.APP_VERSION})
