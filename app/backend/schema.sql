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

-- Enforces get_or_create_series()'s case-insensitive dedup at the database
-- level, not just in application code: two concurrent requests creating the
-- same new series can both pass the app-level "does it exist?" check before
-- either INSERT commits (classic check-then-act race). Without this, that
-- race could create two series rows for the same title. A UNIQUE INDEX
-- achieves the same enforcement as a UNIQUE column constraint would, without
-- needing to recreate the table (SQLite can't ALTER TABLE to add a column
-- constraint after the fact) — safe to add to an existing database with
-- rows already in it, as long as no case-insensitive duplicates exist yet
-- (verified against the live database before this was added).
CREATE UNIQUE INDEX IF NOT EXISTS idx_series_title_unique ON series(title COLLATE NOCASE);

-- Migration note: this project has no formal migration framework (e.g.
-- Alembic) — schema changes ship as additive, idempotent CREATE TABLE/INDEX
-- IF NOT EXISTS statements run on every startup (see db.init_db()). That's
-- adequate for the additive changes made so far, but a genuinely destructive
-- change (renaming/removing a column with existing data) would need a
-- hand-written one-off migration script, since executescript() here can't
-- express that safely.
