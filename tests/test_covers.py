"""Tests for covers.py — cover URL/path resolution, and specifically the
cross-platform path bug this app hit in practice: cover_image_path has been
stored by both a Windows and a Linux instance of this app against the same
database, and a full path written by one OS isn't parseable as a path by
the other (pathlib parses separators according to the *current* platform).
"""

import io

from PIL import Image

from app.backend import covers as covers_module
from app.backend import db


def _make_minimal_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color=(10, 20, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_cover_url_none_for_no_cover():
    assert covers_module.cover_url(None) is None
    assert covers_module.cover_url("") is None


def test_cover_url_from_bare_filename():
    assert covers_module.cover_url("abc123.jpg") == "/covers/abc123.jpg"


def test_cover_url_from_windows_style_legacy_path():
    # A real value this app actually produced before it stored bare
    # filenames. Path(...).name can't parse this at all on Linux, since
    # backslashes aren't separators there — it would return the whole
    # string unchanged instead of just "abc123.jpg".
    windows_path = r"C:\Coding\Manga Library\covers\abc123.jpg"
    assert covers_module.cover_url(windows_path) == "/covers/abc123.jpg"


def test_cover_url_from_posix_style_legacy_path():
    posix_path = "/home/pi/manga-library/covers/abc123.jpg"
    assert covers_module.cover_url(posix_path) == "/covers/abc123.jpg"


def test_cover_file_path_resolves_under_covers_dir_from_legacy_windows_path(
    temp_covers_dir,
):
    windows_path = r"C:\Coding\Manga Library\covers\abc123.jpg"
    assert covers_module.cover_file_path(windows_path) == temp_covers_dir / "abc123.jpg"


def test_save_cover_stores_bare_filename_not_full_path(temp_db, temp_covers_dir):
    series_id = db.get_or_create_series("Cover Path Test Series")
    volume_id = db.add_volume(series_id, volume_number=1)

    saved_filename = covers_module.save_cover(volume_id, _make_minimal_jpeg())

    assert saved_filename is not None
    assert "/" not in saved_filename
    assert "\\" not in saved_filename

    volume = db.get_volume_by_id(volume_id)
    assert volume["cover_image_path"] == saved_filename


def test_save_cover_result_is_directly_usable_by_cover_url(temp_db, temp_covers_dir):
    series_id = db.get_or_create_series("Cover URL Round-Trip Series")
    volume_id = db.add_volume(series_id, volume_number=1)

    saved_filename = covers_module.save_cover(volume_id, _make_minimal_jpeg())

    assert covers_module.cover_url(saved_filename) == f"/covers/{saved_filename}"
