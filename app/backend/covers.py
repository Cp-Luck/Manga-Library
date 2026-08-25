"""
Cover image handling: where they're stored, how they get fetched from
Google Books, and how a manually uploaded one gets saved. The original
bytes are written to disk unchanged (no re-encoding/resizing) — the only
processing is validating that what came in is actually a decodable image
of a format we're willing to serve, and picking the file extension to
match. (An earlier version of this app rectified/embedded photos taken
with a phone camera; that whole pipeline — OpenCV, CLIP, FAISS — was
removed once auto-fetching from Google Books made it unnecessary.)
"""

import io
import uuid
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError

from . import db

PROJECT_ROOT = Path(
    __file__
).parent.parent.parent  # app/backend/covers.py -> project root
COVERS_DIR = PROJECT_ROOT / "covers"
COVERS_DIR.mkdir(exist_ok=True)

# Generous for a book cover (real ones are well under 1MB) while still
# bounding the worst case for a manual upload or a misbehaving remote host.
MAX_COVER_UPLOAD_BYTES = 10 * 1024 * 1024

# Formats we're willing to serve, mapped to the extension saved on disk.
# Deliberately not "whatever Pillow can decode" — e.g. BMP/TIFF are real
# images Pillow would happily verify, but aren't formats we want turning up
# as a manga cover.
ALLOWED_COVER_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}


def cover_url(cover_image_path):
    """Turns a stored filesystem path into a URL under /covers, or None if
    this volume has no cover (Google Books had none for its ISBN)."""
    if not cover_image_path:
        return None
    return f"/covers/{Path(cover_image_path).name}"


def _detect_cover_extension(image_bytes: bytes) -> str | None:
    """Returns the file extension for image_bytes' real format, or None if
    it's oversized, not a genuine image, or a format we don't serve. Never
    trusts a filename or a client-supplied Content-Type — both are just
    labels the uploader chose, not a guarantee about what the bytes are."""
    if not image_bytes or len(image_bytes) > MAX_COVER_UPLOAD_BYTES:
        return None

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img.verify()  # parses structure without fully decoding pixels
        # Pillow's docs say the file object shouldn't be reused after
        # verify() — re-open to read the now-trusted format.
        with Image.open(io.BytesIO(image_bytes)) as img:
            image_format = img.format
    except (UnidentifiedImageError, OSError):
        return None

    return ALLOWED_COVER_FORMATS.get(image_format)


def save_cover(volume_id: int, image_bytes: bytes) -> str | None:
    """Validates and saves a cover image (fetched from Google Books, or
    manually uploaded from the collection page) and links it to the volume.
    Returns the saved filesystem path on success, or None if image_bytes
    isn't a genuine, supported, size-bounded image — callers use the path
    directly rather than re-querying the DB for it."""
    extension = _detect_cover_extension(image_bytes)
    if extension is None:
        return None

    cover_filename = f"{uuid.uuid4().hex}{extension}"
    cover_path = COVERS_DIR / cover_filename
    cover_path.write_bytes(image_bytes)

    with db.get_connection() as conn:
        conn.execute(
            "UPDATE volumes SET cover_image_path = ? WHERE id = ?",
            (str(cover_path), volume_id),
        )
    return str(cover_path)


async def try_fetch_cover_from_google(volume_id: int, image_links: dict) -> bool:
    """Best-effort — downloads and attaches the Google Books cover image if
    one was returned alongside the metadata. A failed download shouldn't
    break the scan itself; some ISBNs just don't have cover art on Google
    Books, and that's fine — the volume is still added, just without one."""
    url = image_links.get("thumbnail") or image_links.get("smallThumbnail")
    if not url:
        return False

    url = url.replace("http://", "https://")  # Google sometimes returns http://
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
        if not resp.is_success:
            return False
        return save_cover(volume_id, resp.content) is not None
    except httpx.HTTPError:
        return False
