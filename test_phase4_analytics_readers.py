"""Phase 4 reader tests — versioned ACTIVE-build contract (task B52).

Runs standalone (pytest is unavailable in this environment):
    python3 test_phase4_analytics_readers.py

Read-only against the live QA database. No test writes anywhere.
"""
from __future__ import annotations

from heimdal.pdfhist.db import qa_dsn

import contextlib
import os
import sys

import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("HEIMDAL_READ_DB",
                     qa_dsn())

from heimdal.modules.analytics import repository as repo
from tests_baseline import ANALYTICS_BUILD
from heimdal.modules.analytics import views as av

_FAILS = []


def check(name, cond, detail=""):
    # H-2: under pytest a false check fails the test; only the __main__ runner
    # collects failures and reports them at the end.
    if __name__ != "__main__":
        assert cond, f"{name} {detail}"
    if cond:
        print(f"  ok: {name}")
    else:
        _FAILS.append(name)
        print(f"  FAIL: {name} {detail}")


def _conn():
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


# --- B5/B3: ACTIVE build selection --------------------------------------------

def test_active_build_selected():
    with _conn() as c:
        b = av.get_active_analytics_build(c)
        check("ACTIVE build resolves", b is not None)
        check("build pin has run id + hash",
              b and b["build_run_id"] == ANALYTICS_BUILD and len(b["content_hash"]) == 32,
              str(b and b["build_run_id"]))


def test_superseded_ignored():
    with _conn() as c:
        b = av.get_active_analytics_build(c)
        sup = repo.superseded_build_ids(c)
        ids = {r["build_run_id"] for r in sup}
        check("superseded builds exist and are excluded",
              bool(ids) and b["build_run_id"] not in ids, str(ids))


def test_no_active_build_returns_none(monkeypatch=False):
    """Fail-closed: an empty build table -> None (never a SUPERSEDED row)."""
    class _FakeConn:
        def execute(self, *_a, **_k):
            return _FakeCursor()

        def transaction(self, **_k):  # read_savepoint() (F-8) opens one
            return contextlib.nullcontext()
    class _FakeCursor:
        def fetchone(self):
            return None
        def fetchall(self):
            return []
    av_build = av.get_active_analytics_build(_FakeConn())
    check("no ACTIVE build -> build None", av_build is None)
    coll = av.get_person_collaborators(_FakeConn(), 718)
    check("no ACTIVE build -> collaborators None", coll is None)
    groups = av.get_company_groups(_FakeConn(), 42)
    check("no ACTIVE build -> company groups None", groups is None)


# --- B4: single build pin per response ----------------------------------------

def test_response_never_mixes_builds():
    with _conn() as c:
        b = av.get_active_analytics_build(c)
        coll = av.get_person_collaborators(c, 718, build=b)
        check("collaborators pinned to one build",
              coll["build_run_id"] == b["build_run_id"]
              and coll["content_hash"] == b["content_hash"])
        groups = av.get_person_groups(c, 718, build=b)
        check("groups pinned to one build",
              groups["build_run_id"] == b["build_run_id"])
        hist = av.get_person_role_history(c, 718, build=b)
        check("history pinned to one build",
              hist["build_run_id"] == b["build_run_id"])


# --- B13/B14: query-time pair metrics + transparent ordering ------------------

def test_pair_metrics_query_time():
    with _conn() as c:
        b = av.get_active_analytics_build(c)
        coll = av.get_person_collaborators(c, 718, build=b)
        check("collaborators found for person 718",
              coll and len(coll["collaborators"]) > 0)
        top = coll["collaborators"][0]
        check("metrics present",
              top["shared_company_count"] >= 1 and
              "context_breakdown" in top and "class_breakdown" in top)
        check("known overlap available where DB says so",
              top["known_overlap_days"] is None or top["known_overlap_days"] >= 0)
        # verify ordering: shared_company_count non-increasing
        counts = [x["shared_company_count"] for x in coll["collaborators"]]
        check("transparent sort (shared DESC)", counts == sorted(counts, reverse=True))
        ids = [x["canonical_person_id"] for x in coll["collaborators"]]
        check("deterministic tiebreak (no dupes)", len(ids) == len(set(ids)))
        # cross-check one collaborator directly against the SQL grain
        row = c.execute(
            "SELECT count(DISTINCT canonical_company_id) AS n"
            " FROM registry_analytics.person_person_overlap"
            f" WHERE build_run_id={ANALYTICS_BUILD} AND (person_a_id=718 AND person_b_id=%s)"
            " OR (person_a_id=%s AND person_b_id=718)",
            (top["canonical_person_id"], top["canonical_person_id"])).fetchone()
        check("shared_company_count recomputes exactly",
              row["n"] == top["shared_company_count"],
              f"sql={row['n']} reader={top['shared_company_count']}")


def test_context_dimensions_remain_separate():
    """B15: BOARD + OWNERSHIP for the same pair never collapse."""
    with _conn() as c:
        b = av.get_active_analytics_build(c)
        # find a pair with >1 context
        row = c.execute(
            f"""SELECT person_a_id, person_b_id FROM registry_analytics.person_person_overlap
               WHERE build_run_id={ANALYTICS_BUILD} GROUP BY 1,2 HAVING count(DISTINCT role_context) > 1
               LIMIT 1""").fetchone()
        check("multi-context pair exists live", row is not None)
        if row:
            coll = av.get_person_collaborators(c, row["person_a_id"], build=b)
            entry = next(x for x in coll["collaborators"]
                         if x["canonical_person_id"] == row["person_b_id"])
            labels = [cb["context"] for cb in entry["context_breakdown"]]
            check("contexts listed separately", len(labels) >= 2, str(labels))


# --- B16: mixed-authority fallback rendering ----------------------------------

def test_mixed_authority_fallback_renders_safely():
    with _conn() as c:
        b = av.get_active_analytics_build(c)
        row = c.execute(
            f"""SELECT person_a_id, person_b_id FROM registry_analytics.person_person_overlap
               WHERE build_run_id={ANALYTICS_BUILD} AND authority_class IS NULL
               AND source_family='PDF_HISTORY' LIMIT 1""").fetchone()
        check("fallback row exists live", row is not None)
        if row:
            ev = av.get_pair_evidence(c, row["person_a_id"], row["person_b_id"], build=b)
            target = [r for r in ev["rows"] if r["stored_authority_class"] is None]
            check("fallback row surfaced", len(target) >= 1)
            r0 = target[0]
            check("family kept, authority NULL explained",
                  r0["stored_source_family"] == "PDF_HISTORY"
                  and r0["fallback_note"] == av.FALLBACK_AUTHORITY_NOTE)
            check("no MIXED label invented",
                  av.MIXED_AUTHORITY_LABEL not in (r0["class_label"] or "")
                  and "MIXED" not in str(r0["class_label"]))


# --- B11/B33: provenance trace resolves ----------------------------------------

def test_pair_provenance_recomputes():
    with _conn() as c:
        b = av.get_active_analytics_build(c)
        row = c.execute(
            f"""SELECT person_a_id, person_b_id, canonical_company_id, observation_count
               FROM registry_analytics.person_person_overlap
               WHERE build_run_id={ANALYTICS_BUILD} ORDER BY observation_count DESC LIMIT 1""").fetchone()
        ev = av.get_pair_evidence(c, row["person_a_id"], row["person_b_id"], build=b)
        r0 = next(r for r in ev["rows"]
                  if r["company"]["canonical_company_id"] == row["canonical_company_id"])
        check("contributing observations present", len(r0["evidence"]) >= 2)
        check("trace carries source ids",
              all(e["source_document_id"] is not None
                  or e["source_event_id"] is not None
                  or e["source_relation_id"] is not None for e in r0["evidence"]))


def test_group_evidence_provenance():
    with _conn() as c:
        b = av.get_active_analytics_build(c)
        g = c.execute(
            f"""SELECT group_id FROM registry_analytics.person_group
               WHERE build_run_id={ANALYTICS_BUILD} AND group_kind='RECURRING_GROUP'
               ORDER BY group_size DESC LIMIT 1""").fetchone()
        ev = av.get_group_evidence(c, g["group_id"], build=b)
        check("group evidence resolves", ev and not ev.get("error"))
        check("episodes typed with co_observation_kind",
              all(e["co_observation_kind"] in ("SAME_SNAPSHOT", "SAME_EVENT",
                                               "SAFE_PERIOD_OVERLAP")
                  for e in ev["group"]["episodes"]))
        check("episode -> members -> sources chain",
              all(e["sources"] for e in ev["group"]["episodes"]))


# --- B5: graceful degradation (read failure) -----------------------------------

def test_read_failure_degrades_gracefully():
    class _BrokenConn:
        def execute(self, *_a, **_k):
            raise psycopg.OperationalError("simulated read failure")
    b = {"build_run_id": 13, "content_hash": "x" * 32, "coverage": {}, 
         "coverage_display": av._coverage_display({})}
    coll = av.get_person_collaborators(_BrokenConn(), 718, build=b)
    check("collaborator read failure -> local error state",
          coll and coll.get("error") and coll["collaborators"] == [])
    groups = av.get_person_groups(_BrokenConn(), 718, build=b)
    check("group read failure -> local error state",
          groups and groups.get("error"))


# --- B52: recurring/core pairing -----------------------------------------------

def test_core_recurring_pairing():
    with _conn() as c:
        b = av.get_active_analytics_build(c)
        row = c.execute(
            f"""SELECT r.member_set_hash, r.role_context FROM registry_analytics.person_group r
               JOIN registry_analytics.person_group c2
                 ON c2.build_run_id=r.build_run_id AND c2.role_context=r.role_context
                AND c2.member_set_hash=r.member_set_hash AND c2.group_kind='CORE_GROUP'
               WHERE r.build_run_id={ANALYTICS_BUILD} AND r.group_kind='RECURRING_GROUP' LIMIT 1""").fetchone()
        check("CORE+RECURRING same member-set exists live", row is not None)
        if row:
            gv = c.execute(
                f"""SELECT group_id FROM registry_analytics.person_group
                   WHERE build_run_id={ANALYTICS_BUILD} AND member_set_hash=%s AND role_context=%s""",
                   (row["member_set_hash"], row["role_context"])).fetchall()
            check("both rows exist as distinct group ids", len(gv) >= 2)


def test_coverage_from_build():
    with _conn() as c:
        b = av.get_active_analytics_build(c)
        cov = av.get_coverage(c, build=b)
        check("coverage loaded from pinned build",
              cov["coverage"]["unresolved_historical"] == 7126
              and cov["build_run_id"] == ANALYTICS_BUILD)
        check("web zero-wording present (B37)", "canonical-linkede" in cov["coverage"]["web_note"])


def test_readonly_no_writes():
    """B6: the analytics repository may only read."""
    src = open("heimdal/modules/analytics/repository.py").read()
    for verb in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE ", "ALTER ", "DROP ", "CREATE "):
        check(f"no '{verb.strip()}' in repository", verb not in src)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print()
    if _FAILS:
        print(f"FAILS: {len(_FAILS)} -> {_FAILS}")
        sys.exit(1)
    print(f"ALL PASS ({len(tests)} tests)")


if __name__ == "__main__":
    main()