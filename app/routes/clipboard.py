import base64
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.database import get_db
from app.middleware.rate_limit import _client_ip, record_action

logger = logging.getLogger("yeet.clipboard")
router = APIRouter()

CLIPBOARD_DIR = settings.BASE_DIR / "clipboard"
MAX_TEXT_LENGTH = 100_000
MAX_IMAGE_SIZE = 10 * 1_048_576
ALLOWED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
CLIPBOARD_RATE_LIMIT = 20


def _gen_id() -> str:
    return secrets.token_urlsafe(6)[:8]


async def _check_rate(ip: str) -> bool:
    db = await get_db()
    window = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    async with db.execute(
        "SELECT COUNT(*) FROM rate_limits WHERE ip=? AND action='clipboard_paste' AND ts >= ?",
        (ip, window),
    ) as cur:
        return (await cur.fetchone())[0] < CLIPBOARD_RATE_LIMIT


async def _unique_id() -> str:
    db = await get_db()
    for _ in range(10):
        cid = _gen_id()
        async with db.execute("SELECT id FROM clipboard_items WHERE id=?", (cid,)) as cur:
            if not await cur.fetchone():
                return cid
    return secrets.token_urlsafe(12)[:12]


@router.post("/api/clipboard/paste")
async def paste_clipboard(request: Request):
    ip = _client_ip(request)
    if not await _check_rate(ip):
        return JSONResponse({"error": "Rate limit: max 20 pastes per hour."}, status_code=429)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON."}, status_code=400)

    content_type = body.get("content_type", "text")
    content = body.get("content", "")
    encrypt = bool(body.get("encrypt", False))
    pin = bool(body.get("pin", False))
    expiry_minutes = max(1, min(int(body.get("expiry_minutes", 60)), 10_080))
    session_id = str(body.get("session_id", "")).strip()[:64] or None

    if content_type == "image":
        if not isinstance(content, str) or not content.startswith("data:"):
            return JSONResponse({"error": "Invalid image data."}, status_code=400)
        try:
            header, b64data = content.split(",", 1)
            mime = header.split(";")[0].replace("data:", "")
            if mime not in ALLOWED_IMAGE_MIMES:
                return JSONResponse({"error": f"Image type '{mime}' not allowed."}, status_code=400)
            img_bytes = base64.b64decode(b64data)
        except Exception:
            return JSONResponse({"error": "Failed to decode image."}, status_code=400)

        if len(img_bytes) > MAX_IMAGE_SIZE:
            return JSONResponse({"error": "Image too large. Max 10 MB."}, status_code=413)

        ext_map = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
        ext = ext_map.get(mime, "png")
        item_id = await _unique_id()
        CLIPBOARD_DIR.mkdir(parents=True, exist_ok=True)
        (CLIPBOARD_DIR / f"{item_id}.{ext}").write_bytes(img_bytes)
        db_content = f"{item_id}.{ext}"
        preview = f"/api/clipboard/image/{item_id}"

    elif content_type == "text":
        if not isinstance(content, str) or not content.strip():
            return JSONResponse({"error": "Content cannot be empty."}, status_code=400)
        if len(content) > MAX_TEXT_LENGTH:
            return JSONResponse({"error": "Text too long. Max 100,000 characters."}, status_code=413)
        item_id = await _unique_id()
        db_content = content
        preview = content[:120].replace("\n", " ")

    else:
        return JSONResponse({"error": "content_type must be 'text' or 'image'."}, status_code=400)

    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)).isoformat()
    db = await get_db()
    await db.execute(
        "INSERT INTO clipboard_items (id,type,content,preview,encrypted,pinned,expires_at,session_id,client_ip) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (item_id, content_type, db_content, preview,
         1 if encrypt else 0, 1 if pin else 0, expires_at, session_id, ip),
    )
    await db.commit()
    await record_action(ip, "clipboard_paste")

    share_url = str(request.base_url).rstrip("/") + f"/c/{item_id}"
    return JSONResponse({"id": item_id, "share_url": share_url, "expires_at": expires_at}, status_code=201)


@router.get("/api/clipboard/item/{item_id}")
async def get_clipboard_item(item_id: str):
    if not item_id or len(item_id) > 20:
        return JSONResponse({"error": "Not found."}, status_code=404)
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT id,type,content,encrypted,pinned,created_at,expires_at "
        "FROM clipboard_items WHERE id=? AND (pinned=1 OR expires_at > ?)",
        (item_id, now),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return JSONResponse({"error": "Not found or expired."}, status_code=404)
    data = dict(row)
    if data["type"] == "image":
        data["content"] = f"/api/clipboard/image/{item_id}"
    return JSONResponse(data)


@router.get("/api/clipboard/image/{item_id}")
async def get_clipboard_image(item_id: str):
    if not item_id or len(item_id) > 20:
        return Response(status_code=404)
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT content FROM clipboard_items WHERE id=? AND type='image' AND (pinned=1 OR expires_at > ?)",
        (item_id, now),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return Response(status_code=404)
    img_path = CLIPBOARD_DIR / row[0]
    if not img_path.exists():
        return Response(status_code=404)
    ext = row[0].rsplit(".", 1)[-1].lower()
    mime_map = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
    return Response(content=img_path.read_bytes(), media_type=mime_map.get(ext, "image/png"))


@router.get("/api/clipboard/recent")
async def get_recent_clipboard(request: Request, limit: int = 20):
    session_id = request.headers.get("X-Session-ID", "").strip()
    if not session_id:
        return JSONResponse({"items": []})
    limit = max(1, min(limit, 50))
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT id,type,preview,encrypted,pinned,created_at,expires_at "
        "FROM clipboard_items WHERE session_id=? AND (pinned=1 OR expires_at > ?) "
        "ORDER BY created_at DESC LIMIT ?",
        (session_id, now, limit),
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return JSONResponse({"items": rows})


@router.post("/api/clipboard/pin/{item_id}")
async def toggle_pin(item_id: str, request: Request):
    session_id = request.headers.get("X-Session-ID", "").strip()
    db = await get_db()
    async with db.execute(
        "SELECT pinned FROM clipboard_items WHERE id=? AND session_id=?",
        (item_id, session_id),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return JSONResponse({"error": "Not found."}, status_code=404)
    new_pin = 0 if row[0] else 1
    await db.execute("UPDATE clipboard_items SET pinned=? WHERE id=?", (new_pin, item_id))
    await db.commit()
    return JSONResponse({"pinned": bool(new_pin)})


@router.delete("/api/clipboard/item/{item_id}")
async def delete_clipboard_item(item_id: str, request: Request):
    session_id = request.headers.get("X-Session-ID", "").strip()
    db = await get_db()
    async with db.execute(
        "SELECT type,content FROM clipboard_items WHERE id=? AND session_id=?",
        (item_id, session_id),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return JSONResponse({"error": "Not found."}, status_code=404)
    if row[0] == "image":
        img_path = CLIPBOARD_DIR / row[1]
        if img_path.exists():
            img_path.unlink()
    await db.execute("DELETE FROM clipboard_items WHERE id=?", (item_id,))
    await db.commit()
    return JSONResponse({"ok": True})
