"""Pilot DB setup - NO LIVE REQUESTS in this script.

1. Creates the dedicated pilot database pdfhist_pilot (drop + create + apply
   migration). The pilot pipeline writes ONLY to the 7 pdf_* tables there.
   The drop destroys the pilot data, so it runs only with --force-drop.
2. Confirms database name / host / current_user / schema before first request.
3. Records the company_location rowcount+checksum from the QA heimdal database
   (READ-ONLY connection) - the before-pilot protection snapshot.
4. Seeds the offline fixture bytes as PRIOR observations (run SEED_RUN) so the
   live pilot can detect CONTENT_CHANGED (same logical document, new hash) -
   the fixtures are real source bytes collected in the earlier recon session.
"""
import argparse
import io
import json
from datetime import datetime, timezone

import psycopg

from heimdal.pdfhist.db import admin_dsn, qa_dsn
from heimdal.pdfhist.ingest import ingest_pdf_observation
from heimdal.pdfhist.migrate import apply_migration, bootstrap_test_database
from heimdal.pdfhist.testsupport import fixture_bytes

PILOT_DB = "pdfhist_pilot"
PILOT_RUN = 424242
SEED_RUN = 424241
QA_DSN = qa_dsn()
SEED_FETCHED_AT = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)

# (pdf_type, source_document_id, fixture file, seed event key, event date)
SEEDS = [
    ("FelagPdf", "134164", "felag_10.bin", "seed-felag-134164", None),
    ("LysingPdf", "2596", "lys_2596_15-04-2010.bin", "seed-lys-2596",
     "2010-04-15"),
    ("LysingPdf", "3801", "lys_3801_29-03-2011.bin", "seed-lys-3801",
     "2011-03-29"),
    ("LysingPdf", "41211", "lys_41211_2026.bin", "seed-lys-41211",
     "2026-04-10"),
    ("LysingPdf", "9704", "lys_9704_2004.bin", "seed-lys-9704",
     "2015-11-25"),
]


def pilot_dsn() -> str:
    return f"{admin_dsn().rsplit('/', 1)[0]}/{PILOT_DB}"


def company_location_snapshot(conn) -> dict:
    """Read-only rowcount + deterministic checksum of company_location."""
    row = conn.execute(
        "SELECT count(*), md5(string_agg(t::text, '|' ORDER BY t::text))"
        " FROM company_location t").fetchone()
    return {"rowcount": row[0], "checksum_md5": row[1]}


def main(force_drop: bool = False) -> None:
    state = {"pilot_db": PILOT_DB, "pilot_run": PILOT_RUN,
             "seed_run": SEED_RUN}

    # -- pilot database ------------------------------------------------------
    dsn = bootstrap_test_database(admin_dsn(), PILOT_DB, force_drop=force_drop)
    state["pilot_dsn_host"] = dsn
    with psycopg.connect(dsn) as conn:
        apply_migration(conn)
        ident = conn.execute(
            "SELECT current_database(), inet_server_addr()::text,"
            " current_user, current_schema()").fetchone()
        state["pilot_db_confirm"] = {
            "database": ident[0], "host": ident[1], "user": ident[2],
            "schema": ident[3]}
        tables = [r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema='public' AND table_name LIKE 'pdf_%'"
            " ORDER BY 1").fetchall()]
        state["pilot_pdf_tables"] = tables

    # -- QA heimdal database: READ-ONLY before-snapshot ----------------------
    with psycopg.connect(QA_DSN) as qa:
        state["company_location_before"] = company_location_snapshot(qa)
        inv = qa.execute(
            "SELECT regexp_replace(registration_number,'^S','','i'),"
            " source_entity_id, raw_display_name FROM company_inventory"
            " WHERE regexp_replace(registration_number,'^S','','i')"
            " = ANY(%s) ORDER BY 1",
            (["10", "931", "5015", "5019", "8023", "8664"],)).fetchall()
        state["inventory_confirm"] = [
            {"regnr": r[0], "source_entity_id": r[1], "name": r[2]}
            for r in inv]

    # -- seed prior fixture observations (run SEED_RUN) ----------------------
    with psycopg.connect(dsn) as conn:
        seeded = []
        for pdf_type, doc_id, fixture, event_key, event_date in SEEDS:
            result = ingest_pdf_observation(
                conn=conn, company_id=10,
                source_event={
                    "row_skrasnr": "10",
                    "source_row_key": f"sg.vs.fo#10#{event_key}",
                    "source_event_date": event_date,
                    "raw_text": f"SEEDED prior observation from fixtures/"
                                f"pdfhist/{fixture} (earlier recon session)",
                    "page_no": 0, "row_ordinal": 1,
                },
                pdf_type=pdf_type, source_document_id=doc_id,
                pdf_bytes=fixture_bytes(fixture),
                collection_run_id=SEED_RUN, fetched_at=SEED_FETCHED_AT)
            seeded.append({"pdf_type": pdf_type, "document": doc_id,
                           "fixture": fixture,
                           "observation_id": result["observation_id"],
                           "parse_result_id": result["parse_result_id"],
                           "parse_status": result["parse_status"]})
        state["seeded_observations"] = seeded

    io.open("pilot_state.json", "w", encoding="utf-8").write(
        json.dumps(state, indent=1, default=str))
    print(json.dumps(state, indent=1, default=str))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force-drop", action="store_true",
                    help=f"DROP and re-create {PILOT_DB} (destroys its data)")
    if not ap.parse_args().force_drop:
        ap.exit(2, f"refusing: this drops and re-creates {PILOT_DB}; "
                   "re-run with --force-drop if that is intended\n")
    main(force_drop=True)