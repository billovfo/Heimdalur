"""Offline ingest service per plan v3.1.2 FASE 7/13/14.

ingest_pdf_observation() is a pure-DB service function: the caller supplies PDF
bytes (in tests: fixture files). It NEVER performs network access. Binding
behaviour:

* content (pdf_document_content) is always inserted BEFORE the observation that
  references it (patch-1 FK order).
* UNIQUE(document_id, collection_run_id): same logical document + same run
  returns the existing observation (max once per run).
* same sha256 + same parser_version reuses the stored parse; a NEW parser_version
  parses to a NEW row; deleting parse rows makes a rerun recompute without new
  observations (restart test).
* embedded identity gate is fail-closed in the DERIVED views (history excluded,
  queue row added) - the parse itself stays as evidence.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone

import psycopg

from psycopg.types.json import Json

from .extract import TextExtractionError, extract_text
from .parse import FELAG_PDF_TYPE, LYSING_PDF_TYPE, parse_felag_pdf, parse_lysing_pdf

PARSER_VERSION_DEFAULT = "1"


def _parser_for(pdf_type: str, parser_fn) :
    if parser_fn is not None:
        return parser_fn
    if pdf_type == FELAG_PDF_TYPE:
        return parse_felag_pdf
    if pdf_type == LYSING_PDF_TYPE:
        return parse_lysing_pdf
    raise ValueError(f"no parser for pdf_type {pdf_type!r}")


def _cell_role(pdf_type: str) -> str:
    if pdf_type == FELAG_PDF_TYPE:
        return "FELAG_CELL"
    return "LYSING_CELL"


def _log(conn: psycopg.Connection, run_id: int, company_id: int | None,
         document_id: int | None, step: str, status: str,
         error_type: str | None = None, detail: str | None = None) -> None:
    conn.execute(
        "INSERT INTO pdf_run_log (run_id, company_id, document_id, step,"
        " status, error_type, detail) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (run_id, company_id, document_id, step, status, error_type, detail))


def _ensure_source_event(conn: psycopg.Connection, company_id: int,
                         source_event: dict) -> int:
    """Upsert the verified source row; return its id. Idempotent on
    source_row_key."""
    conn.execute(
        "INSERT INTO pdf_source_event (company_id, requested_regnr,"
        " row_skrasnr, source_row_key, source_event_date, raw_text, page_no,"
        " row_ordinal)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (source_row_key) DO NOTHING",
        (company_id, source_event["row_skrasnr"], source_event["row_skrasnr"],
         source_event["source_row_key"], source_event.get("source_event_date"),
         source_event["raw_text"], source_event.get("page_no", 0),
         source_event.get("row_ordinal", 1)))
    return conn.execute(
        "SELECT id FROM pdf_source_event WHERE source_row_key = %s",
        (source_event["source_row_key"],),).fetchone()[0]


def _ensure_document(conn: psycopg.Connection, source_system: str, pdf_type:
                     str, source_document_id: str, kind_hint: str) -> int:
    conn.execute(
        "INSERT INTO pdf_document (source_system, pdf_type,"
        " source_document_id, pdf_kind_hint) VALUES (%s,%s,%s,%s)"
        " ON CONFLICT (source_system, pdf_type, source_document_id)"
        " DO NOTHING",
        (source_system, pdf_type, source_document_id, kind_hint))
    return conn.execute(
        "SELECT id FROM pdf_document WHERE source_system = %s AND pdf_type = %s"
        " AND source_document_id = %s",
        (source_system, pdf_type, source_document_id)).fetchone()[0]


def _snapshot_date_of(structured: dict) -> date | None:
    value = structured.get("snapshot_date")
    if not value:
        return None
    return date.fromisoformat(value)


def ingest_pdf_observation(
    *,
    conn: psycopg.Connection,
    company_id: int,
    source_event: dict,
    pdf_type: str,
    source_document_id: str,
    pdf_bytes: bytes,
    collection_run_id: int,
    fetched_at: datetime | None = None,
    parser_version: str = PARSER_VERSION_DEFAULT,
    parser_fn=None,
) -> dict:
    """Ingest one fetched PDF (bytes already in hand - this service does no
    network). Returns a plain dict describing persistence vs reuse."""
    source_system = "sg.vs.fo"
    sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    byte_length = len(pdf_bytes)
    fetched_at = fetched_at or datetime.now(timezone.utc)
    parser = _parser_for(pdf_type, parser_fn)
    kind_hint = ("FULL_STATE_SNAPSHOT" if pdf_type == FELAG_PDF_TYPE
                 else "REGISTRATION_NOTICE")

    # -- 1. verified source row + logical document reference ----------------
    source_event_id = _ensure_source_event(conn, company_id, source_event)
    document_id = _ensure_document(conn, source_system, pdf_type,
                                   source_document_id, kind_hint)

    # -- 2. link row written first, regardless of fetch outcome -------------
    conn.execute(
        "INSERT INTO pdf_source_event_document (source_event_id, document_id,"
        " cell_role) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
        (source_event_id, document_id, _cell_role(pdf_type)))
    _log(conn, collection_run_id, company_id, document_id, "FETCH", "OK",
         None, "sha256=" + sha256)

    # -- 3. content store (dedup; content BEFORE observation) ---------------
    cursor = conn.execute(
        "INSERT INTO pdf_document_content (sha256, byte_length, content)"
        " VALUES (%s,%s,%s) ON CONFLICT (sha256) DO NOTHING",
        (sha256, byte_length, memoryview(pdf_bytes)))
    content_was_new = cursor.rowcount == 1

    # -- 4. observation guard: max once per (document, run) ------------------
    existing = conn.execute(
        "SELECT id, sha256 FROM pdf_document_observation"
        " WHERE document_id = %s AND collection_run_id = %s",
        (document_id, collection_run_id)).fetchone()
    if existing is not None:
        observation_id = existing[0]
        reused_observation = True
        if existing[1] != sha256:
            _log(conn, collection_run_id, company_id, document_id, "FETCH",
                 "WARN", "OBSERVATION_HASH_DIFFERS_WITHIN_RUN",
                 "same document observed earlier this run with other bytes")
    else:
        reused_observation = False
        observation_id = conn.execute(
            "INSERT INTO pdf_document_observation (document_id,"
            " collection_run_id, fetched_at, document_snapshot_date, sha256,"
            " byte_length, extracted_text)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (document_id, collection_run_id, fetched_at, None, sha256,
             byte_length, None)).fetchone()[0]

    # -- 5. parse with FASE-13 reuse (one row per observation+version) -------
    existing_parse = conn.execute(
        "SELECT id, parse_status FROM pdf_parse_result"
        " WHERE document_observation_id = %s AND parser_version = %s",
        (observation_id, parser_version)).fetchone()
    if existing_parse is not None:
        _log(conn, collection_run_id, company_id, document_id, "PARSE", "OK",
             None, "reused parse_result_id=%d" % existing_parse[0])
        return {"document_id": document_id,
                "observation_id": observation_id,
                "parse_result_id": existing_parse[0],
                "reused_observation": reused_observation,
                "reused_parse": True,
                "parse_status": existing_parse[1],
                "structured_data": None,
                "needs_review": None}

    # -- 6. extraction + parse; failure never loses the document -------------
    try:
        extracted_text = extract_text(pdf_bytes)
        struct = parser(extracted_text)
        raw_text = extracted_text
        parse_status = "PARSED"
        if struct.get("needs_review"):
            parse_status = "REVIEW"
    except TextExtractionError as error:
        struct = {"document_kind": "OTHER", "needs_review": True,
                  "review_reasons": [error.review_reason]}
        raw_text = ""
        extracted_text = ""
        parse_status = "REVIEW"

    needs_review = bool(struct.get("needs_review"))
    review_reason = ",".join(struct.get("review_reasons") or []) or None
    snapshot_date = _snapshot_date_of(struct)

    # materialize extracted text + snapshot date on the observation (also
    # backfills a reused observation whose parse did not survive)
    conn.execute(
        "UPDATE pdf_document_observation SET extracted_text = %s,"
        " document_snapshot_date = %s WHERE id = %s",
        (extracted_text, snapshot_date, observation_id))

    parse_result_id = conn.execute(
        "INSERT INTO pdf_parse_result (document_observation_id,"
        " parser_version, document_kind, raw_text, structured_data,"
        " parse_status, needs_review, review_reason)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (observation_id, parser_version, struct.get("document_kind"),
         raw_text, Json(struct), parse_status, needs_review,
         review_reason)).fetchone()[0]

    _log(conn, collection_run_id, company_id, document_id, "PARSE",
         "REVIEW" if needs_review else "OK", review_reason, None)
    return {"document_id": document_id, "observation_id": observation_id,
            "parse_result_id": parse_result_id, "reused_observation":
            reused_observation, "reused_parse": False,
            "parse_status": parse_status, "structured_data": struct,
            "needs_review": needs_review, "content_was_new": content_was_new}
