-- Manga collection schema
-- One row per physical volume you own.

CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS volumes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id INTEGER NOT NULL REFERENCES series(id),
    volume_number INTEGER,               -- nullable: some manga is single-volume, or number unknown
    isbn TEXT UNIQUE,                    -- nullable: not every scan resolves an ISBN
    cover_image_path TEXT,               -- path to the cover image, if Google Books had one
    embedding_id INTEGER,                -- vestigial: was a FAISS index position from the
                                          -- since-removed cover-matching feature. Left in place
                                          -- rather than risk an ALTER TABLE on a live DB; no
                                          -- code reads or writes it anymore.
    date_added TEXT DEFAULT (datetime('now')),
    notes TEXT
);

-- Fast lookups by ISBN (the primary identification path)
CREATE INDEX IF NOT EXISTS idx_volumes_isbn ON volumes(isbn);

-- Fast "all volumes for this series" queries (for the shelf/browse view)
CREATE INDEX IF NOT EXISTS idx_volumes_series ON volumes(series_id);
