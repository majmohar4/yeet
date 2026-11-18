import os
import secrets
from pathlib import Path


class Settings:
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")

    MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", 104857600))  # 100MB
    STORAGE_LIMIT: int = int(os.getenv("STORAGE_LIMIT", 10737418240))  # 10GB
    FILE_EXPIRY_HOURS: int = int(os.getenv("FILE_EXPIRY_HOURS", 24))
    MAX_FILENAME_LENGTH: int = 255

    BASE_DIR: Path = Path(os.getenv("BASE_DIR", "/data"))
    UPLOAD_DIR: Path = Path(os.getenv("UPLOAD_DIR", "/data/uploads"))
    ARCHIVE_DIR: Path = Path(os.getenv("ARCHIVE_DIR", "/data/archive"))
    DB_PATH: Path = Path(os.getenv("DB_PATH", "/data/yeet.db"))

    RATE_LIMIT_UPLOADS: int = int(os.getenv("RATE_LIMIT_UPLOADS", 10))
    RATE_LIMIT_DOWNLOADS: int = int(os.getenv("RATE_LIMIT_DOWNLOADS", 100))
    # Inline previews (/raw) are not metered as downloads — thumbnails would
    # burn the download budget on every grid render — but they still need a
    # (much higher) ceiling so they cannot be used for unmetered bandwidth.
    RATE_LIMIT_PREVIEWS: int = int(os.getenv("RATE_LIMIT_PREVIEWS", 600))

    # ── Client IP resolution ─────────────────────────────────────────────
    # Never trust the left-most X-Forwarded-For entry: the caller writes it.
    # REAL_IP_HEADER is a header our own proxy always overwrites
    # (cloudflared: CF-Connecting-IP). TRUSTED_PROXY_HOPS is how many
    # proxies we run, counted from the right of X-Forwarded-For.
    # Set REAL_IP_HEADER="" and TRUSTED_PROXY_HOPS=0 if the app is exposed
    # directly — otherwise anyone can forge their identity.
    REAL_IP_HEADER: str = os.getenv("REAL_IP_HEADER", "CF-Connecting-IP").strip()
    TRUSTED_PROXY_HOPS: int = int(os.getenv("TRUSTED_PROXY_HOPS", 1))

    # ── Admin panel brute-force protection ───────────────────────────────
    ADMIN_MAX_ATTEMPTS: int = int(os.getenv("ADMIN_MAX_ATTEMPTS", 5))
    ADMIN_LOCKOUT_SECONDS: int = int(os.getenv("ADMIN_LOCKOUT_SECONDS", 900))

    # Expose the full file/bundle listing at /api/files/all to everyone.
    # When false, callers only see their own uploads (by X-Session-ID).
    PUBLIC_LISTING: bool = os.getenv("PUBLIC_LISTING", "true").lower() == "true"

    CLAMAV_ENABLED: bool = os.getenv("CLAMAV_ENABLED", "true").lower() == "true"
    CLAMAV_HOST: str = os.getenv("CLAMAV_HOST", "127.0.0.1")
    CLAMAV_PORT: int = int(os.getenv("CLAMAV_PORT", 3310))
    # Reject uploads when the scanner is unreachable or times out. Fail-open
    # means a clamd outage silently turns the scanner off.
    CLAMAV_FAIL_CLOSED: bool = os.getenv("CLAMAV_FAIL_CLOSED", "true").lower() == "true"
    # clamd's signatures take ~1 GB of RAM. >0: start clamd on the first scan and stop
    # it after this many idle minutes (first scan after idle waits ~1 min for the
    # signatures to load). 0: keep clamd resident.
    CLAMAV_IDLE_MINUTES: int = int(os.getenv("CLAMAV_IDLE_MINUTES", 15))
    CLAMAV_START_TIMEOUT: int = int(os.getenv("CLAMAV_START_TIMEOUT", 180))
    # How often to run freshclam (0 = only at container start). clamd is stopped
    # for the update, so an always-on clamd is briefly unavailable.
    CLAMAV_UPDATE_HOURS: float = float(os.getenv("CLAMAV_UPDATE_HOURS", 6))

    # ── Uploads ──────────────────────────────────────────────────────────
    # Browser and CLI uploads are split into chunks of this size so no single
    # request comes near Cloudflare's 100 MB request-body cap. Must stay well
    # under whatever limit your reverse proxy enforces.
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", 16 * 1_048_576))
    PARTIAL_DIR: Path = Path(
        os.getenv("PARTIAL_DIR", os.path.join(os.getenv("BASE_DIR", "/data"), "partial"))
    )
    # Refuse uploads that would leave less than this much free disk. Space for
    # an accepted upload is reserved up front, so a full disk produces a clean
    # "server is full" answer instead of a crash halfway through.
    MIN_FREE_DISK_MB: int = int(os.getenv("MIN_FREE_DISK_MB", 2048))
    # Unfinished chunked uploads one IP may hold open at once.
    MAX_ACTIVE_UPLOADS: int = int(os.getenv("MAX_ACTIVE_UPLOADS", 6))
    # Abandoned partial uploads are purged after this many hours of inactivity.
    PARTIAL_TTL_HOURS: int = int(os.getenv("PARTIAL_TTL_HOURS", 6))

    # Shown in the footer, error messages and the privacy notice. Empty hides it.
    CONTACT_EMAIL: str = os.getenv("CONTACT_EMAIL", "").strip()

    APP_NAME: str = "yeet"
    APP_VERSION: str = "1.2.0"
    APP_URL: str = os.getenv("APP_URL", "").strip()

    def validate(self) -> None:
        if not self.SECRET_KEY:
            raise RuntimeError(
                "SECRET_KEY is not set. Generate one with: openssl rand -hex 32"
            )
        if len(self.SECRET_KEY) < 32:
            raise RuntimeError("SECRET_KEY must be at least 32 characters.")


settings = Settings()
