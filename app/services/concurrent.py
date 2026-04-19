"""In-memory concurrent upload tracker (per IP). Resets on restart."""
import asyncio
from collections import defaultdict

_slots: dict[str, int] = defaultdict(int)
_lock = asyncio.Lock()
MAX_CONCURRENT = 2


async def acquire(ip: str) -> bool:
    async with _lock:
        if _slots[ip] >= MAX_CONCURRENT:
            return False
        _slots[ip] += 1
        return True


async def release(ip: str) -> None:
    async with _lock:
        if _slots[ip] > 0:
            _slots[ip] -= 1


def active_count(ip: str) -> int:
    return _slots.get(ip, 0)
