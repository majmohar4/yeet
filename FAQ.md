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

ClamAV needs to download its virus definition database (~300MB) on first start. This is completely normal. The download happens inside the container and can take 2–5 minutes depending on your connection.

Once downloaded, the definitions are stored in the `clamav-db` Docker volume and reused on every restart. Subsequent starts are fast.

You can watch it happen:

```bash
docker compose logs -f clamav
```

Wait until you see `socket found, clamd started.`

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

Everything lives in two Docker volumes:

| Volume | Contents |
|---|---|
| `yeet-data` | Uploads, archive, SQLite database |
| `clamav-db` | Virus definitions (can be re-downloaded, not critical) |

Back up `yeet-data`:

```bash
# Dump to a tar file
docker run --rm -v yeet_yeet-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/yeet-backup-$(date +%Y%m%d).tar.gz /data
```

To restore:

```bash
docker run --rm -v yeet_yeet-data:/data -v $(pwd):/backup \
  alpine sh -c "cd /data && tar xzf /backup/yeet-backup-20250101.tar.gz --strip-components=1"
```

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

And remove (or comment out) the `clamav` service from `docker-compose.yml`, along with the `depends_on` block in the `yeet` service.

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

Restart the container for changes to take effect. This only affects new uploads — existing files keep their original expiry.

---

## Files aren't expiring

The cleanup task runs every 15 minutes. If it hasn't run yet, wait a bit. You can also trigger it manually:

```bash
YEET_ADMIN_PASSWORD=pass yeet cleanup
# or
curl -u admin:pass -X POST https://yeet.example.com/admin/cleanup
```

Expired files are moved to `/data/archive/`, not deleted. To actually free disk space, delete files from the archive directory.

---

## How much disk space does it use?

Storage is capped by `STORAGE_LIMIT` (default 10GB). When the limit is reached, new uploads return HTTP 507.

Check current usage:

```bash
YEET_ADMIN_PASSWORD=pass yeet stats
```

The ClamAV database takes ~300MB in the `clamav-db` volume.

---

## Can I run it without Docker?

Yes, though Docker is recommended:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in SECRET_KEY

mkdir -p data/uploads data/archive

# Override data paths for local development
export DB_PATH=./data/yeet.db
export UPLOAD_DIR=./data/uploads
export ARCHIVE_DIR=./data/archive
export CLAMAV_ENABLED=false  # unless you have clamd running locally

uvicorn app.main:app --host 0.0.0.0 --port 8000
```
