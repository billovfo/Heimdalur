"""10-random-company blind data audit export (READ ONLY).

Reads audit_selection_250.json (computed from seed
'heimdal-250-audit-2026-08-31' before any company data was inspected),
exports ALL stored/derived pdf-historik data for the 10 selected companies to:

  docs/HEIMDAL-PDF-HISTORIK-10-RANDOM-DATA-AUDIT-v1.json  (lossless)

NO live requests, NO parser changes, NO reparse, NO DB mutation.
Selection: sha256(seed + '|' + registration_number), ascending, first 10.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg

from heimdal.pdfhist.temporal import (
    SCALAR_FAMILY_MAP, build_change_history)
from heimdal.pdfhist.testsupport import FIXTURE_DIR as FIXTURES_DIR  # real PDFs: outside git (I-5)

DSN = "postgresql://postgres@localhost:5432/pdfhist_pilot"
SELECTION_FILE = "audit_selection_250.json"
JSON_OUT = Path("docs/HEIMDAL-PDF-HISTORIK-10-RANDOM-DATA-AUDIT-v1.json")

REVIEW_DIR = Path("tmp-250val-review")


def js(value):
    """JSON-safe conversion of DB values (deterministic, lossless)."""
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, memoryview):
        return f"<bytea {len(bytes(value))} bytes>"
    return value


def load_selection():
    doc = json.loads(Path(SELECTION_FILE).read_text())
    return doc, doc["companies"][:10]


def local_evidence_paths(content_bytes, extracted_text):
    """Local evidence references matching this blob/text exactly."""
    paths = []
    digest = hashlib.sha256(content_bytes).hexdigest()
    if FIXTURES_DIR.exists():
        for p in sorted(FIXTURES_DIR.glob("*.bin")):
            try:
                if hashlib.sha256(p.read_bytes()).hexdigest() == digest:
                    paths.append(p.as_posix())
            except OSError:
                continue
    if REVIEW_DIR.exists():
        text_digest = hashlib.sha256(
            (extracted_text or "").encode("utf-8")).hexdigest()
        for p in sorted(REVIEW_DIR.glob("*.txt")):
            try:
                if hashlib.sha256(p.read_bytes()).hexdigest() == text_digest:
                    paths.append(p.as_posix())
            except OSError:
                continue
    return paths


def export_company(cur, company_row):
    company_id = company_row["company_id"]
    regnr = company_row["registration_number"]
    data = {
        "company_id": company_id,
        "registration_number": regnr,
        "entity_type": company_row["entity_type"],
        "selection_rank": company_row["rank"],
        "selection_hash": company_row["selection_hash"],
    }

    # -- A. identification ----------------------------------------------------
    snapshot_rows = cur.execute("""
        SELECT s.document_observation_id, s.parser_version, s.valid_from,
               s.valid_to, s.sha256, s.fetched_at, s.snapshot_date_conflict,
               s.structured_data
        FROM company_pdf_snapshot_history s
        WHERE s.company_id = %s
        ORDER BY s.valid_from, s.fetched_at""", (company_id,)).fetchall()

    latest = cur.execute("""
        SELECT p.structured_data->>'company_name'
        FROM pdf_parse_result p
        JOIN pdf_document_observation o ON o.id = p.document_observation_id
        JOIN pdf_document d ON d.id = o.document_id
        JOIN pdf_source_event_document sed ON sed.document_id = d.id
        JOIN pdf_source_event e ON e.id = sed.source_event_id
        WHERE e.company_id = %s AND d.pdf_type = 'FelagPdf'
        ORDER BY o.fetched_at DESC NULLS LAST, o.id DESC LIMIT 1""",
        (company_id,)).fetchone()
    data["company_name_latest"] = latest[0] if latest else None

    # -- B. source events (chronological) ------------------------------------
    events = cur.execute("""
        SELECT id, company_id, requested_regnr, row_skrasnr, source_row_key,
               source_event_date, page_no, row_ordinal, discovered_at, raw_text
        FROM pdf_source_event
        WHERE company_id = %s
        ORDER BY source_event_date, page_no, row_ordinal, id""",
        (company_id,)).fetchall()
    data["source_events"] = [
        {"source_event_id": r[0], "company_id": r[1],
         "requested_regnr": r[2], "row_skrasnr": r[3],
         "source_row_key": r[4], "source_event_date": js(r[5]),
         "page_no": r[6], "row_ordinal": r[7],
         "discovered_at": js(r[8]), "raw_text": r[9]}
        for r in events]

    # -- C/D. logical documents, relations, observations, parses ---------------
    docs = []
    doc_rows = cur.execute("""
        SELECT d.id, d.source_system, d.pdf_type, d.source_document_id,
               d.pdf_kind_hint, d.fetch_policy
        FROM pdf_document d
        JOIN pdf_source_event_document sed ON sed.document_id = d.id
        JOIN pdf_source_event e ON e.id = sed.source_event_id
        WHERE e.company_id = %s
        GROUP BY d.id, d.source_system, d.pdf_type, d.source_document_id,
                 d.pdf_kind_hint, d.fetch_policy
        ORDER BY d.id""", (company_id,)).fetchall()
    for (doc_id, src_system, pdf_type, source_doc_id, kind_hint,
         fetch_policy) in doc_rows:
        rels = cur.execute("""
            SELECT r.source_event_id, r.cell_role, e.row_skrasnr,
                   e.source_event_date
            FROM pdf_source_event_document r
            JOIN pdf_source_event e ON e.id = r.source_event_id
            WHERE r.document_id = %s
            ORDER BY e.source_event_date, e.row_ordinal""", (doc_id,)).fetchall()
        obs_rows = cur.execute("""
            SELECT o.id, o.document_id, o.collection_run_id, o.fetched_at,
                   o.document_snapshot_date, o.sha256, o.byte_length,
                   o.extracted_text
            FROM pdf_document_observation o
            WHERE o.document_id = %s
            ORDER BY o.fetched_at, o.id""", (doc_id,)).fetchall()
        observations = []
        for (oid, did, run, fetched, snap_date, sha, blen, text) in obs_rows:
            prow = cur.execute("""
                SELECT sha256, byte_length, content
                FROM pdf_document_content WHERE sha256 = %s""", (sha,)).fetchone()
            if prow is None:
                content = {"sha256": sha, "byte_length": blen,
                           "verified": False, "verify_note":
                           "no pdf_document_content row for this sha256",
                           "local_evidence_paths": []}
            else:
                raw = prow[2]
                recomputed = hashlib.sha256(raw).hexdigest()
                length_ok = bool(recomputed == prow[0]
                                 and len(raw) == prow[1] == blen)
                length_ok = recomputed == prow[0] and \
                    prow[1] == blen and len(raw) == blen
                content = {
                    "sha256": prow[0], "byte_length": prow[1],
                    "verified": length_ok,
                    "sha256_recomputed_from_stored_bytes": recomputed,
                    "byte_length_matches": len(raw) == prow[1] == blen,
                    "local_evidence_paths":
                        local_evidence_paths(raw, text)}
            prs = cur.execute("""
                SELECT id, document_observation_id, parser_version,
                       document_kind, parse_status, needs_review,
                       review_reason, parsed_at, raw_text, structured_data
                FROM pdf_parse_result
                WHERE document_observation_id = %s
                ORDER BY id""", (oid,)).fetchall()
            parse_results = []
            for (pr_id, pr_obs, pv, kind, status, needs_review, reason,
                 parsed_at, raw_text, sdata) in prs:
                parse_results.append({
                    "parse_result_id": pr_id,
                    "document_observation_id": pr_obs,
                    "parser_version": pv or "1",
                    "document_kind": kind,
                    "parse_status": status,
                    "needs_review": needs_review,
                    "review_reason": reason,
                    "parsed_at": js(parsed_at),
                    "raw_text": raw_text,
                    "structured_data": sdata})
            observations.append({
                "observation_id": oid,
                "document_id": did,
                "collection_run_id": run,
                "fetched_at": js(fetched),
                "document_snapshot_date": js(snap_date),
                "sha256": sha,
                "byte_length": blen,
                "extracted_text": text,
                "content": content,
                "parse_results": parse_results})
        classification = None
        if len(observations) > 1:
            base = observations[0]
            def _sd(o):
                prs2 = o["parse_results"]
                return prs2[0]["structured_data"] if prs2 else None
            base_sd = _sd(base)
            all_have_parse = all(o["parse_results"] for o in observations)
            same_struct = (all(
                json.dumps(o["parse_results"][0]["structured_data"],
                           sort_keys=True, default=str)
                == json.dumps(base_sd, sort_keys=True, default=str)
                for o in observations) if all_have_parse else None)
            pairs = {
                "raw_hash": "same" if all(
                    o["sha256"] == base["sha256"] for o in observations)
                else "different",
                "text": "same" if all(
                    o["extracted_text"] == base["extracted_text"]
                    for o in observations) else "different",
                "structured_data": None if same_struct is None
                else ("same" if same_struct else "different")}
            if pairs["raw_hash"] == "same":
                verdict = "BYTE_IDENTICAL"
            elif pairs["text"] == "same":
                verdict = "BYTE_CHANGED_TEXT_SAME"
            elif same_struct is True:
                verdict = "TEXT_CHANGED_STRUCTURED_SAME"
            else:
                verdict = "SEMANTIC_CONTENT_CHANGED"
            classification = {"pairwise_vs_first_observation": pairs,
                              "verdict": verdict}
        docs.append({
            "logical_document": {
                "document_id": doc_id, "source_system": src_system,
                "pdf_type": pdf_type, "source_document_id": source_doc_id,
                "pdf_kind_hint": kind_hint, "fetch_policy": fetch_policy},
            "source_event_relations": [
                {"source_event_id": a, "cell_role": b,
                 "row_skrasnr": cc, "source_event_date": js(dd)}
                for a, b, cc, dd in rels],
            "observations": observations,
            "version_classification": classification})
    data["documents"] = docs

    # -- 22. snapshot history --------------------------------------------------
    data["snapshot_history"] = [
        {"company_id": company_id, "parser_version": pv,
         "document_observation_id": obs_i, "valid_from": js(vf),
         "valid_to": js(vt), "sha256": sh, "fetched_at": js(fa),
         "snapshot_date_conflict": bool(conflict),
         "company_name": (sd0 or {}).get("company_name"),
         "company_address": (sd0 or {}).get("company_address"),
         "structured_data": sd0}
        for (obs_i, pv, vf, vt, sh, fa, conflict, sd0)
        in snapshot_rows]

    # -- 23/24/25/26. derived change history ------------------------------------
    changes = []
    for doc in docs:
        for obs in doc["observations"]:
            for pr in obs["parse_results"]:
                for ch in (pr["structured_data"] or {}).get("changes") or []:
                    changes.append(ch)
    history = build_change_history(changes)
    data["change_count"] = len(changes)
    data["derived_change_history"] = history
    entries = history.get("entries") or []
    data["history_counts"] = {
        "SCALAR_REPLACEMENT": sum(1 for e0 in entries
                                  if e0.get("family")
                                  in SCALAR_FAMILY_MAP.values()),
        "SET_MEMBERSHIP": sum(1 for e0 in entries if e0.get("family")
                              in ("BOARD", "MANAGEMENT", "AUDITOR")),
        "EVENT_ONLY": sum(1 for e0 in entries
                          if e0.get("family") == "EVENT_ONLY")}

    # -- 21. review issues ------------------------------------------------------
    rq = cur.execute("""
        SELECT issue, parse_result_id, document_observation_id, document_id,
               company_id, source_event_id, detail
        FROM pdf_review_queue WHERE company_id = %s
        ORDER BY parse_result_id NULLS LAST, document_id NULLS LAST""",
        (company_id,)).fetchall()
    data["review_issues"] = [
        {"issue": a, "parse_result_id": b, "document_observation_id": c,
         "document_id": d1, "company_id": e1, "source_event_id": f1,
         "detail": g1}
        for (a, b, c, d1, e1, f1, g1) in rq]

    rl = cur.execute("""
        SELECT run_id, step, status, error_type, detail, created_at
        FROM pdf_run_log WHERE company_id = %s
          AND ((status IN ('WARN','STOPPED','REVIEW'))
               OR (step = 'FETCH' AND status = 'FAILED'))
        ORDER BY id""", (company_id,)).fetchall()
    data["run_log_signals"] = [
        {"run_id": a, "step": b, "status": c, "error_type": d1,
         "detail": e1, "created_at": js(f1)}
        for (a, b, c, d1, e1, f1) in rl]

    # -- 29. registration gate ---------------------------------------------------
    gate = []
    for doc in docs:
        source_regnrs = sorted({r["row_skrasnr"]
                                for r in doc["source_event_relations"]})
        for obs in doc["observations"]:
            for pr in obs["parse_results"]:
                parsed = (pr["structured_data"] or {}).get(
                    "registration_number")
                gate.append({
                    "document_id": doc["logical_document"]["document_id"],
                    "pdf_type": doc["logical_document"]["pdf_type"],
                    "observation_id": obs["observation_id"],
                    "parse_result_id": pr["parse_result_id"],
                    "source_row_regnr": source_regnrs,
                    "requested_regnr": regnr,
                    "parsed_regnr": parsed,
                    "verified": parsed == regnr})
    data["registration_gate"] = gate

    # -- 27. dates -----------------------------------------------------------------
    dates = []
    for ev in data["source_events"]:
        dates.append({"date_type": "source_event_date",
                      "value": ev["source_event_date"],
                      "where": f"source_event {ev['source_event_id']}"})
    for doc in docs:
        did = doc["logical_document"]["document_id"]
        for obs in doc["observations"]:
            dates.append({"date_type": "fetched_at", "value":
                          obs["fetched_at"],
                          "where": f"observation {obs['observation_id']}"})
            if obs["document_snapshot_date"]:
                dates.append({"date_type": "document_snapshot_date",
                              "value": obs["document_snapshot_date"],
                              "where": f"observation {obs['observation_id']}"})
            for pr in obs["parse_results"]:
                sd = pr["structured_data"] or {}
                ref = f"parse {pr['parse_result_id']}"
                if sd.get("snapshot_date"):
                    dates.append({"date_type": "snapshot_date",
                                  "value": sd.get("snapshot_date"),
                                  "where": ref})
                dates_block = sd.get("dates") or {}
                for key in ("document_date", "registration_date",
                            "publication_date", "effective_date"):
                    if dates_block.get(key):
                        dates.append({"date_type": key,
                                      "value": dates_block[key],
                                      "where": ref})
                if sd.get("notice_date"):
                    dates.append({"date_type": "notice_date",
                                  "value": sd.get("notice_date"),
                                  "where": ref})
                if sd.get("registration_date"):
                    dates.append({"date_type": "registration_date",
                                  "value": sd.get("registration_date"),
                                  "where": ref})
                for ad in sd.get("accounts_dates") or []:
                    dates.append({"date_type": "accounts_date", "value": ad,
                                  "where": ref})
                for ch in sd.get("changes") or []:
                    for key in ("notice_date", "registration_date",
                                "effective_date"):
                        if ch.get(key):
                            dates.append({
                                "date_type": f"change {key}",
                                "value": ch[key],
                                "where": (f"{ref} change "
                                          f"{ch.get('type')} "
                                          f"{ch.get('action')}"),
                                "entity": ch.get("entity")})
    for sr in data["snapshot_history"]:
        dates.append({"date_type": "snapshot_valid_from",
                      "value": sr["valid_from"],
                      "where": "snapshot_history obs "
                               f"{sr['document_observation_id']}"})
        if sr["valid_to"]:
            dates.append({"date_type": "snapshot_valid_to",
                          "value": sr["valid_to"],
                          "where": "snapshot_history obs "
                                   f"{sr['document_observation_id']}"})
    for e0 in entries:
        if e0.get("from_date"):
            dates.append({"date_type": f"temporal_from_{e0['family']}",
                          "value": e0.get("from_date"),
                          "where": f"history entry idx {e0.get('index')}"})
        if e0.get("to_date"):
            dates.append({"date_type": f"temporal_to_{e0['family']}",
                          "value": e0.get("to_date"),
                          "where": f"history entry idx {e0.get('index')}"})
    dates.sort(key=lambda x: (x.get("value") or "", x["date_type"]))
    data["dates"] = dates

    # -- 28. address audit -----------------------------------------------------
    addr = []
    for doc in docs:
        for obs in doc["observations"]:
            for pr in obs["parse_results"]:
                sd = pr["structured_data"] or {}
                ref = doc["logical_document"]["document_id"]
                ca = sd.get("company_address")
                if ca:
                    addr.append({
                        "role": ca.get("role"), "owner":
                        sd.get("company_name") or regnr,
                        "verbatim": ca.get("verbatim"),
                        "document_id": ref,
                        "observation_id": obs["observation_id"],
                        "date": obs["document_snapshot_date"]
                        or obs["fetched_at"],
                        "structured": True})
                for member in (sd.get("board") or []) + \
                        (sd.get("management") or []):
                    if not isinstance(member, dict):
                        continue
                    m_addr = member.get("address")
                    if m_addr:
                        addr.append({
                            "role": m_addr.get("role"),
                            "owner": member.get("name"),
                            "member_role": member.get("role"),
                            "verbatim": m_addr.get("verbatim"),
                            "document_id": ref,
                            "observation_id": obs["observation_id"],
                            "date": obs["document_snapshot_date"]
                            or obs["fetched_at"],
                            "structured": True})
                for ch in sd.get("changes") or []:
                    txt = ch.get("raw_text") or ""
                    if "gøta" in txt or "Box " in txt or "Postrúm" in txt:
                        addr.append({
                            "role": "EMBEDDED_IN_CHANGE_RAW_TEXT",
                            "owner": ch.get("entity"),
                            "verbatim": txt,
                            "document_id": ref,
                            "observation_id": obs["observation_id"],
                            "date": ch.get("notice_date")
                            or ch.get("effective_date")
                            or ch.get("registration_date"),
                            "structured": False})
    data["address_audit"] = addr

    # -- 30. completeness (mechanical) ------------------------------------------
    comp = []
    for doc in docs:
        labels = []
        review_here = False
        sd_docs = []
        for obs in doc["observations"]:
            for pr in obs["parse_results"]:
                sd = pr["structured_data"] or {}
                sd_docs.append(sd)
                labels += [u.get("label")
                           for u in (sd.get("unknown_sections") or [])]
                if pr["needs_review"]:
                    review_here = True
        issues_on_doc = [ri for ri in data["review_issues"]
                         if ri["document_id"]
                         == doc["logical_document"]["document_id"]]
        if review_here or issues_on_doc:
            assessment = "REVIEW_REQUIRED"
        elif labels:
            assessment = "PARTIALLY_STRUCTURED_LOSSLESS"
        else:
            assessment = "FULLY_STRUCTURED"
        comp.append({"document_id": doc["logical_document"]["document_id"],
                     "pdf_type": doc["logical_document"]["pdf_type"],
                     "assessment": assessment,
                     "unknown_section_labels": sorted(set(labels)),
                     "review_issues_on_doc": len(issues_on_doc),
                     "manual_agent_control": None})
    data["completeness"] = comp
    return data


def main():
    selection_doc, companies = load_selection()
    result = {"selection": selection_doc, "companies": []}
    type_map = {}
    for fn in ("validation_selection.json",
               "validation_selection_expansion.json"):
        for s0 in json.load(open(fn)):
            type_map[s0["regnr"]] = s0["entity_type"]
    with psycopg.connect(DSN) as c, c.cursor() as cur:
        for company in companies:
            ids = cur.execute(
                "SELECT DISTINCT company_id FROM pdf_source_event "
                "WHERE requested_regnr = %s",
                (company["registration_number"],)).fetchall()
            if not ids:
                result["companies"].append({
                    "company_id": None,
                    "registration_number":
                        company["registration_number"],
                    "entity_type":
                        type_map.get(company["registration_number"]),
                    "selection_rank": company["rank"],
                    "selection_hash": company["selection_hash"],
                    "source_events": [], "documents": [],
                    "snapshot_history": [],
                    "derived_change_history": {"entries": [], "review": []},
                    "history_counts": {}, "change_count": 0,
                    "review_issues": [], "run_log_signals": [],
                    "registration_gate": [], "address_audit": [],
                    "dates": [], "completeness": []})
                continue
            row = {"company_id": ids[0][0],
                   "registration_number": company["registration_number"],
                   "rank": company["rank"],
                   "selection_hash": company["selection_hash"],
                   "entity_type": type_map.get(
                       company["registration_number"])}
            result["companies"].append(export_company(c, row))
    JSON_OUT.parent.mkdir(exist_ok=True)
    JSON_OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1,
                                   default=js))
    print(f"wrote {JSON_OUT} ({JSON_OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()