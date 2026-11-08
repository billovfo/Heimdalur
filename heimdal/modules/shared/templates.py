# heimdal/modules/shared/templates.py
"""Template filters and globals: pct, dk, heatband, fail."""
from decimal import Decimal


def pct(value):
    """Danish decimal comma, FIXED two decimals ("25,00 %") — remediation plan
    v1 T6.4 (F-31): one consistent percent format across the product; variable
    trailing precision ("25", "25,5", "33,3333") read as four different
    formats. None is the caller's problem — a missing percentage has its own
    explicit rendering, never a blank cell.

    Quantized to 2 decimals before display: contributing_paths (jsonb) stores
    a raw Python float from the DP's chained division, e.g. 7.826910000000001
    — without this, that float noise rendered verbatim (bug found 2026-08-02,
    live page for entity 818/Jarðhiti).
    """
    if value is None:
        return None
    d = Decimal(str(value)).quantize(Decimal("0.01"))
    return f"{d:f}".replace(".", ",")


def dk(value):
    return "—" if value is None else value.strftime("%d-%m-%Y %H:%M")


def heatband(value):
    """Farvekodnings-bånd for antal-kolonner (ejerbeslutning 2026-08-16):
    0 dæmpet, 1-2 svagt, 3-5 lyst, 6+ accentueret. Klassen driver CSS'en —
    kuløren er aldrig eneste signal, tallet står uændret ved siden af."""
    if not value or value <= 0:
        return "n-0"
    if value <= 2:
        return "n-1"
    if value <= 5:
        return "n-2"
    return "n-3"


def fail(message):
    """Templates have no assert. §16.2's direction rule is worth one."""
    raise AssertionError(message)
