"""
Dynamic config that merges /data/config.json with env-var defaults.
CLI writes to config.json; app reads on every call (cheap, no caching needed).
"""
import json
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger("yeet.config")
_path = settings.BASE_DIR / "config.json"


def _load() -> dict:
    try:
        if _path.exists():
            return json.loads(_path.read_text())
    except Exception as exc:
        logger.warning("config.json read error: %s", exc)
    return {}


def _save(data: dict) -> None:
    _path.parent.mkdir(parents=True, exist_ok=True)
    _path.write_text(json.dumps(data, indent=2))


def get_max_file_size() -> int:
    return int(_load().get("max_file_size", settings.MAX_FILE_SIZE))


def get_storage_limit() -> int:
    return int(_load().get("storage_limit", settings.STORAGE_LIMIT))


def get_file_expiry_hours() -> int:
    return int(_load().get("file_expiry_hours", settings.FILE_EXPIRY_HOURS))


def get_daily_upload_limit() -> int:
    """Byte-based 24h upload limit per IP, derived from per-file limit."""
    GB = 1_073_741_824
    mfs = get_max_file_size()
    if mfs <= GB:
        return GB          # 1 GB/day
    elif mfs <= 3 * GB:
        return 3 * GB      # 3 GB/day
    else:
        return mfs         # same as per-file limit


def set_max_file_size(size: int) -> None:
    data = _load()
    data["max_file_size"] = size
    _save(data)


def set_storage_limit(size: int) -> None:
    data = _load()
    data["storage_limit"] = size
    _save(data)


def set_file_expiry_hours(hours: int) -> None:
    data = _load()
    data["file_expiry_hours"] = hours
    _save(data)


def get_all() -> dict:
    return {
        "max_file_size":    get_max_file_size(),
        "storage_limit":    get_storage_limit(),
        "file_expiry_hours": get_file_expiry_hours(),
        "daily_upload_limit": get_daily_upload_limit(),
    }
