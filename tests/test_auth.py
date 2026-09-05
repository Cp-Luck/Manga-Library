"""Tests for the optional write-auth gate (APP_SECRET). Unset by default —
every other test file exercises exactly that default (no header needed)
implicitly, since none of them ever set it. These tests cover the opt-in
gated behavior once a secret is configured, by monkeypatching the
module-level APP_SECRET directly rather than the environment — it's read
once at import time, so setting an env var after the fact wouldn't do
anything.
"""

from app.backend import main as main_module


def _set_secret(monkeypatch, secret):
    monkeypatch.setattr(main_module, "APP_SECRET", secret)


def test_write_route_succeeds_without_header_when_secret_unset(client, monkeypatch):
    _set_secret(monkeypatch, None)
    resp = client.post("/volumes", json={"series_title": "No Auth Needed"})
    assert resp.status_code == 200


def test_write_route_rejects_missing_header_when_secret_set(client, monkeypatch):
    _set_secret(monkeypatch, "correct-secret")
    resp = client.post("/volumes", json={"series_title": "Should Be Blocked"})
    assert resp.status_code == 401


def test_write_route_rejects_wrong_header_when_secret_set(client, monkeypatch):
    _set_secret(monkeypatch, "correct-secret")
    resp = client.post(
        "/volumes",
        json={"series_title": "Should Be Blocked"},
        headers={"X-App-Secret": "wrong-secret"},
    )
    assert resp.status_code == 401


def test_write_route_succeeds_with_correct_header_when_secret_set(client, monkeypatch):
    _set_secret(monkeypatch, "correct-secret")
    resp = client.post(
        "/volumes",
        json={"series_title": "Correctly Authed"},
        headers={"X-App-Secret": "correct-secret"},
    )
    assert resp.status_code == 200


def test_read_routes_never_gated_even_when_secret_set(client, monkeypatch):
    _set_secret(monkeypatch, "correct-secret")
    assert client.get("/library").status_code == 200
    assert client.get("/series").status_code == 200
    assert client.get("/collection").status_code == 200
    assert client.get("/").status_code == 200


def test_all_write_routes_gated_when_secret_set(client, monkeypatch):
    # Created before the secret is set, so setup itself isn't blocked.
    add_resp = client.post("/volumes", json={"series_title": "Setup Volume"})
    volume_id = add_resp.json()["id"]

    _set_secret(monkeypatch, "correct-secret")

    assert client.post("/scan/isbn", json={"isbn": "0000000000000"}).status_code == 401
    assert (
        client.patch(f"/volumes/{volume_id}", json={"volume_number": 1}).status_code
        == 401
    )
    assert (
        client.post(
            f"/volumes/{volume_id}/cover",
            files={"file": ("cover.jpg", b"fake", "image/jpeg")},
        ).status_code
        == 401
    )
    assert client.delete(f"/volumes/{volume_id}").status_code == 401
