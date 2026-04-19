import socket
from pathlib import Path

from app.config import settings


def _ping_clamav() -> bool:
    try:
        with socket.create_connection(
            (settings.CLAMAV_HOST, settings.CLAMAV_PORT), timeout=5
        ) as s:
            s.sendall(b"zPING\0")
            return s.recv(10) == b"PONG\0"
    except (OSError, ConnectionRefusedError):
        return False


def scan_bytes(data: bytes) -> tuple[str, str | None]:
    """
    Returns (status, threat_name).
    status: 'clean' | 'infected' | 'error' | 'skipped'
    """
    if not settings.CLAMAV_ENABLED:
        return "skipped", None

    try:
        with socket.create_connection(
            (settings.CLAMAV_HOST, settings.CLAMAV_PORT), timeout=30
        ) as s:
            size = len(data)
            s.sendall(b"zINSTREAM\0")
            s.sendall(size.to_bytes(4, "big"))
            s.sendall(data)
            s.sendall(b"\x00\x00\x00\x00")
            response = s.recv(1024).decode("utf-8", errors="replace").strip("\0")

        if "OK" in response:
            return "clean", None
        if "FOUND" in response:
            threat = response.split(":")[1].strip().replace(" FOUND", "") if ":" in response else "unknown"
            return "infected", threat
        return "error", None

    except (OSError, ConnectionRefusedError, TimeoutError):
        return "error", None


def clamav_available() -> bool:
    if not settings.CLAMAV_ENABLED:
        return False
    return _ping_clamav()
