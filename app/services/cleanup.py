import asyncio
import logging
from datetime import datetime, timezone

from app.config import settings
from app.database import get_db
from app.services.storage import archive_file, delete_file

CLIPBOARD_DIR = settings.BASE_DIR / "clipboard"

logger = logging.getLogger("yeet.cleanup")


async def run_cleanup() -> dict:
    """
    Two-stage expiry:
      1. Expired active files → move to archive directory, set archived_at
      2. Files archived > 24h ago → permanently delete from archive + database
    Also prunes expired bypass codes and old rate-limit rows.
    """
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Stage 1: archive expired files
    async with db.execute(
        "SELECT id FROM files WHERE expires_at <= ? AND archived_at IS NULL",
        (now,),
    ) as cursor:
        expired = [row[0] for row in await cursor.fetchall()]

    archived = 0
    for fid in expired:
        await archive_file(fid)
        await db.execute(
            "UPDATE files SET archived_at=? WHERE id=?", (now, fid)
        )
        archived += 1
        logger.info("archived file %s", fid)

    # Stage 2: permanently delete files archived > 24h ago
    async with db.execute(
        "SELECT id FROM files WHERE archived_at IS NOT NULL "
        "AND archived_at <= datetime('now', '-24 hours')",
    ) as cursor:
        stale = [row[0] for row in await cursor.fetchall()]

    deleted = 0
    for fid in stale:
        await delete_file(fid)
        await db.execute("DELETE FROM files WHERE id=?", (fid,))
        deleted += 1
        logger.info("permanently deleted file %s", fid)

    # Prune expired bypass codes
    await db.execute("DELETE FROM bypass_codes WHERE expires_at <= ?", (now,))

    # Prune download rate-limit entries older than 2 hours
    await db.execute(
        "DELETE FROM rate_limits WHERE ts < datetime('now', '-2 hours')"
    )

    # Prune upload bandwidth entries older than 25 hours
    await db.execute(
        "DELETE FROM upload_bandwidth WHERE ts < datetime('now', '-25 hours')"
    )

    # Prune audit log older than 90 days
    await db.execute(
        "DELETE FROM audit_log WHERE ts < datetime('now', '-90 days')"
    )

    # Delete expired (non-pinned) clipboard items
    async with db.execute(
        "SELECT id, type, content FROM clipboard_items WHERE pinned=0 AND expires_at <= ?",
        (now,),
    ) as cursor:
        expired_clips = [(row[0], row[1], row[2]) for row in await cursor.fetchall()]

    for cid, ctype, ccontent in expired_clips:
        if ctype == "image":
            img_path = CLIPBOARD_DIR / ccontent
            if img_path.exists():
                try:
                    img_path.unlink()
                except Exception:
                    pass
        await db.execute("DELETE FROM clipboard_items WHERE id=?", (cid,))
        logger.info("deleted clipboard item %s", cid)

    await db.commit()
    logger.info("cleanup done: archived=%d deleted=%d clipboard_deleted=%d",
                archived, deleted, len(expired_clips))
    return {"archived": archived, "deleted": deleted, "expired_ids": expired}


async def cleanup_loop() -> None:
    while True:
        try:
            await run_cleanup()
        except Exception as exc:
            logger.exception("cleanup error: %s", exc)
        await asyncio.sleep(900)  # every 15 minutes
