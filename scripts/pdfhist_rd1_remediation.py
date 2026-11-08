"""RD-1 remediation runner (workstream C-3).

Modes:
  remediate  - c1062, c2321, c2341: full pipeline re-attempt with the
               marker/duplicate-aware pagination rule (run 424246).
  observe    - c6692: ONE controlled live search observation, classify
               EXPECTED_NO_SOURCE_ROWS / SOURCE_DATA_ANOMALY / CRAWLER_EDGE_CASE
               / OTHER, no events or documents are written for it.

Safety unchanged from pdfhist_live_pilot: concurrency 1 (process serial),
SEARCH_GAP 1.0s, PDF_GAP 1.2s, 403/429/Retry-After -> HardStop -> exit 2
(STOP of the ENTIRE C-3 workstream), 5xx repeat -> CompanyAbort, request
budget caps kept. Attribution gate unchanged: per-row skrasnr exact match.

Rule (scripts/pdfhist_rd1_pagination.py): advertised == RAW verified rows
(dedup NOT applied pre-reconciliation); exactly one current marker expected;
duplicates = exact-identity EMPTY rows, classified explicitly, collapse only
at storage (carrying no document refs). No fuzzy matching anywhere.
"""
from __future__ import annotations

import json
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import pdfhist_live_pilot as pilot
from pdfhist_live_pilot import (
    BASE, SEARCH_PATH, HardStop, CompanyAbort, http_get, parse_search_page,
    normalize_regnr, row_source_key, store_source_event, ensure_document,
    link_event_document, fetch_pdf_document, same_hash_prior,
    detect_content_changed, iso_day, log_step, budget)
from pdfhist_rd1_pagination import classify_rows, marker_aware_reconciliation
from heimdal.pdfhist.ingest import ingest_pdf_observation

pilot.PILOT_RUN = 424246
pilot.FETCH_BOUND = {}
pilot.LYSING_FETCH_CAP = None
PILOT_RUN = pilot.PILOT_RUN

# budget headroom for 41 planned refs + retry margin (kept, not removed)
pilot.PDF_BUDGET = 60
pilot.SEARCH_BUDGET = 12
pilot.TOTAL_BUDGET = 75

REMEDIATION_COMPANIES = ["1062", "2321", "2341"]
OBSERVE_COMPANY = "6692"

metrics = pilot.metrics
metrics["companies"] = []


def _pagination_block(conn, record, regnr, verified, page, unique_rows):
    """Marker/duplicate-aware replacement of the old rows_seen==count rule."""
    advertised = record["count_advertised"]
    linked_pages = sorted(page["pages"]) if isinstance(page, dict) else []
    expected_pages = max(linked_pages) if linked_pages else 1
    res = marker_aware_reconciliation(
        verified, advertised, record["pages_fetched"], expected_pages)
    d = res["detail"]
    record["rd1"] = {
        "raw_verified_rows": len(verified),
        "document_rows": d["document_rows"],
        "empty_dated_rows": d["row_type_counts"]["EMPTY_DATED_ROW"],
        "current_markers": d["row_type_counts"]["CURRENT_MARKER"],
        "substantive_rows": d["substantive_rows"],
        "duplicate_empty_rows": d["duplicate_empty_rows"],
        "denominator": (advertised - d["row_type_counts"]["CURRENT_MARKER"])
        if advertised is not None else None,
}
    if not res["ok"]:
        record["status"] = "PAGINATION_MISMATCH"
        record["notes"].append(
            f"rd1 reason={res['reason']} raw={d['raw_rows']}"
            f" advertised={advertised}")
        row_evidence = [f"day={r['day'] or '-'} lysing={r['lysing_id'] or '-'}"
                        f" current={r['is_current']}" for r in verified[:25]]
        log_step(conn, int(regnr), None, "PAGINATION", "STOPPED",
                 "PAGINATION_MISMATCH",
                 f"rows_raw={len(verified)} advertised={advertised}"
                 f" rows={row_evidence}" )
        metrics["companies_stopped_review"] += 1
        return False
    log_step(conn, int(regnr), None, "PAGINATION", "OK", None,
             f"rd1 raw_rows={d['raw_rows']} == advertised={advertised};"
             f" marker={d['row_type_counts']['CURRENT_MARKER']}"
             f" empty={d['row_type_counts']['EMPTY_DATED_ROW']}"
             f" dup_empty={len(d['duplicate_empty_rows'])};"
             f" pages={record['pages_fetched']}/{expected_pages}")
    return True


def process_company_rd1(conn, regnr: str) -> dict:
    """Copy of pilot.process_company with the RD-1 pagination rule.
    All other behavior (attribution gate, dedupe, refs, fetch, ingest,
    company_pdf_snapshot_history via ingest) is inherited unchanged."""
    record = {"regnr": regnr, "status": "", "notes": [],
              "pages_fetched": 1, "count_advertised": None,
              "pdf_refs_found": 0, "docs_fetched": 0,
              "docs_failed": 0, "events_stored": 0, "refs_unfetched": 0}
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
    for page_no in sorted(p for p in page["pages"] if p != 1):
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

    # attribution gate unchanged: exact skrasnr per row
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

    # dedupe across pages, keep first occurrence + page provenance (storage)
    unique_rows = {}
    for row in verified:
        key = row_source_key(regnr, row)
        if key not in unique_rows:
            unique_rows[key] = row
    current_rows = [r for r in unique_rows.values() if r["is_current"]]
    if len(current_rows) > 1:
        record["notes"].append(
            f"{len(current_rows)} undated current-like rows seen (expected 1)")

    # ---- RD-1 pagination reconciliation (the ONLY semantic change) --------
    if not _pagination_block(conn, record, regnr, verified, page, unique_rows):
        return record

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
    for key, row in unique_rows.items():
        if row["lysing_id"]:
            refs.append({"method": "0", "api_id": row["lysing_id"],
                         "token": row["lysing_token"],
                         "pdf_type": "LysingPdf", "row_key": key,
                         "event_key": key})
            metrics["lysing_count"] += 1
    record["pdf_refs_found"] = len(refs)
    metrics["pdf_refs_found"] += len(refs)
    metrics["logical_documents"] += len({(r["pdf_type"], r["api_id"])
                                         for r in refs})

    for ref in refs:
        kind_hint = ("FULL_STATE_SNAPSHOT" if ref["pdf_type"] == "FelagPdf"
                     else "REGISTRATION_NOTICE")
        ref["document_id"] = ensure_document(conn, ref["pdf_type"],
                                             ref["api_id"], kind_hint)
        if ref["pdf_type"] == "FelagPdf":
            for key, event_id in stored_event_ids.items():
                link_event_document(conn, event_id, ref["document_id"],
                                    "FELAG_CELL")
        else:
            link_event_document(conn, stored_event_ids[ref["event_key"]],
                                ref["document_id"], "LYSING_CELL")

    fetch_set = [r for r in refs if r["pdf_type"] == "FelagPdf"]
    fetch_set += [r for r in refs if r["pdf_type"] == "LysingPdf"]
    fetch_set.sort(key=lambda r: (0 if r["pdf_type"] == "FelagPdf" else 1,
                                  r["api_id"]))
    record["refs_unfetched"] = len(refs) - len(fetch_set)

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
        sha = __import__("hashlib").sha256(outcome["body"]).hexdigest()
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
                     f" new_sha256={changed['new_sha256']}" )
        log_step(conn, int(regnr), ref["document_id"], "REVIEW", "OK", None,
                 f"parse_status={result['parse_status']}"
                 f" reused_observation={result['reused_observation']}")
    metrics["companies_completed"] += 1
    return record


def observe_c6692(conn) -> dict:
    """ONE controlled live observation; classification only, no writes."""
    record = {"regnr": OBSERVE_COMPANY, "mode": "OBSERVATION_ONLY",
              "writes": "NONE", "notes": []}
    url = f"{BASE}{SEARCH_PATH}?name={OBSERVE_COMPANY}&fromDate=&toDate="
    status, body, _ = http_get(url, "search")
    record["http_status"] = status
    with open("rd1_c6692_search_raw.html", "wb") as fh:
        fh.write(body or b"")
    if status != 200:
        record["classification"] = "CRAWLER_EDGE_CASE"
        record["notes"].append(f"HTTP {status}; no evidence available")
        return record
    page = parse_search_page(body.decode("utf-8", "replace"))
    rows = page["rows"]
    d = classify_rows(rows)
    record.update({"count_advertised": page["count"],
                   "raw_rows": len(rows), "rd1": d})
    if d["raw_rows"] == 0 and page["count"] in (0, None):
        record["classification"] = "EXPECTED_NO_SOURCE_ROWS"
    elif d["raw_rows"] == 0 and (page["count"] or 0) > 0:
        record["classification"] = "CRAWLER_EDGE_CASE"
    elif d["substantive_rows"] > 0:
        record["classification"] = "SOURCE_DATA_ANOMALY"
        record["notes"].append(
            "rows now returned; single observation did NOT ingest them")
    else:
        record["classification"] = "OTHER"
    return record


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "remediate"
    exit_code = 0
    with pilot.psycopg.connect(pilot.PILOT_DSN) as conn:
        targets = ([OBSERVE_COMPANY] if mode == "observe"
                   else REMEDIATION_COMPANIES)
        for regnr in targets:
            try:
                if mode == "observe":
                    record = observe_c6692(conn)
                else:
                    record = process_company_rd1(conn, regnr)
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
    metrics["search_requests"] = budget["search"]
    metrics["pdf_requests"] = budget["pdf"]
    metrics["budget"] = dict(budget)
    out = (f"rd1_c6692_observation.json" if mode == "observe"
           else "rd1_remediation_metrics.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=1, default=str)
    print(json.dumps(metrics, indent=1, default=str))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())