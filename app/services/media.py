"""
Light check for photos and videos.

Scanning a multi-GB video with ClamAV takes minutes and finds nothing: media
files are never executed, and yeet serves them with `nosniff` and their real
media type. What *does* matter is that a file called `.jpg` really is an image
— a renamed `.exe` must still go through the full scan. So for image/video
extensions we read the container signature, and only when the bytes match a
known image/video format of the same kind is ClamAV skipped.
"""
from pathlib import Path

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".apng", ".gif", ".webp", ".bmp",
    ".heic", ".heif", ".avif", ".tif", ".tiff", ".dng", ".cr2", ".cr3", ".nef",
    ".arw", ".orf", ".rw2", ".raf", ".jxl",
}
VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".qt", ".3gp", ".3g2", ".mkv", ".webm", ".avi",
    ".mts", ".m2ts", ".ts", ".mpg", ".mpeg", ".flv", ".wmv", ".asf",
}

# ISO base media (MP4/MOV/HEIC/AVIF/CR3/3GP): bytes 4..8 are "ftyp" + brand.
_IMAGE_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1",
                 b"avif", b"avis", b"crx "}


def _ftyp_brand(head: bytes) -> bytes | None:
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return head[8:12]
    return None


def _is_image(head: bytes) -> bool:
    return (
        head.startswith(b"\xff\xd8\xff")                              # JPEG
        or head.startswith(b"\x89PNG\r\n\x1a\n")                      # PNG / APNG
        or head[:6] in (b"GIF87a", b"GIF89a")                         # GIF
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")            # WebP
        or head.startswith(b"BM")                                     # BMP
        or head[:4] in (b"II*\x00", b"MM\x00*")                       # TIFF + most RAW
        or head.startswith(b"IIRO") or head.startswith(b"IIU\x00")    # ORF / RW2
        or head.startswith(b"FUJIFILMCCD-RAW")                        # RAF
        or head.startswith(b"\xff\x0a")                               # JPEG XL codestream
        or head.startswith(b"\x00\x00\x00\x0cJXL \r\n\x87\n")         # JPEG XL container
        or _ftyp_brand(head) in _IMAGE_BRANDS                         # HEIC / AVIF / CR3
    )


def _is_video(head: bytes) -> bool:
    brand = _ftyp_brand(head)
    return (
        (brand is not None and brand not in _IMAGE_BRANDS)           # MP4 / MOV / 3GP / M4V
        or head[4:8] in (b"moov", b"mdat", b"wide", b"free", b"skip") # old QuickTime
        or head.startswith(b"\x1a\x45\xdf\xa3")                       # Matroska / WebM
        or (head[:4] == b"RIFF" and head[8:12] == b"AVI ")            # AVI
        or head.startswith(b"\x00\x00\x01\xba")                       # MPEG program stream
        or (len(head) > 188 and head[0] == 0x47 and head[188] == 0x47)          # MPEG-TS
        or (len(head) > 196 and head[4] == 0x47 and head[196] == 0x47)          # M2TS (Blu-ray / AVCHD)
        or head.startswith(b"FLV\x01")                                # Flash video
        or head.startswith(b"\x30\x26\xb2\x75\x8e\x66\xcf\x11")       # ASF / WMV
    )


def verify(path: Path, name: str) -> str | None:
    """'image' or 'video' when the extension AND the bytes agree, else None."""
    ext = Path(name or "").suffix.lower()
    if ext not in IMAGE_EXTS and ext not in VIDEO_EXTS:
        return None
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return None
    if ext in IMAGE_EXTS and _is_image(head):
        return "image"
    if ext in VIDEO_EXTS and _is_video(head):
        return "video"
    return None
