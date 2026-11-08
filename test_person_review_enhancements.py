"""Post-MVP enhancement tests (E1-E5). Separate file; frozen P1-P5 suite untouched.

FAIL-CLOSED: hard gates for reason codes, coverage, contradiction-first,
diff, dry-run, QA, session guard. No live DB writes (pdfhist_test only).
"""
from __future__ import annotations

import hashlib
import json

import psycopg
import pytest

from heimdal.modules.review import cases as case_keys
from heimdal.modules.review import reasons as reason_codes
from heimdal.modules.review import repository as repo
from heimdal.pdfhist.testsupport import ensure_source_event, ensure_test_database


@pytest.fixture(scope="module")
def tconn():
    dsn = ensure_test_database()
    conn = psycopg.connect(dsn)
    conn.autocommit = True
    repo.apply_review_migration(conn)
    yield conn
    conn.close()


_CREATED_RUNS: list = []


@pytest.fixture(scope="module", autouse=True)
def _drop_test_double(tconn):
    yield
    try:
        tconn.execute("DELETE FROM pdf_document_observation WHERE sha256 = %s",
                      ("c" * 64,))
        tconn.execute("DELETE FROM pdf_document WHERE source_document_id = 'enh-doc'")
        tconn.execute("DELETE FROM pdf_document_content WHERE sha256 = %s",
                      ("c" * 64,))
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _clean(tconn):
    yield
    for run in list(_CREATED_RUNS):
        try:
            tconn.execute("DELETE FROM registry_projection.entity_link WHERE projection_run_id = %s", (run,))
            tconn.execute("DELETE FROM registry_projection.company_person_role WHERE projection_run_id = %s", (run,))
            tconn.execute("DELETE FROM registry_projection.projection_run WHERE projection_run_id = %s", (run,))
        except Exception:
            pass
    _CREATED_RUNS.clear()
    try:
        tconn.execute("DELETE FROM registry_projection.person_review_decision "
                      "WHERE frozen_batch_id LIKE 'ENH-batch%' "
                      "OR case_key LIKE 'ENH|%' OR case_key LIKE 'PDF-HIST|d|7|%'")
        tconn.execute("DELETE FROM registry_projection.person_review_case "
                      "WHERE case_key LIKE 'ENH|%' OR case_key LIKE 'PDF-HIST|d|7|%'")
    except Exception:
        pass


def _seed_run(tconn, status="SHADOW"):
    r = tconn.execute(
        "INSERT INTO registry_projection.projection_run "
        "(projection_version, status, code_version) VALUES ('v-enh', %s, 't') "
        "RETURNING projection_run_id", (status,)).fetchone()
    run = r[0] if not isinstance(r, dict) else r["projection_run_id"]
    _CREATED_RUNS.append(run)
    return run


# ---------------- E1: reason codes ----------------

def test_reason_sets_complete():
    assert set(reason_codes.AMBIGUOUS_REASONS) == {
        "MULTIPLE_PLAUSIBLE_CANDIDATES", "CONFLICTING_INDEPENDENT_EVIDENCE",
        "INSUFFICIENT_DISCRIMINATOR", "TEMPORAL_CONFLICT", "ROLE_CONFLICT",
        "IDENTITY_CONFLICT", "OTHER_AMBIGUOUS"}
    assert set(reason_codes.DEFER_REASONS) == {
        "INSUFFICIENT_INDEPENDENT_EVIDENCE", "NO_CANONICAL_CANDIDATE",
        "NEEDS_EXTERNAL_EVIDENCE", "SOURCE_CONTEXT_INCOMPLETE",
        "WAIT_FOR_NEW_CANONICAL", "WAIT_FOR_NEW_SOURCE_EVIDENCE", "OTHER_DEFER"}
    assert "NO_MATCH" in reason_codes.REJECT_REASONS


def test_validate_per_decision_type():
    for r in reason_codes.AMBIGUOUS_REASONS:
        assert reason_codes.validate("AMBIGUOUS", r) == r
    for r in reason_codes.DEFER_REASONS:
        assert reason_codes.validate("DEFER", r) == r
    for r in reason_codes.REJECT_REASONS:
        assert reason_codes.validate("REJECT", r) == r
    assert reason_codes.validate("APPROVE_LINK", None) is None


def test_no_match_is_reject_plus_reason():
    d, r = reason_codes.normalize_no_match()
    assert (d, r) == ("REJECT", "NO_MATCH")
    assert reason_codes.validate(d, r) == "NO_MATCH"


def test_missing_reason_rejected():
    for dec in ("AMBIGUOUS", "DEFER", "REJECT"):
        try:
            reason_codes.validate(dec, None)
            assert False, dec
        except ValueError:
            pass


def test_invalid_combinations_rejected():
    bad = [("AMBIGUOUS", "NO_MATCH"), ("AMBIGUOUS", "WRONG_PERSON"),
           ("DEFER", "WRONG_PERSON"), ("DEFER", "NO_MATCH"),
           ("REJECT", "MULTIPLE_PLAUSIBLE_CANDIDATES"),
           ("APPROVE_LINK", "OTHER_DEFER"), ("APPROVE_LINK", "NO_MATCH"),
           ("BOGUS", None)]
    for dec, rsn in bad:
        try:
            reason_codes.validate(dec, rsn)
            assert False, (dec, rsn)
        except ValueError:
            pass


def test_reason_code_immutable_with_payload(tconn):
    assert "reason_code" in repo.PAYLOAD_COLUMNS
    try:
        repo._lifecycle_update(tconn, 999999, {"reason_code": "X"}, "probe")
        assert False, "reason_code must not be lifecycle-mutable"
    except ValueError:
        pass


def _seed_coverage(tconn):
    from heimdal.modules.review import service as svc
    run = _seed_run(tconn)
    obs = tconn.execute(
        "INSERT INTO pdf_document_content (sha256, byte_length, content) "
        "VALUES (%s, 1, '\\x00') ON CONFLICT DO NOTHING RETURNING sha256",
        ("c" * 64,)).fetchone()
    doc = tconn.execute(
        "INSERT INTO pdf_document (pdf_type, source_document_id) "
        "VALUES ('X','enh-doc') ON CONFLICT DO NOTHING RETURNING id").fetchone()
    if doc is None:
        doc = tconn.execute(
            "SELECT id FROM pdf_document WHERE source_document_id='enh-doc'").fetchone()
    did = doc[0] if not isinstance(doc, dict) else doc["id"]
    o = tconn.execute(
        "INSERT INTO pdf_document_observation (document_id, collection_run_id, "
        "fetched_at, sha256, byte_length) VALUES (%s, 1, now(), %s, 1) "
        "ON CONFLICT DO NOTHING RETURNING id", (did, "c" * 64)).fetchone()
    if o is None:
        o = tconn.execute(
            "SELECT id FROM pdf_document_observation WHERE document_id=%s",
            (did,)).fetchone()
    oid = o[0] if not isinstance(o, dict) else o["id"]
    ev_ids = []
    for i, key in enumerate(("enh-ev-1", "enh-ev-2")):
        r = tconn.execute(
            "INSERT INTO pdf_source_event (company_id, requested_regnr, row_skrasnr, "
            "source_row_key, raw_text, page_no, row_ordinal) VALUES "
            "(7,'00000000','7',%s,'enh',0,1) "
            "ON CONFLICT (source_row_key) DO NOTHING RETURNING id",
            (key,)).fetchone()
        if r is None:
            r = tconn.execute(
                "SELECT id FROM pdf_source_event WHERE source_row_key=%s",
                (key,)).fetchone()
        ev_ids.append(r[0] if not isinstance(r, dict) else r["id"])
    for rid, norm, ev, comp in ((901, "anna a", ev_ids[0], 7),
                                (902, "anna a", ev_ids[0], 8),
                                (903, "cara c", ev_ids[1], 7)):
        tconn.execute(
            "INSERT INTO registry_projection.company_person_role (id, projection_run_id, "
            "company_id, person_name, entity_normalized, role_type, source_scope, "
            "source_event_id, source_observation_id, source_parse_result_id) VALUES "
            "(%s,%s,%s,%s,%s,'BOARD','HISTORICAL_EVENT',%s,%s,11)",
            (rid, run, comp, norm.title(), norm, ev, oid))
        tconn.execute(
            "INSERT INTO registry_projection.entity_link (projection_run_id, entity_type, "
            "source_entity_key, person_role_id, link_status, link_method, link_code_version) "
            "VALUES (%s,'PERSON',%s,%s,'UNRESOLVED','EXACT_MULTI_ATTRIBUTE','v')",
            (run, "ROLE:%d" % rid, rid))
    ck = "ENH|PDF-HIST|1|0|cara c|"
    tconn.execute(
        "INSERT INTO registry_projection.person_review_case "
        "(case_key, source_kind, norm_name, source_name_exact) "
        "VALUES (%s,'PDF-HIST','cara c','Cara C')", (ck,))
    repo.insert_decision(tconn, case_key=ck, reviewer="t", decision="REJECT",
                         reason_code="NO_MATCH", shown_packet_hash="h",
                         shown_alternatives=[], shown_score_snapshot_ref="v",
                         evidence_pin="e")
    return run


def test_coverage_health_reconciles(tconn):
    from heimdal.modules.review import service as svc
    before = tconn.execute(
        "SELECT count(*) FROM registry_projection.person_review_decision "
        "WHERE decision='REJECT'").fetchone()
    n0 = before[0] if not isinstance(before, dict) else before["count"]
    run = _seed_coverage(tconn)
    cov = svc.coverage_health(tconn, run)
    assert cov["unresolved_total"] == 3
    assert cov["reviewable_now"] == 1
    assert cov["not_eligible_total"] == 2
    assert cov["not_eligible_breakdown"] == {"DUPLICATE_OCCURRENCE_AMBIGUITY": 2}
    assert cov["reviewable_now"] + cov["not_eligible_total"] == cov["unresolved_total"]
    assert cov["decided_by_decision"].get("REJECT", 0) == n0 + 1


# ---------------- E2: contradiction-first + diff ----------------

def _sealed(packet, h=None):
    from heimdal.modules.review import repository as _repo
    return {"packet_version": 1, "reason": "t",
            "packet": packet, "packet_hash": h or _repo.packet_hash(packet)}


def _ev_packet(**kw):
    p = {"candidates": [{"canonical_entity_id": 1, "display_name": "A"}],
         "source": {"role": "BOARD", "name_exact": "A", "normalized": "a"},
         "evidence": {"supporting": [], "contradictory": [], "missing": []}}
    for k, v in kw.items():
        if k in ("supporting", "contradictory", "missing"):
            p["evidence"][k] = v
        else:
            p[k] = v
    return p


def test_diff_deterministic_and_material():
    from heimdal.modules.review import diff as _diff
    old = _sealed(_ev_packet(supporting=["s1"]))
    new = _sealed(_ev_packet(
        supporting=["s1", "s2"], contradictory=["c1"],
        candidates=[{"canonical_entity_id": 1, "display_name": "A"},
                    {"canonical_entity_id": 2, "display_name": "B"}]))
    d1, d2 = _diff.diff_packets(old, new), _diff.diff_packets(old, new)
    assert d1 == d2
    assert "CANDIDATE_ADDED" in d1["categories"]
    assert "NEW_CONTRADICTION" in d1["categories"]
    assert "NEW_SUPPORTING_EVIDENCE" in d1["categories"]


def test_diff_removed_and_score_change():
    from heimdal.modules.review import diff as _diff
    old = _sealed(_ev_packet(
        contradictory=["c1"],
        candidates=[{"canonical_entity_id": 9, "display_name": "Z"}]))
    new = _sealed(_ev_packet())
    d = _diff.diff_packets(old, new)
    assert "CANDIDATE_REMOVED" in d["categories"]
    assert "REMOVED_CONTRADICTION" in d["categories"]
    assert d["old_packet_hash"] and d["new_packet_hash"]


def test_diff_no_material_change():
    from heimdal.modules.review import diff as _diff
    p = _sealed(_ev_packet(supporting=["s1"]))
    d = _diff.diff_packets(p, dict(p))
    assert d["categories"] == ["NO_MATERIAL_CHANGE"]


def test_diff_ignores_presentation_order():
    from heimdal.modules.review import diff as _diff
    old = _sealed(_ev_packet(supporting=["a", "b"]))
    new = _sealed(_ev_packet(supporting=["b", "a"]))
    assert _diff.diff_packets(old, new)["categories"] == ["NO_MATERIAL_CHANGE"]


def test_old_packet_retained_and_accessible(tconn):
    ck = "ENH|diff|1|0|diff case|"
    tconn.execute(
        "INSERT INTO registry_projection.person_review_case "
        "(case_key, source_kind, norm_name, source_name_exact) "
        "VALUES (%s,'PDF-HIST','diff case','Diff Case') "
        "ON CONFLICT (case_key) DO NOTHING", (ck,))
    repo.materialize_packet(tconn, case_key=ck, packet=_ev_packet())
    repo.supersede_packet(tconn, case_key=ck,
                          packet=_ev_packet(supporting=["late evidence"]),
                          reason="requeue")
    row = repo.get_case(tconn, ck)
    assert len(row["packet_versions"]) == 2
    assert row["packet_versions"][0]["packet"]["evidence"] == \
        {"supporting": [], "contradictory": [], "missing": []}
    from heimdal.modules.review import diff as _diff
    d = _diff.diff_packets(row["packet_versions"][0], row["packet_versions"][1])
    assert "NEW_SUPPORTING_EVIDENCE" in d["categories"]


def test_contradiction_renders_first():
    import sys
    sys.path.insert(0, ".")
    from heimdal.app import create_app
    app = create_app()
    pkt = {"person_role_id": 1,
           "source": {"name_exact": "X", "role": "R", "scope": "S"},
           "candidates": [],
           "evidence": {"supporting": ["s"], "contradictory": ["c"],
                        "missing": []}}
    with app.test_request_context():
        from flask import render_template
        html = render_template("review_case.html", packet=pkt, change=None)
    assert "Contradictory evidence exists — review before approving." in html
    assert html.index("Modstridende") < html.index("Understøttende")
    assert html.index("Beviser") < html.index("Gennemgangstillid")


def test_no_contradiction_supporting_first():
    import sys
    sys.path.insert(0, ".")
    from heimdal.app import create_app
    app = create_app()
    pkt = {"person_role_id": 1,
           "source": {"name_exact": "X", "role": "R", "scope": "S"},
           "candidates": [{"canonical_entity_id": 5, "display_name": "B"},
                          {"canonical_entity_id": 3, "display_name": "A"}],
           "evidence": {"supporting": ["s"], "contradictory": [],
                        "missing": []}}
    with app.test_request_context():
        from flask import render_template
        html = render_template("review_case.html", packet=pkt, change=None)
    assert "Contradictory evidence exists" not in html
    assert html.index("Understøttende") < html.index("Modstridende")
    assert html.index("#3") < html.index("#5")  # neutral order, not score


# ---------------- E3: dry-run preview ----------------

class _FakeCanon:
    def __init__(self, kinds):
        self._kinds = kinds

    def execute(self, *a, **k):
        cid = a[1][0] if len(a) > 1 else None
        kind = self._kinds.get(cid)

        class R:
            def fetchone(self):
                return (kind,) if kind else None

        return R()


def _seed_hist_role(tconn, run, rid=501, event=7, norm="anna testsen"):
    ensure_source_event(tconn, event)
    tconn.execute(
        "INSERT INTO registry_projection.company_person_role (id, projection_run_id, "
        "company_id, person_name, entity_normalized, role_type, source_scope, "
        "source_event_id, source_observation_id, source_parse_result_id) VALUES "
        "(%s,%s,7,%s,%s,'BOARD','HISTORICAL_EVENT',%s,8001,9001)",
        (rid, run, norm.title(), norm, event))
    tconn.execute(
        "INSERT INTO registry_projection.entity_link (projection_run_id, entity_type, "
        "source_entity_key, person_role_id, link_status, link_method, link_code_version) "
        "VALUES (%s,'PERSON',%s,%s,'UNRESOLVED','EXACT_MULTI_ATTRIBUTE','v')",
        (run, "ROLE:%d" % rid, rid))


def _seed_frozen(tconn, run, key="PDF-HIST|d|7|0|anna testsen|", cid=7412,
                 batch="ENH-batch-1"):
    tconn.execute(
        "INSERT INTO registry_projection.person_review_case "
        "(case_key, source_kind, norm_name, source_name_exact) "
        "VALUES (%s,'PDF-HIST','anna testsen','Anna Testsen') "
        "ON CONFLICT (case_key) DO NOTHING", (key,))
    did = repo.insert_decision(
        tconn, case_key=key, reviewer="t", decision="APPROVE_LINK",
        chosen_canonical_id=cid, shown_packet_hash="h",
        shown_alternatives=[cid], shown_score_snapshot_ref="v1",
        evidence_pin="e")
    repo.attach_batch(tconn, did, batch)
    return did


def test_dry_run_clean_reconciles(tconn):
    from heimdal.modules.review import service as svc
    run = _seed_run(tconn)
    _seed_hist_role(tconn, run)
    _seed_frozen(tconn, run)
    canon = _FakeCanon({7412: "PERSON"})
    prev = svc.dry_run_preview(tconn, canon, run, "ENH-batch-1")
    s = prev["summary"]
    assert s["approved_decisions"] == 1 and s["clean_applicable"] == 1
    assert s["expected_net_new_links"] == 1
    assert s["approved_review_cases"] == 1
    assert s["expected_unresolved_reduction"] >= 1
    assert prev["non_clean_cases"] == []
    assert "score" not in json.dumps(prev).lower()
    still = tconn.execute(
        "SELECT link_status FROM registry_projection.entity_link "
        "WHERE projection_run_id=%s AND source_entity_key='ROLE:501'",
        (run,)).fetchone()
    assert (still["link_status"] if isinstance(still, dict) else still[0]) == "UNRESOLVED"


def test_dry_run_classification_mix(tconn):
    from heimdal.modules.review import service as svc
    run = _seed_run(tconn)
    _seed_hist_role(tconn, run, rid=501)
    _seed_hist_role(tconn, run, rid=502, norm="bo be")
    _seed_hist_role(tconn, run, rid=503, norm="ce ce")
    _seed_hist_role(tconn, run, rid=504, norm="de de")
    _seed_frozen(tconn, run, "PDF-HIST|d|7|0|anna testsen|", 7412, "ENH-batch-m")
    _seed_frozen(tconn, run, "PDF-HIST|d|7|0|bo be|", 7412, "ENH-batch-m")
    _seed_frozen(tconn, run, "PDF-HIST|d|7|0|ce ce|", 1036, "ENH-batch-m")
    _seed_frozen(tconn, run, "PDF-HIST|d|7|0|de de|", 4242, "ENH-batch-m")
    tconn.execute(
        "UPDATE registry_projection.entity_link SET link_status='LINKED', "
        "canonical_entity_id=7412 WHERE projection_run_id=%s AND source_entity_key='ROLE:502'",
        (run,))
    tconn.execute(
        "UPDATE registry_projection.entity_link SET link_status='LINKED', "
        "canonical_entity_id=9999 WHERE projection_run_id=%s AND source_entity_key='ROLE:504'",
        (run,))
    canon = _FakeCanon({7412: "PERSON", 1036: "COMPANY", 4242: "PERSON"})
    prev = svc.dry_run_preview(tconn, canon, run, "ENH-batch-m")
    s = prev["summary"]
    assert s["clean_applicable"] == 1
    assert s["already_same_target"] == 1
    assert s["target_conflict"] == 1
    assert s["non_person_target"] == 1
    assert s["approved_decisions"] == 4
    assert len(prev["non_clean_cases"]) == 3


def test_dry_run_missing_canonical_and_stale(tconn):
    from heimdal.modules.review import service as svc
    run = _seed_run(tconn)
    _seed_hist_role(tconn, run, rid=501)
    _seed_hist_role(tconn, run, rid=502, norm="bo be")
    _seed_frozen(tconn, run, "PDF-HIST|d|7|0|anna testsen|", 4242, "ENH-batch-x")
    _seed_frozen(tconn, run, "PDF-HIST|d|7|0|bo be|", 7412, "ENH-batch-x")
    tconn.execute(
        "DELETE FROM registry_projection.entity_link "
        "WHERE projection_run_id=%s AND source_entity_key='ROLE:502'", (run,))
    prev = svc.dry_run_preview(tconn, _FakeCanon({7412: "PERSON"}), run,
                               "ENH-batch-x")
    by_status = {c["case_key"]: c["status"] for c in prev["non_clean_cases"]}
    assert by_status["PDF-HIST|d|7|0|anna testsen|"] == "MISSING_CANONICAL"
    assert by_status["PDF-HIST|d|7|0|bo be|"] == "STALE"


def test_state_pin_drift_invalidates(tconn):
    from heimdal.modules.review import service as svc
    run = _seed_run(tconn)
    _seed_hist_role(tconn, run)
    _seed_frozen(tconn, run)
    canon = _FakeCanon({7412: "PERSON"})
    pin1 = svc.dry_run_preview(tconn, canon, run, "ENH-batch-1")["summary"]["state_pin"]
    tconn.execute(
        "UPDATE registry_projection.entity_link SET link_status='LINKED', "
        "canonical_entity_id=9999 WHERE projection_run_id=%s AND source_entity_key='ROLE:501'",
        (run,))
    pin2 = svc.dry_run_preview(tconn, canon, run, "ENH-batch-1")["summary"]["state_pin"]
    assert pin1 != pin2
    prev = svc.dry_run_preview(tconn, canon, run, "ENH-batch-1")
    assert prev["summary"]["target_conflict"] == 1


# ---------------- E4: QA sampling + session guard ----------------

def _seed_qa_case(tconn, key, evidence, alts, batch="ENH-batch-qa",
                  supersedes=None, prior_ambiguous=False):
    repo.apply_review_migration(tconn)
    tconn.execute(
        "INSERT INTO registry_projection.person_review_case "
        "(case_key, source_kind, norm_name, source_name_exact) "
        "VALUES (%s,'PDF-HIST','qa case','QA Case') "
        "ON CONFLICT (case_key) DO NOTHING", (key,))
    repo.materialize_packet(tconn, case_key=key, packet={
        "candidates": [{"canonical_entity_id": a, "display_name": "N%d" % a}
                       for a in alts],
        "source": {"role": "BOARD", "name_exact": "Q", "normalized": "q"},
        "evidence": evidence})
    prior_id = None
    if prior_ambiguous:
        prior_id = repo.insert_decision(tconn, case_key=key, reviewer="t",
                                        decision="AMBIGUOUS",
                                        reason_code="MULTIPLE_PLAUSIBLE_CANDIDATES",
                                        shown_packet_hash="h0",
                                        shown_alternatives=alts,
                                        shown_score_snapshot_ref="v",
                                        evidence_pin="e")
    if supersedes == "PRIOR":
        supersedes = prior_id
    did = repo.insert_decision(
        tconn, case_key=key, reviewer="t", decision="APPROVE_LINK",
        chosen_canonical_id=alts[0], shown_packet_hash="h",
        shown_alternatives=alts, shown_score_snapshot_ref="v1",
        evidence_pin="e", supersedes_decision_id=supersedes)
    repo.attach_batch(tconn, did, batch)
    repo.mark_applied(tconn, did, 99)
    return did


def test_qa_sample_deterministic_and_high_risk(tconn):
    from heimdal.modules.review import qa as qa_mod
    _seed_qa_case(tconn, "ENH|qa|1|0|risk|",
                  {"supporting": [], "contradictory": ["c1"], "missing": []},
                  [7412, 7413])
    s1 = qa_mod.sample_applied_batch(tconn, "ENH-batch-qa")
    s2 = qa_mod.sample_applied_batch(tconn, "ENH-batch-qa")
    assert [e["decision_id"] for e in s1] == [e["decision_id"] for e in s2]
    assert len(s1) == 1
    assert "CONTRADICTION_PRESENT" in s1[0]["risk_flags"]
    assert "MULTI_CANDIDATE" in s1[0]["risk_flags"]
    assert s1[0]["qa_reason"].startswith("HIGH_RISK:")


def test_qa_normal_sample_min_bound(tconn):
    from heimdal.modules.review import qa as qa_mod
    for i in range(10):
        _seed_qa_case(tconn, "ENH|qa|n|%d|x|" % i,
                      {"supporting": ["s"], "contradictory": [],
                       "missing": []}, [7412], batch="ENH-batch-qn")
    s = qa_mod.sample_applied_batch(tconn, "ENH-batch-qn")
    assert len(s) == 3  # 10% of 10 = 1 -> floor 3
    assert all(e["qa_reason"] == "NORMAL_SAMPLE" for e in s)


def test_qa_marks_never_mutate_links_or_payload(tconn):
    from heimdal.modules.review import qa as qa_mod
    did = _seed_qa_case(tconn, "ENH|qa|2|0|mark|",
                        {"supporting": ["s"], "contradictory": [],
                         "missing": []}, [7412], batch="ENH-batch-qm")
    before = tconn.execute(
        "SELECT decision, chosen_canonical_id, reason_code, shown_packet_hash "
        "FROM registry_projection.person_review_decision WHERE decision_id=%s",
        (did,)).fetchone()
    repo.mark_qa(tconn, did, "QA_NEEDS_REVIEW", "qa-reviewer", "look again")
    after = tconn.execute(
        "SELECT decision, chosen_canonical_id, reason_code, shown_packet_hash, "
        "qa_status FROM registry_projection.person_review_decision "
        "WHERE decision_id=%s", (did,)).fetchone()

    def tup(r):
        return tuple(r) if not isinstance(r, dict) else tuple(r.values())

    assert tup(after)[:4] == tup(before)
    assert tup(after)[4] == "QA_NEEDS_REVIEW"
    try:
        repo.mark_qa(tconn, did, "BOGUS", "x")
        assert False
    except ValueError:
        pass


def test_qa_needs_review_reopen_flow(tconn):
    did = _seed_qa_case(tconn, "ENH|qa|3|0|reopen|",
                        {"supporting": ["s"], "contradictory": [],
                         "missing": []}, [7412], batch="ENH-batch-qo")
    repo.mark_qa(tconn, did, "QA_NEEDS_REVIEW", "qa-reviewer")
    tconn.execute("UPDATE registry_projection.person_review_case "
                  "SET status='APPROVED' WHERE case_key=%s",
                  ("ENH|qa|3|0|reopen|",))
    repo.reopen_case(tconn, "ENH|qa|3|0|reopen|")
    row = repo.get_case(tconn, "ENH|qa|3|0|reopen|")
    assert row["status"] == "QUEUED"
    assert len(row["packet_versions"]) == 1  # packets untouched


def test_qa_rereview_and_prior_ambiguity_flags(tconn):
    from heimdal.modules.review import qa as qa_mod
    _seed_qa_case(tconn, "ENH|qa|4|0|flags|",
                  {"supporting": ["s"], "contradictory": [], "missing": []},
                  [7412], batch="ENH-batch-qf", supersedes="PRIOR",
                  prior_ambiguous=True)
    s = qa_mod.sample_applied_batch(tconn, "ENH-batch-qf")
    assert len(s) == 1
    assert "REREVIEWED_STALE" in s[0]["risk_flags"]
    assert "PREVIOUS_AMBIGUOUS_DEFER" in s[0]["risk_flags"]


def test_session_thresholds_soft_only():
    from heimdal.modules.review import session as sess
    assert sess.fatigue_notice(10, 2) is None
    assert sess.fatigue_notice(39, 0) is None
    msg = sess.fatigue_notice(40, 0)
    assert msg and "fresh review session" in msg
    msg2 = sess.fatigue_notice(5, 25)
    assert msg2 and "ambiguous" in msg2.lower()


def test_session_state_cannot_affect_queue():
    import inspect
    from heimdal.modules.review import service as svc
    src = inspect.getsource(svc.ranked_queue)
    assert "session" not in src.lower()
    src2 = inspect.getsource(svc.queue_summaries)
    assert "session" not in src2.lower()
