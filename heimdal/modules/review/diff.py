"""What-changed diff for re-review (E2, post-MVP).

Compares two SEALED packet version entries only (never live readers).
Deterministic: same inputs -> same categories + same summary lines.
Presentation-order differences are ignored (sets, not lists).
"""
from __future__ import annotations

MATERIAL_CATEGORIES = (
    "CANDIDATE_ADDED",
    "CANDIDATE_REMOVED",
    "CANDIDATE_CHANGED",
    "NEW_SUPPORTING_EVIDENCE",
    "REMOVED_SUPPORTING_EVIDENCE",
    "NEW_CONTRADICTION",
    "REMOVED_CONTRADICTION",
    "ROLE_CONTEXT_CHANGED",
    "TEMPORAL_CONTEXT_CHANGED",
    "SOURCE_CONTEXT_CHANGED",
    "CANONICAL_CHANGED",
    "SCORE_CHANGED",
    "EVIDENCE_CLASS_CHANGED",
    "NO_MATERIAL_CHANGE",
)


def _freeze(value):
    """Normalize an evidence/candidate entry to a hashable, order-free form."""
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _cand_map(packet: dict) -> dict:
    out = {}
    for c in packet.get("candidates") or []:
        cid = c.get("canonical_entity_id") if isinstance(c, dict) else c
        out[cid] = c
    return out


def _score_of(packet: dict):
    try:
        from . import scoring, service
        return scoring.score(service.score_signals(packet))
    except Exception:
        return None


def diff_packets(old_sealed: dict, new_sealed: dict) -> dict:
    """Diff two sealed version entries -> {categories, lines, score_old/new}."""
    old = (old_sealed or {}).get("packet") or {}
    new = (new_sealed or {}).get("packet") or {}
    cats: set[str] = set()
    lines: list[str] = []

    old_c, new_c = _cand_map(old), _cand_map(new)
    for cid in sorted(set(new_c) - set(old_c), key=str):
        cats.add("CANDIDATE_ADDED")
        lines.append("+ Candidate #%s added" % cid)
    for cid in sorted(set(old_c) - set(new_c), key=str):
        cats.add("CANDIDATE_REMOVED")
        lines.append("- Candidate #%s removed" % cid)
    for cid in sorted(set(old_c) & set(new_c), key=str):
        if _freeze(old_c[cid]) != _freeze(new_c[cid]):
            cats.add("CANDIDATE_CHANGED")
            lines.append("~ Candidate #%s changed" % cid)

    for key, add_cat, rem_cat, add_mark, rem_mark, label in (
        ("supporting", "NEW_SUPPORTING_EVIDENCE", "REMOVED_SUPPORTING_EVIDENCE",
         "+", "-", "supporting evidence"),
        ("contradictory", "NEW_CONTRADICTION", "REMOVED_CONTRADICTION",
         "!", "-", "contradiction"),
    ):
        old_e = {(json_key(e)) for e in ((old.get("evidence") or {}).get(key) or [])}
        new_e = {(json_key(e)) for e in ((new.get("evidence") or {}).get(key) or [])}
        for e in sorted(new_e - old_e):
            cats.add(add_cat)
            lines.append("%s New %s: %s" % (add_mark, label, e))
        for e in sorted(old_e - new_e):
            cats.add(rem_cat)
            lines.append("%s Removed %s: %s" % (rem_mark, label, e))

    old_s, new_s = old.get("source") or {}, new.get("source") or {}
    if old_s.get("role") != new_s.get("role"):
        cats.add("ROLE_CONTEXT_CHANGED")
        lines.append("~ Role: %s -> %s" % (old_s.get("role"), new_s.get("role")))
    src_keys = ("name_exact", "normalized", "scope", "document_id", "event_id",
                "observation_id", "parse_id", "company_anchor")
    if any(old_s.get(k) != new_s.get(k) for k in src_keys):
        cats.add("SOURCE_CONTEXT_CHANGED")
        lines.append("~ Source context changed")
    old_t = {(k, _freeze(v)) for k, v in (old.get("temporal") or {}).items()} \
        if isinstance(old.get("temporal"), dict) else _freeze(old.get("temporal"))
    new_t = {(k, _freeze(v)) for k, v in (new.get("temporal") or {}).items()} \
        if isinstance(new.get("temporal"), dict) else _freeze(new.get("temporal"))
    if old_t != new_t and (old.get("temporal") is not None or new.get("temporal") is not None):
        cats.add("TEMPORAL_CONTEXT_CHANGED")
        lines.append("! Temporal evidence changed")
    if _freeze(old.get("canonical")) != _freeze(new.get("canonical")) and \
            (old.get("canonical") is not None or new.get("canonical") is not None):
        cats.add("CANONICAL_CHANGED")
        lines.append("~ Canonical data changed")
    if old.get("h_class") != new.get("h_class"):
        cats.add("EVIDENCE_CLASS_CHANGED")
        lines.append("~ Evidence class: %s -> %s" % (old.get("h_class"), new.get("h_class")))

    s_old, s_new = _score_of(old), _score_of(new)
    v_old = s_old.get("value") if isinstance(s_old, dict) else None
    v_new = s_new.get("value") if isinstance(s_new, dict) else None
    if v_old != v_new:
        cats.add("SCORE_CHANGED")
        lines.append("Score: %s -> %s" % (v_old, v_new))

    if not cats:
        cats.add("NO_MATERIAL_CHANGE")
        lines.append("No material change since last review.")
    categories = sorted(cats)
    return {
        "categories": categories,
        "lines": sorted(lines),
        "score_old": v_old,
        "score_new": v_new,
        "old_packet_hash": old_sealed.get("packet_hash"),
        "new_packet_hash": new_sealed.get("packet_hash"),
    }


def json_key(entry) -> str:
    import json as _json
    try:
        return _json.dumps(entry, sort_keys=True, ensure_ascii=False)
    except Exception:
        return str(entry)
