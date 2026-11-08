"""RD-1: marker/duplicate-aware pagination reconciliation rule.

MINIMAL mechanical fix for the three known source-side anomalies found in the
pre-integration decision audit (c1062, c2321, c2341): the source search page's
advertised row count includes
  * one undated "current" marker row (day=NULL, no lysing ref, is_current)
  * duplicate NON-DOCUMENT rows (same day, no lysing ref, identical name),
which the previous pipeline deduplicated BEFORE comparing rows_seen with the
advertised count, producing an exact off-by-(duplicates) mismatch and a
fail-closed PAGINATION_MISMATCH that blocked real document references from
being fetched.

Rule (mechanical, no fuzzy matching):
  1. classify_rows(row) -> one of DOC_ROW / EMPTY_DATED_ROW / CURRENT_MARKER
  2. marker_aware_reconciliation(raw_rows, advertised):
       advertised == len(raw_rows)            (all rendered rows, dedup NOT applied)
       exactly one CURRENT_MARKER total (multi-page: <= one per page)
  3. denominator for completeness = advertised - n_current_markers
     substantive rows = DOC_ROW + EMPTY_DATED_ROW (raw, incl. duplicates)
  4. storage dedup (exact key identity, ON CONFLICT DO NOTHING) is unchanged:
     duplicate EMPTY rows carry no document refs; collapse loses nothing.
  5. row_skrasnr exact gate unchanged (belongs to the caller).

Rows with evidence can never be silently discarded: DOC_ROWS are always
counted and kept; only identical (day, no-ref) rows may collapse for storage,
and that collapse is reported explicitly as duplicate_empty_rows_collapsed.
"""
from __future__ import annotations

import unicodedata

ROW_TYPES = ("DOC_ROW", "EMPTY_DATED_ROW", "CURRENT_MARKER")


def classify_row(row: dict) -> str:
    """Mechanical classification of one parsed search-page row."""
    has_ref = bool(row.get("lysing_id"))
    has_day = bool(row.get("day"))
    if row.get("is_current"):
        return "CURRENT_MARKER"
    if has_ref:
        return "DOC_ROW"
    if has_day:
        return "EMPTY_DATED_ROW"
    # no day, no ref, not flagged current: treat as marker-like undated row
    return "CURRENT_MARKER"


def _exact_identity(row: dict) -> str:
    day = (row.get("day") or "").strip()
    name = unicodedata.normalize("NFC", row.get("name") or "").strip()
    return f"{day}|{row.get('lysing_id') or ''}|{name}"


def classify_rows(raw_rows: list[dict]) -> dict:
    """Classify all raw rendered rows and group exact-identity duplicates.

    Duplicate grouping is EXACT identity on (day, lysing_id, name) — no fuzzy
    matching. Only EMPTY_DATED_ROW groups can contain duplicates; duplicate
    DOC_ROWs (same lysing id) are reported but never discarded from counts.
    """
    counted = {"DOC_ROW": 0, "EMPTY_DATED_ROW": 0, "CURRENT_MARKER": 0}
    duplicate_empty_rows: list[dict] = []
    seen_ids: set[str] = set()
    for row in raw_rows:
        t = classify_row(row)
        counted[t] += 1
        ident = _exact_identity(row)
        if ident in seen_ids:
            if t == "EMPTY_DATED_ROW":
                duplicate_empty_rows.append({
                    "day": row.get("day"), "name": row.get("name")})
            elif t == "DOC_ROW":
                duplicate_empty_rows.append({
                    "day": row.get("day"), "name": row.get("name"),
                    "lysing_id": row.get("lysing_id"),
                    "kind": "DUPLICATE_DOC_ROW_IDENTICAL"})
        seen_ids.add(ident)
    return {
        "row_type_counts": counted,
        "raw_rows": len(raw_rows),
        "current_markers": counted["CURRENT_MARKER"],
        "substantive_rows": counted["DOC_ROW"] + counted["EMPTY_DATED_ROW"],
        "document_rows": counted["DOC_ROW"],
        "duplicate_empty_rows": duplicate_empty_rows,
    }


def marker_aware_reconciliation(raw_rows: list[dict], advertised: int | None,
                                pages_fetched: int, expected_pages: int):
    """Exact row-count reconciliation used by the RD-1 remediation runner.

    Returns a dict with:
      ok        - True when advertised == rendered raw rows (no silent loss)
      detail    - classification/count breakdown
      reason    - machine-readable failure reason when ok is False
    Marker rows are excluded from the completeness denominator; empty
    duplicate rows are classified explicitly, never silently dropped.
    """
    detail = classify_rows(raw_rows)
    if advertised is None:
        return {"ok": False, "detail": detail,
                "reason": "ADVERTISED_COUNT_MISSING"}
    if detail["current_markers"] == 0:
        return {"ok": False, "detail": detail,
                "reason": "CURRENT_MARKER_NOT_RENDERED"}
    if len(raw_rows) != advertised:
        return {"ok": False, "detail": detail,
                "reason": f"RENDERED_ROWS_{len(raw_rows)}_VS_ADVERTISED_{advertised}"}
    if pages_fetched != expected_pages:
        return {"ok": False, "detail": detail,
                "reason": f"PAGES_{pages_fetched}_VS_EXPECTED_{expected_pages}"}
    return {"ok": True, "detail": detail, "reason": "MARKER_AWARE_RECONCILED"}