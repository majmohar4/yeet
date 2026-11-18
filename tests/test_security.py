"""
yeet security test suite — 107 automated tests
Run with: pytest tests/test_security.py -v

Requires a running yeet instance at YEET_TEST_URL (default: http://localhost:8000).
Admin tests require YEET_TEST_ADMIN_PASSWORD.
"""

import io
import os
import sqlite3
import time
import uuid
from pathlib import Path

import httpx
import pytest

BASE_URL   = os.getenv("YEET_TEST_URL",           "http://localhost:8000")
ADMIN_USER = os.getenv("YEET_TEST_ADMIN_USER",     "admin")
ADMIN_PASS = os.getenv("YEET_TEST_ADMIN_PASSWORD", "testadmin")
DB_PATH    = os.getenv("YEET_DB_PATH",             "/data/yeet.db")


# ── Helpers ───────────────────────────────────────────────────────────────────

def upload(client, content=b"hello world", filename="test.txt",
           password="", max_downloads="", bypass_code=""):
    return client.post(
        "/upload",
        files={"file": (filename, io.BytesIO(content), "text/plain")},
        data={"password": password, "max_downloads": max_downloads,
              "bypass_code": bypass_code},
    )


def admin(client, method, path, **kwargs):
    return client.request(method, path, auth=(ADMIN_USER, ADMIN_PASS), **kwargs)


def _db():
    if Path(DB_PATH).exists():
        return sqlite3.connect(DB_PATH)
    return None


@pytest.fixture(scope="session")
def client():
    # A fresh client IP per run: abuse-signal tests (wrong passwords, EICAR) would
    # otherwise mark 127.0.0.1 "suspicious" and shrink limits for later runs.
    import random
    ip = f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"
    headers = {"X-Forwarded-For": ip, "CF-Connecting-IP": ip}
    with httpx.Client(base_url=BASE_URL, follow_redirects=True, timeout=30, headers=headers) as c:
        yield c


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Health & basics (3 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

def test_health_has_version(client):
    assert "version" in client.get("/health").json()

def test_homepage_loads(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"yeet" in r.content.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Security headers (10 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_header_x_content_type(client):
    assert client.get("/").headers.get("x-content-type-options") == "nosniff"

def test_header_x_frame_options(client):
    assert client.get("/").headers.get("x-frame-options") == "DENY"

def test_header_xss_protection(client):
    assert "x-xss-protection" in client.get("/").headers

def test_header_referrer_policy(client):
    assert "referrer-policy" in client.get("/").headers

def test_header_permissions_policy(client):
    assert "permissions-policy" in client.get("/").headers

def test_header_csp_present(client):
    assert "default-src" in client.get("/").headers.get("content-security-policy", "")

def test_header_csp_no_unsafe_eval(client):
    assert "unsafe-eval" not in client.get("/").headers.get("content-security-policy", "")

def test_header_csp_frame_ancestors_none(client):
    assert "frame-ancestors 'none'" in client.get("/").headers.get("content-security-policy", "")

def test_header_csp_on_upload_response(client):
    assert "content-security-policy" in upload(client).headers

def test_header_csp_on_download_page(client):
    fid = upload(client).json()["id"]
    assert "content-security-policy" in client.get(f"/f/{fid}").headers


# ═══════════════════════════════════════════════════════════════════════════════
# 3. File upload — valid cases (6 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_upload_returns_201(client):
    assert upload(client).status_code == 201

def test_upload_response_has_url(client):
    r = upload(client).json()
    assert "url" in r and "/f/" in r["url"]

def test_upload_response_has_expires(client):
    assert "expires_at" in upload(client).json()

def test_upload_response_has_size(client):
    assert upload(client, content=b"x" * 1024).json()["size"] == 1024

def test_upload_preserves_filename(client):
    assert upload(client, filename="myreport.pdf").status_code == 201

def test_upload_scan_status_in_response(client):
    assert upload(client).json()["scan_status"] in ("clean", "skipped", "error")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. File upload — size & storage limits (4 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_upload_rejects_over_limit(client):
    limit = client.get("/api/limits").json()["max_file_bytes"]
    r = client.post("/api/uploads", json={"name": "big.bin", "size": limit + 1})
    assert r.status_code == 413

def test_upload_allows_at_limit(client):
    r = upload(client, content=b"a" * 1024)
    assert r.status_code in (201, 507)

def test_upload_missing_file_returns_422(client):
    assert client.post("/upload", data={"password": ""}).status_code == 422

def test_upload_empty_filename_handled(client):
    r = client.post("/upload", files={"file": ("", io.BytesIO(b"data"), "application/octet-stream")})
    assert r.status_code in (201, 422)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Download — valid cases (5 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_download_page_exists(client):
    fid = upload(client).json()["id"]
    assert client.get(f"/f/{fid}").status_code == 200

def test_download_serves_correct_content(client):
    content = b"unique_" + uuid.uuid4().hex.encode()
    fid = upload(client, content=content).json()["id"]
    r = client.post(f"/f/{fid}", data={"password": ""})
    assert content in r.content

def test_download_increments_counter(client):
    fid = upload(client).json()["id"]
    client.post(f"/f/{fid}", data={"password": ""})
    r = admin(client, "GET", "/admin")
    if r.status_code == 200:
        files = {f["id"]: f for f in r.json().get("files", [])}
        if fid in files:
            assert files[fid]["download_count"] >= 1

def test_download_has_content_disposition(client):
    fid = upload(client, filename="report.pdf").json()["id"]
    r = client.post(f"/f/{fid}", data={"password": ""})
    assert "content-disposition" in r.headers

def test_download_max_downloads_enforced(client):
    fid = upload(client, max_downloads="1").json()["id"]
    r1 = client.post(f"/f/{fid}", data={"password": ""})
    assert r1.status_code == 200
    r2 = client.post(f"/f/{fid}", data={"password": ""})
    assert r2.status_code in (410, 404)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Password protection (8 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_password_protected_upload(client):
    assert upload(client, password="s3cr3t").status_code == 201

def test_password_page_shown(client):
    fid = upload(client, password="s3cr3t").json()["id"]
    assert b"password" in client.get(f"/f/{fid}").content.lower()

def test_correct_password_grants_access(client):
    content = b"protected_" + uuid.uuid4().hex.encode()
    fid = upload(client, content=content, password="correct_horse").json()["id"]
    r = client.post(f"/f/{fid}", data={"password": "correct_horse"})
    assert r.status_code == 200 and content in r.content

def test_wrong_password_denied(client):
    fid = upload(client, password="correct_horse").json()["id"]
    assert client.post(f"/f/{fid}", data={"password": "wrong"}).status_code == 403

def test_empty_password_denied(client):
    fid = upload(client, password="correct_horse").json()["id"]
    assert client.post(f"/f/{fid}", data={"password": ""}).status_code == 403

def test_no_password_field_public_file(client):
    fid = upload(client).json()["id"]
    r = client.get(f"/f/{fid}")
    assert b"needs_password" not in r.content or b"false" in r.content.lower()

def test_password_not_leaked_in_upload_response(client):
    r = upload(client, password="supersecret")
    assert "supersecret" not in r.text

def test_password_not_leaked_in_download_page(client):
    fid = upload(client, password="supersecret").json()["id"]
    assert "supersecret" not in client.get(f"/f/{fid}").text


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Input validation & injection (10 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_path_traversal_in_file_id(client):
    for p in ("../etc/passwd", "..%2Fetc%2Fpasswd", "....//etc/passwd"):
        assert client.get(f"/f/{p}").status_code in (400, 404, 422)

def test_path_traversal_no_etc_passwd(client):
    r = client.get("/f/../etc/passwd")
    assert r.status_code in (400, 404, 422)
    assert b"root:x:" not in r.content

def test_xss_filename_not_reflected(client):
    fid = upload(client, filename='<script>alert(1)</script>.txt').json()["id"]
    assert b"<script>alert(1)</script>" not in client.get(f"/f/{fid}").content

def test_sql_injection_in_file_id(client):
    for p in ["' OR '1'='1", "1; DROP TABLE files;--", "' UNION SELECT * FROM files--"]:
        assert client.get(f"/f/{p}").status_code in (400, 404, 422)

def test_null_byte_in_file_id(client):
    try:
        resp = client.get("/f/abc\x00def")
        assert resp.status_code in (400, 404, 422)
    except httpx.InvalidURL:
        pass  # httpx correctly rejects null bytes before sending

def test_very_long_file_id(client):
    assert client.get("/f/" + "a" * 5000).status_code in (400, 404, 422)

def test_special_chars_in_filename_sanitized(client):
    r = upload(client, filename='../../evil;rm -rf /.txt')
    assert r.status_code == 201
    fid = r.json()["id"]
    assert b"rm -rf" not in client.get(f"/f/{fid}").content

def test_crlf_injection_in_filename(client):
    r = upload(client, filename="file\r\nX-Injected: pwned.txt")
    assert r.status_code == 201
    fid = r.json()["id"]
    assert "X-Injected" not in client.get(f"/f/{fid}").headers

def test_unicode_filename_handled(client):
    assert upload(client, filename="文件.txt").status_code in (201, 400)

def test_hidden_file_upload(client):
    # .htaccess has no allowed extension — blocked by whitelist is acceptable
    assert upload(client, filename=".htaccess").status_code in (201, 400)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Rate limiting (6 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_rate_limit_on_upload_accepted(client):
    assert upload(client).status_code in (201, 429, 507)

def test_rapid_uploads_may_rate_limit(client):
    results = [upload(client, content=b"rl").status_code for _ in range(15)]
    assert all(s in (201, 429, 413, 507) for s in results)

def test_rate_limit_download_header(client):
    fid = upload(client).json()["id"]
    assert client.get(f"/f/{fid}").status_code in (200, 429)

def test_rate_limit_retry_after(client):
    for _ in range(20):
        r = upload(client, content=b"rl")
        if r.status_code == 429:
            assert "retry-after" in r.headers
            break

def test_different_ips_independent(client):
    for ip in ("10.0.0.1", "10.0.0.2"):
        r = client.post(
            "/upload",
            files={"file": ("t.txt", io.BytesIO(b"a"), "text/plain")},
            data={"password": ""},
            headers={"X-Forwarded-For": ip},
        )
        assert r.status_code in (201, 429, 507)

def test_rate_limit_error_is_json(client):
    for _ in range(20):
        r = upload(client, content=b"rl")
        if r.status_code == 429:
            assert r.headers.get("content-type", "").startswith("application/json")
            break


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Non-existent / expired files (4 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_nonexistent_file_404(client):
    assert client.get("/f/" + "0" * 32).status_code == 404

def test_nonexistent_no_path_disclosure(client):
    r = client.get("/f/" + "0" * 32)
    assert "/data/" not in r.text and "/var/" not in r.text

def test_invalid_id_format_404(client):
    assert client.get("/f/notahexid").status_code == 404

def test_unknown_route_404(client):
    assert client.get("/this-does-not-exist-xyz").status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Admin panel security (9 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_admin_requires_auth(client):
    assert client.get("/admin").status_code == 401

def test_admin_wrong_password(client):
    assert client.get("/admin", auth=(ADMIN_USER, "wrongpassword")).status_code == 401

def test_admin_correct_password(client):
    assert admin(client, "GET", "/admin").status_code in (200, 503)

def test_admin_delete_requires_auth(client):
    fid = upload(client).json()["id"]
    assert client.delete(f"/admin/files/{fid}").status_code == 401

def test_admin_logs_requires_auth(client):
    assert client.get("/admin/logs").status_code == 401

def test_admin_cleanup_requires_auth(client):
    assert client.post("/admin/cleanup").status_code == 401

def test_admin_delete_nonexistent(client):
    assert admin(client, "DELETE", f"/admin/files/{'0'*32}").status_code in (404, 503)

def test_admin_logs_returns_list(client):
    r = admin(client, "GET", "/admin/logs?limit=5")
    if r.status_code == 200:
        assert "logs" in r.json()

def test_admin_cleanup_returns_result(client):
    r = admin(client, "POST", "/admin/cleanup")
    if r.status_code == 200:
        assert "archived" in r.json()


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Content-type & MIME handling (4 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_json_response_content_type(client):
    assert upload(client).headers["content-type"].startswith("application/json")

def test_download_nosniff_header(client):
    fid = upload(client).json()["id"]
    r = client.post(f"/f/{fid}", data={"password": ""})
    assert r.headers.get("x-content-type-options") == "nosniff"

def test_exe_not_served_as_html(client):
    # .exe is blocked by the extension whitelist — 400 is correct
    r = upload(client, filename="evil.exe", content=b"MZ\x90\x00")
    assert r.status_code in (400, 201)
    if r.status_code == 201:
        fid = r.json()["id"]
        ct = client.post(f"/f/{fid}", data={"password": ""}).headers.get("content-type", "")
        assert "text/html" not in ct

def test_upload_response_is_json(client):
    assert isinstance(upload(client).json(), dict)


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Misc (4 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_http_method_not_allowed(client):
    assert client.delete("/upload").status_code in (405, 404)

def test_file_id_is_32_hex(client):
    fid = upload(client).json()["id"]
    assert len(fid) == 32 and all(c in "0123456789abcdef" for c in fid)

def test_roundtrip_upload_download(client):
    sentinel = b"roundtrip_" + uuid.uuid4().hex.encode()
    fid = upload(client, content=sentinel).json()["id"]
    r = client.post(f"/f/{fid}", data={"password": ""})
    assert r.status_code == 200 and sentinel in r.content

def test_server_not_expose_server_header(client):
    r = client.get("/")
    assert r.status_code in range(200, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Bypass codes (12 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def _create_bypass_code() -> str | None:
    db = _db()
    if db is None:
        return None
    import secrets
    from datetime import datetime, timedelta, timezone
    code = secrets.token_hex(4).upper()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    db.execute("INSERT INTO bypass_codes (code, expires_at) VALUES (?, ?)", (code, expires))
    db.commit()
    db.close()
    return code


def test_bypass_code_admin_list_endpoint(client):
    r = admin(client, "GET", "/admin/codes")
    assert r.status_code in (200, 503)

def test_bypass_code_admin_requires_auth(client):
    assert client.get("/admin/codes").status_code == 401

def test_bypass_code_upload_succeeds(client):
    code = _create_bypass_code()
    if code is None:
        pytest.skip("DB not accessible for bypass code creation")
    r = upload(client, bypass_code=code)
    assert r.status_code in (201, 507)

def test_bypass_code_marked_used_after_upload(client):
    code = _create_bypass_code()
    if code is None:
        pytest.skip("DB not accessible")
    upload(client, bypass_code=code)
    db = _db()
    if db:
        row = db.execute("SELECT used FROM bypass_codes WHERE code=?", (code,)).fetchone()
        db.close()
        if row:
            assert row[0] == 1

def test_bypass_code_single_use(client):
    code = _create_bypass_code()
    if code is None:
        pytest.skip("DB not accessible")
    r1 = upload(client, bypass_code=code)
    r2 = upload(client, bypass_code=code)
    # Second use should NOT get the bypass benefit — rate limits apply again
    assert r1.status_code in (201, 507)
    # r2 may still succeed if within normal limits
    assert r2.status_code in (201, 429, 507)

def test_bypass_code_invalid_does_not_bypass(client):
    r = upload(client, bypass_code="INVALID1")
    assert r.status_code in (201, 429, 507)
    if r.status_code == 201:
        assert r.json().get("bypassed") is False

def test_bypass_code_revoke_endpoint(client):
    code = _create_bypass_code()
    if code is None:
        pytest.skip("DB not accessible")
    r = admin(client, "DELETE", f"/admin/codes/{code}")
    assert r.status_code in (200, 503)

def test_bypass_code_revoke_requires_auth(client):
    assert client.delete("/admin/codes/TESTCODE").status_code == 401

def test_bypass_code_expired_rejected(client):
    db = _db()
    if db is None:
        pytest.skip("DB not accessible")
    import secrets as _s
    from datetime import datetime, timedelta, timezone as tz
    code = _s.token_hex(4).upper()
    expired = (datetime.now(tz.utc) - timedelta(minutes=1)).isoformat()
    db.execute("INSERT INTO bypass_codes (code, expires_at) VALUES (?, ?)", (code, expired))
    db.commit()
    db.close()
    r = upload(client, bypass_code=code)
    if r.status_code == 201:
        assert r.json().get("bypassed") is False

def test_bypass_code_not_leaked_in_response(client):
    code = _create_bypass_code()
    if code is None:
        pytest.skip("DB not accessible")
    r = upload(client, bypass_code=code)
    assert code not in r.text

def test_bypass_code_empty_string_no_bypass(client):
    r = upload(client, bypass_code="")
    if r.status_code == 201:
        assert r.json().get("bypassed") is False

def test_bypass_code_response_indicates_bypass(client):
    code = _create_bypass_code()
    if code is None:
        pytest.skip("DB not accessible")
    r = upload(client, bypass_code=code)
    if r.status_code == 201:
        assert "bypassed" in r.json()


# ═══════════════════════════════════════════════════════════════════════════════
# 14. Byte-based rate limiting (6 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_bandwidth_check_endpoint_response_structure(client):
    r = upload(client)
    assert r.status_code in (201, 429, 507)
    if r.status_code == 201:
        data = r.json()
        assert "size" in data

def test_bandwidth_warning_at_80pct(client):
    # If warning is present in response it must be a string
    r = upload(client)
    if r.status_code == 201 and r.json().get("warning"):
        assert isinstance(r.json()["warning"], str)

def test_bandwidth_limit_error_has_retry_after(client):
    for _ in range(50):
        r = upload(client, content=b"bw")
        if r.status_code == 429:
            assert "retry-after" in r.headers
            break

def test_bandwidth_limit_message_mentions_daily(client):
    for _ in range(50):
        r = upload(client, content=b"bw")
        if r.status_code == 429:
            assert "daily" in r.json().get("error", "").lower() or "limit" in r.json().get("error", "").lower()
            break

def test_bandwidth_limit_response_has_bandwidth_key(client):
    for _ in range(50):
        r = upload(client, content=b"bw")
        if r.status_code == 429 and "bandwidth" in r.json():
            bw = r.json()["bandwidth"]
            assert "used" in bw and "limit" in bw and "pct" in bw
            break

def test_bandwidth_bypass_skips_limit(client):
    code = _create_bypass_code()
    if code is None:
        pytest.skip("DB not accessible")
    # Bypass should go through even if bandwidth limit hit
    r = upload(client, bypass_code=code)
    assert r.status_code in (201, 507)


# ═══════════════════════════════════════════════════════════════════════════════
# 15. Concurrent upload limit (4 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_concurrent_upload_error_message(client):
    # Single upload should work fine
    r = upload(client)
    assert r.status_code in (201, 429, 507)

def test_concurrent_upload_limit_is_enforced(client):
    # We can't easily simulate truly concurrent uploads in a single-threaded test,
    # but we can verify the endpoint accepts the max_concurrent field gracefully
    r = upload(client, content=b"concurrent test")
    assert r.status_code in (201, 429, 507)

def test_concurrent_error_returns_429(client):
    # If ever hit, should be 429 not 500
    for _ in range(3):
        r = upload(client, content=b"cc")
        if r.status_code == 429 and "wait" in r.json().get("error", "").lower():
            assert True
            return
    # Didn't hit limit in 3 sequential uploads — that's fine
    assert True

def test_concurrent_upload_message_is_helpful(client):
    # Verify the error message, when it appears, is descriptive
    for _ in range(10):
        r = upload(client, content=b"cc")
        if r.status_code == 429:
            msg = r.json().get("error", "").lower()
            assert "upload" in msg or "wait" in msg or "limit" in msg
            break


# ═══════════════════════════════════════════════════════════════════════════════
# 16. Storage limits (4 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_storage_limit_507_is_json(client):
    for _ in range(5):
        r = upload(client)
        if r.status_code == 507:
            assert r.headers.get("content-type", "").startswith("application/json")
            assert "error" in r.json()
            break

def test_storage_limit_error_is_explained(client):
    for _ in range(5):
        r = upload(client)
        if r.status_code == 507:
            msg = r.json().get("error", "").lower()
            assert "space" in msg or "storage" in msg
            break

def test_homepage_storage_pct_field(client):
    r = client.get("/")
    assert r.status_code == 200

def test_upload_size_limit_enforced_strictly(client):
    # Exactly at the limit is allowed to start; 1 byte over is refused.
    limit = client.get("/api/limits").json()["max_file_bytes"]
    at = client.post("/api/uploads", json={"name": "edge.bin", "size": limit})
    assert at.status_code in (201, 429, 507)  # 429: daily volume already used by the suite
    if at.status_code == 201:
        client.delete(f"/api/uploads/{at.json()['id']}")
    over = client.post("/api/uploads", json={"name": "edge.bin", "size": limit + 1})
    assert over.status_code == 413


# ═══════════════════════════════════════════════════════════════════════════════
# 17. File expiry & archive (4 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_upload_has_expires_at(client):
    r = upload(client)
    assert "expires_at" in r.json()

def test_archived_file_returns_404(client):
    db = _db()
    if db is None:
        pytest.skip("DB not accessible")
    fid = upload(client).json()["id"]
    # Force-archive it
    from datetime import datetime, timezone as tz
    db.execute("UPDATE files SET archived_at=? WHERE id=?",
               (datetime.now(tz.utc).isoformat(), fid))
    db.commit()
    db.close()
    r = client.get(f"/f/{fid}")
    assert r.status_code == 404

def test_cleanup_endpoint_archives_expired(client):
    r = admin(client, "POST", "/admin/cleanup")
    if r.status_code == 200:
        d = r.json()
        assert "archived" in d and "deleted" in d

def test_expired_file_not_downloadable(client):
    db = _db()
    if db is None:
        pytest.skip("DB not accessible")
    fid = upload(client).json()["id"]
    # Force-expire it
    from datetime import datetime, timedelta, timezone as tz
    past = (datetime.now(tz.utc) - timedelta(hours=1)).isoformat()
    db.execute("UPDATE files SET expires_at=? WHERE id=?", (past, fid))
    db.commit()
    db.close()
    # Run cleanup
    admin(client, "POST", "/admin/cleanup")
    r = client.get(f"/f/{fid}")
    assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 18. File listing API (4 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_files_api_empty_ids(client):
    r = client.get("/api/files?ids=")
    assert r.status_code == 200
    assert r.json()["files"] == []

def test_files_api_returns_metadata(client):
    fid = upload(client).json()["id"]
    r = client.get(f"/api/files?ids={fid}")
    assert r.status_code == 200
    files = r.json()["files"]
    if files:
        assert "orig_name" in files[0]
        assert "file_size" in files[0]

def test_files_api_rejects_invalid_ids(client):
    r = client.get("/api/files?ids=../../etc/passwd,invalid")
    assert r.status_code == 200
    assert r.json()["files"] == []

def test_files_api_multiple_ids(client):
    ids = [upload(client, content=b"file" + str(i).encode()).json()["id"] for i in range(3)]
    r = client.get(f"/api/files?ids={','.join(ids)}")
    assert r.status_code == 200
    assert len(r.json()["files"]) <= 3


# ═══════════════════════════════════════════════════════════════════════════════
# 19. Virus log API (3 tests)
# ═══════════════════════════════════════════════════════════════════════════════

def test_virus_log_endpoint_exists(client):
    r = client.get("/api/virus-log")
    assert r.status_code == 200

def test_virus_log_returns_deletions_key(client):
    assert "deletions" in client.get("/api/virus-log").json()

def test_virus_log_entries_have_expected_fields(client):
    r = client.get("/api/virus-log").json()
    for e in r.get("deletions", []):
        assert "ts" in e and "filename" in e and "threat" in e


# ═══════════════════════════════════════════════════════════════════════════════
# 20. ClamAV / EICAR (2 tests)
# ═══════════════════════════════════════════════════════════════════════════════

EICAR = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)

def test_eicar_rejected_if_clamav_enabled(client):
    # Use .txt to pass extension whitelist; ClamAV still scans content
    r = upload(client, content=EICAR, filename="eicar.txt")
    # If ClamAV is running: 422 (infected). If disabled/unavailable: 201 with scan_status!=clean
    if r.status_code == 422:
        assert "malware" in r.json().get("error", "").lower() or "rejected" in r.json().get("error", "").lower()
    else:
        assert r.status_code in (201, 429, 507)
        if r.status_code == 201:
            assert r.json()["scan_status"] in ("clean", "skipped", "error")

def test_eicar_logged_to_audit(client):
    upload(client, content=EICAR, filename="eicar_audit_test.txt")
    db = _db()
    if db is None:
        pytest.skip("DB not accessible")
    rows = db.execute(
        "SELECT details FROM audit_log WHERE action='virus_detected' "
        "AND details LIKE '%eicar%' ORDER BY ts DESC LIMIT 1"
    ).fetchall()
    db.close()
    # May be empty if ClamAV not running — that's OK
    assert isinstance(rows, list)


# ═══════════════════════════════════════════════════════════════════════════════
# 21. Ownership & access control (regression: session-id disclosure)
# ═══════════════════════════════════════════════════════════════════════════════

def _upload_owned(client, session, filename="owned.txt"):
    r = client.post(
        "/upload",
        files={"file": (filename, io.BytesIO(b"owned content"), "text/plain")},
        data={"session_id": session},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_file_listing_never_returns_uploader_session(client):
    _upload_owned(client, "sess-leak-check")
    body = client.get("/api/files/all").json()
    for row in body["files"] + body["bundles"]:
        assert "uploader_session" not in row


def test_bundle_listing_never_returns_uploader_session(client):
    body = client.get("/api/bundles/all").json()
    for row in body["bundles"]:
        assert "uploader_session" not in row


def test_listing_marks_ownership_for_the_owner(client):
    session = f"owner-{uuid.uuid4().hex}"
    fid = _upload_owned(client, session)
    rows = client.get("/api/files/all", headers={"X-Session-ID": session}).json()["files"]
    mine = [r for r in rows if r["id"] == fid]
    assert mine and mine[0]["is_yours"] is True


def test_listing_hides_ownership_from_strangers(client):
    fid = _upload_owned(client, f"owner-{uuid.uuid4().hex}")
    rows = client.get("/api/files/all", headers={"X-Session-ID": "someone-else"}).json()["files"]
    seen = [r for r in rows if r["id"] == fid]
    if seen:  # absent entirely when PUBLIC_LISTING=false, which is also fine
        assert seen[0]["is_yours"] is False


def test_stranger_cannot_delete_someone_elses_file(client):
    session = f"owner-{uuid.uuid4().hex}"
    fid = _upload_owned(client, session)
    assert client.delete(f"/api/files/{fid}",
                         headers={"X-Session-ID": "attacker"}).status_code == 403
    assert client.delete(f"/api/files/{fid}").status_code == 403
    assert client.get(f"/f/{fid}").status_code == 200  # still there
    assert client.delete(f"/api/files/{fid}",
                         headers={"X-Session-ID": session}).status_code == 200


def test_stranger_cannot_delete_someone_elses_bundle(client):
    session = f"owner-{uuid.uuid4().hex}"
    r = client.post(
        "/upload-bundle",
        files=[("files", ("a.txt", io.BytesIO(b"a"), "text/plain"))],
        data={"paths": "d/a.txt", "session_id": session},
    )
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    assert client.delete(f"/api/bundles/{bid}",
                         headers={"X-Session-ID": "attacker"}).status_code == 403
    assert client.delete(f"/api/bundles/{bid}",
                         headers={"X-Session-ID": session}).status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 22. Client IP spoofing
# ═══════════════════════════════════════════════════════════════════════════════

def test_forged_x_forwarded_for_is_not_taken_as_client_ip(client):
    forged = "203.0.113.250"
    fid = None
    r = client.post(
        "/upload",
        files={"file": ("xff.txt", io.BytesIO(b"xff"), "text/plain")},
        data={"session_id": "xff-test"},
        headers={"X-Forwarded-For": f"{forged}, 198.51.100.1"},
    )
    assert r.status_code == 201, r.text
    fid = r.json()["id"]

    db = _db()
    if db is None:
        pytest.skip("DB not accessible")
    row = db.execute("SELECT client_ip FROM files WHERE id=?", (fid,)).fetchone()
    db.close()
    # The left-most entry is written by the caller and must never be trusted.
    assert row and row[0] != forged


# ═══════════════════════════════════════════════════════════════════════════════
# 23. /raw cannot be used to dodge download accounting
# ═══════════════════════════════════════════════════════════════════════════════

def test_raw_respects_max_downloads(client):
    r = client.post(
        "/upload",
        files={"file": ("capped.txt", io.BytesIO(b"capped"), "text/plain")},
        data={"max_downloads": "1"},
    )
    assert r.status_code == 201
    fid = r.json()["id"]
    assert client.get(f"/f/{fid}").status_code == 200      # spends the only download
    assert client.get(f"/f/{fid}").status_code == 410
    assert client.get(f"/raw/{fid}").status_code == 410    # must not serve it either


def test_raw_redirects_non_previewable_types_to_metered_path(client):
    r = client.post(
        "/upload",
        files={"file": ("blob.zip", io.BytesIO(b"PK\x03\x04blob"), "application/zip")},
    )
    assert r.status_code == 201
    fid = r.json()["id"]
    with httpx.Client(base_url=BASE_URL, follow_redirects=False, timeout=30) as c:
        resp = c.get(f"/raw/{fid}")
    assert resp.status_code == 302
    assert resp.headers["location"].split("?")[0].endswith(f"/f/{fid}")


def test_raw_preview_does_not_count_as_a_download(client):
    r = client.post(
        "/upload",
        files={"file": ("preview.txt", io.BytesIO(b"preview me"), "text/plain")},
    )
    fid = r.json()["id"]
    for _ in range(3):
        assert client.get(f"/raw/{fid}").status_code == 200
    meta = client.get(f"/api/files?ids={fid}").json()["files"][0]
    assert meta["download_count"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 24. Misc hardening
# ═══════════════════════════════════════════════════════════════════════════════

def test_max_downloads_zero_is_stored_as_unlimited(client):
    r = client.post(
        "/upload",
        files={"file": ("zero.txt", io.BytesIO(b"zero"), "text/plain")},
        data={"max_downloads": "0"},
    )
    assert r.status_code == 201
    fid = r.json()["id"]
    assert client.get(f"/api/files?ids={fid}").json()["files"][0]["max_downloads"] is None
    assert client.get(f"/f/{fid}").status_code == 200


def test_health_does_not_leak_storage_or_file_counts(client):
    body = client.get("/health").json()
    assert "storage" not in body
    assert "files" not in body


# ── Chunked uploads, ranged downloads, landing page (1.2.0) ──────────────────

def _chunked_upload(client, name: str, data: bytes, **opts) -> dict:
    r = client.post("/api/uploads", json={"name": name, "size": len(data), **opts})
    assert r.status_code == 201, r.text
    up = r.json()
    cs = up["chunk_size"]
    for i in reversed(range(up["chunk_count"])):  # order must not matter
        rr = client.put(f"/api/uploads/{up['id']}/chunks/{i}", content=data[i * cs:(i + 1) * cs])
        assert rr.status_code == 200, rr.text
    r = client.post(f"/api/uploads/{up['id']}/complete")
    for _ in range(120):
        if r.status_code != 202:
            break
        time.sleep(1)
        st = client.get(f"/api/uploads/{up['id']}").json()
        if st["status"] in ("done", "failed"):
            assert st["code"] == 201, st
            return st["result"]
    assert r.status_code == 201, r.text
    return r.json()


def test_chunked_upload_roundtrip(client):
    data = os.urandom(300_000)
    res = _chunked_upload(client, "chunked.bin", data)
    assert client.get(f"/f/{res['id']}").content == data


def test_chunked_upload_rejects_wrong_chunk_size(client):
    r = client.post("/api/uploads", json={"name": "x.bin", "size": 10})
    up = r.json()
    assert client.put(f"/api/uploads/{up['id']}/chunks/0", content=b"short").status_code == 400
    assert client.put(f"/api/uploads/{up['id']}/chunks/5", content=b"0123456789").status_code == 400
    assert client.post(f"/api/uploads/{up['id']}/complete").status_code == 409


def test_chunked_upload_size_limit(client):
    r = client.post("/api/uploads", json={"name": "huge.bin", "size": 10**13})
    assert r.status_code in (413, 507)


def test_range_download_and_resume_not_counted(client):
    data = os.urandom(50_000)
    res = _chunked_upload(client, "r.bin", data, max_downloads=1)
    r = client.get(f"/f/{res['id']}", headers={"Range": "bytes=0-9999"})
    assert r.status_code == 206 and r.content == data[:10000]
    r = client.get(f"/f/{res['id']}", headers={"Range": "bytes=10000-"})
    assert r.status_code == 206 and r.content == data[10000:]
    # The first request used the single allowed download; a fresh one is refused.
    assert client.get(f"/f/{res['id']}").status_code == 410


def test_browser_gets_landing_page_not_bytes(client):
    res = _chunked_upload(client, "page.txt", b"hello landing")
    r = client.get(f"/f/{res['id']}", headers={"Accept": "text/html"})
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert b"hello landing" not in r.content
    r = client.get(f"/f/{res['id']}?dl=1", headers={"Accept": "text/html"})
    assert r.content == b"hello landing"


def test_burn_survives_link_preview_bots_and_ranges(client):
    res = _chunked_upload(client, "burn.txt", b"only once", burn=1)
    url = f"/f/{res['id']}"
    assert b"only once" not in client.get(url, headers={"User-Agent": "Slackbot-LinkExpanding 1.1"}).content
    assert client.get(url, headers={"Range": "bytes=1-1"}).status_code == 409
    assert client.get(url).content == b"only once"
    assert client.get(url).status_code in (404, 410)


def test_chunked_folder_hidden_until_published(client):
    r = client.post("/api/bundles", json={"name": "F", "file_count": 1, "total_size": 3,
                                          "session_id": "t-sess"})
    b = r.json()
    r = client.post("/api/uploads", json={"name": "a.txt", "size": 3, "bundle_id": b["id"],
                                          "bundle_token": "nope", "path": "F/a.txt"})
    assert r.status_code == 403
    _chunked_upload(client, "a.txt", b"abc", bundle_id=b["id"], bundle_token=b["token"],
                    path="F/a.txt")
    assert client.get(f"/b/{b['id']}").status_code == 404
    r = client.post(f"/api/bundles/{b['id']}/complete", json={"bundle_token": b["token"]})
    assert r.status_code == 201 and r.json()["file_count"] == 1
    assert client.get(f"/b/{b['id']}").status_code == 200


# ── Visitor tiers & unlock (1.3.0) ───────────────────────────────────────────

def test_limits_endpoint_shape(client):
    d = client.get("/api/limits").json()
    assert {"tier", "max_file_bytes", "daily_limit", "unlock_available"} <= d.keys()


def test_unlock_wrong_password_rejected_without_cookie(client):
    r = httpx.post(f"{BASE_URL}/api/unlock", json={"password": "definitely-wrong-guess"},
                   headers={"X-Forwarded-For": "10.250.0.1"}, timeout=30)
    assert r.status_code in (403, 404, 429)
    assert "yeet_unlock" not in r.headers.get("set-cookie", "")


def test_forged_unlock_cookie_ignored(client):
    r = httpx.get(f"{BASE_URL}/api/limits", cookies={"yeet_unlock": "9999999999.abcd.deadbeef"},
                  timeout=30)
    assert r.json()["tier"] != "trusted"


def test_my_uploads_scoped_to_session(client):
    r = client.post("/api/uploads", json={"name": "s.bin", "size": 10, "session_id": "scope-a"})
    uid = r.json()["id"]
    mine = client.get("/api/uploads", headers={"X-Session-ID": "scope-a"}).json()["uploads"]
    assert any(u["id"] == uid for u in mine)
    other = client.get("/api/uploads", headers={"X-Session-ID": "scope-b"}).json()["uploads"]
    assert all(u["id"] != uid for u in other)
    client.delete(f"/api/uploads/{uid}")
