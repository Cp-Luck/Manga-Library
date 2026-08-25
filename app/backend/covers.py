"""
Cover image handling: where they're stored, how they get fetched from
Google Books, and how a manually uploaded one gets saved. No image
processing happens here — every cover, whether from Google or a manual
upload, is written to disk exactly as received. (An earlier version of this
app rectified/embedded photos taken with a phone camera; that whole pipeline
— OpenCV, CLIP, FAISS — was removed once auto-fetching from Google Books
made it unnecessary.)
"""
import uuid
from pathlib import Path
from typing import Optional

import httpx

from . import db

PROJECT_ROOT = Path(__file__).parent.parent.parent  # app/backend/covers.py -> project root
COVERS_DIR = PROJECT_ROOT / "covers"
COVERS_DIR.mkdir(exist_ok=True)


def cover_url(cover_image_path):
    """Turns a stored filesystem path into a URL under /covers, or None if
    this volume has no cover (Google Books had none for its ISBN)."""
    if not cover_image_path:
        return None
    return f"/covers/{Path(cover_image_path).name}"


def save_cover(volume_id: int, image_bytes: bytes) -> Optional[str]:
    """Saves a cover image (fetched from Google Books, or manually uploaded
    from the collection page) and links it to the volume. Returns the saved
    filesystem path on success, or None if there's nothing usable to save —
    callers use the path directly rather than re-querying the DB for it."""
    if not image_bytes:
        return None

    cover_filename = f"{uuid.uuid4().hex}.jpg"
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
