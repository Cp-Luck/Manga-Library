"""
Manga collection tracker — FastAPI backend.

Routes:
  POST   /scan/isbn          — barcode was scanned client-side, look up metadata
  POST   /volumes            — add a volume by hand, no barcode/lookup involved
  DELETE /volumes/{id}       — remove a volume
  PATCH  /volumes/{id}       — manually correct a volume's number or series
  POST   /volumes/{id}/cover — manually attach a cover image
  GET    /library            — full collection, grouped by series
  GET    /series/{id}        — one series and its volumes
  GET    /collection         — cover-art grid browser page

Cover-image handling lives in covers.py, Google Books title parsing in
titles.py, and request/response schemas in models.py — this file is just
app setup and the routes themselves.
"""
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .covers import COVERS_DIR, cover_url, save_cover, try_fetch_cover_from_google
from .models import IsbnScanRequest, ManualVolumeRequest, VolumeResponse, VolumeUpdateRequest
from .titles import parse_title

PROJECT_ROOT = Path(__file__).parent.parent.parent  # app/backend/main.py -> project root
FRONTEND_DIR = PROJECT_ROOT / "app" / "frontend"

load_dotenv(PROJECT_ROOT / ".env")  # never commit that file

# Anonymous Google Books API quota is effectively 0 — get a free key at
# https://console.cloud.google.com (enable "Books API", create an API key)
# and put it in .env as GOOGLE_BOOKS_API_KEY=... Lookups still work without
# one, but will hit 429s.
GOOGLE_BOOKS_API_KEY = os.environ.get("GOOGLE_BOOKS_API_KEY")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()  # creates tables if they don't exist yet — safe to run every startup
    yield


app = FastAPI(title="Manga Collection Tracker", lifespan=lifespan)

# Allow the frontend (served from a different origin during dev) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this to your actual frontend origin before shipping
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cover images are saved to disk with a filesystem path (see covers.py), but
# the DB rows carry that raw path — meaningless to a browser. Serve the same
# directory over HTTP so the frontend can request them.
app.mount("/covers", StaticFiles(directory=str(COVERS_DIR)), name="covers")

SCAN_LOG_PATH = PROJECT_ROOT / "scan_log.jsonl"


def log_scan_event(event: dict):
    """Append-only record of every scan, one JSON object per line, in the
    order it happened — a plain-text history alongside the SQLite state."""
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
    with open(SCAN_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


@app.get("/")
def scanner_page():
    """Serves the scanner UI itself, so the same host:port works for both
    the page and the API it calls — no separate static server needed."""
    return FileResponse(FRONTEND_DIR / "scanner.html")


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
            "series_title": existing["series_title"],
            "volume_number": existing["volume_number"],
            "already_owned": True,
        })
        return VolumeResponse(
            id=existing["id"],
            series_title=existing["series_title"],
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
    series_title, volume_number = parse_title(title)

    series_id = db.get_or_create_series(title=series_title, author=author)
    volume_id = db.add_volume(series_id, volume_number=volume_number, isbn=isbn)

    has_cover = await try_fetch_cover_from_google(volume_id, info.get("imageLinks", {}))

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


@app.post("/volumes", response_model=VolumeResponse)
def add_volume_manually(payload: ManualVolumeRequest):
    """Adds a volume directly from the collection page's "Add manually"
    form, bypassing the Google Books lookup entirely — for a book with no
    barcode, a damaged one, or one Google Books just doesn't have a record
    for. No cover comes with it; add one afterward via PATCH/cover upload,
    same as any Google-Books-sourced volume with no cover art."""
    series_title = payload.series_title.strip()
    if not series_title:
        raise HTTPException(status_code=400, detail="Series title is required")

    isbn = payload.isbn.strip() if payload.isbn else None
    if isbn and db.get_volume_by_isbn(isbn):
        raise HTTPException(status_code=409, detail="A volume with that ISBN is already in your collection")

    author = payload.author.strip() if payload.author else None
    series_id = db.get_or_create_series(title=series_title, author=author)
    volume_id = db.add_volume(series_id, volume_number=payload.volume_number, isbn=isbn)

    log_scan_event({
        "event": "add_manual",
        "isbn": isbn,
        "volume_id": volume_id,
        "series_title": series_title,
        "volume_number": payload.volume_number,
        "already_owned": False,
    })

    return VolumeResponse(
        id=volume_id,
        series_title=series_title,
        volume_number=payload.volume_number,
        isbn=isbn,
        already_owned=False,
        has_cover=False,
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
    don't always carry a parseable volume number (see titles.parse_title),
    and inconsistent/incomplete titles across ISBNs of the same real series
    can land a volume under the wrong one (or its own stray one-volume
    series).

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
    cover_path = save_cover(volume_id, image_bytes)
    if cover_path is None:
        raise HTTPException(status_code=400, detail="Could not read that image")
    return {"volume_id": volume_id, "cover_url": cover_url(cover_path)}


@app.get("/library")
def get_library():
    rows = [dict(row) for row in db.get_full_library()]
    for row in rows:
        row["cover_url"] = cover_url(row["cover_image_path"])
    return rows


@app.get("/series")
def list_series():
    rows = db.list_series()
    return [dict(row) for row in rows]


@app.get("/series/{series_id}/volumes")
def get_series_volumes(series_id: int):
    rows = [dict(row) for row in db.get_volumes_for_series(series_id)]
    for row in rows:
        row["cover_url"] = cover_url(row["cover_image_path"])
    return rows


@app.get("/collection")
def collection_page():
    """Browsable grid view of the library — the other half of the frontend,
    alongside the scanner at '/'."""
    return FileResponse(FRONTEND_DIR / "collection.html")
