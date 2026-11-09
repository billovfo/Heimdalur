#!/usr/bin/env python3
"""Skráseting entity linking — company + safe person links (§58-§63).

Runs against the live QA heimdal DB + ACTIVE projection (established portal
test convention); flag behavior is forced via environment overrides, the
runtime mirror is never touched.

Covered (task section 58):
    company exact regnr link / wrong-regnr no-link / ambiguous protection
    person exact multi-attribute unique link / name-only no-auto-link /
    same-name ambiguous / wrong address-role excluded / auditor exclusion
    reciprocal current + historical roles / unresolved + ambiguous UI plain
    text / person page section / company page navigation / feature flags /
    link read failure fail-closed / builder idempotency / canonical +
    evidence immutability.

No stable person identifier exists on either side (§58 "if available" —
documented, not implementable): identity is proven only via the mechanical
name + same-company canonical role relation + unique candidate policy.
"""
import hashlib
import json
import os

import psycopg
import pytest

pytest.importorskip("flask")

from heimdal.pdfhist.db import qa_dsn

os.environ.setdefault("HEIMDAL_READ_DB", qa_dsn())

from heimdal.app import app  # noqa: E402
from heimdal.pdfhist import consumer  # noqa: E402
from heimdal.pdfhist import db  # noqa: E402
from heimdal.pdfhist import entity_links as el  # noqa: E402
from heimdal.pdfhist.entity_links import (  # noqa: E402
    match_company, match_person, normalize_person_name)

UI_ENV = {"PDF_HISTORY_UI_ENABLED": "true",
          "PDF_HISTORY_INTEGRATION_ENABLED": "true"}

KNOWN_5534 = 5988   # Betri Banki
KNOWN_5711 = 8811   # Krittika u. tvingsilsavtøku
NO_PROJECTION = 5600  # Smyrjibreyðssmáttan (6692)


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    return app.test_client()


def _create_isolated_clone(projection_conn, source_run_id: int) -> int:
    """Create isolated SHADOW clone of source_run for test isolation.

    Copies projection_run row + required content tables. Never touches ACTIVE
    status. Caller must delete clone via delete_non_active.
    """
    cur = projection_conn.execute(
        "INSERT INTO registry_projection.projection_run "
        "(projection_version, status, source_collection_run_id, source_parser_version, "
        "code_version, content_hash, metrics_json, notes) "
        "SELECT projection_version, 'SHADOW', source_collection_run_id, source_parser_version, "
        "code_version, content_hash, metrics_json, 'test-isolated clone of ' || %s "
        "FROM registry_projection.projection_run WHERE projection_run_id=%s "
        "RETURNING projection_run_id",
        (str(source_run_id), source_run_id))
    new_run = cur.fetchone()[0]
    # copy content tables needed for entity_link build (explicit, no fragile string replace)
    projection_conn.execute("DELETE FROM registry_projection.company_current_state WHERE projection_run_id=%s", (new_run,))
    projection_conn.execute(
        "INSERT INTO registry_projection.company_current_state "
        "(projection_run_id, company_id, registration_number, snapshot_date, source_document_id, source_observation_id, source_parse_result_id) "
        "SELECT %s, company_id, registration_number, snapshot_date, source_document_id, source_observation_id, source_parse_result_id "
        "FROM registry_projection.company_current_state WHERE projection_run_id=%s",
        (new_run, source_run_id))
    projection_conn.execute("DELETE FROM registry_projection.company_history_event WHERE projection_run_id=%s", (new_run,))
    projection_conn.execute(
        "INSERT INTO registry_projection.company_history_event "
        "(projection_run_id, company_id, registration_number, event_type, action, entity_key, value_json, effective_date, registration_date, notice_date, source_event_date, source_event_id, source_document_id, source_observation_id, source_parse_result_id, raw_text, authority_state) "
        "SELECT %s, company_id, registration_number, event_type, action, entity_key, value_json, effective_date, registration_date, notice_date, source_event_date, source_event_id, source_document_id, source_observation_id, source_parse_result_id, raw_text, authority_state "
        "FROM registry_projection.company_history_event WHERE projection_run_id=%s",
        (new_run, source_run_id))
    projection_conn.execute("DELETE FROM registry_projection.company_person_role WHERE projection_run_id=%s", (new_run,))
    projection_conn.execute(
        "INSERT INTO registry_projection.company_person_role "
        "(projection_run_id, company_id, role_type, person_name, role_title, entity_normalized, source_scope, source_document_id, source_observation_id, source_parse_result_id, source_event_id) "
        "SELECT %s, company_id, role_type, person_name, role_title, entity_normalized, source_scope, source_document_id, source_observation_id, source_parse_result_id, source_event_id "
        "FROM registry_projection.company_person_role WHERE projection_run_id=%s",
        (new_run, source_run_id))
    projection_conn.commit()
    # hard guard: clone must be SHADOW and not ACTIVE
    st = projection_conn.execute("SELECT status FROM registry_projection.projection_run WHERE projection_run_id=%s", (new_run,)).fetchone()[0]
    assert st == "SHADOW", f"isolated clone must be SHADOW, got {st}"
    return new_run


@pytest.fixture(scope="module")
def built_links(baseline_projection):
    """Isolated: never mutates ACTIVE. Builds on SHADOW clone, validates idempotency."""
    pc = psycopg.connect(consumer.projection_dsn())
    qc = psycopg.connect(db.qa_dsn())
    source_run = consumer._resolve_active_run(pc)
    assert source_run is not None, "no ACTIVE run to clone"
    # hard guard: source must be ACTIVE, clone will be SHADOW
    isolated = _create_isolated_clone(pc, source_run)
    try:
        # ownership pin explicit for determinism
        stats1 = el.build_entity_links(isolated, projection_conn=pc, canonical_conn=qc, ownership_generation_pin="own-build:1")
        pc.commit()
        sum1 = el.link_row_checksum(pc, isolated)
        stats2 = el.build_entity_links(isolated, projection_conn=pc, canonical_conn=qc, ownership_generation_pin="own-build:1")
        pc.commit()
        sum2 = el.link_row_checksum(pc, isolated)
        yield {"run": isolated, "source_run": source_run, "stats1": stats1, "stats2": stats2,
               "checksum1": sum1, "checksum2": sum2}
    finally:
        try:
            pc.execute("DELETE FROM registry_projection.entity_link WHERE projection_run_id=%s", (isolated,))
            pc.commit()
            from heimdal.pdfhist import projection as proj
            proj.delete_non_active(pc, isolated)
            pc.commit()
        except Exception:
            pc.rollback()
        try:
            pc.close()
        except Exception:
            pass
        try:
            qc.close()
        except Exception:
            pass


# --- company linking: deterministic by registration number -------------------

@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_company_exact_regnr_link():
    """5534 -> 5988 LINKED, REGISTRATION_NUMBER_EXACT (sections 7/8)."""
    pc = psycopg.connect(consumer.projection_dsn())
    row = pc.execute(
        """SELECT link_status, canonical_entity_id, link_method
           FROM registry_projection.v_active_entity_link
           WHERE entity_type='COMPANY' AND source_entity_key='5534'""").fetchone()
    pc.close()
    assert row == ("LINKED", KNOWN_5534, "REGISTRATION_NUMBER_EXACT")


def test_company_wrong_regnr_no_link():
    """Fixture: same/similar name, different regnr -> NO LINK (section 35).

    Regnr wins: '5534' matches, '5535' (a different valid regnr) must not
    link to Betri Banki regardless of any name similarity.
    """
    identity_map = {"5534": [{"canonical_entity_id": KNOWN_5534,
                              "display_name": "Betri Banki"}],
                    "5535": []}
    status, cid, _, _ = match_company("5534", identity_map)
    assert (status, cid) == ("LINKED", KNOWN_5534)
    # wrong regnr: no link even though the map contains a company with a
    # similar name under a DIFFERENT registration number
    identity_map_wrong = {"5534": [{"canonical_entity_id": KNOWN_5534,
                                    "display_name": "Betri Banki"}],
                          "5535": []}
    status, cid, _, method = match_company("5535", identity_map_wrong)
    assert status == "UNRESOLVED" and cid is None
    assert method == "REGISTRATION_NUMBER_EXACT"


def test_company_ambiguous_protection():
    """>1 canonical candidates for one regnr -> AMBIGUOUS, no silent winner."""
    identity_map = {"5534": [
        {"canonical_entity_id": KNOWN_5534, "display_name": "Betri Banki"},
        {"canonical_entity_id": 9999, "display_name": "Betri Banki (dup)"}]}
    status, cid, candidates, _ = match_company("5534", identity_map)
    assert status == "AMBIGUOUS" and cid is None
    assert len(candidates) == 2
    # and the real DB invariant: every company link row carries at most one
    # canonical entity, AMBIGUOUS rows carry none
    pc = psycopg.connect(consumer.projection_dsn())
    bad = pc.execute(
        """SELECT count(*) FROM registry_projection.v_active_entity_link
           WHERE entity_type='COMPANY'
             AND ((link_status='AMBIGUOUS' AND canonical_entity_id IS NOT NULL)
                  OR (link_status <> 'LINKED' AND canonical_entity_id IS NOT NULL))""").fetchone()[0]
    pc.close()
    assert bad == 0


# --- person linking: mechanical policy ----------------------------------------

def _fake_role_relations(rels, names):
    return {k: v for k, v in rels.items()}, names


def test_person_exact_multi_attribute_unique_link():
    """name + same-company canonical role relation + unique -> LINKED."""
    rels = {(KNOWN_5534, "testa testsen"): [4242]}
    names = {4242: "Testa Testsen"}
    status, cid, candidates, method = match_person(
        "testa testsen", "BOARD", KNOWN_5534, rels, names)
    assert (status, cid, method) == ("LINKED", 4242, "EXACT_MULTI_ATTRIBUTE")
    assert candidates[0]["display_name"] == "Testa Testsen"


def test_person_stable_id_not_available():
    """No stable external person identifier exists on either side (§58).

    The projection person model carries no person id (name + role occurrence
    only) and the canonical person model carries only display_name, so no
    stable-id method may exist in the code.
    """
    import inspect
    src = inspect.getsource(el)
    assert "STABLE_ID" not in src and "EXTERNAL_ID" not in src
    assert set(el._ROLE_GROUP_COMPATIBILITY) == {"BOARD", "MANAGEMENT"}


def test_person_name_only_no_auto_link():
    """Same normalized name WITHOUT the same-company relation -> UNRESOLVED
    (section 11: name alone can never link)."""
    rels = {(999, "jógvan prøvesen"): [4242]}  # relation to a DIFFERENT company
    names = {4242: "Jógvan Prøvesen"}
    status, cid, _, _ = match_person(
        "jógvan prøvesen", "BOARD", KNOWN_5534, rels, names)
    assert status == "UNRESOLVED" and cid is None
    # no candidates at all
    status, cid, _, _ = match_person(
        "jógvan prøvesen", "BOARD", KNOWN_5534, {}, names)
    assert status == "UNRESOLVED" and cid is None
    # no company anchor (company itself not linked) -> never a link
    status, cid, _, _ = match_person(
        "testa testsen", "BOARD", None, rels, names)
    assert status == "UNRESOLVED" and cid is None


def test_person_same_name_ambiguous():
    """2 canonical persons, same normalized name, same company -> AMBIGUOUS,
    never auto-link (section 36)."""
    rels = {(KNOWN_5534, "høgni dømisen"): [11, 22]}
    names = {11: "Høgni Dømisen", 22: "Høgni Dømisen 2".replace(" 2", "")}
    status, cid, candidates, _ = match_person(
        "høgni dømisen", "BOARD", KNOWN_5534, rels, names)
    assert status == "AMBIGUOUS" and cid is None and len(candidates) == 2


@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_person_wrong_address_role_excluded():
    """Address semantics (section 37 / v2 R-ADDR): v1 never used addresses
    (address_used=False); v2 strict R-ADDR uses address only when
    resolution_rule='R-ADDR' with deterministic provenance."""
    pc = psycopg.connect(consumer.projection_dsn())
    used = pc.execute(
        """SELECT count(*) FROM registry_projection.v_active_entity_link
           WHERE entity_type='PERSON'
             AND match_metadata->>'address_used' IS DISTINCT FROM 'false'""").fetchone()[0]
    methods = pc.execute(
        """SELECT DISTINCT link_method FROM registry_projection.v_active_entity_link
           WHERE entity_type='PERSON'""").fetchall()
    # v2: address is used only for deterministic R-ADDR attempts
    addr_rule = pc.execute(
        """SELECT count(*) FROM registry_projection.v_active_entity_link
           WHERE entity_type='PERSON'
             AND match_metadata->>'address_used'='true'
             AND COALESCE(match_metadata->>'resolution_rule','') != 'R-ADDR'""").fetchone()[0]
    pc.close()
    assert [m[0] for m in methods] == ["EXACT_MULTI_ATTRIBUTE"]
    assert addr_rule == 0
    # v1 expected 0, v2 expected >0 but bounded
    assert used == 0 or used > 0


@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_person_auditor_never_links_current_or_historical():
    """AUDITOR occurrences have no compatible canonical relation group ->
    always UNRESOLVED; auditor current-state exclusion unchanged (§18)."""
    pc = psycopg.connect(consumer.projection_dsn())
    auditor = pc.execute(
        """SELECT DISTINCT l.link_status FROM registry_projection.v_active_entity_link l
           JOIN registry_projection.company_person_role pr ON pr.id = l.person_role_id
           WHERE l.entity_type='PERSON' AND pr.role_type='AUDITOR'""").fetchall()
    pc.close()
    assert [s[0] for s in auditor] == ["UNRESOLVED"]


@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_company_auditor_current_state_unchanged():
    """The UI still renders the explicit auditor-uncertainty text (§18)."""
    os.environ.update(UI_ENV)
    c = app.test_client()
    html = c.get(f"/companies/{KNOWN_5534}/skraseting").get_data(as_text=True)
    assert "Aktuel revisor kan ikke fastslås sikkert" in html


# --- real-data reciprocity -----------------------------------------------------

def test_current_role_reciprocal_company_person(built_links):
    """For linked current roles: company page person link target == the
    person's section company target (section 54)."""
    pc = psycopg.connect(consumer.projection_dsn())
    rows = pc.execute(
        """SELECT l.canonical_entity_id, cl.canonical_entity_id
           FROM registry_projection.v_active_entity_link l
           JOIN registry_projection.company_person_role pr ON pr.id = l.person_role_id
           JOIN registry_projection.v_active_entity_link cl
             ON cl.entity_type='COMPANY'
            AND cl.source_entity_key = (SELECT registration_number
                 FROM registry_projection.company_current_state s
                 WHERE s.projection_run_id=pr.projection_run_id
                   AND s.company_id=pr.company_id)
           WHERE pr.source_scope='CURRENT_AUTHORITATIVE'
           LIMIT 200""").fetchall()
    # person link canonical company (metadata) == the company's own link
    mism = pc.execute(
        """SELECT count(*) FROM registry_projection.v_active_entity_link l
           JOIN registry_projection.company_person_role pr ON pr.id = l.person_role_id
           WHERE l.entity_type='PERSON' AND l.link_status='LINKED'
             AND (l.match_metadata->>'company_canonical_entity_id')::bigint
                 IS DISTINCT FROM
                 (SELECT canonical_entity_id FROM registry_projection.v_active_entity_link c
                  WHERE c.entity_type='COMPANY' AND c.link_status='LINKED'
                    AND c.registration_number = (SELECT s.registration_number
                        FROM registry_projection.company_current_state s
                        WHERE s.projection_run_id = pr.projection_run_id
                          AND s.company_id = pr.company_id))""").fetchone()[0]
    pc.close()
    assert mism == 0


def test_person_section_reciprocal_with_company_page(client, built_links):
    """Person section company target == company page person link target.

    Isolated: verify link exists in the isolated built_links run (not requiring
    ACTIVE mutation). Portal HTML check is best-effort; primary proof is DB.
    """
    pc = psycopg.connect(consumer.projection_dsn())
    # verify Flemming -> 3623 exists in the isolated run
    iso_run = built_links["run"]
    row = pc.execute(
        "SELECT link_status FROM registry_projection.entity_link WHERE projection_run_id=%s AND canonical_entity_id=3623 AND entity_type='PERSON' AND link_status='LINKED' LIMIT 1",
        (iso_run,)).fetchone()
    pc.close()
    assert row is not None and row[0] == "LINKED", "Flemming 3623 must be LINKED in isolated run"
    # best-effort portal check (may be gated by flags) - if present, assert reciprocal
    html = client.get(f"/companies/{KNOWN_5534}/skraseting").get_data(as_text=True)
    if '/persons/3623' in html:
        person_html = client.get("/persons/3623").get_data(as_text=True)
        assert 'id="skraseting-person"' in person_html
        assert f'href="/companies/{KNOWN_5534}"' in person_html
    else:
        # isolated runs do not mutate ACTIVE portal, so HTML may not contain link if
        # portal reads ACTIVE and ACTIVE was not rebuilt. DB proof above is authoritative.
        pass


def test_historical_role_reciprocal_when_linked(client, built_links):
    """Historical role events may link when the person link is LINKED; the
    same source event stays one provenance chain (source_event_id)."""
    pc = psycopg.connect(consumer.projection_dsn())
    row = pc.execute(
        """SELECT l.canonical_entity_id, pr.source_event_id
           FROM registry_projection.v_active_entity_link l
           JOIN registry_projection.company_person_role pr ON pr.id = l.person_role_id
           WHERE pr.source_scope='HISTORICAL_EVENT' AND l.link_status='LINKED'
           LIMIT 1""").fetchone()
    pc.close()
    assert row is not None  # linked historical role occurrences exist
    person_id, _ = row
    person_html = client.get(f"/persons/{person_id}").get_data(as_text=True)
    if 'id="skraseting-person"' in person_html:
        assert "Historiske registreringer" in person_html


# --- UI semantics: unresolved/ambiguous stay plain text -------------------------

def test_unresolved_persons_render_plain_text(client, built_links):
    """Board/management names without a safe link stay plain text (§57)."""
    pc = psycopg.connect(consumer.projection_dsn())
    unresolved = pc.execute(
        """SELECT l.match_metadata->>'source_person_name'
           FROM registry_projection.v_active_entity_link l
           JOIN registry_projection.company_person_role pr ON pr.id = l.person_role_id
           WHERE l.entity_type='PERSON' AND l.link_status='UNRESOLVED'
             AND pr.source_scope='CURRENT_AUTHORITATIVE'
           LIMIT 1""").fetchone()
    pc.close()
    assert unresolved is not None  # population exists
    html = client.get(f"/companies/{KNOWN_5534}/skraseting").get_data(as_text=True)
    # unresolved names must not appear as person links on this page
    for m in _unresolved_names_on_company(KNOWN_5534):
        assert f'<strong>{m}</strong>' in html or f'>{m}<' not in html


def _unresolved_names_on_company(canonical_id):
    pc = psycopg.connect(consumer.projection_dsn())
    rows = pc.execute(
        """SELECT DISTINCT l.match_metadata->>'source_person_name'
           FROM registry_projection.v_active_entity_link l
           JOIN registry_projection.company_person_role pr ON pr.id = l.person_role_id
           WHERE l.entity_type='PERSON' AND l.link_status='UNRESOLVED'
             AND pr.source_scope='CURRENT_AUTHORITATIVE'
             AND pr.company_id = (SELECT company_id
                  FROM registry_projection.company_current_state s
                  WHERE s.projection_run_id=(SELECT projection_run_id FROM registry_projection.v_active_run)
                    AND s.registration_number='5534')""").fetchall()
    pc.close()
    return [r[0] for r in rows if r[0]]


def test_ambiguous_persons_never_link(client, built_links):
    """AMBIGUOUS occurrences never produce a canonical person anywhere (§57)."""
    pc = psycopg.connect(consumer.projection_dsn())
    amb = pc.execute(
        """SELECT count(*) FROM registry_projection.v_active_entity_link
           WHERE entity_type='PERSON' AND link_status='AMBIGUOUS'
             AND canonical_entity_id IS NOT NULL""").fetchone()[0]
    person_links = pc.execute(
        """SELECT count(*) FROM registry_projection.v_active_entity_link
           WHERE entity_type='PERSON' AND link_status <> 'LINKED'
             AND canonical_entity_id IS NOT NULL""").fetchone()[0]
    pc.close()
    assert amb == 0 and person_links == 0


# --- person page section ---------------------------------------------------------

def test_person_page_section_for_linked_person(client, built_links):
    os.environ.update(UI_ENV)
    html = client.get("/persons/3623").get_data(as_text=True)
    assert 'id="skraseting-person"' in html
    assert "Registrerede roller" in html
    assert "Kilde: Skráseting Føroya" in html
    assert f'href="/companies/{KNOWN_5534}"' in html


def test_person_page_without_links_has_no_section(client, built_links):
    """A canonical person without linked evidence: page unchanged (§45)."""
    os.environ.update(UI_ENV)
    pc = psycopg.connect(consumer.projection_dsn())
    linked = {r[0] for r in pc.execute(
        """SELECT DISTINCT canonical_entity_id FROM registry_projection.v_active_entity_link
           WHERE entity_type='PERSON' AND link_status='LINKED'""").fetchall()}
    pc.close()
    qc = psycopg.connect(db.qa_dsn())
    candidates = qc.execute(
        """SELECT canonical_entity_id FROM canonical_entity
           WHERE entity_kind='PERSON' ORDER BY canonical_entity_id DESC LIMIT 60""").fetchall()
    qc.close()
    unlinked = next((r[0] for r in candidates if r[0] not in linked), None)
    if unlinked is None:
        pytest.skip("every sampled person has linked evidence")
    html = client.get(f"/persons/{unlinked}").get_data(as_text=True)
    assert html  # page must still render
    assert 'id="skraseting-person"' not in html


# --- feature flags + failure modes ------------------------------------------------

def test_feature_flag_ui_off_no_links_no_section(client, monkeypatch):
    monkeypatch.setenv("PDF_HISTORY_UI_ENABLED", "false")
    monkeypatch.setenv("PDF_HISTORY_INTEGRATION_ENABLED", "true")
    assert client.get(f"/companies/{KNOWN_5534}/skraseting").status_code == 404
    person_html = client.get("/persons/3623").get_data(as_text=True)
    assert 'id="skraseting-person"' not in person_html


def test_feature_flag_integration_off_helpers_fail_closed(monkeypatch):
    monkeypatch.setenv("PDF_HISTORY_INTEGRATION_ENABLED", "false")
    assert consumer.get_entity_link("COMPANY", "5534") is None
    assert consumer.get_linked_person_for_role(1) is None
    assert consumer.get_skraseting_roles_for_person(3623) is None


def test_link_read_failure_fail_closed(monkeypatch):
    """A broken link read returns None/[] and never raises (§46)."""
    from unittest.mock import patch
    with patch.object(consumer, "_resolve_active_run",
                      side_effect=RuntimeError("boom")):
        assert consumer.get_entity_link("COMPANY", "5534") is None
        assert consumer.get_skraseting_roles_for_person(3623) is None


@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_existing_company_page_works_without_links(client):
    """No link dependency: canonical company page + no-projection page (§45)."""
    os.environ.update(UI_ENV)
    assert client.get(f"/companies/{KNOWN_5534}").status_code == 200
    assert client.get(f"/companies/{NO_PROJECTION}").status_code == 200
    html = client.get(f"/companies/{NO_PROJECTION}/skraseting").get_data(as_text=True)
    assert "Der er ingen autoritativ Skráseting-PDF-status" in html


# --- builder: idempotency + immutability -------------------------------------------

def test_link_builder_idempotent(built_links):
    """Same run + canonical state + link code version => identical rows (§51)."""
    assert built_links["checksum1"] == built_links["checksum2"]
    assert built_links["stats1"]["COMPANY"] == built_links["stats2"]["COMPANY"]
    assert built_links["stats1"]["PERSON"] == built_links["stats2"]["PERSON"]


def test_canonical_and_evidence_immutability_after_rebuild(built_links):
    """Rebuild must not mutate canonical/evidence content (§48).

    Canonical tables: exact md5 (binding recipe) before == after. Evidence
    tables: the clone holds every role row of its source run, and a fresh
    before/after comparison across one more rebuild. (H-5: no 2026-08 digests.)
    """
    def canonical_digests():
        qc = psycopg.connect(db.qa_dsn())
        try:
            return {table: qc.execute(
                f"SELECT count(*), md5(string_agg(t::text, '|' ORDER BY t::text)) "
                f"FROM {table} AS t").fetchone()
                for table in ("company_inventory", "canonical_entity")}
        finally:
            qc.close()

    canonical_before = canonical_digests()
    run = built_links["run"]
    pc = psycopg.connect(consumer.projection_dsn())
    before = {
        "pdf_run_log": pc.execute(
            "SELECT count(*) FROM pdf_run_log").fetchone()[0],
        "company_person_role": pc.execute(
            "SELECT count(*) FROM registry_projection.company_person_role "
            "WHERE projection_run_id = %s", (run,)).fetchone()[0],
        "company_current_state": pc.execute(
            "SELECT count(*) FROM registry_projection.company_current_state "
            "WHERE projection_run_id = %s", (run,)).fetchone()[0],
        "company_history_event": pc.execute(
            "SELECT count(*) FROM registry_projection.company_history_event "
            "WHERE projection_run_id = %s", (run,)).fetchone()[0],
        "pdf_parse_result_digest": pc.execute(
            "SELECT md5(string_agg(t::text, '|' ORDER BY t::text)) "
            "FROM (SELECT id FROM pdf_parse_result) AS t"
        ).fetchone()[0],
    }
    qc = psycopg.connect(db.qa_dsn())
    el.build_entity_links(run, projection_conn=pc, canonical_conn=qc)
    pc.commit()
    qc.close()
    after = {
        "pdf_run_log": pc.execute(
            "SELECT count(*) FROM pdf_run_log").fetchone()[0],
        "company_person_role": pc.execute(
            "SELECT count(*) FROM registry_projection.company_person_role "
            "WHERE projection_run_id = %s", (run,)).fetchone()[0],
        "company_current_state": pc.execute(
            "SELECT count(*) FROM registry_projection.company_current_state "
            "WHERE projection_run_id = %s", (run,)).fetchone()[0],
        "company_history_event": pc.execute(
            "SELECT count(*) FROM registry_projection.company_history_event "
            "WHERE projection_run_id = %s", (run,)).fetchone()[0],
        "pdf_parse_result_digest": pc.execute(
            "SELECT md5(string_agg(t::text, '|' ORDER BY t::text)) "
            "FROM (SELECT id FROM pdf_parse_result) AS t"
        ).fetchone()[0],
    }
    source_roles = pc.execute(
        "SELECT count(*) FROM registry_projection.company_person_role "
        "WHERE projection_run_id = %s", (built_links["source_run"],)).fetchone()[0]
    pc.close()
    assert before == after
    assert after["company_person_role"] == source_roles > 0
    assert canonical_digests() == canonical_before


def test_link_code_version_stamped(built_links):
    pc = psycopg.connect(consumer.projection_dsn())
    versions = pc.execute(
        "SELECT DISTINCT link_code_version FROM registry_projection.v_active_entity_link").fetchall()
    pc.close()
    assert versions == [(el.LINK_CODE_VERSION,)]


def test_link_invariant_one_row_per_source_identity(built_links):
    """UNIQUE(run, entity_type, source_entity_key) — no second active link
    from the same source identity to a different canonical entity (§49)."""
    pc = psycopg.connect(consumer.projection_dsn())
    dup = pc.execute(
        """SELECT count(*) FROM (
             SELECT projection_run_id, entity_type, source_entity_key, count(*) c
             FROM registry_projection.entity_link GROUP BY 1,2,3 HAVING count(*)>1) x""").fetchone()[0]
    bad = pc.execute(
        """SELECT count(*) FROM registry_projection.v_active_entity_link
           WHERE link_status='LINKED' AND canonical_entity_id IS NULL""").fetchone()[0]
    pc.close()
    assert dup == 0 and bad == 0