"""
Rate limiting middleware.

Downloads: count-based, per IP, 1-hour sliding window.
Uploads:   byte-based, per IP, 24-hour rolling window (enforced in upload route).
Bypass:    valid bypass code skips all upload limits.
"""
from datetime import datetime, timedelta, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings
from app.database import get_db
from app.services.config_manager import get_daily_upload_limit


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── Bypass code helpers ───────────────────────────────────────────────────────

async def validate_bypass_code(code: str) -> bool:
    """Return True and mark code used if valid and not expired."""
    if not code or not code.strip():
        return False
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT code FROM bypass_codes WHERE code=? AND used=0 AND expires_at > ?",
        (code.strip().upper(), now),
    ) as cursor:
        row = await cursor.fetchone()
    if row:
        await db.execute("UPDATE bypass_codes SET used=1 WHERE code=?", (code.strip().upper(),))
        await db.commit()
        return True
    return False


# ── Upload bandwidth helpers ──────────────────────────────────────────────────

async def get_used_bandwidth(ip: str) -> int:
    """Bytes uploaded by this IP in the last 24 hours."""
    db = await get_db()
    window = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    async with db.execute(
        "SELECT COALESCE(SUM(bytes), 0) FROM upload_bandwidth WHERE ip=? AND ts >= ?",
        (ip, window),
    ) as cursor:
        return (await cursor.fetchone())[0]


async def record_bandwidth(ip: str, bytes_used: int) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO upload_bandwidth (ip, bytes) VALUES (?, ?)", (ip, bytes_used)
    )
    # Prune entries older than 25h
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    await db.execute("DELETE FROM upload_bandwidth WHERE ts < ?", (cutoff,))
    await db.commit()


async def check_upload_bandwidth(ip: str, upload_size: int) -> dict:
    """
    Returns dict with:
      allowed: bool
      used: int (bytes)
      limit: int (bytes)
      pct: float
      remaining: int (bytes)
      reset_in_hours: float
    """
    daily_limit = get_daily_upload_limit()
    used = await get_used_bandwidth(ip)
    pct = (used / daily_limit * 100) if daily_limit else 0
    remaining = max(0, daily_limit - used)
    allowed = (used + upload_size) <= daily_limit

    # Estimate when the oldest entry in the window expires
    db = await get_db()
    window = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    async with db.execute(
        "SELECT MIN(ts) FROM upload_bandwidth WHERE ip=? AND ts >= ?", (ip, window)
    ) as cursor:
        oldest = (await cursor.fetchone())[0]

    reset_in = 0.0
    if oldest:
        try:
            oldest_dt = datetime.fromisoformat(oldest.replace("Z", "+00:00"))
            reset_dt = oldest_dt + timedelta(hours=24)
            now = datetime.now(timezone.utc)
            if reset_dt > now:
                reset_in = (reset_dt - now).total_seconds() / 3600
        except Exception:
            pass

    return {
        "allowed": allowed,
        "used": used,
        "limit": daily_limit,
        "pct": round(pct, 1),
        "remaining": remaining,
        "reset_in_hours": round(reset_in, 1),
    }


# ── Download count helpers ────────────────────────────────────────────────────

async def check_download_rate(ip: str) -> tuple[bool, int]:
    db = await get_db()
    window = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    await db.execute(
        "DELETE FROM rate_limits WHERE ip=? AND action='download' AND ts < ?",
        (ip, window),
    )
    await db.commit()
    async with db.execute(
        "SELECT COUNT(*) FROM rate_limits WHERE ip=? AND action='download' AND ts >= ?",
        (ip, window),
    ) as cursor:
        count = (await cursor.fetchone())[0]
    return count < settings.RATE_LIMIT_DOWNLOADS, max(0, settings.RATE_LIMIT_DOWNLOADS - count)


async def record_action(ip: str, action: str) -> None:
    db = await get_db()
    await db.execute("INSERT INTO rate_limits (ip, action) VALUES (?, ?)", (ip, action))
    await db.commit()


async def record_download(ip: str) -> None:
    await record_action(ip, "download")


# ── Middleware ────────────────────────────────────────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> JSONResponse:
        ip = _client_ip(request)
        path = request.url.path

        if request.method == "GET" and path.startswith("/f/"):
            allowed, remaining = await check_download_rate(ip)
            if not allowed:
                return JSONResponse(
                    {"error": "Download rate limit exceeded. Try again later."},
                    status_code=429,
                    headers={"Retry-After": "3600"},
                )

        response = await call_next(request)
        return response
