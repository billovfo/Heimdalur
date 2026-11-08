"""PERSON-REVIEW-SCORE-v1: deterministic rules-based review confidence 0-100.

Pure function score(packet) -> (value, components, weaknesses). No DB, no
imports from entity_links/human_links/review.service. Not a probability,
never an auto-link threshold. F3a source-company context = 0 by contract.
"""
from __future__ import annotations

SCORE_VERSION = "PERSON-REVIEW-SCORE-v1"

FORBIDDEN_FEATURES = (
    "network_centrality", "social_graph", "ownership_overlap",
    "unrelated_temporal_overlap", "popularity",
)


def score(packet: dict) -> dict:
    """Score a frozen review packet. Returns value/components/weaknesses."""
    for f in FORBIDDEN_FEATURES:
        if f in packet:
            raise ValueError("forbidden feature in packet: %s" % f)
    ev = packet.get("evidence_signals", {})
    n_alt = int(packet.get("alternative_count", 0))
    n_contr = len((packet.get("evidence") or {}).get("contradictory", []))
    h_class = packet.get("h_class", "H0")
    comp = {}
    total = 0

    def add(key, pts):
        nonlocal total
        comp[key] = pts
        total += pts

    add("F1_name", 16 if ev.get("exact_raw_name") else (10 if ev.get("normalized_name") else 0))
    add("F2_variant", 10 if ev.get("variant_quality") == "strong"
        else (8 if ev.get("variant_quality") else 0))
    add("F3a_context", 0)  # binding: source-company context carries no weight
    f3b = ev.get("independent_company")
    add("F3b_independent", 25 if f3b == "full" else (12 if f3b == "partial" else 0))
    add("F4_role", 10 if ev.get("role_agreement") else 0)
    add("F5_temporal", 8 if ev.get("temporal_agreement") else 0)
    add("F6_official", 10 if ev.get("official_external") else 0)
    add("F7_raddr", 5 if ev.get("raddr_strict") else 0)
    if n_alt == 0:
        add("F8_unique", 5 if h_class in ("H5", "H4") else (3 if h_class == "H3" else 0))
    else:
        comp["F8_unique"] = 0
    add("P1_alternatives", -min(8 * n_alt, 24))
    add("P2_contradictions", -min(25 * n_contr, 50))

    total = max(0, min(100, total))
    if n_contr > 0:
        total = min(total, 49)
    if n_alt >= 2:
        total = min(total, 49)
    if h_class in ("H1", "H0"):
        total = min(total, 35)
    if h_class == "H2":
        total = min(total, 55)

    weaknesses = []
    if not ev.get("independent_company"):
        weaknesses.append("no independent company evidence")
    if n_alt:
        weaknesses.append("%d alternative candidate(s)" % n_alt)
    if n_contr:
        weaknesses.append("%d contradiction(s)" % n_contr)
    if h_class in ("H1", "H0"):
        weaknesses.append("name/variant only (weak source)")
    return {"score_version": SCORE_VERSION, "value": total,
            "components": comp, "weaknesses": weaknesses}
