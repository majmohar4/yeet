# Security Policy

## Reporting a vulnerability

If you find a security issue, please **do not open a public GitHub issue**. Instead, email:

**info@majmohar.eu**

Include:
- What the vulnerability is and where it exists
- Steps to reproduce
- Potential impact

I aim to acknowledge reports within 48 hours and provide a fix or mitigation timeline within 7 days for confirmed issues.

---

## Scope

**In scope:**
- Authentication bypass on admin panel or password-protected files
- Path traversal allowing access to files outside `/data/uploads/`
- Remote code execution
- SQL injection
- Stored/reflected XSS
- CSRF on sensitive actions
- Rate limiting bypass
- Information disclosure (server paths, internal data)
- Insecure file handling (e.g. zip slip)

**Out of scope:**
- Denial of service via large file uploads (mitigated by file size limits)
- Social engineering
- Issues requiring physical access to the server
- Third-party infrastructure (hosting provider, CDN)
- Self-hosted instances with misconfigured environments (missing reverse proxy, exposed admin port, etc.)

---

## Known limitations

- **ClamAV is not a guarantee.** Virus scanning uses ClamAV which may not detect all threats, especially novel or targeted malware. It is a defence-in-depth measure, not a silver bullet.
- **Files are not end-to-end encrypted.** Files are stored plaintext on disk. Password protection only gates access via the web interface — it does not encrypt the stored file.
- **Admin panel uses HTTP Basic Auth.** This is secure over HTTPS but should never be exposed without TLS.
- **Rate limiting is per-IP.** Requests behind NAT or shared egress may be affected.
- **Archive auto-deletes after 24 h.** Expired files move to `/data/archive/` and are permanently removed by the cleanup loop after 24 hours. Burn-after-read files skip the archive entirely.
- **Public clipboard is public.** The clipboard manager feed at `/api/clipboard/recent` is intentionally global — anyone visiting the site sees all non-expired pastes. Don't put secrets in there. Pin and delete remain owner-only (require the matching `X-Session-ID`).
- **Permissive extension policy.** Any reasonably-shaped extension is accepted (see "File handling" below). Defence is layered, not gated by the allow-list.

---

## Security features overview

| Feature | Implementation |
|---|---|
| Session signing | `itsdangerous` with `SECRET_KEY` |
| Password hashing | `bcrypt` with random salt |
| File IDs | 128-bit random UUID (hex), no guessable pattern |
| Path traversal prevention | File IDs validated as 32-char hex; files stored by UUID only |
| SQL injection prevention | All queries use parameterized statements (aiosqlite) |
| XSS prevention | Jinja2 auto-escaping + `Content-Security-Policy` header |
| Clickjacking prevention | `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'` |
| MIME sniffing prevention | `X-Content-Type-Options: nosniff` |
| Virus scanning | ClamAV via TCP socket (INSTREAM protocol) |
| Rate limiting | Per-IP byte-bucket on uploads (24 h rolling), per-IP count on downloads (1 h sliding) |
| Concurrent uploads | In-memory semaphore, 2 / IP |
| Burn-after-read | Atomic `UPDATE … WHERE claimed_at IS NULL`; only one downloader can win |
| Audit logging | All uploads, downloads, and admin actions logged |
| Non-root container | App runs as UID 1000 inside Docker |
| Capability drop | Docker `cap_drop: ALL`, `no-new-privileges` |
| Dependency pinning | `requirements.txt` with exact versions |

---

## File handling — defence in depth

Filename extensions are **not** the security boundary. The allow-list is permissive (`~600+` known extensions plus a regex fallback for anything else extension-shaped). What actually keeps things safe:

1. **ClamAV scans every byte** before the file is committed.
2. **Files are stored by random UUID with no extension** under `/data/uploads/<uuid>` — nothing the server reaches for can be executed.
3. **`/f/<id>` always sends `Content-Disposition: attachment` and `X-Content-Type-Options: nosniff`.** Browsers will download, never render.
4. **`/raw/<id>` is hardened by type:**
   - Web-renderable types (`.html .htm .svg .js .wasm` …) are served as `text/plain; charset=utf-8` so the browser shows source — no script execution, no fake-login phishing.
   - Executables (`.exe .msi .dll .so .dmg .deb .rpm .bat .ps1 .sh .vbs .scr .hta` …) are refused outright (HTTP 403). They can only be downloaded via `/f/<id>`.
   - Everything else keeps its declared MIME plus a `default-src 'none'; img-src 'self'; style-src 'unsafe-inline';` CSP that blocks scripts, fetches, forms, and frames.
5. **Burn-after-read** files are also refused by `/raw/<id>` so a preview can never silently consume the only view, and they are permanently deleted from disk + archive the instant they're served.
6. **Bundle members** are full files in their own right — same scan, same `/f/<id>`, same expiry. The bundle envelope (`/b/<id>`) just groups them.

---

## Re-running security tests

```bash
# Requires a running server at localhost:8000
pip install httpx pytest
pytest tests/test_security.py -v
```

Set `YEET_TEST_URL` to test a remote instance, and `YEET_TEST_ADMIN_PASSWORD` for admin tests.
