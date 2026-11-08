"""Lane W3b (audit 2026-11) on the heimdal test clone (HEIMDAL_READ_DB, real
2026-09-04 data, read-only): F-2, F-3, C-3, C-8 through the real code paths.

F-2: KYC, dossier and ejarayvirlit.csv show the names person_owners_tree finds.
F-3: the 8 majority-cycle entities answer /groups and /api/groups/flow with 200
     and a bounded query count (was ~1,950 queries, then RecursionError).
C-3: every owner–company chain through a cycle edge gets the D8 marker, checked
     against an independent recompute; 219 pairs have nothing else.
C-8: the 70 self-edges never make a company its own subsidiary or parent.
"""
import csv
import io

import psycopg
import pytest
from markupsafe import escape
from psycopg.rows import dict_row

from heimdal.app import app
from heimdal.config import DB_URI, INCOMING, OUTGOING
from heimdal.modules.entities import repository as erepo
from heimdal.modules.entities.service import indirect_view, person_owners_tree
from heimdal.modules.groups.service import (
    all_corporate_groups, corporate_group_tree, ultimate_parent_of)


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DB_URI, row_factory=dict_row,
                         options="-c default_transaction_read_only=on") as c:
        yield c


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def _sid(db, cid):
    return db.execute("SELECT source_entity_id FROM canonical_entity"
                      " WHERE canonical_entity_id = %s", (cid,)).fetchone()["source_entity_id"]


def test_f2_routes_show_the_names_person_owners_tree_finds(db, client):
    cid = db.execute(  # the company check_f uses: a person owns >= 25 % directly
        "SELECT owned_id FROM v_direct_ownership WHERE owner_kind = 'PERSON'"
        " AND owned_kind = 'COMPANY' AND parsed_percentage >= 25 ORDER BY owned_id LIMIT 1"
    ).fetchone()["owned_id"]
    for company in (cid, 9467):  # 9467: 26 persons, most through chains
        names = [p["display_name"] for p in person_owners_tree(db, company, _sid(db, company))]
        assert names
        res = client.get(f"/api/export/company/{company}/ejarayvirlit.csv")
        rows = list(csv.DictReader(io.StringIO(res.get_data(as_text=True))))
        assert [r["person_name"] for r in rows] == names
        assert all(r["path_type"] and r["path_desc"] for r in rows)
        for url in (f"/companies/{company}/dossier", f"/companies/{company}/kyc"):
            html = client.get(url).get_data(as_text=True)
            assert "<td><strong></strong></td>" not in html, url
        assert f"<td><strong>{escape(names[0])}</strong></td>" in client.get(
            f"/companies/{company}/dossier").get_data(as_text=True)
    kyc = client.get(f"/companies/{cid}/kyc").get_data(as_text=True)
    assert "Direkte ejer" in kyc[kyc.find("Beregnet Ejerandel"):]


def test_f3_majority_cycles_answer_with_bounded_queries(db, client, monkeypatch):
    ids = [r["owner_id"] for r in db.execute(
        "SELECT DISTINCT d.owner_id FROM v_direct_ownership d"
        " JOIN canonical_entity a ON a.canonical_entity_id = d.owner_id"
        " JOIN canonical_entity b ON b.canonical_entity_id = d.owned_id"
        " JOIN registry_ownership.ownership_cycle_member ma ON ma.entity_id = a.source_entity_id"
        " JOIN registry_ownership.ownership_cycle_member mb ON mb.entity_id = b.source_entity_id"
        "  AND mb.cycle_group_id = ma.cycle_group_id"
        " WHERE d.parsed_percentage > 50 ORDER BY 1")]
    # The audit's 8 endless trees (6 self-majority + the 4524 <-> 566 two-cycle)
    # plus 8129, whose 100 % holding 9110 owns 25 % of it back.
    assert len(ids) == 9, ids
    count = [0]
    real = psycopg.Cursor.execute

    def counting(self, *a, **k):
        count[0] += 1
        return real(self, *a, **k)

    monkeypatch.setattr(psycopg.Cursor, "execute", counting)
    for i in ids:
        for url in (f"/groups/{i}", f"/api/groups/{i}/flow"):
            count[0] = 0
            res = client.get(url)
            assert res.status_code == 200, url
            assert count[0] <= 40, (url, count[0])
    flow = client.get("/api/groups/4524/flow").get_json()
    assert [(l["source"], l["target"]) for l in flow["links"] if l["cycle"]] == [(566, 4524)]
    for i in (566, 8129):
        assert "krydsejerskabscyklus" in client.get(f"/groups/{i}").get_data(as_text=True), i


def _chains_through_cycles(db):
    """Independent recompute: {(owner, owned): cycle groups} over simple paths
    of >= 2 hops that use an edge inside one cycle group (source entity ids)."""
    out = {}
    for r in db.execute(
            "SELECT canonical_subject_entity_id AS o, canonical_object_entity_id AS w"
            " FROM registry_ownership.relation WHERE canonical_relation_type = 'DIRECT_OWNERSHIP'"
            " AND canonical_subject_entity_id <> canonical_object_entity_id"):
        out.setdefault(r["o"], set()).add(r["w"])
    cyc = {r["entity_id"]: r["cycle_group_id"] for r in db.execute(
        "SELECT entity_id, cycle_group_id FROM registry_ownership.ownership_cycle_member")}
    same = lambda a, b: a in cyc and cyc.get(a) == cyc.get(b)  # noqa: E731
    want = {}
    for a in out:
        stack = [(a, (a,), frozenset())]
        while stack:
            n, path, groups = stack.pop()
            for m in out.get(n, ()):
                if m in path:
                    continue
                g = groups | ({cyc[n]} if same(n, m) else set())
                if g and len(path) >= 2 and not same(a, m):
                    want.setdefault((a, m), set()).update(g)
                stack.append((m, path + (m,), g))
    return want


def test_c3_every_chain_through_a_cycle_is_marked(db, client):
    want = _chains_through_cycles(db)
    io_pairs = {(r["owner_entity_id"], r["owned_entity_id"]) for r in db.execute(
        "SELECT owner_entity_id, owned_entity_id FROM registry_ownership.indirect_ownership")}
    direct = {(r["o"], r["w"]) for r in db.execute(
        "SELECT canonical_subject_entity_id AS o, canonical_object_entity_id AS w"
        " FROM registry_ownership.relation WHERE canonical_relation_type = 'DIRECT_OWNERSHIP'")}
    assert len({k for k in want if k not in io_pairs and k not in direct}) == 219  # the audit's C-3 figure

    ids = {n for k in direct for n in k}
    for incoming in (True, False):
        have = {}
        for x in ids:
            for m in erepo.select_cycle_passing(db, x, incoming):
                key = (m["counterparty_source_id"], x) if incoming else (x, m["counterparty_source_id"])
                have[key] = set(m["cycle_group_ids"])
        assert have == want, incoming

    for owned in sorted({w for _, w in want})[:15]:
        v = indirect_view(db, owned, INCOMING)
        assert {m["counterparty_source_id"] for m in v["withheld"]} == {
            a for a, w in want if w == owned and (a, w) not in io_pairs}
        assert {r["counterparty_source_id"] for r in v["indirect"] if r["withheld_cycles"]} == {
            a for a, w in want if w == owned and (a, w) in io_pairs}
    owner = next(a for a, _ in sorted(want))
    assert indirect_view(db, owner, OUTGOING)["withheld"] or any(
        r["withheld_cycles"] for r in indirect_view(db, owner, OUTGOING)["indirect"])

    company = db.execute(  # an affected company that is not itself a cycle member
        "SELECT canonical_entity_id FROM canonical_entity WHERE entity_kind = 'COMPANY'"
        " AND source_entity_id = ANY(%s) AND source_entity_id NOT IN"
        " (SELECT entity_id FROM registry_ownership.ownership_cycle_member)"
        " ORDER BY canonical_entity_id LIMIT 1",
        ([w for a, w in want if (a, w) not in io_pairs],)).fetchone()["canonical_entity_id"]
    html = client.get(f"/companies/{company}").get_data(as_text=True)
    assert "Ejerstien går gennem ejerskabscyklus" in html
    assert "— procent tilbageholdes." in html


def test_c8_self_edges_never_parent_or_subsidiary(db):
    selfs = [r["owner_id"] for r in db.execute(
        "SELECT owner_id FROM v_direct_ownership WHERE owner_id = owned_id ORDER BY 1")]
    assert len(selfs) == 70  # not the 5 the older docs name (C-8)
    for e in selfs:
        assert all(n["entity"]["owned_id"] != e for n in corporate_group_tree(db, e)["tree"]), e
    others = {r["owner_id"]: r for r in db.execute(
        "SELECT s.owner_id,"
        " EXISTS (SELECT 1 FROM v_direct_ownership d WHERE d.owner_id = s.owner_id"
        "   AND d.owned_id <> s.owner_id AND d.parsed_percentage > 50) AS holds,"
        " EXISTS (SELECT 1 FROM v_direct_ownership d WHERE d.owned_id = s.owner_id"
        "   AND d.owner_id <> s.owner_id AND d.parsed_percentage > 50) AS held"
        " FROM v_direct_ownership s WHERE s.owner_id = s.owned_id AND s.parsed_percentage > 50")}
    assert len(others) == 6
    listed = {g["parent_id"] for g in all_corporate_groups(db, limit=100000)}
    for e, r in others.items():
        if not r["holds"] and not r["held"]:
            assert not ultimate_parent_of(db, e)["is_in_group"], e  # owns only itself
        if r["holds"] and not r["held"]:
            assert e in listed, e  # e.g. VM Holding: owns itself and 2 subsidiaries
