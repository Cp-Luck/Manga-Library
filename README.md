# Manga Collection Tracker

A personal FastAPI backend for tracking a physical manga collection: scan a
barcode to log a volume, browse the result as a cover-art grid. Cover images
come straight from Google Books — there's no camera-based cover scanning.

## Setup

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in `GOOGLE_BOOKS_API_KEY` (free — from
[console.cloud.google.com](https://console.cloud.google.com), enable "Books
API", create an API key). Without it, ISBN lookups run unauthenticated and
will hit Google's near-zero anonymous quota (429s on most requests). `.env`
is gitignored — never commit it.

## Run the backend

```bash
python run.py
```

(Equivalent to `python -m uvicorn app.main:app --reload --host 0.0.0.0 --port
8000` — `run.py` just avoids retyping that, and sidesteps PATH issues on
systems where pip installs scripts somewhere your shell doesn't look.)

The database (`manga.db`) is created automatically from `schema.sql` on
first startup.

Once it's running, check `http://localhost:8000/docs` for the interactive
API.

## Try the scanner

The backend serves `scanner.html` itself at `/`, so whatever URL reaches
the backend also opens the scanner — no separate static server, no editing
`API_BASE` (it auto-detects its own origin: `window.location.origin`).

Camera access needs a secure context, though — a plain `http://` LAN link
(the "Network" URL `run.py` prints) doesn't qualify on any phone browser,
only `https://` or literally `localhost` do. So for a real phone test, tunnel
the backend and open the tunnel's `https://` URL instead of the LAN address:

```bash
cloudflared tunnel --url http://localhost:8000
```

No account needed — it prints a random `https://...trycloudflare.com` URL
that proxies straight to your local server, and camera access works from it.
`run.py` prints this exact command on every startup as a reminder.

(ngrok is the more commonly recommended alternative, but in practice its
free accounts now require a newer agent version than package managers tend
to ship, and its self-updater got flagged and deleted by Windows Defender in
testing — cloudflared avoided both problems and needs no signup at all.)

## Flow

1. Scan a barcode → `POST /scan/isbn` → looks up Google Books, creates
   the series/volume if new, tells you if you already own it
2. Confirm the result ("✓ Yes, that's it" / "✗ Wrong manga") — a barcode
   misread can point at the wrong ISBN, so nothing is trusted until you
   confirm it. "Wrong manga" calls `DELETE /volumes/{id}` to undo the
   volume this same scan just created, but only if this scan is what
   created it — a pre-existing library entry is never touched.
3. If Google Books had a cover image for that ISBN, it's downloaded and
   attached automatically as part of step 1 — nothing else to do. If it
   didn't, the volume is still added, just without one.
4. Browse the result at `/collection` — a cover-art grid, grouped by
   series. Click any cover to remove that volume from the library.

## Scan log

Every `/scan/isbn` call that resolves to a volume (new or already owned)
appends one JSON line to `scan_log.jsonl` — a plain-text, timestamped record
of everything scanned, in order. It's a log, not the source of truth (that's
`manga.db`); a failed lookup (e.g. a 502 from Google Books) writes nothing,
since nothing happened yet. Gitignored, like the rest of the runtime data
below.

## API routes

| Method | Path                          | Purpose                                              |
|--------|-------------------------------|-------------------------------------------------------|
| POST   | `/scan/isbn`                  | Look up/register a volume from a scanned ISBN, auto-fetching its cover |
| DELETE | `/volumes/{id}`               | Remove a volume (undo a scan, or a deliberate removal from `/collection`) |
| GET    | `/library`                    | Full collection, grouped by series                     |
| GET    | `/series`                     | List all series                                        |
| GET    | `/series/{series_id}/volumes` | Volumes belonging to one series                         |
| GET    | `/collection`                 | The cover-art grid browser page                          |

## Files

```
Manga Library/
├── run.py              entry point — start with `python run.py`
├── requirements.txt
├── .env.example        template for GOOGLE_BOOKS_API_KEY; copy to .env
├── app/                 all source code (a Python package)
│   ├── main.py          FastAPI app, all routes
│   ├── db.py             SQLite data access layer
│   ├── schema.sql         database schema (series, volumes)
│   ├── scanner.html        barcode scanner + confirm UI, served at "/"
│   └── collection.html      cover-art grid browser, served at "/collection"
└── (gitignored runtime data, created automatically — see below)
```

`app/` holds only source code; `run.py` imports it as `app.main:app`. Runtime
data lives at the project root instead of inside `app/`, so it stays put and
easy to find regardless of how the source is organized internally.

Gitignored runtime data (created automatically, never committed):
`.env`, `manga.db`, `scan_log.jsonl`, `covers/`

## Known gaps / next steps

- No auth — fine for personal/local use, not for anything public-facing
- No way to identify a volume without its barcode (no cover-matching
  feature) — if the barcode's unreadable or missing, there's currently no
  fallback besides adding it by ISBN manually via `/docs`
- Series/volume-number parsing from the Google Books title (`_parse_title`
  in `main.py`) is a best-effort regex, not guaranteed — publishers format
  titles inconsistently ("Series, Vol. 4" vs "Series 04 (Manga)" vs "Series.
  2" have all shown up for real). Worth a periodic sanity check on
  `/library` for series that should be merged but aren't
- CORS is wide open (`allow_origins=["*"]`) — tighten before deploying
  anywhere it's reachable by more than you
