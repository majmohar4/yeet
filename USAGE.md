# Using yeet

A practical guide. For deployment and operational topics see [README](README.md) and [FAQ](FAQ.md); for vulnerability reporting see [SECURITY](SECURITY.md).

## At a glance

| What | Where | Result |
|---|---|---|
| Single file | drop on home page, or click drop zone | `https://yeet.../f/<id>` |
| Folder / many files | drag a folder, or click **📁 Folder** | `https://yeet.../b/<id>` |
| Quick text snippet | press <kbd>Ctrl</kbd>+<kbd>V</kbd>, or **📋 Paste** on mobile | `https://yeet.../c/<id>` |
| Pasted screenshot | press <kbd>Ctrl</kbd>+<kbd>V</kbd> with image on clipboard | `https://yeet.../c/<id>` |
| Phone camera grab | tap **🖼 Photo** | `https://yeet.../c/<id>` |

## File upload

### From the browser

- **Drop** files anywhere on the upload area, **click** to open the file picker, or **paste** (<kbd>Ctrl</kbd>+<kbd>V</kbd>) — works for binary files too (paste an image and it uploads as a clipboard item).
- Optional **🔒 password** — recipients enter it to unlock the download.
- Optional **expiry** — pick 1h, 24h, or 7d.
- Optional **🔥 burn after read** — file is wiped from disk the instant it's served. The download URL only ever works once.
- A progress bar shows upload progress; on success the link is shown and copied to your clipboard.

### From a terminal

```bash
# JSON response (default)
yeet upload photo.jpg
yeet upload secret.pdf --password hunter2
yeet upload contract.pdf --max-downloads 3
yeet upload one-shot.zip --burn

# Plain-text URL — handy for piping
curl -F f=@build.log https://yeet.majmohar.eu/raw
# → https://yeet.majmohar.eu/f/abc123…
```

`POST /raw` is the curl-friendly companion: it accepts a `multipart/form-data` field named `f` and replies with the URL on stdout (newline-terminated). No JSON parsing required.

### From a phone or tablet

Hardware Ctrl-V doesn't exist on touch devices, so the upload area now has three quick-action chips:

- **📋 Paste** — opens a text box. On modern browsers it tries to read your clipboard automatically (you may see a permission prompt). If denied, long-press inside the box and choose Paste, or just type. Pasting an image inside the box uploads it as a clipboard image.
- **🖼 Photo** — opens your camera or photo library directly.
- **📁 Folder** — picks a folder and uploads it as a bundle.

## Folders / bundles

A bundle is a group of files sharing one URL. Member files are still individual on the server — each has its own scan, expiry, and `/f/<id>` URL.

- **Drag a folder** onto the upload area, or **drop multiple files at once**, or click **📁 Folder**.
- The bundle page at `/b/<id>` shows each file with size, lets you preview/download individually, and offers **⬇ Download all (.zip)** which streams the whole tree.
- Up to 200 files per bundle. Each file still respects the per-file size cap; total bandwidth still counts against your daily IP budget.

## Public clipboard

The **📋 Public clipboard** panel on the home page is a global feed of recent text and image pastes from everyone using the site.

- Items you posted are tinted green and labelled **YOU** — pin and 🗑 delete are available only on those rows.
- Anyone can copy or share any item. **Don't paste secrets, credentials, or personal data here.**
- Each item gets its own page at `/c/<id>` with a copy button and a `?raw=1` plain-text view. Image items expose a direct image URL.
- Items expire on the schedule you picked at upload time, or after the server default. Pinned items stay until you unpin or delete them.

## Sharing options at a glance

| You want… | Set… |
|---|---|
| One-off link, then gone | **🔥 burn after read** |
| Limit how many people can grab it | `--max-downloads N` (or `Max downloads` on the form) |
| Lock it behind a passphrase | password field (or `--password`) on upload |
| Custom lifetime | pick a chip (1 h / 24 h / 7 d) — burn implies single-view |
| Share many files at once | drop a folder, get a bundle URL |
| Quick code snippet share | <kbd>Ctrl</kbd>+<kbd>V</kbd> the text → `/c/<id>` |
| Skip your own daily limits | use a single-use bypass code (admin issues with `yeet generate-code`) |

## Supported file formats

See [FORMATS.md](FORMATS.md) for the full categorised list. The short version:

- **Almost everything is accepted.** The check is permissive: `~600+` named extensions plus a fallback that accepts any reasonable extension shape (`^\.[a-z0-9_+-]{1,12}$`).
- **Executables are flagged but allowed** — `.exe .msi .dll .so .dmg .deb .rpm .bat .ps1 .sh .vbs .scr .hta .jar` etc. show a ⚠ badge so downloaders know.
- **Browser-renderable types are neutralised** — `.html .svg .js .wasm` still upload, but `/raw/<id>` serves them as plain-text source so they can't execute or phish.
- **No-extension files work** — Makefiles, `id_rsa.pub`, etc. ClamAV still scans.

## Limits

- Per-file upload: configurable, default 100 MB. Visible in the footer.
- Daily upload bandwidth: derived from the per-file cap (`≤1 GB → 1 GB/day`, `1–3 GB → 3 GB/day`, otherwise per-file). Resets on a 24 h rolling window per IP.
- Concurrent uploads: 2 per IP.
- Downloads: 100/IP/hour for the count gate; per-file `max_downloads` is enforced separately.
- Bundle: up to 200 files.
- Clipboard text: 100 000 characters; clipboard image: 10 MB; 20 pastes/IP/hour.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Upload returns 413 | File exceeds the per-file limit. Pack as `.zip` if needed, or ask the admin for a bypass code. |
| Upload returns 429 | You hit the daily upload bandwidth cap; the response includes `Retry-After`. |
| Upload returns 422 | ClamAV detected malware. The file is rejected and logged in the audit trail. |
| `/raw/<id>` returns 403 | The file is an executable or burn-after-read. Use `/f/<id>` to download. |
| `/raw/<id>` shows source for HTML | By design — yeet never renders user-uploaded HTML. Use **⬇ Download** to save the raw file. |
| Burn link returns 410 / 404 on second click | The single view has been claimed; the file is permanently gone. |
| Mobile clipboard read denied | Some browsers restrict `navigator.clipboard.read()`. Type or long-press → paste in the text box instead. |
