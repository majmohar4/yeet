import hashlib
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

import aiofiles

from app.config import settings


def sanitize_filename(name: str) -> str:
    """Strip dangerous characters from filename, keep extension."""
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^\w\s\-.]", "", name).strip()
    name = re.sub(r"[\s]+", "_", name)
    name = name.lstrip(".")
    if not name:
        name = "file"
    return name[: settings.MAX_FILENAME_LENGTH]


def content_disposition(name: str) -> str:
    """RFC 5987 Content-Disposition with Unicode-safe filename.

    HTTP headers must be latin-1 encodable. Raw filenames with characters
    like 'č' would crash Starlette when it encodes the response. We emit an
    ASCII fallback plus a percent-encoded UTF-8 form so modern clients see
    the original name and old ones still get something sensible.
    """
    ascii_fallback = sanitize_filename(name).replace('"', "").replace("\\", "")
    encoded = quote(name, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


async def save_file(file_id: str, data: bytes) -> Path:
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = settings.UPLOAD_DIR / file_id
    async with aiofiles.open(path, "wb") as f:
        await f.write(data)
    return path


async def read_file(file_id: str) -> bytes:
    path = settings.UPLOAD_DIR / file_id
    async with aiofiles.open(path, "rb") as f:
        return await f.read()


def get_file_path(file_id: str) -> Path:
    return settings.UPLOAD_DIR / file_id


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def archive_file(file_id: str) -> None:
    settings.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    src = settings.UPLOAD_DIR / file_id
    dst = settings.ARCHIVE_DIR / file_id
    if src.exists():
        src.rename(dst)


async def delete_file(file_id: str) -> None:
    for directory in (settings.UPLOAD_DIR, settings.ARCHIVE_DIR):
        path = directory / file_id
        if path.exists():
            path.unlink()


def get_total_storage() -> int:
    total = 0
    for directory in (settings.UPLOAD_DIR, settings.ARCHIVE_DIR):
        if directory.exists():
            for entry in directory.iterdir():
                if entry.is_file():
                    total += entry.stat().st_size
    return total
