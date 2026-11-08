"""HUMAN-REVIEW-PERSON-v1 application tier (P4).

Runs AFTER the automatic tiers: as the last step of build_entity_links() (D10:
human links are re-applied on every build) and from the review batch apply.
Receives ONLY approved decisions with exact pre-chosen targets. This module
must never import candidate generation or scoring; decisions carry an
opaque score_snapshot_ref used write-only for provenance.

Contract (frozen APPLICATION-SCORE-v1 + conflict precedence):
- automatic LINKED same target -> AGREEMENT (skip, count, no duplicate)
- automatic LINKED different target -> CONFLICT (never overwrite)
- UNRESOLVED/AMBIGUOUS -> apply exact approved target after revalidation
- PERSON-kind hard gate; missing canonical -> stale; SHADOW runs only.
"""
from __future__ import annotations

import json

from psycopg.rows import dict_row

LINK_METHOD = "HUMAN-REVIEW-PERSON-v1"
DECISION_TABLE = "registry_projection.person_review_decision"


def _provenance_text(frozen: dict) -> str:
    """Write-only provenance serialization. Output never read for branching."""
    return json.dumps({
        "review_case_key": frozen["case_key"],
        "decision_id": frozen["decision_id"],
        "decided_at": str(frozen.get("decided_at")),
        "decided_by": frozen.get("decided_by"),
        "source_stable_key": frozen["source_stable_key"],
        "evidence_pin": frozen.get("evidence_pin"),
        "shown_packet_hash": frozen.get("shown_packet_hash"),
        "score_snapshot_ref": frozen.get("score_snapshot_ref"),
        "owner_note": frozen.get("owner_note"),
        "owner_reason": frozen.get("owner_reason"),
    }, ensure_ascii=False)


def run_status(conn, run_id):
    st = conn.execute(
        "SELECT status FROM registry_projection.projection_run "
        "WHERE projection_run_id = %s", (run_id,)).fetchone()
    return st["status"] if isinstance(st, dict) else (st[0] if st else None)


def resolve_keys_in_run(conn, run_id: int, case_key: str) -> list[str]:
    """Resolve a stable case key to source_entity_keys in a given run.

    Exact stable matching only: event/observation identity + normalized name
    equality. Zero or multiple matches -> [] (fail closed, caller marks stale).
    """
    from heimdal.pdfhist import entity_links as el
    parts = case_key.split("|")
    kind = parts[0]
    hits: list = []
    cur = conn.cursor(row_factory=dict_row)
    if kind == "PDF-HIST" and len(parts) == 6:
        _, _sha, event, _occ, norm, _anchor = parts
        rows = cur.execute(
            "SELECT id, entity_normalized, person_name "
            "FROM registry_projection.company_person_role "
            "WHERE projection_run_id = %s AND source_scope = 'HISTORICAL_EVENT' "
            "AND source_event_id = %s", (run_id, int(event))).fetchall()
        hits = [r["id"] for r in rows
                if el.normalize_person_name(r["entity_normalized"] or r["person_name"]) == norm]
    elif kind == "PDF-CURRENT" and len(parts) == 8:
        _, _sha, obs, _par, _pos, role, norm, _anchor = parts
        rows = cur.execute(
            "SELECT id, entity_normalized, person_name, role_type "
            "FROM registry_projection.company_person_role "
            "WHERE projection_run_id = %s AND source_scope = 'CURRENT_AUTHORITATIVE' "
            "AND source_observation_id = %s", (run_id, int(obs))).fetchall()
        hits = [r["id"] for r in rows if r["role_type"] == role
                and el.normalize_person_name(r["entity_normalized"] or r["person_name"]) == norm]
    if len(hits) != 1:
        return []
    return ["ROLE:%s" % hits[0]]


def frozen_item(decision: dict, source_entity_key: str) -> dict:
    """Application input for one approved decision row (all decision columns)."""
    return {
        "case_key": decision["case_key"], "source_stable_key": decision["case_key"],
        "source_entity_key": source_entity_key,
        "canonical_entity_id": decision["chosen_canonical_id"],
        "decision_id": decision["decision_id"],
        "decided_at": str(decision["decided_at"]),
        "decided_by": decision["reviewer"], "evidence_pin": decision["evidence_pin"],
        "shown_packet_hash": decision["shown_packet_hash"],
        "score_snapshot_ref": decision["shown_score_snapshot_ref"],
        "owner_note": decision["owner_note"], "owner_reason": None}


def apply_approved_human_links(projection_conn, run_id: int,
                               frozen_decisions: list[dict],
                               canonical_conn=None) -> dict:
    """Apply approved decisions to a SHADOW candidate run.

    Each item MUST carry an exact pre-resolved `source_entity_key`
    (resolved by the caller from the stable case key in THIS run) and an
    exact `canonical_entity_id`. This function never searches, ranks, or
    infers anything. Returns counts plus `outcomes`: decision_id ->
    applied | agreement | conflict | missing | non_person.
    """
    stats = {"applied": 0, "agreements": 0, "conflicts": 0, "stale": 0,
             "outcomes": {}}
    status = run_status(projection_conn, run_id)
    if status != "SHADOW":
        raise RuntimeError("refusing to mutate %s projection %s (SHADOW only)"
                           % (status or "missing", run_id))

    def outcome(frozen, what, counter):
        stats["outcomes"][frozen["decision_id"]] = what
        stats[counter] += 1

    for frozen in frozen_decisions:
        target = frozen["canonical_entity_id"]
        skey = frozen["source_entity_key"]
        # revalidate: source still unresolved in this run
        cur = projection_conn.execute(
            "SELECT link_status, canonical_entity_id "
            "FROM registry_projection.entity_link "
            "WHERE projection_run_id = %s AND entity_type = 'PERSON' "
            "AND source_entity_key = %s", (run_id, skey)).fetchone()
        if cur is None:
            outcome(frozen, "missing", "stale")
            continue
        cur_status = cur["link_status"] if isinstance(cur, dict) else cur[0]
        cur_cid = cur["canonical_entity_id"] if isinstance(cur, dict) else cur[1]
        if cur_status == "LINKED" and cur_cid == target:
            outcome(frozen, "agreement", "agreements")
            continue
        if cur_status == "LINKED":
            outcome(frozen, "conflict", "conflicts")
            continue
        if canonical_conn is not None:
            crow = canonical_conn.execute(
                "SELECT entity_kind FROM canonical_entity "
                "WHERE canonical_entity_id = %s", (target,)).fetchone()
            kind = crow["entity_kind"] if isinstance(crow, dict) \
                else (crow[0] if crow else None)
            if kind != "PERSON":
                outcome(frozen, "non_person", "stale")
                continue
        n = projection_conn.execute(
            "UPDATE registry_projection.entity_link SET link_status = 'LINKED', "
            "canonical_entity_id = %s, link_method = %s, match_metadata = "
            "COALESCE(match_metadata,'{}'::jsonb) || %s::jsonb "
            "WHERE projection_run_id = %s AND entity_type = 'PERSON' "
            "AND source_entity_key = %s AND link_status != 'LINKED'",
            (target, LINK_METHOD, _provenance_text(frozen), run_id, skey)).rowcount
        if n == 1:
            outcome(frozen, "applied", "applied")
        else:  # linked by someone else since the read above
            outcome(frozen, "missing", "stale")
    return stats


def reapply_current_decisions(projection_conn, run_id: int, canonical_conn):
    """D10 human tier of a link build: re-apply every case's newest decision
    when it is an APPROVE_LINK the owner already applied (status APPLIED).

    New approvals still go through the review batch (freeze, dry-run,
    apply). Decision rows are only read; per-decision outcomes go to the
    run's metrics_json. None when the DB has no review tables or no such
    decision.
    """
    cur = projection_conn.cursor(row_factory=dict_row)
    if cur.execute("SELECT to_regclass(%s) AS t",
                   (DECISION_TABLE,)).fetchone()["t"] is None:
        return None
    rows = cur.execute(
        "SELECT * FROM %s WHERE decision_id IN (SELECT DISTINCT ON (case_key) "
        "decision_id FROM %s ORDER BY case_key, decision_id DESC) "
        "AND decision = 'APPROVE_LINK' AND application_status = 'APPLIED' "
        "ORDER BY decision_id" % (DECISION_TABLE, DECISION_TABLE)).fetchall()
    if not rows:
        return None
    items, unresolvable = [], []
    for d in rows:
        keys = resolve_keys_in_run(projection_conn, run_id, d["case_key"])
        if len(keys) == 1:
            items.append(frozen_item(d, keys[0]))
        else:
            unresolvable.append(d["decision_id"])
    stats = apply_approved_human_links(projection_conn, run_id, items, canonical_conn)
    for did in unresolvable:
        stats["outcomes"][did] = "unresolvable"
        stats["stale"] += 1
    projection_conn.execute(
        "UPDATE registry_projection.projection_run SET metrics_json = "
        "COALESCE(metrics_json,'{}'::jsonb) || %s::jsonb WHERE projection_run_id = %s",
        (json.dumps({"human_review_tier": stats}, default=str), run_id))
    return stats
