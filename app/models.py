from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class FileRecord(BaseModel):
    id: str
    filename: str
    orig_name: str
    file_hash: str
    file_size: int
    mime_type: Optional[str]
    has_password: bool
    created_at: datetime
    expires_at: datetime
    download_count: int
    max_downloads: Optional[int]
    archived_at: Optional[datetime]
    client_ip: Optional[str]
    scan_status: str


class UploadResponse(BaseModel):
    id: str
    url: str
    expires_at: datetime
    size: int
    scan_status: str


class DownloadRequest(BaseModel):
    password: Optional[str] = None
