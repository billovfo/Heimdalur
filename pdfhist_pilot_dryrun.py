"""DRY-RUN of the live pilot against SAVED recon HTML + fixture bytes.

No network: http_get is monkey-patched to serve tmp-pdf-recon/*.html for
search pages and $HEIMDAL_FIXTURES_DIR/*.bin for PDF api calls. Uses a throwaway
pdfhist_dryrun_test database. Validates parsing, pagination reconciliation, row
storage, refs, fetch bounds and ingest mechanics BEFORE the first live
request."""
import io
import sys

import psycopg

import pdfhist_live_pilot as pilot
from heimdal.pdfhist.db import admin_dsn
from heimdal.pdfhist.migrate import apply_migration, bootstrap_test_database
from heimdal.pdfhist.testsupport import FIXTURE_DIR

SAVED = {
    ("10", 1): "tmp-pdf-recon/search_10_p1.html",
    ("10", 2): "tmp-pdf-recon/search_10_p2.html",
    ("5015", 1): "tmp-pdf-recon/search_5015.html",
    ("8023", 1): "tmp-pdf-recon/search_8023.html",
    ("8664", 1): "tmp-pdf-recon/search_8664.html",
    ("931", 1): "tmp-pdf-recon/search_931.html",
}
FIXTURE_BY_ID = {  # real register PDFs: HEIMDAL_FIXTURES_DIR, outside git (I-5)
    "134164": FIXTURE_DIR / "felag_10.bin",
    "2596": FIXTURE_DIR / "lys_2596_15-04-2010.bin",
    "3801": FIXTURE_DIR / "lys_3801_29-03-2011.bin",
    "41211": FIXTURE_DIR / "lys_41211_2026.bin",
    "9704": FIXTURE_DIR / "lys_9704_2004.bin",
}
STANDIN = FIXTURE_DIR / "lys_9704_2004.bin"


def fake_http_get(url, kind, referer=None):
    pilot._wait_gap = lambda kind: None  # no sleeping in the dry-run
    pilot.budget[kind] = pilot.budget.get(kind, 0) + 1
    pilot.budget["total"] += 1
    if pilot.SEARCH_PATH in url:
        import re
        name = re.search(r"name=(\d+)", url).group(1)
        page_no = 1
        m = re.search(r"page=(\d+)", url)
        if m:
            page_no = int(m.group(1))
        key = (name, page_no)
        if key not in SAVED:
            return 404, b"no saved page", {}
        return 200, io.open(SAVED[key], "rb").read(), {
            "Content-Type": "text/html"}
    m = __import__("re").search(r"/api/Skraseting/(LysingPdf|FelagPdf)\?id=(\d+)", url)
    api_id = m.group(2)
    path = FIXTURE_BY_ID.get(api_id, STANDIN)
    return 200, io.open(path, "rb").read(), {"Content-Type": "application/pdf"}


def main():
    dsn = bootstrap_test_database(admin_dsn(), "pdfhist_dryrun_test")
    with psycopg.connect(dsn) as conn:
        apply_migration(conn)
    pilot.PILOT_COMPANIES = ["10", "5015", "8023", "8664", "931"]
    pilot.http_get = fake_http_get
    pilot._wait_gap = lambda kind: None
    exit_code = pilot.main(dsn)
    print("dry-run exit code:", exit_code)
    with psycopg.connect(dsn) as conn:
        events = conn.execute(
            "SELECT count(*) FROM pdf_source_event").fetchone()[0]
        obs = conn.execute(
            "SELECT count(*) FROM pdf_document_observation").fetchone()[0]
        docs = conn.execute(
            "SELECT count(*) FROM pdf_document").fetchone()[0]
        parses = conn.execute(
            "SELECT parse_status, count(*) FROM pdf_parse_result"
            " GROUP BY 1").fetchall()
        junctions = conn.execute(
            "SELECT count(*) FROM pdf_source_event_document").fetchone()[0]
    print(f"events={events} documents={docs} observations={obs} "
          f"junctions={junctions} parses={parses}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())