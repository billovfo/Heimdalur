"""Person human-review routes (P2 read-only, P3 decisions).

FAIL-CLOSED: every route 404s unless review_allowed(). HTML-first like the
entities module; JSON via Accept: application/json reusing the same service.
No bulk-decide, no threshold-approve endpoints exist by design.
"""
from __future__ import annotations

import os

from flask import Blueprint, abort, jsonify, request

from .repository import LifecycleConflict

review_bp = Blueprint("review", __name__)

# E-6: the stored reviewer is set here, never taken from the request.
_REVIEWER = "local-owner"


def review_allowed() -> bool:
    """Enablement: explicit local/dev opt-in only. Default closed."""
    return os.environ.get("HEIMDAL_REVIEW_UI", "") == "1"


def _wants_json() -> bool:
    return "application/json" in (request.headers.get("Accept") or "")


def _projection_conn():
    import psycopg
    from psycopg.rows import dict_row
    from heimdal.pdfhist import consumer
    return psycopg.connect(consumer.projection_dsn(), row_factory=dict_row)


def _canonical_conn():
    import psycopg
    from psycopg.rows import dict_row
    from heimdal.config import DB_URI
    return psycopg.connect(DB_URI, row_factory=dict_row)


@review_bp.get("/persons/review")
def queue():
    if not review_allowed():
        abort(404)
    from . import service
    conn = _projection_conn()
    try:
        run_id = _active_run(conn)
        q = service.queue_summaries(conn, run_id)
        q["coverage"] = service.coverage_health(conn, run_id)
    finally:
        conn.close()
    if _wants_json():
        return jsonify({"reviewable": q["reviewable"],
                        "unresolved_total": q["unresolved_total"],
                        "not_eligible": q["not_eligible"],
                        "coverage": q["coverage"],
                        "sample": q["cases"][:50]})
    from flask import render_template
    return render_template("review_queue.html", queue=q)


@review_bp.errorhandler(LifecycleConflict)
def _lifecycle_conflict(exc):
    # E-9/E-11: a decision changed status under us -> 409, never 500
    return jsonify({"status": "CONFLICT", "reason": str(exc)}), 409


def _open_case(conn, canon, run_id: int, person_role_id: int):
    """Build, check and seal the packet a reviewer sees (E-7, E-9, E-10).

    Returns (packet, case_key, sealed). The case must be an UNRESOLVED,
    eligible queue item of the ACTIVE run.
    """
    from . import repository as repo
    from . import service
    try:
        packet = service.build_packet(conn, canon, run_id, person_role_id)
    except KeyError:
        abort(404)
    if not service.reviewable(conn, run_id, person_role_id):
        abort(409, "not an UNRESOLVED, eligible queue item")
    try:
        case_key = _case_key_for(conn, run_id, packet)
    except ValueError:
        abort(409, "stable source identity not derivable (NOT ELIGIBLE)")
    _ensure_case(conn, case_key, packet)
    return packet, case_key, repo.seal_packet(conn, case_key=case_key, packet=packet)


@review_bp.get("/persons/review/<int:person_role_id>")
def case(person_role_id: int):
    if not review_allowed():
        abort(404)
    from . import diff as packet_diff
    from . import repository as repo
    from heimdal.modules.shared import db as shared_db
    from heimdal.pdfhist import consumer
    # The first open seals packet v1; a changed packet seals a new version.
    with shared_db.tx(consumer.projection_dsn()) as conn:
        run_id = _active_run(conn)
        canon = _canonical_conn()
        try:
            packet, case_key, sealed = _open_case(conn, canon, run_id, person_role_id)
        finally:
            canon.close()
        versions = repo.get_case(conn, case_key)["packet_versions"]
        change = (packet_diff.diff_packets(versions[-2], versions[-1])
                  if len(versions) >= 2 else None)
    if _wants_json():
        return jsonify({**packet, "case_key": case_key,
                        "packet_hash": sealed["packet_hash"],
                        "change_since_last_review": change})
    from flask import render_template
    return render_template("review_case.html", packet=packet, change=change,
                           packet_hash=sealed["packet_hash"])


@review_bp.get("/persons/review/decided")
def decided():
    if not review_allowed():
        abort(404)
    conn = _projection_conn()
    try:
        from . import repository as repo
        rows = conn.execute(
            "SELECT decision_id, case_key, reviewer, decided_at, decision, "
            "chosen_canonical_id, reason_code, application_status "
            "FROM %s ORDER BY decided_at DESC LIMIT 200" % repo.DECISION_TABLE
        ).fetchall()
    finally:
        conn.close()
    items = [dict(r)
        for r in rows]
    if _wants_json():
        return jsonify({"decisions": items})
    from flask import render_template
    return render_template("review_decided.html", decisions=items)


@review_bp.post("/persons/review/<int:person_role_id>/decision")
def decide(person_role_id: int):
    if not review_allowed():
        abort(404)
    from . import repository as repo
    from . import service
    from . import reasons as reason_codes
    decision = request.form.get("decision", "")
    if decision not in ("APPROVE_LINK", "REJECT", "AMBIGUOUS", "DEFER"):
        abort(400)
    if request.form.get("no_match") == "1":
        decision, reason_code = reason_codes.normalize_no_match()
    else:
        try:
            reason_code = reason_codes.validate(
                decision, request.form.get("reason_code"))
        except ValueError:
            abort(400)
    chosen = request.form.get("chosen_canonical_id") or None
    chosen_id = int(chosen) if chosen and chosen.isdigit() else None
    if decision == "APPROVE_LINK" and chosen_id is None:
        abort(400)  # choice required, never implicit candidate #1
    shown_hash = request.form.get("shown_packet_hash", "")
    if not shown_hash:
        abort(400)  # E-7: the form posts the hash of the packet it showed
    from heimdal.modules.shared import db as shared_db
    from heimdal.pdfhist import consumer
    with shared_db.tx(consumer.projection_dsn()) as conn:
        run_id = _active_run(conn)
        canon = _canonical_conn()
        try:
            packet, case_key, sealed = _open_case(conn, canon, run_id, person_role_id)
            if sealed["packet_hash"] != shown_hash:
                abort(409, "the case changed since it was shown; reload it")
            shown = [c["canonical_entity_id"] for c in packet["candidates"]]
            if chosen_id is not None:
                if chosen_id not in shown:
                    abort(400)  # D15: only a candidate the packet showed
                from . import gates
                try:
                    gates.assert_person_kind(canon, chosen_id)
                except ValueError:
                    abort(400)  # COMPANY canonical cannot be approved as PERSON
        finally:
            canon.close()
        repo.add_occurrence_ref(conn, case_key=case_key, run_id=run_id,
                                person_role_id=person_role_id)
        did = repo.insert_decision(
            conn, case_key=case_key, reviewer=_REVIEWER, decision=decision,
            chosen_canonical_id=chosen_id,
            owner_note=request.form.get("note"),
            reason_code=reason_code,
            shown_packet_hash=sealed["packet_hash"],
            shown_alternatives=shown,
            shown_score_snapshot_ref=request.form.get("score_ref", "UNSCORED"),
            evidence_pin="run:%s/role:%s" % (run_id, person_role_id),
            supersedes_decision_id=repo.latest_decision_id(conn, case_key))
        repo.supersede_older(conn, case_key)  # E-2: the newest decision wins
        nxt = None if _wants_json() else service.next_queue_role(
            conn, run_id, person_role_id)
    if _wants_json():
        return jsonify({"decision_id": did, "case_key": case_key})
    from flask import redirect
    return redirect("/persons/review/%d" % nxt if nxt else "/persons/review")


def _case_key_for(conn, run_id: int, packet: dict) -> str:
    from . import cases as case_keys
    src = packet["source"]
    anchor = src.get("company_anchor")
    norm = src.get("normalized")
    if src["scope"] == "HISTORICAL_EVENT":
        sha = _doc_sha(conn, src.get("observation_id"))
        occ = _persisted_occ_index(conn, run_id, packet["person_role_id"], norm)
        return case_keys.hist_case_key(sha, src.get("event_id"), occ, norm,
                                       anchor)
    sha = _doc_sha(conn, src.get("observation_id"))
    pos = _persisted_snap_position(conn, run_id, packet["person_role_id"])
    return case_keys.current_case_key(sha, src.get("observation_id"),
                                      src.get("parse_id"), pos,
                                      src.get("role"), norm, anchor)


def _doc_sha(conn, observation_id):
    from . import service
    return service._doc_sha(conn, observation_id)


def _persisted_occ_index(conn, run_id, role_id, norm):
    row = conn.execute(
        "SELECT source_observation_id, source_parse_result_id "
        "FROM registry_projection.company_person_role "
        "WHERE projection_run_id = %s AND id = %s", (run_id, role_id)).fetchone()
    obs = row["source_observation_id"]
    par = row["source_parse_result_id"]
    srow = conn.execute("SELECT structured_data FROM pdf_parse_result WHERE id = %s",
                        (par,)).fetchone()
    import json as _json
    sd = srow["structured_data"]
    if isinstance(sd, str):
        sd = _json.loads(sd or "{}")
    for idx, ch in enumerate(sd.get("changes") or []):
        if (ch.get("entity_normalized") or ch.get("entity") or "").strip().lower() == norm:
            return idx
    raise ValueError("occurrence position not derivable; NOT ELIGIBLE")


def _persisted_snap_position(conn, run_id, role_id):
    """E-12: the person's index in its snapshot's board/management list, read
    from the persisted parse result, so other roles in the run never shift it."""
    from heimdal.pdfhist.projection import _norm_text  # the builder's own rule
    row = conn.execute(
        "SELECT role_type, entity_normalized, source_parse_result_id "
        "FROM registry_projection.company_person_role "
        "WHERE projection_run_id = %s AND id = %s", (run_id, role_id)).fetchone()
    srow = conn.execute("SELECT structured_data FROM pdf_parse_result WHERE id = %s",
                        (row["source_parse_result_id"],)).fetchone()
    import json as _json
    sd = (srow or {}).get("structured_data") or {}
    if isinstance(sd, str):
        sd = _json.loads(sd or "{}")
    field = {"BOARD": "board", "MANAGEMENT": "management"}.get(row["role_type"])
    for idx, person in enumerate(sd.get(field) or []):
        name = person.get("name") if isinstance(person, dict) else str(person)
        if _norm_text(name) == row["entity_normalized"]:
            return idx
    raise ValueError("snapshot position not derivable; NOT ELIGIBLE")


@review_bp.get("/persons/review/export")
def export_csv():
    if not review_allowed():
        abort(404)
    from heimdal.modules.shared.csv_export import csv_response
    conn = _projection_conn()
    try:
        from . import repository as repo
        rows = conn.execute(
            "SELECT d.decision_id, d.case_key, d.reviewer, d.decided_at, d.decision, "
            "d.chosen_canonical_id, d.reason_code, d.owner_note, d.shown_packet_hash, "
            "d.application_status, c.source_name_exact AS source_name, "
            "c.company_anchor AS company, c.role_type AS role "
            "FROM %s d JOIN %s c ON c.case_key = d.case_key "
            "ORDER BY d.decided_at" % (repo.DECISION_TABLE, repo.CASE_TABLE)).fetchall()
    finally:
        conn.close()
    # owner_decision/owner_note_out are blank columns for the owner to fill in
    return csv_response(rows, [
        "decision_id", "case_key", "reviewer", "decided_at", "decision",
        "chosen_canonical_id", "reason_code", "owner_note",
        "shown_packet_hash", "application_status", "source_name",
        "company", "role", "owner_decision", "owner_note_out"],
        "heimdal_person_review_decisions.csv")


def _json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400)
    return body


def _target_run(conn, raw) -> int:
    """E-1/E-3: freeze, dry-run and apply all name the SHADOW run they target."""
    from heimdal.pdfhist.human_links import run_status
    try:
        run_id = int(raw)
    except (TypeError, ValueError):
        abort(400)
    status = run_status(conn, run_id)
    if status is None:
        abort(404)
    if status != "SHADOW":
        abort(409, "target run %s is %s, not SHADOW" % (run_id, status))
    return run_id


@review_bp.post("/persons/review/batch/freeze")
def batch_freeze():
    if not review_allowed():
        abort(404)
    # E-6: a JSON body forces a CORS preflight on any cross-site POST.
    # Non-JSON -> 415, malformed -> 400 (get_json raises both).
    body = request.get_json()
    if not isinstance(body, dict):
        abort(400)
    import uuid
    from . import repository as repo
    from . import service
    from heimdal.modules.shared import db as shared_db
    from heimdal.pdfhist import consumer
    with shared_db.tx(consumer.projection_dsn()) as conn:
        target = _target_run(conn, body.get("target_run_id"))
        superseded = repo.supersede_older(conn)  # also older legacy rows
        # E-2: a case's newest decision only, and only if it approves.
        # E-11: SKIP LOCKED leaves rows another freeze holds to that freeze.
        rows = conn.execute(
            "SELECT decision_id, case_key, chosen_canonical_id, shown_packet_hash, "
            "shown_alternatives FROM {t} WHERE decision_id IN ("
            "SELECT DISTINCT ON (case_key) decision_id FROM {t} "
            "ORDER BY case_key, decision_id DESC) "
            "AND decision = 'APPROVE_LINK' AND application_status = 'PENDING' "
            "ORDER BY decision_id FOR UPDATE SKIP LOCKED".format(
                t=repo.DECISION_TABLE)).fetchall()
        batch_id = "batch-%s" % uuid.uuid4().hex[:12]
        out = {"clean": [], "stale": [], "conflict": [], "refused_off_list": []}
        canon = _canonical_conn()
        try:
            for d in rows:
                did = d["decision_id"]
                if d["chosen_canonical_id"] not in (d["shown_alternatives"] or []):
                    out["refused_off_list"].append(did)  # D15; stays PENDING
                    continue
                status, _reason = service._preview_class(conn, canon, target, d)
                if status in ("CLEAN", "ALREADY_SAME_TARGET"):
                    repo.attach_batch(conn, did, batch_id)
                    out["clean"].append(did)
                elif status == "TARGET_CONFLICT":
                    repo.mark_conflict(conn, did, expect="PENDING")
                    out["conflict"].append(did)
                else:
                    repo.mark_stale(conn, did, status, expect="PENDING")
                    out["stale"].append(did)
        finally:
            canon.close()
        pin = _batch_pin_for(conn, target, batch_id)
    return jsonify({"frozen_batch_id": batch_id, "target_run_id": target, **out,
                    "superseded": superseded, "state_pin": pin})


def _batch_pin_for(conn, run_id: int, batch_id: str) -> str:
    from . import repository as repo
    from . import service
    rows = conn.execute(
        "SELECT decision_id, case_key, chosen_canonical_id, shown_packet_hash "
        "FROM %s WHERE frozen_batch_id = %%s AND application_status = 'FROZEN'"
        % repo.DECISION_TABLE, (batch_id,)).fetchall()
    return service.batch_state_pin(conn, run_id, [
        dict(r) for r in rows])


@review_bp.post("/persons/review/batch/dry-run")
def batch_dry_run():
    if not review_allowed():
        abort(404)
    from . import service
    body = _json_body()
    batch_id = body.get("frozen_batch_id", "")
    if not batch_id:
        abort(400)
    conn = _projection_conn()
    try:
        target = _target_run(conn, body.get("target_run_id"))
        canon = _canonical_conn()
        try:
            preview = service.dry_run_preview(conn, canon, target, batch_id)
        finally:
            canon.close()
    finally:
        conn.close()
    return jsonify({**preview, "target_run_id": target})


def _ensure_case(conn, case_key, packet):
    from . import repository as repo
    src = packet["source"]
    conn.execute(  # E-9: ON CONFLICT, so two first decisions never collide
        "INSERT INTO %s (case_key, source_kind, norm_name, source_name_exact, "
        "company_anchor, role_type, source_context, candidate_count) "
        "VALUES (%%s,%%s,%%s,%%s,%%s,%%s,%%s,%%s) ON CONFLICT (case_key) DO NOTHING"
        % repo.CASE_TABLE,
        (case_key, "PDF-HIST" if src["scope"] == "HISTORICAL_EVENT" else "PDF-CURRENT",
         src.get("normalized"), src.get("name_exact"),
         str(src.get("company_anchor") or ""), src.get("role"),
         src.get("scope"), len(packet.get("candidates", []))))
    # No commit here: the caller owns the transaction (P0.2b).


@review_bp.post("/persons/review/batch/apply")
def batch_apply():
    if not review_allowed():
        abort(404)
    from . import repository as repo
    from . import service
    from heimdal.pdfhist import human_links
    body = _json_body()
    batch_id = body.get("frozen_batch_id", "")
    state_pin = body.get("state_pin", "")
    if not batch_id or not state_pin:
        abort(400)
    from heimdal.modules.shared import db as shared_db
    from heimdal.pdfhist import consumer
    with shared_db.tx(consumer.projection_dsn()) as conn:
        target = _target_run(conn, body.get("target_run_id"))
        rows = conn.execute(
            "SELECT * FROM %s WHERE frozen_batch_id = %%s AND application_status = "
            "'FROZEN' ORDER BY decision_id FOR UPDATE" % repo.DECISION_TABLE,
            (batch_id,)).fetchall()
        if _batch_pin_for(conn, target, batch_id) != state_pin:
            return jsonify({"status": "STALE_BATCH",
                            "reason": "state drift after preview; re-freeze and re-run dry-run"}), 409
        items, unresolvable = [], []
        for d in rows:
            keys = service.resolve_keys_in_run(conn, target, d["case_key"])
            if len(keys) == 1:
                items.append(human_links.frozen_item(d, keys[0]))
            else:
                unresolvable.append(d["decision_id"])
        canon = _canonical_conn()
        try:
            stats = human_links.apply_approved_human_links(conn, target, items, canon)
        finally:
            canon.close()
        # E-3: each decision gets the status of what actually happened to it
        for did in unresolvable:
            repo.mark_stale(conn, did, "SOURCE-UNRESOLVABLE", expect="FROZEN")
            stats["outcomes"][did] = "unresolvable"
            stats["stale"] += 1
        for did, what in stats["outcomes"].items():
            if what in ("applied", "agreement"):
                repo.mark_applied(conn, did, target)
            elif what == "conflict":
                repo.mark_conflict(conn, did, expect="FROZEN")
            elif what != "unresolvable":
                repo.mark_stale(conn, did, what.upper(), expect="FROZEN")
    return jsonify({**stats, "target_run_id": target})


@review_bp.get("/persons/review/batch/<batch_id>/qa")
def batch_qa(batch_id: str):
    if not review_allowed():
        abort(404)
    from . import qa as qa_sampling
    conn = _projection_conn()
    try:
        sample = qa_sampling.sample_applied_batch(conn, batch_id)
    finally:
        conn.close()
    for e in sample:
        e["decided_at"] = str(e.get("decided_at"))
    if _wants_json():
        return jsonify({"frozen_batch_id": batch_id, "sample": sample})
    from flask import render_template
    return render_template("review_qa.html", batch_id=batch_id, sample=sample)


@review_bp.post("/persons/review/batch/qa/mark")
def batch_qa_mark():
    if not review_allowed():
        abort(404)
    from . import qa as qa_sampling
    from . import repository as repo
    body = _json_body()
    try:
        did = int(body.get("decision_id", 0))
    except (TypeError, ValueError):
        abort(400)
    status = body.get("qa_status", "")
    if status not in (qa_sampling.QA_OK, qa_sampling.QA_NEEDS_REVIEW):
        abort(400)
    from heimdal.modules.shared import db as shared_db
    from heimdal.pdfhist import consumer
    with shared_db.tx(consumer.projection_dsn()) as conn:
        row = conn.execute(
            "SELECT case_key, application_status FROM %s WHERE decision_id = %%s "
            "FOR UPDATE" % repo.DECISION_TABLE, (did,)).fetchone()
        if row is None:
            abort(404)  # E-14: no such decision
        repo.mark_qa(conn, did, status, _REVIEWER, body.get("note"))
        reopened = False
        if status == qa_sampling.QA_NEEDS_REVIEW and body.get("reopen"):
            # D9: a reopened decision stops being live, so builds stop
            # re-applying it and its source returns to the queue.
            st = row["application_status"]
            if st in ("PENDING", "FROZEN", "APPLIED"):
                repo.mark_stale(conn, did, "QA-REOPENED", expect=st)
            repo.reopen_case(conn, row["case_key"])
            reopened = True
    return jsonify({"decision_id": did, "qa_status": status,
                    "reopened": reopened})


def _active_run(conn) -> int:
    row = conn.execute(
        "SELECT projection_run_id FROM registry_projection.projection_run "
        "WHERE status = 'ACTIVE' ORDER BY projection_run_id DESC LIMIT 1").fetchone()
    if row is None:
        abort(404)
    return row["projection_run_id"]
