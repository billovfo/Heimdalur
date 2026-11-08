"""Offline regressions for lane W3b (audit 2026-11): F-2, F-3, C-3.

F-2: person_owners_tree's REAL output (built here from repository rows of the
     real shape, not hand-made template dicts) must give the KYC table and
     ejarayvirlit.csv a person_name, path_type and path_desc.
F-3: a majority-ownership cycle ends the group tree with a §15 cycle marker.
C-3: indirect_block names a chain that runs through a cycle (owner D8).
No DB: the repository functions are monkeypatched.
"""
import csv
import io
from decimal import Decimal

import pytest

from heimdal.app import app
from heimdal.modules.entities import repository as erepo
from heimdal.modules.entities.service import owner_chain_rows, person_owners_tree
from heimdal.modules.groups import repository as grepo
from heimdal.modules.groups.service import corporate_group_flow, corporate_group_tree

# One company (source 701) owned directly by Anna and through Holding by Bjarni.
NODES = {
    501: {"source_entity_id": 501, "canonical_entity_id": 12, "display_name": "Bjarni", "entity_kind": "PERSON"},
    601: {"source_entity_id": 601, "canonical_entity_id": 13, "display_name": "Holding", "entity_kind": "COMPANY"},
    701: {"source_entity_id": 701, "canonical_entity_id": 1, "display_name": "Mál", "entity_kind": "COMPANY"},
}


@pytest.fixture
def tree(monkeypatch):
    monkeypatch.setattr(erepo, "select_owners_of", lambda c, cid: [
        {"owner_id": 11, "owner_name": "Anna", "owner_kind": "PERSON",
         "parsed_percentage": Decimal("30.000")},
        {"owner_id": 13, "owner_name": "Holding", "owner_kind": "COMPANY",
         "parsed_percentage": Decimal("70.000")}])
    monkeypatch.setattr(erepo, "select_indirect_owners_of", lambda c, sid: [
        {"counterparty_source_id": 501, "computed_percentage": Decimal("35.0000"),
         "path_count": 1, "paths_truncated": False,
         "contributing_paths": [{"path": [501, 601, 701], "percentage": 35.0}]}])
    monkeypatch.setattr(erepo, "select_hydrate_paths_nodes",
                        lambda c, ids: {i: NODES[i] for i in ids})
    monkeypatch.setattr(erepo, "select_hydrate_paths_edges", lambda c, ids: {})
    return person_owners_tree(None, 1, 701)


def test_f2_view_model_maps_the_real_tree(tree):
    assert {"display_name", "best_pct", "direct", "paths"} <= set(tree[0])
    assert "person_name" not in tree[0]          # the shape F-2 tripped over
    rows = {r["person_name"]: r for r in owner_chain_rows(tree)}
    assert set(rows) == {"Anna", "Bjarni"}
    assert rows["Anna"]["path_type"] == "Direkte ejer"
    assert rows["Anna"]["path_desc"] == "Direkte ejer"
    assert rows["Bjarni"]["path_type"] == "Via ejerkæde"
    assert rows["Bjarni"]["path_desc"] == "Bjarni → Holding → Mál (35,00 %)"


def test_f2_kyc_table_shows_names(tree):
    with app.test_request_context():
        html = app.jinja_env.get_template("dossier_kyc.html").render(
            entity={"canonical_entity_id": 1, "display_name": "Mál", "entity_kind": "COMPANY",
                    "registration_number": "1", "raw_type_token": "T",
                    "inventory_status": "ACTIVE", "source_entity_id": 701},
            provenance={"last_fetched_at": None, "snapshot_sha256": None,
                        "last_http_status": None, "last_error_category": None},
            fetch_state="FETCHED_OK", generated_at="01-01-2026 00:00", control=None,
            roles={}, group_info=None, tree=owner_chain_rows(tree),
            structure_status={"has_observations": False, "observation_count": 0,
                              "observations": []})
    assert "<td><strong>Anna</strong></td>" in html
    assert "<td><strong>Bjarni</strong></td>" in html
    assert "Bjarni → Holding → Mál (35,00 %)" in html
    assert "<td><strong></strong></td>" not in html


def test_f2_ejarayvirlit_csv_has_names(tree, monkeypatch):
    from heimdal.modules.export import routes
    monkeypatch.setattr(routes, "conn", lambda: None)
    monkeypatch.setattr(routes, "entity", lambda c, cid: {"source_entity_id": 701})
    app.config["TESTING"] = True
    res = app.test_client().get("/api/export/company/1/ejarayvirlit.csv")
    rows = list(csv.DictReader(io.StringIO(res.get_data(as_text=True))))
    assert [r["person_name"] for r in rows] == ["Bjarni", "Anna"]
    assert all(r["path_type"] and r["path_desc"] for r in rows)


def _groups(monkeypatch, edges):
    """edges: {(owner, owned): pct}; names are str(id)."""
    def rows(pid, lo, hi):
        return [{"owned_id": w, "owned_name": str(w), "owned_kind": "COMPANY",
                 "parsed_percentage": Decimal(p)}
                for (o, w), p in sorted(edges.items()) if o == pid and lo < p <= hi]
    monkeypatch.setattr(grepo, "select_subsidiaries", lambda c, pid: rows(pid, 50, 100))
    monkeypatch.setattr(grepo, "select_associated", lambda c, pid: rows(pid, 19.99, 50))
    import heimdal.modules.groups.service as gs
    monkeypatch.setattr(gs, "entity", lambda c, i: {
        "canonical_entity_id": i, "display_name": str(i), "entity_kind": "COMPANY"})


def test_f3_majority_cycle_ends_with_a_cycle_marker(monkeypatch):
    # 4524 <-> 566 both 100 % (the clone's 2-cycle), plus 566 -> 9 at 60 %
    # and an associated back-edge 9 -> 4524 at 30 %.
    _groups(monkeypatch, {(4524, 566): 100, (566, 4524): 100, (566, 9): 60, (9, 4524): 30})
    tree = corporate_group_tree(None, 4524)
    (sub,) = tree["tree"]
    assert (sub["entity"]["owned_id"], sub["cycle"], sub["cash_flow_pct"]) == (566, False, 100.0)
    back = {k["entity"]["owned_id"]: k for k in sub["children"]}
    assert back[4524]["cycle"] and back[4524]["cash_flow_pct"] is None
    assert back[4524]["children"] == [] and back[4524]["control_pct"] == 100.0
    (assoc,) = back[9]["children"]
    assert assoc["type"] == "ASSOCIATED" and assoc["cycle"] and assoc["cash_flow_pct"] is None
    flow = corporate_group_flow(None, 4524)
    assert sum(link["cycle"] for link in flow["links"]) == 2
    with app.test_request_context():
        html = app.jinja_env.get_template("group_detail.html").render(group=tree)
    assert "krydsejerskabscyklus" in html
    assert "En procent hen over en cyklus ville ikke svare til noget, kilden har registreret." in html


def test_c3_indirect_block_names_withheld_chains():
    row = {"counterparty": NODES[501], "computed_percentage": 10.0, "path_count": 1,
           "paths_truncated": False, "paths": [], "withheld_cycles": [4]}
    withheld = [{"canonical_entity_id": 13, "display_name": "Holding",
                 "entity_kind": "COMPANY", "cycle_group_ids": [1, 3]}]
    with app.test_request_context():
        m = app.jinja_env.get_template("macros.html").make_module()
        html = str(m.indirect_block([row], "INCOMING", False, withheld))
        only = str(m.indirect_block([], "OUTGOING", True, withheld))
    assert "Desuden går en ejersti gennem ejerskabscyklus 4 — dens andel tilbageholdes" in html
    assert "Ejerstien går gennem ejerskabscyklus 1, 3 — procent tilbageholdes." in html
    assert "Holding" in only and "Ingen indirekte andele" not in only
