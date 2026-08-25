"""Tests for parse_title — matches the documented tricky cases in
titles.py's own module docstring, plus the no-volume-number fallback."""

import pytest

from app.backend.titles import parse_title


@pytest.mark.parametrize(
    "title, expected_series, expected_volume",
    [
        ("Chainsaw Man, Vol. 4", "Chainsaw Man", 4),
        ("Apothecary Diaries 01 (Manga)", "Apothecary Diaries", 1),
        ("Apothecary Diaries. 2", "Apothecary Diaries", 2),
        ("Horimiya, Vol. 17 - Special Edition", "Horimiya", 17),
        ("One Piece, Vol. 105", "One Piece", 105),
        ("Berserk Deluxe Edition, Vol. 1", "Berserk Deluxe Edition", 1),
        ("Fullmetal Alchemist, Vol. 27: The Final Volume", "Fullmetal Alchemist", 27),
    ],
)
def test_parse_title_extracts_series_and_volume(
    title, expected_series, expected_volume
):
    series, volume = parse_title(title)
    assert series == expected_series
    assert volume == expected_volume


def test_parse_title_falls_back_when_no_volume_number():
    # "100 Years Quest" isn't a volume number — the trailing digits aren't
    # at the end of the title, so this should fall back to (title, None)
    # rather than misreading "100" as a volume.
    series, volume = parse_title("FAIRY TAIL: 100 Years Quest")
    assert series == "FAIRY TAIL: 100 Years Quest"
    assert volume is None


def test_parse_title_falls_back_for_titles_with_no_number_at_all():
    series, volume = parse_title("A one-shot with no number")
    assert series == "A one-shot with no number"
    assert volume is None


def test_parse_title_strips_whitespace():
    series, volume = parse_title("  Chainsaw Man, Vol. 4  ")
    assert series == "Chainsaw Man"
    assert volume == 4
