"""
Upload limits per visitor.

  trusted     — holds a valid unlock cookie (secret UNLOCK_PASSWORD entered in
                the app): big per-file and daily limits, no storage-cap check.
  standard    — everyone else: the admin-configured per-file limit.
  suspicious  — an IP with recent abuse signals in the audit log: small limits.

The unlock cookie is an HMAC-signed, expiring token. The MAC covers a hash of
the current UNLOCK_PASSWORD, so changing the password revokes every cookie.
"""
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from starlette.requests import Request

from app.config import settings
from app.database import get_db
from app.services.config_manager import get_daily_upload_limit, get_max_file_size

COOKIE = "yeet_unlock"

# Abuse signals in the last 24 h that make an IP "suspicious": action → threshold.
SUSPICIOUS_SIGNALS = {
    "virus_detected": 1,
    "bot_rejected": 1,
    "unlock_failed": 3,
    "admin_bad_login": 3,
    "download_bad_password": 10,
}


@dataclass(frozen=True)
class Tier:
    name: str           # trusted | standard | suspicious
    max_file: int
    daily: int

    @property
    def trusted(self) -> bool:
        return self.name == "trusted"


def unlock_enabled() -> bool:
    return len(settings.UNLOCK_PASSWORD) >= 12


def _pw_fingerprint() -> str:
    return hashlib.sha256(settings.UNLOCK_PASSWORD.encode()).hexdigest()


def _sign(exp: int, nonce: str) -> str:
    msg = f"unlock|{exp}|{nonce}|{_pw_fingerprint()}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def issue_token() -> tuple[str, int]:
    ttl = settings.UNLOCK_TTL_DAYS * 86400
    exp = int(time.time()) + ttl
    nonce = secrets.token_hex(8)
    return f"{exp}.{nonce}.{_sign(exp, nonce)}", ttl


def token_expiry(token: str | None) -> int | None:
    """Expiry timestamp of a valid token, else None."""
    if not token or not unlock_enabled():
        return None
    try:
        exp_s, nonce, sig = token.split(".", 2)
        exp = int(exp_s)
    except ValueError:
        return None
    if exp < time.time() or not hmac.compare_digest(sig, _sign(exp, nonce)):
        return None
    return exp


def check_password(candidate: str) -> bool:
    if not unlock_enabled() or not candidate:
        return False
    a = hashlib.sha256(candidate.encode()).digest()
    b = hashlib.sha256(settings.UNLOCK_PASSWORD.encode()).digest()
    return hmac.compare_digest(a, b)


async def is_suspicious(ip: str) -> bool:
    db = await get_db()
    async with db.execute(
        "SELECT action, COUNT(*) FROM audit_log WHERE client_ip=? "
        "AND ts >= datetime('now', '-24 hours') GROUP BY action",
        (ip,),
    ) as cur:
        counts = {row[0]: row[1] for row in await cur.fetchall()}
    return any(counts.get(a, 0) >= n for a, n in SUSPICIOUS_SIGNALS.items())


def by_name(name: str | None) -> Tier:
    if name == "trusted":
        return Tier("trusted", settings.TRUSTED_MAX_FILE_SIZE, settings.TRUSTED_DAILY_LIMIT)
    if name == "suspicious":
        return Tier("suspicious", settings.SUSPICIOUS_MAX_FILE_SIZE,
                    settings.SUSPICIOUS_DAILY_LIMIT)
    return Tier("standard", get_max_file_size(), get_daily_upload_limit())


async def tier_for(request: Request, ip: str) -> Tier:
    if token_expiry(request.cookies.get(COOKIE)):
        return by_name("trusted")
    if await is_suspicious(ip):
        return by_name("suspicious")
    return by_name("standard")


# ── Brute-force protection for the unlock password ───────────────────────────
# In memory: one process, and a guess must not cost a DB write before it is
# rejected. Per-IP lockout plus a global ceiling against distributed guessing.

_ip_fails: dict[str, list[float]] = {}
_global_fails: list[float] = []
IP_MAX, IP_WINDOW = 5, 900
GLOBAL_MAX, GLOBAL_WINDOW = 30, 3600


def locked_for(ip: str) -> int:
    now = time.monotonic()
    recent = [t for t in _ip_fails.get(ip, []) if now - t < IP_WINDOW]
    _ip_fails[ip] = recent
    global_recent = [t for t in _global_fails if now - t < GLOBAL_WINDOW]
    _global_fails[:] = global_recent
    if len(global_recent) >= GLOBAL_MAX:
        return int(GLOBAL_WINDOW - (now - global_recent[0])) + 1
    if len(recent) >= IP_MAX:
        return int(IP_WINDOW - (now - recent[0])) + 1
    return 0


def record_failure(ip: str) -> None:
    now = time.monotonic()
    _ip_fails.setdefault(ip, []).append(now)
    _global_fails.append(now)
    if len(_ip_fails) > 10_000:
        for k in [k for k, v in _ip_fails.items() if not v or now - v[-1] > IP_WINDOW]:
            _ip_fails.pop(k, None)
