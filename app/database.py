import aiosqlite
from app.config import settings

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(str(settings.DB_PATH))
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
    return _db


async def init_db() -> None:
    db = await get_db()
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            id          TEXT PRIMARY KEY,
            filename    TEXT NOT NULL,
            orig_name   TEXT NOT NULL,
            file_hash   TEXT NOT NULL,
            file_size   INTEGER NOT NULL,
            mime_type   TEXT,
            password_hash TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at  TEXT NOT NULL,
            download_count INTEGER NOT NULL DEFAULT 0,
            max_downloads  INTEGER,
            archived_at TEXT,
            client_ip   TEXT,
            scan_status TEXT NOT NULL DEFAULT 'pending',
            uploader_session TEXT
        );

        -- count-based download rate limit (per hour)
        CREATE TABLE IF NOT EXISTS rate_limits (
            ip        TEXT NOT NULL,
            action    TEXT NOT NULL,
            ts        TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- byte-based upload bandwidth tracking (24h rolling window)
        CREATE TABLE IF NOT EXISTS upload_bandwidth (
            ip    TEXT NOT NULL,
            bytes INTEGER NOT NULL,
            ts    TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- single-use bypass codes (skip all limits)
        CREATE TABLE IF NOT EXISTS bypass_codes (
            code       TEXT PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            used       INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            action    TEXT NOT NULL,
            file_id   TEXT,
            client_ip TEXT,
            user_agent TEXT,
            ts        TEXT NOT NULL DEFAULT (datetime('now')),
            details   TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_files_expires     ON files(expires_at);
        CREATE INDEX IF NOT EXISTS idx_rate_ip_action    ON rate_limits(ip, action, ts);
        CREATE INDEX IF NOT EXISTS idx_bandwidth_ip_ts   ON upload_bandwidth(ip, ts);
        CREATE INDEX IF NOT EXISTS idx_bypass_expires    ON bypass_codes(expires_at);
        CREATE INDEX IF NOT EXISTS idx_audit_ts          ON audit_log(ts);
        CREATE INDEX IF NOT EXISTS idx_audit_action      ON audit_log(action, ts);
    """)
    # Migration: add uploader_session column to existing databases
    try:
        await db.execute("ALTER TABLE files ADD COLUMN uploader_session TEXT")
        await db.commit()
    except Exception:
        pass  # column already exists
    await db.commit()


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None
