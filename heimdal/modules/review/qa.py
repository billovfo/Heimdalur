"""Post-application QA sampling (E4, post-MVP). Read-only selection.

- Deterministic: seeded by frozen_batch_id; same batch -> same sample.
- 100% of high-risk applied cases + 10% of normal (floor 3, ceiling 20).
- QA marks (QA_OK / QA_NEEDS_REVIEW) live in controlled lifecycle columns
  (qa_status/qa_note/...); they NEVER mutate links or decision payload.
- QA_NEEDS_REVIEW only flags the case for explicit manual reopen.
"""
from __future__ import annotations

import hashlib
import json

NORMAL_SAMPLE_RATE = 0.10
NORMAL_SAMPLE_MIN = 3
NORMAL_SAMPLE_MAX = 20

QA_OK = "QA_OK"
QA_NEEDS_REVIEW = "QA_NEEDS_REVIEW"


def _seeded_order(batch_id: str, decision_ids: list[int]) -> list[int]:
    scored = []
    for did in decision_ids:
        h = hashlib.sha256(("%s:%s" % (batch_id, did)).encode()).hexdigest()
        scored.append((h, did))
    return [did for _, did in sorted(scored)]


def _sealed_for(conn, case_key: str, packet_hash: str | None) -> dict:
    from . import repository as repo
    case = repo.get_case(conn, case_key)
    versions = (case or {}).get("packet_versions") or []
    if isinstance(versions, str):
        versions = json.loads(versions)
    for v in versions:
        if packet_hash and v.get("packet_hash") == packet_hash:
            return v.get("packet") or {}
    cur = repo.current_packet(case or {})
    return (cur or {}).get("packet") or {}


def risk_flags(conn, decision: dict) -> list[str]:
    """High-risk reasons for one APPLIED decision. No score reads."""
    flags = []
    packet = _sealed_for(conn, decision["case_key"],
                         decision.get("shown_packet_hash"))
    ev = packet.get("evidence") or {}
    if ev.get("contradictory"):
        flags.append("CONTRADICTION_PRESENT")
    alts = decision.get("shown_alternatives") or []
    if isinstance(alts, str):
        alts = json.loads(alts)
    if len(alts) > 1:
        flags.append("MULTI_CANDIDATE")
    if decision.get("supersedes_decision_id") is not None:
        flags.append("REREVIEWED_STALE")
    prior = conn.execute(
        "SELECT decision FROM registry_projection.person_review_decision "
        "WHERE case_key = %s AND decision_id != %s "
        "AND decision IN ('AMBIGUOUS','DEFER') LIMIT 1",
        (decision["case_key"], decision["decision_id"])).fetchone()
    if prior is not None:
        flags.append("PREVIOUS_AMBIGUOUS_DEFER")
    return flags


def sample_applied_batch(conn, batch_id: str) -> list[dict]:
    """Deterministic QA sample for an applied batch."""
    from . import repository as repo
    rows = conn.execute(
        "SELECT decision_id, case_key, reviewer, decided_at, decision, "
        "chosen_canonical_id, reason_code, shown_packet_hash, "
        "shown_alternatives, shown_score_snapshot_ref, evidence_pin, "
        "supersedes_decision_id, applied_projection_run_id, qa_status "
        "FROM %s WHERE frozen_batch_id = %%s AND application_status = 'APPLIED' "
        "ORDER BY decision_id" % repo.DECISION_TABLE, (batch_id,)).fetchall()
    cols = ("decision_id", "case_key", "reviewer", "decided_at", "decision",
            "chosen_canonical_id", "reason_code", "shown_packet_hash",
            "shown_alternatives", "shown_score_snapshot_ref", "evidence_pin",
            "supersedes_decision_id", "applied_projection_run_id", "qa_status")
    decisions = [dict(r) if isinstance(r, dict) else dict(zip(cols, r))
                 for r in rows]
    high, normal = [], []
    for d in decisions:
        flags = risk_flags(conn, d)
        entry = {**d, "risk_flags": flags,
                 "qa_reason": "HIGH_RISK:" + ",".join(flags) if flags
                 else "NORMAL_SAMPLE"}
        (high if flags else normal).append(entry)
    ordered = _seeded_order(batch_id, [e["decision_id"] for e in normal])
    by_id = {e["decision_id"]: e for e in normal}
    n = len(normal)
    k = 0 if n == 0 else min(NORMAL_SAMPLE_MAX,
                             max(NORMAL_SAMPLE_MIN, int(n * NORMAL_SAMPLE_RATE)))
    sampled = [by_id[did] for did in ordered[:k]]
    return high + sampled
