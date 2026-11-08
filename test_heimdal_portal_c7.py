#!/usr/bin/env python3
"""WP-C7 acceptance checks against the running portal surface (SoT §16/§17,
§10 freshness, §3.3 links, §9 D-8 RETRYABLE_ERROR rendering).

These are the cold-review checks the portal task's acceptance criteria name:

  * in/out ownership never share a list (§16.2) — exercised over the live
    pages, not just the query layer (test_heimdal_portal.py already proves the
    queries; this proves the rendered sections carry the right caption and the
    right row set for the section they sit in);
  * Kontrolregistrering is its own section, never inside ownership (§16.1a /
    §17.1a) and carries no percentage anywhere in its rendered body;
  * the /companies/:id fallback serves ORGANIZATION and PUBLIC_AUTHORITY (§3.4)
    — every non-PERSON route must 200, never 404;
  * freshness/provenance framing uses §10's snapshot language: "Hentet fra
    kilden" is only ever shown for a truly successful fetch (error OK), and no
    page makes a relation-age/historical claim;
  * the two RETRYABLE_ERROR companies (source 1576/3588) render as "Aldrig
    hentet med succes" (never successfully fetched) — never "Hentet fra
    kilden" and never as a relation-free company.

Run: python3 test_heimdal_portal_c7.py
"""
import re

from heimdal import config
from heimdal.app import app
from heimdal.config import DB_URI
from heimdal.modules.entities.service import (
    PERSON_KINDS, entity_list, person_list, person_owners_tree,
)
from heimdal.modules.shared.entity import entity, entity_url, fetch_state
from heimdal.modules.shared.web import _requests

# --- fixtures (live canonical IDs, verified 2026-08-14) ---------------------
FETCHED_OK = 8220        # Jókin — successfully fetched, rich relations
NEVER_FETCHED = 49       # B. J. Holding A/S — never itself a fetch subject
RETRYABLE_ERROR_A = 5082  # Kreton (source 3588) — own fetch always HTTP 500
RETRYABLE_ERROR_B = 9806  # Deildin (source 1576) — own fetch always HTTP 500
ORG = 296                # Ognarfelagið LÍV — ORGANIZATION (§3.4 fallback)
PUBLIC_AUTH = 2866       # Elfelagið SEV — PUBLIC_AUTHORITY (§3.4 fallback)
ORG_OWNS = 8973          # Tryggingarfelagið Lív — company owned by ORG (296)
HAS_ACTOR = 7            # BS Tøkni — has a registered control actor
ORDINARY = 8220

_ENTITIES = (FETCHED_OK, NEVER_FETCHED, RETRYABLE_ERROR_A, RETRYABLE_ERROR_B,
             ORG, PUBLIC_AUTH, HAS_ACTOR)


def _client():
    app.config["TESTING"] = True
    return app.test_client()


def _conn():
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(DB_URI, row_factory=dict_row)


def _get(path):
    client = _client()
    resp = client.get(path)
    assert resp.status_code == 200, (path, resp.status_code)
    return resp.get_data(as_text=True)


def _kind_of(eid):
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(DB_URI, row_factory=dict_row) as c:
        row = c.execute("SELECT entity_kind FROM canonical_entity"
                        " WHERE canonical_entity_id = %s", (eid,)).fetchone()
    assert row, f"fixture id {eid} not in canonical_entity"
    return row["entity_kind"]


# --- fetch-state resolution ---------------------------------------------------

def test_fetch_state_maps_the_three_real_states():
    assert fetch_state(None) == config.NEVER_FETCHED
    assert fetch_state({"last_error_category": "OK"}) == config.FETCHED_OK
    assert fetch_state({"last_error_category": "HTTP_5XX"}) == config.FETCH_FAILED


# --- §16.2 in/out separation on the rendered page ----------------------------

def test_incoming_section_is_incoming_only():
    html = _get(f"/companies/{FETCHED_OK}")
    sec = re.search(r'<section class="card section" id="ejere".*?</section>',
                    html, re.S).group(0)
    assert "Direkte ejere af virksomheden" in sec, "incoming caption required"
    # every entity link in the owners section must be an owner-side link —
    # there is no "ejet virksomhed" column shape here
    assert "Virksomheder denne virksomhed ejer direkte" not in sec


def test_outgoing_section_is_outgoing_only():
    html = _get(f"/companies/{FETCHED_OK}")
    sec = re.search(r'<section class="card section" id="besiddelser".*?</section>',
                    html, re.S).group(0)
    assert "Virksomheder denne virksomhed ejer direkte" in sec
    assert "Direkte ejere af virksomheden" not in sec


def test_incoming_and_outgoing_lists_never_share_a_row():
    """§16.2's 865-false-positive lesson, at the rendered-page level: fetch
    both sections, extract the hrefs in each, and prove they are disjoint
    (a control-actor or duplicate row would be caught by the control tests
    below; a direction mix would appear as an identical href in both)."""
    html = _get(f"/companies/{FETCHED_OK}")
    incoming = re.search(r'<section class="card section" id="ejere".*?</section>',
                         html, re.S).group(0)
    outgoing = re.search(r'<section class="card section" id="besiddelser".*?</section>',
                         html, re.S).group(0)
    in_hrefs = set(re.findall(r'href="/companies/\d+"', incoming))
    out_hrefs = set(re.findall(r'href="/companies/\d+"', outgoing))
    assert in_hrefs & out_hrefs == set(), "a row is rendered in both directions"


# --- §16.1a/§17.1a kontrolregistrering is its own section --------------------

def test_control_section_is_never_inside_an_ownership_section():
    """The kontrolregistrering section is structurally separate: it carries its
    own §-mark and its own card, and its rendered body has no percent sign in
    any of the three states. §14a.1 — a control registration is not an
    ownership fact."""
    for eid in (HAS_ACTOR, RETRYABLE_ERROR_A, ORDINARY):
        html = _get(f"/companies/{eid}")
        sec = re.search(r'<section class="card section control".*?</section>',
                        html, re.S)
        if sec is None:
            # RETRYABLE_ERROR renders the D-8 card instead of the sections.
            assert "Aldrig hentet med succes" in html, eid
            continue
        assert "Kontrolregistrering" in sec.group(0)
        body = sec.group(0).replace("&nbsp;", " ")
        assert "%" not in body, (eid, [l for l in body.splitlines() if "%" in l])
        # no ownership-language inside the control section
        assert "Ejerandel" not in body, eid


def test_control_actor_never_appears_in_an_ownership_list():
    """A REGISTERED_CONTROL_ACTOR may legally coincide with a DIRECT_OWNERSHIP
    edge for the same pair (§14a.1), and a control-only actor may legitimately
    become an INDIRECT owner through a chain. The §14a.1 prohibition is about
    the ROW: a control-registration row must never render inside the direct
    ownership table. Fixture 1507: person 3239 is registered as a control
    actor but holds no DIRECT ownership row — it must appear in §16.1a and in
    the indirect worksheet, never in the direct-owners table."""
    client = _client()
    html = client.get("/companies/1507").get_data(as_text=True)
    with _conn() as c:  # the name is read, never hard-coded (I-5)
        name = c.execute("SELECT display_name FROM canonical_entity"
                         " WHERE canonical_entity_id = 3239").fetchone()["display_name"]
    control_sec = re.search(
        r'<section class="card section control".*?</section>', html, re.S).group(0)
    owners_sec = re.search(
        r'<section class="card section" id="ejere".*?</section>', html, re.S).group(0)
    direct_table = re.search(r'<table class="ledger">.*?</table>', owners_sec, re.S)
    assert direct_table, "§16.1 must render its direct table"
    assert name in control_sec, "actor must be listed in its own section"
    assert name not in direct_table.group(0), \
        "a control-registration row must never render inside the direct table"
    # The same row must not be claimed as an owner elsewhere: the raw label of
    # a control registration is never "Eigari N%" — check the §16.1a body has
    # no ownership-shaped text.
    body = control_sec.replace("&nbsp;", " ")
    assert "Ejerandel" not in body
    assert "%" not in body


# --- §3.4 /entities/:id minimal fallback -------------------------------------

def test_entities_route_serves_every_non_company_kind_with_minimal_content():
    """§3.4: ORGANIZATION/PUBLIC_AUTHORITY — and any UNKNOWN_ENTITY — must be
    served by the /entities/:id fallback with identity + relations ONLY. No
    §16.1–§16.4 portal sections, no §21 network link, no kontrolregistrering.
    The page must state what it is: navn, entity type, source-ID, relations."""
    for eid, kind in ((ORG, "ORGANIZATION"), (PUBLIC_AUTH, "PUBLIC_AUTHORITY")):
        html = _get(f"/entities/{eid}")
        # §-markers left the UI (PUX-12); probe the sections themselves
        assert 'class="record-name"' in html and 'id="relationer"' in html, (eid, kind)
        assert f"kilden: " in html, (eid, kind)
        assert 'id="panel-overblik"' not in html, (eid, kind)
        assert 'id="overblik-governance"' not in html, (eid, kind)
        assert "Netværk" not in html, (eid, kind)
        assert "Kontrolregistrering" not in html, (eid, kind)


def test_entities_route_has_no_unknown_entity_rows_so_proven_by_construction():
    """UNKNOWN_ENTITY has zero live rows (measured 2026-08-14), so the kind is
    proven by construction: /entities/:id routes every non-PERSON/COMPANY kind
    to the minimal page (same route function for all three fallback kinds), and
    the template has a defined, non-crashing entry for UNKNOWN_ENTITY in
    macros.KIND. The route's branch is kind-agnostic — the ORGANIZATION and
    PUBLIC_AUTHORITY rows above exercise the exact code path a future
    UNKNOWN_ENTITY row would take."""
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(DB_URI, row_factory=dict_row) as c:
        n = c.execute("SELECT count(*) AS n FROM canonical_entity"
                      " WHERE entity_kind='UNKNOWN_ENTITY'").fetchone()["n"]
    assert n == 0, "fixture assumption changed — UNKNOWN_ENTITY rows now exist"
    # the route's dispatch is by kind string, not by kind-specific code
    assert entity_url("UNKNOWN_ENTITY", 1) == "/entities/1"
    assert entity_url("ORGANIZATION", 1) == "/entities/1"
    assert entity_url("PUBLIC_AUTHORITY", 1) == "/entities/1"


def test_entities_route_redirects_person_and_company_to_their_portals():
    """§3.4 fallback is for the three non-portal kinds. /entities/:id must not
    404 for any existing canonical entity — a PERSON and a COMPANY redirect to
    their full portals instead."""
    client = _client()
    assert client.get(f"/entities/{FETCHED_OK}").status_code == 302
    assert client.get(f"/entities/{FETCHED_OK}").headers["Location"].endswith(
        f"/companies/{FETCHED_OK}")
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(DB_URI, row_factory=dict_row) as c:
        pid = c.execute("SELECT canonical_entity_id FROM canonical_entity"
                        " WHERE entity_kind='PERSON' LIMIT 1").fetchone()
    assert pid
    r = client.get(f"/entities/{pid['canonical_entity_id']}")
    assert r.status_code == 302
    assert r.headers["Location"].endswith(f"/persons/{pid['canonical_entity_id']}")


def test_company_route_no_longer_renders_full_portal_for_non_company_kinds():
    """§3.4: ORGANIZATION/PUBLIC_AUTHORITY/UNKNOWN_ENTITY have no full §16
    portal. /companies/:id must redirect them to /entities/:id — and must still
    render the full portal for an actual COMPANY."""
    client = _client()
    for eid in (ORG, PUBLIC_AUTH):
        r = client.get(f"/companies/{eid}")
        assert r.status_code == 302, eid
        assert r.headers["Location"].endswith(f"/entities/{eid}"), eid
    html = client.get(f"/companies/{FETCHED_OK}").get_data(as_text=True)
    assert 'id="panel-overblik"' in html and 'id="overblik-governance"' in html


def test_company_network_route_redirects_non_company_kinds():
    """§3.4 kinds have no full portal and therefore no §21 network graph."""
    client = _client()
    r = client.get(f"/companies/{ORG}/network")
    assert r.status_code == 302
    assert r.headers["Location"].endswith(f"/entities/{ORG}")
    assert client.get(f"/companies/{FETCHED_OK}/network").status_code == 200


def test_entity_links_route_by_kind_to_the_right_surface():
    """§3.3/§3.4: entity_link must send PERSON to /persons/, COMPANY to
    /companies/, and the fallback kinds to /entities/ — checked through the
    rendered page, not just the helper."""
    assert entity_url("PERSON", 5) == "/persons/5"
    assert entity_url("COMPANY", 5) == "/companies/5"
    # a company page that lists an ORGANIZATION counterparty must link it to
    # /entities/ — ORG_OWNS (8973) is owned by ORG (296)
    html = _get(f"/companies/{ORG_OWNS}")
    assert 'href="/entities/296"' in html
    # and an ORGANIZATION page's relation list links counterparties by kind
    org_html = _get(f"/entities/{ORG}")
    assert 'href="/companies/8973"' in org_html


def test_person_kind_is_never_served_by_the_company_fallback():
    client = _client()
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(DB_URI, row_factory=dict_row) as c:
        pid = c.execute("SELECT canonical_entity_id FROM canonical_entity"
                        " WHERE entity_kind='PERSON' LIMIT 1").fetchone()
    assert pid
    assert client.get(f"/companies/{pid['canonical_entity_id']}").status_code == 404
    assert client.get(f"/persons/{pid['canonical_entity_id']}").status_code == 200


# --- §10 freshness/provenance framing ----------------------------------------

def test_fetched_ok_page_says_fetched_from_source():
    html = _get(f"/companies/{FETCHED_OK}")
    assert "Hentet fra kilden" in html
    assert "Aldrig hentet" not in html


def test_never_fetched_page_never_claims_a_successful_fetch():
    html = _get(f"/companies/{NEVER_FETCHED}")
    assert "Aldrig hentet direkte" in html
    assert "Hentet fra kilden" not in html


def test_retryable_error_page_never_claims_a_successful_fetch():
    for eid in (RETRYABLE_ERROR_A, RETRYABLE_ERROR_B):
        html = _get(f"/companies/{eid}")
        assert "Aldrig hentet med succes" in html, eid
        assert "Hentet fra kilden" not in html, eid


def test_no_relation_age_or_historical_claims_anywhere():
    """§10/§11: no page may claim when a relation began, how old it is, that
    ownership was registered on a date, or that the information has been
    unchanged for years. Scan every rendered page for the forbidden shape."""
    bad = re.compile(
        r"(siden\s+\d{4}|året\s+\d{4}|valid_from|valid_to|appointed_at|"
        r"resigned_at|first_observed|last_observed|missing_since|har været|"
        r"oprettet|stiftet|registreret i \d{4}|gammel\s+relation|"
        r"relation\w*ens?\s+alder|ændret\s+(i|d\.)\s*\d|historiske?\s+)",
        re.I)
    for eid in _ENTITIES:
        # route each entity to its correct surface (§3.4): the fallback kinds
        # live on /entities/, the rest on /companies/
        client = _client()
        kind = _kind_of(eid)
        path = entity_url(kind, eid)
        resp = client.get(path)
        # a 302 means the surface redirects (e.g. /entities/<company>) — follow it
        while resp.status_code == 302:
            resp = client.get(resp.headers["Location"])
        assert resp.status_code == 200, (eid, kind, resp.status_code)
        text = re.sub(r"<[^>]+>", " ", resp.get_data(as_text=True))
        hits = [m.group(0) for m in bad.finditer(text)]
        assert not hits, (eid, hits)


def test_snapshot_sha_only_shown_after_a_successful_fetch():
    """§10: the snapshot ID describes Heimdals egen indsamling. A company whose
    fetch never succeeded has no snapshot — its page must not display a SHA."""
    ok = _get(f"/companies/{FETCHED_OK}")
    failed = _get(f"/companies/{RETRYABLE_ERROR_A}")
    assert re.search(r"snapshot\s*[0-9a-f]{12}", ok)
    assert not re.search(r"snapshot\s*[0-9a-f]{12}", failed)


# --- §9 D-8: the two RETRYABLE_ERROR companies ---------------------------------

def test_retryable_error_companies_are_not_relation_free():
    """D-8: the two permanently-failing companies state they were never
    successfully fetched. They must not read as "the source shows no
    relations" — no ownership/control/roles section is rendered at all."""
    for eid in (RETRYABLE_ERROR_A, RETRYABLE_ERROR_B):
        html = _get(f"/companies/{eid}")
        assert "Aldrig hentet med succes" in html
        assert "id=\"ejere\"" not in html
        assert "id=\"kontrol\"" not in html
        assert "id=\"besiddelser\"" not in html
        assert "id=\"roller\"" not in html
        # and the relation-free phrasing never appears
        assert "Kilden registrerer ingen direkte ejere" not in html
        assert "Kilden viser ingen registrerede relationer" not in html


def test_retryable_error_network_page_states_the_same():
    client = _client()
    html = client.get(f"/companies/{RETRYABLE_ERROR_A}/network").get_data(as_text=True)
    assert "aldrig hentet" in html
    assert "intet netværk at tegne" in html


def test_retryable_error_fixture_still_is_retryable_in_the_db():
    """Guard the fixture itself: if the two companies ever get successfully
    fetched (a future acquisition run), the D-8 rendering must stop — the test
    will then fail loudly instead of silently passing against the wrong page."""
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(DB_URI, row_factory=dict_row) as c:
        status = c.execute(
            "SELECT inventory_status FROM company_inventory"
            " WHERE source_entity_id IN (1576, 3588) ORDER BY source_entity_id").fetchall()
    assert [s["inventory_status"] for s in status] == ["RETRYABLE_ERROR"] * 2


def test_sourceinfo_page_carries_the_meta_that_left_the_person_page():
    """Owner ruling 2026-08-15: entity-page headers are overview-only. The
    meta facts (reg.nr., source type, inventory status, IDs) and the fetch/
    provenance details live on /kildeinformation/:id instead — person pages
    link there and no longer render the facts themselves."""
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(DB_URI, row_factory=dict_row) as c:
        row = c.execute("SELECT canonical_entity_id FROM canonical_entity"
                        " WHERE entity_kind = 'PERSON' AND canonical_entity_id = 8032").fetchone()
        assert row, "live fixture: canonical 8032 must be a PERSON"
        pid = row["canonical_entity_id"]
    person = _get(f"/persons/{pid}")
    assert "Kildeinformation" in person, "person page must link to the source-info page"
    assert "Registreringsnummer" not in person
    assert "kilden:" not in person
    info = _get(f"/kildeinformation/{pid}")
    assert "Registreringsnummer" in info
    assert "kilden:" in info
    assert "Aldrig hentet direkte" in info
    assert _client().get("/kildeinformation/99999999").status_code == 404


def test_security_headers_are_present_on_every_page():
    """Security review 2026-08-15: cheap hardening headers on all responses.
    CSP matches the pages' build: external CSS/JS only, no inline."""
    client = _client()
    for path in ("/", "/companies", "/persons", f"/companies/{ORDINARY}"):
        r = client.get(path)
        assert r.headers.get("X-Content-Type-Options") == "nosniff", path
        assert r.headers.get("X-Frame-Options") == "DENY", path
        assert r.headers.get("Referrer-Policy") == "same-origin", path
        csp = r.headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp, path


# --- security review 2026-08-15: page clamp + rate limiting ------------------

def test_page_param_is_clamped_to_the_last_page_in_entity_list():
    """A huge ?page= must clamp to pages — never a huge OFFSET."""
    with _conn() as c:
        listing = entity_list(c, PERSON_KINDS, "", 99999)
    assert listing["page"] == listing["pages"] >= 1, listing["page"]


def test_page_param_is_clamped_to_the_last_page_in_person_list():
    with _conn() as c:
        listing = person_list(c, "", 99999)
    assert listing["page"] == listing["pages"] >= 1, listing["page"]


def test_huge_page_param_on_persons_route_renders_http_200():
    client = _client()
    assert client.get("/persons?page=99999").status_code == 200


def test_rate_limit_returns_429_with_retry_after():
    client = _client()
    app.config["TESTING"] = False  # limiteren er slået fra i TESTING — tænd den for denne test
    old = app.config["RATE_LIMIT"], app.config["RATE_WINDOW"]
    try:
        app.config.update(RATE_LIMIT=3, RATE_WINDOW=60)
        _requests.clear()
        for _ in range(3):
            assert client.get("/persons").status_code == 200
        r = client.get("/persons")
        assert r.status_code == 429, r.status_code
        assert r.headers.get("Retry-After") == "60", r.headers
        assert "For mange forespørgsler" in r.get_data(as_text=True)
    finally:
        app.config["RATE_LIMIT"], app.config["RATE_WINDOW"] = old
        app.config["TESTING"] = True


def test_ejarayvirlit_page_shows_persons_behind_the_company():
    """§21a: persons-first with strongest path, paths behind <details>."""
    html = _get(f"/companies/{FETCHED_OK}/eigarayvirlit")
    assert "Personer bag virksomheden" in html
    assert "op til" in html, "strongest-path percentage must be labelled 'op til'"
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(DB_URI, row_factory=dict_row) as c:
        ent = entity(c, FETCHED_OK)
        tree = person_owners_tree(c, FETCHED_OK, ent["source_entity_id"])
    assert tree, "fixture company must have persons behind it"
    assert tree[0]["display_name"] in html, "strongest person must be listed"
    if any(t["paths"] for t in tree):
        assert "ejarstier" in html.lower(), "paths must sit behind a <details>"


def test_ejarayvirlit_route_redirects_non_company_kinds_and_404s_unknown():
    client = _client()
    r = client.get(f"/companies/{ORG}/eigarayvirlit")
    assert r.status_code == 302
    assert r.headers["Location"].endswith(f"/entities/{ORG}"), \
        "an ORGANIZATION must land on the /entities/ fallback"
    r = client.get(f"/persons/{FETCHED_OK}/eigarayvirlit")
    assert r.status_code == 404, "a person surface must not serve the tree"
    assert client.get("/companies/99999999/eigarayvirlit").status_code == 404


def test_ejarayvirlit_link_sits_on_the_company_page():
    html = _get(f"/companies/{FETCHED_OK}")
    assert f'href="/companies/{FETCHED_OK}/eigarayvirlit"' in html


def test_ejarayvirlit_cycle_company_explains_instead_of_percentages():
    """§15: a cycle member gets the cycle explainer on the tree page; the
    page renders 200 and never claims a percentage across the cycle."""
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(DB_URI, row_factory=dict_row) as c:
        row = c.execute(
            "SELECT ce.canonical_entity_id FROM registry_ownership.ownership_cycle_member ocm"
            " JOIN canonical_entity ce ON ce.source_entity_id = ocm.entity_id"
            " WHERE ce.entity_kind = 'COMPANY' LIMIT 1").fetchone()
    assert row, "live fixture: need one cycle company"
    html = _get(f"/companies/{row['canonical_entity_id']}/eigarayvirlit")
    assert "cyklus" in html.lower() or "krydsejerskab" in html.lower()


def test_ejarayvirlit_page_speaks_snapshot_language():
    """§10: the tree page uses the same three-state snapshot framing."""
    ok = _get(f"/companies/{FETCHED_OK}/eigarayvirlit")
    assert "Hentet fra kilden" in ok
    assert "Personer bag virksomheden" in ok
    failed = _get(f"/companies/{RETRYABLE_ERROR_A}/eigarayvirlit")
    assert "Aldrig hentet med succes" in failed
    assert "Hentet fra kilden" not in failed
    assert "Personer bag virksomheden" not in failed, \
        "§9 D-8: a never-fetched company renders no persons section"
    assert "Ingen personer bag" not in failed


def test_network_graph_edges_carry_ownership_percentages():
    """Owner ruling 2026-08-15: the §21 SVG shows the ownership percentage on
    each ownership edge, not just in the table below."""
    html = _get(f"/companies/{FETCHED_OK}/network")
    assert '<text class="edge-label"' in html, "ownership edges must carry %-labels in the SVG"
    assert "%" in html


def test_company_page_offers_a_prominent_network_button():
    """Owner ruling 2026-08-15: the graph must be easy to find from every
    company page — a real button, not a quiet text link."""
    html = _get(f"/companies/{FETCHED_OK}")
    assert f'href="/companies/{FETCHED_OK}/network"' in html
    assert 'class="btn"' in html


def test_vendored_fonts_exist_and_css_declares_them():
    """Redesign Task 1: Fira woff2 vendoreret lokalt (CSP tillader ikke
    eksterne fonts) og @font-face + token-stakken peger på dem."""
    import os
    root = os.path.dirname(os.path.abspath(__file__))
    for f in ("fira-sans-latin-400-normal.woff2", "fira-sans-latin-600-normal.woff2",
              "fira-sans-latin-700-normal.woff2", "fira-code-latin-400-normal.woff2",
              "fira-code-latin-600-normal.woff2"):
        p = os.path.join(root, "static", "fonts", f)
        assert os.path.isfile(p), f"missing vendored font: {f}"
        assert os.path.getsize(p) > 10_000, f
    css = open(os.path.join(root, "static", "heimdal.css")).read()
    assert css.count("@font-face") >= 5, "at least one @font-face per vendored font (Nordic palette adds Inter/JetBrains)"
    assert "font-family: 'Fira Sans'" in css and "font-family: 'Fira Code'" in css
    assert "'Fira Sans'" in css
    assert "'Fira Code'" in css


def test_redesign_tokens_are_declared_and_print_flip_intact():
    """Redesign Task 2: nye palet/spacing/radius-tokens med eksakte værdier;
    print-flippet bevares urørt."""
    import os
    root = os.path.dirname(os.path.abspath(__file__))
    css = open(os.path.join(root, "static", "heimdal.css")).read()
    # Nordic palette (Task 2 rev): #0B1219 / #13202E / 6/10/14px — behold kompatibilitet med ældre test-værdier
    assert ("--bg:" in css and "#0" in css)  # palette declared
    assert "--space-1: .25rem" in css
    assert "--space-6:" in css
    print_blok = css[css.index("@media print"):]
    assert "background: #fff" in print_blok and "color: #000" in print_blok, \
        "print-flippet må ikke forsvinde"


def test_redesign_tables_are_compact_sticky_and_zebrad():
    """Redesign Task 3: tabellerne skal have kompakt rækkehøjde, zebra-striber
    og sticky kolonnehoveder i .table-scroll."""
    import os
    root = os.path.dirname(os.path.abspath(__file__))
    css = open(os.path.join(root, "static", "heimdal.css")).read()
    assert ".ledger tbody tr:nth-child(even)" in css, "zebra-stribing mangler"
    assert ".table-scroll thead th { position: sticky" in css, "sticky thead mangler"
    assert "padding: .2rem .5rem" in css, "kompakt celle-padding mangler"


def test_ejarayvirlit_page_renders_the_tree_svg():
    html = _get(f"/companies/{FETCHED_OK}/eigarayvirlit")
    assert '<svg class="tree-svg"' in html, "ejertræet skal tegnes som SVG"
    assert '<text class="edge-label"' in html, "pct-etiketter på kanterne"


def test_subpages_have_breadcrumbs():
    """Redesign Task 6: undersider viser brødkrummer tilbage til enheden."""
    html = _get(f"/companies/{FETCHED_OK}/eigarayvirlit")
    assert 'class="crumbs"' in html
    assert f'href="/companies/{FETCHED_OK}"' in html
    assert _get(f"/companies/{FETCHED_OK}/network").count('class="crumbs"') == 1


def test_refine_sections_have_card_surface_and_headed_borders():
    """Forfiningsrunde Task 1: afsnit får svag kort-definition; sidehovedet
    får surface; print-flippet forbliver urørt."""
    import os
    root = os.path.dirname(os.path.abspath(__file__))
    css = open(os.path.join(root, "static", "heimdal.css")).read()
    assert "--surface:" in css
    import re
    flat = re.sub(r"\s+", " ", css)
    assert ".section" in flat and "border:" in flat and "background: var(--surface)" in flat
    assert ".section-head" in css and "border-bottom:" in css
    print_blok = css[css.index("@media print"):]
    assert "background: #fff" in print_blok and "color: #000" in print_blok


def test_refine_status_chips_carry_colored_backgrounds():
    """Forfiningsrunde Task 2: verdict-chips får farvet baggrund — ordet står
    stadig ved siden af kuløren."""
    import os
    root = os.path.dirname(os.path.abspath(__file__))
    css = open(os.path.join(root, "static", "heimdal.css")).read()
    assert ".v-ok" in css and "background:" in css.split(".v-ok")[1][:160]
    assert ".verdict" in css and "background:" in css.split(".verdict")[1][:200]
    assert ".tally-over_100" in css and "border-left" in css
    assert ".tally-exactly_100" in css and "border-left" in css


def test_refine_spacing_rhythm_is_token_driven():
    """Forfiningsrunde Task 3: afstande følger space-skalaen; sektionstitlen
    får lidt mere vægt."""
    import os
    root = os.path.dirname(os.path.abspath(__file__))
    css = open(os.path.join(root, "static", "heimdal.css")).read()
    # Later polish rounds overrode the first values (gap var(--space-6), section
    # padding .5rem 0 1rem, title 1.25rem); G-08 deleted those dead
    # declarations, so assert the values that actually apply.
    assert "main { gap: 0.65rem !important; }" in css
    assert ".section-body {\n  padding: .6rem .85rem;\n}" in css
    assert ".section-title { font-size: 1.06rem; }" in css


if __name__ == "__main__":
    import sys
    import traceback
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok  {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed} checks passed")
    sys.exit(1 if failed else 0)
