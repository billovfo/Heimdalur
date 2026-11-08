"""COV-HISTORICAL-PERSON-SOURCE v1.1 helpers — immutable source-key derivation.

Persistent logical identity:
  COV-HISTORICAL-SOURCE-KEY v1 = <document_sha256>|<source_event_id>|<occurrence_index>

Where occurrence_index is zero-based ordinal in pdf_parse_result.structured_data.changes[]
BEFORE filtering, sorting, normalization or projection persistence. No surrogate ids,
no name dependency, no runtime ordering.
"""
from __future__ import annotations
import json

SOURCE_KEY_VERSION = "COV-HISTORICAL-SOURCE-KEY v1"
COVERAGE_RULE = "COV-HISTORICAL-PERSON-SOURCE"
RULE_VERSION = "v1.1"

def cov_source_key(document_sha256: str, source_event_id: int, occurrence_index: int) -> str:
    """Canonical serialization for COV-HISTORICAL-SOURCE-KEY v1."""
    return f"{document_sha256}|{int(source_event_id)}|{int(occurrence_index)}"

def parse_cov_source_key(key: str) -> tuple[str, int, int]:
    parts = key.split("|")
    if len(parts) != 3:
        raise ValueError(f"invalid COV key {key}")
    doc_sha, evt, idx = parts
    return doc_sha, int(evt), int(idx)

def occurrence_index_from_structured(structured_data: dict | str, target_entity_normalized: str | None, target_raw_text: str | None = None) -> int | None:
    """Derive occurrence_index as original changes array position (pre-filter).

    Finds the first changes entry whose entity_normalized (or entity) matches
    target_entity_normalized in original order. Returns zero-based index or None
    if not found. This is pre-filter and pre-sort — original array order.
    Caller must verify uniqueness; if multiple entries match same normalized name,
    caller must disambiguate via additional stable discriminator (not implemented
    for lean v1.1 — such events are excluded from H1).
    """
    if structured_data is None:
        return None
    sd = structured_data if isinstance(structured_data, dict) else json.loads(structured_data or "{}")
    changes = sd.get("changes") or []
    norm_target = (target_entity_normalized or "").strip().lower()
    for idx, ch in enumerate(changes):
        ent_norm = (ch.get("entity_normalized") or ch.get("entity") or "").strip().lower()
        # also consider raw_text match as fallback for strictness
        if norm_target and ent_norm == norm_target:
            return idx
        if target_raw_text and ch.get("raw_text") == target_raw_text:
            return idx
    return None
