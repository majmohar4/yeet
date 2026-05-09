# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), versioning follows [Semantic Versioning](https://semver.org/).

---

## [1.1.0] — 2026-05-09

### Added

**Folders / bundles**
- Upload a directory or multiple files as one shareable bundle at `/b/<id>`.
- New endpoints: `POST /upload-bundle`, `GET /b/<id>`, `GET /b/<id>/zip` (streamed), `DELETE /api/bundles/<id>`, `GET /api/bundles/all`.
- Drag a folder onto the drop zone, or click **📁 Folder**, or drag multiple files at once.
- Each member is scanned individually by ClamAV and gets its own `/f/<id>` URL.
- Zip download streams from a temp file (memory-safe; supports ZIP64 for very large bundles).

**Burn after read**
- New **🔥 burn** checkbox on the upload form (and `--burn` flag in the CLI).
- Atomic single-view: a transactional claim ensures only one downloader can win even with concurrent requests.
- File is wiped from upload and archive directories the instant it's served — no 24h archive grace period.
- `/raw/<id>` refuses burn files outright (so an inline preview can't silently consume the only view).

**Public clipboard**
- The clipboard panel on the home page now shows recent text/image pastes from everyone.
- Items you posted are tagged **YOU** with a green tint; pin and delete are still owner-only (server enforces session match).
- Drawer is open by default with `max-height: min(36vh, 320px)` so it never dominates the page; scrolls when full.
- Other users' session ids are never returned by the API.

**Mobile + tablet**
- New quick-action chips inside the drop zone: **📋 Paste**, **🖼 Photo**, **📁 Folder** — all ≥44 px tap targets.
- **Paste** opens a modal that tries `navigator.clipboard.read()`/`readText()` automatically (works after a user gesture on iOS/Android), and falls back to a manual paste textarea.
- **Photo** uses `accept="image/*" capture="environment"` for direct camera or photo-library access.
- `@media (pointer: coarse)` swaps "Ctrl+V" hints for tap copy.
- Fully responsive bundle viewer, clipboard rows, file grid, and modals down to 480 px.

**Curl-friendly upload**
- `POST /raw` accepts a multipart `f` field and returns the URL on stdout (newline-terminated). No JSON to parse — `curl -F f=@thing.txt $YEET/raw` just works.

**Massively expanded extension allowlist**
- `~600+` explicit extensions across camera RAW (`.cr3`, `.dng`, `.arw` …), e-books, subtitles, big-data formats (`.parquet`, `.arrow`, `.feather`, `.h5`, `.hdf5`, `.nc`), GIS (`.geojson`, `.kml`, `.gpx`, `.shp`, `.las`), 3D/CAD, game files (Minecraft, Source, Unreal, Bethesda mods), retro ROMs, scientific data, fonts, audio trackers/chiptune (`.mod`, `.s3m`, `.it`, `.sf2`), shaders (`.glsl`, `.hlsl`, `.wgsl`, `.metal`), more languages (OCaml, Pascal, Ada, Racket, Raku, F#, …), templates, build tools, mail, calendars, certificates, and more.
- New permissive fallback: any `^\.[a-z0-9_+-]{1,12}$` extension is accepted silently, so even unrecognised types pass.
- New `EXECUTABLE_EXTENSIONS` set covers Windows (`.exe .msi .dll .scr .bat .ps1 .vbs .hta .reg …`), Unix (`.sh .so .deb .rpm .appimage …`), macOS (`.dmg .dylib .app .scpt .workflow`), and Java (`.jar .war .ear`). These upload fine but get a ⚠ badge in the UI and are refused by `/raw/<id>` so they can never render inline.
- New `WEB_RENDERABLE_EXTENSIONS` set (`.html .svg .js .wasm …`) — accepted, but `/raw/<id>` forces `Content-Type: text/plain; charset=utf-8` so the browser shows source instead of executing.

### Changed

- `/api/files/all` now also returns `bundles` and annotates each file with `dangerous` and `web_renderable` flags.
- Files without an extension are now accepted (a `Makefile`, `id_rsa.pub`, etc.). ClamAV still scans.
- Burn-after-read implies `max_downloads=1`; the smaller cap wins if both are set.

### Security

- `/raw/<id>` now actively neutralizes script-y types (text/plain for HTML/SVG/JS/WASM) and refuses executables outright. Combined with the existing strict CSP, this kills the inline-XSS and inline-phishing surfaces while keeping legitimate previews.
- New atomic claim semantics for burn-after-read prevent race conditions where two concurrent downloaders could both succeed.
- Public clipboard listing strips session ids from responses; pin/delete still require the matching `X-Session-ID`.

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
