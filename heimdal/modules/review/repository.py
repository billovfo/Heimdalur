"""Review persistence repository (P1). Two tables, additive migration.

Transaction discipline (frozen P0.2b): this module NEVER commits. The caller
owns the transaction — normally `heimdal.modules.shared.db.tx()`. All writes
in one operation commit atomically or roll back together; failures propagate
loudly. The single exception is nothing: even apply_review_migration leaves
committing to its caller (tests use autocommit connections; operations use tx).

Payload discipline (frozen DECISION-IMMUTABILITY-v1):
- person_review_decision payload columns: INSERT-only. Repository exposes NO
  update path for them; correction = superseding row.
- lifecycle columns (applied_projection_run_id, stale_reason/at,
  application_status, frozen_batch_id): ONLY via mark_applied/mark_stale/
  mark_conflict/attach_batch/supersede_older. Each names the status it
  expects and raises LifecycleConflict when the row is not in it. Direct
  UPDATE elsewhere is a test failure. The DB trigger from
  review_immutability_migration.sql refuses any payload UPDATE.
- decision lifecycle (audit 2026-11 D9): PENDING -> FROZEN -> APPLIED |
  CONFLICT | STALE. A newer decision on the same case turns older live ones
  STALE (stale_reason SUPERSEDED). There is no case lifecycle: the case row
  holds the packet versions, and its status is only ever written QUEUED.
- packet_versions: append-only via materialize_packet(); sealed entries are
  never mutated (repository re-reads and compares before append).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

CASE_TABLE = "registry_projection.person_review_case"
DECISION_TABLE = "registry_projection.person_review_decision"

PAYLOAD_COLUMNS = (
    "decision", "chosen_canonical_id", "reviewer", "decided_at",
    "owner_note", "shown_packet_hash", "shown_alternatives",
    "shown_score_snapshot_ref", "evidence_pin", "reason_code",
)

_PDFHIST = Path(__file__).resolve().parents[2] / "pdfhist"
MIGRATION_PATH = _PDFHIST / "review_migration.sql"
MIGRATION_PATHS = (MIGRATION_PATH, _PDFHIST / "review_immutability_migration.sql")


class LifecycleConflict(RuntimeError):
    """A lifecycle UPDATE found the row in another status (lost a race)."""


def apply_review_migration(conn) -> list[str]:
    """Execute the review migrations idempotently. DEV/LOCAL ONLY."""
    from heimdal.pdfhist.migrate import split_statements
    statements = [st for path in MIGRATION_PATHS
                  for st in split_statements(path.read_text(encoding="utf-8"))]
    has_link_table = conn.execute(
        "SELECT to_regclass('registry_projection.entity_link')").fetchone()
    has_link_table = has_link_table[0] if not isinstance(has_link_table, dict) \
        else has_link_table["to_regclass"]
    executed = []
    for statement in statements:
        if "entity_link_link_method_check" in statement and not has_link_table:
            continue  # projection tables absent (bare test DB): nothing to extend
        conn.execute(statement)
        executed.append(" ".join(statement.split())[:60])
    return executed


def packet_hash(packet: dict) -> str:
    """Stable sha256 over a sealed packet (shown_packet_hash)."""
    blob = json.dumps(packet, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def get_case(conn, case_key: str):
    row = conn.execute(
        "SELECT case_key, source_kind, norm_name, source_name_exact, "
        "company_anchor, role_type, source_context, bucket, occurrence_refs, "
        "packet_versions, current_packet_version, status, queue_rank, "
        "h_class_estimate, candidate_count, superseded_by "
        "FROM %s WHERE case_key = %%s" % CASE_TABLE, (case_key,)).fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        out = dict(row)
    else:
        cols = ("case_key", "source_kind", "norm_name", "source_name_exact",
                "company_anchor", "role_type", "source_context", "bucket",
                "occurrence_refs", "packet_versions", "current_packet_version",
                "status", "queue_rank", "h_class_estimate", "candidate_count",
                "superseded_by")
        out = dict(zip(cols, row))
    for k in ("occurrence_refs", "packet_versions"):
        if isinstance(out.get(k), str):
            out[k] = json.loads(out[k])
    return out


def current_packet(case_row: dict):
    """Return the current sealed packet version entry or None."""
    versions = case_row.get("packet_versions") or []
    if isinstance(versions, str):
        versions = json.loads(versions)
    want = case_row.get("current_packet_version", 0)
    for v in versions:
        if v.get("packet_version") == want:
            return v
    return None


def materialize_packet(conn, *, case_key: str, packet: dict,
                       reason: str = "first-open") -> dict:
    """Idempotent first-open freeze. Re-reads; never duplicates or mutates.

    Returns the sealed version entry (existing or newly appended).
    """
    existing = get_case(conn, case_key)
    if existing is None:
        raise KeyError("case row must be created before packet materialization")
    cur = current_packet(existing)
    if cur is not None:
        return cur
    sealed = {"packet_version": 1, "reason": reason,
              "packet": packet, "packet_hash": packet_hash(packet)}
    n = conn.execute(
        "UPDATE %s SET packet_versions = %%s::jsonb, current_packet_version = 1 "
        "WHERE case_key = %%s AND current_packet_version = 0" % CASE_TABLE,
        (json.dumps([sealed]), case_key)).rowcount
    if n != 1:  # E-11: another transaction sealed v1 first; that one counts
        return current_packet(get_case(conn, case_key))
    return sealed


def seal_packet(conn, *, case_key: str, packet: dict) -> dict:
    """The packet a reviewer sees is the case's current sealed version (E-7).

    Locks the case row, then seals v1, keeps the current version when the
    content is unchanged, or appends a new version when it changed.
    """
    conn.execute("SELECT 1 FROM %s WHERE case_key = %%s FOR UPDATE" % CASE_TABLE,
                 (case_key,))
    cur = current_packet(get_case(conn, case_key))
    if cur is None:
        return materialize_packet(conn, case_key=case_key, packet=packet)
    if cur["packet_hash"] == packet_hash(packet):
        return cur
    return supersede_packet(conn, case_key=case_key, packet=packet,
                            reason="regenerated")


def supersede_packet(conn, *, case_key: str, packet: dict, reason: str) -> dict:
    """Append a new sealed version; old versions retained untouched."""
    existing = get_case(conn, case_key)
    if existing is None:
        raise KeyError("unknown case")
    versions = existing.get("packet_versions") or []
    if isinstance(versions, str):
        versions = json.loads(versions)
    sealed = {"packet_version": len(versions) + 1, "reason": reason,
              "packet": packet, "packet_hash": packet_hash(packet)}
    versions.append(sealed)
    conn.execute(
        "UPDATE %s SET packet_versions = %%s::jsonb, current_packet_version = %%s, "
        "status = 'QUEUED', status_updated_at = now() WHERE case_key = %%s"
        % CASE_TABLE,
        (json.dumps(versions), sealed["packet_version"], case_key))
    return sealed


def add_occurrence_ref(conn, *, case_key: str, run_id: int,
                         person_role_id: int) -> None:
    """Record an occurrence reference (idempotent, traceability only).
    One atomic UPDATE, so concurrent callers never drop each other's refs."""
    ref = json.dumps([{"run_id": run_id, "person_role_id": person_role_id}])
    conn.execute(
        "UPDATE %s SET occurrence_refs = occurrence_refs || %%s::jsonb "
        "WHERE case_key = %%s AND NOT occurrence_refs @> %%s::jsonb"
        % CASE_TABLE, (ref, case_key, ref))


def latest_decision_id(conn, case_key: str):
    """The case's newest decision, or None. Newest = highest decision_id:
    insert order, serialised per case by seal_packet's row lock. decided_at
    is the transaction start time, so two concurrent decisions can invert it."""
    row = conn.execute(
        "SELECT decision_id FROM %s WHERE case_key = %%s "
        "ORDER BY decision_id DESC LIMIT 1" % DECISION_TABLE,
        (case_key,)).fetchone()
    if row is None:
        return None
    return row["decision_id"] if isinstance(row, dict) else row[0]


def insert_decision(conn, *, case_key: str, reviewer: str, decision: str,
                    chosen_canonical_id=None, owner_note=None,
                    reason_code=None, shown_packet_hash: str,
                    shown_alternatives, shown_score_snapshot_ref: str,
                    evidence_pin: str, supersedes_decision_id=None) -> int:
    """Write an immutable decision payload row. No update path exists."""
    if decision == "APPROVE_LINK" and chosen_canonical_id is None:
        raise ValueError("APPROVE_LINK requires an explicitly chosen canonical")
    row = conn.execute(
        "INSERT INTO %s (case_key, reviewer, decision, chosen_canonical_id, "
        "owner_note, reason_code, shown_packet_hash, shown_alternatives, "
        "shown_score_snapshot_ref, evidence_pin, supersedes_decision_id) "
        "VALUES (%%s,%%s,%%s,%%s,%%s,%%s,%%s,%%s::jsonb,%%s,%%s,%%s) "
        "RETURNING decision_id" % DECISION_TABLE,
        (case_key, reviewer, decision, chosen_canonical_id, owner_note,
         reason_code, shown_packet_hash, json.dumps(shown_alternatives or []),
         shown_score_snapshot_ref, evidence_pin, supersedes_decision_id)).fetchone()
    return row["decision_id"] if isinstance(row, dict) else row[0]


def _lifecycle_update(conn, decision_id: int, assignments: dict,
                      func_name: str, expect=None) -> None:
    """One lifecycle UPDATE. `expect`: the status (or statuses) the row must
    have. Another status, or no such row, raises LifecycleConflict (E-11)."""
    allowed = {"applied_projection_run_id", "stale_reason", "stale_at",
               "application_status", "frozen_batch_id",
               "qa_status", "qa_reviewer", "qa_note", "qa_decided_at"}
    if set(assignments) - allowed:
        raise ValueError("%s: forbidden lifecycle column" % func_name)
    sets = ", ".join("%s = %%s" % k for k in assignments)
    where, params = "decision_id = %s", [decision_id]
    if expect is not None:
        where += " AND application_status = ANY(%s)"
        params.append([expect] if isinstance(expect, str) else list(expect))
    n = conn.execute(
        "UPDATE %s SET %s WHERE %s" % (DECISION_TABLE, sets, where),
        (*assignments.values(), *params)).rowcount
    if n != 1:
        raise LifecycleConflict("%s: decision %s is not %s"
                                % (func_name, decision_id, expect or "present"))


def mark_applied(conn, decision_id: int, run_id: int, expect="FROZEN") -> None:
    _lifecycle_update(conn, decision_id,
                      {"applied_projection_run_id": run_id,
                       "application_status": "APPLIED"}, "mark_applied", expect)


def mark_stale(conn, decision_id: int, reason: str,
               expect=("PENDING", "FROZEN")) -> None:
    from datetime import datetime, timezone
    _lifecycle_update(conn, decision_id,
                      {"stale_reason": reason, "stale_at": datetime.now(timezone.utc),
                       "application_status": "STALE"}, "mark_stale", expect)


def mark_conflict(conn, decision_id: int, expect=("PENDING", "FROZEN")) -> None:
    """The target run links the source to another canonical: never applied."""
    from datetime import datetime, timezone
    _lifecycle_update(conn, decision_id,
                      {"stale_reason": "TARGET-CONFLICT",
                       "stale_at": datetime.now(timezone.utc),
                       "application_status": "CONFLICT"}, "mark_conflict", expect)


def attach_batch(conn, decision_id: int, batch_id: str, expect="PENDING") -> None:
    _lifecycle_update(conn, decision_id,
                      {"frozen_batch_id": batch_id,
                       "application_status": "FROZEN"}, "attach_batch", expect)


def supersede_older(conn, case_key: str | None = None) -> list[int]:
    """E-2: only a case's newest decision is live. Older PENDING, FROZEN or
    APPLIED decisions become STALE (SUPERSEDED). case_key=None: every case."""
    rows = conn.execute(
        "UPDATE %s d SET application_status = 'STALE', "
        "stale_reason = 'SUPERSEDED', stale_at = now() "
        "WHERE d.application_status IN ('PENDING','FROZEN','APPLIED') "
        "AND (%%(ck)s::text IS NULL OR d.case_key = %%(ck)s) "
        "AND EXISTS (SELECT 1 FROM %s n WHERE n.case_key = d.case_key "
        "AND n.decision_id > d.decision_id) "
        "RETURNING d.decision_id" % (DECISION_TABLE, DECISION_TABLE),
        {"ck": case_key}).fetchall()
    return [r["decision_id"] if isinstance(r, dict) else r[0] for r in rows]


def mark_qa(conn, decision_id: int, status: str, reviewer: str,
            note: str | None = None) -> None:
    """Controlled QA mark (E4). QA_OK / QA_NEEDS_REVIEW only. Never touches
    payload or links; QA_NEEDS_REVIEW alone reopens nothing (explicit
    reopen_case required)."""
    from datetime import datetime, timezone
    if status not in ("QA_OK", "QA_NEEDS_REVIEW"):
        raise ValueError("invalid qa status %r" % (status,))
    _lifecycle_update(conn, decision_id,
                      {"qa_status": status, "qa_reviewer": reviewer,
                       "qa_note": note,
                       "qa_decided_at": datetime.now(timezone.utc)}, "mark_qa")


def reopen_case(conn, case_key: str) -> None:
    """Explicit manual reopen after QA_NEEDS_REVIEW (E4). Resets case status
    to QUEUED only; decisions, packets, scores, queue logic untouched."""
    conn.execute(
        "UPDATE %s SET status = 'QUEUED', status_updated_at = now() "
        "WHERE case_key = %%s" % CASE_TABLE, (case_key,))
