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

    CLAMAV_ENABLED: bool = os.getenv("CLAMAV_ENABLED", "true").lower() == "true"
    CLAMAV_HOST: str = os.getenv("CLAMAV_HOST", "127.0.0.1")
    CLAMAV_PORT: int = int(os.getenv("CLAMAV_PORT", 3310))

    APP_NAME: str = "yeet"
    APP_VERSION: str = "1.0.0"
    APP_URL: str = os.getenv("APP_URL", "https://yeet.majmohar.eu")

    def validate(self) -> None:
        if not self.SECRET_KEY:
            raise RuntimeError(
                "SECRET_KEY is not set. Generate one with: openssl rand -hex 32"
            )
        if len(self.SECRET_KEY) < 32:
            raise RuntimeError("SECRET_KEY must be at least 32 characters.")


settings = Settings()
