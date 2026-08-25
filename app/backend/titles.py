"""Parses a Google Books title into (series_title, volume_number)."""
import re
from typing import Optional

# Google Books titles carry the volume number in several different shapes
# depending on publisher/edition — 'Chainsaw Man, Vol. 4', 'Apothecary
# Diaries 01 (Manga)', 'Apothecary Diaries. 2', 'Horimiya, Vol. 17 - Special
# Edition' — not always with the word "Vol"/"Volume" at all, and sometimes
# with an edition qualifier trailing after the number instead of (or besides)
# a parenthetical. Match a trailing number, with an optional "Vol./Volume"
# keyword before it and an optional "(...)" or "- qualifier"/": qualifier"
# after it, and treat everything before that as the series name.
_VOLUME_SUFFIX_RE = re.compile(
    r"[,.]?\s*(?:vol\.?|volume)?\s*(\d+)\s*(?:\([^)]*\)|[-:]\s*\S.*)?\s*$",
    re.IGNORECASE,
)


def parse_title(title: str) -> tuple[str, Optional[int]]:
    """Splits a Google Books title into (series_title, volume_number).
    Falls back to (title, None) when nothing matches — e.g. a one-shot with
    no volume number at all. Not foolproof; user can correct via the
    collection page (PATCH /volumes/{id})."""
    match = _VOLUME_SUFFIX_RE.search(title)
    if not match:
        return title.strip(), None
    series_title = title[:match.start()].strip().rstrip(",.").strip()
    return (series_title or title.strip()), int(match.group(1))
