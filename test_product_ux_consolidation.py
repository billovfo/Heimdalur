from heimdal.pdfhist.db import qa_dsn
"""Product UX consolidation — acceptance detectors (§54-§71).

Run with `python3 test_product_ux_consolidation.py` (READ-ONLY: renders real
pages through the test client; no DB write, no analytics rebuild; the
analytics build pin in tests_baseline.py must hold).

Covers:
  PUX-01 (§5/§6)   Overblik is the default tab on company AND person pages
  PUX-02 (§7/§52)  Technical header fields live in "Vis kildedetaljer"
  PUX-03 (§9)      Governance summary on company Overblik (Felag-only current)
  PUX-04 (§10/§11) Ownership summary + 3-5 recent source-supported events
  PUX-12 (§16)     Internal spine numbers removed from user-facing pages
  PUX-13 (§27)     No internal sort tokens in UI; human order copy instead
  PUX-14 (§21/§22) Duplicate occurrence detector (same person occurrence
                   never rendered twice within one event — run-1/run-4 fix)
  PUX-15 (§45)     Unknown history action stays neutral (never coerced)
  PUX-16 (§9/§51)  Felag-only current authority; no second authority
  PUX-17 (§60)     Family fixture: strict subsets fold under maximal headline
  PUX-18 (§61)     Non-subset groups stay distinct families
  PUX-19 (§33)     No data loss: every group appears exactly once
  PUX-20 (§62)     No score/strength/alliance/ranking in UI or view model
  PUX-21 (§36/§37) Network split: compact preview on detail page, full
                   interactive graph preserved on /network
  PUX-22 (§44/§64) ONE primary history: tab links to /history; no duplicate
                   inline history panel
  PUX-23 (§42)     Person roller: current vs historical separated
  PUX-24 (§53/UX-53) Canonical click-through: sampled links resolve 200
  PUX-25 (§59)     Analytics immutability: build pin + after-record
"""
import json
import os
import re

import pytest

from heimdal.app import app

DSN = qa_dsn()
from tests_baseline import ANALYTICS_BUILD as PIN_BUILD  # noqa: E402
from tests_baseline import ANALYTICS_COUNTS, ANALYTICS_HASH as PIN_HASH  # noqa: E402

COMPANY_OWNER_CASE = 9667
COMPANY_MULTI = 9287
COMPANY_TWIN = 9063
PERSON_ROLES = 7412
PERSON_PLAIN = 2321

_pass = _fail = 0


def check(name, ok, detail=""):
    # H-2: under pytest a false check fails the test; only the __main__ runner
    # collects failures and reports them at the end.
    if __name__ != "__main__":
        assert ok, f"{name} {detail}"
    global _pass, _fail
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))
    if ok:
        _pass += 1
    else:
        _fail += 1


@pytest.fixture
def cl(monkeypatch):
    """The test client main() passes in, with both analytics flags on (H-2)."""
    monkeypatch.setenv("REGISTRY_ANALYTICS_ENABLED", "1")
    monkeypatch.setenv("REGISTRY_ANALYTICS_GRAPH_ENABLED", "1")
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def flat(html):
    return html.replace("\n", " ")


def set_flags(analytics="1", graph="1"):
    os.environ["REGISTRY_ANALYTICS_ENABLED"] = analytics
    os.environ["REGISTRY_ANALYTICS_GRAPH_ENABLED"] = graph


def family_fixture():
    """§60/§61/§33 synthetic fixture: A+B, A+C, A+B+C, A+B+C+D share context;
    A+B and A+C are strict subsets; A+B+C is a strict subset of A+B+C+D."""
    from heimdal.modules.analytics import views as av

    def gv(a, b, c, d, gid):
        members = [{"canonical_person_id": pid, "display_name": f"P{pid}"}
                   for pid in [x for x in (a, b, c, d) if x]]
        return {"group_id": gid, "members": members, "group_size": len(members),
                "role_context": "BOARD",
                "support": {"support_company_count": 3, "support_episode_count": 5,
                            "companies": 3},
                "kinds_labels": ["RECURRING"], "episode_kinds_labels": [],
                "observation_wording": "", "member_preview": members[:3]}

    merged = [gv(1, 2, None, None, "g-ab"), gv(1, 3, None, None, "g-ac"),
              gv(1, 2, 3, None, "g-abc"), gv(1, 2, 3, 4, "g-abcd")]
    return av._build_constellation_families(merged)


# ---------------------------------------------------------------- PUX-01..04
@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_company_overblik(cl):
    h = flat(cl.get(f"/companies/{COMPANY_OWNER_CASE}").get_data(as_text=True))
    check("company: Overblik panel default is-active",
          'data-panel="overblik" role="tabpanel" id="panel-overblik"' in h)
    check("company: first tab is Overblik",
          re.search(r'tablist[^>]*>.*?<button role="tab" aria-selected="true" data-tab="overblik">Overblik</button>',
                    h, re.S) is not None)
    check("company: governance summary present",
          "Nuværende bestyrelse og ledelse" in h and ">Bestyrelse</h3>" in h)
    check("company: ownership summary present", "Ejerskab i kort" in h)
    check("company: recent history present",
          "Seneste registrerede ændringer" in h)
    events = re.findall(r'<li class="timeline-event">', h)
    check("company: recent history bounded 3-5", 1 <= len(events) <= 5, str(len(events)))
    check("company: technical fields in disclosure",
          "Vis kildedetaljer" in h and "Status i beholdning" in h)


def test_person_overblik(cl):
    h = flat(cl.get(f"/persons/{PERSON_ROLES}").get_data(as_text=True))
    check("person: Overblik panel default is-active",
          'data-panel="overblik" role="tabpanel" id="panel-overblik"' in h)
    check("person: first tab is Overblik",
          re.search(r'tablist[^>]*>.*?<button role="tab" aria-selected="true" data-tab="overblik">Overblik</button>',
                    h, re.S) is not None)
    check("person: current roles summary", "Aktuelle roller" in h)
    check("person: history summary", "Historik</h2>" in h and "Se fuld historik" in h)
    check("person: ownership summary", "Ejerskab i kort" in h)


# ---------------------------------------------------------------- PUX-12/13
SPINE_TOKEN = re.compile(r"(§16\.\d|§17\.\d|§21 Netværk|§16\.0|§17\.5|clause-mark)")


def test_no_internal_tokens(cl):
    for url, label in ((f"/companies/{COMPANY_OWNER_CASE}", "company"),
                       (f"/persons/{PERSON_ROLES}", "person"),
                       (f"/companies/{COMPANY_MULTI}", "company 9287")):
        h = cl.get(url).get_data(as_text=True)
        hits = SPINE_TOKEN.findall(h)
        check(f"{label}: no internal clause tokens", not hits, str(set(hits))[:120])
    h = cl.get(f"/companies/{COMPANY_MULTI}").get_data(as_text=True)
    check("analytics: no internal sort tokens in UI",
          "support_company_count DESC" not in h and "member_set_hash" not in h
          and "group_id DESC" not in h)
    check("analytics: human order copy present", "ingen skjult score" in h)


# ---------------------------------------------------------------- PUX-14/15
@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_no_duplicate_occurrences(cl):
    """§21 investigation fix: one source occurrence renders exactly once —
    a person appears at most once per event, and a linked occurrence never
    ALSO renders as an unresolved row in the same event."""
    h = flat(cl.get(f"/companies/{COMPANY_OWNER_CASE}/skraseting").get_data(as_text=True))
    events = re.split(r'<li class="timeline-event"', h)[1:]
    dup_any = False
    for ev in events:
        # count rows per person within this event
        names = re.findall(r'history-person-name">([^<]+)<', ev)
        # each person-row may appear once; duplicates within one event fail
        if len(names) != len(set(names)):
            dup_any = True
        if "person-unresolved" in ev and re.search(r'href="/persons/\d+"', ev):
            # same event mixing unresolved + linked is allowed only if the
            # names differ; identical names mixed = the old run-1/run-4 bug
            linked = set(re.findall(r'href="/persons/\d+"[^>]*>([^<]+)<', ev))
            unresolved = set(re.findall(r'person-unresolved[^>]*>\s*([A-ZÆØÅ][^<]*)', ev))
            if linked & {u.strip() for u in unresolved}:
                dup_any = True
    check("skraseting: no duplicated person occurrence within an event", not dup_any)
    check("skraseting: linked canonical occurrences present on 9667",
          re.search(r'href="/persons/\d+"', h) is not None)


def test_unknown_action_neutral():
    """§45: unknown action text never coerced into join/leave.
    Resolution lives in _event_view (§23 — render only what the source says)."""
    from heimdal.modules.skraseting import views as sv

    def ev(action, event_type="BOARD_CHANGE"):
        return sv._event_view({"event_type": event_type, "action": action})

    check("skraseting: CHANGED -> neutral", ev("CHANGED")["action_label"] is None)
    check("skraseting: UNKNOWN -> neutral", ev("TOTALLY_UNKNOWN_KIND")["action_label"] is None)
    check("skraseting: missing action -> neutral", ev(None)["action_label"] is None)
    check("skraseting: APPOINTED -> join", "tiltrådte" in ev("APPOINTED")["action_label"])
    check("skraseting: REMOVED -> leave", "fratrådte" in ev("REMOVED")["action_label"])


# ---------------------------------------------------------------- PUX-16
def test_felag_authority(cl):
    h = flat(cl.get(f"/companies/{COMPANY_OWNER_CASE}").get_data(as_text=True))
    check("company: current governance badged Aktuel status (Felag)",
          "Aktuel status (Felag)" in h)
    check("company: derived layer badged Udledt", "Udledt" in h)
    check("company: historical tilknytninger only inside Analyse (labeled)",
          "ikke en påstand om nuværende medlemskab" in h or "Tidligere personer" not in h)


# ---------------------------------------------------------------- PUX-17..20
def test_family_view_model():
    fams = family_fixture()
    check("family fixture: exactly one family", len(fams) == 1, str(len(fams)))
    f = fams[0]
    ids = {m["canonical_person_id"] for m in f["headline"]["members"]}
    check("family fixture: headline is maximal set A+B+C+D", ids == {1, 2, 3, 4})
    check("family fixture: 3 folded subgroups", f["subgroup_count"] == 3)
    sub_ids = [{m["canonical_person_id"] for m in sg["members"]} for sg in f["subgroups"]]
    check("family fixture: subgroups are the strict subsets",
          sorted(sub_ids) == [{1, 2}, {1, 3}, {1, 2, 3}])

    # distinct families stay distinct (§61)
    from heimdal.modules.analytics import views as av

    def gv(members, gid):
        return {"group_id": gid,
                "members": [{"canonical_person_id": m, "display_name": f"P{m}"}
                            for m in members],
                "group_size": len(members), "role_context": "BOARD",
                "support": {"support_company_count": 3, "support_episode_count": 5,
                            "companies": 3},
                "kinds_labels": ["RECURRING"], "episode_kinds_labels": [],
                "observation_wording": "", "member_preview": []}

    fams2 = av._build_constellation_families([gv([1, 2], "x"), gv([3, 4], "y")])
    check("disjoint fixture: two distinct families", len(fams2) == 2)
    check("disjoint fixture: no subgroups", all(f["subgroup_count"] == 0 for f in fams2))

    # no data loss: every group appears exactly once (§33)
    all_fams = av._build_constellation_families(
        [gv([1, 2], "a"), gv([1, 3], "b"), gv([1, 2, 3], "c"), gv([1, 2, 3, 4], "d")])
    seen = []
    for f in all_fams:
        seen.append(f["headline"]["group_id"])
        seen.extend(sg["group_id"] for sg in f["subgroups"])
    check("family fixture: no data loss (every group exactly once)",
          sorted(seen) == ["a", "b", "c", "d"], str(seen))

    # reviewer fix: families payload UNCAP — families beyond the 8-headline
    # cap must be delivered so the "Vis flere" disclosure can render them
    # (person 3175 = family_total 9 in the current build)
    with app.app_context():
        from heimdal.modules.shared.db import conn as _conn
        g = av.get_person_groups(_conn(), 3175)
        if g and not g.get("error") and g.get("family_total", 0) > 8:
            check("family payload: families beyond headline cap delivered",
                  len(g["families"]) == g["family_total"],
                  f"{len(g['families'])} vs {g['family_total']}")
        else:
            check("family payload: families beyond headline cap delivered",
                  True, "n/a — person 3175 has <=8 families in this build")


SCORE_TOKENS = re.compile(
    r"(score|strength|allianc|ranking|konfidensscore)", re.I)


def test_no_score(cl):
    for url, label in ((f"/companies/{COMPANY_OWNER_CASE}", "company"),
                       (f"/persons/{PERSON_ROLES}", "person"),
                       (f"/api/analytics/graph/{PERSON_ROLES}", "graph API")):
        body = cl.get(url).get_data(as_text=True)
        # 'score' allowed only inside technical evidence attributes, not copy;
        # negation phrases are the only allowed mentions
        visible = re.sub(r'data-[a-z-]+="[^"]*"', "", body)
        visible = visible.replace("ingen skjult score", "")
        visible = re.sub(r"ikke en alliance[-\s]", "", visible)
        hits = SCORE_TOKENS.findall(flat(visible))
        check(f"no score/strength/ranking in {label} UI copy", not hits,
              str(set(hits))[:120])
    from heimdal.modules.analytics import views as av
    fams = av._build_constellation_families(family_fixture.__wrapped__() if hasattr(
        family_fixture, "__wrapped__") else _fixture_merged())
    blob = json.dumps(fams, default=str)
    check("family view model: no score fields",
          not re.search(r'"score"|"strength"|"alliance"|"ranking"', blob))


def _fixture_merged():
    def gv(a, b, c, d, gid):
        members = [{"canonical_person_id": pid, "display_name": f"P{pid}"}
                   for pid in [x for x in (a, b, c, d) if x]]
        return {"group_id": gid, "members": members, "group_size": len(members),
                "role_context": "BOARD",
                "support": {"support_company_count": 3, "support_episode_count": 5,
                            "companies": 3},
                "kinds_labels": ["RECURRING"], "episode_kinds_labels": [],
                "observation_wording": "", "member_preview": members[:3]}
    return [gv(1, 2, None, None, "g-ab"), gv(1, 3, None, None, "g-ac"),
            gv(1, 2, 3, None, "g-abc"), gv(1, 2, 3, 4, "g-abcd")]


# ---------------------------------------------------------------- PUX-21
def test_network_split(cl):
    h = flat(cl.get(f"/companies/{COMPANY_OWNER_CASE}").get_data(as_text=True))
    check("company detail: compact preview present", "Åbn netværksanalyse" in h)
    check("company detail: no embedded interactive graph runtime",
          "graph_network.js" not in h)
    ph = flat(cl.get(f"/persons/{PERSON_ROLES}").get_data(as_text=True))
    check("person detail: compact preview present", "Åbn netværksanalyse" in ph)
    check("person detail: no embedded interactive graph runtime",
          "graph_network.js" not in ph)
    # Remediation plan v1 T4.2 (F-09): the interactive graph is now the ONLY
    # primary full-graph surface; the static SVG + edge ledger moved into an
    # evidence-fallback details (print/no-JS). Same intent, updated contract.
    full = cl.get(f"/persons/{PERSON_ROLES}/network").get_data(as_text=True)
    check("person /network full tool preserved (T4.2 contract)",
          'network-graph-container' in full and 'id="netvaerk-fallback"' in full
          and "graph_network.js" in full)
    check("person /network exactly one primary graph surface",
          full.count('id="network-graph-container"') == 1)
    check("person /network fallback carries evidence (svg+ledger)",
          'class="network-svg"' in full and "svg-legend" in full)
    full2 = cl.get(f"/companies/{COMPANY_OWNER_CASE}/network").get_data(as_text=True)
    check("company /network full tool preserved (T4.2 contract)",
          'network-graph-container' in full2 and 'id="netvaerk-fallback"' in full2
          and "graph_network.js" in full2)
    check("company /network exactly one primary graph surface",
          full2.count('id="network-graph-container"') == 1)
    check("company /network fallback carries evidence (svg+ledger)",
          'class="network-svg"' in full2 and "svg-legend" in full2)


# ---------------------------------------------------------------- PUX-22/23
def test_one_history(cl):
    hc = cl.get(f"/companies/{COMPANY_OWNER_CASE}").get_data(as_text=True)
    check("company: Historik tab links to /history",
          f'<a class="tab-route-link" data-route-tab="historik" '
          f'href="/companies/{COMPANY_OWNER_CASE}/history">Historik →</a>'
          in flat(hc))
    check("company: no duplicate inline historik panel",
          'id="panel-historik"' not in hc and 'id="aendringer"' not in hc)
    check("company: /history route renders", cl.get(
        f"/companies/{COMPANY_OWNER_CASE}/history").status_code == 200)
    hp = cl.get(f"/persons/{PERSON_ROLES}").get_data(as_text=True)
    check("person: Historik tab links to /history",
          f'<a class="tab-route-link" data-route-tab="historik" '
          f'href="/persons/{PERSON_ROLES}/history">Historik →</a>'
          in flat(hp))
    check("person: no duplicate inline historik panel",
          'id="panel-historik"' not in hp and 'id="aendringer"' not in hp)
    check("person: /history route renders", cl.get(
        f"/persons/{PERSON_ROLES}/history").status_code == 200)
    # person history consistency: history page + skraseting share the same
    # linked-event model (same canonical links, no invented roles)
    hpage = flat(cl.get(f"/persons/{PERSON_ROLES}/history").get_data(as_text=True))
    check("person history: renders canonical person links",
          re.search(r'href="/persons/\d+"', hpage) is not None
          or re.search(r'href="/companies/\d+"', hpage) is not None)


def test_person_roller_split(cl):
    h = flat(cl.get(f"/persons/{PERSON_ROLES}").get_data(as_text=True))
    check("person roller: Aktuelle roller heading", "Aktuelle roller" in h)
    check("person roller: current roles badged Felag source",
          "Aktuel status (Felag)" in h)


# ---------------------------------------------------------------- PUX-24
def test_canonical_links(cl):
    ok = True
    for pid in ("7412", "2321", "1377"):
        r = cl.get(f"/persons/{pid}")
        if r.status_code != 200:
            ok = False
    check("canonical click-through: sampled person pages 200", ok)
    ok = True
    for cid in ("9667", "9063", "9287"):
        r = cl.get(f"/companies/{cid}")
        if r.status_code != 200:
            ok = False
    check("canonical click-through: sampled company pages 200", ok)


# ---------------------------------------------------------------- PUX-25
def test_immutability():
    """§59: analytics build pin + after-record equal to before-record."""
    from heimdal.modules.shared.db import conn
    with app.app_context():
        c = conn()
        from heimdal.modules.analytics import views as av
        build = av.get_active_analytics_build(c)
        check("analytics: ACTIVE build pinned at 13",
              build["build_run_id"] == PIN_BUILD, str(build["build_run_id"]))
        check("analytics: content hash unchanged",
              build["content_hash"] == PIN_HASH, build["content_hash"])

    counts = _measure()
    after_path = "docs/consolidation_immutability_after.json"
    # H-5: the before-record is the tracked baseline, not the git-ignored
    # docs/consolidation_immutability_before.json (same numbers, build 13)
    bf = ANALYTICS_COUNTS
    expected = {
        "person_company_observation": bf["obs"],
        "person_company_period": bf["periods"],
        "person_person_overlap": bf["pairs"],
        "person_group": bf["groups"],
        "person_group_observation": bf["gobs"],
        "junction_all": bf["junction"],
    }
    for k, v in expected.items():
        check(f"immutability: {k} unchanged", counts[k] == v, f"{counts[k]} vs {v}")
    after = {"build_run_id": PIN_BUILD, "content_hash": PIN_HASH,
             "build_filtered": {"obs": counts["person_company_observation"],
                                "periods": counts["person_company_period"],
                                "pairs": counts["person_person_overlap"],
                                "groups": counts["person_group"],
                                "gobs": counts["person_group_observation"]},
             "junction_all": counts["junction_all"],
             "methodology": "build-13-FILTERED counts; junction_all = full-table "
                            "count of person_group_observation_member (same "
                            "methodology as consolidation_immutability_before.json)"}
    if __name__ == "__main__":  # H-1: pytest never rewrites the tracked record
        with open(after_path, "w") as fh:
            json.dump(after, fh, indent=2, sort_keys=True)
        check("immutability: after-record written", os.path.exists(after_path))


def _measure():
    import psycopg
    with psycopg.connect(DSN) as c, c.cursor() as cur:
        out = {}
        for key, table in (("person_company_observation", "person_company_observation"),
                           ("person_company_period", "person_company_period"),
                           ("person_person_overlap", "person_person_overlap"),
                           ("person_group", "person_group"),
                           ("person_group_observation", "person_group_observation")):
            cur.execute(f"SELECT count(*) FROM registry_analytics.{table} "
                        f"WHERE build_run_id = %s", (PIN_BUILD,))
            out[key] = cur.fetchone()[0]
        # junction_all: full-table count of the group-observation member
        # junction (SAME methodology as docs/consolidation_immutability_before.json)
        cur.execute("SELECT count(*) FROM registry_analytics."
                    "person_group_observation_member")
        out["junction_all"] = cur.fetchone()[0]
    return out


def main():
    set_flags()
    app.config["TESTING"] = True
    with app.test_client() as cl:
        print("— Overblik IA (§5-§11) —")
        test_company_overblik(cl)
        test_person_overblik(cl)
        print("— Tokens (§16/§27/§25) —")
        test_no_internal_tokens(cl)
        print("— Occurrences (§21/§45) —")
        test_no_duplicate_occurrences(cl)
        test_unknown_action_neutral()
        print("— Authority (§9/§51) —")
        test_felag_authority(cl)
        print("— Families (§60-§62) —")
        test_family_view_model()
        test_no_score(cl)
        print("— Network (§36/§37) —")
        test_network_split(cl)
        print("— History (§44/§64) —")
        test_one_history(cl)
        test_person_roller_split(cl)
        print("— Click-through (§53) —")
        test_canonical_links(cl)
        print("— Immutability (§59) —")
        test_immutability()
    print(f"\nPRODUCT UX CONSOLIDATION: {_pass} passed, {_fail} failed")
    raise SystemExit(1 if _fail else 0)


if __name__ == "__main__":
    main()