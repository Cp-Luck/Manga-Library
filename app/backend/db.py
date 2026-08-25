"""
Data access layer for the manga collection database.
Thin wrapper around sqlite3 — no ORM, since the schema is small and stable.
"""
import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent.parent.parent / "manga.db"  # project root, not inside app/backend/
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_db():
    """Create tables if they don't exist. Safe to call on every app startup."""
    with get_connection() as conn:
        conn.executescript(SCHEMA_PATH.read_text())


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets us access columns by name
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- Series ---

def get_or_create_series(title, author=None):
    """Look up a series by title; create it if it doesn't exist yet.
    Avoids duplicate series rows when the same title comes in from
    different scans (Google Books, manual entry, etc). Case-insensitive —
    Google Books itself isn't consistent about it (e.g. "FAIRY TAIL: 100
    Years Quest" vs "Fairy Tail: 100 Years Quest" across different ISBNs of
    the same real series), so an exact match would fragment those apart."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM series WHERE title = ? COLLATE NOCASE", (title,)
        ).fetchone()
        if row:
            return row["id"]

        cursor = conn.execute(
            "INSERT INTO series (title, author) VALUES (?, ?)", (title, author)
        )
        return cursor.lastrowid


def list_series():
    with get_connection() as conn:
        return conn.execute("SELECT * FROM series ORDER BY title").fetchall()


# --- Volumes ---

def add_volume(series_id, volume_number=None, isbn=None, cover_image_path=None, notes=None):
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO volumes (series_id, volume_number, isbn, cover_image_path, notes)
               VALUES (?, ?, ?, ?, ?)""",
            (series_id, volume_number, isbn, cover_image_path, notes),
        )
        return cursor.lastrowid


def delete_volume(volume_id):
    """Removes a volume row outright — used both to undo a misidentified
    scan and for deliberate removal from the collection page."""
    with get_connection() as conn:
        conn.execute("DELETE FROM volumes WHERE id = ?", (volume_id,))


def set_volume_number(volume_id, volume_number):
    """Manual correction from the collection page — for when Google Books'
    title didn't carry a parseable volume number at all."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE volumes SET volume_number = ? WHERE id = ?",
            (volume_number, volume_id),
        )


def move_volume_to_series(volume_id, series_id):
    """Manual correction from the collection page — for when Google Books
    returned an inconsistent or incomplete title that landed a volume under
    the wrong series (or its own stray one-volume series)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE volumes SET series_id = ? WHERE id = ?",
            (series_id, volume_id),
        )


def count_volumes_in_series(series_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM volumes WHERE series_id = ?", (series_id,)
        ).fetchone()
        return row["n"]


def delete_series(series_id):
    """Only ever called on an already-empty series — see move_volume_to_series
    callers, which check count_volumes_in_series first."""
    with get_connection() as conn:
        conn.execute("DELETE FROM series WHERE id = ?", (series_id,))


def get_volume_by_isbn(isbn):
    """Primary lookup path — used right after a barcode scan to check
    if this exact volume is already in the collection. Joined with series
    so the caller gets series_title for free instead of a second query."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT volumes.*, series.title AS series_title
               FROM volumes JOIN series ON volumes.series_id = series.id
               WHERE volumes.isbn = ?""",
            (isbn,),
        ).fetchone()


def get_volumes_for_series(series_id):
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM volumes WHERE series_id = ?
               ORDER BY volume_number""",
            (series_id,),
        ).fetchall()


def get_full_library():
    """Everything, joined with series info — the query behind the main
    'shelf' browse view."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT volumes.*, series.title AS series_title, series.author
               FROM volumes
               JOIN series ON volumes.series_id = series.id
               ORDER BY series.title, volumes.volume_number"""
        ).fetchall()


def get_volume_by_id(volume_id):
    """Straight lookup by primary key, joined with series info."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT volumes.*, series.title AS series_title
               FROM volumes JOIN series ON volumes.series_id = series.id
               WHERE volumes.id = ?""",
            (volume_id,),
        ).fetchone()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
