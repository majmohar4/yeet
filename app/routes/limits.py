"""
Visitor limits and the unlock password.

  GET  /api/limits  → this visitor's tier, per-file and daily limits, usage
  POST /api/unlock  {password} → sets the signed `yeet_unlock` cookie
  POST /api/lock    → clears it
"""
import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.middleware.rate_limit import _client_ip, check_upload_bandwidth
from app.services import ingest, tiers

logger = logging.getLogger("yeet.limits")
router = APIRouter()


async def _limits(request: Request, ip: str, tier: tiers.Tier, exp: int | None) -> dict:
    bw = await check_upload_bandwidth(ip, 0, tier.daily)
    return {
        "tier": tier.name,
        "max_file_bytes": tier.max_file,
        "daily_limit": tier.daily,
        "used_today": bw["used"],
        "unlock_available": tiers.unlock_enabled(),
        "unlocked_until": exp,
    }


@router.get("/api/limits")
async def get_limits(request: Request):
    ip = _client_ip(request)
    tier = await tiers.tier_for(request, ip)
    exp = tiers.token_expiry(request.cookies.get(tiers.COOKIE))
    return JSONResponse(await _limits(request, ip, tier, exp),
                        headers={"Cache-Control": "no-store"})


@router.post("/api/unlock")
async def unlock(request: Request):
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")
    if not tiers.unlock_enabled():
        return ingest.error("Unlocking isn't enabled on this server.", 404)

    wait = tiers.locked_for(ip)
    if wait:
        return ingest.error("Too many wrong attempts. Try again later.", 429,
                            headers={"Retry-After": str(wait)})

    try:
        body = await request.json()
    except Exception:
        body = {}
    password = str((body or {}).get("password") or "")[:256]

    kind = tiers.check_password(password)
    if not kind:
        tiers.record_failure(ip)
        await ingest.audit(ip, ua, "unlock_failed", None, None)
        logger.warning("unlock failed: ip=%s", ip)
        await asyncio.sleep(1)  # caps online guessing even before the lockout
        return ingest.error("Wrong password.", 403)

    token, ttl = tiers.issue_token(kind)
    await ingest.audit(ip, ua, "admin_unlock" if kind == "admin" else "unlock", None, None)
    tier = tiers.by_name("admin" if kind == "admin" else "trusted")
    exp = tiers.token_expiry(token)
    resp = JSONResponse(await _limits(request, ip, tier, exp))
    resp.set_cookie(
        tiers.COOKIE, token, max_age=ttl, path="/", httponly=True,
        secure=request.url.scheme == "https", samesite="strict",
    )
    return resp


@router.post("/api/lock")
async def lock(request: Request):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(tiers.COOKIE, path="/", samesite="strict",
                       secure=request.url.scheme == "https", httponly=True)
    return resp
