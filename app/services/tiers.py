"""
Upload limits per visitor.

  admin       — unlock cookie earned with ADMIN_PASSWORD: trusted limits, and
                uploads skip the ClamAV scan.
  trusted     — holds a valid unlock cookie (secret UNLOCK_PASSWORD entered in
                the app): big per-file and daily limits, no storage-cap check.
  standard    — everyone else: the admin-configured per-file limit.
  suspicious  — an IP with recent abuse signals in the audit log: small limits.

The unlock cookie is an HMAC-signed, expiring token (never the password). The
MAC covers a hash of the password it was issued for, so changing that password
revokes every cookie it issued.
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
    name: str           # admin | trusted | standard | suspicious
    max_file: int
    daily: int

    @property
    def trusted(self) -> bool:
        return self.name in ("trusted", "admin")


def _unlock_pw() -> str:
    return settings.UNLOCK_PASSWORD if len(settings.UNLOCK_PASSWORD) >= 12 else ""


def unlock_enabled() -> bool:
    return bool(_unlock_pw() or settings.ADMIN_PASSWORD)


def _password_for(kind: str) -> str:
    return settings.ADMIN_PASSWORD if kind == "admin" else _unlock_pw()


def _sign(kind: str, exp: int, nonce: str) -> str:
    fp = hashlib.sha256(_password_for(kind).encode()).hexdigest()
    msg = f"{kind}|{exp}|{nonce}|{fp}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def issue_token(kind: str = "unlock") -> tuple[str, int]:
    ttl = settings.UNLOCK_TTL_DAYS * 86400
    exp = int(time.time()) + ttl
    nonce = secrets.token_hex(8)
    return f"{kind}.{exp}.{nonce}.{_sign(kind, exp, nonce)}", ttl


def token_info(token: str | None) -> tuple[str, int] | None:
    """(kind, expiry) of a valid token, else None. kind is 'unlock' or 'admin'."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) == 3:  # pre-admin cookies: exp.nonce.sig
        parts = ["unlock", *parts]
    try:
        kind, exp_s, nonce, sig = parts
        exp = int(exp_s)
    except ValueError:
        return None
    if kind not in ("unlock", "admin") or not _password_for(kind):
        return None
    if exp < time.time() or not hmac.compare_digest(sig, _sign(kind, exp, nonce)):
        return None
    return kind, exp


def token_expiry(token: str | None) -> int | None:
    info = token_info(token)
    return info[1] if info else None


def _same(candidate: str, secret: str) -> bool:
    a = hashlib.sha256(candidate.encode()).digest()
    b = hashlib.sha256(secret.encode()).digest()
    return hmac.compare_digest(a, b)


def check_password(candidate: str) -> str | None:
    """Token kind the password earns ('admin' or 'unlock'), else None."""
    if not candidate:
        return None
    if settings.ADMIN_PASSWORD and _same(candidate, settings.ADMIN_PASSWORD):
        return "admin"
    if _unlock_pw() and _same(candidate, _unlock_pw()):
        return "unlock"
    return None


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
    if name in ("trusted", "admin"):
        return Tier(name, settings.TRUSTED_MAX_FILE_SIZE, settings.TRUSTED_DAILY_LIMIT)
    if name == "suspicious":
        return Tier("suspicious", settings.SUSPICIOUS_MAX_FILE_SIZE,
                    settings.SUSPICIOUS_DAILY_LIMIT)
    return Tier("standard", get_max_file_size(), get_daily_upload_limit())


async def tier_for(request: Request, ip: str) -> Tier:
    info = token_info(request.cookies.get(COOKIE))
    if info:
        return by_name("admin" if info[0] == "admin" else "trusted")
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
