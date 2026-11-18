import asyncio
import logging
import socket
import time

from app.config import settings

logger = logging.getLogger("yeet.clamd")

# clamd holds ~1 GB of signatures in RAM, which is most of the container's
# footprint. When clamd is local the app owns it: with CLAMAV_IDLE_MINUTES > 0
# it is spawned on the first scan and stopped once idle, with 0 it is started
# at boot and kept up. Signature updates only run while clamd is stopped —
# freshclam test-loads the new database, and doing that next to a running
# clamd would need ~2 GB.
_proc: asyncio.subprocess.Process | None = None
_lock = asyncio.Lock()  # serialises start/stop/update so a scan never hits a dying clamd
_in_flight = 0
_last_used = 0.0
_last_update = time.monotonic()  # entrypoint.sh runs freshclam right before we start


def _managed() -> bool:
    return settings.CLAMAV_ENABLED and settings.CLAMAV_HOST in ("127.0.0.1", "localhost", "::1")


def _always_on() -> bool:
    return settings.CLAMAV_IDLE_MINUTES <= 0


def _ping_clamav() -> bool:
    try:
        with socket.create_connection(
            (settings.CLAMAV_HOST, settings.CLAMAV_PORT), timeout=5
        ) as s:
            s.sendall(b"zPING\0")
            return s.recv(10) == b"PONG\0"
    except (OSError, ConnectionRefusedError):
        return False


async def _start_clamd() -> bool:
    """Caller must hold _lock."""
    global _proc
    if await asyncio.to_thread(_ping_clamav):
        return True
    if _proc is None or _proc.returncode is not None:
        logger.info("starting clamd")
        _proc = await asyncio.create_subprocess_exec(
            "clamd",
            "--foreground",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    started = time.monotonic()
    deadline = started + settings.CLAMAV_START_TIMEOUT
    while time.monotonic() < deadline:
        if _proc.returncode is not None:
            logger.error("clamd exited during startup (code %s)", _proc.returncode)
            return False
        if await asyncio.to_thread(_ping_clamav):
            logger.info("clamd ready after %.0fs", time.monotonic() - started)
            return True
        await asyncio.sleep(1)
    logger.error("clamd not ready after %ss", settings.CLAMAV_START_TIMEOUT)
    return False


async def stop_clamd() -> None:
    global _proc
    if _proc is None or _proc.returncode is not None:
        _proc = None
        return
    _proc.terminate()
    try:
        await asyncio.wait_for(_proc.wait(), timeout=30)
    except TimeoutError:
        _proc.kill()
        await _proc.wait()
    _proc = None


async def _update_signatures() -> None:
    """Caller must hold _lock with clamd stopped."""
    global _last_update
    _last_update = time.monotonic()
    logger.info("updating ClamAV signatures")
    proc = await asyncio.create_subprocess_exec(
        "freshclam", "--quiet",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=900)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        logger.error("freshclam timed out — keeping current signatures")
        return
    if proc.returncode not in (0, 1):  # 1 = already up to date
        msg = err.decode(errors="replace").strip().splitlines()
        logger.warning("freshclam failed (code %s): %s",
                       proc.returncode, msg[-1] if msg else "no output")


async def clamd_supervisor_loop() -> None:
    if not _managed():
        return
    if _always_on():
        async with _lock:
            await _start_clamd()
    idle_seconds = settings.CLAMAV_IDLE_MINUTES * 60
    update_seconds = settings.CLAMAV_UPDATE_HOURS * 3600
    while True:
        await asyncio.sleep(60)
        if _in_flight or _lock.locked():
            continue
        now = time.monotonic()
        update_due = update_seconds > 0 and now - _last_update >= update_seconds
        idle = _proc is not None and not _always_on() and now - _last_used >= idle_seconds
        if not (update_due or idle):
            continue
        async with _lock:
            if _in_flight:
                continue
            if idle:
                logger.info("stopping clamd after %s idle minutes", settings.CLAMAV_IDLE_MINUTES)
            await stop_clamd()
            if update_due:
                await _update_signatures()
                if _always_on():
                    await _start_clamd()


async def _ensure_clamd() -> bool:
    async with _lock:
        return await _start_clamd()


_SCAN_TIMEOUT = 900  # must outlast clamd's MaxScanTime (Dockerfile sets 600s)


def _parse_reply(response: str) -> tuple[str, str | None]:
    # FOUND first: a signature name may itself contain "OK".
    if response.endswith("FOUND"):
        body = response.split(":", 1)[1] if ":" in response else response
        return "infected", body.strip().removesuffix("FOUND").strip() or "unknown"
    if response.endswith("OK"):
        return "clean", None
    logger.error("unexpected clamd response: %s", response)
    return "error", None


def _instream(chunks) -> tuple[str, str | None]:
    try:
        with socket.create_connection(
            (settings.CLAMAV_HOST, settings.CLAMAV_PORT), timeout=_SCAN_TIMEOUT
        ) as s:
            s.sendall(b"zINSTREAM\0")
            for chunk in chunks:
                if chunk:
                    s.sendall(len(chunk).to_bytes(4, "big"))
                    s.sendall(chunk)
            s.sendall(b"\x00\x00\x00\x00")
            response = b""
            while not response.endswith(b"\0"):
                part = s.recv(1024)
                if not part:
                    break
                response += part
        return _parse_reply(response.decode("utf-8", errors="replace").strip("\0").strip())
    except (OSError, TimeoutError) as exc:
        logger.error("clamd scan failed: %s", exc)
        return "error", None


def _scan_sync(data: bytes) -> tuple[str, str | None]:
    step = 1_048_576
    return _instream(data[i:i + step] for i in range(0, len(data), step))


def _scan_path_sync(path: str) -> tuple[str, str | None]:
    def gen():
        with open(path, "rb") as fh:
            while chunk := fh.read(1_048_576):
                yield chunk
    return _instream(gen())


async def _run_scan(fn, arg) -> tuple[str, str | None]:
    global _in_flight, _last_used
    if not settings.CLAMAV_ENABLED:
        return "skipped", None
    _in_flight += 1
    try:
        if _managed() and not await _ensure_clamd():
            return "error", None
        return await asyncio.to_thread(fn, arg)
    finally:
        _in_flight -= 1
        _last_used = time.monotonic()


async def scan_path(path) -> tuple[str, str | None]:
    """Stream a file on disk to clamd. Same return contract as scan_bytes."""
    return await _run_scan(_scan_path_sync, str(path))


async def scan_bytes(data: bytes) -> tuple[str, str | None]:
    """
    Returns (status, threat_name).
    status: 'clean' | 'infected' | 'error' | 'skipped'
    """
    return await _run_scan(_scan_sync, data)


def clamav_available() -> bool:
    if not settings.CLAMAV_ENABLED:
        return False
    return _ping_clamav()
