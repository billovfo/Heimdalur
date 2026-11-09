"""Phase 4 portal integration tests — pages, gate, authority, temporal
wording, coverage (tasks B53-B58).

Runs standalone (pytest is unavailable in this environment):
    HEIMDAL_READ_DB=$HEIMDAL_READ_DB \
        python3 test_phase4_portal_integration.py

The app talks to the live QA database (read-only). Feature gate is exercised
by monkeypatching the env var before each request.
"""
from __future__ import annotations

from heimdal.pdfhist.db import qa_dsn

import os
import re
import sys

os.environ.setdefault("HEIMDAL_READ_DB",
                      qa_dsn())

from heimdal.app import app
from tests_baseline import ANALYTICS_BUILD, ANALYTICS_UNRESOLVED_HISTORICAL  # noqa: E402

_FAILS = []
app.config["TESTING"] = True


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


def _client():
    return app.test_client()


def _live_ids():
    """Representative canonical ids from the ACTIVE build (read-only)."""
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(os.environ["HEIMDAL_READ_DB"], autocommit=True,
                         row_factory=dict_row) as c:
        person = c.execute(
            f"""SELECT person_a_id AS id FROM registry_analytics.person_person_overlap
               WHERE build_run_id={ANALYTICS_BUILD} GROUP BY 1
               ORDER BY count(*) DESC LIMIT 1""").fetchone()
        person_groups = c.execute(
            f"""SELECT m.canonical_person_id AS id FROM registry_analytics.person_group_member m
               JOIN registry_analytics.person_group g ON g.group_id=m.group_id
               WHERE g.build_run_id={ANALYTICS_BUILD} AND g.group_kind='RECURRING_GROUP'
               GROUP BY 1 ORDER BY count(*) DESC LIMIT 1""").fetchone()
        company = c.execute(
            f"""SELECT canonical_company_id AS id FROM registry_analytics.person_group_observation
               WHERE build_run_id={ANALYTICS_BUILD} GROUP BY 1
               ORDER BY count(*) DESC LIMIT 1""").fetchone()
        fallback = c.execute(
            f"""SELECT person_a_id AS a, person_b_id AS b FROM registry_analytics.person_person_overlap
               WHERE build_run_id={ANALYTICS_BUILD} AND authority_class IS NULL
               AND source_family='PDF_HISTORY' LIMIT 1""").fetchone()
        known = c.execute(
            f"""SELECT person_a_id AS a, person_b_id AS b FROM registry_analytics.person_person_overlap
               WHERE build_run_id={ANALYTICS_BUILD} AND co_participation_class='KNOWN_TEMPORAL_OVERLAP'
               LIMIT 1""").fetchone()
        ever = c.execute(
            f"""SELECT person_a_id AS a, person_b_id AS b FROM registry_analytics.person_person_overlap
               WHERE build_run_id={ANALYTICS_BUILD} AND co_participation_class='SAME_COMPANY_EVER'
               LIMIT 1""").fetchone()
        own = c.execute(
            f"""SELECT person_a_id AS a, person_b_id AS b FROM registry_analytics.person_person_overlap
               WHERE build_run_id={ANALYTICS_BUILD} AND role_context='OWNERSHIP'
               AND co_participation_class='SAME_OBSERVATION' LIMIT 1""").fetchone()
        period_group = c.execute(
            f"""SELECT go.group_id FROM registry_analytics.person_group_observation go
               WHERE go.build_run_id={ANALYTICS_BUILD} AND go.co_observation_kind='SAFE_PERIOD_OVERLAP'
               LIMIT 1""").fetchone()
        core = c.execute(
            f"""SELECT r.member_set_hash, r.role_context FROM registry_analytics.person_group r
               JOIN registry_analytics.person_group k
                 ON k.build_run_id=r.build_run_id AND k.member_set_hash=r.member_set_hash
                AND k.role_context=r.role_context AND k.group_kind='CORE_GROUP'
               WHERE r.build_run_id={ANALYTICS_BUILD} AND r.group_kind='RECURRING_GROUP' LIMIT 1""").fetchone()
    return {"person": person["id"], "person_groups": person_groups["id"],
            "company": company["id"],
            "fallback": (fallback["a"], fallback["b"]) if fallback else None,
            "known": (known["a"], known["b"]) if known else None,
            "ever": (ever["a"], ever["b"]) if ever else None,
            "own": (own["a"], own["b"]) if own else None,
            "period_group": period_group["group_id"] if period_group else None,
            "core_pair": core}


_IDS_CACHE: dict | None = None


def _IDS() -> dict:
    """Lazy live ids (module __getattr__ would also work; explicit is simpler).

    Import-time DB connects break `pytest --collect-only` when the dev DB is
    down, so resolution is deferred to first test use.
    """
    global _IDS_CACHE
    if _IDS_CACHE is None:
        _IDS_CACHE = _live_ids()
    return _IDS_CACHE


def _flag(monkey_value):
    """Set the analytics flag for the next request only."""
    if monkey_value:
        os.environ["REGISTRY_ANALYTICS_ENABLED"] = "1"
    else:
        os.environ.pop("REGISTRY_ANALYTICS_ENABLED", None)


# --- B58: feature gate ---------------------------------------------------------

def test_flag_off_portal_unchanged():
    _flag(False)
    with _client() as cl:
        res = cl.get(f"/persons/{_IDS()['person']}")
        check("flag OFF: person page 200", res.status_code == 200)
        body = res.get_data(as_text=True)
        check("flag OFF: no analytics tab", "data-panel=\"analyse\"" not in body)
        check("flag OFF: no analytics section", "Observeret samarbejde (analyse)" not in body)
        res2 = cl.get(f"/companies/{_IDS()['company']}")
        check("flag OFF: company page 200", res.status_code == 200)
        check("flag OFF: no company analytics section",
              "Konstellationer i analyse-buildet" not in res.get_data(as_text=True))


def test_flag_on_sections_render():
    _flag(True)
    try:
        with _client() as cl:
            res = cl.get(f"/persons/{_IDS()['person']}")
            body = res.get_data(as_text=True)
            check("flag ON: person page 200", res.status_code == 200)
            check("flag ON: analytics tab present", "data-panel=\"analyse\"" in body)
            check("flag ON: analytics heading present",
                  "Observeret samarbejde (analyse)" in body)
            res2 = cl.get(f"/companies/{_IDS()['company']}")
            body2 = res2.get_data(as_text=True)
            check("flag ON: company page 200", res2.status_code == 200)
            check("flag ON: company analytics present",
                  "Konstellationer i analyse-buildet" in body2)
    finally:
        _flag(False)


# --- B53: person page rendering -------------------------------------------------

def test_person_page_collaborators():
    _flag(True)
    try:
        with _client() as cl:
            body = cl.get(f"/persons/{_IDS()['person']}").get_data(as_text=True)
            check("top collaborators render (person links)",
                  "/persons/" in body
                  and re.search(r"\d+ fælles virksomheder", body) is not None)
            check("no opaque score vocabulary",
                  not re.search(r"alliance[_ -]?score|relationship[_ -]?score|"
                                r"strength[_ -]?score|friend[_ -]?score", body, re.I))
            check("no raw JSON/repr leak",
                  "{'" not in body and '"support_company_count":' not in body)
            check("coverage note renders", "Dækning" in body)
            check("temporal honesty: no invented tenure wording",
                  "ansættelsestid" not in body.lower()
                  and "Ejerperiode" not in body)
    finally:
        _flag(False)


def test_pair_evidence_api_reader():
    """B63 drilldowns via the reader contract (pair classes)."""
    from heimdal.modules.analytics import views as av
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(os.environ["HEIMDAL_READ_DB"], autocommit=True,
                         row_factory=dict_row) as c:
        b = av.get_active_analytics_build(c)
        for label, key in (("mixed fallback", "fallback"),
                           ("KNOWN_TEMPORAL_OVERLAP", "known"),
                           ("SAME_COMPANY_EVER", "ever"),
                           ("ownership SAME_OBSERVATION", "own")):
            pair = _IDS()[label if label in _IDS() else key]
            if pair is None:
                check(f"drilldown {key}: pair exists", False, "no live row")
                continue
            ev = av.get_pair_evidence(c, pair[0], pair[1], build=b)
            check(f"drilldown {key} resolves", ev and ev["rows"])
            if key == "fallback":
                check("fallback drilldown explains NULL authority",
                      any(r["stored_authority_class"] is None
                          and r["fallback_note"] for r in ev["rows"]))


def test_group_evidence_drilldowns():
    from heimdal.modules.analytics import views as av
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(os.environ["HEIMDAL_READ_DB"], autocommit=True,
                         row_factory=dict_row) as c:
        b = av.get_active_analytics_build(c)
        ev = av.get_group_evidence(c, _IDS()["period_group"], build=b)
        check("SAFE_PERIOD_OVERLAP group evidence resolves", ev and ev["group"])
        kinds = {e["co_observation_kind"] for e in ev["group"]["episodes"]}
        check("episode kinds typed", kinds, str(kinds))
        if "SAFE_PERIOD_OVERLAP" in kinds:
            ep = next(e for e in ev["group"]["episodes"]
                      if e["co_observation_kind"] == "SAFE_PERIOD_OVERLAP")
            text = av.episode_render_text(ep)
            check("overlap episode wording", "Observeret overlap" in text)


# --- B55: authority assertions ---------------------------------------------------

def test_authority_labels():
    from heimdal.modules.analytics import views as av
    check("Felag is the sole current authority label",
          av.AUTHORITY_CLASS_LABELS["AUTHORITATIVE_CURRENT_GOVERNANCE"]
          == "Aktuel autoritativ status (Felag)")
    check("historical PDF labeled historical",
          "Historisk" in av.AUTHORITY_CLASS_LABELS["HISTORICAL_PDF_OBSERVATION"])
    check("web governance labeled supplementary",
          "supplerende" in av.SOURCE_FAMILY_LABELS["OWNERSHIP_WEB_GOVERNANCE"])
    # the views never present web governance as current authority
    import inspect
    src = inspect.getsource(av)
    check("web governance never mapped to current wording",
          "OWNERSHIP_WEB_GOVERNANCE" not in
          src.split("AUTHORITY_CLASS_LABELS")[1].split("}")[0])


def test_temporal_wording_metadata():
    from heimdal.modules.analytics import views as av
    from heimdal.modules.analytics.views import episode_render_text, _period_view
    # BOUNDED -> duration allowed
    p = {"period_id": 1, "canonical_company_id": 1, "role_context": "BOARD",
         "period_start": None, "period_end": None, "start_status": "KNOWN",
         "end_status": "KNOWN", "period_status": "BOUNDED", "source_family": "PDF_HISTORY"}
    class _D:
        def strftime(self, _f):
            return "01.01.2024"
    p["period_start"] = _D(); p["period_end"] = _D()
    v = _period_view(p)
    check("BOUNDED shows interval", v["duration_known"] and "–" in v["text"])
    # OPEN_LEFT_CENSORED -> no invented original start
    p["period_status"] = "OPEN_LEFT_CENSORED"; p["period_end"] = None
    v = _period_view(p)
    check("OPEN_LEFT_CENSORED honest wording",
          "oprindelig start ukendt" in v["text"] and not v["duration_known"])
    # CLOSED_ONLY -> no duration
    p["period_status"] = "CLOSED_ONLY"; p["period_start"] = None
    v = _period_view(p)
    check("CLOSED_ONLY no start, no duration",
          "Tidligere registreret" in v["text"] and not v["duration_known"])
    # ownership point -> never a period
    ep = {"co_observation_kind": "SNAPSHOT", "episode_start": None,
          "episode_end": None}
    check("snapshot episode: no duration",
          "–" not in av.episode_render_text(ep))
    ep = {"co_observation_kind": "SAME_EVENT", "episode_start": "01.01.2024",
          "episode_end": "01.01.2024"}
    check("same event: no inferred roster tenure",
          av.episode_render_text(ep) == "Observeret sammen i samme registreringshændelse")


# --- B57: coverage with unresolved evidence --------------------------------------

def test_coverage_unresolved_surfaced():
    _flag(True)
    try:
        with _client() as cl:
            body = cl.get(f"/persons/{_IDS()['person_groups']}").get_data(as_text=True)
            # the page formats the count with the Danish thousands separator
            unresolved_dk = f"{ANALYTICS_UNRESOLVED_HISTORICAL:,}".replace(",", ".")
            check("unresolved count surfaced as limitation",
                  unresolved_dk in body and "ikke med" in body)
            check("web zero honest wording",
                  "canonical-linkede web governance" in body)
    finally:
        _flag(False)


# --- B53: click-through targets are canonical -------------------------------------

def test_clickthrough_only_canonical():
    _flag(True)
    try:
        with _client() as cl:
            body = cl.get(f"/persons/{_IDS()['person']}").get_data(as_text=True)
            hrefs = set(re.findall(r'href="/(persons|companies)/(\d+)"', body))
            check("analytics links are canonical /persons|/companies/:id",
                  len(hrefs) > 0)
            bad = [h for h in hrefs if h[0] not in ("persons", "companies")]
            check("no non-canonical analytics hrefs", not bad, str(bad[:3]))
    finally:
        _flag(False)


# --- B56: ownership point wording on company people analytics ---------------------

def test_ownership_point_wording():
    from heimdal.modules.analytics import views as av
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(os.environ["HEIMDAL_READ_DB"], autocommit=True,
                         row_factory=dict_row) as c:
        b = av.get_active_analytics_build(c)
        comp = c.execute(
            f"""SELECT canonical_company_id AS id FROM registry_analytics.person_company_observation
               WHERE build_run_id={ANALYTICS_BUILD} AND source_family='OWNERSHIP_GRAPH'
               GROUP BY 1 HAVING count(*) > 1 LIMIT 1""").fetchone()
        check("company with ownership observations exists", comp is not None)
        if comp:
            data = av.get_company_people_analytics(c, comp["id"], build=b)
            check("company people analytics resolves", data and not data.get("error"))
            check("ownership observation date exposed (point semantics)",
                  all("ejerperiode" not in str(p).lower()
                      and "har ejet siden" not in str(p).lower()
                      for p in data["people"]))


def main():
    import atexit
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    _flag(False)
    print()
    if _FAILS:
        print(f"FAILS: {len(_FAILS)} -> {_FAILS}")
        sys.exit(1)
    print(f"ALL PASS ({len(tests)} tests)")


if __name__ == "__main__":
    main()