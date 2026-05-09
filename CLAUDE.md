# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**yeet** — minimal file-sharing server (FastAPI + SQLite + ClamAV). Files expire automatically; no accounts. Designed to run as a single Docker container behind a reverse proxy. Live instance: `yeet.majmohar.eu`.

## Commands

### Local development
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                    # set SECRET_KEY (>=32 chars) and ADMIN_PASSWORD
mkdir -p data/uploads data/archive
# Set CLAMAV_ENABLED=false in .env if you don't have clamd running locally
uvicorn app.main:app --reload --port 8000
```

`BASE_DIR`, `UPLOAD_DIR`, `ARCHIVE_DIR`, `DB_PATH` default to `/data/...` — override via env when running outside Docker.

### Docker
```bash
docker compose up -d                    # builds image, host port 4534 → container 8000
docker compose logs -f yeet
docker exec yeet yeet <admin-command>   # see CLI section
```

### Tests
Tests hit a running server (no mocking). Two ways to run:

```bash
# Against an already-running instance:
pytest tests/test_security.py -v
pytest tests/test_security.py -v -k "password"          # single category

# Full orchestrated run (rebuilds Docker, starts service on :4534, runs suite):
./test.sh                               # = python3 tests/run_all.py
```

`tests/run_all.py` uses `YEET_TEST_URL=http://localhost:4534` and `YEET_TEST_ADMIN_PASSWORD=testadmin`. Output lands in `test_results/`.

### CLI (`./yeet`)
The `yeet` script is dual-purpose:
- **Admin commands** (`stats`, `generate-code`, `set-file-limit`, `list`, `logs`, `cleanup`, …) talk to SQLite directly — must run where `/data/yeet.db` is reachable, typically `docker exec yeet yeet <cmd>`.
- **HTTP commands** (`upload`, `download`, `health`) hit `YEET_URL` (default `https://yeet.majmohar.eu`).

## Architecture

### Request lifecycle
1. `app/main.py` — FastAPI lifespan creates dirs, runs `init_db()`, starts the `cleanup_loop` asyncio task, mounts middleware (`SecurityHeadersMiddleware`, `RateLimitMiddleware`) and routers.
2. Routers in `app/routes/`: `upload` (incl. `POST /raw` for curl-friendly text-URL response), `download`, `files` (listing/admin file ops), `admin`, `clipboard`, `bundles` (folder uploads at `/upload-bundle`, viewer `/b/{id}`, zip stream `/b/{id}/zip`). `/c/{id}` and `/health` are defined inline in `main.py`.
3. Persistence is a single async aiosqlite connection (`app/database.py`, WAL mode). Tables: `files`, `bundles`, `rate_limits`, `upload_bandwidth`, `bypass_codes`, `audit_log`, `clipboard_items`. `files` carries `bundle_id`/`bundle_path` for bundle members and `burn_after_read`/`claimed_at` for single-view files. Schema is created on startup with `CREATE TABLE IF NOT EXISTS`; column additions use try/except `ALTER TABLE` for idempotent migrations.
4. File bytes are read fully into memory, scanned by ClamAV (`app/services/virus_scan.py` — TCP `INSTREAM` to `127.0.0.1:3310`), then written to `UPLOAD_DIR/<uuid>` with no extension. Metadata (incl. bcrypt password hash) lives in SQLite.
5. **Bundle uploads**: `POST /upload-bundle` accepts `files[]` + a newline-separated `paths` list. The server creates one `bundles` row plus one `files` row per member (each with its own UUID, ClamAV scan, expiry). Member files are hidden from `/api/files/all`'s loose-file list — they only show up under their bundle. Zip download at `/b/{id}/zip` writes to a temp file (unlinked immediately, fd kept open), then streams it.
6. **Burn-after-read**: when `burn=1` on upload, the file gets `burn_after_read=1` and `max_downloads=1`. The download path in `_serve_file` does an atomic `UPDATE … SET claimed_at=now WHERE claimed_at IS NULL`; only the row whose update affects 1 row wins. The winner serves the bytes, then `delete_file` wipes both upload and archive copies and `archived_at` is set so cleanup tidies the metadata row. `/raw/{id}` refuses burn files outright (a preview would silently consume the only view).
7. `cleanup_loop` runs every 15 minutes (`app/services/cleanup.py`): two-stage expiry — expired files move to `ARCHIVE_DIR` and get `archived_at`; rows with `archived_at` older than 24h are permanently deleted. Bundles use the same scheme. Also prunes bypass codes, rate-limit rows, audit log (>90 days), and expired non-pinned clipboard items.

### Two-layer config
Env vars (`app/config.py` → `Settings`) are *defaults*. Runtime overrides live in `/data/config.json` and are read on every call by `app/services/config_manager.py` (no caching — keep it that way; the CLI writes this file). When you need a tunable value (`max_file_size`, `storage_limit`, `file_expiry_hours`, `daily_upload_limit`), call the getter from `config_manager`, never read `settings.MAX_FILE_SIZE` directly in request handlers.

### Rate limiting (split by axis)
- **Downloads**: count-based (`RATE_LIMIT_DOWNLOADS`/IP/hour), enforced in `RateLimitMiddleware` for `GET /f/...`.
- **Uploads**: byte-based 24h rolling window per IP, derived from `MAX_FILE_SIZE` via `get_daily_upload_limit()` (1 GB → 1 GB/day; 1–3 GB → 3 GB/day; >3 GB → same as per-file). Enforced inside `app/routes/upload.py`, not in middleware, because it needs the body length.
- **Concurrent uploads**: 2/IP via `app/services/concurrent.py` semaphore — wrap the upload handler with `acquire`/`release`.
- **Bypass codes**: single-use, 15-minute TTL. `validate_bypass_code` marks the code used atomically and skips all limits except concurrency tracking.
- Client IP comes from `X-Forwarded-For` (first hop). uvicorn is started with `--proxy-headers --forwarded-allow-ips='*'` — do not run it without a trusted reverse proxy in front.

### ClamAV
Installed inside the same container via apt (works on ARM64 + x86_64). `entrypoint.sh` runs `freshclam` (downloads ~110MB definitions on first start), launches `clamd`, polls `zPING` until ready, then execs uvicorn. Definitions persist in `/data/clamav-db`. The healthcheck has a 180s `start_period` to cover this. If you change clamd config, edit the `sed` block in the Dockerfile, not the image at runtime.

### Data layout (`/data`)
`uploads/`, `archive/`, `clipboard/`, `yeet.db`, `config.json`, `clamav-db/`. In `docker-compose.yml` this is bind-mounted from a host path (`/home/home/Video/Yeet:/data` in the committed compose file — adjust before deploying elsewhere).

### Templates / static
Server-rendered Jinja2 (`templates/`). `index.html` is the upload UI with a "recent uploads" panel filtered by `uploader_session` (a client-generated id stored in localStorage and sent as a form field). No JS framework; downloads work without JavaScript. Static assets are mounted at `/static`.

## Conventions

- Python 3.12, PEP 8, 4-space indent, max line ~100. Type hints on every signature.
- Async throughout — `aiosqlite`, `aiofiles`, `async def` handlers. Don't introduce sync DB calls.
- Validate at the boundary (route handler / form parser); trust internal data.
- Comments only when *why* is non-obvious. No docstrings unless the contract is surprising.
- Don't break the public download URL shape `/f/<32-char-hex-id>` — clients depend on it.
- The minimal UI is intentional. Avoid pulling in JS frameworks, analytics, or anything that adds server-side user state beyond what SQLite already holds.
