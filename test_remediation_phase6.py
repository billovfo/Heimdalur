from heimdal.pdfhist.db import qa_dsn
# test_remediation_phase6.py
"""Fase 6-detektorer (remediation plan v1): T6.1 skjult Time Machine væk,
T6.2 print-regler, T6.3 komponentgrammatik, T6.4 procent/pluralis-format.

Kører:  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 HEIMDAL_READ_DB=... python test_remediation_phase6.py
"""
import os
import re
import sys

os.environ.setdefault("HEIMDAL_READ_DB", qa_dsn())

from heimdal.app import app  # noqa: E402

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


# --- T6.1 -------------------------------------------------------------------
def test_t6_1_no_hidden_time_machine():
    gn = open("static/graph_network.js", encoding="utf-8").read()
    check("T6.1 initTimeMachine fjernet", "initTimeMachine" not in gn)
    check("T6.1 seekTime/updateTimeLabel fjernet",
          "seekTime" not in gn and "updateTimeLabel" not in gn)
    check("T6.1 ingen historical-timeline fetch ved load",
          "historical-timeline" not in gn)
    mc = open("templates/macros.html", encoding="utf-8").read()
    check("T6.1 skjult tidsrejse-blok fjernet",
          "time-machine-slider" not in mc and "time-machine-controls" not in mc)
    b = get("/companies/9667/network")
    check("T6.1 ingen tidsrejse-markup i render", "time-machine" not in b)


# --- T6.2 -------------------------------------------------------------------
def test_t6_2_print_rules():
    js = open("static/app.js", encoding="utf-8").read()
    check("T6.2 beforeprint åbner disclosures",
          "beforeprint" in js and "details:not([open])" in js)
    check("T6.2 afterprint genopretter", "afterprint" in js)
    css = open("static/heimdal.css", encoding="utf-8").read()
    m = re.search(r"@media print \{[\s\S]*", css)
    block = m.group(0)
    check("T6.2 print skjuler kontroller",
          ".graph-control-group" in block and ".graph-btn" in block  # .graph-toolbar matched nothing (G-08)
          and ".tablist" in block)
    check("T6.2 print sidebrud-hygiejne",
          "break-inside: avoid" in block and "thead { display: table-header-group; }" in block)
    check("T6.2 print skjuler summary (indhold foldes ud)",
          "details > summary { display: none !important; }" in block)


# --- T6.3 -------------------------------------------------------------------
def test_t6_3_grammar_documented():
    css = open("static/heimdal.css", encoding="utf-8").read()
    check("T6.3 komponentgrammatik dokumenteret",
          "KOMPONENTGRAMMATIK" in css)
    # spot-check: touched surfaces deler mønstre (ingen one-off)
    for tpl, probe in [
        ("templates/company_history.html", "badge-source"),
        ("templates/person_history.html", "badge-source"),
        ("templates/changelog.html", "badge badge-sm"),
        ("templates/groups.html", "entity-link entity-company"),
        ("templates/network.html", "source-details"),
        ("static/pathfinder.js", "btn btn-outline"),  # the pathfinder script left the template (G-05)
    ]:
        src = open(tpl, encoding="utf-8").read()
        check(f"T6.3 {tpl}: fælles mønster ({probe})", probe in src)
    # ingen dekorative tilføjelser i fase 6 (STOP-gate): diff-surface check —
    # grafområdet har kun GraphShell + kontroller, ingen nye dekorationer
    b = get("/companies/9667/network")
    check("T6.3 ingen nye dekorative elementer i graf",
          "np-node" not in b and "deco-circle" not in b)


# --- T6.4 -------------------------------------------------------------------
def test_t6_4_percent_and_plural():
    from heimdal.modules.shared.templates import pct
    check("T6.4 pct: fast to-decimaler", pct(50) == "50,00"
          and pct(33.3333) == "33,33")
    # ingen engelsk punktum-format tilbage i skabeloner
    for tpl in ("templates/compliance.html", "templates/dossier_company.html",
                "templates/dossier_person.html", "templates/group_detail.html"):
        src = open(tpl, encoding="utf-8").read()
        check(f"T6.4 {tpl}: ingen %.2f-format", '%.2f' not in src)
    gd = get("/groups/6401")
    check("T6.4 group_detail: dansk procentformat",
          re.search(r"Kontrol: \d+,\d\d % · Effektiv kapitalandel: \d+,\d\d %", gd)
          is not None, gd[:0])
    # render-sweep: ingen "1 <flertal>"-klassens fejl på touched surfaces
    bad_re = re.compile(
        r"\b1 (selskaber|poster|personer|hændelser|registreringer|moderselskaber|"
        r"datterselskaber|forbindelser|resultater|koncerner)\b")
    pages = ["/", "/companies/9667", "/persons/7412", "/companies/9667/network",
             "/companies/9667/history", "/persons/1/history", "/aendringer",
             "/groups", "/companies/9667/dossier", "/persons/1/dossier",
             "/companies/9667/kyc", "/companies/9667/eigarayvirlit", "/geo-kort"]
    bad = []
    for p in pages:
        tc = app.test_client()
        r = tc.get(p)
        if r.status_code != 200:
            continue
        m = bad_re.search(r.data.decode("utf-8"))
        if m:
            bad.append((p, m.group(0)))
    check("T6.4 ingen '1 <flertal>'-render", not bad, str(bad))
    # procentformat: komma overalt på touched surfaces (ingen "N.NN %")
    dot_re = re.compile(r"\d+\.\d\d\s?%")
    bad2 = []
    for p in ["/groups/6401", "/companies/9667/dossier", "/persons/1/dossier",
              "/companies/9667/kyc"]:
        tc = app.test_client()
        r = tc.get(p)
        if r.status_code != 200:
            continue
        m = dot_re.search(r.data.decode("utf-8"))
        if m:
            bad2.append((p, m.group(0)))
    check("T6.4 procent: dansk komma-konsekvent", not bad2, str(bad2))


def main():
    tests = [test_t6_1_no_hidden_time_machine, test_t6_2_print_rules,
             test_t6_3_grammar_documented, test_t6_4_percent_and_plural]
    for t in tests:
        try:
            t()
        except Exception as e:
            failures.append((t.__name__, f"EXCEPTION {e!r}"))
    print(f"\n=== Fase 6-detektorer: {len(passed)} passed / {len(failures)} failed ===")
    for name, detail in failures:
        print(f"  FAIL: {name} — {detail}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()