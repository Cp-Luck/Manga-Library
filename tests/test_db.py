"""Tests for the db.py data-access layer. Every test gets its own throwaway
SQLite file via the temp_db fixture — never the real manga.db."""
from app.backend import db


def test_get_or_create_series_creates_once_and_reuses(temp_db):
    first_id = db.get_or_create_series("Chainsaw Man", author="Tatsuki Fujimoto")
    second_id = db.get_or_create_series("Chainsaw Man")

    assert first_id == second_id
    assert len(db.list_series()) == 1


def test_get_or_create_series_is_case_insensitive(temp_db):
    # Google Books isn't consistent about casing across ISBNs of the same
    # real series — this must not fragment them into separate series rows.
    lower_id = db.get_or_create_series("fairy tail")
    upper_id = db.get_or_create_series("FAIRY TAIL")

    assert lower_id == upper_id
    assert len(db.list_series()) == 1


def test_add_volume_and_get_by_isbn(temp_db):
    series_id = db.get_or_create_series("One Piece")
    volume_id = db.add_volume(series_id, volume_number=105, isbn="9781974735306")

    found = db.get_volume_by_isbn("9781974735306")

    assert found is not None
    assert found["id"] == volume_id
    assert found["series_title"] == "One Piece"
    assert found["volume_number"] == 105


def test_get_volume_by_isbn_returns_none_when_not_found(temp_db):
    assert db.get_volume_by_isbn("0000000000000") is None


def test_delete_volume_removes_it(temp_db):
    series_id = db.get_or_create_series("Berserk")
    volume_id = db.add_volume(series_id, volume_number=1)

    db.delete_volume(volume_id)

    assert db.get_volume_by_id(volume_id) is None


def test_set_volume_number_updates_in_place(temp_db):
    series_id = db.get_or_create_series("Horimiya")
    volume_id = db.add_volume(series_id, volume_number=None)

    db.set_volume_number(volume_id, 17)

    assert db.get_volume_by_id(volume_id)["volume_number"] == 17


def test_move_volume_to_series_changes_ownership(temp_db):
    wrong_series_id = db.get_or_create_series("Fairy Tail: Misfiled")
    correct_series_id = db.get_or_create_series("Fairy Tail")
    volume_id = db.add_volume(wrong_series_id, volume_number=63)

    db.move_volume_to_series(volume_id, correct_series_id)

    assert db.get_volume_by_id(volume_id)["series_id"] == correct_series_id


def test_count_volumes_in_series(temp_db):
    series_id = db.get_or_create_series("Vinland Saga")
    db.add_volume(series_id, volume_number=1)
    db.add_volume(series_id, volume_number=2)

    assert db.count_volumes_in_series(series_id) == 2


def test_delete_series_removes_empty_series(temp_db):
    series_id = db.get_or_create_series("Temporary Series")

    db.delete_series(series_id)

    assert db.list_series() == []


def test_get_volumes_for_series_orders_by_volume_number(temp_db):
    series_id = db.get_or_create_series("Attack on Titan")
    db.add_volume(series_id, volume_number=3)
    db.add_volume(series_id, volume_number=1)
    db.add_volume(series_id, volume_number=2)

    volumes = db.get_volumes_for_series(series_id)

    assert [v["volume_number"] for v in volumes] == [1, 2, 3]


def test_get_full_library_joins_series_title_and_author(temp_db):
    series_id = db.get_or_create_series("Jujutsu Kaisen", author="Gege Akutami")
    db.add_volume(series_id, volume_number=1)

    rows = db.get_full_library()

    assert len(rows) == 1
    assert rows[0]["series_title"] == "Jujutsu Kaisen"
    assert rows[0]["author"] == "Gege Akutami"
