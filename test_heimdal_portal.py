#!/usr/bin/env python3
"""Checks for the virksomhedsportal's decision logic (SoT §16.1/§16.1a/§16.2).

Pure logic is tested with plain dicts. The direction guarantee and the cycle
rule are tested against the live database, because the thing worth proving
there is that the SQL itself cannot return the wrong side — a mock would only
prove the mock. If the database is unreachable those checks state so and fail,
rather than passing quietly.

Run: python3 test_heimdal_portal.py
"""
from decimal import Decimal

from heimdal.app import app
from heimdal.config import DB_URI, INCOMING, OUTGOING
from heimdal.modules.dashboard.service import dashboard_charts, top_lists
from heimdal.modules.entities.service import (
    ALL_TERMINAL, COMPANY_KINDS, CONTROL_NONE_REGISTERED, CONTROL_NO_DATA, CONTROL_REGISTERED,
    PARTIAL_POPULATION, PERSON_KINDS, RELATION_BASIS_LABELS, ROLE_GROUPS, SUM_EXACT, SUM_OVER,
    SUM_UNDER, _DIRECTIONAL_BASES, _person_relation_basis, company_relations_of,
    control_registration, control_registrations_of_actor, control_state, cycle_info, ego_graph,
    ego_layout, entity_list, holdings_of, hydrate_paths, indirect_owners_of, overview,
    owners_of, ownership_sum, ownership_tree_layout, person_list, person_owners_tree,
    person_relations_of, population_state, roles_at_company, roles_held_by_person, top_persons,
    voting_rights_at_company, voting_rights_held_by_person,
)
from heimdal.modules.shared.entity import entity, entity_url, provenance
from heimdal.modules.shared.templates import heatband

# --- fixtures ----------------------------------------------------------------
# Live canonical IDs, verified 2026-08-02 against the heimdal database.
ORDINARY = 8220        # Jókin — 10 contributing paths, deep chains
OVER_100 = 3531        # Tórðargøta — direct owners sum to 200 %
CYCLE_MEMBER = 7870    # Varðin — cycle group 1, with Másasteinur
NO_ACTOR_STATUS = 4310  # Tjaldur — has a NO_REGISTERED_CONTROL_ACTOR row
HAS_ACTOR = 7          # BS Tøkni — has a registered control actor
NO_CONTROL_DATA = 10   # Malltek — neither state
NEVER_FETCHED = 49     # B. J. Holding A/S — no v_provenance row
# Company 818 has role relations too (verified 2026-08-03), but NAX below was
# the fixture already checked against the live DB for this test.
TEST_COMPANY_WITH_ROLES = 5708  # NAX — has a Stjóri (STJORN) row, verified live
# Bána has a rare row shape: the same person carries both a STJORN role and an
# EIGARAR-tagged row (Atkvøðurættur / voting rights, cbTypeId 101 — not
# ownership, not a Nevnd/Stjórn/Annað role) for this company, verified live.
TEST_COMPANY_WITH_VOTING_RIGHTS = 2800  # Bána
# Company 5493 — 80 distinct company-relation peers, 183 context rows
# spanning DIRECT_OWNERSHIP/INDIRECT_OWNERSHIP (directional) plus
# CROSS_ROLE_PERSON/SHARED_BOARD_PERSON/SHARED_MANAGEMENT_PERSON
# (non-directional), verified live 2026-08-03.
TEST_COMPANY_WITH_RELATIONS = 5493
# Gallarí Á Stongunum — zero rows in registry_ownership.company_relation_context, verified live.
TEST_COMPANY_NO_RELATIONS_SOURCE_ID = 21135

# Person 5761 — direct ownership (outgoing, 2 rows), indirect ownership
# (1 row), registered as control actor for 2 companies, and holds NEVND +
# STJORN roles. Satisfies the full intersection (control-actor ∩ direct-
# ownership ∩ role), verified live 2026-08-03.
TEST_PERSON_FULL = 5761
TEST_PERSON_WITH_CONTROL_REG = 5761  # same person — registered as actor for 2 companies
TEST_PERSON_WITH_ROLE = 5761         # same person — NEVND + STJORN rows
# Person 6387 — the person-side twin of TEST_COMPANY_WITH_VOTING_RIGHTS
# (company 2800/Bána): holds both a STJORN role and an EIGARAR-tagged
# (Atkvøðurættur / voting rights) row, verified live 2026-08-03.
TEST_PERSON_WITH_VOTING_RIGHTS = 6387
# Person 3 — never appears as actor_id in v_control_registration,
# i.e. CONTROL_NO_DATA on the actor side, verified live 2026-08-03.
TEST_PERSON_NO_CONTROL_DATA = 3
# Person 6 — zero rows in registry_ownership.person_relation_context (neither side),
# verified live 2026-08-03.
TEST_PERSON_NO_RELATIONS = 6
# Person 1303 (source_entity_id 9277) — 8 person-relation
# peers; the pair with person 6744 (source_entity_id 13568) alone has 51
# context rows spanning every relation_basis: DIRECT_OWNERSHIP/DIRECT_OWNERSHIP
# (SHARED_OWNER), REGISTERED_CONTROL_ACTOR/REGISTERED_CONTROL_ACTOR
# (SHARED_BENEFICIAL_OWNER), NEVND/NEVND (SHARED_BOARD_PERSON), STJORN/STJORN
# (SHARED_MANAGEMENT_PERSON), and — the regression case this task exists for —
# DIRECT_OWNERSHIP paired with REGISTERED_CONTROL_ACTOR, both tagged
# person_a_group=person_b_group='EIGARAR', which must fall through to
# CROSS_ROLE_PERSON rather than being merged with either SHARED_OWNER or
# SHARED_BENEFICIAL_OWNER. Verified live 2026-08-03.
TEST_PERSON_WITH_PEER_RELATIONS = 1303
TEST_PEER_OF_PEER_RELATIONS = 6744  # its peer; names are read from the DB (I-5)
# canonical_entity_id 9667 has registration_number S3690, verified live 2026-08-03.
TEST_REGNR_BARE = "3690"
TEST_REGNR_S = "S3690"
TEST_REGNR_HASH = "#3690"

# --- §21 network fixtures -----------------------------------------------------
# NAX (5708, already TEST_COMPANY_WITH_ROLES) has a rich depth-1 neighborhood:
# two PERSON owners, one COMPANY owner (Wext), a CONTROL edge and a ROLE edge —
# and, verified live 2026-08-03, a genuine OWNERSHIP self-loop row (NAX owns
# 21,25 % of itself in the source). A real, live-reachable edge case for the
# layout math (a zero-length line) and for the "every edge endpoint is a
# listed node" guarantee (the center id itself).
#
# Person 5761 (TEST_PERSON_FULL) is registered as a control actor —
# the branch of v_control_registration where the OTHER side's kind is not
# selected by the view. One of its control-registration neighbors, canonical
# id 1499, is verified live to actually be entity_kind
# COMPANY — but ego_graph must (and does) default it to UNKNOWN_ENTITY per
# the brief's documented, deliberate simplification, not crash or guess.
TEST_NETWORK_UNKNOWN_KIND_NEIGHBOR_ID = 1499
# 8973 (Tryggingarfelagið Lív) is owned by canonical id 296, an ORGANIZATION
# (Ognarfelagið LÍV) — verified live 2026-08-03. Exercises the ORGANIZATION
# glyph/kind path, which TEST_COMPANY_WITH_ROLES's neighborhood does not.
TEST_COMPANY_OWNED_BY_ORGANIZATION = 8973
# 5253 (Fjarhitafelagið) is owned by canonical id 2866, a PUBLIC_AUTHORITY
# (Elfelagið SEV) — verified live 2026-08-03. Exercises the PUBLIC_AUTHORITY
# glyph/kind path.
TEST_COMPANY_OWNED_BY_PUBLIC_AUTHORITY = 5253
# North Atlantic Seafood í konkurs (9802) has zero rows across
# v_direct_ownership/v_control_registration/v_role_relation — a genuine
# zero-edge ego_graph, verified live 2026-08-03. Neither TEST_PERSON_NO_RELATIONS
# nor TEST_COMPANY_NO_RELATIONS_SOURCE_ID work for this: registry_ownership.person_relation_context/
# registry_ownership.company_relation_context (§18/§19, "shared context with a peer") are derived
# tables distinct from ego_graph's three direct sources — both those fixtures
# still have 3 ego_graph edges each.
TEST_COMPANY_NO_NETWORK_EDGES = 9802


def row(pct, status="PARSED", raw=None):
    return {"parsed_percentage": None if pct is None else Decimal(str(pct)),
            "percentage_parse_status": status,
            "raw_percentage_label": raw or (f"Eigari {pct}%" if pct is not None else None)}


# --- sum footer (§16.1) ------------------------------------------------------

def test_sum_under_100_reports_the_gap():
    s = ownership_sum([row(60), row(15.5)])
    assert s["state"] == SUM_UNDER, s
    assert s["total"] == Decimal("75.5")
    assert s["remainder"] == Decimal("24.5")


def test_sum_exactly_100():
    s = ownership_sum([row(50), row("33.333"), row("16.667")])
    assert s["state"] == SUM_EXACT, s
    assert s["total"] == Decimal("100.000")
    assert s["remainder"] == 0


def test_sum_over_100_is_flagged_and_never_normalised():
    s = ownership_sum([row(100), row(100)])
    assert s["state"] == SUM_OVER, s
    assert s["total"] == Decimal("200"), "the source's own total must survive verbatim"
    assert s["excess"] == Decimal("100")


def test_sum_over_100_by_a_rounding_hair_still_counts_as_over():
    """100.01 % is a real shape in the live data — it must not round down to 100."""
    s = ownership_sum([row("50.005"), row("50.005")])
    assert s["state"] == SUM_OVER, s


def test_empty_owner_list_still_yields_a_footer():
    s = ownership_sum([])
    assert s["state"] == SUM_UNDER and s["total"] == 0 and s["remainder"] == 100


def test_failed_parse_is_counted_not_read_as_zero():
    s = ownership_sum([row(60), row(None, "FAILED", raw="Eigari ??%")])
    assert s["total"] == Decimal("60"), "a failed parse must contribute nothing"
    assert s["unreadable"] == 1
    assert s["state"] == SUM_UNDER
    # and it must not silently disappear from the row count either
    assert s["row_count"] == 2


# --- kontrolregistrering (§16.1a) --------------------------------------------

def test_control_state_registered_actors():
    assert control_state([{"actor_id": 1}], False) == CONTROL_REGISTERED


def test_control_state_no_registered_actor_is_a_source_claim():
    assert control_state([], True) == CONTROL_NONE_REGISTERED


def test_control_state_no_data_is_a_different_claim():
    assert control_state([], False) == CONTROL_NO_DATA


def test_control_states_two_and_three_are_never_collapsed():
    assert CONTROL_NONE_REGISTERED != CONTROL_NO_DATA
    assert len({control_state([{"actor_id": 1}], False),
                control_state([], True),
                control_state([], False)}) == 3


def test_actor_rows_win_over_a_stale_absence_status():
    """If the source somehow reports both, the registered actor is the fact."""
    assert control_state([{"actor_id": 1}], True) == CONTROL_REGISTERED


# --- live-database checks ----------------------------------------------------

def conn():
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(DB_URI, row_factory=dict_row)


def test_direction_queries_never_return_the_other_direction():
    """SoT §16.2's documented failure mode, checked against real rows."""
    with conn() as c:
        for eid in (ORDINARY, OVER_100, CYCLE_MEMBER, HAS_ACTOR):
            incoming = owners_of(c, eid)
            outgoing = holdings_of(c, eid)
            for r in incoming:
                assert r["owned_id"] == eid, f"owners_of({eid}) returned an outgoing row"
                assert r["direction"] == INCOMING
            for r in outgoing:
                assert r["owner_id"] == eid, f"holdings_of({eid}) returned an incoming row"
                assert r["direction"] == OUTGOING
            # The two sets are disjoint by relation_id — no row is ever in both.
            assert not ({r["relation_id"] for r in incoming}
                        & {r["relation_id"] for r in outgoing})


def test_direction_queries_cover_every_direct_ownership_row_exactly_once():
    """Not just disjoint — together they must be the whole picture for an entity."""
    with conn() as c:
        got = {r["relation_id"] for r in owners_of(c, ORDINARY)} \
            | {r["relation_id"] for r in holdings_of(c, ORDINARY)}
        expected = {r["relation_id"] for r in c.execute(
            "SELECT relation_id FROM v_direct_ownership"
            " WHERE owner_id = %s OR owned_id = %s", (ORDINARY, ORDINARY)).fetchall()}
        assert got == expected, (got ^ expected)


def test_control_registration_resolves_all_three_states_live():
    with conn() as c:
        cases = {HAS_ACTOR: CONTROL_REGISTERED,
                 NO_ACTOR_STATUS: CONTROL_NONE_REGISTERED,
                 NO_CONTROL_DATA: CONTROL_NO_DATA}
        for eid, expected in cases.items():
            src = entity(c, eid)["source_entity_id"]
            assert control_registration(c, eid, src)["state"] == expected, eid


def test_control_registration_view_has_no_percentage_column():
    """§14a.1 is enforced by the column's absence, not by convention."""
    with conn() as c:
        cols = {r["column_name"] for r in c.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'v_control_registration'").fetchall()}
        assert not [x for x in cols if "percent" in x], cols


def test_cycle_member_gets_an_explainer_naming_members_and_direct_edges():
    """SoT §15: no indirect percentage across the cycle; the direct edges that
    form it are shown and the cycle is explained instead."""
    with conn() as c:
        src = entity(c, CYCLE_MEMBER)["source_entity_id"]
        cyc = cycle_info(c, src)
        assert cyc is not None, "expected a cycle for this fixture"
        assert len(cyc["members"]) >= 1, cyc
        assert cyc["edges"], "the direct edges forming the cycle must be shown"
        member_ids = {m["canonical_entity_id"] for m in cyc["members"]}
        for e in cyc["edges"]:
            assert e["owner_id"] in member_ids and e["owned_id"] in member_ids


def test_non_cycle_member_has_no_cycle_info():
    with conn() as c:
        src = entity(c, ORDINARY)["source_entity_id"]
        assert cycle_info(c, src) is None


def test_no_indirect_percentage_is_ever_computed_across_a_cycle():
    """The real §15 invariant, as the data layer implements it: a pair is
    excluded when BOTH ends sit in the SAME cycle component. A cycle member's
    unrelated, non-cyclic holdings survive on purpose — excluding those
    entirely was a bug fixed in heimdal_collect.py on 2026-08-02, and the
    portal must not reintroduce it by blanking the whole section.
    """
    with conn() as c:
        across = c.execute(
            "SELECT count(*) AS n FROM registry_ownership.indirect_ownership io"
            " JOIN registry_ownership.ownership_cycle_member a ON a.entity_id = io.owner_entity_id"
            " JOIN registry_ownership.ownership_cycle_member b ON b.entity_id = io.owned_entity_id"
            " WHERE a.cycle_group_id = b.cycle_group_id").fetchone()["n"]
        assert across == 0, f"{across} indirect rows cross a cycle"


def test_paths_hydrate_into_named_hops():
    with conn() as c:
        src = entity(c, ORDINARY)["source_entity_id"]
        rows = hydrate_paths(c, indirect_owners_of(c, src))
        assert rows, "expected indirect owners for this fixture"
        r = rows[0]
        assert r["counterparty"]["display_name"]
        for path in r["paths"]:
            assert len(path["nodes"]) >= 2
            assert len(path["hops"]) == len(path["nodes"]) - 1
            assert all(n and n["display_name"] for n in path["nodes"])
            assert all(h is not None for h in path["hops"]), "every hop must resolve to an edge"


def test_never_fetched_entity_has_no_provenance_and_that_is_not_an_error():
    with conn() as c:
        assert entity(c, NEVER_FETCHED) is not None
        assert provenance(c, NEVER_FETCHED) is None


def test_entity_query_does_not_expose_raw_labels():
    """A PERSON's raw label carries a home address — it must not be selectable
    from anything the page renders."""
    with conn() as c:
        ent = entity(c, ORDINARY)
        assert "raw_inventory_label" not in ent
        assert "raw_display_name" not in ent


# --- rendering ---------------------------------------------------------------

def test_pages_render_for_every_fixture():
    app.config["TESTING"] = True
    client = app.test_client()
    for eid in (ORDINARY, OVER_100, CYCLE_MEMBER, NO_ACTOR_STATUS,
                HAS_ACTOR, NO_CONTROL_DATA, NEVER_FETCHED):
        r = client.get(f"/companies/{eid}")
        assert r.status_code == 200, (eid, r.status_code)
        html = r.get_data(as_text=True)
        assert "Hvem ejer virksomheden?" in html
        assert "Kontrolregistrering" in html
        # §16.2 is hidden when the company holds nothing (owner ruling 2026-08-15)
        with conn() as c:
            has_outgoing = c.execute(
                "SELECT 1 FROM v_direct_ownership WHERE owner_id = %s LIMIT 1",
                (eid,)).fetchone() is not None
            has_indirect = c.execute(
                "SELECT 1 FROM registry_ownership.indirect_ownership io JOIN canonical_entity ce"
                " ON ce.source_entity_id = io.owner_entity_id"
                " WHERE ce.canonical_entity_id = %s LIMIT 1", (eid,)).fetchone() is not None
        assert ("Hvad ejer virksomheden?" in html) == (has_outgoing or has_indirect), eid


def test_over_100_page_flags_without_normalising():
    client = app.test_client()
    html = client.get(f"/companies/{OVER_100}").get_data(as_text=True)
    assert "Over 100" in html
    assert "normaliserer aldrig" in html
    assert "200,00&nbsp;%" in html  # T6.4: fixed two decimals


def test_cycle_page_shows_the_explainer_not_a_percentage():
    client = app.test_client()
    html = client.get(f"/companies/{CYCLE_MEMBER}").get_data(as_text=True)
    assert "krydsejerskabscyklus" in html
    assert "Cyklusgruppe" in html
    assert "Másasteinur" in html, "the cycle's other member must be named"
    assert "ingen procent er ført hen over cyklussen" in html


def test_no_control_data_page_does_not_claim_no_registered_actor():
    client = app.test_client()
    html = client.get(f"/companies/{NO_CONTROL_DATA}").get_data(as_text=True)
    assert "Ingen kontrolregistrering i dette snapshot" in html
    assert "Kilden oplyser, at der ikke er registreret" not in html


def test_no_registered_actor_page_makes_the_positive_statement():
    client = app.test_client()
    html = client.get(f"/companies/{NO_ACTOR_STATUS}").get_data(as_text=True)
    assert "Ingen registreret kontrolaktør" in html
    assert "Kilden oplyser, at der ikke er registreret" in html


def test_control_section_renders_no_percentage_at_all():
    """§14a.1 in the markup: the rendered kontrolregistrering section must not
    contain a percent sign, in any state. The view has no percentage column;
    this asserts the page never grows one back through a stray macro reuse."""
    import re
    client = app.test_client()
    for eid in (HAS_ACTOR, NO_ACTOR_STATUS, NO_CONTROL_DATA, OVER_100):
        html = client.get(f"/companies/{eid}").get_data(as_text=True)
        section = re.search(r'<section class="card section control".*?</section>',
                            html, re.S)
        assert section, eid
        body = section.group(0).replace("&nbsp;", " ")
        assert "%" not in body, (eid, [l for l in body.splitlines() if "%" in l])


def test_the_three_control_states_render_three_distinct_markers():
    """The states must stay visually unmistakable from each other, not just
    differ in prose. Each carries its own banner class (own colour, own marker
    shape, own border style) — and no page ever shows two of them."""
    client = app.test_client()
    seen = {}
    for eid, cls in ((HAS_ACTOR, "state-actors"),
                     (NO_ACTOR_STATUS, "state-none"),
                     (NO_CONTROL_DATA, "state-nodata")):
        html = client.get(f"/companies/{eid}").get_data(as_text=True)
        present = [c for c in ("state-actors", "state-none", "state-nodata") if c in html]
        assert present == [cls], (eid, present)
        seen[cls] = eid
    assert len(seen) == 3


def test_person_id_is_not_served_by_the_company_portal():
    client = app.test_client()
    with conn() as c:
        pid = c.execute("SELECT canonical_entity_id FROM canonical_entity"
                        " WHERE entity_kind = 'PERSON' LIMIT 1").fetchone()["canonical_entity_id"]
    assert client.get(f"/companies/{pid}").status_code == 404


def test_only_get_is_served():
    client = app.test_client()
    assert client.post(f"/companies/{ORDINARY}").status_code == 405


# --- oversigt og lister ------------------------------------------------------

def test_population_is_partial_while_anything_is_unresolved():
    """FULL_POPULATION kan aldrig hævdes herfra: porten ser ikke enumeration_key."""
    assert population_state(1) == PARTIAL_POPULATION
    assert population_state(0) == ALL_TERMINAL
    assert PARTIAL_POPULATION != ALL_TERMINAL


def test_person_relation_pairs_page_lists_top_pairs_and_links_from_persons():
    """Owner ruling 2026-08-15: the person-pair list lives at
    /persons/relationer as a subpage of /persons, sorted by shared companies
    descending, paginated — and /persons links to it."""
    client = app.test_client()
    r = client.get("/persons/relationer")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Relationer mellem personer" in html
    assert "Fælles virksomheder" in html
    with conn() as c:
        top = top_lists(c)["relations"][0]
    assert top["a_name"] in html and top["b_name"] in html, "top pair must lead the list"
    persons_html = client.get("/persons").get_data(as_text=True)
    assert 'href="/persons/relationer"' in persons_html, "/persons must link to the pair list"


def test_front_page_is_a_dashboard_not_a_404():
    client = app.test_client()
    r = client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Gå til" in html and 'href="/bestand"' in html


def test_dashboard_shows_top5_lists_with_real_leaders():
    """Owner ruling 2026-08-15: the dashboard shows top-5 lists. The lists
    reuse person_list's tested counts, so the leaders must match the DB."""
    client = app.test_client()
    html = client.get("/").get_data(as_text=True)
    for title in ("Top 5 — direkte ejerskab", "Top 5 — indirekte ejerskab",
                  "flest bestyrelsesposter", "flest ledelsesposter",
                  "Top relationer mellem personer"):
        assert title in html, title
    with conn() as c:
        top_dir = person_list(c, "", 1, sort="-direct", per_page=1)["rows"][0]
        top_ind = person_list(c, "", 1, sort="-indirect", per_page=1)["rows"][0]
        top_pair = top_lists(c)["relations"][0]
    assert top_dir["canonical_entity_id"] == 6006, "the pinned top direct owner (person 6006)"
    assert top_dir["display_name"] in html, "top direct owner must lead the list"
    assert top_ind["display_name"] in html, "top indirect owner must lead the list"
    assert top_pair["a_name"] in html and top_pair["b_name"] in html, \
        "top person pair must appear on the dashboard"


def test_bestand_page_holds_the_old_dashboard_content():
    """Owner ruling 2026-08-15: the old dashboard content moved to /bestand."""
    client = app.test_client()
    html = client.get("/bestand").get_data(as_text=True)
    assert "Bestand" in html and "Indirekte ejerskab" in html
    assert "Seneste kørsler" in html


def test_overview_counts_match_the_tables_they_summarise():
    with conn() as c:
        o = overview(c)
        assert o["inventory"] == c.execute(
            "SELECT count(*) AS n FROM company_inventory").fetchone()["n"]
        assert o["indirect"] == c.execute(
            "SELECT count(*) AS n FROM registry_ownership.indirect_ownership").fetchone()["n"]
        assert sum(k["n"] for k in o["kinds"]) == c.execute(
            "SELECT count(*) AS n FROM canonical_entity").fetchone()["n"]
        assert o["state"] == population_state(o["unresolved"])


def test_the_two_lists_partition_the_entities_and_never_overlap():
    """Én liste for personer, én for alt andet — aldrig den samme række i begge."""
    with conn() as c:
        comp = entity_list(c, COMPANY_KINDS, "", 1)
        pers = entity_list(c, PERSON_KINDS, "", 1)
        total = c.execute("SELECT count(*) AS n FROM canonical_entity").fetchone()["n"]
        assert comp["total"] + pers["total"] == total
        assert all(r["entity_kind"] == "PERSON" for r in pers["rows"])
        assert not any(r["entity_kind"] == "PERSON" for r in comp["rows"])


def test_the_name_filter_actually_filters():
    with conn() as c:
        all_rows = entity_list(c, COMPANY_KINDS, "", 1)
        hit = entity_list(c, COMPANY_KINDS, "varðin", 1)
        assert 0 < hit["total"] < all_rows["total"]
        assert all("varðin" in r["display_name"].lower() for r in hit["rows"])


def test_paging_walks_the_whole_list_without_repeating_a_row():
    with conn() as c:
        a = entity_list(c, PERSON_KINDS, "", 1, per_page=25)
        b = entity_list(c, PERSON_KINDS, "", 2, per_page=25)
        assert len(a["rows"]) == len(b["rows"]) == 25
        assert not ({r["canonical_entity_id"] for r in a["rows"]}
                    & {r["canonical_entity_id"] for r in b["rows"]})
        assert a["pages"] == b["pages"] == -(-a["total"] // 25)


def test_companies_search_matches_all_three_registration_number_forms():
    """§22: bare/S-prefixed/#-prefixed registration numbers must all find the
    same company."""
    with conn() as c:
        bare = entity_list(c, COMPANY_KINDS, TEST_REGNR_BARE, 1)
        s_form = entity_list(c, COMPANY_KINDS, TEST_REGNR_S, 1)
        hash_form = entity_list(c, COMPANY_KINDS, TEST_REGNR_HASH, 1)
    assert bare["total"] >= 1
    assert s_form["total"] >= 1
    assert hash_form["total"] >= 1
    assert {r["canonical_entity_id"] for r in bare["rows"]} == \
           {r["canonical_entity_id"] for r in s_form["rows"]} == \
           {r["canonical_entity_id"] for r in hash_form["rows"]}


def test_lone_s_query_does_not_wildcard_match_every_registration_number():
    """§22 regression: 'S'.lstrip('Ss#') is '' — an empty normalized string
    must never become a '%%' wildcard that matches every non-null
    registration_number. A query of just 'S' should behave exactly like
    name-only matching, not like 'return almost the whole company list'."""
    with conn() as c:
        name_only_total = c.execute(
            "SELECT count(*) AS n FROM canonical_entity"
            " WHERE entity_kind = ANY(%s) AND display_name ILIKE '%%S%%'",
            (list(COMPANY_KINDS),)).fetchone()["n"]
        all_companies_total = c.execute(
            "SELECT count(*) AS n FROM canonical_entity"
            " WHERE entity_kind = ANY(%s)",
            (list(COMPANY_KINDS),)).fetchone()["n"]
        s_only = entity_list(c, COMPANY_KINDS, "S", 1)
        hash_only = entity_list(c, COMPANY_KINDS, "#", 1)
    assert 0 < name_only_total < all_companies_total, \
        "fixture assumption broken: need 'S' to be a partial, non-trivial name match"
    assert s_only["total"] == name_only_total
    assert hash_only["total"] == 0  # no company name contains a literal '#'


def test_list_rows_never_carry_a_raw_label():
    """A PERSON's raw source label holds a home address (§5). The list query must
    not be able to hand one to a template at all."""
    with conn() as c:
        for r in entity_list(c, PERSON_KINDS, "", 1)["rows"]:
            assert "raw_inventory_label" not in r and "raw_display_name" not in r


def test_person_list_page_renders_names_only():
    client = app.test_client()
    html = client.get("/persons").get_data(as_text=True)
    assert html.count("/persons/") > 10
    with conn() as c:
        raw = c.execute(
            "SELECT raw_inventory_label AS l FROM v_entity WHERE entity_kind = 'PERSON'"
            " AND raw_inventory_label IS NOT NULL LIMIT 5").fetchall()
    for r in raw:
        assert r["l"] not in html, "a raw person label reached the page"


def test_person_list_sort_orders_the_whole_database_not_just_the_page():
    """Owner ruling: the name header must sort the entire person table, not
    the 60 rows of the current page. The globally first/last person comes from
    one query over the whole table, in the DB's own collation (H-5: the clone's
    collation decides whether Øyvind or Zydrúnas is last)."""
    client = app.test_client()
    with conn() as c:
        first, last = (c.execute(
            "SELECT display_name FROM canonical_entity WHERE entity_kind = ANY(%s)"
            f" ORDER BY display_name {d}, canonical_entity_id {d} LIMIT 1",
            (list(PERSON_KINDS),)).fetchone()["display_name"] for d in ("ASC", "DESC"))
        rows = entity_list(c, PERSON_KINDS, "", 1, sort="name")["rows"]
        assert rows[0]["display_name"] == first, rows[0]["display_name"]
        rev = entity_list(c, PERSON_KINDS, "", 1, sort="-name")["rows"]
        assert rev[0]["display_name"] == last, rev[0]["display_name"]
    asc = client.get("/persons?sort=name").get_data(as_text=True)
    assert first in asc, "ascending first page must hold the globally first person"
    desc = client.get("/persons?sort=-name").get_data(as_text=True)
    assert last in desc, "descending first page must hold the globally last person"
    assert first not in desc, "the globally first person belongs on the last page, not the first"


def test_person_list_nevnd_stjorn_counts_match_the_role_views():
    """Owner ruling 2026-08-15: Nevnd/Stjórn are count columns like the
    ownership ones — distinct companies where the person holds a role in the
    given inferred group (Nevnd: Nevndarlimur/Nevndarformaður/…, Stjórn:
    Stjóri/Forstjóri/…). Sorting finds who sits in the most boards."""
    with conn() as c:
        rows = person_list(c, "", 1, sort="-nevn", per_page=25)["rows"]
        top = c.execute("SELECT person_id, count(DISTINCT company_id) AS n FROM v_role_relation"
                        " WHERE inferred_group = 'NEVND' GROUP BY person_id"
                        " ORDER BY n DESC, person_id ASC LIMIT 1").fetchone()
        st_rows = person_list(c, "", 1, sort="-stjorn", per_page=25)["rows"]
        top_st = c.execute("SELECT person_id, count(DISTINCT company_id) AS n FROM v_role_relation"
                           " WHERE inferred_group = 'STJORN' GROUP BY person_id"
                           " ORDER BY n DESC, person_id ASC LIMIT 1").fetchone()
    assert rows[0]["nevn_count"] == top["n"]
    assert all(a["nevn_count"] >= b["nevn_count"] for a, b in zip(rows, rows[1:]))
    assert st_rows[0]["stjorn_count"] == top_st["n"]
    assert all(a["stjorn_count"] >= b["stjorn_count"]
               for a, b in zip(st_rows, st_rows[1:]))


def test_person_list_page_exposes_all_sort_headers():
    """The 2026-09 list has four sortable columns: Navn, Direkte, Samlet, Ledelse."""
    client = app.test_client()
    html = client.get("/persons").get_data(as_text=True)
    assert "?sort=-name" in html, "name header must toggle to descending"
    for key, label in (("direct", "Direkte"), ("combined", "Samlet"), ("nevn", "Ledelse")):
        assert f'?sort={key}">{label}' in html, f"missing sort header for {key}"


def test_person_list_pager_offers_first_and_last():
    client = app.test_client()
    html = client.get("/persons").get_data(as_text=True)
    assert "Sidste" in html and "Første" in html, "the pager must offer first/last navigation"
    assert 'href="/persons?page=85"' in html, "Sidste must link to the final page"
    desc = client.get("/persons?sort=-name").get_data(as_text=True)
    assert 'href="/persons?page=85&amp;sort=-name"' in desc, "pager links must preserve the sort"


def test_person_list_ownership_counts_match_the_db():
    """The three new columns are counts of COMPANIES, not shares. Ground truth
    computed here independently of person_list's own SQL."""
    with conn() as c:
        first_name = entity(c, 8032)["display_name"].split()[0]  # no names in git (I-5)
        row = next(r for r in person_list(c, first_name, 1, sort="combined")["rows"]
                   if r["canonical_entity_id"] == 8032)
        direct = c.execute(
            "SELECT count(DISTINCT owned_id) AS n FROM v_direct_ownership"
            " WHERE owner_id = %s AND owner_kind = 'PERSON' AND owned_kind <> 'PERSON'",
            (row["canonical_entity_id"],)).fetchone()["n"]
        indirect = c.execute(
            "SELECT count(DISTINCT io.owned_entity_id) AS n FROM registry_ownership.indirect_ownership io"
            " JOIN canonical_entity ce ON ce.source_entity_id = io.owner_entity_id"
            " WHERE ce.canonical_entity_id = %s AND io.owner_entity_id <> io.owned_entity_id"
            " AND io.owned_entity_id IN (SELECT source_entity_id FROM canonical_entity"
            " WHERE entity_kind <> 'PERSON')",
            (row["canonical_entity_id"],)).fetchone()["n"]
    assert row["direct_count"] == direct
    assert row["indirect_count"] == indirect
    # combined is the union of the two company sets, never a raw sum
    assert row["combined_count"] >= max(direct, indirect)
    assert row["combined_count"] <= direct + indirect


def test_person_list_sorts_by_ownership_counts_across_the_whole_db():
    """sort=combined orders all 5,083 persons by their union company count,
    so page 1 must start with the globally highest count, and page 2 must
    never exceed page 1's last row."""
    with conn() as c:
        first = person_list(c, "", 1, sort="-combined", per_page=25)
        second = person_list(c, "", 2, sort="-combined", per_page=25)
        top = c.execute(
            "SELECT ve.canonical_entity_id, ve.display_name, count(DISTINCT u.cid) AS n"
            " FROM v_entity ve"
            " LEFT JOIN ("
            "  SELECT owner_id AS pid, owned_id AS cid FROM v_direct_ownership"
            "  WHERE owner_kind = 'PERSON' AND owned_kind <> 'PERSON'"
            "  UNION"
            "  SELECT o.canonical_entity_id, d.canonical_entity_id"
            "  FROM registry_ownership.indirect_ownership io"
            "  JOIN canonical_entity o ON o.source_entity_id = io.owner_entity_id AND o.entity_kind = 'PERSON'"
            "  JOIN canonical_entity d ON d.source_entity_id = io.owned_entity_id AND d.entity_kind <> 'PERSON'"
            "  WHERE io.owner_entity_id <> io.owned_entity_id) u ON u.pid = ve.canonical_entity_id"
            " WHERE ve.entity_kind = 'PERSON'"
            " GROUP BY ve.canonical_entity_id, ve.display_name"
            " ORDER BY n DESC, ve.display_name ASC LIMIT 1").fetchone()
    assert first["rows"], "fixture list must not be empty"
    assert first["rows"][0]["combined_count"] == top["n"], top["display_name"]
    assert first["rows"][0]["combined_count"] >= second["rows"][0]["combined_count"]
    assert all(a["combined_count"] >= b["combined_count"]
               for a, b in zip(first["rows"], first["rows"][1:])), "rows must be non-increasing"


def test_person_detail_renders_the_real_portal_not_the_old_placeholder():
    """The person portal (§17.1/§17.1a/§17.2/§17.3) is now built — this
    replaces the earlier placeholder test, which asserted the opposite."""
    client = app.test_client()
    with conn() as c:
        person = c.execute(
            "SELECT canonical_entity_id AS id, display_name AS n FROM canonical_entity"
            " WHERE entity_kind = 'PERSON' ORDER BY canonical_entity_id LIMIT 1").fetchone()
    r = client.get(f"/persons/{person['id']}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert person["n"] in html
    assert "Personportalen er ikke bygget endnu" not in html
    for present in ("Direkte ejerskab", "Indirekte ejerskab", "Kildeinformation",
                    "Roller i virksomheder"):
        assert present in html, present
    # the combined "Hvad ejer personen?" section no longer exists (§17.2 split)
    assert "Hvad ejer personen?" not in html
    # a person cannot be owned — §17.1 must not exist on person pages
    assert "Hvem ejer personen?" not in html


def test_a_company_is_not_served_by_the_person_route():
    client = app.test_client()
    assert client.get(f"/persons/{ORDINARY}").status_code == 404


def test_every_page_offers_the_three_destinations():
    client = app.test_client()
    for path in ("/", "/companies", "/persons", f"/companies/{ORDINARY}"):
        html = client.get(path).get_data(as_text=True)
        for dest in ('href="/"', 'href="/companies"', 'href="/persons"'):
            assert dest in html, (path, dest)


# --- roles (§16.3) ------------------------------------------------------------

def test_roles_at_company_groups_into_three_buckets_only():
    """roles_at_company only ever buckets NEVND/STJORN/ANNAD. v_role_relation
    also carries a fourth inferred_group, EIGARAR (Atkvøðurættur / voting
    rights, cbTypeId 101) — a distinct concept from both ownership and these
    three roles. It is excluded by this query outright and shown separately
    by voting_rights_at_company instead (never duplicated, never dropped)."""
    with conn() as c:
        groups = roles_at_company(c, TEST_COMPANY_WITH_ROLES)
    assert set(groups) == {"NEVND", "STJORN", "ANNAD"}
    for group_name, rows in groups.items():
        for row in rows:
            assert row["inferred_group"] == group_name
            assert row["inferred_group_confidence"] == "INFERRED"


def test_roles_at_company_person_names_are_the_parsed_display_name():
    """person_name must come from canonical_entity's parsed display_name, not
    a raw source label — a raw person label can carry a home address (§5).
    Checked against canonical_entity directly, so this fails if the view (or
    this query) ever started handing back something other than the parsed
    name for the same person_id."""
    with conn() as c:
        groups = roles_at_company(c, TEST_COMPANY_WITH_ROLES)
        all_rows = [r for rows in groups.values() for r in rows]
        assert all_rows, "fixture company must have at least one role row"
        for row in all_rows:
            expected = c.execute(
                "SELECT display_name FROM canonical_entity WHERE canonical_entity_id = %s",
                (row["person_id"],)).fetchone()
            assert row["person_name"] == expected["display_name"]


def test_voting_rights_are_excluded_from_roles_and_never_500():
    """Regression: v_role_relation rows with inferred_group='EIGARAR' (voting
    rights, not ownership) used to crash roles_at_company's grouping with a
    KeyError. They must never land in NEVND/STJORN/ANNAD, and the company page
    must render them in their own §16.3a section instead of 500ing."""
    with conn() as c:
        groups = roles_at_company(c, TEST_COMPANY_WITH_VOTING_RIGHTS)
        voting = voting_rights_at_company(c, TEST_COMPANY_WITH_VOTING_RIGHTS)
    assert voting, "fixture company must have at least one voting-rights row"
    for rows in groups.values():
        for row in rows:
            assert row["inferred_group"] != "EIGARAR"

    client = app.test_client()
    r = client.get(f"/companies/{TEST_COMPANY_WITH_VOTING_RIGHTS}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Stemmerettigheder" in html
    assert "Atkvøðurættur" in html


# --- company relations (§16.4/§19) --------------------------------------------

def test_company_relations_never_shows_a_bare_total():
    """§18.4/§19's core rule: a count is worthless without its breakdown next
    to it. Every peer's `counts` dict must sum to exactly its context list length."""
    with conn() as c:
        ent = entity(c, TEST_COMPANY_WITH_RELATIONS)
        peers = company_relations_of(c, ent["source_entity_id"])
    assert peers, "fixture company must have at least one peer relation"
    for peer in peers:
        assert sum(peer["counts"].values()) == len(peer["contexts"])
        assert peer["counts"], "counts dict must never be empty when contexts exist"


def test_company_relations_directional_bases_state_direction():
    with conn() as c:
        ent = entity(c, TEST_COMPANY_WITH_RELATIONS)
        peers = company_relations_of(c, ent["source_entity_id"])
    directional_bases = {"DIRECT_OWNERSHIP", "INDIRECT_OWNERSHIP", "DIRECT_CONTROL_ACTOR"}
    found_directional = False
    for peer in peers:
        for ctx in peer["contexts"]:
            if ctx["relation_basis"] in directional_bases:
                found_directional = True
                assert ctx["subject_is_this"] in (True, False)
            else:
                assert ctx["subject_is_this"] is None
    assert found_directional, "fixture must include at least one directional relation_basis"


def test_company_relations_resolves_peer_to_canonical_id_never_raw():
    with conn() as c:
        ent = entity(c, TEST_COMPANY_WITH_RELATIONS)
        peers = company_relations_of(c, ent["source_entity_id"])
        assert peers, "fixture company must have at least one peer relation"
        for peer in peers:
            assert isinstance(peer["peer_id"], int)
            # a canonical_entity_id is a small sequential int from canonical_entity,
            # never the raw pkCbi — spot check it actually resolves
            row = c.execute("SELECT 1 FROM canonical_entity WHERE canonical_entity_id = %s",
                            (peer["peer_id"],)).fetchone()
            assert row is not None


def test_company_relations_of_returns_empty_list_when_none_exist():
    """A company never fetched into a relation context still gets a clean
    empty list, not an error — the template's empty-state branch depends on
    this, and it's the one branch the 80-peer fixture above never exercises."""
    with conn() as c:
        peers = company_relations_of(c, TEST_COMPANY_NO_RELATIONS_SOURCE_ID)
    assert peers == []


def test_company_relations_covers_all_nine_relation_basis_values():
    """The CHECK constraint on registry_ownership.company_relation_context.relation_basis allows
    exactly 9 values (schema.sql). RELATION_BASIS_LABELS must have a real
    label for every one of them, not just whichever ones one fixture company
    happens to exhibit — Task 1's review found a crash from exactly this
    class of gap (an unhandled value that no single fixture triggered)."""
    check_constraint_values = {
        "DIRECT_OWNERSHIP", "INDIRECT_OWNERSHIP", "SHARED_OWNER",
        "SHARED_BENEFICIAL_OWNER", "SHARED_BOARD_PERSON", "SHARED_MANAGEMENT_PERSON",
        "CROSS_ROLE_PERSON", "SHARED_OTHER_RELATION", "DIRECT_CONTROL_ACTOR",
    }
    assert set(RELATION_BASIS_LABELS) == check_constraint_values
    assert _DIRECTIONAL_BASES <= check_constraint_values
    with conn() as c:
        db_values = {r["relation_basis"] for r in c.execute(
            "SELECT DISTINCT relation_basis FROM registry_ownership.company_relation_context").fetchall()}
    assert db_values, "expected at least one relation_basis value in the live DB"
    assert db_values <= check_constraint_values


# --- person portal (§17.1/§17.1a/§17.2/§17.3) ---------------------------------

def test_person_page_never_leaks_raw_label_or_address():
    with conn() as c:
        ent = entity(c, TEST_PERSON_FULL)
        owners_of(c, TEST_PERSON_FULL)  # sanity: function accepts a person id
    # entity() already excludes raw_inventory_label by column list (existing
    # behavior) — this test locks that guarantee in for the person route too.
    assert "raw_inventory_label" not in ent
    assert ent["entity_kind"] == "PERSON"


def test_person_control_registration_has_no_percent_sign():
    """Same guarantee as test_control_section_renders_no_percentage_at_all, but
    for the §17.1a actor side. Rendered via the established mechanism in this
    file (app.test_client() + a full page fetch), matching how
    test_the_three_control_states_render_three_distinct_markers does it for
    the company page. Checked across both live-reachable actor-side states —
    REGISTERED_ACTORS and NO_CONTROL_DATA (no person fixture is known to hit
    NO_REGISTERED_CONTROL_ACTOR on the actor side, see control_registrations_of_actor's
    own comment) — not just the one state the original version of this test
    happened to cover."""
    import re
    client = app.test_client()
    for person_id in (TEST_PERSON_WITH_CONTROL_REG, TEST_PERSON_NO_CONTROL_DATA):
        html = client.get(f"/persons/{person_id}").get_data(as_text=True)
        section = re.search(r'<section class="card section control".*?</section>', html, re.S)
        assert section, f"expected a §17.1a kontrolregistrering section for person {person_id}"
        body = section.group(0).replace("&nbsp;", " ")
        assert "%" not in body, (person_id, [l for l in body.splitlines() if "%" in l])


def test_roles_held_by_person_groups_into_three_buckets_only():
    with conn() as c:
        groups = roles_held_by_person(c, TEST_PERSON_WITH_ROLE)
    assert set(groups) == {"NEVND", "STJORN", "ANNAD"}
    total = sum(len(v) for v in groups.values())
    assert total > 0, "fixture person must hold at least one role"


def test_roles_held_by_person_covers_all_four_inferred_group_values():
    """The CHECK constraint on inferred_group allows exactly 4 values
    (schema.sql): EIGARAR, NEVND, STJORN, ANNAD. roles_held_by_person must
    bucket exactly the three non-EIGARAR ones and never crash on the fourth —
    the identical gap that made roles_at_company KeyError on a live EIGARAR
    row before Task 1's fix. Applied proactively here, not rediscovered."""
    check_constraint_values = {"EIGARAR", "NEVND", "STJORN", "ANNAD"}
    assert set(ROLE_GROUPS) | {"EIGARAR"} == check_constraint_values
    with conn() as c:
        groups = roles_held_by_person(c, TEST_PERSON_WITH_VOTING_RIGHTS)
        voting = voting_rights_held_by_person(c, TEST_PERSON_WITH_VOTING_RIGHTS)
    assert voting, "fixture person must have at least one voting-rights row"
    for rows in groups.values():
        for row in rows:
            assert row["inferred_group"] != "EIGARAR"


def test_person_voting_rights_render_in_their_own_section_not_500():
    client = app.test_client()
    r = client.get(f"/persons/{TEST_PERSON_WITH_VOTING_RIGHTS}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Stemmerettigheder" in html
    assert "Atkvøðurættur" in html


def test_person_pages_render_for_every_fixture():
    client = app.test_client()
    for eid in (TEST_PERSON_FULL, TEST_PERSON_WITH_VOTING_RIGHTS, TEST_PERSON_NO_CONTROL_DATA):
        r = client.get(f"/persons/{eid}")
        assert r.status_code == 200, (eid, r.status_code)
        html = r.get_data(as_text=True)
        assert "Kildeinformation" in html
        assert "Roller i virksomheder" in html


def test_person_control_states_reachable_render_distinct_markers():
    """§17.1a's actor side reaches REGISTERED and NO_DATA live (verified: 0
    PERSON rows ever carry a NO_REGISTERED_CONTROL_ACTOR status, so
    NONE_REGISTERED is structurally unreachable for a person in this dataset
    today — see control_registrations_of_actor's own comment). The pure
    control_state() logic is still exercised for all three states by the
    existing dict-based tests above; this only checks what's live-reachable."""
    client = app.test_client()
    with conn() as c:
        assert control_registrations_of_actor(
            c, TEST_PERSON_WITH_CONTROL_REG,
            entity(c, TEST_PERSON_WITH_CONTROL_REG)["source_entity_id"])["state"] == CONTROL_REGISTERED
        assert control_registrations_of_actor(
            c, TEST_PERSON_NO_CONTROL_DATA,
            entity(c, TEST_PERSON_NO_CONTROL_DATA)["source_entity_id"])["state"] == CONTROL_NO_DATA
    html_reg = client.get(f"/persons/{TEST_PERSON_WITH_CONTROL_REG}").get_data(as_text=True)
    html_none = client.get(f"/persons/{TEST_PERSON_NO_CONTROL_DATA}").get_data(as_text=True)
    assert "state-actors" in html_reg and "state-none" not in html_reg and "state-nodata" not in html_reg
    assert "state-nodata" in html_none and "state-actors" not in html_none and "state-none" not in html_none


def test_person_address_markers_never_reach_the_page():
    """Reuses the same check list as test_person_list_page_renders_names_only:
    a PERSON's raw source label can carry a home address (§5), so any raw
    label for the fixture person must never appear on their own detail page."""
    client = app.test_client()
    html = client.get(f"/persons/{TEST_PERSON_FULL}").get_data(as_text=True)
    with conn() as c:
        raw = c.execute(
            "SELECT raw_inventory_label AS l FROM v_entity WHERE canonical_entity_id = %s"
            " AND raw_inventory_label IS NOT NULL", (TEST_PERSON_FULL,)).fetchall()
    for r in raw:
        assert r["l"] not in html, "a raw person label reached the page"


# --- person relations (§17.4/§18) ---------------------------------------------

def test_person_relations_categorizes_by_canonical_relation_type_not_group_alone():
    """Regression guard: person_a_group/person_b_group alone would merge
    DIRECT_OWNERSHIP and REGISTERED_CONTROL_ACTOR under the shared EIGARAR
    group. This must never collapse SHARED_OWNER and SHARED_BENEFICIAL_OWNER
    into one bucket."""
    with conn() as c:
        ent = entity(c, TEST_PERSON_WITH_PEER_RELATIONS)
        peers = person_relations_of(c, ent["source_entity_id"])
    assert peers, "fixture person must have at least one peer relation"
    bases_seen = {ctx["relation_basis"] for peer in peers for ctx in peer["contexts"]}
    # SHARED_OWNER and SHARED_BENEFICIAL_OWNER are structurally different
    # facts and must be independently reachable, not merged.
    assert bases_seen <= {
        "SHARED_OWNER", "SHARED_BENEFICIAL_OWNER", "SHARED_BOARD_PERSON",
        "SHARED_MANAGEMENT_PERSON", "CROSS_ROLE_PERSON", "SHARED_OTHER_RELATION",
    }


def test_person_relations_never_shows_a_bare_total():
    with conn() as c:
        ent = entity(c, TEST_PERSON_WITH_PEER_RELATIONS)
        peers = person_relations_of(c, ent["source_entity_id"])
    for peer in peers:
        assert sum(peer["counts"].values()) == len(peer["contexts"])


def test_person_relations_mixed_eigarar_group_pair_never_collapses_to_one_basis():
    """The specific live regression case this task exists for: person
    9277/13568's shared contexts include rows where BOTH sides carry
    person_a_group=person_b_group='EIGARAR' but different
    canonical_relation_type (DIRECT_OWNERSHIP vs REGISTERED_CONTROL_ACTOR).
    Dispatching on group alone would put those under one shared bucket;
    dispatching on canonical_relation_type (as _person_relation_basis does)
    must instead fall through to CROSS_ROLE_PERSON, and the pair must also
    independently reach SHARED_OWNER (both DIRECT_OWNERSHIP) and
    SHARED_BENEFICIAL_OWNER (both REGISTERED_CONTROL_ACTOR) from its other
    rows — proving all three bases are simultaneously reachable for one pair,
    which a group-only dispatch could never produce."""
    with conn() as c:
        ent = entity(c, TEST_PERSON_WITH_PEER_RELATIONS)
        peers = person_relations_of(c, ent["source_entity_id"])
        # Confirm the live DB still actually contains the mixed-type shape
        # this test depends on, rather than trusting the fixture comment.
        mixed = c.execute(
            "SELECT count(*) AS n FROM registry_ownership.person_relation_context prc"
            " JOIN registry_ownership.relation ra ON ra.relation_id = prc.person_a_relation_id"
            " JOIN registry_ownership.relation rb ON rb.relation_id = prc.person_b_relation_id"
            " WHERE prc.person_a_entity_id = %s AND prc.person_b_entity_id = %s"
            " AND prc.person_a_group = 'EIGARAR' AND prc.person_b_group = 'EIGARAR'"
            " AND ra.canonical_relation_type <> rb.canonical_relation_type",
            (ent["source_entity_id"], 13568)).fetchone()["n"]
        assert mixed > 0, "live fixture pair no longer has the mixed EIGARAR-group shape this test needs"
        peer_name = entity(c, TEST_PEER_OF_PEER_RELATIONS)["display_name"]

    peer = next((pe for pe in peers if pe["peer_name"] == peer_name), None)
    assert peer is not None, "fixture pair (source 9277/13568) not found among this person's peers"
    assert "CROSS_ROLE_PERSON" in peer["counts"], \
        "the mixed DIRECT_OWNERSHIP/REGISTERED_CONTROL_ACTOR rows must land in CROSS_ROLE_PERSON"
    assert "SHARED_OWNER" in peer["counts"], "same-pair DIRECT_OWNERSHIP/DIRECT_OWNERSHIP rows must still reach SHARED_OWNER"
    assert "SHARED_BENEFICIAL_OWNER" in peer["counts"], \
        "same-pair REGISTERED_CONTROL_ACTOR/REGISTERED_CONTROL_ACTOR rows must still reach SHARED_BENEFICIAL_OWNER"


def test_person_relation_basis_dispatches_on_relation_type_before_group():
    """Pure unit check on the dispatch function itself: two EIGARAR-group rows
    with different canonical_relation_type must never produce SHARED_OWNER or
    SHARED_BENEFICIAL_OWNER — the exact shortcut this task exists to avoid."""
    basis = _person_relation_basis("DIRECT_OWNERSHIP", "EIGARAR", "REGISTERED_CONTROL_ACTOR", "EIGARAR")
    assert basis == "CROSS_ROLE_PERSON", basis
    assert _person_relation_basis("DIRECT_OWNERSHIP", "EIGARAR", "DIRECT_OWNERSHIP", "EIGARAR") == "SHARED_OWNER"
    assert _person_relation_basis(
        "REGISTERED_CONTROL_ACTOR", "EIGARAR", "REGISTERED_CONTROL_ACTOR", "EIGARAR") == "SHARED_BENEFICIAL_OWNER"


def test_person_relations_resolves_peer_and_company_to_canonical_id_never_raw():
    with conn() as c:
        ent = entity(c, TEST_PERSON_WITH_PEER_RELATIONS)
        peers = person_relations_of(c, ent["source_entity_id"])
        assert peers, "fixture person must have at least one peer relation"
        for peer in peers:
            assert isinstance(peer["peer_id"], int)
            assert peer["peer_kind"] == "PERSON"
            row = c.execute("SELECT 1 FROM canonical_entity WHERE canonical_entity_id = %s",
                            (peer["peer_id"],)).fetchone()
            assert row is not None
            for ctx in peer["contexts"]:
                assert ctx["related_entity_kind"] == "COMPANY"
                row = c.execute("SELECT 1 FROM canonical_entity WHERE canonical_entity_id = %s",
                                (ctx["related_entity_id"],)).fetchone()
                assert row is not None


def test_person_relations_of_returns_empty_list_when_none_exist():
    with conn() as c:
        ent = entity(c, TEST_PERSON_NO_RELATIONS)
        peers = person_relations_of(c, ent["source_entity_id"])
    assert peers == []


def test_person_relation_headline_counts_companies_not_role_combos():
    """Owner ruling 2026-08-15: outward the relation between two persons
    counts once per company. The breakdown inside may still show the
    internal role combinations. Live fixture: persons 6217 and 5677 hold
    several basis contexts in ONE company — the peer headline must say 1
    relation."""
    client = app.test_client()
    html = client.get("/persons/6217").get_data(as_text=True)
    with conn() as c:
        i = html.index(entity(c, 5677)["display_name"])
    seg = html[i:i + 700]
    import re
    assert re.search(r'relation-total">\s*1\s+relation', seg), \
        "the outward total must count companies, not role combinations"
    assert "Vis de" in html, "the internal breakdown list must still exist"


def test_person_pair_holds_one_relation_per_company_plus_ownership():
    """Owner ruling 2026-08-15: two persons hold at most ONE relation per
    company — except the ownership-share relation, which stands alongside.
    Live fixture: 6512/9696 share 10 cross-role rows AND 2 same-Nevnd rows
    AND 4 EIGARAR rows in company 1493 (Cura). That must collapse to
    SHARED_BOARD_PERSON 1 (most specific non-ownership basis wins; the
    cross-role rows never surface) plus the ownership relations — never
    10 cross + 2 board."""
    with conn() as c:
        rows = c.execute(
            "SELECT count(*) AS n, count(DISTINCT company_entity_id) AS d"
            " FROM registry_ownership.person_relation_context WHERE is_cross_role"
            " AND person_a_entity_id = 6512 AND person_b_entity_id = 9696").fetchone()
        assert rows["n"] > 1, "live fixture no longer holds multi-combination cross-role rows"
        assert rows["d"] == 1, "fixture pair must share cross-role rows in exactly one company"
        ent = entity(c, 1503)
        peers = person_relations_of(c, ent["source_entity_id"])
    peer = next((pe for pe in peers if pe["peer_id"] == 2219), None)
    assert peer is not None, "fixture peer (canonical 2219) not found among person 1503's peers"
    assert peer["counts"].get("SHARED_BOARD_PERSON") == 1, (
        f"one non-ownership relation per company, got {peer['counts']}")
    assert "CROSS_ROLE_PERSON" not in peer["counts"], (
        "cross-role rows must collapse into the most specific basis, got "
        f"{peer['counts']}")
    assert sum(peer["counts"].values()) == len(peer["contexts"])


def test_person_relations_page_renders_breakdown_not_a_bare_total():
    client = app.test_client()
    r = client.get(f"/persons/{TEST_PERSON_WITH_PEER_RELATIONS}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Relation til andre personer" in html
    with conn() as c:
        assert entity(c, TEST_PEER_OF_PEER_RELATIONS)["display_name"] in html
    assert "Personportalen er ikke bygget endnu" not in html
    assert "er beskrevet i en separat opgave" not in html


def test_company_page_still_renders_correctly_after_shared_macro_change():
    """The relation_breakdown macro's hardcoded entity kind was replaced with
    ctx.related_entity_kind so it can serve both company peers (a shared
    PERSON) and person peers (a shared COMPANY). This re-checks the company
    page specifically: its related entity is a person, and that link must
    still point at /persons/, never /companies/."""
    client = app.test_client()
    r = client.get(f"/companies/{TEST_COMPANY_WITH_RELATIONS}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Relation til andre virksomheder" in html
    with conn() as c:
        ent = entity(c, TEST_COMPANY_WITH_RELATIONS)
        peers = company_relations_of(c, ent["source_entity_id"])
    named_related = [ctx for peer in peers for ctx in peer["contexts"] if ctx["related_entity_id"]]
    assert named_related, "fixture company must have at least one non-directional context with a related entity"
    for ctx in named_related:
        assert ctx["related_entity_kind"] == "PERSON"
    sample = named_related[0]
    assert f'href="/persons/{sample["related_entity_id"]}"' in html


# --- §21 netværk (ego graph) --------------------------------------------------

def test_ego_graph_every_edge_endpoint_is_a_listed_node():
    with conn() as c:
        ent = entity(c, TEST_COMPANY_WITH_ROLES)
        graph = ego_graph(c, TEST_COMPANY_WITH_ROLES, ent["source_entity_id"])
    node_ids = {n["id"] for n in graph["nodes"]} | {graph["center"]["id"]}
    for edge in graph["edges"]:
        assert edge["from_id"] in node_ids
        assert edge["to_id"] in node_ids


def test_ego_graph_edge_kinds_are_one_of_three_values():
    with conn() as c:
        ent = entity(c, TEST_COMPANY_WITH_ROLES)
        graph = ego_graph(c, TEST_COMPANY_WITH_ROLES, ent["source_entity_id"])
    for edge in graph["edges"]:
        assert edge["kind"] in ("OWNERSHIP", "CONTROL", "ROLE")


def test_ego_graph_handles_a_genuine_ownership_self_loop():
    """NAX (5708) owns 21,25 % of itself in the source (verified live). The
    self-loop edge's endpoints are both the center id, which is not in
    graph["nodes"] (the center is popped out into its own key) — the
    endpoint-is-a-listed-node guarantee above only holds because the center's
    id is unioned in. This test pins that specific shape down directly."""
    with conn() as c:
        ent = entity(c, TEST_COMPANY_WITH_ROLES)
        graph = ego_graph(c, TEST_COMPANY_WITH_ROLES, ent["source_entity_id"])
    self_loops = [e for e in graph["edges"]
                  if e["from_id"] == TEST_COMPANY_WITH_ROLES and e["to_id"] == TEST_COMPANY_WITH_ROLES]
    assert self_loops, "fixture must still have its self-ownership row"
    assert self_loops[0]["kind"] == "OWNERSHIP"


def test_ego_graph_control_registration_defaults_missing_kind_to_unknown_entity():
    """v_control_registration exposes actor_kind but not the registered
    entity's kind. TEST_PERSON_FULL is registered as a control actor FOR
    canonical id 1499 — which is genuinely entity_kind
    COMPANY, verified live — but ego_graph cannot know that from this view
    and must default to UNKNOWN_ENTITY rather than guess or crash. This is
    the brief's documented, deliberate simplification, not a silent gap."""
    with conn() as c:
        ent = entity(c, TEST_PERSON_FULL)
        graph = ego_graph(c, TEST_PERSON_FULL, ent["source_entity_id"])
        real_kind = c.execute(
            "SELECT entity_kind FROM canonical_entity WHERE canonical_entity_id = %s",
            (TEST_NETWORK_UNKNOWN_KIND_NEIGHBOR_ID,)).fetchone()["entity_kind"]
    node = next(n for n in graph["nodes"] if n["id"] == TEST_NETWORK_UNKNOWN_KIND_NEIGHBOR_ID)
    assert real_kind == "COMPANY", "fixture assumption changed — pick a new id"
    assert node["kind"] == "UNKNOWN_ENTITY"


def test_ego_graph_covers_the_real_entity_kinds_seen_in_live_neighbors():
    """Lesson from prior review cycles: verify against the live database that
    every entity_kind that can actually appear among a 1-hop neighborhood is
    exercised, not just PERSON/COMPANY. canonical_entity overall only has
    PERSON/COMPANY/ORGANIZATION/PUBLIC_AUTHORITY rows (no real UNKNOWN_ENTITY
    row exists) — UNKNOWN_ENTITY is ego_graph's own synthetic fallback,
    covered separately above. macros.html's KIND dict has a defined,
    non-crashing entry for all five; this test proves the other four are
    real, reachable data shapes ego_graph must actually produce."""
    with conn() as c:
        seen = set()
        for cid, sid_of in (
                (TEST_COMPANY_WITH_ROLES, None),
                (TEST_COMPANY_OWNED_BY_ORGANIZATION, None),
                (TEST_COMPANY_OWNED_BY_PUBLIC_AUTHORITY, None)):
            ent = entity(c, cid)
            graph = ego_graph(c, cid, ent["source_entity_id"])
            seen |= {n["kind"] for n in graph["nodes"]}
    assert {"PERSON", "COMPANY", "ORGANIZATION", "PUBLIC_AUTHORITY"} <= seen, seen


def test_ego_layout_places_center_and_every_node_on_the_circle():
    with conn() as c:
        ent = entity(c, TEST_COMPANY_WITH_ROLES)
        graph = ego_graph(c, TEST_COMPANY_WITH_ROLES, ent["source_entity_id"])
    layout = ego_layout(graph)
    assert layout["center"]["x"] == 400 and layout["center"]["y"] == 400
    assert layout["truncated"] is False
    assert layout["shown_count"] == len(graph["nodes"])
    for node in layout["nodes"]:
        assert "x" in node and "y" in node  # exact radius checked in the next test


def test_ego_layout_every_shown_node_sits_exactly_on_the_300_radius_circle():
    with conn() as c:
        ent = entity(c, TEST_COMPANY_WITH_ROLES)
        graph = ego_graph(c, TEST_COMPANY_WITH_ROLES, ent["source_entity_id"])
    layout = ego_layout(graph)
    for node in layout["nodes"]:
        dist_sq = (node["x"] - 400) ** 2 + (node["y"] - 400) ** 2
        assert abs(dist_sq - 300 ** 2) < 1e-6


def test_ego_layout_caps_at_60_nodes_and_states_the_true_total_without_dropping_it():
    """Pure logic test, no DB: a fabricated 100-node ego graph must be capped
    at 60 displayed nodes, must say so (truncated=True), and must still state
    the true total (100) rather than silently truncating. Edges touching a
    dropped node must not survive into the shown edge list — the SVG and the
    plain-HTML fallback below it must always describe the identical edge set."""
    center = {"id": 0, "name": "Center", "kind": "COMPANY"}
    nodes = [{"id": i, "name": f"N{i}", "kind": "PERSON"} for i in range(1, 101)]
    edges = ([{"from_id": 0, "to_id": i, "kind": "OWNERSHIP", "label": "x"} for i in range(1, 101)]
             + [{"from_id": 99, "to_id": 100, "kind": "ROLE", "label": "y"}])  # both endpoints dropped
    graph = {"center": center, "nodes": nodes, "edges": edges}
    layout = ego_layout(graph)
    assert layout["truncated"] is True
    assert layout["total_nodes"] == 100
    assert layout["shown_count"] == 60
    assert len(layout["nodes"]) == 60
    shown_ids = {n["id"] for n in layout["nodes"]} | {0}
    for edge in layout["edges"]:
        assert edge["from_id"] in shown_ids and edge["to_id"] in shown_ids
    # the dropped-both-endpoints edge (99 -> 100, both > 60) must not survive
    assert not any(e["from_id"] == 99 and e["to_id"] == 100 for e in layout["edges"])


def test_ego_layout_under_the_cap_is_not_marked_truncated():
    graph = {"center": {"id": 0, "name": "C", "kind": "COMPANY"},
             "nodes": [{"id": 1, "name": "A", "kind": "PERSON"}], "edges": []}
    layout = ego_layout(graph)
    assert layout["truncated"] is False
    assert layout["total_nodes"] == 1 == layout["shown_count"]


def test_network_route_serves_and_lists_every_edge_as_html():
    client = app.test_client()
    resp = client.get(f"/companies/{TEST_COMPANY_WITH_ROLES}/network")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "<svg" in body
    assert "<ul" in body or "<table" in body  # the plain-HTML fallback list


def test_network_route_html_table_lists_the_exact_same_edges_as_ego_graph():
    """The self-review bar: the plain-HTML fallback must contain literally the
    same edge set as the SVG (built from the same layout["edges"] list in the
    template), not a summary. Checked by asserting every edge's own label text
    (a distinguishing string per row: a percentage, or a role title) appears
    in the rendered page, and that the count is not smaller than the graph's."""
    with conn() as c:
        ent = entity(c, TEST_COMPANY_WITH_ROLES)
        graph = ego_graph(c, TEST_COMPANY_WITH_ROLES, ent["source_entity_id"])
    client = app.test_client()
    body = client.get(f"/companies/{TEST_COMPANY_WITH_ROLES}/network").get_data(as_text=True)
    ledger = body[body.index("Alle kanter i netværket"):]  # the fallback ledger table
    ledger = ledger[:ledger.index("</table>")]
    assert ledger.count("<tr>") - 1 == len(graph["edges"]), \
        "one <tr> per edge, plus the header row's own <tr>"
    # role labels are shown in product wording (templates/network.html)
    product_role = app.jinja_env.filters["product_role"]
    for edge in graph["edges"]:
        role = edge["kind"] == "ROLE" or "Veruligur" in edge["label"]
        assert (product_role(edge["label"]) if role else edge["label"]) in ledger


def test_network_route_person_side_serves_and_every_node_is_a_real_link():
    with conn() as c:
        ent = entity(c, TEST_PERSON_FULL)
        graph = ego_graph(c, TEST_PERSON_FULL, ent["source_entity_id"])
    client = app.test_client()
    r = client.get(f"/persons/{TEST_PERSON_FULL}/network")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "<svg" in body
    for node in graph["nodes"]:
        # §3.3/§3.4: routing is by entity_url — the node's synthetic kind (e.g.
        # UNKNOWN_ENTITY for 1499) routes to /entities/, which then redirects
        # to the real surface. The link must exist and resolve, never 404.
        href = f'href="{entity_url(node["kind"], node["id"])}"'
        assert href in body, f'node {node["id"]} ({node["name"]}) has no link in the network page'
        assert client.get(entity_url(node["kind"], node["id"])).status_code in (200, 302)


def test_network_route_wrong_kind_is_404():
    """§16/§17's own PERSON/COMPANY split applies here too: a person's id
    on the company network route (and vice versa) must 404, not render the
    wrong entity's page under company framing."""
    client = app.test_client()
    assert client.get(f"/companies/{TEST_PERSON_FULL}/network").status_code == 404
    assert client.get(f"/persons/{TEST_COMPANY_WITH_ROLES}/network").status_code == 404


def test_company_and_person_pages_link_to_their_network_view():
    client = app.test_client()
    company_html = client.get(f"/companies/{TEST_COMPANY_WITH_ROLES}").get_data(as_text=True)
    assert f'href="/companies/{TEST_COMPANY_WITH_ROLES}/network"' in company_html
    person_html = client.get(f"/persons/{TEST_PERSON_FULL}").get_data(as_text=True)
    assert f'href="/persons/{TEST_PERSON_FULL}/network"' in person_html


def test_network_route_zero_edges_shows_empty_state_not_an_empty_table():
    """Final-review Finding 3: a real zero-edge fixture (verified live — zero
    rows across v_direct_ownership/v_control_registration/v_role_relation for
    this canonical id) must render the empty-state paragraph, not an empty
    <table>."""
    client = app.test_client()
    r = client.get(f"/companies/{TEST_COMPANY_NO_NETWORK_EDGES}/network")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Ingen direkte ejerskabs-, kontrol- eller rollerelationer" in body
    assert "<table" not in body


# --- final-review fix wave (2026-08-03) ----------------------------------------

def test_person_page_never_falsely_claims_partial_ownership():
    """Owner ruling (2026-08-15): a person cannot be owned, so §17.1 is
    removed from person pages entirely — no direct-ownership block, no
    INCOMING tally ("X % — Delvist registreret"), no "Indirekte ejere"
    sub-section. This must hold on every person page, not just one fixture,
    while the company page keeps its §16.1."""
    client = app.test_client()
    for person_id in (TEST_PERSON_FULL, TEST_PERSON_WITH_VOTING_RIGHTS, TEST_PERSON_NO_CONTROL_DATA):
        html = client.get(f"/persons/{person_id}").get_data(as_text=True)
        assert "Hvem ejer personen?" not in html, person_id
        assert "Indirekte ejere" not in html, person_id
        assert "Delvist registreret" not in html, person_id
    company_html = client.get(f"/companies/{ORDINARY}").get_data(as_text=True)
    assert "Hvem ejer virksomheden?" in company_html, "§16.1 must stay on company pages"


def test_relation_breakdown_counts_render_before_the_collapsible_detail():
    """Final-review Finding 5: the non-collapsible-breakdown rule (§16.4/§18/
    §19 — a total is never shown without its per-category breakdown beside
    it) is a markup property, not just the data-level sum(counts) ==
    len(contexts) invariant already covered elsewhere. relation_breakdown's
    <ul class="relation-counts"> must sit before/outside the <details>
    element it shares the macro with, on both pages that use the shared
    relation_breakdown macro — checked via string offset, the same technique
    used to verify this by hand during the final branch review."""
    client = app.test_client()
    for path in (f"/companies/{TEST_COMPANY_WITH_RELATIONS}",
                 f"/persons/{TEST_PERSON_WITH_PEER_RELATIONS}"):
        html = client.get(path).get_data(as_text=True)
        counts_at = html.index("relation-counts")
        # scoped to relation_breakdown's own <details> (class relation-detail):
        # the page now contains other, unrelated details elements (help icons)
        details_at = html.index("relation-detail")
        assert counts_at < details_at, path


def test_person_owners_tree_matches_direct_plus_indirect_ground_truth():
    """Ejerayvirlit data-lag: alle personer bag virksomheden = direkte person-
    ejere forenet med indirekte person-ejere, dedup pr. person, best_pct =
    stærkeste sti (aldrig summen), sorteret faldende."""
    COMPANY_ID = 8220  # Jókin — har både direkte og indirekte person-ejere (live)
    with conn() as c:
        ent = entity(c, COMPANY_ID)
        tree = person_owners_tree(c, COMPANY_ID, ent["source_entity_id"])
        # ground truth: direkte person-ejere (maks. parsed_percentage pr. person)
        direct = set()
        direct_pct = {}
        for r in owners_of(c, COMPANY_ID):
            if r["owner_kind"] != "PERSON":
                continue
            direct.add(r["owner_id"])
            pct = r["parsed_percentage"]
            if pct is not None and (direct_pct.get(r["owner_id"]) is None
                                    or pct > direct_pct[r["owner_id"]]):
                direct_pct[r["owner_id"]] = pct
        # ground truth: indirekte person-ejere med sti-produkter
        indirect = {}
        for r in hydrate_paths(c, indirect_owners_of(c, ent["source_entity_id"])):
            cp = r["counterparty"]
            if cp is not None and cp["entity_kind"] == "PERSON":
                best = max((path["percentage"] for path in r["paths"]), default=None)
                cur = indirect.setdefault(cp["canonical_entity_id"], None)
                if best is not None and (cur is None or best > cur):
                    indirect[cp["canonical_entity_id"]] = best
    assert tree, "fixture company must have at least one person behind it"
    assert {t["canonical_entity_id"] for t in tree} == direct | set(indirect)
    by_id = {t["canonical_entity_id"]: t for t in tree}
    for pid, best in indirect.items():
        # best_pct = stærkeste af direkte andel og indirekte sti-produkt —
        # en direkte andel kan sagtens være stærkere end den indirekte sti
        expected = best
        if direct_pct.get(pid) is not None and direct_pct[pid] > expected:
            expected = direct_pct[pid]
        assert by_id[pid]["best_pct"] == expected, pid
    for pid in direct:
        assert by_id[pid]["direct"] is True, pid
    # sorteret: best_pct faldende, None sidst
    pcts = [t["best_pct"] for t in tree]
    assert pcts == sorted(pcts, key=lambda x: (x is None, -x)), pcts[:5]


def test_top_persons_caps_at_the_given_limit():
    persons = [{"display_name": f"p{i}", "best_pct": 100 - i} for i in range(150)]
    assert len(top_persons(persons, cap=100)) == 100
    assert top_persons(persons, cap=100)[0]["display_name"] == "p0"
    assert len(top_persons(persons[:3], cap=100)) == 3


def test_ownership_tree_layout_includes_all_branches_and_dead_ends():
    """Ejerayvirlit-grafen (ejerbeslutning 2026-08-16): ALLE grene med —
    grene der ender i personer OG grene der ender i døde ender (virksomheder
    uden registrerede ejere). Virksomheden er roden i bunden."""
    COMPANY_ID = 8220
    with conn() as c:
        ent = entity(c, COMPANY_ID)
        rows = hydrate_paths(c, indirect_owners_of(c, ent["source_entity_id"]))
        direct = owners_of(c, COMPANY_ID)
        tree = ownership_tree_layout(ent, rows, direct)
    assert rows, "fixture company must have incoming ownership rows"
    node_ids = {n["id"] for n in tree["nodes"]}
    cps = {r["counterparty"]["canonical_entity_id"] for r in rows if r["counterparty"]}
    assert cps <= node_ids, "hver gren-ende — også døde ender — skal være en node"
    dead = {r["counterparty"]["canonical_entity_id"] for r in rows
            if r["counterparty"] and r["counterparty"]["entity_kind"] != "PERSON"}
    assert dead, "fixture must include a dead-end company branch"
    assert dead <= node_ids
    for e in tree["edges"]:
        assert e["from_id"] in tree["pos"] and e["to_id"] in tree["pos"], \
            ("kant-endepunkter skal være noder i grafen", e)
        assert e["pct"] is not None
    assert tree["width"] > 0 and tree["height"] > 0
    root = next(n for n in tree["nodes"] if n["is_center"])
    assert root["id"] == COMPANY_ID
    deepest_y = max(tree["pos"].values(), key=lambda p: p["y"])["y"]
    assert tree["pos"][COMPANY_ID]["y"] == deepest_y, "virksomheden skal stå nederst"


def test_dashboard_has_kpi_cards_with_the_list_leaders():
    client = app.test_client()
    html = client.get("/").get_data(as_text=True)
    assert html.count('class="kpi"') == 4, "fire KPI-kort"
    with conn() as c:
        tops = top_lists(c)
    assert tops["direct"][0]["name"] in html
    assert tops["stjorn"][0]["name"] in html


def test_dashboard_charts_match_the_database_counts():
    """Farvekodningsrunde: de tre dashboard-fordelinger er rene tællinger."""
    with conn() as c:
        charts = dashboard_charts(c)
        gt_kinds = c.execute("SELECT entity_kind, count(*) AS n FROM canonical_entity"
                             " GROUP BY 1 ORDER BY n DESC").fetchall()
        gt_rel = c.execute("SELECT canonical_relation_type, count(*) AS n FROM registry_ownership.relation"
                           " GROUP BY 1 ORDER BY n DESC").fetchall()
        gt_bands = c.execute(
            "SELECT count(*) FILTER (WHERE parsed_percentage >= 100) AS b100,"
            " count(*) FILTER (WHERE parsed_percentage >= 50 AND parsed_percentage < 100) AS b50,"
            " count(*) FILTER (WHERE parsed_percentage >= 25 AND parsed_percentage < 50) AS b25,"
            " count(*) FILTER (WHERE parsed_percentage >= 10 AND parsed_percentage < 25) AS b10,"
            " count(*) FILTER (WHERE parsed_percentage < 10) AS b1"
            " FROM v_direct_ownership WHERE parsed_percentage IS NOT NULL").fetchone()
    assert [r["n"] for r in charts["kinds"]] == [r["n"] for r in gt_kinds]
    assert [r["n"] for r in charts["relations"]] == [r["n"] for r in gt_rel]
    assert [r["n"] for r in charts["bands"]] == \
           [gt_bands["b1"], gt_bands["b10"], gt_bands["b25"], gt_bands["b50"], gt_bands["b100"]]


def test_dashboard_renders_the_color_charts():
    client = app.test_client()
    html = client.get("/").get_data(as_text=True)
    for title in ("Enheder efter type", "Relationer efter type",
                  "Ejerandele efter størrelse"):
        assert title in html, title
    assert html.count('class="chart-svg"') == 3
    assert '<rect class="chart-bar"' in html


def test_heatband_filter_orders_zero_to_high():
    assert heatband(0) == "n-0"
    assert heatband(2) == "n-1"
    assert heatband(5) == "n-2"
    assert heatband(6) == "n-3"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} checks passed")
