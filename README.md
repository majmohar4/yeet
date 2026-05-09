# yeet

A minimal, secure file sharing server. Drop a file, get a link, share it. Files expire in 24 hours. No accounts, no tracking, no bloat.

**Live instance:** [yeet.majmohar.eu](https://yeet.majmohar.eu)

```
$ yeet upload photo.jpg
✓ Upload complete

  URL:      https://yeet.majmohar.eu/f/a3f9c2...
  Expires:  2025-06-01
  Size:     2.4 MB
  Scan:     clean
```

For a hands-on walkthrough of every feature see **[USAGE.md](USAGE.md)**, the full file-format catalog is in **[FORMATS.md](FORMATS.md)**, and the security/threat model is in **[SECURITY.md](SECURITY.md)**.

## Features

**Sharing**
- Single files (`/f/<id>`), folders / multi-file bundles (`/b/<id>`), and quick text/image clipboard items (`/c/<id>`)
- Files expire automatically (default: 24 hours, configurable per upload — 1 h / 24 h / 7 d)
- Optional **🔥 burn after read** — file is wiped from disk the moment it's served (atomic, race-free)
- Optional password protection (bcrypt) and per-file download limit
- Public clipboard panel — recent text/image pastes from everyone; pin/delete remain owner-only
- Folder uploads scan each member individually, expose a single share URL, and offer a streaming `.zip` download

**Mobile + tablet**
- Quick **📋 Paste**, **🖼 Photo**, **📁 Folder** buttons in the drop zone (no Ctrl-V required)
- Camera capture and clipboard-API integration; manual paste textarea fallback
- Fully responsive layout, ≥44 px tap targets, `pointer: coarse` adaptations

**Security**
- ClamAV virus scanning on every upload — infected files rejected and logged
- `/raw/<id>` neutralizes web-renderable types (HTML/SVG/JS/WASM served as plain-text source) and refuses executables outright
- All `/f/<id>` downloads forced via `Content-Disposition: attachment` + `nosniff`
- Strict Content-Security-Policy on all asset paths
- Honeypot field rejects most upload bots silently
- Audit log for every upload, download, deletion, virus hit, and admin action

**Limits**
- Byte-based rate limiting — 1 GB/day per IP, scales up with file size limit
- Concurrent upload limit (max 2 simultaneous per IP)
- Storage cap with 80% warning and 90% block
- **Bypass codes** — single-use codes that skip all upload limits (`yeet generate-code`)

**Files / file types**
- ~1000 explicitly supported extensions across docs, media, RAW, scientific, GIS, 3D/CAD, fonts, e-books, archives, source code (see [FORMATS.md](FORMATS.md))
- Permissive fallback — any extension shape passes (`^\.[a-z0-9_+-]{1,12}$`)
- ⚠ executable badge on `.exe`/`.dll`/`.deb`/`.dmg`/`.sh`/etc. so downloaders know what they're getting

**Daily-driver UX**
- Drag-and-drop with progress bar
- Search and filter your recent uploads on the main page
- Collapsed "recently deleted" section shows virus scanner activity
- `POST /raw` for `curl -F f=@thing.txt $YEET/raw` — returns the URL on stdout
- No JavaScript required to download
- Admin panel (HTTP Basic Auth)
- Zero third-party analytics

## Quick start

```bash
# 1. Clone
git clone https://github.com/majmohar4/yeet
cd yeet

# 2. Configure
cp .env.example .env
# Edit .env — set SECRET_KEY and ADMIN_PASSWORD

# 3. Start
docker compose up -d

# 4. Visit http://localhost:8000
```

Detailed deployment instructions are below.

---

## Deployment

### Prerequisites

- Docker 24+
- Docker Compose v2+
- A reverse proxy (nginx, Caddy, or Cloudflare Tunnel) — see `examples/`

**Architecture support:** Works on both **ARM64 (Apple Silicon M1/M2/M3)** and **x86_64** (standard servers). ClamAV is installed directly inside the container via `apt`, so no platform-specific Docker image is needed.

### Step 1: Generate a secret key

```bash
openssl rand -hex 32
```

Paste the output into `.env` as `SECRET_KEY`.

### Step 2: Set an admin password

```bash
echo "ADMIN_PASSWORD=your_strong_password" >> .env
```

### Step 3: Start the stack

```bash
docker compose up -d
```

ClamAV runs inside the yeet container and downloads virus definitions on first start (~110MB, takes ~1–2 minutes). The app starts after definitions are loaded. Check progress with:

```bash
docker compose logs -f yeet
```

### Step 4: Reverse proxy

Point your reverse proxy to `127.0.0.1:8000`. Example configs are in `examples/`.

For Caddy (easiest):
```
yeet.example.com {
    reverse_proxy localhost:8000
}
```

---

## Bypass codes

Bypass codes let a trusted person upload without hitting any limits (file size, storage, rate limit). Each code is single-use and expires in 15 minutes.

```bash
# Generate a code (inside Docker)
docker exec yeet yeet generate-code
#   Code:    A1B2C3D4
#   Expires: 2025-06-01T12:15:00 UTC

# List active codes
docker exec yeet yeet list-codes

# Revoke a code
docker exec yeet yeet revoke-code A1B2C3D4
```

On the upload page, enter the code in the "Bypass Code" field. The response will confirm `"bypassed": true`.

---

## Rate limiting

Uploads are limited by **bytes uploaded per IP per 24 hours** (rolling window):

| Per-file limit | Daily IP limit |
|---|---|
| ≤ 1 GB | 1 GB/day |
| 1–3 GB | 3 GB/day |
| > 3 GB | same as per-file limit |

At 80% of the daily limit, a warning is shown in the upload response. At 100%, uploads return HTTP 429 with a `Retry-After` header and time until reset.

Downloads are limited to 100 per IP per hour.

---

## CLI tool

Install by adding the `yeet` script to your PATH:

```bash
sudo cp yeet /usr/local/bin/yeet
```

Or use it locally:

```bash
./yeet upload file.txt
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `YEET_URL` | `https://yeet.majmohar.eu` | Server URL |
| `YEET_ADMIN_PASSWORD` | — | Admin password for admin commands |

### Commands

```
Admin (direct DB access — run via docker exec):
  stats                     Storage usage, file counts
  generate-code             Generate bypass code (valid 15 min)
  list-codes                List active bypass codes
  revoke-code <code>        Delete a bypass code
  set-file-limit <size>     e.g. 500MB, 2GB
  set-storage-limit <size>  e.g. 10GB, 50GB
  set-expiry <hours>        e.g. 48
  delete <file-id>          Delete a file
  list [--archived]         List active or archived files
  list-archived             List archived files
  logs [--limit N]          Audit log
  cleanup                   Archive expired files now

HTTP (uses YEET_URL):
  upload <file> [--password <pw>] [--max-downloads <n>] [--bypass-code <code>] [--burn]
  download <url> [--output <path>] [--password <pw>]
  health
```

For one-liners that don't need JSON:

```bash
# Upload, get URL on stdout
curl -F f=@build.log https://yeet.majmohar.eu/raw
# → https://yeet.majmohar.eu/f/abc123…
```

### Examples

```bash
# Inside Docker (admin commands)
docker exec yeet yeet stats
docker exec yeet yeet generate-code
docker exec yeet yeet set-file-limit 500MB
docker exec yeet yeet list
docker exec yeet yeet logs --limit 100

# Upload via HTTP
YEET_URL=https://yeet.majmohar.eu yeet upload photo.jpg
yeet upload secret.pdf --password hunter2
yeet upload invoice.pdf --max-downloads 3 --bypass-code A1B2C3D4

# Download
yeet download https://yeet.majmohar.eu/f/abc123
yeet download https://yeet.majmohar.eu/f/abc123 --password hunter2

# Check health
yeet health
```

---

## Configuration

All settings are environment variables. Copy `.env.example` to `.env` and adjust.

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | **required** | Session signing key (min 32 chars) |
| `ADMIN_PASSWORD` | — | Admin panel password (leave empty to disable) |
| `MAX_FILE_SIZE` | `104857600` | Max upload size in bytes (100MB) |
| `STORAGE_LIMIT` | `10737418240` | Total disk limit in bytes (10GB) |
| `FILE_EXPIRY_HOURS` | `24` | Hours until files expire |
| `CLAMAV_ENABLED` | `true` | Enable ClamAV virus scanning |
| `RATE_LIMIT_DOWNLOADS` | `100` | Max downloads per IP per hour |

These values can also be changed at runtime via the CLI (stored in `/data/config.json`):

```bash
docker exec yeet yeet set-file-limit 500MB
docker exec yeet yeet set-storage-limit 50GB
docker exec yeet yeet set-expiry 48
```

---

## Security tests

```bash
# Install test dependencies
pip install httpx pytest

# Start the server first, then:
pytest tests/test_security.py -v
```

73 tests covering: security headers, rate limiting, password protection, path traversal, XSS, SQL injection, admin authentication, content-type handling, and more.

---

## Admin panel

The admin API requires HTTP Basic Auth with `admin` / `$ADMIN_PASSWORD`:

```bash
# List files
curl -u admin:pass https://yeet.example.com/admin

# Delete a file
curl -u admin:pass -X DELETE https://yeet.example.com/admin/files/<id>

# View audit log
curl -u admin:pass https://yeet.example.com/admin/logs

# Run cleanup manually
curl -u admin:pass -X POST https://yeet.example.com/admin/cleanup
```

---

## How it works

1. File is uploaded and read into memory
2. ClamAV scans the bytes (if enabled)
3. File is saved to `/data/uploads/<uuid>` with no extension on disk — nothing yeet stores can be executed
4. Metadata (filename, hash, expiry, password hash, dangerous/burn flags) is stored in SQLite
5. A link is returned — `/f/<32-char-hex-id>` for files, `/b/<id>` for folder bundles, `/c/<id>` for clipboard items
6. Background task checks every 15 minutes: expired files move to `/data/archive/`, files archived more than 24 h ago are permanently deleted
7. **Burn-after-read** files skip the archive — they are wiped from disk the moment they are served (single atomic claim prevents double-serves)

---

## Troubleshooting

**ClamAV is slow on first run** — it downloads ~300MB of virus definitions. This is normal. Subsequent starts use the cached database.

**"SECRET_KEY is not set"** — copy `.env.example` to `.env` and fill in the value.

**Files not expiring** — the cleanup task runs every 15 minutes. You can trigger it manually: `yeet cleanup` or `curl -u admin:pass -X POST /admin/cleanup`.

See [FAQ.md](FAQ.md) for more common questions.

---

## Documentation

- **[USAGE.md](USAGE.md)** — feature-by-feature walkthrough (mobile paste, folder uploads, burn-after-read, clipboard, bypass codes, troubleshooting)
- **[FORMATS.md](FORMATS.md)** — full categorized list of supported file extensions
- **[SECURITY.md](SECURITY.md)** — threat model, defence-in-depth controls, vulnerability reporting
- **[CHANGELOG.md](CHANGELOG.md)** — what shipped when
- **[FAQ.md](FAQ.md)** — operations questions (backups, ClamAV, rate limits, …)
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — development setup and code style

## License

MIT — see [LICENSE](LICENSE).
