import asyncio
import errno
import hashlib
import os
import re
import shutil
import sqlite3
import unicodedata
from pathlib import Path
from urllib.parse import quote

import aiofiles

from app.config import settings


class DiskFullError(Exception):
    """Raised when an upload can't be accepted because the disk is (nearly) full."""


def is_disk_full_error(exc: BaseException) -> bool:
    if isinstance(exc, DiskFullError):
        return True
    if isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT):
        return True
    if isinstance(exc, sqlite3.OperationalError) and "full" in str(exc).lower():
        return True
    return False


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


def content_disposition(name: str, inline: bool = False) -> str:
    """RFC 5987 Content-Disposition with Unicode-safe filename.

    HTTP headers must be latin-1 encodable. Raw filenames with characters
    like 'č' would crash Starlette when it encodes the response. We emit an
    ASCII fallback plus a percent-encoded UTF-8 form so modern clients see
    the original name and old ones still get something sensible.
    """
    ascii_fallback = sanitize_filename(name).replace('"', "").replace("\\", "")
    encoded = quote(name, safe="")
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


def get_file_path(file_id: str) -> Path:
    return settings.UPLOAD_DIR / file_id


def partial_path(upload_id: str) -> Path:
    return settings.PARTIAL_DIR / upload_id


# ── Disk space ────────────────────────────────────────────────────────────────

def free_bytes() -> int:
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(settings.UPLOAD_DIR).free


def has_room_for(size: int) -> bool:
    return free_bytes() - size >= settings.MIN_FREE_DISK_MB * 1_048_576


def _reserve_sync(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        if size <= 0:
            return
        if hasattr(os, "posix_fallocate"):
            # Allocates real blocks: a later ENOSPC mid-upload is impossible.
            os.posix_fallocate(fd, 0, size)
        else:
            os.ftruncate(fd, size)
    except OSError:
        os.close(fd)
        fd = -1
        path.unlink(missing_ok=True)
        raise
    finally:
        if fd >= 0:
            os.close(fd)


async def reserve(path: Path, size: int) -> None:
    """Create `path` with `size` bytes allocated, or raise DiskFullError."""
    if not has_room_for(size):
        raise DiskFullError()
    try:
        await asyncio.to_thread(_reserve_sync, path, size)
    except OSError as exc:
        if is_disk_full_error(exc):
            raise DiskFullError() from exc
        raise


def _pwrite_sync(path: Path, offset: int, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY)
    try:
        view = memoryview(data)
        while view:
            n = os.pwrite(fd, view, offset)
            view = view[n:]
            offset += n
    finally:
        os.close(fd)


async def write_at(path: Path, offset: int, data: bytes) -> None:
    await asyncio.to_thread(_pwrite_sync, path, offset, data)


def _hash_file_sync(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1_048_576):
            h.update(chunk)
    return h.hexdigest()


async def hash_file(path: Path) -> str:
    return await asyncio.to_thread(_hash_file_sync, path)


async def promote(src: Path, file_id: str) -> Path:
    """Move a finished partial into UPLOAD_DIR under its permanent id."""
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dst = settings.UPLOAD_DIR / file_id
    try:
        await asyncio.to_thread(os.replace, src, dst)
    except OSError as exc:
        if exc.errno != errno.EXDEV:  # PARTIAL_DIR on another filesystem
            raise
        await asyncio.to_thread(shutil.move, src, dst)
    return dst


async def save_file(file_id: str, data: bytes) -> Path:
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = settings.UPLOAD_DIR / file_id
    async with aiofiles.open(path, "wb") as f:
        await f.write(data)
    return path


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
    for directory in (settings.UPLOAD_DIR, settings.ARCHIVE_DIR, settings.PARTIAL_DIR):
        if directory.exists():
            for entry in directory.iterdir():
                if entry.is_file():
                    total += entry.stat().st_size
    return total
