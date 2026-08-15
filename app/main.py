"""
Manga collection tracker — FastAPI backend.

Routes:
  POST   /scan/isbn          — barcode was scanned client-side, look up metadata
  DELETE /volumes/{id}       — remove a volume
  PATCH  /volumes/{id}       — manually correct a volume's number or series
  POST   /volumes/{id}/cover — manually attach a cover image
  GET    /library            — full collection, grouped by series
  GET    /series/{id}        — one series and its volumes
  GET    /collection         — cover-art grid browser page
"""
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db

PROJECT_ROOT = Path(__file__).parent.parent

load_dotenv(PROJECT_ROOT / ".env")  # never commit that file

# Anonymous Google Books API quota is effectively 0 — get a free key at
# https://console.cloud.google.com (enable "Books API", create an API key)
# and put it in .env as GOOGLE_BOOKS_API_KEY=... Lookups still work without
# one, but will hit 429s.
GOOGLE_BOOKS_API_KEY = os.environ.get("GOOGLE_BOOKS_API_KEY")

app = FastAPI(title="Manga Collection Tracker")

# Allow the frontend (served from a different origin during dev) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this to your actual frontend origin before shipping
    allow_methods=["*"],
    allow_headers=["*"],
)

COVERS_DIR = PROJECT_ROOT / "covers"
COVERS_DIR.mkdir(exist_ok=True)

# Cover images are saved to disk with a filesystem path (see COVERS_DIR
# above), but the DB rows carry that raw path — meaningless to a browser.
# Serve the same directory over HTTP so the frontend can request them.
app.mount("/covers", StaticFiles(directory=str(COVERS_DIR)), name="covers")


def _cover_url(cover_image_path):
    """Turns a stored filesystem path into a URL under /covers, or None if
    this volume has no cover (Google Books had none for its ISBN)."""
    if not cover_image_path:
        return None
    return f"/covers/{Path(cover_image_path).name}"


SCAN_LOG_PATH = PROJECT_ROOT / "scan_log.jsonl"


def log_scan_event(event: dict):
    """Append-only record of every scan, one JSON object per line, in the
    order it happened — a plain-text history alongside the SQLite state."""
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
    with open(SCAN_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


@app.on_event("startup")
def startup():
    db.init_db()


@app.get("/")
def scanner_page():
    """Serves the scanner UI itself, so the same host:port works for both
    the page and the API it calls — no separate static server needed."""
    return FileResponse(Path(__file__).parent / "scanner.html")


# --- Request/response models ---

class IsbnScanRequest(BaseModel):
    isbn: str


class VolumeUpdateRequest(BaseModel):
    volume_number: Optional[int] = None
    series_title: Optional[str] = None


class VolumeResponse(BaseModel):
    id: int
    series_title: str
    volume_number: Optional[int]
    isbn: Optional[str]
    already_owned: bool
    has_cover: bool = False


def _save_cover(volume_id: int, image_bytes: bytes) -> bool:
    """Saves a cover image fetched from Google Books and links it to the
    volume. It's already a clean, valid JPEG straight from Google — no
    rectification or re-encoding needed, just write the bytes as-is.
    Returns False if there's nothing usable to save."""
    if not image_bytes:
        return False

    cover_filename = f"{uuid.uuid4().hex}.jpg"
    cover_path = COVERS_DIR / cover_filename
    cover_path.write_bytes(image_bytes)

    with db.get_connection() as conn:
        conn.execute(
            "UPDATE volumes SET cover_image_path = ? WHERE id = ?",
            (str(cover_path), volume_id),
        )
    return True


async def _try_fetch_cover_from_google(volume_id: int, image_links: dict) -> bool:
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
        return _save_cover(volume_id, resp.content)
    except httpx.HTTPError:
        return False


# --- Routes ---

@app.post("/scan/isbn", response_model=VolumeResponse)
async def scan_isbn(payload: IsbnScanRequest):
    """Called right after the client-side barcode scanner decodes an ISBN.
    Checks if we already own it; if not, looks up metadata and creates it."""
    isbn = payload.isbn.strip()

    existing = db.get_volume_by_isbn(isbn)
    if existing:
        log_scan_event({
            "event": "scan_isbn",
            "isbn": isbn,
            "volume_id": existing["id"],
            "series_title": _series_title(existing["series_id"]),
            "volume_number": existing["volume_number"],
            "already_owned": True,
        })
        return VolumeResponse(
            id=existing["id"],
            series_title=_series_title(existing["series_id"]),
            volume_number=existing["volume_number"],
            isbn=existing["isbn"],
            already_owned=True,
            has_cover=bool(existing["cover_image_path"]),
        )

    # Not in our collection yet — look up metadata from Google Books
    params = {"q": f"isbn:{isbn}"}
    if GOOGLE_BOOKS_API_KEY:
        params["key"] = GOOGLE_BOOKS_API_KEY

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.googleapis.com/books/v1/volumes",
            params=params,
        )
    data = resp.json()

    if not resp.is_success:
        message = data.get("error", {}).get("message", "Google Books API request failed")
        raise HTTPException(
            status_code=502,
            detail=f"Google Books lookup failed ({resp.status_code}): {message}. Try again shortly.",
        )

    if not data.get("items"):
        raise HTTPException(
            status_code=404,
            detail="ISBN not found in Google Books. Add this volume manually.",
        )

    info = data["items"][0]["volumeInfo"]
    title = info.get("title", "Unknown title")
    author = ", ".join(info.get("authors", [])) or None
    series_title, volume_number = _parse_title(title)

    series_id = db.get_or_create_series(title=series_title, author=author)
    volume_id = db.add_volume(series_id, volume_number=volume_number, isbn=isbn)

    has_cover = await _try_fetch_cover_from_google(volume_id, info.get("imageLinks", {}))

    log_scan_event({
        "event": "scan_isbn",
        "isbn": isbn,
        "volume_id": volume_id,
        "series_title": series_title,
        "volume_number": volume_number,
        "already_owned": False,
        "has_cover": has_cover,
    })

    return VolumeResponse(
        id=volume_id,
        series_title=series_title,
        volume_number=volume_number,
        isbn=isbn,
        already_owned=False,
        has_cover=has_cover,
    )


@app.delete("/volumes/{volume_id}")
def delete_volume(volume_id: int):
    """Removes a volume outright. Two callers today: the scanner's "Wrong
    manga" button (undoing a volume that same scan just created — a barcode
    misread pointing at the wrong ISBN) and the collection page (clicking a
    cover to deliberately remove it from the library, e.g. a lost/sold
    book). Both cases delete unconditionally by id; there's no "was this
    just created" check here, that distinction only matters to the caller.

    Also cleans up the cover file on disk, if any.
    """
    volume = db.get_volume_by_id(volume_id)
    if volume and volume["cover_image_path"]:
        Path(volume["cover_image_path"]).unlink(missing_ok=True)
    db.delete_volume(volume_id)
    return {"deleted": volume_id}


@app.patch("/volumes/{volume_id}")
def update_volume(volume_id: int, payload: VolumeUpdateRequest):
    """Manual correction from the collection page — Google Books titles
    don't always carry a parseable volume number (see _parse_title), and
    inconsistent/incomplete titles across ISBNs of the same real series can
    land a volume under the wrong one (or its own stray one-volume series).

    Only touches fields actually present in the request body (exclude_unset)
    — a series-only edit must not silently wipe volume_number back to null,
    and vice versa.
    """
    volume = db.get_volume_by_id(volume_id)
    if volume is None:
        raise HTTPException(status_code=404, detail="No such volume")

    fields_set = payload.model_dump(exclude_unset=True)

    if "volume_number" in fields_set:
        db.set_volume_number(volume_id, payload.volume_number)

    if fields_set.get("series_title"):
        new_title = payload.series_title.strip()
        if new_title:
            old_series_id = volume["series_id"]
            new_series_id = db.get_or_create_series(title=new_title)
            if new_series_id != old_series_id:
                db.move_volume_to_series(volume_id, new_series_id)
                if db.count_volumes_in_series(old_series_id) == 0:
                    db.delete_series(old_series_id)

    updated = db.get_volume_by_id(volume_id)
    return {
        "id": volume_id,
        "volume_number": updated["volume_number"],
        "series_title": updated["series_title"],
    }


@app.post("/volumes/{volume_id}/cover")
async def upload_cover(volume_id: int, file: UploadFile = File(...)):
    """Manual cover upload from the collection page — for when Google Books
    had no cover art for that ISBN. Saves whatever image is given as-is, no
    rectification; unlike the old camera-based flow this is just a straight
    file picker, so there's no perspective to correct in the first place."""
    if db.get_volume_by_id(volume_id) is None:
        raise HTTPException(status_code=404, detail="No such volume")
    image_bytes = await file.read()
    if not _save_cover(volume_id, image_bytes):
        raise HTTPException(status_code=400, detail="Could not read that image")
    return {"volume_id": volume_id, "cover_url": _cover_url(db.get_volume_by_id(volume_id)["cover_image_path"])}


@app.get("/library")
def get_library():
    rows = [dict(row) for row in db.get_full_library()]
    for row in rows:
        row["cover_url"] = _cover_url(row["cover_image_path"])
    return rows


@app.get("/series")
def list_series():
    rows = db.list_series()
    return [dict(row) for row in rows]


@app.get("/series/{series_id}/volumes")
def get_series_volumes(series_id: int):
    rows = [dict(row) for row in db.get_volumes_for_series(series_id)]
    for row in rows:
        row["cover_url"] = _cover_url(row["cover_image_path"])
    return rows


@app.get("/collection")
def collection_page():
    """Browsable grid view of the library — the other half of the frontend,
    alongside the scanner at '/'."""
    return FileResponse(Path(__file__).parent / "collection.html")


# --- helpers ---

def _series_title(series_id: int) -> str:
    with db.get_connection() as conn:
        row = conn.execute("SELECT title FROM series WHERE id = ?", (series_id,)).fetchone()
        return row["title"] if row else "Unknown"


# Google Books titles carry the volume number in several different shapes
# depending on publisher/edition — 'Chainsaw Man, Vol. 4', 'Apothecary
# Diaries 01 (Manga)', 'Apothecary Diaries. 2' — not always with the word
# "Vol"/"Volume" at all. Match a trailing number, with an optional
# "Vol./Volume" keyword before it and an optional parenthetical (like
# "(Manga)") after it, and treat everything before that as the series name.
_VOLUME_SUFFIX_RE = re.compile(
    r"[,.]?\s*(?:vol\.?|volume)?\s*(\d+)\s*(?:\([^)]*\))?\s*$",
    re.IGNORECASE,
)


def _parse_title(title: str) -> tuple[str, Optional[int]]:
    """Splits a Google Books title into (series_title, volume_number).
    Falls back to (title, None) when nothing matches — e.g. a one-shot with
    no volume number at all. Not foolproof; user can correct."""
    match = _VOLUME_SUFFIX_RE.search(title)
    if not match:
        return title.strip(), None
    series_title = title[:match.start()].strip().rstrip(",.").strip()
    return (series_title or title.strip()), int(match.group(1))
