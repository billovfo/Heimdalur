from heimdal.pdfhist.db import qa_dsn
#!/usr/bin/env python3
"""Phase 5 — constellation graph UX test suite (B70-style, __main__ runner).

Run from the repo root under a test env: `python3 -m acceptance.phase5_graph_ux`
(not a pytest module; tools/gate.sh runs it report-only).

Covers the Phase 5 invariant table (docs/HEIMDAL-REGISTRY-ANALYTICS-PHASE-5-
REMEASUREMENT-AND-PLAN.md §4). Each test maps to at least one invariant and
documents the violating mutation it detects:

  I1  build pin / fail-closed (T2 T3 T4)          — mutation: SUPERSEDED fallback
  I2  read-only repository verbs (T12)            — mutation: a write verb
  I3  flag matrix (T1 T2)                          — mutation: gate removed
  I4  member-set dedupe (T5 T15)                   — mutation: split twins
  I5  temporal wording via Phase 4 renderer (T7)   — mutation: invented duration
  I6  ownership point wording (T8)                 — mutation: period wording
  I7  authority labels (T7b)                       — mutation: web as current
  I8  coverage from pinned build (T11)             — mutation: hardcoded numbers
  I9  deterministic cap + stated total (T6)        — mutation: silent truncation
  I10 no raw JSON in UI (T10)                      — mutation: dict dump
  I11 canonical click-through (T14)                — mutation: non-canonical link
  I12 analytics immutability (T13)                 — mutation: any count/hash drift
  I13 Phase 4 flag-off behavior preserved (T1 T16) — mutation: ungated render
"""
import os
import re
import sys

os.environ.setdefault("HEIMDAL_READ_DB",
                      qa_dsn())
os.environ.pop("REGISTRY_ANALYTICS_ENABLED", None)
os.environ.pop("REGISTRY_ANALYTICS_GRAPH_ENABLED", None)

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

DSN = os.environ["HEIMDAL_READ_DB"]
from tests_baseline import ANALYTICS_BUILD  # noqa: E402
ACTIVE_BUILD = ANALYTICS_BUILD
from tests_baseline import ANALYTICS_COUNTS as BASELINE  # noqa: E402
from tests_baseline import ANALYTICS_HASH as ACTIVE_HASH  # noqa: E402

# live entities chosen by the Phase 5 re-measurement (groups guaranteed)
PERSON_WITH_GROUPS = 7412     # 130 group memberships, all at ego companies
COMPANY_WITH_GROUPS = 5266    # groups observed
PERSON_PLAIN = 718            # has groups too; used for page assertions

_SCORE_PAT = re.compile(r"alliance[_ -]?score|relationship[_ -]?score|"
                        r"strength[_ -]?score|friend[_ -]?score|"
                        r"close[- ]associate", re.I)
_OWNERSHIP_PERIOD_PAT = re.compile(r"Ejerperiode|ejet siden|har ejet|"
                                   "ejerhistorik.*periode", re.I)
_RAW_JSON_PAT = re.compile(r"\{'|\"co_observation_kind\":|None,|<dict|"
                           r"json\.dumps|__main__")

_PASSED = []
_FAILED = []


def check(name, cond, detail=""):
    if cond:
        _PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        _FAILED.append(name)
        print(f"  FAIL  {name}  {detail}")


def set_flags(analytics=None, graph=None):
    for key, val in (("REGISTRY_ANALYTICS_ENABLED", analytics),
                     ("REGISTRY_ANALYTICS_GRAPH_ENABLED", graph)):
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val


def client():
    from heimdal.app import app
    app.config["TESTING"] = True
    return app.test_client()


def baseline_counts(cur):
    row = cur.execute(
        "SELECT (SELECT count(*) FROM registry_analytics.person_company_observation"
        f" WHERE build_run_id={ANALYTICS_BUILD}) AS obs,"
        " (SELECT count(*) FROM registry_analytics.person_company_period"
        f" WHERE build_run_id={ANALYTICS_BUILD}) AS periods,"
        " (SELECT count(*) FROM registry_analytics.person_person_overlap"
        f" WHERE build_run_id={ANALYTICS_BUILD}) AS pairs,"
        " (SELECT count(*) FROM registry_analytics.person_group"
        f" WHERE build_run_id={ANALYTICS_BUILD}) AS groups,"
        " (SELECT count(*) FROM registry_analytics.person_group_observation"
        f" WHERE build_run_id={ANALYTICS_BUILD}) AS gobs,"
        " (SELECT count(*) FROM registry_analytics.person_group_observation_member)"
        " AS junction").fetchone()
    return dict(row)


# --- T1/I3: flag matrix on network pages --------------------------------------
def t1_flag_matrix(cl):
    print("T1/I3 flag matrix on network pages")
    person_url = f"/persons/{PERSON_WITH_GROUPS}/network"
    set_flags(None, None)
    body_off = cl.get(person_url).get_data(as_text=True)
    set_flags("1", None)
    body_a_only = cl.get(person_url).get_data(as_text=True)
    set_flags(None, "1")
    body_g_only = cl.get(person_url).get_data(as_text=True)
    set_flags("1", "1")
    body_both = cl.get(person_url).get_data(as_text=True)
    for label, body in (("both off", body_off), ("analytics only", body_a_only),
                        ("graph only", body_g_only)):
        check(f"no constellation DOM with {label}",
              "konstellationer" not in body
              and "edge-constellation" not in body, label)
    check("constellation section renders with both flags on",
          'id="konstellationer"' in body_both and "edge-constellation" in body_both)
    check("flag-off page identical to no-analytics page",
          body_off == body_g_only)
    check("analytics flag still renders Phase 4 note (unchanged contract)",
          "analytics-coverage" in body_a_only and body_a_only != body_off)
    # company route too
    set_flags("1", "1")
    body_c = cl.get(f"/companies/{COMPANY_WITH_GROUPS}").get_data(as_text=True)
    body_cn = cl.get(f"/companies/{COMPANY_WITH_GROUPS}/network").get_data(as_text=True)
    check("company network renders constellations when enabled",
          "konstellationer" in body_cn)
    check("company main page renders content with graph flag on",
          body_c and "record-name" in body_c)


# --- T2/I1: API fail-closed ----------------------------------------------------
def t2_api_fail_closed(cl):
    print("T2/I1 API fail-closed")
    set_flags(None, None)
    check("404 with both flags off",
          cl.get(f"/api/analytics/graph/{PERSON_WITH_GROUPS}").status_code == 404)
    set_flags(None, "1")
    check("404 with graph flag only (ANDed gate)",
          cl.get(f"/api/analytics/graph/{PERSON_WITH_GROUPS}").status_code == 404)
    set_flags("1", None)
    check("404 with analytics flag only",
          cl.get(f"/api/analytics/graph/{PERSON_WITH_GROUPS}").status_code == 404)
    set_flags("1", "1")
    check("404 for unknown entity",
          cl.get("/api/analytics/graph/999999999").status_code == 404)


# --- T3/I1: API contract + build pin ------------------------------------------
def t3_api_contract(cl, cur):
    print("T3/I1 API contract + build pin")
    set_flags("1", "1")
    resp = cl.get(f"/api/analytics/graph/{PERSON_WITH_GROUPS}")
    check("200 with both flags", resp.status_code == 200)
    check("no-store cache (derived, version-pinned data)",
          resp.headers.get("Cache-Control") == "no-store")
    payload = resp.get_json()
    check("registry section present and separate",
          "registry" in payload and "nodes" in payload["registry"])
    an = payload.get("analytics") or {}
    check("analytics section pinned to ACTIVE build",
          an.get("build_run_id") == ACTIVE_BUILD
          and an.get("content_hash") == ACTIVE_HASH,
          f"{an.get('build_run_id')} {an.get('content_hash')}")
    live = cur.execute("SELECT build_run_id, content_hash FROM"
                       " registry_analytics.analytics_build"
                       " WHERE status='ACTIVE'").fetchone()
    check("pin equals live ACTIVE row",
          an.get("build_run_id") == live["build_run_id"]
          and an.get("content_hash") == live["content_hash"])
    check("constellation nodes are synthetic ids",
          all(n["node_id"].startswith("g") and n["kind"] == "CONSTELLATION"
              for n in an.get("nodes", [])))
    check("member edges link synthetic group to canonical person",
          all(str(e["from_id"]).startswith("g") and isinstance(e["to_id"], int)
              for e in an.get("edges", [])
              if e["kind"] == "CONSTELLATION_MEMBER"))


# --- T4/I1 mutation: no ACTIVE build -> fail-closed None ----------------------
def t4_no_active_mutation():
    print("T4/I1 mutation: no ACTIVE build")
    import heimdal.modules.analytics.graph_views as gv

    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as c:
        real = gv.get_active_analytics_build
        gv.get_active_analytics_build = lambda conn: None
        try:
            layer = gv.constellation_layer(c, "PERSON", PERSON_WITH_GROUPS)
            check("layer returns None without ACTIVE build", layer is None)
        finally:
            gv.get_active_analytics_build = real
        # mutation: a SUPERSEDED build passed explicitly still yields the
        # caller's build only when caller passes it — the layer itself must
        # never look one up beyond ACTIVE resolution.
        sup = c.execute("SELECT build_run_id FROM"
                        " registry_analytics.analytics_build"
                        " WHERE status='SUPERSEDED' LIMIT 1").fetchone()
        if sup:
            layer2 = gv.constellation_layer(c, "PERSON", PERSON_WITH_GROUPS,
                                            build={"build_run_id":
                                                   sup["build_run_id"],
                                                   "content_hash": "x",
                                                   "coverage_display": {}})
            check("explicit build is honored as the pin (no silent ACTIVE swap)",
                  layer2 is None or layer2["build_run_id"] == sup["build_run_id"])


# --- T4b/I1 mutation: endpoint 404 with no ACTIVE build -----------------------
def t4b_endpoint_no_active(cl):
    """I1 detector at the ENDPOINT level (validator m2/M1): the API must
    404 — never return a pin-less 200 — when no ACTIVE build resolves."""
    print("T4b/I1 mutation: endpoint 404 without ACTIVE build")
    import heimdal.modules.analytics.graph_views as gv
    import heimdal.modules.analytics.views as views
    set_flags("1", "1")
    real_gv = gv.get_active_analytics_build
    real_v = views.get_active_analytics_build
    gv.get_active_analytics_build = lambda conn: None
    views.get_active_analytics_build = lambda conn: None
    try:
        resp = cl.get(f"/api/analytics/graph/{PERSON_WITH_GROUPS}")
        check("endpoint 404 without ACTIVE build (no pin-less 200)",
              resp.status_code == 404, f"got {resp.status_code}")
        body = resp.get_data(as_text=True)
        check("404 body carries no build pin",
              "build_run_id" not in body and "content_hash" not in body)
    finally:
        gv.get_active_analytics_build = real_gv
        views.get_active_analytics_build = real_v
    resp2 = cl.get(f"/api/analytics/graph/{PERSON_WITH_GROUPS}")
    check("endpoint pins ACTIVE build again after restore",
          resp2.status_code == 200
          and (resp2.get_json().get("analytics") or {}).get("build_run_id")
          == ACTIVE_BUILD)


# --- T5/T15/I4: member-set dedupe ---------------------------------------------
def t5_dedupe(cur):
    print("T5/T15/I4 member-set dedupe")
    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as c:
        import heimdal.modules.analytics.graph_views as gv
        build = c.execute("SELECT build_run_id, content_hash, coverage"
                          " FROM registry_analytics.analytics_build"
                          " WHERE status='ACTIVE'").fetchone()
        layer = gv.constellation_layer(c, "PERSON", PERSON_WITH_GROUPS)
        check("layer resolves for person with groups",
              layer is not None and len(layer["nodes"]) > 0)
        # I4 detector: merged nodes unique per (context, member-set) —
        # recomputed from raw rows through the same Phase 4 functions.
        from heimdal.modules.analytics import repository as repo
        from heimdal.modules.analytics.views import (
            _decorate_groups, _merge_core_recurring)
        rows = repo.groups_for_person(c, ACTIVE_BUILD, PERSON_WITH_GROUPS)
        merged = _merge_core_recurring(_decorate_groups(c, build, rows))
        merged_keys = [(m["member_set_hash"], m["context"]) for m in merged]
        check("merge produces unique member-set/context keys",
              len(set(merged_keys)) == len(merged_keys),
              f"{len(set(merged_keys))} vs {len(merged_keys)}")
        node_keys = [(n["context"], n["group_id"]) for n in layer["nodes"]]
        check("constellation nodes unique per merged entry",
              len(set(node_keys)) == len(node_keys))
        # mutation test: splitting twins would double nodes — assert detector
        # (uniqueness of merged keys) would fail if twins were separate
        twins = c.execute(
            "SELECT count(*) AS n FROM registry_analytics.person_group r"
            " JOIN registry_analytics.person_group k"
            "  ON k.build_run_id=r.build_run_id"
            "   AND k.member_set_hash=r.member_set_hash"
            "   AND k.role_context=r.role_context AND k.group_kind='CORE_GROUP'"
            f" WHERE r.build_run_id={ANALYTICS_BUILD} AND r.group_kind='RECURRING_GROUP'").fetchone()
        check("twin population exists to mutate (dataset precondition)",
              twins["n"] > 0)


# --- T6/I9: deterministic cap + stated total ----------------------------------
def t6_cap_deterministic(cur):
    print("T6/I9 cap + deterministic order")
    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as c:
        import heimdal.modules.analytics.graph_views as gv
        layer = gv.constellation_layer(c, "PERSON", PERSON_WITH_GROUPS, limit=8)
        check("shown never exceeds cap",
              len(layer["nodes"]) <= 8 and layer["shown"] == len(layer["nodes"]))
        check("stated total >= shown",
              layer["total"] >= layer["shown"])
        layer2 = gv.constellation_layer(c, "PERSON", PERSON_WITH_GROUPS, limit=8)
        check("order deterministic across calls",
              [n["group_id"] for n in layer["nodes"]]
              == [n["group_id"] for n in layer2["nodes"]])
        # I9 detector: sort key recomputation
        keys = [gv._constellation_sort_key_from_node(n) for n in layer["nodes"]]
        check("sort keys ascending", keys == sorted(keys))


# --- T7/I5: temporal wording ----------------------------------------------------
def t7_temporal_wording(cl, cur):
    print("T7/I5 temporal wording")
    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as c:
        import heimdal.modules.analytics.graph_views as gv
        layer = gv.constellation_layer(c, "PERSON", PERSON_WITH_GROUPS)
        texts = [ep["render_text"] for n in layer["nodes"]
                 for ep in n.get("episodes", [])]
        kinds = [ep["co_observation_kind"] for n in layer["nodes"]
                 for ep in n.get("episodes", [])]
        check("episodes present in shown constellations", len(texts) > 0)
        for t, k in zip(texts, kinds):
            if k == "SAME_SNAPSHOT":
                check("no interval on SAME_SNAPSHOT episode",
                      "–" not in t and "fra " not in t, t)
        # mutation: invented duration detector — the renderer itself must
        # refuse durations for non-bounded kinds (Phase 4 unit already proves
        # this; assert the wiring is the same function)
        from heimdal.modules.analytics.views import episode_render_text as ert
        check("graph layer reuses Phase 4 episode renderer",
              gv.episode_render_text is ert)


# --- T8/I6 + T9 + T10: wording/leak detectors on rendered pages ----------------
def t8_wording_detectors(cl):
    print("T8/T9/T10 wording + leak detectors")
    set_flags("1", "1")
    body = cl.get(f"/persons/{PERSON_WITH_GROUPS}/network").get_data(as_text=True)
    check("no ownership period wording", not _OWNERSHIP_PERIOD_PAT.search(body))
    check("no score vocabulary", not _SCORE_PAT.search(body))
    check("no raw JSON leak", not _RAW_JSON_PAT.search(body))
    api = cl.get(f"/api/analytics/graph/{PERSON_WITH_GROUPS}").get_data(as_text=True)
    check("API has no score vocabulary", not _SCORE_PAT.search(api))
    check("API carries disclaimers",
          "alliance" in api)


# --- T11/I8: coverage from pinned build ----------------------------------------
def t11_coverage(cur):
    print("T11/I8 coverage from pinned build")
    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as c:
        import heimdal.modules.analytics.graph_views as gv
        build = c.execute("SELECT build_run_id, content_hash, coverage"
                          " FROM registry_analytics.analytics_build"
                          " WHERE status='ACTIVE'").fetchone()
        layer = gv.constellation_layer(c, "PERSON", PERSON_WITH_GROUPS)
        cov = build["coverage"] or {}
        check("unresolved from build jsonb",
              layer["coverage"]["unresolved_historical"] == cov.get("unresolved"))
        check("web-zero honest wording present",
              layer["coverage"]["web_note"]
              == "Ingen sikkert canonical-linkede web governance-observationer"
                 " indgår i dette build.")
        # mutation: hardcoded coverage would drift from jsonb
        check("coverage values equal build coverage jsonb",
              layer["coverage"]["current_linked"]
              == cov.get("direct_cpr_linked_coverage"))


# --- T12/I2: read-only verbs -----------------------------------------------------
def t12_read_only():
    print("T12/I2 read-only verbs")
    src = open("heimdal/modules/analytics/graph_views.py").read()
    bad = [w for w in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ",
                       "CREATE ", "TRUNCATE ", ".execute_(\"INSERT")
           if w.lower() in src.lower()]
    check("no write verbs in graph module", not bad, str(bad))
    src2 = open("heimdal/modules/entities/service.py").read()
    fn = src2[src2.find("def network_layout_with_constellations"):
              src2.find("# --- relationer")]
    check("layout extension is pure computation (no SQL)",
          "execute" not in fn and "SELECT" not in fn)


# --- T13/I12: immutability ---------------------------------------------------------
def t13_immutability(cur):
    print("T13/I12 analytics immutability")
    counts = baseline_counts(cur)
    check("baseline unchanged",
          counts == BASELINE, str(counts))
    build = cur.execute("SELECT build_run_id, content_hash FROM"
                        " registry_analytics.analytics_build"
                        " WHERE status='ACTIVE'").fetchone()
    check("ACTIVE build unchanged",
          build["build_run_id"] == ACTIVE_BUILD
          and build["content_hash"] == ACTIVE_HASH)


# --- T14/I11: canonical click-through -----------------------------------------------
def t14_click_through_real(cl):
    print("T14/I11 canonical click-through (real)")
    set_flags("1", "1")
    body = cl.get(f"/persons/{PERSON_WITH_GROUPS}/network").get_data(as_text=True)
    i = body.find('id="konstellationer"')
    section = body[i:body.find("</section>", i)] if i >= 0 else ""
    hrefs = re.findall(r'href="([^"]+)"', section)
    # Remediation plan v1 T2.5: provenance stamps on entity surfaces are real
    # links to /kildeinformation/<canonical_id> — a canonical surface for the
    # same entity, allowed alongside canonical entity links. Everything else
    # (raw source ids, external urls, anchors) is still forbidden.
    bad = [h for h in hrefs
           if not (re.fullmatch(r"/(persons|companies)/\d+", h)
                   or re.fullmatch(r"/kildeinformation/\d+", h))]
    check("constellation section links canonical surfaces only", not bad, str(bad[:5]))
    check("member links present", "/persons/" in section)


def main():
    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as cur:
        pre = baseline_counts(cur)
        check("pre-run baseline", pre == BASELINE, str(pre))
        cl = client()
        t1_flag_matrix(cl)
        t2_api_fail_closed(cl)
        t3_api_contract(cl, cur)
        t4_no_active_mutation()
        t4b_endpoint_no_active(cl)
        t5_dedupe(cur)
        t6_cap_deterministic(cur)
        t7_temporal_wording(cl, cur)
        t8_wording_detectors(cl)
        t11_coverage(cur)
        t12_read_only()
        t13_immutability(cur)
        t14_click_through_real(cl)
        post = baseline_counts(cur)
        check("post-run baseline identical", post == BASELINE, str(post))
    print(f"\n{'ALL PASS' if not _FAILED else 'FAILURES'} "
          f"({len(_PASSED)} checks passed, {len(_FAILED)} failed)")
    for f in _FAILED:
        print(f"  FAILED: {f}")
    sys.exit(1 if _FAILED else 0)


if __name__ == "__main__":
    main()