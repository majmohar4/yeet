"""
Public file listing and virus-log API endpoints.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.database import get_db
from app.services.storage import archive_file

router = APIRouter()


def _safe_id(file_id: str) -> str | None:
    if file_id and len(file_id) == 32 and all(c in "0123456789abcdef" for c in file_id):
        return file_id
    return None


@router.get("/api/files/all")
async def get_all_files():
    """Return all active (non-archived, non-expired) files, newest first."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT id, orig_name, file_size, mime_type, created_at, expires_at, "
        "download_count, max_downloads, scan_status, uploader_session, "
        "(password_hash IS NOT NULL) as has_password "
        "FROM files WHERE archived_at IS NULL AND expires_at > ? "
        "ORDER BY created_at DESC",
        (now,),
    ) as cursor:
        rows = [dict(r) for r in await cursor.fetchall()]
    return JSONResponse({"files": rows})


@router.delete("/api/files/{file_id}")
async def delete_file_endpoint(request: Request, file_id: str):
    """Delete a file — only the uploader (matched by session) may do this."""
    file_id = _safe_id(file_id)
    if not file_id:
        return JSONResponse({"error": "Not found."}, status_code=404)

    session = request.headers.get("X-Session-ID", "").strip()
    if not session:
        return JSONResponse({"error": "Forbidden."}, status_code=403)

    db = await get_db()
    async with db.execute(
        "SELECT uploader_session FROM files WHERE id=? AND archived_at IS NULL",
        (file_id,),
    ) as cursor:
        row = await cursor.fetchone()

    if not row:
        return JSONResponse({"error": "Not found."}, status_code=404)

    if not row[0] or row[0] != session:
        return JSONResponse({"error": "Forbidden."}, status_code=403)

    now = datetime.now(timezone.utc).isoformat()
    await archive_file(file_id)
    await db.execute("UPDATE files SET archived_at=? WHERE id=?", (now, file_id))
    await db.commit()
    return JSONResponse({"ok": True})


@router.get("/api/files")
async def get_files(ids: str = ""):
    """
    Fetch metadata for uploaded files.
    If no IDs are provided, returns all active (non-archived, non-expired) files.
    If IDs are provided, returns metadata for those specific file IDs.
    """
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    if not ids.strip():
        async with db.execute(
            "SELECT id, orig_name, file_size, mime_type, expires_at, "
            "download_count, max_downloads, archived_at, scan_status, "
            "(password_hash IS NOT NULL) as has_password "
            "FROM files WHERE archived_at IS NULL AND expires_at > ? "
            "ORDER BY created_at DESC",
            (now,),
        ) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]
        for r in rows:
            r["expired"] = False
            r["archived"] = False
        return JSONResponse({"files": rows})

    raw_ids = [i.strip() for i in ids.split(",") if i.strip()]
    # Validate: only hex IDs accepted
    safe_ids = [i for i in raw_ids if len(i) == 32 and all(c in "0123456789abcdef" for c in i)]
    if not safe_ids:
        return JSONResponse({"files": []})

    placeholders = ",".join("?" * len(safe_ids))
    async with db.execute(
        f"SELECT id, orig_name, file_size, mime_type, expires_at, "
        f"download_count, max_downloads, archived_at, scan_status, "
        f"(password_hash IS NOT NULL) as has_password "
        f"FROM files WHERE id IN ({placeholders})",
        safe_ids,
    ) as cursor:
        rows = [dict(r) for r in await cursor.fetchall()]

    for r in rows:
        r["expired"] = r["expires_at"] < now
        r["archived"] = r["archived_at"] is not None

    return JSONResponse({"files": rows})


@router.get("/api/virus-log")
async def virus_log():
    """Return the last 20 files rejected by the virus scanner."""
    db = await get_db()
    async with db.execute(
        "SELECT ts, client_ip, details FROM audit_log "
        "WHERE action='virus_detected' ORDER BY ts DESC LIMIT 20"
    ) as cursor:
        rows = [dict(r) for r in await cursor.fetchall()]

    entries = []
    for r in rows:
        filename = "unknown"
        threat = "unknown"
        if r.get("details"):
            for part in r["details"].split(","):
                if part.startswith("filename:"):
                    filename = part[9:]
                elif part.startswith("threat:"):
                    threat = part[7:]
        entries.append({
            "ts": r["ts"],
            "filename": filename,
            "threat": threat,
        })

    return JSONResponse({"deletions": entries})
