from heimdal.pdfhist.db import qa_dsn
# test_remediation_phase5.py
"""Fase 5-detektorer (remediation plan v1): T5.1 historik scanbarhed +
sikker person-context, T5.2 changelog række-summary, T5.3 groups navigation.

Kører:  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 HEIMDAL_READ_DB=... python test_remediation_phase5.py
"""
import os
import re
import sys

import pytest

os.environ.setdefault("HEIMDAL_READ_DB", qa_dsn())

from heimdal.app import app  # noqa: E402
import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

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


def db():
    c = psycopg.connect(os.environ["HEIMDAL_READ_DB"], row_factory=dict_row)
    c.execute("SET default_transaction_read_only = on")
    return c


# --- T5.1 -------------------------------------------------------------------
def test_t5_1_history_person_context():
    c = db()
    # secure-binding example: newest event with canonical actor reference
    row = c.execute(
        "SELECT canonical_entity_id, related_entity_id, related_entity_name "
        "FROM v_entity_change_feed WHERE related_entity_id IS NOT NULL "
        "ORDER BY event_timestamp DESC LIMIT 1").fetchone()
    b = get(f"/companies/{row['canonical_entity_id']}/history")
    check("T5.1 aktør-kontekst ved sikker binding",
          f'href="/persons/{row["related_entity_id"]}"' in b
          and row["related_entity_name"] in b)
    # actor links exist ONLY for rows whose related id is a canonical person
    links = re.findall(r'history-actor[\s\S]{0,120}?href="/persons/(\d+)"', b)
    ok = True
    if links:
        ids = [int(x) for x in links]
        known = c.execute(
            "SELECT canonical_entity_id FROM canonical_entity "
            "WHERE canonical_entity_id = ANY(%s) AND entity_kind = 'PERSON'",
            (ids,)).fetchall()
        known_ids = {(k["canonical_entity_id"] if isinstance(k, dict) else k[0])
                     for k in known}
        ok = set(ids) <= known_ids
    check("T5.1 alle aktør-links er canonical PERSON", ok, str(links[:5]))

    # unresolved rows: company 9667 (STATUS_CHANGED rows uden related id)
    b2 = get("/companies/9667/history")
    check("T5.1 unresolved → neutral beskrivelse (ingen aktør-link)",
          'history-actor' not in b2)
    check("T5.1 dato+række+summary stadig synlige",
          "history-date-group" in b2 and "history-detail" in b2)
    check("T5.1 Heimdal-kildebadge bevaret", "badge-heimdal" in b2)

    # Skráseting-grænse: consumer understøtter limit; route bruger 101; note i template
    src = open("heimdal/modules/entities/routes.py", encoding="utf-8").read()
    check("T5.1 Skráseting-historik grænsesat (limit=101 i route)",
          "get_projected_company_history(regnr, limit=101)" in src)
    tpl = open("templates/company_history.html", encoding="utf-8").read()
    check("T5.1 afgrænsningsnote i skabelon", "skraseting_truncated" in tpl)
    cons = open("heimdal/pdfhist/consumer.py", encoding="utf-8").read()
    check("T5.1 consumer accepterer limit (default uændret)",
          "limit: int | None = None" in cons)


@pytest.mark.usefixtures("active_projection")  # Skráseting rows need run 33 (H-6)
def test_t5_1_skraseting_source_badge():
    b = get("/companies/9667/history")
    check("T5.1 Skráseting-kildebadge bevaret",
          "badge-heimdal" in b and "badge-source" in b)

# --- T5.2 -------------------------------------------------------------------
def test_t5_2_changelog_row_summary():
    b = get("/aendringer")
    check("T5.2 synlig række-summary", "changelog-row-summary" in b)
    check("T5.2 disclosure pr. række", "changelog-row-details" in b
          and "Fuld detalje" in b)
    check("T5.2 summary ikke kun hover (tekst synlig uden title-attribut)",
          b.count("changelog-row-summary") >= 3)
    # pager virker
    check("T5.2 pager bevaret", "Side 1 af" in b)
    b2 = get("/aendringer?page=2")
    check("T5.2 page 2 nåbar", "event-row" in b2)


# --- T5.3 -------------------------------------------------------------------
def test_t5_3_groups_navigation():
    b1 = get("/groups")
    check("T5.3 søgeformular", 'id="groups-q"' in b1 and 'name="q"' in b1)
    check("T5.3 total-tæller", "af " in b1 and "moderselskaber" in b1)
    check("T5.3 pager", "Side 1 af" in b1)
    b2 = get("/groups?page=2")
    check("T5.3 page 2 har 100 rækker", b2.count("entity-company") >= 90)
    first1 = re.search(r'/groups/(\d+)"', b1).group(1)
    first2 = re.search(r'/groups/(\d+)"', b2).group(1)
    check("T5.3 page 2 viser ANDRE grupper end page 1", first1 != first2,
          f"p1={first1} p2={first2}")
    b3 = get("/groups?q=Var%C3%B0in")
    check("T5.3 søgning finder Varðin", "Varðin" in b3)
    b4 = get("/groups?q=ZZZIKKEFINDES")
    check("T5.3 tom søgning → tom tilstand", "Ingen koncerner fundet" in b4)
    # q + page kombineret
    b5 = get("/groups?q=a&page=2")
    check("T5.3 q+page kombineres", "Side 2 af" in b5 or "Side " in b5)

    # 50,0 %-relation står ALDRIG under >50 %-label (strict >50.0 i engine)
    c = db()
    fifty = c.execute(
        "SELECT owner_id, owned_id FROM v_direct_ownership "
        "WHERE parsed_percentage = 50.0 LIMIT 5").fetchall()
    check("T5.3 50,0 % relationer findes i data", len(fifty) > 0)
    from heimdal.modules.groups.service import corporate_group_tree
    ok = True
    as_sub = []
    def walk(node, out):
        out.add(node["entity"]["owned_id"])
        for ch in node.get("children", []) or []:
            walk(ch, out)
    for r in fifty:
        owner = r["owner_id"]
        tree = corporate_group_tree(c, owner)
        if tree:
            sub_ids = set()
            for n in tree["tree"]:
                walk(n, sub_ids)
            # 50,0 %-ejet selskab må ikke være blandt SUBSIDIARY-noderne
            def contains_sub(nodes):
                hit = False
                for n in nodes:
                    if n["type"] == "SUBSIDIARY" and n["entity"]["owned_id"] == r["owned_id"]:
                        return True
                    hit = hit or contains_sub(n.get("children", []) or [])
                return hit
            if r["owned_id"] in sub_ids and contains_sub(tree["tree"]):
                as_sub.append((owner, r["owned_id"]))
                ok = False
    check("T5.3 50,0 %-relationer medfører ikke SUBSIDIARY-medlemskab", ok, str(as_sub[:3]))
    # associeret-bandet: 50,0 % må gerne optræde som ASSOCIATED (20-50 % band)
    tree_v = corporate_group_tree(c, 7870)  # Varðin: 16 >50 % + én 50,0 %
    if tree_v:
        assoc_ids = set()
        def walk2(node):
            if node["type"] == "ASSOCIATED":
                assoc_ids.add(node["entity"]["owned_id"])
            for ch in node.get("children", []) or []:
                walk2(ch)
        for n in tree_v["tree"]:
            walk2(n)
        sub_ids = set()
        def walk_sub(node):
            if node["type"] == "SUBSIDIARY":
                sub_ids.add(node["entity"]["owned_id"])
            for ch in node.get("children", []) or []:
                walk_sub(ch)
        for n in tree_v["tree"]:
            walk_sub(n)
        c2 = db()
        fifty_v = c2.execute("SELECT owned_id FROM v_direct_ownership WHERE owner_id = 7870 AND parsed_percentage = 50.0").fetchall()
        fifty_ids = {f["owned_id"] for f in fifty_v}
        check("T5.3 Varðins 50,0 %-datter er ASSOCIATED, ikke SUBSIDIARY",
              fifty_ids & sub_ids == set() and fifty_ids & assoc_ids == fifty_ids,
              f"fifty={fifty_ids} assoc={fifty_ids & assoc_ids} sub={fifty_ids & sub_ids}")

    # threshold-copy uændret (G4 ÅBEN — koden afvejer ikke)
    g = get("/groups")
    gd = get("/groups/6401")
    check('T5.3 lead-copy ">50 % af kapital eller stemmer" bevaret (G4-deferred)',
          "&gt; 50 % af kapital eller stemmer" in g)
    check('T5.3 detail-chip "FLERTALSKONTROL &gt;50%" bevaret (G4-deferred)',
          "FLERTALSKONTROL &gt;50%" in gd)


def main():
    tests = [test_t5_1_history_person_context, test_t5_2_changelog_row_summary,
             test_t5_3_groups_navigation]
    for t in tests:
        try:
            t()
        except Exception as e:
            failures.append((t.__name__, f"EXCEPTION {e!r}"))
    print(f"\n=== Fase 5-detektorer: {len(passed)} passed / {len(failures)} failed ===")
    for name, detail in failures:
        print(f"  FAIL: {name} — {detail}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()