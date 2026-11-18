# Using yeet

A practical guide. For deployment see [README](README.md) and [FAQ](FAQ.md); for vulnerability reporting see [SECURITY](SECURITY.md).

## At a glance

| What | How | Link |
|---|---|---|
| One or more files | drop them on the page, or **Files** | `https://yeet…/f/<id>` per file |
| Photos and videos from a phone | **Photos & videos** (opens the gallery) | `https://yeet…/f/<id>` |
| A folder | drag it in, or **Folder** | `https://yeet…/b/<id>` |
| Text snippet | <kbd>Ctrl</kbd>/<kbd>⌘</kbd>+<kbd>V</kbd> on the page, or **Paste** | `https://yeet…/c/<id>` (public) |
| Screenshot | paste it; hold <kbd>Shift</kbd> to upload it as a file instead | `/c/<id>`, or `/f/<id>` with Shift |

## Uploading in the browser

- Drop files anywhere on the page, tap the upload area, or use the buttons under it.
- **Options** (set them before picking files):
  - **Delete after** — 1 hour, 1 day or 7 days.
  - **Password** — recipients must enter it before they can preview or download. The key button generates one and copies it; send it separately from the link.
  - **Single download** — the file is erased the moment someone downloads it. Link previews in chat apps don't count, so you can paste the link anywhere. Doesn't apply to folders.
- Each upload gets a row with progress, speed and time left. When it's done the link is copied for you; on phones a **Share** button opens the system share sheet. **Copy all links** appears when several uploads finish.

### Big files

Files are sent in 16 MB pieces, three at a time, so no single request comes near Cloudflare's 100 MB limit.

- A dropped connection only resends the piece that failed. Uploads pause while you are offline and continue when you're back.
- If you close or reload the tab, pick the same file again within a few hours and the upload continues where it stopped.
- After the last piece the server runs a virus scan. The first scan after a quiet period waits for the scanner to start (about a minute). The row says *Scanning* meanwhile.
- Keep the tab open until the row shows the link. On phones the screen is kept awake during uploads where the browser allows it.

## Downloading

Opening a `/f/<id>` link in a browser shows a page with the file name, size, expiry, a preview for images, video, audio and text, and a **Download** button.

- Downloads support HTTP Range, so browsers can pause and resume them.
- In Chrome and Edge, files over 100 MB are fetched in 64 MB pieces straight to disk; a failed piece is retried on its own.
- Scripts get the file directly: `curl -OJ https://yeet…/f/<id>`. Add `?dl=1` to force the raw file from a browser.

## From a terminal

```bash
# The bundled CLI uploads in pieces and resumes downloads
YEET_URL=https://yeet.example.com ./yeet upload video.mov --expiry 168
./yeet upload secret.pdf --password hunter2
./yeet upload contract.pdf --max-downloads 3
./yeet upload one-shot.zip --burn
./yeet download https://yeet.example.com/f/<id> [--password …]   # re-run to resume

# Plain curl — prints the link (fine up to your proxy's request limit)
curl -F f=@build.log https://yeet.example.com/raw
curl -F f=@photo.jpg -F burn=1 -F expiry_hours=1 https://yeet.example.com/raw
```

### HTTP API (chunked)

```text
POST /api/uploads                {name, size, password?, expiry_hours?, burn?, max_downloads?, session_id?}
                                 → 201 {id, chunk_size, chunk_count}
PUT  /api/uploads/{id}/chunks/{n}  raw bytes of chunk n (any order, safe to retry)
POST /api/uploads/{id}/complete  → 201 {url, …}  or 202 while scanning
GET  /api/uploads/{id}           → {status: uploading|processing|done|failed, received, result}
```

Folders: `POST /api/bundles {name, file_count, total_size}` → `{id, token}`, then upload each file with `bundle_id`, `bundle_token` and `path`, then `POST /api/bundles/{id}/complete {bundle_token}`.

## Folders

- The folder page at `/b/<id>` lists every file with its own link and offers **Download all (.zip)**, built on the fly.
- Up to 1000 files per folder; each file still respects the per-file limit and is scanned on its own.
- A password applies to every file in the folder. Zipping a password-protected folder is disabled by design; open the individual links instead.

## Public clipboard

The **Public clipboard** panel is a global feed of recent text and image pastes from everyone.

- Your own items are marked **you**; only you can pin or delete them.
- **Anyone can read every item. Don't paste secrets, credentials or personal data.**
- Each item has a page at `/c/<id>` and a plain-text view at `/c/<id>/raw`.
- Clipboard images must be PNG, JPEG, GIF or WebP under 10 MB. Anything else (HEIC, big photos) is uploaded as a normal file instead.

## Your files

The **Files** list shows **Mine** (uploaded from this browser) or **Everyone**. Ownership is tied to a random ID in your browser's local storage — clearing site data or switching browsers means you can no longer delete earlier uploads, but their links keep working until they expire.

## Supported formats

See [FORMATS.md](FORMATS.md). In short: almost everything is accepted, executables are allowed but marked, and HTML/SVG/JS are only ever shown as source.

## Limits

- Per file: set by the admin (shown on the home page). Default 100 MB.
- Daily upload volume per IP, derived from the per-file limit (≤ 1 GB → 1 GB/day, 1–3 GB → 3 GB/day, otherwise equal to the per-file limit). Rolling 24 h window.
- Up to 6 unfinished chunked uploads per IP at once.
- Downloads: 100 per IP per hour; resuming a download doesn't count again.
- Clipboard: 100 000 characters of text, 10 MB images, 20 pastes per IP per hour.

## Troubleshooting

| Symptom | Meaning |
|---|---|
| "File too large" (413) | Over the per-file limit. Ask the admin for a bypass code if needed. |
| "Daily upload limit reached" (429) | Your IP hit the 24 h volume cap; the message says when it resets. |
| "malware detected" (422) | ClamAV flagged the file. It was not stored. |
| "Virus scanner unavailable" (503) | The scanner is down or restarting; uploads are refused rather than accepted unscanned. Retry in a minute. |
| "out of disk space" (507) | The server is full. Nothing was stored; existing links still work. |
| Upload stuck on *Scanning* | Large files take a while to scan. Leave the tab open; it continues on its own. |
| Video preview doesn't play | Your browser doesn't support that codec. Download it instead. |
| Single-download link shows 404/410 | Someone already downloaded it; the file is gone. |
| Mobile paste doesn't fill the box | The browser blocked clipboard access. Long-press the box and choose Paste. |
