# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), versioning follows [Semantic Versioning](https://semver.org/).

---

## [1.0.0] — 2025-06-01

Initial public release.

### Added

**Core features**
- File upload via drag-and-drop or file picker
- Automatic file expiry (default: 24 hours, configurable)
- Optional password protection with bcrypt hashing
- Optional download limit per file
- Shareable direct download links (`/f/<id>`)
- No account required for upload or download

**Security**
- ClamAV virus scanning via INSTREAM protocol
- Rate limiting: 10 uploads / 100 downloads per IP per hour
- Full security header suite: CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, HSTS
- File IDs are 128-bit random UUIDs — not guessable or enumerable
- All database queries use parameterized statements
- Filenames sanitised before storage and display
- Non-root Docker container with capability drop
- Audit log for all upload, download, and admin actions

**Admin panel**
- HTTP Basic Auth protected admin API
- List all files with metadata
- Delete individual files
- View audit log (pruned after 90 days)
- Trigger manual cleanup
- Storage usage statistics

**Infrastructure**
- Multi-stage Dockerfile (builder + minimal runtime image)
- Docker Compose with resource limits, health checks, and ClamAV
- SQLite database with WAL mode for concurrent reads
- Background cleanup task (runs every 15 minutes)
- Graceful shutdown

**CLI tool**
- `yeet upload` — upload files with optional password and download limit
- `yeet download` — download files from the command line
- `yeet list` — list all files (admin)
- `yeet delete` — delete a file (admin)
- `yeet logs` — view audit log (admin)
- `yeet cleanup` — trigger expired file archiving (admin)
- `yeet stats` — storage usage summary (admin)
- `yeet health` — check server health
- Auto-copy URL to clipboard (macOS/Linux)

**UI**
- Minimal dark terminal aesthetic (JetBrains Mono, green accent on near-black)
- Drag-and-drop upload with XHR progress bar
- Responsive layout
- Works without JavaScript (download page)
- Subtle scan-line CSS overlay

**Documentation**
- README with quick start, CLI reference, config reference
- SECURITY.md with vulnerability reporting and known limitations
- CONTRIBUTING.md with development setup and guidelines
- FAQ.md covering common operational questions
- Example configs: Nginx, Caddy, Cloudflare Tunnel, systemd service
- 73 automated security tests
