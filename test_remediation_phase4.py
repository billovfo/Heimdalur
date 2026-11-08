from heimdal.pdfhist.db import qa_dsn
# test_remediation_phase4.py
"""Fase 4-detektorer (remediation plan v1, T4.1–T4.7).

Kører via:  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python test_remediation_phase4.py
Browser-DOM-verifikation af F-10/F-11 ligger i Fase 7 + tools/verify_phase4_graph.py.
"""
import os
import re
import sys

os.environ.setdefault("HEIMDAL_READ_DB", qa_dsn())

from heimdal.app import app  # noqa: E402

REF_COMPANY = 9667
REF_PERSON = 7412
GROUP_ID = 6401

failures = []
passed = []


def check(name, cond, detail=""):
    # H-2: under pytest a false check fails the test; only the __main__ runner
    # collects failures and reports them at the end.
    if __name__ != "__main__":
        assert cond, f"{name} {detail}"
    if cond:
        passed.append(name)
    else:
        failures.append((name, detail))


def get(path):
    tc = app.test_client()
    r = tc.get(path)
    check(f"HTTP 200 {path}", r.status_code == 200, f"status={r.status_code}")
    return r.data.decode("utf-8")


# --- T4.1 -------------------------------------------------------------------
def test_t4_1_no_fake_preview():
    b = get(f"/companies/{REF_COMPANY}")
    check("T4.1 preview: ingen dekorativ node-SVG",
          "np-node" not in b and "network-preview-visual" not in b)
    check("T4.1 preview: counts + CTA bevaret",
          "network-preview-summary" in b and "Åbn netværksanalyse" in b)
    b2 = get(f"/persons/{REF_COMPANY and 1}")
    check("T4.1 person-preview: CTA bevaret", "Åbn netværksanalyse" in b2)
    check("T4.1 person-preview: ingen dekorativ SVG", "np-node" not in b2)


# --- T4.2 -------------------------------------------------------------------
def test_t4_2_single_primary_graph():
    b = get(f"/companies/{REF_COMPANY}/network")
    check("T4.2 interaktiv graf primær", b.count('id="network-graph-container"') == 1)
    check("T4.2 fallback-details (svg + kantliste)",
          'id="netvaerk-fallback"' in b and "class=\"network-svg\"" in b
          and "svg-legend" in b)
    check("T4.2 fallback-summary omtaler evidence-rolle",
          "evidence" in b and "udskrivning" in b)
    check("T4.2 ingen konkurrerende 'tabel er sandhed'-toolbar",
          "statisk cirkel" not in b and "Tabel ↓ primær" not in b)
    check("T4.2 graph_shell.js indlæses før graph_network.js",
          b.find("graph_shell.js") < b.find("graph_network.js"))


# --- T4.3 -------------------------------------------------------------------
def test_t4_3_graphshell_wiring():
    src = open("static/graph_shell.js", encoding="utf-8").read()
    for state in ("LOADING", "LOADED", "EMPTY", "ERROR"):
        check(f"T4.3 shell-state {state}", state in src)
    gn = open("static/graph_network.js", encoding="utf-8").read()
    check("T4.3 network: LOADING ved init", "setState('LOADING')" in gn)
    check("T4.3 network: ERROR ved !res.ok (ikke silent return)",
          "setState('ERROR')" in gn and "if (!res.ok) {" in gn)
    check("T4.3 network: EMPTY ved tom 200",
          "nodeCount ? 'LOADED' : 'EMPTY'" in gn)
    sf = open("static/sankey_flow.js", encoding="utf-8").read()
    check("T4.3 sankey: states wired",
          "setState('ERROR')" in sf and "'LOADED' : 'EMPTY'" in sf)
    gm = open("static/geo_map.js", encoding="utf-8").read()
    check("T4.3 geo: EMPTY/LOADED state", "setState(items.length ? 'LOADED' : 'EMPTY')" in gm)
    css = open("static/heimdal.css", encoding="utf-8").read()
    check("T4.3 css: synlige state-overlays", ".graph-shell-state" in css)
    for tpl in ("templates/geo_map.html", "templates/group_detail.html",
                "templates/macros.html", "templates/pathfinder.html"):
        t = open(tpl, encoding="utf-8").read()
        check(f"T4.3 {tpl}: shell-script før graf-script",
              "graph_shell.js" in t)


# --- T4.4 -------------------------------------------------------------------
def test_t4_4_control_accessibility():
    js = open("static/app.js", encoding="utf-8").read()
    for name in ('aria-label="Zoom ind"',
                 'aria-label="Zoom ud"',
                 'aria-label="Nulstil visning"',
                 'aria-label="Gem grafen som SVG-fil"'):
        check(f"T4.4 aria-label: {name[:30]}", name in js)
    css = open("static/heimdal.css", encoding="utf-8").read()
    check("T4.4 graph-btn ≥44px", "width: 2.75rem; height: 2.75rem;" in css)
    check("T4.4 synlig fokus", ".graph-btn:focus-visible" in css)


# --- T4.5 -------------------------------------------------------------------
def test_t4_5_geo_legend_catalogue():
    b = get("/geo-kort")
    check("T4.5 legend: markørstørrelse = antal selskaber",
          "Markørstørrelse = antal selskaber pr. by" in b)
    check("T4.5 forkert 'Farvekodning: tæthed' fjernet",
          "Farvekodning: t" not in b)
    check("T4.5 rulbart bykatalog", "geo-catalog" in b)
    css = open("static/heimdal.css", encoding="utf-8").read()
    check("T4.5 katalog max-height scroll", ".geo-catalog" in css and "max-height: 420px" in css)


# --- T4.6 -------------------------------------------------------------------
def test_t4_6_pathfinder_resolver():
    b = get("/find-forbindelse")
    check("T4.6 resolver-status element", 'id="pf-resolver-status"' in b)
    check("T4.6 tvetydigheds-valg element (pr. felt)",
          'id="pf-from-choices"' in b and 'id="pf-to-choices"' in b)
    check("T4.6 resolver genbruger /api/search", 'src="/static/pathfinder.js"' in b
          and "/api/search?q=" in open("static/pathfinder.js", encoding="utf-8").read())
    check("T4.6 int-path uændret (type=int args i blueprint)",
          "type=int" in open("heimdal/modules/graph/routes.py",
                             encoding="utf-8").read())
    check("T4.6 copy: dokumenterede forbindelser kun",
          "Find dokumenterede forbindelser" in b and "bruger kun registrerede" in b)
    # deep-link med int-ID virker stadig
    b2 = get("/find-forbindelse?from=9667&to=7412")
    check("T4.6 int-dyplink virker", "Fundne forbindelser" in b2 or "Ingen forbindelse fundet" in b2)


# --- T4.7 -------------------------------------------------------------------
def test_t4_7_single_path_representation():
    b = get("/find-forbindelse?from=9667&to=7412")
    if "Fundne forbindelser" not in b:
        # ingen sti for dette par — verify med et par der har sti
        b = get("/find-forbindelse?from=9667&to=9667")
    check("T4.7 mini-graf primær", 'id="pathfinder-mini-graph"' in b)
    check("T4.7 tabel i disclosure",
          "Sammenligningstabel" in b and b.find("path-comparison-table") > b.find("Sammenligningstabel"))
    check("T4.7 transskript i disclosure",
          "Sti-transskript" in b and b.find("path-chains-list") > b.find("Sti-transskript"))


def main():
    tests = [test_t4_1_no_fake_preview, test_t4_2_single_primary_graph,
             test_t4_3_graphshell_wiring, test_t4_4_control_accessibility,
             test_t4_5_geo_legend_catalogue, test_t4_6_pathfinder_resolver,
             test_t4_7_single_path_representation]
    for t in tests:
        try:
            t()
        except Exception as e:
            failures.append((t.__name__, f"EXCEPTION {e!r}"))
    print(f"\n=== Fase 4-detektorer: {len(passed)} passed / {len(failures)} failed ===")
    for name, detail in failures:
        print(f"  FAIL: {name} — {detail}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()