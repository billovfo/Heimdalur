"""Deterministic normalization helpers (plan v3.1.2, section 25).

Verbatim source values are ALWAYS preserved next to the normalized value by the
parsers. Normalization here is conservative: digits/lower/trim/collapse only -
no fuzzy matching in v1.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

_WS_RE = re.compile(r"\s+")


def normalize_whitespace(text: str) -> str:
    """Collapse all internal whitespace runs to single spaces and trim."""
    return _WS_RE.sub(" ", text or "").strip()


def normalize_registration_number(text: str | None) -> str | None:
    """Conservative regnr normalization: first digit run, lowercase-trimmed.

    'P/F  10' -> '10'; '5015' -> '5015'; 'S 5015' -> '5015'. Returns None when
    no digit run exists (missing/unparseable registration number).
    """
    if not text:
        return None
    match = re.search(r"\d+", unicodedata.normalize("NFC", str(text)))
    return match.group(0) if match else None


def normalize_entity_name(text: str | None) -> str | None:
    """Lowercase, whitespace-collapsed entity key (verbatim name always kept by
    the caller). Used only for SET_MEMBERSHIP entity pairing."""
    if text is None:
        return None
    slug = unicodedata.normalize("NFC", str(text)).strip().lower()
    slug = re.sub(r"[.,;:]+$", "", slug)
    return _WS_RE.sub(" ", slug).strip()


def _month_from_name(name: str) -> int | None:
    months = {
        "januar": 1, "februar": 2, "mars": 3, "april": 4, "apríl": 4,
        "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
        "oktober": 10, "november": 11, "desember": 12,
    }
    return months.get(name.strip().lower())


def parse_date(text: str | None) -> date | None:
    """Parse the source date formats seen in fixtures, nothing more.

    Supported (Faroese/Danish print formats):
      DD-MM-YYYY   ('Dagf. 30-08-2026', 'Dagf. 02-06-2021')
      DD.MM.YYYY   ('15.04.2010' notice header, '29.03.2010' change date)
      'tann DD. <month> YYYY' ('Skráseting Føroya tann 15. apríl 2010')
    Returns None when the text does not match one of these exactly - no guesses.
    """
    if not text:
        return None
    text = unicodedata.normalize("NFC", text)
    match = re.search(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b", text)
    if match:
        day_, month_, year = (int(part) for part in match.groups())
        try:
            return _valid_date(year, month_, day_)
        except ValueError:
            pass
    match = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", text)
    if match:
        day_, month_, year = (int(part) for part in match.groups())
        try:
            return _valid_date(year, month_, day_)
        except ValueError:
            pass
    match = re.search(
        r"\b(\d{1,2})\.\s*([A-Za-zÀ-ÿ]+)\s+(\d{4})", text)
    if match:
        day_, month_name, year = match.group(1), match.group(2), match.group(3)
        month = _month_from_name(month_name)
        if month:
            try:
                return _valid_date(int(year), month, int(day_))
            except ValueError:
                return None
    return None


def _valid_date(year: int, month: int, day: int) -> date:
    return date(year, month, day)


def parse_money(text: str | None) -> tuple[str | None, int | None]:
    """Parse 'DKK: 192.000.000' / 'DKK 192.000.000' -> (currency, amount).

    Dot-separated thousands; verbatim value stays with the caller.
    """
    if not text:
        return None, None
    currency = None
    match = re.search(r"\b([A-Z]{3})\b", text)
    if match:
        currency = match.group(1)
    # Dot-separated groups are thousands separators; only digits survive.
    digits = re.sub(r"[^\d]", "", text)
    amount = int(digits) if digits else None
    return currency, amount