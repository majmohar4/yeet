"""
Rate limiting middleware.

Downloads: count-based, per IP, 1-hour sliding window.
Uploads:   byte-based, per IP, 24-hour rolling window (enforced in upload route).
Bypass:    valid bypass code skips all upload limits.
"""
import ipaddress
import time
from datetime import datetime, timedelta, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings
from app.database import get_db
from app.services.config_manager import get_daily_upload_limit


def _parse_ip(value: str) -> str | None:
    """Return a normalised IP, or None if `value` isn't one."""
    v = value.strip()
    if not v:
        return None
    if v.startswith("["):  # [2001:db8::1]:443
        v = v[1:].split("]", 1)[0]
    elif v.count(":") == 1:  # 1.2.3.4:5678
        v = v.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(v))
    except ValueError:
        return None


def _client_ip(request: Request) -> str:
    """Resolve the client IP without trusting caller-supplied hops.

    The left-most X-Forwarded-For entry is written by the caller, so using it
    hands every attacker a fresh identity per request and voids all per-IP
    limits. Only two sources are trusted: a header our own proxy always
    overwrites (REAL_IP_HEADER, e.g. cloudflared's CF-Connecting-IP), and the
    Nth-from-the-right XFF entry, where N is the number of proxies we run.
    """
    if settings.REAL_IP_HEADER:
        ip = _parse_ip(request.headers.get(settings.REAL_IP_HEADER, "").split(",")[0])
        if ip:
            return ip

    hops = settings.TRUSTED_PROXY_HOPS
    if hops > 0:
        parts = [p for p in (request.headers.get("X-Forwarded-For") or "").split(",") if p.strip()]
        if len(parts) >= hops:
            ip = _parse_ip(parts[-hops])
            if ip:
                return ip

    return request.client.host if request.client else "unknown"


# ── Bypass code helpers ───────────────────────────────────────────────────────

async def peek_bypass_code(code: str) -> bool:
    """Return True if the code is currently valid — without spending it.

    Codes are single-use, so they must only be marked used once the upload has
    actually been accepted; otherwise a rejected upload (too large, malware,
    storage full) burns a code the user never got any benefit from.
    """
    if not code or not code.strip():
        return False
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        "SELECT code FROM bypass_codes WHERE code=? AND used=0 AND expires_at > ?",
        (code.strip().upper(), now),
    ) as cursor:
        return await cursor.fetchone() is not None


async def consume_bypass_code(code: str) -> bool:
    """Atomically mark a code used. False if someone else got there first."""
    if not code or not code.strip():
        return False
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        "UPDATE bypass_codes SET used=1 WHERE code=? AND used=0 AND expires_at > ?",
        (code.strip().upper(), now),
    )
    await db.commit()
    return cursor.rowcount > 0



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


# ── Preview rate limit ────────────────────────────────────────────────────────
# Kept in memory, not in `rate_limits`: a single grid render fires one /raw
# request per thumbnail, and writing a row for each would swamp the table.

_preview_hits: dict[str, list[float]] = {}


def check_preview_rate(ip: str) -> bool:
    limit = settings.RATE_LIMIT_PREVIEWS
    if limit <= 0:
        return True
    now = time.monotonic()
    cutoff = now - 3600
    hits = [t for t in _preview_hits.get(ip, []) if t >= cutoff]
    if len(hits) >= limit:
        _preview_hits[ip] = hits
        return False
    hits.append(now)
    _preview_hits[ip] = hits
    if len(_preview_hits) > 10_000:  # bound memory on a busy/abused instance
        for k in [k for k, v in _preview_hits.items() if not v or v[-1] < cutoff]:
            _preview_hits.pop(k, None)
    return True


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
