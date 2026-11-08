from heimdal.pdfhist.db import qa_dsn
# test_remediation_phase3.py
"""Fase 3-detektorer (remediation plan v1, T3.1–T3.4).

Kører via:  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python test_remediation_phase3.py
(main()-drevet suite, samme konvention som de øvrige remediation-suites).

Dækker:
  T3.1  Bindende produktvokabular (roller/tokens) — ingen rå Faroese/engelske
        tokens på produktflader; ukendte roller → neutral fallback; ULTIMATE
        PARENT → neutral gloss (G4 ÅBEN — ingen gate-beslutning).
  T3.2  Source-fact grading — tekniske facts (raw type-token, beholdnings-
        status-token, IDs) i SourceDetails-disclosure; primære flader viser
        menneskelige facts. 'Vis rå kildetekst' → 'Vis kildedetaljer'.
  T3.3  Lean ReportChrome på alle tre dossiers i bindende rækkefølge:
        type → identitet → timestamp → disclaimer → summary → eksport →
        teknisk appendiks. Ingen juridisk genre-wording (G1–G3 ÅBNE).
  T3.4  eigarayvirlit-forklaringslinje: snapshot (kilde) adskilt fra
        beregning (Heimdal); træet følger kun ejerandele.
"""
import os
import re
import sys

os.environ.setdefault("HEIMDAL_READ_DB", qa_dsn())

from heimdal.app import app  # noqa: E402

# T3.1-detektoren måler KUN produktfladen: source/evidence-flader (Vis kilde,
# advanced-only, source-details, m.raw) må gerne bære kildens originale term.
_EVIDENCE_PATTERNS = [
    re.compile(r'<details class="source-details">.*?</details>', re.S),
    re.compile(r'<span class="raw advanced-only">.*?</q>\s*</span>', re.S),
    re.compile(r'<span class="text-xs advanced-only"[^>]*>.*?</span>', re.S),
]


def strip_evidence(html):
    for pat in _EVIDENCE_PATTERNS:
        html = pat.sub("", html)
    return html

REF_COMPANY = 9667      # Betri — FETCHED_OK
REF_PERSON_NEVER = 7412  # person — NEVER_FETCHED
REF_PERSON_ROLES = 1     # person — 4× Stjóri
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
    check(f"HTTP 200 {path}", r.status_code == 200,
          f"status={r.status_code}")
    return r.data.decode("utf-8")


# --- T3.1 -------------------------------------------------------------------
def test_t3_1_role_labels():
    from heimdal.modules.shared.labels import (
        product_role_label, product_token_label, TECHNICAL_TOKEN_LABELS,
    )
    # bindende rolle-mapping
    check("T3.1 Nevnd→Bestyrelse", product_role_label("Nevnd") == "Bestyrelse")
    check("T3.1 Stjórn→Ledelse", product_role_label("Stjórn") == "Ledelse")
    check("T3.1 Stjóri→Direktør", product_role_label("Stjóri") == "Direktør")
    check("T3.1 Forstjóri→Direktør", product_role_label("Forstjóri") == "Direktør")
    check("T3.1 Veruligur Eigari→Reel ejer",
          product_role_label("Veruligur Eigari") == "Reel ejer")
    check("T3.1 Nevndarlimur→Bestyrelsesmedlem",
          product_role_label("Nevndarlimur") == "Bestyrelsesmedlem")
    # ukendte roller → neutral fallback, ingen opdigtede oversættelser
    for unknown in ("Daglig leiðsla", "Filialleiðari", "Varalimur",
                    "Ábyrgdarhavandi", "Atkvøðurættur", None, "HeltUkendt"):
        check(f"T3.1 neutral fallback {unknown!r}",
              product_role_label(unknown) == "Registreret rolle")
    # tekniske tokens (binding)
    check("T3.1 FETCHED→Hentet fra kilde",
          product_token_label("FETCHED") == "Hentet fra kilde")
    check("T3.1 DIRECT OWNERSHIP",
          product_token_label("DIRECT OWNERSHIP") == "Direkte ejerskab")
    check("T3.1 OTHER SOURCE RELATION",
          product_token_label("OTHER SOURCE RELATION") == "Anden kilderelation")
    check("T3.1 REGISTERED CONTROL ACTOR",
          product_token_label("REGISTERED CONTROL ACTOR") == "Registreret kontrolaktør")
    # ULTIMATE PARENT: neutral gloss + kildeterm bevaret (G4 ÅBEN — koden
    # afgør ikke gate-udfaldet)
    check("T3.1 ULTIMATE PARENT gloss (G4 neutral)",
          product_token_label("ULTIMATE PARENT")
          == "Øverste moderselskab (ULTIMATE PARENT)")

    # renderet: ingen rå Faroese rolle-tokens på personens PRODUKT-flade
    # (source/evidence-flader er taget ud af målingen)
    b = strip_evidence(get(f"/persons/{REF_PERSON_ROLES}"))
    check("T3.1 person/1 viser Direktør", "Direktør" in b)
    for raw_tok in ("Nevndar", "Stjóri", "Stjórn", "Forstjóri", "Veruligur"):
        check(f"T3.1 person/1 ingen rå token {raw_tok!r} på produktflade",
              raw_tok not in b)
    # groups-flade: neutral gloss + kildeterm bevaret i evidence
    g = get(f"/groups/{GROUP_ID}")
    check("T3.1 group ULTIMATE PARENT gloss",
          "Øverste moderselskab (ULTIMATE PARENT)" in g
          or "ØVERSTE MODERSELSKAB (ULTIMATE PARENT)" in g)
    # kildetermen forbliver synlig (evidence bevarer kildens term)
    check("T3.1 group kildeterm bevaret", "ULTIMATE PARENT" in g)


# --- T3.2 -------------------------------------------------------------------
def test_t3_2_source_fact_grading():
    # navne-kontrakt på avanceret-toggle
    for path in (f"/persons/{REF_PERSON_NEVER}", f"/companies/{REF_COMPANY}"):
        b = get(path)
        check(f"T3.2 {path} 'Vis kildedetaljer'", "Vis kildedetaljer" in b)
        check(f"T3.2 {path} ingen 'Vis rå kildetekst'", "Vis rå kildetekst" not in b)

    # listing: beholdningsstatus i dansk, rå token væk fra produktfladen
    b = get("/companies?q=Betri")
    check("T3.2 listing dansk status", "Hentet fra kilde" in b)
    check("T3.2 listing ingen rå FETCHED-badge", ">FETCHED<" not in b)

    # dossier: primær enhedstype er menneskelig; rå token i disclosure
    b = get(f"/companies/{REF_COMPANY}/dossier")
    check("T3.2 dossier 'Enhedstype' primær", "Enhedstype" in b)
    check("T3.2 dossier raw token kun i disclosure",
          "Formbetegnelse (kilden)" in b and "Tekniske kildedetaljer" in b)
    idx_summ = b.find("<summary>Tekniske kildedetaljer</summary>")
    idx_stat = b.find("Enhedstype")
    idx_raw = b.find("Formbetegnelse (kilden)")
    check("T3.2 disclosure ligger efter stats (primær først)",
          0 < idx_stat < idx_raw and idx_raw > idx_summ > 0)

    # kyc-dossier: samme grading + ingen opfundet 'Aktiv'-status
    b = get(f"/companies/{REF_COMPANY}/kyc")
    check("T3.2 kyc 'Status i beholdning'", "Status i beholdning" in b)
    check("T3.2 kyc ingen falsk 'Aktiv'", "'Aktiv'" not in b and ">Aktiv<" not in b)
    check("T3.2 kyc raw token i disclosure", "Formbetegnelse (kilden)" in b)


# --- T3.3 -------------------------------------------------------------------
def test_t3_3_report_chrome():
    for path, type_label in (
        (f"/companies/{REF_COMPANY}/dossier", "HEIMDAL DUE DILIGENCE DOSSIER"),
        (f"/persons/{REF_PERSON_NEVER}/dossier", "HEIMDAL DUE DILIGENCE DOSSIER"),
        (f"/companies/{REF_COMPANY}/kyc", None),
    ):
        b = get(path)
        idx_disc = b.find("report-disclaimer")
        idx_summ = b.find("Primær fund")
        idx_act = b.find("Udskriv")
        idx_app = b.find("teknisk appendiks")
        check(f"T3.3 {path} disclaimer til stede", idx_disc != -1)
        check(f"T3.3 {path} summary til stede", idx_summ != -1)
        check(f"T3.3 {path} eksport-action til stede", idx_act != -1)
        check(f"T3.3 {path} teknisk appendiks til stede", idx_app != -1)
        check(f"T3.3 {path} rækkefølge type→identitet→timestamp→disclaimer→"
              "summary→eksport→appendiks",
              -1 not in (idx_disc, idx_summ, idx_act, idx_app)
              and idx_disc < idx_summ < idx_act < idx_app,
              f"{idx_disc},{idx_summ},{idx_act},{idx_app}")
        if type_label:
            check(f"T3.3 {path} rapporttype-chip", type_label in b)
        # ingen SHA i primær chrome (kun i appendiks)
        sha_first = b.find("SHA-256")
        check(f"T3.3 {path} SHA kun i appendiks (efter summary)",
              sha_first == -1 or sha_first > idx_summ)

    # ingen juridisk genre-wording på KYC (G1 ÅBEN — koden afgør det ikke)
    b = get(f"/companies/{REF_COMPANY}/kyc")
    for claim in ("Hvidvaskloven", "gyldigt som intern", "Lovpligtigt"):
        check(f"T3.3 kyc ingen juridisk genre-claim: {claim!r}", claim not in b)

    # dansk pluralis — ingen "1 selskaber"/"1 poster"/"1 personer"
    b2 = get(f"/persons/{REF_PERSON_NEVER}/dossier")
    for bad in ("1 selskaber", "1 poster", "1 personer"):
        check(f"T3.3 pluralis: '{bad}' findes ikke", bad not in b2)
    from heimdal.app import app as _a  # noqa: F811
    tpl = _a.jinja_env.get_template("macros.html")
    out = tpl.module.pluralize(1, "selskab", "selskaber")
    check("T3.3 pluralize(1)", out == "1 selskab", out)
    out = tpl.module.pluralize(3, "selskab", "selskaber")
    check("T3.3 pluralize(3)", out == "3 selskaber", out)


# --- T3.4 -------------------------------------------------------------------
def test_t3_4_eigarayvirlit_explainer():
    b = get(f"/companies/{REF_COMPANY}/eigarayvirlit")
    check("T3.4 kun ejerandele-linje bevaret",
          "kun gennem ejerandele" in b)
    check("T3.4 snapshot↔beregning adskilt",
          "Skráseting Føroya" in b and "beregnet af Heimdal" in b)
    check("T3.4 kildegrundlag peges på Kildeinformation",
          "Kildeinformation" in b)


def main():
    tests = [test_t3_1_role_labels, test_t3_2_source_fact_grading,
             test_t3_3_report_chrome, test_t3_4_eigarayvirlit_explainer]
    for t in tests:
        try:
            t()
        except Exception as e:  # render-fejl er en fejl, ikke et pass
            failures.append((t.__name__, f"EXCEPTION {e!r}"))
    print(f"\n=== Fase 3-detektorer: {len(passed)} passed / "
          f"{len(failures)} failed ===")
    for name, detail in failures:
        print(f"  FAIL: {name} — {detail}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()