"""API-level tests via FastAPI's TestClient. Every test uses the isolated
`client` fixture (throwaway db/covers/scan-log — see conftest.py), so none
of this touches your real collection.

Google Books lookups are faked via FakeAsyncClient rather than hitting the
real network — tests should be deterministic and not depend on external
availability or your API quota.
"""

import io

from PIL import Image

from app.backend import covers
from app.backend import main as main_module


def _make_test_image_bytes(image_format: str) -> bytes:
    """A genuine, minimal, decodable image in the given format — for
    exercising real Pillow validation rather than a fake byte string that
    only *looks* like an image header."""
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color=(200, 50, 50)).save(buffer, format=image_format)
    return buffer.getvalue()


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.is_success = 200 <= status_code < 300

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Drop-in replacement for httpx.AsyncClient that returns a canned
    Google Books response instead of making a real request."""

    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, *args, **kwargs):
        return self._response


def _mock_google_books(monkeypatch, status_code=200, payload=None):
    response = _FakeResponse(status_code, payload or {})
    monkeypatch.setattr(
        main_module.httpx, "AsyncClient", lambda: _FakeAsyncClient(response)
    )


# --- POST /volumes (manual add) ---


def test_add_volume_manually(client):
    resp = client.post(
        "/volumes", json={"series_title": "Chainsaw Man", "volume_number": 4}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["series_title"] == "Chainsaw Man"
    assert body["volume_number"] == 4
    assert body["already_owned"] is False
    assert body["has_cover"] is False


def test_add_volume_manually_requires_series_title(client):
    resp = client.post("/volumes", json={"series_title": "   "})
    assert resp.status_code == 400


def test_add_volume_manually_rejects_duplicate_isbn(client):
    client.post("/volumes", json={"series_title": "Berserk", "isbn": "1111111111111"})
    resp = client.post(
        "/volumes", json={"series_title": "Berserk (dup)", "isbn": "1111111111111"}
    )

    assert resp.status_code == 409


# --- DELETE /volumes/{id} ---


def test_delete_volume(client):
    add_resp = client.post(
        "/volumes", json={"series_title": "Vinland Saga", "volume_number": 1}
    )
    volume_id = add_resp.json()["id"]

    delete_resp = client.delete(f"/volumes/{volume_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json() == {"deleted": volume_id}

    library = client.get("/library").json()
    assert all(row["id"] != volume_id for row in library)


# --- PATCH /volumes/{id} ---


def test_update_volume_number_only_leaves_series_untouched(client):
    add_resp = client.post("/volumes", json={"series_title": "Horimiya"})
    volume_id = add_resp.json()["id"]

    resp = client.patch(f"/volumes/{volume_id}", json={"volume_number": 17})

    assert resp.status_code == 200
    body = resp.json()
    assert body["volume_number"] == 17
    assert body["series_title"] == "Horimiya"


def test_update_volume_returns_404_for_missing_volume(client):
    resp = client.patch("/volumes/999999", json={"volume_number": 1})
    assert resp.status_code == 404


def test_update_volume_moves_to_new_series_and_cleans_up_empty_old_series(client):
    add_resp = client.post(
        "/volumes", json={"series_title": "Fairy Tail: Misfiled", "volume_number": 63}
    )
    volume_id = add_resp.json()["id"]

    resp = client.patch(f"/volumes/{volume_id}", json={"series_title": "Fairy Tail"})

    assert resp.status_code == 200
    assert resp.json()["series_title"] == "Fairy Tail"

    series_titles = {s["title"] for s in client.get("/series").json()}
    assert "Fairy Tail: Misfiled" not in series_titles
    assert "Fairy Tail" in series_titles


# --- GET /library, /series, /series/{id}/volumes ---


def test_get_library_returns_added_volume_with_cover_url_field(client):
    client.post("/volumes", json={"series_title": "Jujutsu Kaisen", "volume_number": 1})

    rows = client.get("/library").json()

    assert len(rows) == 1
    assert rows[0]["series_title"] == "Jujutsu Kaisen"
    assert rows[0]["cover_url"] is None  # no cover attached yet


def test_get_series_volumes(client):
    add_resp = client.post(
        "/volumes", json={"series_title": "Attack on Titan", "volume_number": 1}
    )
    series_id = None
    for s in client.get("/series").json():
        if s["title"] == "Attack on Titan":
            series_id = s["id"]

    volumes = client.get(f"/series/{series_id}/volumes").json()

    assert len(volumes) == 1
    assert volumes[0]["id"] == add_resp.json()["id"]


# --- POST /scan/isbn ---


def test_scan_isbn_already_owned_skips_google_books_lookup(client, monkeypatch):
    client.post(
        "/volumes",
        json={
            "series_title": "One Piece",
            "volume_number": 105,
            "isbn": "9781974735306",
        },
    )

    # No Google Books mock installed — if the endpoint tried to call out
    # over the real network for an already-owned ISBN, this would hang or
    # fail instead of returning quickly.
    resp = client.post("/scan/isbn", json={"isbn": "9781974735306"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["already_owned"] is True
    assert body["series_title"] == "One Piece"


def test_scan_isbn_new_volume_creates_it_from_google_books_metadata(
    client, monkeypatch
):
    _mock_google_books(
        monkeypatch,
        status_code=200,
        payload={
            "items": [
                {
                    "volumeInfo": {
                        "title": "Chainsaw Man, Vol. 4",
                        "authors": ["Tatsuki Fujimoto"],
                        "imageLinks": {},  # no cover art for this ISBN
                    }
                }
            ]
        },
    )

    resp = client.post("/scan/isbn", json={"isbn": "9781974716280"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["already_owned"] is False
    assert body["series_title"] == "Chainsaw Man"
    assert body["volume_number"] == 4
    assert body["has_cover"] is False


def test_scan_isbn_not_found_in_google_books_returns_404(client, monkeypatch):
    _mock_google_books(monkeypatch, status_code=200, payload={})  # no "items" key

    resp = client.post("/scan/isbn", json={"isbn": "0000000000000"})

    assert resp.status_code == 404


def test_scan_isbn_google_books_error_returns_502(client, monkeypatch):
    _mock_google_books(
        monkeypatch, status_code=500, payload={"error": {"message": "Backend Error"}}
    )

    resp = client.post("/scan/isbn", json={"isbn": "9781974716280"})

    assert resp.status_code == 502


# --- POST /volumes/{id}/cover ---


def test_upload_cover_for_existing_volume(client):
    add_resp = client.post(
        "/volumes", json={"series_title": "Spy x Family", "volume_number": 1}
    )
    volume_id = add_resp.json()["id"]

    resp = client.post(
        f"/volumes/{volume_id}/cover",
        files={"file": ("cover.jpg", _make_test_image_bytes("JPEG"), "image/jpeg")},
    )

    assert resp.status_code == 200
    assert resp.json()["volume_id"] == volume_id
    assert resp.json()["cover_url"].startswith("/covers/")
    assert resp.json()["cover_url"].endswith(".jpg")


def test_upload_cover_saves_with_extension_matching_real_format_not_filename(client):
    # Upload a genuine PNG named "cover.jpg" — the saved extension should
    # reflect the real decoded format (PNG), not the filename's claim.
    add_resp = client.post(
        "/volumes", json={"series_title": "Mushishi", "volume_number": 1}
    )
    volume_id = add_resp.json()["id"]

    resp = client.post(
        f"/volumes/{volume_id}/cover",
        files={"file": ("cover.jpg", _make_test_image_bytes("PNG"), "image/jpeg")},
    )

    assert resp.status_code == 200
    assert resp.json()["cover_url"].endswith(".png")


def test_upload_cover_rejects_non_image_bytes(client):
    add_resp = client.post(
        "/volumes", json={"series_title": "Naruto", "volume_number": 1}
    )
    volume_id = add_resp.json()["id"]

    resp = client.post(
        f"/volumes/{volume_id}/cover",
        files={
            "file": (
                "cover.jpg",
                b"not actually an image, just text pretending to be one",
                "image/jpeg",
            )
        },
    )

    assert resp.status_code == 400


def test_upload_cover_rejects_oversized_file(client):
    add_resp = client.post(
        "/volumes", json={"series_title": "Bleach", "volume_number": 1}
    )
    volume_id = add_resp.json()["id"]

    oversized = b"\x00" * (covers.MAX_COVER_UPLOAD_BYTES + 1)
    resp = client.post(
        f"/volumes/{volume_id}/cover",
        files={"file": ("cover.jpg", oversized, "image/jpeg")},
    )

    assert resp.status_code == 400


def test_upload_cover_returns_404_for_missing_volume(client):
    resp = client.post(
        "/volumes/999999/cover",
        files={"file": ("cover.jpg", b"fakejpegbytes", "image/jpeg")},
    )
    assert resp.status_code == 404
