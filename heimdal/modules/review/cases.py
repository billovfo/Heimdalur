"""REVIEW-CASE-KEY-v1 generation + eligibility (P1, read-only).

Source-specific stable identity contracts (frozen P0 audit):
- PDF-HIST (HISTORICAL_EVENT): doc_sha256 | event | occ_index(persisted) | norm | anchor
- PDF-CURRENT (CURRENT_AUTHORITATIVE): doc_sha | obs | parse | snap_pos(persisted) | role | norm | anchor
- ROLE:<id>, person_role_id, bare normalized names: NEVER identity.
- Ownership-derived identities: NOT ELIGIBLE.
- Duplicate normalized names within one event/list: NOT ELIGIBLE.
"""
from __future__ import annotations

CASE_KEY_VERSION = "REVIEW-CASE-KEY-v1"

NOT_ELIGIBLE_REASONS = (
    "NO_STABLE_SOURCE_IDENTITY",
    "DUPLICATE_OCCURRENCE_AMBIGUITY",
    "OWNERSHIP_SOURCE_NOT_SUPPORTED",
    "SOURCE_IDENTITY_RECONSTRUCTION_FAILED",
)


def hist_case_key(doc_sha256, source_event_id, occ_index, norm_name,
                  company_anchor) -> str:
    """Build a PDF-HIST case key. occ_index must be persisted, never rebuilt."""
    if not doc_sha256 or source_event_id is None or occ_index is None:
        raise ValueError("PDF-HIST key requires doc_sha, event, persisted occ_index")
    if not norm_name:
        raise ValueError("PDF-HIST key requires normalized name")
    return ("PDF-HIST|%s|%s|%s|%s|%s"
            % (doc_sha256, int(source_event_id), int(occ_index),
               norm_name, company_anchor or ""))


def current_case_key(doc_sha256, observation_id, parse_id, snap_position,
                     role_type, norm_name, company_anchor) -> str:
    """Build a PDF-CURRENT case key. snap_position persisted at materialization."""
    if not doc_sha256 or observation_id is None or parse_id is None \
            or snap_position is None:
        raise ValueError("PDF-CURRENT key requires doc_sha, obs, parse, persisted position")
    if not norm_name or not role_type:
        raise ValueError("PDF-CURRENT key requires name and role")
    return ("PDF-CURRENT|%s|%s|%s|%s|%s|%s|%s"
            % (doc_sha256, int(observation_id), int(parse_id),
               int(snap_position), role_type, norm_name, company_anchor or ""))


def check_role_id_forbidden(value) -> None:
    """Hard guard: ROLE:<id>/person_role_id must never become case identity."""
    s = str(value or "")
    if s.startswith("ROLE:") or (s.isdigit() and s):
        raise ValueError("volatile build ids are forbidden as case identity")


def classify_occurrence(row: dict) -> dict:
    """Classify one company_person_role row as eligible or not-eligible.

    row keys: source_scope, doc_sha256, source_event_id, source_observation_id,
    source_parse_result_id, norm_name, role_type, company_anchor,
    duplicate_in_event (bool, precomputed by caller).
    Returns {eligible: bool, kind|reason: str}.
    """
    scope = row.get("source_scope")
    if row.get("ownership_source"):
        return {"eligible": False, "reason": "OWNERSHIP_SOURCE_NOT_SUPPORTED"}
    if row.get("duplicate_in_event"):
        return {"eligible": False, "reason": "DUPLICATE_OCCURRENCE_AMBIGUITY"}
    if scope == "HISTORICAL_EVENT":
        if not row.get("doc_sha256") or row.get("source_event_id") is None:
            return {"eligible": False, "reason": "NO_STABLE_SOURCE_IDENTITY"}
        return {"eligible": True, "kind": "PDF-HIST"}
    if scope == "CURRENT_AUTHORITATIVE":
        if not row.get("doc_sha256") or row.get("source_observation_id") is None \
                or row.get("source_parse_result_id") is None:
            return {"eligible": False, "reason": "NO_STABLE_SOURCE_IDENTITY"}
        return {"eligible": True, "kind": "PDF-CURRENT"}
    return {"eligible": False, "reason": "SOURCE_IDENTITY_RECONSTRUCTION_FAILED"}
