import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings
from app.database import get_db
from app.services import cleanup as cleanup_svc
from app.services.config_manager import get_all as cfg_all
from app.services.storage import delete_file, get_total_storage

router = APIRouter(prefix="/admin")
security = HTTPBasic()


def _require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    if not settings.ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin panel not configured.")
    ok = secrets.compare_digest(
        credentials.password.encode(), settings.ADMIN_PASSWORD.encode()
    )
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
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

    async with db.execute(
        "SELECT COUNT(*) FROM bypass_codes WHERE used=0 AND expires_at > ?",
        (datetime.now(timezone.utc).isoformat(),),
    ) as cursor:
        active_codes = (await cursor.fetchone())[0]

    return JSONResponse(
        {
            "files": files,
            "storage_used": get_total_storage(),
            "storage_limit": cfg_all()["storage_limit"],
            "log_entries": log_count,
            "active_bypass_codes": active_codes,
            "config": cfg_all(),
        }
    )


@router.delete("/files/{file_id}")
async def admin_delete(file_id: str, _=Depends(_require_admin)):
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
