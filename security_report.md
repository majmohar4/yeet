# Security Test Report

**Project:** yeet — minimal file sharing server  
**Version:** 1.0.0  
**Date:** 2025-06-01  
**Tested by:** Automated test suite (`tests/test_security.py`)

---

## Introduction

This report summarises the results of yeet's automated security test suite. The tests were written to verify that common web application vulnerabilities are mitigated before the application is deployed to production at `yeet.majmohar.eu`.

The test suite does **not** use mocks. Every test runs against a live server instance, making real HTTP requests, to ensure that the security properties hold end-to-end — not just in unit isolation.

### What was tested

73 automated tests across 12 categories:

| Category | Tests | What it checks |
|---|---|---|
| Health & basics | 3 | Server starts, returns correct structure |
| Security headers | 10 | CSP, X-Frame-Options, HSTS, etc. |
| File upload (valid) | 6 | Correct responses, metadata, scan status |
| Upload limits | 4 | Size limit enforcement, missing files |
| Download (valid) | 5 | Content delivery, counters, download limits |
| Password protection | 8 | bcrypt auth, wrong passwords, no leakage |
| Input validation | 10 | Path traversal, XSS, SQLi, CRLF, Unicode |
| Rate limiting | 6 | 429 responses, Retry-After, per-IP isolation |
| Non-existent files | 4 | 404 handling, no path disclosure |
| Admin panel | 9 | Auth required, delete, logs, cleanup |
| MIME / content-type | 4 | JSON responses, nosniff, no HTML for .exe |
| Miscellaneous | 4 | Method not allowed, hex IDs, roundtrip |

### How to re-run the tests

```bash
# 1. Start the server
cp .env.example .env
# Set SECRET_KEY and ADMIN_PASSWORD in .env
docker compose up -d

# 2. Wait for ClamAV to initialise (first run only)
docker compose logs -f clamav  # wait for "clamd started"

# 3. Install test dependencies
pip install httpx pytest

# 4. Run all tests
pytest tests/test_security.py -v

# 5. Run against a remote instance
YEET_TEST_URL=https://yeet.example.com \
YEET_TEST_ADMIN_PASSWORD=yourpassword \
pytest tests/test_security.py -v
```

---

## Results summary

| Category | Tests | Status |
|---|---|---|
| Health & basics | 3 | ✅ Pass |
| Security headers | 10 | ✅ Pass |
| File upload (valid) | 6 | ✅ Pass |
| Upload limits | 4 | ✅ Pass |
| Download (valid) | 5 | ✅ Pass |
| Password protection | 8 | ✅ Pass |
| Input validation | 10 | ✅ Pass |
| Rate limiting | 6 | ✅ Pass |
| Non-existent files | 4 | ✅ Pass |
| Admin panel | 9 | ✅ Pass |
| MIME / content-type | 4 | ✅ Pass |
| Miscellaneous | 4 | ✅ Pass |
| **Total** | **73** | **✅ All pass** |

---

## Key findings

### Path traversal — MITIGATED
File IDs are validated as exactly 32 lowercase hex characters (`[0-9a-f]{32}`). Any request with a non-conforming ID returns 404 before hitting the database or filesystem. Files are stored by UUID only — the original filename is stored in the database but never used as a filesystem path.

### SQL injection — MITIGATED
All database queries use parameterised statements via `aiosqlite`. No string interpolation into SQL. Verified by sending `' OR '1'='1`, `UNION SELECT`, and `DROP TABLE` payloads — all return 404.

### XSS — MITIGATED
Jinja2 auto-escaping is enabled. Filenames are sanitised (non-ASCII stripped, dangerous characters removed) before storage. CSP header blocks inline scripts from external origins. Tested with `<script>alert(1)</script>` in filenames — not reflected in responses.

### Password protection — MITIGATED
Passwords are hashed with bcrypt (random salt, cost factor 12). The plaintext password is never stored or logged. Verified that incorrect passwords return 403 and that the correct password is not present in any response.

### Admin panel — MITIGATED
HTTP Basic Auth is required for all `/admin/*` endpoints. Unauthenticated requests return 401 with `WWW-Authenticate: Basic`. Verified that delete, logs, and cleanup endpoints all enforce authentication.

### Security headers — PASS
All 10 header tests pass:
- `Content-Security-Policy` with `frame-ancestors 'none'`
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- HSTS present on HTTPS responses

### Rate limiting — PASS
Uploads are limited to 10 per IP per hour, downloads to 100. Exceeded limits return 429 with `Retry-After: 3600`. Rate limit state is stored in SQLite and cleaned up on each check.

---

## Recommendations for operators

1. **Always use HTTPS.** Run behind nginx/Caddy with TLS. Never expose the app directly on port 80/443 without TLS termination.
2. **Set a strong ADMIN_PASSWORD.** The admin API has no brute-force lockout — rely on your reverse proxy or firewall to restrict access to `/admin`.
3. **Keep ClamAV updated.** The `clamav/clamav:stable` image auto-updates definitions. Pin to `stable`, not `latest`, for predictability.
4. **Monitor disk usage.** Files accumulate in `/data/archive/`. Set up an alert when the volume exceeds 80% of `STORAGE_LIMIT`.
5. **Review the audit log periodically.** Available at `/admin/logs` — look for unusual upload patterns or repeated failed password attempts.
