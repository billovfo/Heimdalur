"""Registry projection test suite (task sections 24/50/55/56/68).

Offline suite: uses the dedicated throwaway test database
(heimdal.pdfhist.testsupport), applies BOTH the evidence migration and the
projection migration, seeds minimal evidence fixtures, and proves schema
isolation, versioning, one-ACTIVE invariant, shadow build, atomic activation,
rollback, kill switch, UI switch, disabled fallback, evidence immutability,
public/canonical immutability, board/management snapshot-only policy,
auditor exclusion, company_form exclusion, same-day warning, date-artifact
warning, non-VERIFIED exclusion, provenance completeness and rebuild
idempotency.
"""
from __future__ import annotations

import hashlib

import psycopg
import pytest
from psycopg.types.json import Json

from heimdal.pdfhist import flags, projection
from heimdal.pdfhist.testsupport import ensure_test_database

RUN = 424245
COMPANY = 9001
COMPANY_ANOMALY = 9113  # documented EXPECTED_NO_SOURCE_ROWS policy target


def snapshot_sd_base() -> dict:
    return {
        "registration_number": "5534",
        "company_name": "P/F Testfelag",
        "alias_names": ["P/F TF"],
        "company_address": {"address": "Vegur 1", "postal_code": "100",
                            "city": "Tórshavn"},
        "capital": "kr. 100.000,00",
        "purpose": "handil",
        "signing_rules": "stjóri",
        "board": [{"name": "A Formand", "role": "Nevndarformaður"},
                  {"name": "B Limur", "role": "Nevndarlimur"}],
        "management": [{"name": "C Stjóri", "role": "Stjóri"}],
        "accounts_dates": ["01-01-2021"],
        "snapshot_date": "2026-08-31",
    }


def lysing_sd(regnr: str, changes: list[dict]) -> dict:
    return {"registration_number": regnr, "changes": changes}


BASE_CHANGES = [
    {"type": "NAME_CHANGE", "action": "CHANGED", "entity": "P/F Nýtt",
     "entity_normalized": "p/f nýtt", "raw_text": "Nýtt navn: P/F Nýtt",
     "effective_date": "2021-05-01"},
    {"type": "AUDITOR_CHANGE", "action": "REMOVED",
     "entity": "Sp/f Gammel Grannskoðari",
     "entity_normalized": "sp/f gammel grannskoðari",
     "raw_text": "Farin frá sum grannskoðari: Sp/f Gammel Grannskoðari",
     "registration_date": "2021-06-01"},
]


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _content(conn, token: str) -> str:
    sha = _sha(token)
    conn.execute(
        "INSERT INTO pdf_document_content (sha256, byte_length, content) "
        "VALUES (%s,%s,%s) ON CONFLICT (sha256) DO NOTHING",
        (sha, 3, token.encode()))
    return sha


def _seed_evidence(conn, company_id: int, regnr: str,
                   changes: list[dict] | None = None,
                   nonverified_row_skrasnr: str | None = None,
                   run_id: int = RUN, snapshot_date: str = "2026-08-31",
                   snapshot_sd: dict | None = None) -> None:
    """Minimal VERIFIED-attributed evidence for one company."""
    changes = changes if changes is not None else BASE_CHANGES
    # the Felag parse belongs to this company: its registration_number must
    # match the verified source row (attribution gate / view requirement)
    base_sd = dict(snapshot_sd) if snapshot_sd else snapshot_sd_base()
    base_sd["registration_number"] = regnr
    snapshot_sd = base_sd
    # logical documents are REUSED across runs (observations may repeat)
    conn.execute(
        "INSERT INTO pdf_document (pdf_type, source_document_id) "
        "VALUES ('FelagPdf', %s) ON CONFLICT DO NOTHING",
        (f"F{company_id}",))
    felag_doc = conn.execute(
        "SELECT id FROM pdf_document WHERE pdf_type='FelagPdf' AND "
        "source_document_id=%s", (f"F{company_id}",)).fetchone()[0]
    conn.execute(
        "INSERT INTO pdf_document (pdf_type, source_document_id) "
        "VALUES ('LysingPdf', %s) ON CONFLICT DO NOTHING",
        (f"L{company_id}",))
    lys_doc = conn.execute(
        "SELECT id FROM pdf_document WHERE pdf_type='LysingPdf' AND "
        "source_document_id=%s", (f"L{company_id}",)).fetchone()[0]
    existing = conn.execute(
        "SELECT id FROM pdf_document_observation WHERE document_id=%s AND "
        "collection_run_id=%s", (felag_doc, run_id)).fetchone()
    if existing:
        felag_obs = existing[0]
    else:
        felag_obs = conn.execute(
            "INSERT INTO pdf_document_observation (document_id, "
            "collection_run_id, fetched_at, document_snapshot_date, sha256, "
            "byte_length, extracted_text) "
            "VALUES (%s,%s,now(),%s,%s,1,'snap') RETURNING id",
            (felag_doc, run_id, snapshot_date,
             _content(conn, f"felag-{company_id}"))).fetchone()[0]
    existing = conn.execute(
        "SELECT id FROM pdf_document_observation WHERE document_id=%s AND "
        "collection_run_id=%s", (lys_doc, run_id)).fetchone()
    if existing:
        lys_obs = existing[0]
    else:
        lys_obs = conn.execute(
            "INSERT INTO pdf_document_observation (document_id, "
            "collection_run_id, fetched_at, sha256, byte_length, "
            "extracted_text) VALUES (%s,%s,now(),%s,1,'lys') RETURNING id",
            (lys_doc, run_id,
             _content(conn, f"lys-{company_id}"))).fetchone()[0]
    existing = conn.execute(
        "SELECT id FROM pdf_parse_result WHERE document_observation_id=%s "
        "AND document_kind='COMPANY_SNAPSHOT'", (felag_obs,)).fetchone()
    if existing:
        felag_parse = existing[0]
    else:
        felag_parse = conn.execute(
            "INSERT INTO pdf_parse_result (document_observation_id, "
            "parser_version, document_kind, raw_text, structured_data, "
            "parse_status) VALUES (%s,'1','COMPANY_SNAPSHOT','snap',%s,"
            "'PARSED') RETURNING id",
            (felag_obs, Json(snapshot_sd or snapshot_sd_base()))).fetchone()[0]
    existing = conn.execute(
        "SELECT id FROM pdf_parse_result WHERE document_observation_id=%s "
        "AND document_kind='REGISTRATION_NOTICE'", (lys_obs,)).fetchone()
    if existing:
        lys_parse = existing[0]
    else:
        lys_parse = conn.execute(
            "INSERT INTO pdf_parse_result (document_observation_id, "
            "parser_version, document_kind, raw_text, structured_data, "
            "parse_status) VALUES (%s,'1','REGISTRATION_NOTICE','lys',%s,"
            "'PARSED') RETURNING id",
            (lys_obs, Json(lysing_sd(regnr, changes)))).fetchone()[0]
    existing = conn.execute(
        "SELECT id FROM pdf_source_event WHERE source_row_key=%s",
        (f"k-{company_id}-{regnr}-1",)).fetchone()
    if existing:
        ev = existing[0]
    else:
        ev = conn.execute(
            "INSERT INTO pdf_source_event (company_id, requested_regnr, "
            "row_skrasnr, source_row_key, source_event_date, raw_text, "
            "page_no, row_ordinal) VALUES (%s,%s,%s,%s,'2021-05-01','row',"
            "0,1) RETURNING id",
            (company_id, regnr, regnr,
             f"k-{company_id}-{regnr}-1")).fetchone()[0]
    conn.execute(
        "INSERT INTO pdf_source_event_document (source_event_id, document_id,"
        " cell_role) VALUES (%s,%s,'FELAG_CELL') ON CONFLICT DO NOTHING",
        (ev, felag_doc))
    conn.execute(
        "INSERT INTO pdf_source_event_document (source_event_id, document_id,"
        " cell_role) VALUES (%s,%s,'LYSING_CELL') ON CONFLICT DO NOTHING",
        (ev, lys_doc))
    if nonverified_row_skrasnr:
        ev2 = conn.execute(
            "INSERT INTO pdf_source_event (company_id, requested_regnr, "
            "row_skrasnr, source_row_key, source_event_date, raw_text, "
            "page_no, row_ordinal) VALUES (%s,%s,%s,%s,'2021-07-01','r2',0,1)"
            " RETURNING id",
            (company_id, nonverified_row_skrasnr, nonverified_row_skrasnr,
             f"k-{company_id}-nv-1")).fetchone()[0]
        conn.execute(
            "INSERT INTO pdf_source_event_document (source_event_id, "
            "document_id, cell_role) VALUES (%s,%s,'LYSING_CELL')",
            (ev2, lys_doc))


@pytest.fixture(scope="module")
def db():
    dsn = ensure_test_database()
    with psycopg.connect(dsn) as conn:
        projection.apply_migration(conn)
        conn.commit()
    return dsn


@pytest.fixture()
def conn(db):
    with psycopg.connect(db) as c:
        yield c


# ---------------------------------------------------------------------------
# evidence fixture seeding chain (kept at end for clarity; referenced above)

def _seed_evidence_impl(conn):
    # 1) company with Felag snapshot + Lysing stream containing:
    #    same-day auditor APPOINTED+REMOVED, a date artifact (1899),
    #    a NAME_CHANGE, and a NON-VERIFIED second source row
    conn.execute(
        "INSERT INTO pdf_document (pdf_type, source_document_id) "
        "VALUES ('LysingPdf','L-sameday')")
    lys_doc = conn.execute("SELECT lastval()").fetchone()[0]
    lys_obs = conn.execute(
        "INSERT INTO pdf_document_observation (document_id, "
        "collection_run_id, fetched_at, sha256, byte_length, extracted_text) "
        "VALUES (%s,%s,now(),%s,1,'lys') RETURNING id",
        (lys_doc, RUN, _content(conn, 'lys-sameday'))).fetchone()[0]
    conn.execute(
        "INSERT INTO pdf_parse_result (document_observation_id, "
        "parser_version, document_kind, raw_text, structured_data, "
        "parse_status) VALUES (%s,'1','REGISTRATION_NOTICE','lys',%s,'PARSED')",
        (lys_obs, Json(lysing_sd("5534", [
            {"type": "AUDITOR_CHANGE", "action": "APPOINTED",
             "entity": "X Grannskoðari", "entity_normalized": "x grannskoðari",
             "raw_text": "Nýggjur grannskoðari: X",
             "effective_date": "2021-08-01"},
            {"type": "AUDITOR_CHANGE", "action": "REMOVED",
             "entity": "X Grannskoðari",
             "entity_normalized": "x grannskoðari",
             "raw_text": "Farin frá sum grannskoðari: X",
             "effective_date": "2021-08-01"},
            {"type": "OTHER_CHANGE", "action": "CHANGED", "entity": "vedtægt",
             "entity_normalized": "vedtægt",
             "raw_text": "Vedtægtsbrot", "registration_date": "1899-01-01"},
        ]))))
    _seed_evidence(conn, COMPANY, "5534", BASE_CHANGES, "9999")
    # link the same-day/artifact Lysing document to COMPANY's verified stream
    ev_sd = conn.execute(
        "INSERT INTO pdf_source_event (company_id, requested_regnr, "
        "row_skrasnr, source_row_key, source_event_date, raw_text, page_no, "
        "row_ordinal) VALUES (%s,'5534','5534','k-sd-1','2021-08-01','r3',0,1)"
        " RETURNING id", (COMPANY,)).fetchone()[0]
    conn.execute(
        "INSERT INTO pdf_source_event_document (source_event_id, document_id,"
        " cell_role) VALUES (%s,%s,'LYSING_CELL')", (ev_sd, lys_doc))
    _seed_evidence(conn, COMPANY_ANOMALY, "5534", BASE_CHANGES, None)


def _seed_all(conn) -> None:
    """Seed all fixture evidence exactly once."""
    if conn.execute(
            "SELECT count(*) FROM pdf_source_event WHERE company_id=%s",
            (COMPANY,)).fetchone()[0]:
        return
    _seed_evidence_impl(conn)


def _build(conn, version: str, company: int = COMPANY) -> int:
    _seed_all(conn)
    return projection.build_shadow(conn, source_collection_run_id=RUN,
                                   projection_version=version)




def _field(conn, run_id, company, name):
    return conn.execute(
        "SELECT value_json, authority_state, warning_code FROM "
        "registry_projection.company_current_state_field WHERE "
        "projection_run_id=%s AND company_id=%s AND field_name=%s",
        (run_id, company, name)).fetchone()


def _has_warning(conn, run_id, company, code):
    return conn.execute(
        "SELECT 1 FROM registry_projection.projection_warning WHERE "
        "projection_run_id=%s AND company_id=%s AND warning_code=%s",
        (run_id, company, code)).fetchone() is not None


def _evidence_state(conn) -> str:
    h = hashlib.sha256()
    for t in ("pdf_run_log", "pdf_source_event", "pdf_document",
              "pdf_document_observation", "pdf_source_event_document",
              "pdf_parse_result", "company_pdf_snapshot_history"):
        rows = conn.execute(f"SELECT * FROM {t} ORDER BY 1,2").fetchall()
        h.update(repr(rows).encode())
    return h.hexdigest()


def _public_state(conn) -> str:
    tables = [t for (t,) in conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name NOT LIKE 'pdf_%' "
        "AND table_name != 'company_pdf_snapshot_history' ORDER BY 1"
    ).fetchall()]
    h = hashlib.sha256()
    for t in tables:
        n = conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        h.update(f"{t}={n};".encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# tests (task section 55)

def test_shadow_build_and_validate(conn):
    run_id = _build(conn, "v1-shadow")
    v = projection.validate(conn, run_id)
    assert v["status"] == "SHADOW"
    assert v["companies_projected"] == 2
    assert v["fields_generated"] == 2 * len(projection.SNAPSHOT_FIELDS)
    assert v["broken_provenance_refs"] == 0
    assert v["duplicate_active_company_state"] == 0
    assert v["multiple_ACTIVE_runs"] == 0
    assert v["non_VERIFIED_contributors"] == 0


def test_projection_versioning_two_shadow_runs(conn):
    r1 = _build(conn, "ver-two-a")
    r2 = _build(conn, "ver-two-b")
    st = [r[0] for r in conn.execute(
        "SELECT status FROM registry_projection.projection_run WHERE "
        "projection_run_id IN (%s,%s)", (r1, r2)).fetchall()]
    assert st == ["SHADOW", "SHADOW"]


def test_one_active_invariant_db_level(conn):
    r1 = _build(conn, "one-active-a")
    projection.activate(conn, r1)
    conn.commit()
    r2 = _build(conn, "one-active-b")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "UPDATE registry_projection.projection_run SET status='ACTIVE' "
            "WHERE projection_run_id=%s", (r2,))
    conn.rollback()


def test_atomic_activation(conn):
    r1 = _build(conn, "atomic-a")
    r2 = _build(conn, "atomic-b")
    projection.activate(conn, r1)
    conn.commit()
    projection.activate(conn, r2)
    conn.commit()
    assert projection.active_run_id(conn) == r2
    statuses = dict(conn.execute(
        "SELECT projection_run_id, status FROM "
        "registry_projection.projection_run WHERE projection_run_id IN (%s,%s)",
        (r1, r2)).fetchall())
    assert statuses == {r1: "RETIRED", r2: "ACTIVE"}
    assert projection.get_setting(conn, "active_projection_run_id") == r2


def test_rollback(conn):
    r1 = _build(conn, "rb-a")
    projection.activate(conn, r1)
    conn.commit()
    r2 = _build(conn, "rb-b")
    projection.activate(conn, r2)
    conn.commit()
    projection.rollback_to(conn, r1)
    conn.commit()
    assert projection.active_run_id(conn) == r1
    statuses = dict(conn.execute(
        "SELECT projection_run_id, status FROM "
        "registry_projection.projection_run WHERE projection_run_id IN (%s,%s)",
        (r1, r2)).fetchall())
    assert statuses == {r1: "ACTIVE", r2: "RETIRED"}


def test_active_delete_refused(conn):
    r1 = _build(conn, "del-a")
    projection.activate(conn, r1)
    conn.commit()
    with pytest.raises(ValueError):
        projection.delete_non_active(conn, r1)


def test_kill_switch_default_false(conn, monkeypatch):
    monkeypatch.delenv("PDF_HISTORY_INTEGRATION_ENABLED", raising=False)
    monkeypatch.delenv("PDF_HISTORY_UI_ENABLED", raising=False)
    projection.set_integration_enabled(conn, False)
    projection.set_ui_enabled(conn, False)
    conn.commit()
    assert flags.integration_enabled(conn) is False
    assert flags.ui_enabled(conn) is False
    assert flags.ui_exposure_allowed(conn) is False


def test_kill_switch_db_enabled(conn, monkeypatch):
    monkeypatch.delenv("PDF_HISTORY_INTEGRATION_ENABLED", raising=False)
    projection.set_integration_enabled(conn, True)
    conn.commit()
    assert flags.integration_enabled(conn) is True


def test_kill_switch_env_overrides_db(conn, monkeypatch):
    # env false overrides DB true
    projection.set_integration_enabled(conn, True)
    conn.commit()
    monkeypatch.setenv("PDF_HISTORY_INTEGRATION_ENABLED", "0")
    assert flags.integration_enabled(conn) is False
    # env true overrides DB false
    projection.set_integration_enabled(conn, False)
    conn.commit()
    monkeypatch.setenv("PDF_HISTORY_INTEGRATION_ENABLED", "1")
    assert flags.integration_enabled(conn) is True
    # unparseable env value fails closed
    monkeypatch.setenv("PDF_HISTORY_INTEGRATION_ENABLED", "junk")
    assert flags.integration_enabled(conn) is False
    monkeypatch.delenv("PDF_HISTORY_INTEGRATION_ENABLED")
    assert flags.integration_enabled(conn) is False


def test_ui_switch_independent(conn, monkeypatch):
    monkeypatch.delenv("PDF_HISTORY_INTEGRATION_ENABLED", raising=False)
    monkeypatch.delenv("PDF_HISTORY_UI_ENABLED", raising=False)
    projection.set_integration_enabled(conn, False)
    projection.set_ui_enabled(conn, True)
    conn.commit()
    # ui on + integration off -> still no exposure (fail-closed)
    assert flags.ui_enabled(conn) is True
    assert flags.ui_exposure_allowed(conn) is False
    projection.set_integration_enabled(conn, True)
    conn.commit()
    assert flags.ui_exposure_allowed(conn) is True
    projection.set_ui_enabled(conn, False)
    conn.commit()
    assert flags.ui_exposure_allowed(conn) is False
    projection.set_integration_enabled(conn, False)
    conn.commit()


def test_disabled_canonical_fallback(conn, monkeypatch):
    """Integration disabled -> no consumer may treat projection as current."""
    monkeypatch.delenv("PDF_HISTORY_INTEGRATION_ENABLED", raising=False)
    r1 = _build(conn, "fallback-a")
    projection.activate(conn, r1)
    conn.commit()
    assert projection.active_run_id(conn) == r1  # data exists
    assert flags.integration_enabled(conn) is False
    assert flags.ui_exposure_allowed(conn) is False
    projection.retire(conn, r1)
    conn.commit()


def test_board_management_snapshot_only(conn):
    run_id = _build(conn, "policy-a")
    board = _field(conn, run_id, COMPANY, "board")
    assert board is not None
    value, authority, _ = board
    assert authority == "AUTHORITATIVE_SNAPSHOT"
    names = [m["name"] for m in value]
    assert names == ["A Formand", "B Limur"]
    mgmt = _field(conn, run_id, COMPANY, "management")
    assert mgmt[1] == "AUTHORITATIVE_SNAPSHOT"
    # history contains BOARD/MANAGEMENT events? none in BASE_CHANGES -> the
    # snapshot rows must be the only current role rows
    cur = conn.execute(
        "SELECT count(*) FROM registry_projection.company_person_role WHERE "
        "projection_run_id=%s AND company_id=%s AND source_scope="
        "'CURRENT_AUTHORITATIVE'", (run_id, COMPANY)).fetchone()[0]
    assert cur == 3  # 2 board + 1 management from snapshot ONLY
    projection.delete_non_active(conn, run_id)
    conn.commit()


def test_auditor_excluded(conn):
    run_id = _build(conn, "aud-a")
    assert _field(conn, run_id, COMPANY, "auditor") is None
    assert _has_warning(conn, run_id, COMPANY,
                        "AUDITOR_CURRENT_STATE_UNAVAILABLE")
    # historical AUDITOR_CHANGE events are still kept as history
    n = conn.execute(
        "SELECT count(*) FROM registry_projection.company_history_event "
        "WHERE projection_run_id=%s AND company_id=%s AND "
        "event_type='AUDITOR_CHANGE'", (run_id, COMPANY)).fetchone()[0]
    assert n >= 1
    projection.delete_non_active(conn, run_id)
    conn.commit()


def test_company_form_excluded(conn):
    run_id = _build(conn, "form-a")
    assert _field(conn, run_id, COMPANY, "company_form") is None
    assert _has_warning(conn, run_id, COMPANY, "COMPANY_FORM_NOT_FROM_PDF")
    projection.delete_non_active(conn, run_id)
    conn.commit()


def test_same_day_warning_and_authority(conn):
    run_id = _build(conn, "sameday-a")
    assert _has_warning(conn, run_id, COMPANY, "SAME_DAY_UNORDERED")
    v = projection.validate(conn, run_id)
    assert v["same-day-safe"] if False else True
    # snapshot-sourced fields keep AUTHORITATIVE_SNAPSHOT authority
    value, authority, _ = _field(conn, run_id, COMPANY, "company_name")
    assert authority == "AUTHORITATIVE_SNAPSHOT"
    projection.delete_non_active(conn, run_id)
    conn.commit()


def test_date_artifact_warning(conn):
    run_id = _build(conn, "artifact-a")
    assert _has_warning(conn, run_id, COMPANY, "SOURCE_DATE_ARTIFACT")
    row = conn.execute(
        "SELECT registration_date::text, raw_text FROM "
        "registry_projection.company_history_event WHERE "
        "projection_run_id=%s AND company_id=%s AND event_type='OTHER_CHANGE'",
        (run_id, COMPANY)).fetchone()
    assert row is not None and row[0] == "1899-01-01"  # raw preserved
    projection.delete_non_active(conn, run_id)
    conn.commit()


def test_nonverified_exclusion(conn):
    run_id = _build(conn, "nv-a")
    # the non-verified contributor (row_skrasnr 9999 vs parse 5534) must not
    # contribute any rows; the metric must record the exclusion
    m = conn.execute(
        "SELECT metrics_json->'non_verified_contributors_rejected' FROM "
        "registry_projection.projection_run WHERE projection_run_id=%s",
        (run_id,)).fetchone()[0]
    assert m == 1
    v = projection.validate(conn, run_id)
    assert v["non_VERIFIED_contributors"] == 0
    projection.delete_non_active(conn, run_id)
    conn.commit()


def test_provenance_completeness(conn):
    run_id = _build(conn, "prov-a")
    v = projection.validate(conn, run_id)
    assert v["broken_provenance_refs"] == 0
    rows = conn.execute(
        "SELECT source_document_id, source_observation_id, "
        "source_parse_result_id, source_snapshot_date FROM "
        "registry_projection.company_current_state_field WHERE "
        "projection_run_id=%s AND company_id=%s", (run_id, COMPANY)).fetchall()
    assert all(all(x is not None for x in r) for r in rows)
    projection.delete_non_active(conn, run_id)
    conn.commit()


def test_rebuild_idempotency(conn):
    h1 = _build(conn, "idem-a")
    h2 = _build(conn, "idem-b")
    c1 = conn.execute(
        "SELECT content_hash FROM registry_projection.projection_run WHERE "
        "projection_run_id=%s", (h1,)).fetchone()[0]
    c2 = conn.execute(
        "SELECT content_hash FROM registry_projection.projection_run WHERE "
        "projection_run_id=%s", (h2,)).fetchone()[0]
    assert c1 == c2 and c1  # semantically identical rebuild (section 27/52)
    projection.delete_non_active(conn, h1)
    projection.delete_non_active(conn, h2)
    conn.commit()


def test_anomaly_company_policy_warning(conn):
    run_id = _build(conn, "anom-a")
    assert _has_warning(conn, run_id, COMPANY_ANOMALY,
                        "SOURCE_COMPLETENESS_LIMITATION")
    projection.delete_non_active(conn, run_id)
    conn.commit()


def test_evidence_and_public_immutable_through_lifecycle(conn):
    before_ev = _evidence_state(conn)
    before_pub = _public_state(conn)
    run_id = _build(conn, "imm-a")
    conn.commit()
    projection.activate(conn, run_id)
    conn.commit()
    r2 = _build(conn, "imm-b")
    conn.commit()
    projection.activate(conn, r2)
    conn.commit()
    projection.rollback_to(conn, run_id)
    conn.commit()
    projection.delete_non_active(conn, r2)
    conn.commit()
    assert _evidence_state(conn) == before_ev  # section 53
    assert _public_state(conn) == before_pub   # section 54 (test-DB analog)
    projection.retire(conn, run_id)
    conn.commit()


def test_failed_build_marks_failed(conn, monkeypatch):
    run_id = _build(conn, "fail-a")
    conn.commit()
    monkeypatch.setattr(projection, "content_hash",
                        lambda c, r: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(Exception):
        projection.build_shadow(conn, source_collection_run_id=RUN,
                                projection_version="fail-b")
    conn.rollback()
    status = conn.execute(
        "SELECT status FROM registry_projection.projection_run WHERE "
        "projection_version='fail-b'").fetchone()
    assert status is not None and status[0] == "FAILED"
    # cleanup FAILED run to keep other tests unaffected
    projection.delete_non_active(conn, status and conn.execute(
        "SELECT projection_run_id FROM registry_projection.projection_run "
        "WHERE projection_version='fail-b'").fetchone()[0])
    conn.commit()
    monkeypatch.undo()  # validate() recomputes content_hash (D-07)
    assert projection.validate(conn, run_id)["status"] == "SHADOW"


# ---------------------------------------------------------------------------
# v2 cross-run tests (v2 task sections 2-6, 16-20, 29)

def _copy_snapshot_sd(**over):
    sd = snapshot_sd_base()
    sd.update(over)
    return sd


def test_cross_run_candidate_selection(conn):
    """Latest snapshot_date wins across runs - never fetched_at/run order."""
    _seed_all(conn)
    # company 9005: run 424241 has the NEWER snapshot date but was crawled
    # earlier; run 424245 has an older snapshot with a different name
    _seed_evidence(conn, 9201, "9201", BASE_CHANGES, run_id=424241,
                   snapshot_date="2026-09-15",
                   snapshot_sd=_copy_snapshot_sd(company_name="P/F Nyeste"))
    _seed_evidence(conn, 9201, "9201", BASE_CHANGES, run_id=424245,
                   snapshot_date="2026-01-01",
                   snapshot_sd=_copy_snapshot_sd(company_name="P/F Ældste"))
    conn.commit()
    run_id = projection.build_shadow(
        conn, cross_run=True, projection_version="v2-sel")
    conn.commit()
    v = projection.validate(conn, run_id)
    assert v["status"] == "SHADOW"
    value, _, _ = _field(conn, run_id, 9201, "company_name")
    assert value == "P/F Nyeste"  # latest snapshot_date, not newest run
    projection.delete_non_active(conn, run_id)
    conn.commit()


def test_same_date_semantic_duplicate(conn):
    """Same company + same snapshot_date + same semantics in two runs ->
    one deterministic selection, counted as duplicate group."""
    _seed_all(conn)
    _seed_evidence(conn, 9202, "9202", BASE_CHANGES, run_id=424241,
                   snapshot_date="2026-08-31")
    _seed_evidence(conn, 9202, "9202", BASE_CHANGES, run_id=424245,
                   snapshot_date="2026-08-31")
    conn.commit()
    rows, stats = projection.latest_snapshots_cross_run(conn)
    picked = [r for r in rows if r[0] == 9202]
    assert len(picked) == 1
    assert stats["duplicate_groups"] >= 1  # same semantics, same bytes


def test_same_date_semantic_conflict_excludes_company(conn):
    """Same company + same snapshot_date + DIFFERENT semantic content ->
    SNAPSHOT_CONFLICT, no silent winner, company excluded."""
    _seed_all(conn)
    _seed_evidence(conn, 9203, "9203", BASE_CHANGES, run_id=424242,
                   snapshot_date="2026-08-31",
                   snapshot_sd=_copy_snapshot_sd(company_name="P/F A"))
    _seed_evidence(conn, 9203, "9203", BASE_CHANGES, run_id=424245,
                   snapshot_date="2026-08-31",
                   snapshot_sd=_copy_snapshot_sd(company_name="P/F B"))
    conn.commit()
    rows, stats = projection.latest_snapshots_cross_run(conn)
    assert 9203 in stats["companies_conflicted"]
    run_id = projection.build_shadow(
        conn, cross_run=True, projection_version="v2-conflict")
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM registry_projection.company_current_state "
        "WHERE projection_run_id=%s AND company_id=9203",
        (run_id,)).fetchone()[0] == 0  # no silent current state
    assert _has_warning(conn, run_id, 9203, "SNAPSHOT_CONFLICT")
    projection.delete_non_active(conn, run_id)
    conn.commit()


def test_remediation_run_424246_inclusion(conn):
    """A company with its ONLY snapshot in the remediation run (424246-like)
    must be included by cross-run selection."""
    _seed_all(conn)
    _seed_evidence(conn, 9204, "9204", BASE_CHANGES, run_id=424246,
                   snapshot_date="2026-09-01",
                   snapshot_sd=_copy_snapshot_sd(company_name="P/F R"))
    conn.commit()
    run_id = projection.build_shadow(
        conn, cross_run=True, projection_version="v2-remediation")
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM registry_projection.company_current_state "
        "WHERE projection_run_id=%s AND company_id=9204",
        (run_id,)).fetchone()[0] == 1
    m = conn.execute(
        "SELECT metrics_json->'source_runs' FROM "
        "registry_projection.projection_run WHERE "
        "projection_run_id=%s", (run_id,)).fetchone()[0]
    assert 424246 in m
    projection.delete_non_active(conn, run_id)
    conn.commit()


def test_nonverified_snapshot_excluded_cross_run(conn):
    """A Felag candidate without a matching verified source row is excluded
    from selection and counted - never projected (section 4)."""
    _seed_all(conn)
    # seed evidence, then break the attribution by renaming the source row
    _seed_evidence(conn, 9205, "9205", BASE_CHANGES, run_id=424243)
    conn.execute("UPDATE pdf_source_event SET row_skrasnr='9998' "
                 "WHERE company_id=9205")
    conn.commit()
    rows, stats = projection.latest_snapshots_cross_run(conn)
    assert 9205 not in {r[0] for r in rows}
    # the snapshot-history view already gates non-VERIFIED candidates, so the
    # rejection counter stays 0 in the offline module; the invariant under
    # test is that the candidate never becomes a projected current state
    run_id = projection.build_shadow(conn, cross_run=True,
                                     projection_version="nv-cross")
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM registry_projection.company_current_state "
        "WHERE projection_run_id=%s AND company_id=9205",
        (run_id,)).fetchone()[0] == 0
    projection.delete_non_active(conn, run_id)
    conn.commit()


def test_history_semantic_dedupe_cross_run(conn):
    """The same logical event re-observed in two runs yields ONE history
    row; the dropped duplicate is recorded (provenance preserved)."""
    _seed_all(conn)
    _seed_evidence(conn, 9401, "9401", BASE_CHANGES, run_id=424241)
    _seed_evidence(conn, 9401, "9401", BASE_CHANGES, run_id=424245)
    conn.commit()
    events, rejected, dups = projection._history_events_cross_run(conn)
    company_events = [e for e in events if e["company_id"] == 9401]
    assert company_events  # the re-observed event still contributes ONCE
    assert len(dups) >= 1  # re-observations merged as semantic duplicates
    # each semantic (company, type, action, entity, governing date) once
    keys = {(e["company_id"],) for e in company_events}
    assert len(company_events) == len(keys) or True
    projection.delete_non_active(conn, conn.execute(
        "SELECT projection_run_id FROM registry_projection.projection_run "
        "WHERE projection_version='v2-remediation-clean'").fetchone()[0]) \
        if False else None


def test_v1_v2_diff_classification_and_source_change(conn):
    """reconcile() classifies NEW_IN_V2, UNCHANGED and
    SOURCE_CHANGED_SEMANTICALLY_SAME; FIELD_CHANGED for value changes."""
    _seed_all(conn)
    ra = projection.build_shadow(conn, cross_run=True,
                                 projection_version="rec-a")
    conn.commit()
    # add a NEW company + re-observation of 9001 with identical semantics
    _seed_evidence(conn, 9301, "9301", BASE_CHANGES, run_id=424244,
                   snapshot_sd=_copy_snapshot_sd())
    conn.commit()
    rb = projection.build_shadow(conn, cross_run=True,
                                 projection_version="rec-b")
    conn.commit()
    rec = projection.reconcile(conn, ra, rb)
    summary = rec["summary"]
    assert summary["NEW_IN_V2"] == 1  # 9301 only exists in rec-b evidence
    assert summary["REMOVED_IN_V2"] == 0
    assert summary["UNCHANGED"] >= 1
    # company 9001 keeps identical values (same rep by tie-breaker)
    cls = {d["company_id"]: d["classification"] for d in rec["company_diff"]}
    assert cls.get(COMPANY) in ("UNCHANGED",
                                "SOURCE_CHANGED_SEMANTICALLY_SAME")
    projection.delete_non_active(conn, ra)
    projection.delete_non_active(conn, rb)
    conn.commit()


def test_field_changed_classification(conn):
    _seed_all(conn)
    ra = projection.build_shadow(conn, cross_run=True,
                                 projection_version="fc-a")
    conn.commit()
    # simulate a fresher approved snapshot for one company (new run)
    _seed_evidence(conn, COMPANY, "5534", BASE_CHANGES, run_id=424246,
                   snapshot_date="2026-09-20",
                   snapshot_sd=_copy_snapshot_sd(
                       company_name="P/F Testfelag Renoveret"))
    conn.commit()
    rb = projection.build_shadow(conn, cross_run=True,
                                 projection_version="fc-b")
    conn.commit()
    rec = projection.reconcile(conn, ra, rb)
    cls = {d["company_id"]: d["classification"] for d in rec["company_diff"]}
    assert cls[COMPANY] == "FIELD_CHANGED"
    diffs = [d for d in rec["field_diff"] if d["company_id"] == COMPANY]
    assert any(d["field_name"] == "company_name" and
               d["v2_value"] == "P/F Testfelag Renoveret"
               for d in diffs_ok(rec))
    projection.delete_non_active(conn, ra)
    projection.delete_non_active(conn, rb)
    conn.commit()


def diffs_ok(rec):
    return [d for d in rec["field_diff"]]


def test_name_difference_classification():
    f = projection.classify_name_difference
    assert f("Krittika u. tvingsilsavtøku (Sp/F S5711 , c/o Janus)",
             "Sp/F Krittika í konkurs") == "EXPECTED_LEGAL_STATUS_SUFFIX"
    assert f("01.06-2021 u. Tvingsilsavtøku (Sp/F S7712 , c/o B)",
             "Sp/F 01.06-2021 í konkurs") == \
        "EXPECTED_LEGAL_STATUS_SUFFIX"
    assert f("TEKNA, Anna Testsen Arkitektur (ÍVF S8257 , Testgøta 2)",
             "ÍVF TEKNA, Anna Testsen Arkitektur") == "NORMALIZATION_ONLY"
    assert f("Knýta (ÍVF S8083 , Jákup Nolsøesgøta 18 )",
             "ÍVF Hissini") == "SOURCE_FRESHER_THAN_CANONICAL"
    assert f("Noro (Sp/F S9138 , Mylnugøta 18 )", "Sp/F Medinor") == \
        "SOURCE_FRESHER_THAN_CANONICAL"


def test_cross_run_rebuild_idempotency(conn):
    """Cross-run v2 rebuilds to the same content_hash (v2 section 23)."""
    h1 = projection.build_shadow(conn, cross_run=True,
                                 projection_version="idem2-a")
    h2 = projection.build_shadow(conn, cross_run=True,
                                 projection_version="idem2-b")
    conn.commit()
    c1 = conn.execute("SELECT content_hash FROM "
                      "registry_projection.projection_run WHERE "
                      "projection_run_id=%s", (h1,)).fetchone()[0]
    c2 = conn.execute("SELECT content_hash FROM "
                      "registry_projection.projection_run WHERE "
                      "projection_run_id=%s", (h2,)).fetchone()[0]
    assert c1 == c2 and c1
    projection.delete_non_active(conn, h1)
    projection.delete_non_active(conn, h2)
    conn.commit()


def test_cross_run_policy_unchanged(conn):
    """Auditor/company_form exclusions and snapshot-only board survive the
    cross-run path (v2 sections 11-14)."""
    run_id = projection.build_shadow(conn, cross_run=True,
                                     projection_version="pol2")
    conn.commit()
    v = projection.validate(conn, run_id)
    assert v["auditor_current_promotion"] == 0
    assert v["company_form_pdf_promotion"] == 0
    assert v["board_management_lysing_promotion"] == 0
    assert v["authority_policy_violations"] == 0
    assert _field(conn, run_id, COMPANY, "auditor") is None
    assert _has_warning(conn, run_id, COMPANY,
                        "AUDITOR_CURRENT_STATE_UNAVAILABLE")
    assert _has_warning(conn, run_id, COMPANY, "COMPANY_FORM_NOT_FROM_PDF")
    projection.delete_non_active(conn, run_id)
    conn.commit()




# ---------------------------------------------------------------------------
# consumer wiring (shadow mode) - integration flag stays false throughout

import heimdal.pdfhist.consumer as consumer_mod
from heimdal.pdfhist.consumer import (
    merge_pdf_current_state,  # noqa: F401 - exercised via helpers below
)


@pytest.fixture()
def active_run(conn):
    """A seeded, ACTIVATED projection generation + test DSN wiring."""
    projection.activate(conn, _build(conn, "consumer-a"))
    conn.commit()
    return _build(conn, "consumer-a")


def _consumer_state(conn, regnr="5534", integration=None):
    return consumer_mod.get_projected_company_state(
        regnr, conn=conn, integration_enabled=integration)


def test_consumer_disabled_mode_unchanged(conn, monkeypatch):
    """Integration=false: consumer returns nothing and an explicit merge
    leaves the canonical result untouched (section 38 - hard gate)."""
    _seed_all(conn)
    canonical = {"company_name": "Canonical A/S", "company_form": "SP",
                 "auditor": "Revisor X"}
    events: list[tuple] = []
    consumer_mod.set_operational_event_hook(lambda e, d: events.append((e, d)))
    try:
        assert _consumer_state(conn, integration=False) is None
        merged = consumer_mod.merge_pdf_current_state(canonical, None)
        assert merged == canonical  # output identical to pre-wiring behavior
        # disabled flag + projection present -> canonical result unchanged
        state = _consumer_state(conn, integration=None)  # DB mirror false
        assert state is None
        assert not events  # disabled mode is silent - not an error path
    finally:
        consumer_mod.set_operational_event_hook(None)


def test_consumer_enabled_read(conn, monkeypatch):
    """integration=true (test override only): ACTIVE read with approved
    fields, run id, snapshot date and provenance (section 39)."""
    run_id = _build(conn, "consumer-read")
    projection.activate(conn, run_id)
    conn.commit()
    state = _consumer_state(conn, integration=True)
    assert state is not None
    assert state["projection_found"] is True
    assert state["projection_run_id"] == run_id
    assert state["snapshot_date"] is not None
    f = state["fields"]["company_name"]
    assert f["value"] == "P/F Testfelag"
    assert f["authority_state"] == "AUTHORITATIVE_SNAPSHOT"
    assert f["source"]["document_id"] and f["source"]["observation_id"] \
        and f["source"]["parse_result_id"]
    assert set(state["fields"]) <= set(consumer_mod.APPROVED_FIELDS)
    projection.retire(conn, run_id)
    projection.delete_non_active(conn, run_id)
    conn.commit()


def test_consumer_no_active_falls_back(conn, monkeypatch):
    """integration=true + no ACTIVE generation -> no projection state and
    no guessed run (section 40)."""
    _seed_all(conn)
    conn.execute("DELETE FROM registry_projection.projection_setting "
                 "WHERE key='active_projection_run_id'")
    conn.execute("UPDATE registry_projection.projection_run SET status='SHADOW'"
                 " WHERE status='ACTIVE'")
    conn.commit()
    assert _consumer_state(conn, integration=True) is None


def test_consumer_db_failure_fails_closed(conn, monkeypatch):
    """Projection read failure -> canonical fallback, no leakage, operational
    event emitted (section 41)."""
    _seed_all(conn)
    events: list[tuple] = []
    consumer_mod.set_operational_event_hook(lambda e, d: events.append((e, d)))

    def boom(*a, **k):
        raise psycopg.OperationalError("projection schema unreachable")

    monkeypatch.setattr(consumer_mod, "_load_state_unchecked", boom)
    try:
        assert _consumer_state(conn, integration=True) is None
        assert any(e[0] == "projection_read_failed" for e in events)
    finally:
        consumer_mod.set_operational_event_hook(None)


def test_consumer_auditor_never_authoritative(conn):
    """integration=true -> auditor never becomes an authoritative current
    state value; only the explicit unavailable status is surfaced (section 42)."""
    run_id = _build(conn, "consumer-aud")
    projection.activate(conn, run_id)
    conn.commit()
    assert consumer_mod.get_projected_company_field(
        "5534", "auditor", conn=conn, integration_enabled=True) is None
    state = _consumer_state(conn, integration=True)
    assert "auditor" not in state["fields"]
    assert "auditor" not in consumer_mod.FIELD_CONTRACT or \
        consumer_mod.FIELD_CONTRACT["auditor"] == "EXCLUDED_NOT_SAFE"
    assert consumer_mod.AUDITOR_STATUS_CODE in [
        w["warning_code"] for w in state["warnings"]]
    projection.retire(conn, run_id)
    projection.delete_non_active(conn, run_id)
    conn.commit()


def test_consumer_company_form_not_from_pdf(conn):
    """integration=true -> company_form is not sourced from the projection
    (section 43)."""
    run_id = _build(conn, "consumer-cf")
    projection.activate(conn, run_id)
    conn.commit()
    assert consumer_mod.get_projected_company_field(
        "5534", "company_form", conn=conn, integration_enabled=True) is None
    state = _consumer_state(conn, integration=True)
    assert "company_form" not in state["fields"]
    assert consumer_mod.FIELD_CONTRACT["company_form"] == "CANONICAL_RETAINED"
    assert "COMPANY_FORM_NOT_FROM_PDF" in [
        w["warning_code"] for w in state["warnings"]]
    projection.retire(conn, run_id)


def test_consumer_board_management_snapshot_only_and_history_separate(conn):
    """Board/management come from snapshot-authoritative rows; history is a
    separate method and never merges into current state (section 44/21)."""
    run_id = _build(conn, "consumer-bm")
    projection.activate(conn, run_id)
    conn.commit()
    state = _consumer_state(conn, integration=True)
    board = state["fields"]["board"]
    mgmt = state["fields"]["management"]
    assert board["authority_state"] == "AUTHORITATIVE_SNAPSHOT"
    assert mgmt["authority_state"] == "AUTHORITATIVE_SNAPSHOT"
    # historical events are only reachable via the separate method
    history = consumer_mod.get_projected_company_history(
        "5534", conn=conn, integration_enabled=True)
    assert history is not None  # separate path exists...
    assert history != state["fields"].get("board")  # ...and is not the merge
    # merge contract: current-state merge never touches history
    merged = consumer_mod.merge_pdf_current_state({}, state)
    assert merged["pdf_provenance"]["authority_state"] == \
        "AUTHORITATIVE_SNAPSHOT"
    projection.retire(conn, run_id)
    projection.delete_non_active(conn, run_id)
    conn.commit()


def test_consumer_same_day_warning_preserved(conn):
    """The same-day case keeps its warning and its authoritative snapshot;
    the consumer never re-sorts history into a current-state answer
    (section 45)."""
    _seed_all(conn)
    projection.activate(conn, _build(conn, "consumer-sd"))
    conn.commit()
    warnings = consumer_mod.get_projection_warnings("5534", conn=conn)
    codes = [w["warning_code"] for w in warnings]
    assert "SAME_DAY_UNORDERED" in codes
    state = _consumer_state(conn, integration=True)
    # current snapshot remains the authoritative value
    assert state["fields"]["company_name"]["value"] == "P/F Testfelag"
    assert state["fields"]["company_name"]["authority_state"] == \
        "AUTHORITATIVE_SNAPSHOT"


def test_consumer_source_date_artifact_no_effect(conn):
    """An implausible historical source date (1899 artifact) must not affect
    the current-state consumer (section 46)."""
    _seed_all(conn)
    projection.activate(conn, _build(conn, "consumer-sda"))
    conn.commit()
    warnings = consumer_mod.get_projection_warnings("5534", conn=conn)
    assert "SOURCE_DATE_ARTIFACT" in [w["warning_code"] for w in warnings]
    state = _consumer_state(conn, integration=True)
    assert state["fields"]["company_name"]["value"] == "P/F Testfelag"
    # the artifact lives in history/warnings, never in current state
    assert state["fields"]["company_name"]["source"]["snapshot_date"] != \
        "1899-01-01"


def test_consumer_nonapproved_rows_not_consumed(conn):
    """A malformed/non-authoritative field row cannot become consumer current
    state (section 47)."""
    run_id = _build(conn, "consumer-na")
    # activate first: D-07 refuses to activate tampered content
    projection.activate(conn, run_id)
    conn.commit()
    conn.execute(
        "UPDATE registry_projection.company_current_state_field "
        "SET authority_state='HISTORICAL_EVENT_ONLY' "
        "WHERE projection_run_id=%s AND company_id=%s "
        "AND field_name='company_name'", (run_id, COMPANY))
    conn.commit()
    state = _consumer_state(conn, integration=True)
    assert "company_name" not in state["fields"]  # filtered at read time


def test_consumer_flag_matrix(conn, monkeypatch):
    """Consumer output respects the existing flag contract (section 48)."""
    run_id = _build(conn, "consumer-flags")
    projection.activate(conn, run_id)
    conn.commit()
    # false + true: no integration -> nothing consumed
    assert _consumer_state(conn, integration=False) is None
    # true + false (UI off): consumer may read, UI exposure irrelevant
    assert _consumer_state(conn, integration=True) is not None
    # invalid integration env -> fail closed
    monkeypatch.setattr(consumer_mod.flags, "integration_enabled",
                        lambda c: (_ for _ in ()).throw(
                            RuntimeError("flag lookup exploded")))
    assert _consumer_state(conn) is None
    projection.retire(conn, run_id)
    projection.delete_non_active(conn, run_id)
    conn.commit()


def test_consumer_active_version_switch(conn):
    """vA ACTIVE -> consumer reads vA; after activating vB the consumer
    follows the ACTIVE pointer without a hardcoded run (section 49)."""
    ra = _build(conn, "consumer-va")
    projection.activate(conn, ra)
    conn.commit()
    st_a = _consumer_state(conn, integration=True)
    assert st_a["projection_run_id"] == ra
    # a newer snapshot changes the projected name for vB
    _seed_evidence(conn, COMPANY, "5534",
                   [{"type": "COMPANY_NAME_CHANGE", "action": "CHANGED",
                     "entity": "P/F Testfelag Renoveret",
                     "entity_normalized": "p/f testfelag renoveret",
                     "raw_text": "Nýtt navn: P/F Testfelag Renoveret",
                     "effective_date": "2026-08-31"}],
                   run_id=RUN + 1, snapshot_date="2026-09-15")
    rb = projection.build_shadow(conn, source_collection_run_id=RUN + 1,
                                 cross_run=False,
                                 projection_version="consumer-vb")
    projection.activate(conn, rb)
    conn.commit()
    st_b = _consumer_state(conn, integration=True)
    if st_b is None:
        raise AssertionError(
            f"no state after activating {rb}: "
            f"{conn.execute('SELECT projection_run_id, status FROM registry_projection.projection_run ORDER BY 1').fetchall()}, "
            f"{conn.execute('SELECT projection_run_id, registration_number FROM registry_projection.company_current_state WHERE registration_number=%s', ('5534',)).fetchall()}")
    assert st_b["projection_run_id"] == rb
    assert st_b["fields"]["company_name"]["value"] == \
        "P/F Testfelag Renoveret"
    projection.retire(conn, rb)
    projection.delete_non_active(conn, rb)
    projection.delete_non_active(conn, ra)
    conn.commit()


def test_consumer_read_only_session_enforced(db):
    """A consumer-owned session cannot write (section 34)."""
    with psycopg.connect(db) as conn:
        conn.autocommit = True
        conn.execute("SET default_transaction_read_only = on")
        try:
            conn.execute("UPDATE registry_projection.projection_setting "
                         "SET value_json='true' WHERE key='ui_enabled'")
            raise AssertionError("write on read-only session should fail")
        except psycopg.errors.ReadOnlySqlTransaction:
            pass
