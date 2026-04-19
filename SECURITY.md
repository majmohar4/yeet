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
- **Archive is not auto-purged.** Expired files move to `/data/archive/` but are not automatically deleted. Run `yeet cleanup` or `docker exec` to purge if needed.

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
| Rate limiting | Per-IP, per-action, 1-hour sliding window in SQLite |
| Audit logging | All uploads, downloads, and admin actions logged |
| Non-root container | App runs as UID 1000 inside Docker |
| Capability drop | Docker `cap_drop: ALL`, `no-new-privileges` |
| Dependency pinning | `requirements.txt` with exact versions |

---

## Re-running security tests

```bash
# Requires a running server at localhost:8000
pip install httpx pytest
pytest tests/test_security.py -v
```

Set `YEET_TEST_URL` to test a remote instance, and `YEET_TEST_ADMIN_PASSWORD` for admin tests.
