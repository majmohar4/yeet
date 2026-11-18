# Frequently Asked Questions

## How do bypass codes work?

Bypass codes let a trusted person skip all upload limits in one shot — file size, storage quota, and bandwidth limit are all ignored. Codes are single-use and expire after 15 minutes.

Generate one from the server:

```bash
docker exec yeet yeet generate-code
#   Code:    A1B2C3D4
#   Expires: 2025-06-01T12:15:00 UTC
```

Share the code with the person who needs to upload. They enter it in the "Bypass Code" field on the upload page (or pass `--bypass-code A1B2C3D4` to the CLI).

Once used, the code is invalidated immediately. You can revoke an unused code with:

```bash
docker exec yeet yeet revoke-code A1B2C3D4
```

---

## What are the rate limits?

Upload limits are byte-based, per IP, over a 24-hour rolling window:

| Per-file limit | Daily upload limit per IP |
|---|---|
| ≤ 1 GB (default) | 1 GB/day |
| 1–3 GB | 3 GB/day |
| > 3 GB | same as per-file limit |

At 80% of the daily limit, the upload response includes a warning. At 100%, uploads are blocked with HTTP 429 and a `Retry-After` header showing how many seconds until the window resets.

Download rate limit: 100 downloads per IP per hour.

To bypass the limit for a single upload, use a bypass code (see above).

---

## Why is ClamAV slow on first run?

ClamAV needs to download its virus definition database (~170MB) on first start. This is completely normal. The download happens inside the container and can take 2–5 minutes depending on your connection.

Once downloaded, the definitions are stored in `/data/clamav-db` and reused on every restart.

clamd itself (about 1 GB of RAM) is started by the app on the first upload and stopped after `CLAMAV_IDLE_MINUTES` of inactivity, so the first upload after a quiet period waits roughly a minute while it loads. Set `CLAMAV_IDLE_MINUTES=0` to keep it running. Watch it with:

```bash
docker compose logs -f yeet | grep -i clamd
```

If you want to disable virus scanning entirely (not recommended for production), set `CLAMAV_ENABLED=false` in `.env`.

---

## How do I change the port?

Edit the `ports` section in `docker-compose.yml`:

```yaml
ports:
  - "127.0.0.1:8080:8000"   # change 8080 to your desired port
```

If you're exposing yeet behind a reverse proxy (recommended), keep it bound to `127.0.0.1` and only change the host port.

---

## How do I back up files?

Everything lives in one host folder, mounted at `/data` — `YEET_DATA_DIR` in `.env` (default `./data` next to `docker-compose.yml`):

| Path | Contents |
|---|---|
| `uploads/`, `archive/` | File contents, named by random ID |
| `yeet.db` | SQLite database (metadata, audit log) |
| `config.json` | Runtime settings written by the CLI |
| `clipboard/` | Clipboard images |
| `partial/` | Uploads in progress (safe to lose) |
| `clamav-db/` | Virus definitions (re-downloadable) |

Take a consistent copy of the database while the app runs, then archive the rest:

```bash
docker exec yeet python -c "import sqlite3; s=sqlite3.connect('/data/yeet.db'); d=sqlite3.connect('/tmp/yeet.db'); s.backup(d); d.close()"
docker cp yeet:/tmp/yeet.db ./yeet-$(date +%Y%m%d).db
tar czf yeet-files-$(date +%Y%m%d).tar.gz -C "$YEET_DATA_DIR" uploads archive clipboard config.json
```

To restore, stop the container, put the files back in the data folder, and start it again.

---

## Uploads fail with "out of disk space" but `df` shows free space

The data folder is probably on a pooled or quota-limited filesystem. mergerfs with `minfreespace=…` only creates files on branches that have at least that much free, so once every branch is below it, every new file fails with `ENOSPC` while `df` still reports the pool's total. Move `YEET_DATA_DIR` to a plain disk (stop the container, `cp -a` the folder, update `.env`, start again) or free space on a branch.

yeet reserves the full size of an upload before accepting it and keeps `MIN_FREE_DISK_MB` (default 2 GB) spare, so a full disk shows a clear message instead of a broken upload.

---

## How do large uploads get past Cloudflare's 100 MB limit?

The browser and the `yeet` CLI split files into 16 MB chunks (`CHUNK_SIZE`) and send each as its own request, so the limit never applies. Raise the per-file limit with `docker exec yeet yeet set-file-limit 2GB` — it takes effect immediately. Plain `curl -F … /raw` still sends one request and is therefore capped by your proxy.

---

## How secure is password protection?

Passwords are hashed with bcrypt (cost factor 12) before storage. The original password is never saved.

When someone downloads a password-protected file, their input is compared against the stored hash using `bcrypt.checkpw()` — which is constant-time and resistant to timing attacks.

Important caveats:
- The file itself is **not encrypted on disk**. Password protection only gates access through the web interface.
- If an attacker gains direct access to `/data/uploads/`, they can read files without a password.
- Protect your server — keep the data directory inaccessible from the web and run the container as non-root (which Docker Compose does by default).

---

## Can I disable virus scanning?

Yes. Add this to your `.env`:

```bash
CLAMAV_ENABLED=false
```

ClamAV runs inside the yeet container, so nothing else needs changing; with scanning off, clamd is never started.

Files uploaded with scanning disabled will show `scan_status: skipped` in API responses.

---

## How do I see logs?

**Application logs** (access log, errors, cleanup events):

```bash
docker compose logs -f yeet
```

**Audit log** (all uploads, downloads, admin actions) via the admin API:

```bash
curl -u admin:YOUR_ADMIN_PASSWORD https://yeet.example.com/admin/logs
```

Or using the CLI:

```bash
YEET_ADMIN_PASSWORD=pass yeet logs --limit 100
```

The audit log is stored in SQLite and automatically pruned after 90 days.

---

## How do I change the file expiry time?

Set `FILE_EXPIRY_HOURS` in `.env`:

```bash
FILE_EXPIRY_HOURS=72   # 3 days
```

Restart the container for changes to take effect, or change it live with `docker exec yeet yeet set-expiry 72`. This is the default; uploaders can still pick 1 hour to 7 days. Existing files keep their original expiry.

---

## Files aren't expiring

The cleanup task runs every 15 minutes. If it hasn't run yet, wait a bit. You can also trigger it manually:

```bash
YEET_ADMIN_PASSWORD=pass yeet cleanup
# or
curl -u admin:pass -X POST https://yeet.example.com/admin/cleanup
```

Expired files are moved to `/data/archive/` and permanently deleted 24 hours later by the same task.

---

## How much disk space does it use?

Storage is capped by `STORAGE_LIMIT` (default 10GB). When the limit is reached, new uploads return HTTP 507.

Check current usage:

```bash
YEET_ADMIN_PASSWORD=pass yeet stats
```

The ClamAV database takes about 170 MB in `/data/clamav-db`.

---

## Can I run it without Docker?

Yes, though Docker is recommended:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in SECRET_KEY

mkdir -p data/uploads data/archive data/partial

# Override data paths for local development
export BASE_DIR=./data
export DB_PATH=./data/yeet.db
export UPLOAD_DIR=./data/uploads
export ARCHIVE_DIR=./data/archive
export PARTIAL_DIR=./data/partial
export CLAMAV_ENABLED=false  # unless you have clamd running locally

uvicorn app.main:app --host 0.0.0.0 --port 8000
```
