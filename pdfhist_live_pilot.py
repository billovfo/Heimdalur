"""OWNER-APPROVED LIMITED LIVE PILOT - Heimdal PDF-historik (6 companies).

Safety rules (binding, from the owner task):
* CONCURRENCY = 1 (sequential), search gap >= 1.0 s, PDF gap >= 1.2 s
* one requests.Session, cookies in memory only, no persistence
* token `s` is ephemeral: parsed from search HTML, held in memory, never
  logged/persisted (logged URLs have the s param stripped)
* HARD STOP on HTTP 403/429/Retry-After -> SOURCE_REFUSAL, whole pilot halts
* bounded retry (max 2 attempts) for network errors / 5xx only
* cross-host redirects are NEVER followed (REDIRECT_HOST)
* small hard request budget -> STOP REQUEST_BUDGET
* writes ONLY to the 7 pdf_* tables in the dedicated pilot database;
  company_inventory + company_location are read from the QA heimdal database
  READ-ONLY.

Rebuild tooling (audit D-14) is deliberately NOT restored: no full-run driver
(deleted; `git show 6e1a424^:pdfhist_fullrun.py`), RD-1 pagination only in
scripts/pdfhist_rd1_remediation.py, no admin command that builds entity links.
The recovered corpus (pdfhist_pilot_recovered_20260904, 16,400 PDFs) makes a
re-crawl unnecessary; close these gaps here first if one is ever approved.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

import psycopg
import requests

from heimdal.pdfhist.db import qa_dsn
from heimdal.pdfhist.extract import MAX_PDF_BYTES
from heimdal.pdfhist.ingest import ingest_pdf_observation

BASE = "https://www.skraseting.fo"
SEARCH_PATH = "/fo/feloeg/leita-i-skrasetingum"
API_PATH = "/api/Skraseting/{method}"
ALLOWED_HOSTS = {"www.skraseting.fo", "skraseting.fo"}
USER_AGENT = ("Heimdal-Collector/1.0 (bounded public-register collection, permitted; "
              "1 req/s, concurrency 1)")  # owner D11 2026-11-06: no private contact address
HTTP_TIMEOUT = 60                 # existing timeout rule (heimdal_collect.py)
MAX_ATTEMPTS = 2                  # existing bounded retry rule (retry_number 0/1)
SEARCH_GAP = 1.0                  # owner task: >= 1.0 s
PDF_GAP = 1.2                     # owner task: >= 1.2 s
MAX_REDIRECTS = 3                 # existing bounded redirect rule
MAX_BODY_BYTES = MAX_PDF_BYTES    # D-04: bodies are streamed and capped

SEARCH_BUDGET = 10                # 6 companies x pages + token-refresh margin
PDF_BUDGET = 34                   # ~28 expected unique refs + retry margin
TOTAL_BUDGET = 45

PILOT_RUN = 424242


def pilot_dsn() -> str:
    """Operator DSN, resolved lazily so import never fails (G2)."""
    dsn = os.environ.get("HEIMDAL_PDFHIST_PROJECTION_DSN", "")
    if not dsn:
        raise RuntimeError(
            "HEIMDAL_PDFHIST_PROJECTION_DSN is not set; refusing implicit DSN")
    return dsn


QA_DSN = qa_dsn()

# regnr -> lysing event ids to fetch (None = fetch all lysing refs).
# Company 10 has 31 lysing refs - far beyond manual-reviewable pilot size, so a
# deterministic bound: the 4 fixture-matched events (CONTENT_CHANGED test) plus
# the single newest dated event. All OTHER refs are recorded unfetched.
PILOT_COMPANIES = os.environ.get(
    "PILOT_COMPANIES", "10,5015,5019,8023,8664,931").split(",")
FETCH_BOUND = {
    "10": {"2596", "3801", "41211", "9704"},
}
# 100-company validation: cap lysing fetches per company (newest-first by
# event date, deterministic). None = fetch all lysing refs.
LYSING_FETCH_CAP = None

CONSECUTIVE_5XX_COMPANY_STOP = 2
CONSECUTIVE_5XX_PILOT_STOP = 3


class HardStop(Exception):
    """Unconditional pilot halt (source refusal / budget)."""


class CompanyAbort(Exception):
    """This company is stopped; the pilot may continue with the next one."""


# ---------------------------------------------------------------------------
# HTTP layer: one session, rate limits, hard stops, budget, redirect policy
# ---------------------------------------------------------------------------

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})

_last_search = 0.0
_last_pdf = 0.0
budget = {"search": 0, "pdf": 0, "total": 0}
_consecutive_5xx = 0


def _wait_gap(kind: str) -> None:
    global _last_search, _last_pdf
    if kind == "search":
        gap, last = SEARCH_GAP, _last_search
    else:
        gap, last = PDF_GAP, _last_pdf
    elapsed = time.monotonic() - last
    if elapsed < gap:
        time.sleep(gap - elapsed)
    if kind == "search":
        _last_search = time.monotonic()
    else:
        _last_pdf = time.monotonic()


def _check_budget(kind: str) -> None:
    if budget["total"] >= TOTAL_BUDGET:
        raise HardStop("REQUEST_BUDGET total=%d" % budget["total"])
    if kind == "search" and budget["search"] >= SEARCH_BUDGET:
        raise HardStop("REQUEST_BUDGET search=%d" % budget["search"])
    if kind == "pdf" and budget["pdf"] >= PDF_BUDGET:
        raise HardStop("REQUEST_BUDGET pdf=%d" % budget["pdf"])


def _url_for_log(url: str) -> str:
    """NEVER let the ephemeral token s reach logs or the database."""
    return re.sub(r"([?&])s=[^&]*", r"\1s=<redacted>", url)


def _read_capped(response) -> bytes | None:
    """The streamed body, or None once it passes MAX_BODY_BYTES (D-04)."""
    body = bytearray()
    try:
        for chunk in response.iter_content(64 * 1024):
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                return None
    finally:
        response.close()
    return bytes(body)


def http_get(url: str, kind: str, referer: str | None = None):
    """One request with all safety rules. Returns (status, body, headers).

    Hard stops (403/429/Retry-After) raise HardStop; network/5xx fall through
    to the single permitted retry; cross-host redirects are not followed; a
    body over MAX_BODY_BYTES is dropped unread as status BODY_TOO_LARGE.
    """
    global _consecutive_5xx
    headers = {}
    if referer:
        headers["Referer"] = referer
    for attempt in range(MAX_ATTEMPTS):
        _check_budget(kind)
        _wait_gap(kind)
        budget[kind] += 1
        budget["total"] += 1
        current = url
        redirects = 0
        try:
            while True:
                response = session.get(current, headers=headers,
                                        timeout=HTTP_TIMEOUT,
                                        allow_redirects=False, stream=True)
                if response.status_code in (301, 302, 303, 307, 308):
                    response.close()
                    location = response.headers.get("Location", "")
                    host = re.sub(r"^https?://", "", location).split("/", 1)[0]
                    if redirects >= MAX_REDIRECTS:
                        return response.status_code, b"", dict(response.headers)
                    if host not in ALLOWED_HOSTS:
                        # REDIRECT_HOST: never follow a foreign host
                        return "REDIRECT_HOST:" + host, b"", dict(response.headers)
                    current = location if location.startswith("http") \
                        else BASE + location
                    redirects += 1
                    continue
                break
            body = _read_capped(response)
        except requests.RequestException as error:
            if attempt == MAX_ATTEMPTS - 1:
                return None, str(error).encode(), {}
            continue
        retry_after = response.headers.get("Retry-After")
        if response.status_code in (403, 429):
            raise HardStop("SOURCE_REFUSAL HTTP %d on %s"
                           % (response.status_code, _url_for_log(current)))
        if retry_after:
            raise HardStop("SOURCE_REFUSAL Retry-After %s on %s"
                           % (retry_after, _url_for_log(current)))
        if body is None:
            return "BODY_TOO_LARGE", b"", dict(response.headers)
        if response.status_code == 200:
            _consecutive_5xx = 0
            return 200, body, dict(response.headers)
        if 500 <= response.status_code < 600:
            _consecutive_5xx += 1
            if _consecutive_5xx >= CONSECUTIVE_5XX_COMPANY_STOP:
                raise CompanyAbort("repeated 5xx (HTTP %d on %s)"
                                   % (response.status_code,
                                      _url_for_log(current)))
            if attempt == MAX_ATTEMPTS - 1:
                return response.status_code, body, dict(response.headers)
            continue
        return response.status_code, body, dict(response.headers)
    return None, b"attempts exhausted", {}


# ---------------------------------------------------------------------------
# Search page parsing (formats verified against saved recon HTML)
# ---------------------------------------------------------------------------

COUNT_RE = re.compile(r"leitingini\s+bleiv\s+(\d+)")
PAGINATION_RE = re.compile(
    r'<li><a href="([^"]*page=(\d+)[^"]*)">'
    r'|<li class=selected><a href="[^"]*page=(\d+)[^"]*">')


def parse_search_page(html: str) -> dict:
    """Parse one live search page: advertised count, rows, pagination links.

    Row model (verified on all six saved recon companies): each page renders
    dated event rows plus ONE undated 'current' row (same name popup, empty
    day/cells) that the advertised count includes exactly once per company.
    """
    count_match = COUNT_RE.search(html)
    count = int(count_match.group(1)) if count_match else None
    body_start = html.find("tblResult")
    body_end = html.find("</table>", body_start)
    body = html[body_start:body_end] if body_start >= 0 else ""
    rows = []
    for chunk in body.split("<tr>"):
        if "skrasnr" not in chunk or "class=head" in chunk:
            continue
        row = {"skrasnr": None, "felag_id": None, "felag_token": None,
               "name": None, "day": None, "lysing_id": None,
               "lysing_token": None, "lysing_text": None, "is_current": False}
        m = re.search(r"""<td\s+class=["']?skrasnr["']?\s*>\s*([^<]+?)\s*<td""", chunk)
        if not m:
            continue
        row["skrasnr"] = m.group(1)
        m = re.search(r"skrPopup\(1,(\d+),'([^']+)'\)", chunk)
        if m:
            row["felag_id"], row["felag_token"] = m.group(1), m.group(2)
        m = re.search(r"skrPopup\(1,\d+,'[^']+\'\)\">([^<]*)", chunk)
        if m:
            row["name"] = m.group(1)
        m = re.search(r"""<td\s+class=["']?day["']?\s*>\s*([^<]*?)\s*(?:<|$)""", chunk)
        if m:
            row["day"] = m.group(1) or None
        m = re.search(r"skrPopup\(0,(\d+),'([^']+)'\)", chunk)
        if m:
            row["lysing_id"], row["lysing_token"] = m.group(1), m.group(2)
            m2 = re.search(r"skrPopup\(0,\d+,'[^']+\'\)\">([^<]*)", chunk)
            if m2:
                row["lysing_text"] = m2.group(1)
        if not row["day"] and not row["lysing_id"]:
            row["is_current"] = True
        rows.append(row)
    pages = {}
    for m in PAGINATION_RE.finditer(html):
        href, page_no = m.group(1), m.group(2) or m.group(3)
        if href and page_no:
            pages[int(page_no)] = href.replace("&amp;", "&")
    return {"count": count, "rows": rows, "pages": pages}


def normalize_regnr(text: str) -> str:
    digits = re.sub(r"\D", "", unicodedata.normalize("NFC", text or ""))
    return digits


def row_source_key(regnr: str, row: dict) -> str:
    if row["lysing_id"]:
        return f"sg.vs.fo#{regnr}#ev{row['lysing_id']}"
    if row["is_current"]:
        return f"sg.vs.fo#{regnr}#current"
    digest = hashlib.sha256(
        f"{regnr}|{row['day']}|{row['name']}".encode("utf-8")).hexdigest()[:8]
    return f"sg.vs.fo#{regnr}#nr{digest}"


def iso_day(day: str | None) -> str | None:
    if not day:
        return None
    m = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{4})$", day.strip())
    if not m:
        return None
    return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1)))


# ---------------------------------------------------------------------------
# Pilot DB writes (only the 7 pdf_* tables)
# ---------------------------------------------------------------------------

def log_step(conn, company_id, document_id, step, status, error_type, detail):
    conn.execute(
        "INSERT INTO pdf_run_log (run_id, company_id, document_id, step,"
        " status, error_type, detail) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (PILOT_RUN, company_id, document_id, step, status, error_type,
         detail))


def store_source_event(conn, regnr: str, row: dict, page_no: int,
                       ordinal: int) -> int:
    key = row_source_key(regnr, row)
    conn.execute(
        "INSERT INTO pdf_source_event (company_id, requested_regnr,"
        " row_skrasnr, source_row_key, source_event_date, raw_text, page_no,"
        " row_ordinal) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (source_row_key) DO NOTHING",
        (int(regnr), regnr, row["skrasnr"], key, iso_day(row["day"]),
         row["lysing_text"] or row["name"] or "", page_no, ordinal))
    return conn.execute(
        "SELECT id FROM pdf_source_event WHERE source_row_key = %s",
        (key,)).fetchone()[0]


def ensure_document(conn, pdf_type: str, source_document_id: str,
                    kind_hint: str) -> int:
    conn.execute(
        "INSERT INTO pdf_document (source_system, pdf_type,"
        " source_document_id, pdf_kind_hint) VALUES ('sg.vs.fo',%s,%s,%s)"
        " ON CONFLICT (source_system, pdf_type, source_document_id)"
        " DO NOTHING", (pdf_type, source_document_id, kind_hint))
    return conn.execute(
        "SELECT id FROM pdf_document WHERE source_system = 'sg.vs.fo'"
        " AND pdf_type = %s AND source_document_id = %s",
        (pdf_type, source_document_id)).fetchone()[0]


def link_event_document(conn, event_id: int, document_id: int, cell_role: str):
    conn.execute(
        "INSERT INTO pdf_source_event_document (source_event_id, document_id,"
        " cell_role) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
        (event_id, document_id, cell_role))


# ---------------------------------------------------------------------------
# PDF fetch + validation + ingest
# ---------------------------------------------------------------------------

def build_api_url(ref: dict) -> str:
    """Popup URL EXACTLY as the live inline JS builds it:

        method = method===0 ? 'LysingPdf' : 'FelagPdf'
        url = '/api/Skraseting/' + method + '?id=' + id + '&s=' + s

    (verified in tmp-pdf-recon/search_10_p1.html; the path segment is the
    WORD, not the digit - round 1 of the pilot proved the digit form 404s).
    The token is ephemeral and must never be logged: use _url_for_log()."""
    return (f"{BASE}/api/Skraseting/{ref['pdf_type']}?id={ref['api_id']}"
            f"&s={ref['token']}")


def fetch_pdf_document(regnr: str, ref: dict, referer: str):
    """Fetch one logical document (method/id/token). Returns a dict.

    Validation gate: HTTP 200 + plausible content type + body begins %PDF-.
    A 200 HTML body is treated as TOKEN_INVALID suspect -> one fresh search
    page + one retry with the fresh token (F12); second failure -> FAILED.
    """
    outcome = {"status": None, "body": None, "content_type": None,
               "token_refreshed": False}
    for round_no in (1, 2):
        url = build_api_url(ref)
        status, body, headers = http_get(url, "pdf", referer=referer)
        outcome["status"] = status
        outcome["content_type"] = (headers.get("Content-Type")
                                    or headers.get("content-type"))
        if status == 200 and body.startswith(b"%PDF-"):
            outcome["body"] = body
            return outcome
        if status == 200 and not body.startswith(b"%PDF-"):
            # HTML/JSON/error payload: never parser input
            outcome["body"] = None
            if round_no == 1:
                # one fresh search, then one retry with the fresh token
                fresh = search_page_for_tokens(regnr)
                if fresh and ref.get("row_key"):
                    new_row = fresh["rows_by_key"].get(ref["row_key"])
                    if new_row and new_row.get("token_for_ref"):
                        ref["token"] = new_row["token_for_ref"]
                        outcome["token_refreshed"] = True
                        continue
            outcome["error"] = "INVALID_PDF_RESPONSE"
            return outcome
        outcome["body"] = None
        outcome["error"] = (status.split(":")[0] if isinstance(status, str)
                            else f"HTTP_{status}")
        return outcome
    return outcome


def search_page_for_tokens(regnr: str):
    """One fresh search (page 1) used ONLY to refresh an invalid token."""
    url = f"{BASE}{SEARCH_PATH}?name={regnr}&fromDate=&toDate="
    status, body, _ = http_get(url, "search")
    if status != 200:
        return None
    page = parse_search_page(body.decode("utf-8", "replace"))
    rows_by_key = {}
    for row in page["rows"]:
        key = row_source_key(regnr, row)
        if row["lysing_id"]:
            rows_by_key.setdefault(key, {"token_for_ref":
                                          row["lysing_token"]})
        else:
            rows_by_key.setdefault(key, {"token_for_ref":
                                          row["felag_token"]})
        if row["felag_token"]:
            # the company-level FelagPdf token sits on every row's name cell
            rows_by_key.setdefault(key + "#felag",
                                   {"token_for_ref": row["felag_token"]})
    page["rows_by_key"] = rows_by_key
    return page


def detect_content_changed(conn, document_id: int, new_sha: str) -> dict | None:
    rows = conn.execute(
        "SELECT sha256, fetched_at FROM pdf_document_observation"
        " WHERE document_id = %s ORDER BY fetched_at, id", (document_id,)
    ).fetchall()
    shas = {r[0] for r in rows}
    if len(shas) > 1:
        old = [r[0] for r in rows if r[0] != new_sha]
        return {"document_id": document_id, "old_sha256": old[0] if old
                else None, "new_sha256": new_sha,
                "observations": len(rows)}
    return None


def same_hash_prior(conn, document_id: int, sha: str) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM pdf_document_observation"
        " WHERE document_id = %s AND sha256 = %s AND collection_run_id <> %s",
        (document_id, sha, PILOT_RUN)).fetchone()
    return row[0] > 0


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

metrics = {
    "companies_attempted": 0, "companies_completed": 0,
    "companies_stopped_review": 0, "source_rows_found": 0,
    "source_rows_verified": 0, "source_rows_rejected_skrasnr": 0,
    "pdf_refs_found": 0, "logical_documents": 0, "pdf_requests": 0,
    "valid_pdf_responses": 0, "invalid_pdf_responses": 0,
    "unique_content_hashes": 0, "same_hash_repeat_observations": 0,
    "content_changed_cases": [], "felag_count": 0, "lysing_count": 0,
    "companies": [], "hard_stop": None, "search_requests": 0,
}


def process_company(conn, regnr: str) -> dict:
    record = {"regnr": regnr, "status": "COMPLETED",
              "count_advertised": None, "pages_fetched": 1,
              "rows_found": 0, "rows_verified": 0,
              "rows_rejected_skrasnr": 0, "events_stored": 0,
              "pdf_refs_found": 0, "docs_fetched": 0, "docs_failed": 0,
              "refs_unfetched": 0, "notes": []}
    metrics["companies_attempted"] += 1

    # -- page 1 (exact URL form from the owner task) -------------------------
    page1_url = f"{BASE}{SEARCH_PATH}?name={regnr}&fromDate=&toDate="
    status, body, _ = http_get(page1_url, "search")
    if status != 200:
        record["status"] = "ABORTED_SEARCH_FAILED"
        record["notes"].append(f"page1 HTTP {status}")
        metrics["companies_stopped_review"] += 1
        return record
    page = parse_search_page(body.decode("utf-8", "replace"))
    record["count_advertised"] = page["count"]
    all_rows = list(page["rows"])
    for row in all_rows:
        row["_page"] = 1
    referers = {1: page1_url}

    # -- remaining pages (deterministic ascending traversal) -----------------
    page_numbers = sorted(p for p in page["pages"] if p != 1)
    for page_no in page_numbers:
        url = f"{BASE}{SEARCH_PATH}{page['pages'][page_no]}"
        status, body, _ = http_get(url, "search", referer=page1_url)
        if status != 200:
            record["status"] = "ABORTED_SEARCH_FAILED"
            record["notes"].append(f"page{page_no} HTTP {status}")
            metrics["companies_stopped_review"] += 1
            return record
        record["pages_fetched"] += 1
        referers[page_no] = url
        parsed = parse_search_page(body.decode("utf-8", "replace"))
        for row in parsed["rows"]:
            row["_page"] = page_no
        all_rows.extend(parsed["rows"])

    # -- attribution gate: exact skrasnr verification per row -----------------
    verified, rejected = [], []
    for row in all_rows:
        (verified if row["skrasnr"] and normalize_regnr(row["skrasnr"])
         == regnr else rejected).append(row)
    record["rows_found"] = len(all_rows)
    record["rows_verified"] = len(verified)
    record["rows_rejected_skrasnr"] = len(rejected)
    metrics["source_rows_found"] += len(all_rows)
    metrics["source_rows_verified"] += len(verified)
    metrics["source_rows_rejected_skrasnr"] += len(rejected)
    for row in rejected:
        log_step(conn, int(regnr), None, "EVENT", "REVIEW",
                 "SOURCE_COMPANY_MISMATCH",
                 f"row skrasnr {row['skrasnr']!r} != requested {regnr!r}")

    # -- dedupe across pages, keep first occurrence + page provenance --------
    unique_rows = {}
    for row in verified:
        key = row_source_key(regnr, row)
        if key not in unique_rows:
            unique_rows[key] = row
    current_rows = [r for r in unique_rows.values() if r["is_current"]]
    if len(current_rows) > 1:
        record["notes"].append(
            f"{len(current_rows)} undated current-like rows seen (expected 1)")

    # -- pagination reconciliation -------------------------------------------
    # Page model (live-verified pilot + 100-company validation): pages hold
    # up to 20 DATED rows plus one undated current row per page; the source's
    # own pagination links are the page truth. count = dated + current(once).
    rows_seen = len(unique_rows)
    count = record["count_advertised"]
    linked_pages = sorted(page["pages"]) if isinstance(page, dict) else []
    expected_pages = max(linked_pages) if linked_pages else 1
    pagination_ok = (count is not None and rows_seen == count
                     and expected_pages == record["pages_fetched"])
    if not pagination_ok:
        record["status"] = "PAGINATION_MISMATCH"
        record["notes"].append(
            f"rows_seen={rows_seen} count={count} pages={record['pages_fetched']}"
            f" expected_pages={expected_pages}")
        # forensic evidence: row identities WITHOUT tokens (day/event id only)
        row_evidence = []
        for row in verified[:25]:
            row_evidence.append(
                f"day={row['day'] or '-'} lysing={row['lysing_id'] or '-'}"
                f" current={row['is_current']}")
        log_step(conn, int(regnr), None, "PAGINATION", "STOPPED",
                 "PAGINATION_MISMATCH",
                 f"rows_seen={rows_seen} advertised={count}"
                 f" rows={row_evidence}")
        metrics["companies_stopped_review"] += 1
        return record
    log_step(conn, int(regnr), None, "PAGINATION", "OK", None,
             f"rows_seen={rows_seen} == advertised={count};"
             f" pages={record['pages_fetched']}")

    # -- store verified source rows (evidence, also rows without PDF) --------
    stored_event_ids = {}
    ordinal = 0
    for row in verified:
        key = row_source_key(regnr, row)
        if key in stored_event_ids:
            continue
        ordinal += 1
        stored_event_ids[key] = store_source_event(
            conn, regnr, row, row.get("_page", 1), ordinal)
    record["events_stored"] = len(stored_event_ids)

    # -- PDF refs: felag (company-level, on every row) + lysing (per event) --
    felag_rows = [r for r in verified if r["felag_id"]]
    felag_id = felag_rows[0]["felag_id"] if felag_rows else None
    refs = []
    if felag_id:
        first_key = row_source_key(regnr, felag_rows[0])
        refs.append({"method": "1", "api_id": felag_id,
                     "token": felag_rows[0]["felag_token"],
                     "pdf_type": "FelagPdf", "row_key": first_key + "#felag",
                     "event_key": row_source_key(regnr, felag_rows[0])})
        metrics["felag_count"] += 1
    lysing_ids = []
    for key, row in unique_rows.items():
        if row["lysing_id"]:
            lysing_ids.append(row["lysing_id"])
            refs.append({"method": "0", "api_id": row["lysing_id"],
                         "token": row["lysing_token"],
                         "pdf_type": "LysingPdf", "row_key": key,
                         "event_key": key})
            metrics["lysing_count"] += 1
    record["pdf_refs_found"] = len(refs)
    metrics["pdf_refs_found"] += len(refs)
    metrics["logical_documents"] += len({(r["pdf_type"], r["api_id"])
                                         for r in refs})

    # -- logical documents + junctions BEFORE any fetch outcome --------------
    for ref in refs:
        kind_hint = ("FULL_STATE_SNAPSHOT"
                     if ref["pdf_type"] == "FelagPdf"
                     else "REGISTRATION_NOTICE")
        ref["document_id"] = ensure_document(conn, ref["pdf_type"],
                                             ref["api_id"], kind_hint)
        if ref["pdf_type"] == "FelagPdf":
            # company-level printout: referenced from EVERY verified row
            for key, event_id in stored_event_ids.items():
                link_event_document(conn, event_id, ref["document_id"],
                                    "FELAG_CELL")
        else:
            link_event_document(conn,
                                 stored_event_ids[ref["event_key"]],
                                 ref["document_id"], "LYSING_CELL")

    # -- deterministic fetch set --------------------------------------------
    fetch_set = [r for r in refs if r["pdf_type"] == "FelagPdf"]
    bound = FETCH_BOUND.get(regnr)
    if bound is not None:
        fetch_set += [r for r in refs if r["pdf_type"] == "LysingPdf"
                      and r["api_id"] in bound]
        newest = None
        for r in refs:
            if r["pdf_type"] != "LysingPdf":
                continue
            row = unique_rows[r["event_key"]]
            # days are DD-MM-YYYY: compare as ISO (YYYY-MM-DD), not as text
            day_iso = iso_day(row["day"]) or ""
            if newest is None or day_iso > (iso_day(
                    unique_rows[newest["event_key"]]["day"]) or ""):
                newest = r
        if newest and newest["api_id"] not in bound:
            fetch_set.append(newest)
            record["notes"].append(f"newest lysing {newest['api_id']} added")
    else:
        fetch_set += [r for r in refs if r["pdf_type"] == "LysingPdf"]
    if LYSING_FETCH_CAP is not None:
        lysing_in_set = [r for r in fetch_set
                         if r["pdf_type"] == "LysingPdf"]
        if len(lysing_in_set) > LYSING_FETCH_CAP:
            def _cap_key(r):
                row = unique_rows.get(r["event_key"])
                return (iso_day(row["day"]) or "", r["api_id"])
            keep = {r["api_id"] for r in sorted(
                lysing_in_set, key=_cap_key, reverse=True)[:LYSING_FETCH_CAP]}
            fetch_set = [r for r in fetch_set
                         if r["pdf_type"] == "FelagPdf" or r["api_id"] in keep]
            record["notes"].append(
                f"LYSING_FETCH_CAP {LYSING_FETCH_CAP} applied"
                f" ({len(lysing_in_set)} lysing refs)")
    fetch_set.sort(key=lambda r: (0 if r["pdf_type"] == "FelagPdf" else 1,
                                  r["api_id"]))
    record["refs_unfetched"] = len(refs) - len(fetch_set)

    # -- fetch + ingest -------------------------------------------------------
    for ref in fetch_set:
        referer = page1_url
        outcome = fetch_pdf_document(regnr, ref, referer)
        metrics["pdf_requests"] = budget["pdf"]
        if outcome["body"] is None:
            record["docs_failed"] += 1
            metrics["invalid_pdf_responses"] += 1
            log_step(conn, int(regnr), ref["document_id"], "FETCH", "FAILED",
                     outcome.get("error", "INVALID_PDF_RESPONSE"),
                     f"api_id={ref['api_id']} method={ref['method']}"
                     f" content_type={outcome.get('content_type')}")
            continue
        record["docs_fetched"] += 1
        metrics["valid_pdf_responses"] += 1
        row = unique_rows.get(ref["event_key"])
        event_dict = {
            "row_skrasnr": regnr,
            "source_row_key": ref["event_key"],
            "source_event_date": iso_day(row["day"]) if row else None,
            "raw_text": (row["lysing_text"] or row["name"]) if row else "",
            "page_no": 1, "row_ordinal": 1,
        }
        sha = hashlib.sha256(outcome["body"]).hexdigest()
        if same_hash_prior(conn, ref["document_id"], sha):
            metrics["same_hash_repeat_observations"] += 1
        result = ingest_pdf_observation(
            conn=conn, company_id=int(regnr), source_event=event_dict,
            pdf_type=ref["pdf_type"], source_document_id=ref["api_id"],
            pdf_bytes=outcome["body"], collection_run_id=PILOT_RUN)
        changed = detect_content_changed(conn, ref["document_id"], sha)
        if changed:
            metrics["content_changed_cases"].append(
                {"regnr": regnr, "pdf_type": ref["pdf_type"],
                 "api_id": ref["api_id"], **changed})
            log_step(conn, int(regnr), ref["document_id"], "FETCH", "WARN",
                     "CONTENT_CHANGED",
                     f"old_sha256={changed['old_sha256']}"
                     f" new_sha256={changed['new_sha256']}")
        log_step(conn, int(regnr), ref["document_id"], "REVIEW", "OK", None,
                 f"parse_status={result['parse_status']}"
                 f" reused_observation={result['reused_observation']}")
    if record["refs_unfetched"]:
        record["status"] = "COMPLETED_WITH_FETCH_BOUND"
        record["notes"].append(
            f"PILOT_FETCH_BOUND: {record['refs_unfetched']} lysing refs"
            " recorded but not fetched (deterministic pilot bound)")
    metrics["companies_completed"] += 1
    return record


def main(dsn: str | None = None) -> int:
    exit_code = 0
    with psycopg.connect(dsn or pilot_dsn()) as conn:
        for regnr in PILOT_COMPANIES:
            try:
                record = process_company(conn, regnr)
            except CompanyAbort as abort:
                record = {"regnr": regnr, "status": "ABORTED_5XX",
                          "notes": [str(abort)]}
                metrics["companies_stopped_review"] += 1
            except HardStop as stop:
                metrics["hard_stop"] = str(stop)
                metrics["companies"].append(
                    {"regnr": regnr, "status": "STOPPED_BY_HARD_STOP"})
                exit_code = 2
                break
            metrics["companies"].append(record)
            conn.commit()
        conn.commit()

        # -- DB-derived metrics ----------------------------------------------
        row = conn.execute(
            "SELECT count(DISTINCT sha256) FROM pdf_document_content"
        ).fetchone()
        metrics["unique_content_hashes"] = row[0]
        parses = conn.execute(
            "SELECT parse_status, count(*) FROM pdf_parse_result GROUP BY 1"
        ).fetchall()
        metrics["parse_status_counts"] = {r[0]: r[1] for r in parses}
        obs = conn.execute(
            "SELECT collection_run_id, count(*) FROM pdf_document_observation"
            " GROUP BY 1 ORDER BY 1").fetchall()
        metrics["observations_per_run"] = {str(r[0]): r[1] for r in obs}
        queue = conn.execute(
            "SELECT issue, count(*) FROM pdf_review_queue GROUP BY 1"
            " ORDER BY 1").fetchall()
        metrics["review_queue"] = {r[0]: r[1] for r in queue}
        unknown = conn.execute(
            "SELECT count(*) FROM pdf_parse_result WHERE"
            " structured_data ? 'unknown_sections'").fetchone()
        metrics["parse_results_with_unknown_sections"] = unknown[0]
    metrics["search_requests"] = budget["search"]
    metrics["pdf_requests"] = budget["pdf"]
    metrics["budget"] = dict(budget)
    with open("pilot_metrics.json", "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=1, default=str)
    print(json.dumps(metrics, indent=1, default=str))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
