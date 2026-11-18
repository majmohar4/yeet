"""
Public file listing and virus-log API endpoints.
"""
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import get_db
from app.routes.upload import EXECUTABLE_EXTENSIONS, WEB_RENDERABLE_EXTENSIONS
from app.services.storage import archive_file


def _annotate_dangerous(rows: list[dict]) -> list[dict]:
    for r in rows:
        name = (r.get("orig_name") or "").lower()
        ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
        r["dangerous"] = ext in EXECUTABLE_EXTENSIONS
        r["web_renderable"] = ext in WEB_RENDERABLE_EXTENSIONS
    return rows

router = APIRouter()


def _safe_id(file_id: str) -> str | None:
    if file_id and len(file_id) == 32 and all(c in "0123456789abcdef" for c in file_id):
        return file_id
    return None


def owns(session: str, owner: str | None) -> bool:
    """Constant-time ownership check for a session id.

    Compared as bytes — compare_digest raises TypeError on non-ASCII str, and
    the session arrives in a client-controlled header.
    """
    if not session or not owner:
        return False
    return secrets.compare_digest(session.encode("utf-8"), owner.encode("utf-8"))


def annotate_ownership(rows: list[dict], session: str) -> list[dict]:
    """Replace each row's `uploader_session` with a boolean `is_yours`.

    The session id is the only thing standing between a visitor and deleting
    someone else's upload, so it must never leave the server. The client only
    needs to know whether a row is its own, to decide if it renders a delete
    button.
    """
    for r in rows:
        r["is_yours"] = owns(session, r.pop("uploader_session", None))
    return rows


@router.get("/api/files/all")
async def get_all_files(request: Request):
    """Return active loose files + bundles (folders), newest first.

    Files that belong to a bundle are hidden from this listing — they show up
    inside the bundle's /b/{id} view instead.
    """
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    session = request.headers.get("X-Session-ID", "").strip()

    # PUBLIC_LISTING=false narrows the listing to the caller's own uploads, so
    # filenames and sizes aren't readable by anyone who hits the endpoint.
    scope = "" if settings.PUBLIC_LISTING else "AND uploader_session = ? "
    params: tuple = (now,) if settings.PUBLIC_LISTING else (now, session)
    if not settings.PUBLIC_LISTING and not session:
        return JSONResponse({"files": [], "bundles": []})

    async with db.execute(
        "SELECT id, orig_name, file_size, mime_type, created_at, expires_at, "
        "download_count, max_downloads, scan_status, uploader_session, "
        "burn_after_read, claimed_at, "
        "(password_hash IS NOT NULL) as has_password "
        "FROM files WHERE archived_at IS NULL AND expires_at > ? "
        "AND bundle_id IS NULL " + scope +
        "ORDER BY created_at DESC",
        params,
    ) as cursor:
        files_rows = _annotate_dangerous([dict(r) for r in await cursor.fetchall()])

    async with db.execute(
        "SELECT id, name, file_count, total_size, created_at, expires_at, "
        "download_count, uploader_session, "
        "(password_hash IS NOT NULL) AS has_password "
        "FROM bundles WHERE archived_at IS NULL AND pending=0 AND expires_at > ? " + scope +
        "ORDER BY created_at DESC",
        params,
    ) as cursor:
        bundle_rows = [dict(r) for r in await cursor.fetchall()]

    return JSONResponse({
        "files": annotate_ownership(files_rows, session),
        "bundles": annotate_ownership(bundle_rows, session),
    })


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

    if not owns(session, row[0]):
        return JSONResponse({"error": "Forbidden."}, status_code=403)

    now = datetime.now(timezone.utc).isoformat()
    await archive_file(file_id)
    await db.execute("UPDATE files SET archived_at=? WHERE id=?", (now, file_id))
    await db.commit()
    return JSONResponse({"ok": True})


@router.get("/api/files")
async def get_files(ids: str = ""):
    """Fetch metadata for specific file IDs (comma-separated hex). Returns [] if ids is empty."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    if not ids.strip():
        return JSONResponse({"files": []})

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
