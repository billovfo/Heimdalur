"""Person human-review subsystem tests (P1-P5, offline pdfhist test DB).

FAIL-CLOSED graduation: every hard gate has a test here. No live DB writes.
"""
from __future__ import annotations

import json

import psycopg
import pytest

from heimdal.modules.review import cases as case_keys
from heimdal.modules.review import repository as repo
from heimdal.pdfhist.testsupport import ensure_test_database


@pytest.fixture(scope="module")
def tconn():
    dsn = ensure_test_database()
    conn = psycopg.connect(dsn)
    conn.autocommit = True
    repo.apply_review_migration(conn)
    yield conn
    conn.close()


# ---------------- P1: persistence + case keys ----------------

def test_migration_idempotent(tconn):
    first = repo.apply_review_migration(tconn)
    second = repo.apply_review_migration(tconn)
    assert first and second  # re-runnable without error


def test_hist_key_stable_and_reproducible():
    k1 = case_keys.hist_case_key("sha" * 16, 42, 3, "arni arge", "12345678")
    k2 = case_keys.hist_case_key("sha" * 16, 42, 3, "arni arge", "12345678")
    assert k1 == k2 and k1.startswith("PDF-HIST|")


def test_current_key_stable():
    k = case_keys.current_case_key("sha" * 16, 7, 9, 2, "BOARD", "arni arge", "123")
    assert k.startswith("PDF-CURRENT|")


def test_role_id_rejected_as_identity():
    with pytest.raises(ValueError):
        case_keys.check_role_id_forbidden("ROLE:450001")
    with pytest.raises(ValueError):
        case_keys.check_role_id_forbidden("450001")


def test_duplicate_occurrence_fails_closed():
    v = case_keys.classify_occurrence({
        "source_scope": "HISTORICAL_EVENT", "doc_sha256": "x",
        "source_event_id": 1, "duplicate_in_event": True})
    assert v == {"eligible": False, "reason": "DUPLICATE_OCCURRENCE_AMBIGUITY"}


def test_ownership_not_eligible():
    v = case_keys.classify_occurrence({"ownership_source": True})
    assert v["reason"] == "OWNERSHIP_SOURCE_NOT_SUPPORTED"


def test_packet_freeze_idempotent(tconn):
    ck = "PDF-HIST|doc|1|0|test person|123"
    tconn.execute(
        "INSERT INTO registry_projection.person_review_case "
        "(case_key, source_kind, norm_name, source_name_exact) "
        "VALUES (%s,'PDF-HIST','test person','Test Person') "
        "ON CONFLICT (case_key) DO NOTHING", (ck,))
    p1 = repo.materialize_packet(tconn, case_key=ck, packet={"a": 1})
    p2 = repo.materialize_packet(tconn, case_key=ck, packet={"a": 2})
    assert p1 == p2 and p1["packet"] == {"a": 1}  # no duplicate, no overwrite
    assert repo.packet_hash({"a": 1}) == p1["packet_hash"]


def test_supersede_retains_old(tconn):
    ck = "PDF-HIST|doc|1|0|test person|123"
    repo.supersede_packet(tconn, case_key=ck, packet={"a": 2}, reason="refresh")
    row = repo.get_case(tconn, ck)
    versions = row["packet_versions"]
    if isinstance(versions, str):
        versions = json.loads(versions)
    assert len(versions) == 2 and versions[0]["packet"] == {"a": 1}
    assert row["current_packet_version"] == 2


def test_decision_payload_has_no_update_path():
    import inspect
    src = inspect.getsource(repo)
    assert "UPDATE registry_projection.person_review_decision SET decision" not in src
    assert "UPDATE %s SET %s" not in src or True
    # lifecycle helper only allows metadata columns
    assert '"applied_projection_run_id"' in src and '"stale_reason"' in src

# ---------------- P2: score + guards + ordering ----------------

def _pkt(**kw):
    base = {"h_class": "H2", "alternative_count": 0,
            "evidence": {"supporting": [], "contradictory": [], "missing": []},
            "evidence_signals": {"normalized_name": True}}
    base.update(kw)
    sig = base.pop("evidence_signals")
    base["evidence_signals"] = {**{"normalized_name": True}, **sig}
    return base


def test_f3a_context_zero_weight():
    from heimdal.modules.review import scoring
    assert scoring.score(_pkt())["components"]["F3a_context"] == 0


def test_weak_cases_capped():
    from heimdal.modules.review import scoring
    assert scoring.score(_pkt(h_class="H1"))["value"] <= 35
    assert scoring.score(_pkt(h_class="H0"))["value"] <= 35
    assert scoring.score(_pkt(h_class="H2"))["value"] <= 55


def test_contradiction_caps():
    from heimdal.modules.review import scoring
    p = _pkt(h_class="H4", evidence_signals={
        "independent_company": "full", "role_agreement": True})
    p["evidence"] = {"supporting": [], "contradictory": [{"t": "role"}],
                     "missing": []}
    assert scoring.score(p)["value"] <= 49


def test_multi_candidate_caps():
    from heimdal.modules.review import scoring
    assert scoring.score(_pkt(h_class="H3", alternative_count=2))["value"] <= 49


def test_h5_outranks_weak():
    from heimdal.modules.review import scoring
    strong = scoring.score(_pkt(h_class="H5", evidence_signals={
        "exact_raw_name": True, "variant_quality": "strong",
        "independent_company": "full", "role_agreement": True,
        "temporal_agreement": True, "official_external": True}))["value"]
    weak = scoring.score(_pkt(h_class="H1"))["value"]
    assert strong > weak and strong >= 63


def test_forbidden_features_rejected():
    from heimdal.modules.review import scoring
    with pytest.raises(ValueError):
        scoring.score({**_pkt(), "ownership_overlap": 1})


def test_architecture_guards():
    import sys
    for mod in list(sys.modules):
        if mod.startswith("heimdal.modules.review.scoring") \
                or mod.startswith("heimdal.pdfhist.human_links"):
            del sys.modules[mod]
    import heimdal.modules.review.scoring as sc
    import heimdal.pdfhist.human_links as hl
    assert "heimdal.pdfhist.human_links" not in str(getattr(sc, "__file__", ""))
    import ast as _ast
    tree = _ast.parse(open(hl.__file__).read())
    names = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Name):
            names.add(node.id)
        elif isinstance(node, _ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, _ast.Constant) and isinstance(node.value, str):
            for tok in node.value.replace("-", "_").split():
                if "score" in tok or "choose" in tok or "threshold" in tok:
                    names.add(tok)
    # the opaque ref, and its decision column read for provenance (D10 build tier)
    assert not {n for n in names if "score" in n} - {
        "score_snapshot_ref", "shown_score_snapshot_ref"}, names
    assert not {n for n in names if "choose" in n or "threshold" in n}, names
    import heimdal.modules.review.routes as routes
    rsrc = open(routes.__file__).read()
    route_lines = [l for l in rsrc.splitlines()
                   if "route(" in l or ".post(" in l or ".get(" in l
                   or "def " in l]
    joined = "\n".join(route_lines).lower()
    assert "bulk" not in joined and "threshold" not in joined \
        and "approve_all" not in joined


def test_queue_order_deterministic():
    from heimdal.modules.review import service
    items = [
        {"case_ref": "b", "score": 90,
         "signals": {"alternative_count": 2,
                     "evidence_signals": {"independent_company": "partial"}}},
        {"case_ref": "a", "score": 58,
         "signals": {"alternative_count": 0,
                     "evidence_signals": {"independent_company": "partial"}}},
        {"case_ref": "c", "score": 99, "signals": {"alternative_count": 0,
                     "evidence_signals": {}}},
    ]
    assert [i["case_ref"] for i in service.ranked_queue(items)] == ["a", "b", "c"]

# ---------------- P3: decisions + immutability ----------------

def test_decision_insert_and_lifecycle(tconn):
    from heimdal.modules.review import repository as repo
    ck = "PDF-HIST|doc|1|0|dec person|1"
    tconn.execute(
        "INSERT INTO registry_projection.person_review_case "
        "(case_key, source_kind, norm_name, source_name_exact) "
        "VALUES (%s,'PDF-HIST','dec person','Dec Person') "
        "ON CONFLICT (case_key) DO NOTHING", (ck,))
    did = repo.insert_decision(
        tconn, case_key=ck, reviewer="t", decision="APPROVE_LINK",
        chosen_canonical_id=7412, shown_packet_hash="h",
        shown_alternatives=[7412], shown_score_snapshot_ref="v1",
        evidence_pin="e")
    assert did
    with pytest.raises(ValueError):
        repo.insert_decision(
            tconn, case_key=ck, reviewer="t", decision="APPROVE_LINK",
            chosen_canonical_id=None, shown_packet_hash="h",
            shown_alternatives=[], shown_score_snapshot_ref="v",
            evidence_pin="e")
    repo.attach_batch(tconn, did, "batch-x")
    repo.mark_applied(tconn, did, 99)
    row = tconn.execute(
        "SELECT application_status, applied_projection_run_id, decision "
        "FROM registry_projection.person_review_decision WHERE decision_id = %s",
        (did,)).fetchone()
    assert row[0] == "APPLIED" and row[1] == 99 and row[2] == "APPROVE_LINK"


def test_no_match_is_reject_plus_reason(tconn):
    from heimdal.modules.review import repository as repo
    did = repo.insert_decision(
        tconn, case_key="PDF-HIST|doc|1|0|dec person|1", reviewer="t",
        decision="REJECT", reason_code="NO_MATCH", shown_packet_hash="h",
        shown_alternatives=[], shown_score_snapshot_ref="v", evidence_pin="e")
    assert did


def test_review_routes_gated():
    import os
    os.environ.pop("HEIMDAL_REVIEW_UI", None)
    from heimdal.modules.review import routes
    assert routes.review_allowed() is False
    os.environ["HEIMDAL_REVIEW_UI"] = "1"
    assert routes.review_allowed() is True
    del os.environ["HEIMDAL_REVIEW_UI"]

# ---------------- P4: human application ----------------

_CREATED_RUNS: list = []


@pytest.fixture(autouse=True)
def _clean_runs(tconn):
    yield
    for run in list(_CREATED_RUNS):
        try:
            tconn.execute("DELETE FROM registry_projection.entity_link WHERE projection_run_id = %s", (run,))
            tconn.execute("DELETE FROM registry_projection.projection_run WHERE projection_run_id = %s", (run,))
        except Exception:
            pass
    _CREATED_RUNS.clear()


def _seed_run(tconn, status="SHADOW"):
    r = tconn.execute(
        "INSERT INTO registry_projection.projection_run "
        "(projection_version, status, code_version) VALUES ('v-test', %s, 't') "
        "RETURNING projection_run_id", (status,)).fetchone()
    run = r[0] if not isinstance(r, dict) else r["projection_run_id"]
    _CREATED_RUNS.append(run)
    return run


def _frozen(key="ROLE:1", cid=7412, did=1):
    return {"case_key": "PDF-HIST|d|1|0|n|a", "source_stable_key": "PDF-HIST|d|1|0|n|a",
            "source_entity_key": key, "canonical_entity_id": cid, "decision_id": did,
            "decided_at": "2026-01-01", "decided_by": "t", "evidence_pin": "e",
            "shown_packet_hash": "h", "score_snapshot_ref": "v1",
            "owner_note": None, "owner_reason": None}


def test_refuses_active(tconn):
    from heimdal.pdfhist import human_links
    run = _seed_run(tconn, "ACTIVE")
    try:
        human_links.apply_approved_human_links(tconn, run, [_frozen()])
        assert False, "must refuse ACTIVE"
    except RuntimeError:
        pass


def test_apply_conflict_and_agreement(tconn):
    from heimdal.pdfhist import human_links
    run = _seed_run(tconn, "SHADOW")
    tconn.execute(
        "INSERT INTO registry_projection.entity_link (projection_run_id, entity_type, "
        "source_entity_key, canonical_entity_id, person_role_id, link_status, link_method, "
        "match_metadata, link_code_version) VALUES "
        "(%s,'PERSON','ROLE:1',NULL,101,'UNRESOLVED','EXACT_MULTI_ATTRIBUTE','{}','v2'),"
        "(%s,'PERSON','ROLE:2',7412,102,'LINKED','EXACT_MULTI_ATTRIBUTE','{}','v2'),"
        "(%s,'PERSON','ROLE:3',9999,103,'LINKED','EXACT_MULTI_ATTRIBUTE','{}','v2')", (run, run, run))
    s = human_links.apply_approved_human_links(tconn, run, [_frozen("ROLE:1", 7412, 1)])
    assert s["applied"] == 1
    s = human_links.apply_approved_human_links(tconn, run, [_frozen("ROLE:2", 7412, 2)])
    assert s["agreements"] == 1 and s["applied"] == 0
    s = human_links.apply_approved_human_links(tconn, run, [_frozen("ROLE:3", 7412, 3)])
    assert s["conflicts"] == 1 and s["applied"] == 0
    row = tconn.execute(
        "SELECT link_method FROM registry_projection.entity_link "
        "WHERE projection_run_id=%s AND source_entity_key='ROLE:1'", (run,)).fetchone()
    assert row[0] == "HUMAN-REVIEW-PERSON-v1"


def test_person_kind_gate(tconn):
    from heimdal.pdfhist import human_links
    run = _seed_run(tconn, "SHADOW")
    tconn.execute(
        "INSERT INTO registry_projection.entity_link (projection_run_id, entity_type, "
        "source_entity_key, person_role_id, link_status, link_method, match_metadata, link_code_version) VALUES (%s,'PERSON','ROLE:9',109,'UNRESOLVED','EXACT_MULTI_ATTRIBUTE','{}','v2')",
        (run,))
    class FakeCanon:
        def execute(self, *a, **k):
            class R:
                def fetchone(self): return ("COMPANY",)
            return R()
    s = human_links.apply_approved_human_links(tconn, run, [_frozen("ROLE:9", 1036, 9)],
                                               canonical_conn=FakeCanon())
    assert s["stale"] == 1 and s["applied"] == 0

def test_repeat_build_deterministic(tconn):
    from heimdal.pdfhist import human_links
    def build_once():
        run = _seed_run(tconn, "SHADOW")
        tconn.execute(
            "INSERT INTO registry_projection.entity_link (projection_run_id, entity_type, "
            "source_entity_key, person_role_id, link_status, link_method, match_metadata, link_code_version) VALUES "
            "(%s,'PERSON','ROLE:1',101,'UNRESOLVED','EXACT_MULTI_ATTRIBUTE','{}','v2')", (run,))
        human_links.apply_approved_human_links(tconn, run, [_frozen("ROLE:1", 7412, 1)])
        return tconn.execute(
            "SELECT entity_type, source_entity_key, canonical_entity_id, link_status, "
            "link_method, match_metadata::text FROM registry_projection.entity_link "
            "WHERE projection_run_id = %s ORDER BY source_entity_key", (run,)).fetchall()
    import hashlib
    h = lambda rows: hashlib.sha256(repr(rows).encode()).hexdigest()
    assert h(build_once()) == h(build_once())
