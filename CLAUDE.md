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
4. **No upload is ever held in memory.** Every path gets the bytes into a temp file under `PARTIAL_DIR` first, then calls `app/services/ingest.finalize()` — the single place that checks the extension, streams the file to ClamAV (`scan_path`, TCP `INSTREAM`), hashes it, moves it to `UPLOAD_DIR/<uuid>` (no extension), inserts the `files` row, spends bypass codes and records bandwidth. `ingest.precheck()` holds the size/storage/disk/bandwidth checks. The browser and CLI use the **chunked API** (`app/routes/chunked.py`): `POST /api/uploads` reserves the full size with `posix_fallocate` (so a full disk fails up front with 507, not mid-upload), chunks (`CHUNK_SIZE`, 16 MB — far below Cloudflare's 100 MB request cap) are `PUT` in any order and are idempotent, `POST …/complete` starts finalize as a background task and answers 201 or 202 (client polls `GET /api/uploads/{id}`) so slow scans never hit a proxy timeout. `POST /upload`, `POST /raw` and `/upload-bundle` remain for curl/no-JS and spool multipart bodies to disk.
5. **Bundles (folders)**: chunked flow is `POST /api/bundles` (creates a `pending=1` bundle + `upload_token`), one chunked upload per member with `bundle_id`/`bundle_token`/`path`, then `POST /api/bundles/{id}/complete`. Pending bundles are invisible everywhere (`pending=0` filter on every bundle query) and purged by cleanup. Members are ordinary `files` rows with `bundle_id`/`bundle_path`, hidden from the loose-file listing. `/b/{id}/zip` builds the zip while streaming (`_stream_zip`) — no temp copy.
6. **Preview vs download**: `/f/{id}` is the metered path — it counts the download, enforces `max_downloads`, and is covered by the download rate limiter. A browser navigation (`Accept: text/html`, no `dl`/Range) gets a landing page (`download.html`) instead of bytes; scripts and `?dl=1` get the file. Files are served with `FileResponse`, so Range works; a Range starting past 0 is a resume and is not counted again. `/raw/{id}` is for inline previews: it serves only previewable types (anything else 302s to `/f/`), refuses files whose cap is spent, and does *not* increment `download_count` (a grid render fires one request per thumbnail), but has its own `RATE_LIMIT_PREVIEWS` ceiling held in memory.
7. **Burn-after-read**: when `burn=1` on upload, the file gets `burn_after_read=1` and `max_downloads=1`. The download path in `_serve_file` does an atomic `UPDATE … SET claimed_at=now WHERE claimed_at IS NULL`; only the row whose update affects 1 row wins. The winner's file is unlinked while its handle streams, and `archived_at` is set so cleanup tidies the row. Link-preview bots (`_PREVIEW_BOT_RE`) get the landing page and Range requests get 409, so neither can spend the single view. `/raw/{id}` refuses burn files outright (a preview would silently consume the only view).
8. `cleanup_loop` runs every 15 minutes (`app/services/cleanup.py`): two-stage expiry — expired files move to `ARCHIVE_DIR` and get `archived_at`; rows with `archived_at` older than 24h are permanently deleted. Bundles use the same scheme. Also prunes bypass codes, rate-limit rows, audit log (>90 days), expired non-pinned clipboard items, chunked uploads idle longer than `PARTIAL_TTL_HOURS`, and never-published bundles.
9. **Tiers** (`app/services/tiers.py`): `tier_for(request, ip)` → trusted (valid `yeet_unlock` cookie from `POST /api/unlock`), suspicious (abuse signals in `audit_log` for that IP in 24 h — see `SUSPICIOUS_SIGNALS`), else standard (`config_manager` limits). Every upload path passes the tier into `ingest.precheck`; uploads store `tier` so finalize uses the same one. Abuse-ish events must be written to `audit_log` with the client IP or they won't count.
10. **Scan fast path + progress**: `ingest._finalize` skips ClamAV when `media.verify()` confirms an image/video by signature (`scan_status='media'`). Otherwise `scan_path(on_progress)` reports phases into `chunked._progress` (in memory), exposed by `GET /api/uploads/{id}`. `GET /api/uploads` lists the caller's own (by `X-Session-ID`) unfinished/recent uploads so the page can restore them after a reload.
11. **Full disk**: `OSError(ENOSPC)` and SQLite "database or disk is full" map to a JSON 507 via handlers in `main.py`; `storage.has_room_for()` keeps `MIN_FREE_DISK_MB` spare.

### Two-layer config
Env vars (`app/config.py` → `Settings`) are *defaults*. Runtime overrides live in `/data/config.json` and are read on every call by `app/services/config_manager.py` (no caching — keep it that way; the CLI writes this file). When you need a tunable value (`max_file_size`, `storage_limit`, `file_expiry_hours`, `daily_upload_limit`), call the getter from `config_manager`, never read `settings.MAX_FILE_SIZE` directly in request handlers.

### Rate limiting (split by axis)
- **Downloads**: count-based (`RATE_LIMIT_DOWNLOADS`/IP/hour), enforced in `RateLimitMiddleware` for `GET /f/...`.
- **Uploads**: byte-based 24h rolling window per IP, derived from `MAX_FILE_SIZE` via `get_daily_upload_limit()` (1 GB → 1 GB/day; 1–3 GB → 3 GB/day; >3 GB → same as per-file). Enforced inside `app/routes/upload.py`, not in middleware, because it needs the body length.
- **Concurrent uploads**: 2/IP via `app/services/concurrent.py` semaphore — wrap the upload handler with `acquire`/`release`.
- **Bypass codes**: single-use, 15-minute TTL. `peek_bypass_code` checks validity, `consume_bypass_code` spends it atomically once the upload is stored; a code skips all limits except concurrency tracking and virus scanning.
- Client IP comes from `_client_ip` in `app/middleware/rate_limit.py`: a proxy-set header (`REAL_IP_HEADER`, default `CF-Connecting-IP`), else the `TRUSTED_PROXY_HOPS`-th `X-Forwarded-For` entry counted **from the right**, else the socket peer. Never read the left-most XFF entry — the caller writes it, and every per-IP limit keys off this value. `entrypoint.sh` scopes `--forwarded-allow-ips` to private ranges for the same reason; `'*'` makes uvicorn rewrite `request.client` from caller-supplied headers.
- Ownership: `uploader_session` must never appear in a response. Listings run rows through `annotate_ownership` (`app/routes/files.py`), which swaps it for a boolean `is_yours`; delete handlers compare with `owns()` (constant-time). Anything that leaks the session id hands out delete rights.

### Uploads and scanning
`scan_bytes` returns `clean|infected|error|skipped`. `infected` rejects with 422; `error` (clamd down or timing out) rejects with 503 while `CLAMAV_FAIL_CLOSED` is on — do not turn that into a warning, an outage would otherwise disable scanning silently. Bypass codes are two-phase: `peek_bypass_code` up front, `consume_bypass_code` only after the row is committed, so a rejected upload doesn't burn the user's single-use code.

### ClamAV
Installed inside the same container via apt (works on ARM64 + x86_64). `entrypoint.sh` runs `freshclam` (downloads ~110MB definitions on first start), then execs uvicorn. The app owns clamd (`app/services/virus_scan.py`, `clamd_supervisor_loop`): it is spawned with `--foreground` on the first scan and stopped after `CLAMAV_IDLE_MINUTES` idle (default 15; `0` = started at boot and kept up). It uses ~1 GB RSS and takes ~30–50 s to load. Every `CLAMAV_UPDATE_HOURS` the supervisor stops clamd, runs `freshclam`, and restarts it only in always-on mode — never run freshclam next to a live clamd: its test-load plus clamd is ~2 GB, over the 1.5 GB limit. `scan_bytes` is async — it may wait for clamd to load, and runs the socket I/O in a thread. clamd keeps the *first* occurrence of a config key, so the Dockerfile `sed` deletes keys before appending overrides; `StreamMaxLength`/`MaxFileSize`/`MaxScanSize` are 4000M because anything below the upload limit makes scans fail (503). Definitions persist in `/data/clamav-db`. The healthcheck has a 180s `start_period` to cover this. If you change clamd config, edit the `sed` block in the Dockerfile, not the image at runtime.

### Data layout (`/data`)
`uploads/`, `archive/`, `partial/`, `clipboard/`, `yeet.db`, `config.json`, `clamav-db/`. `docker-compose.yml` bind-mounts `${YEET_DATA_DIR:-./data}`. `PARTIAL_DIR` must be on the same filesystem as `UPLOAD_DIR` (finalize uses `os.replace`). Avoid pooled filesystems like mergerfs with `minfreespace`: they refuse new files while `df` still shows space.

### Templates / static
Server-rendered Jinja2 (`templates/`), one stylesheet and one `app.js` (no build step, icons are an inline SVG sprite in `base.html` — no emoji). `index.html` is the upload UI with a files list (Mine / Everyone); the client-generated session id lives in localStorage, goes out as a form field on upload and as `X-Session-ID` on listing/delete calls, and the server decides ownership (`is_yours`) — the id itself never comes back down. No JS framework; downloads work without JavaScript. Static assets are mounted at `/static`.

## Conventions

- Python 3.12, PEP 8, 4-space indent, max line ~100. Type hints on every signature.
- Async throughout — `aiosqlite`, `aiofiles`, `async def` handlers. Don't introduce sync DB calls.
- Validate at the boundary (route handler / form parser); trust internal data.
- Comments only when *why* is non-obvious. No docstrings unless the contract is surprising.
- Don't break the public download URL shape `/f/<32-char-hex-id>` — clients depend on it.
- The minimal UI is intentional. Avoid pulling in JS frameworks, analytics, or anything that adds server-side user state beyond what SQLite already holds.
