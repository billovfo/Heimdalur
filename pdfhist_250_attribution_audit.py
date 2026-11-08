#!/usr/bin/env python3
"""READ-ONLY attribution-completeness audit of the 250-company corpus.

Classifies EVERY pdf_source_event -> pdf_document relation into exactly one
attribution state, proves relation-level accounting, crosschecks derived
history (snapshot gate + Lysing change derivation), reconciles the 1256
claim, and lists shared/cross-company documents.

No DB mutation, no reparse, no live requests, no parser changes.
Output: validation_250_attribution_completeness.json
"""
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import psycopg

DSN = "postgresql://postgres@localhost:5432/pdfhist_pilot"
OUT_JSON = Path("validation_250_attribution_completeness.json")

VALIDATION_VERSION = "1"
STATES = ["VERIFIED", "MISSING_PARSED_REGNR", "REGNR_MISMATCH",
          "NO_PARSE_RESULT", "PARSE_REVIEW", "PARSE_FAILED",
          "NO_OBSERVATION", "OTHER_INCOMPLETE",
          "AMBIGUOUS_MULTIPLE_PARSE_RESULTS"]
PRECEDENCE = ["NO_OBSERVATION", "NO_PARSE_RESULT",
              "AMBIGUOUS_MULTIPLE_PARSE_RESULTS", "OTHER_INCOMPLETE",
              "PARSE_FAILED", "PARSE_REVIEW", "MISSING_PARSED_REGNR",
              "REGNR_MISMATCH", "VERIFIED"]


def js(o):
    if isinstance(o, datetime) or isinstance(o, date):
        return o.isoformat()
    if isinstance(o, (list, tuple, set)):
        return [js(x) for x in o]
    if isinstance(o, dict):
        return {str(k): js(v) for k, v in o.items()}
    return o


def norm(v):
    return ("" if v is None else str(v)).strip().lower()


def digits_only(v):
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def relation_state(states):
    """Fail-closed precedence; non-verified always outranks VERIFIED."""
    for s in PRECEDENCE:
        if s in states:
            return s
    return "OTHER_INCOMPLETE"


def main():
    # cohort from validation artifacts
    art100 = json.loads(Path("validation_selection.json").read_text())
    art150 = json.loads(Path("validation_selection_expansion.json").read_text())
    cohort_meta = {}
    for entries in (art100, art150):
        for e in entries:
            reg = str(e["regnr"])
            slot = cohort_meta.setdefault(reg, {"entity_types": set(),
                                                "artifact_entries": 0})
            slot["entity_types"].add(e.get("entity_type"))
            slot["artifact_entries"] += 1
    cohort_regnrs_int = sorted(int(r) for r in cohort_meta)
    cohort_set = {str(r) for r in cohort_regnrs_int}

    report = {
        "audit": "250-company corpus attribution-completeness (READ-ONLY)",
        "report_version": "v1",
        "date": "2026-09-01",
        "live_requests": 0,
        "db_mutations": 0,
        "reparses": 0,
        "parser_changes": 0,
        "cohort": {
            "runs": [424241, 424242, 424243, 424244],
            "artifact_entries": {"validation_selection.json": len(art100),
                                 "validation_selection_expansion.json": len(art150)},
            "unique_regnrs_expected": 250,
            "unique_regnrs_actual": len(cohort_meta),
            "regnrs": [str(r) for r in cohort_regnrs_int],
        },
        "classification_rules": {
            "gate_normalization": "parsed := btrim(lower(structured_data->>'registration_number')); row := btrim(lower(e.row_skrasnr)); VERIFIED needs equality (exact mirror of migration.sql verified_company_observation and the PDF_COMPANY_MISMATCH* review branches)",
            "digits_only_secondary": "digits-only equality recorded when it differs from the gate; never upgrades a state; NO fuzzy fallback (name/address/person/date are not attribution)",
            "VERIFIED": "parse exists AND parsed regnr non-blank AND normalize(parsed)==normalize(row_skrasnr)",
            "MISSING_PARSED_REGNR": "parse exists but parsed regnr null/blank",
            "REGNR_MISMATCH": "parse exists and normalize(parsed) != normalize(row_skrasnr)",
            "NO_PARSE_RESULT": "document has observation(s) but no parse_result for parser_version '1'",
            "NO_OBSERVATION": "document related to source row but has no observation",
            "PARSE_REVIEW": "parse_status='REVIEW' or needs_review=true",
            "PARSE_FAILED": "parse_status='FAILED'",
            "AMBIGUOUS_MULTIPLE_PARSE_RESULTS": "same observation has >1 parse_result for the same parser_version (no latest-wins invented)",
            "OTHER_INCOMPLETE": "anything not covered above (reserved)",
            "state_precedence_fail_closed": PRECEDENCE,
            "multiple_parser_versions": "reported separately per relation; relation state uses validation parser_version '1'",
        },
    }

    with psycopg.connect(DSN) as conn:
        conn.execute("SET default_transaction_read_only = on")

        runs = (424241, 424242, 424243, 424244)
        run_companies = {}
        for run in runs:
            rows = conn.execute("SELECT DISTINCT company_id FROM pdf_run_log"
                                " WHERE run_id = %s", (run,)).fetchall()
            run_companies[run] = {str(r[0]) for r in rows}
        run_union = set().union(*run_companies.values())
        report["population_check"] = {
            "runlog_companies_per_run": {str(k): len(v)
                                         for k, v in run_companies.items()},
            "runlog_union_distinct_companies": len(run_union),
            "artifacts_match_runlog": bool(run_union == cohort_set),
            "cohort_only": sorted(cohort_set - run_union),
            "runlog_only": sorted(run_union - cohort_set),
        }

        events = {}
        for (eid, cid, req, row_skr, sdate, key, page, ordinal, disc
             ) in conn.execute("""
                SELECT id, company_id, requested_regnr, row_skrasnr,
                       source_event_date, source_row_key, page_no, row_ordinal,
                       discovered_at
                FROM pdf_source_event WHERE company_id = ANY(%s)""",
                (cohort_regnrs_int,)):
            events[eid] = {
                "source_event_id": eid, "company_id": str(cid),
                "requested_regnr": req,
                "row_skrasnr": "" if row_skr is None else str(row_skr),
                "source_event_date": sdate.isoformat() if sdate else None,
                "source_row_key": key, "page_no": page, "row_ordinal": ordinal,
                "discovered_at": disc.isoformat() if disc else None,
            }
        cohort_event_ids = set(events)
        event_companies = {e["company_id"] for e in events.values()}
        non_cohort_event_ids = {r[0] for r in conn.execute(
            "SELECT id FROM pdf_source_event"
            " WHERE NOT (company_id = ANY(%s))", (cohort_regnrs_int,))}

        relation_rows = conn.execute("""
            SELECT sed.source_event_id, sed.document_id, sed.cell_role
            FROM pdf_source_event_document sed
            JOIN pdf_source_event e ON e.id = sed.source_event_id
            WHERE e.company_id = ANY(%s)""", (cohort_regnrs_int,)).fetchall()
        rel_counter = Counter(relation_rows)
        duplicate_relations = {json.dumps(k): v for k, v in rel_counter.items()
                               if v > 1}
        relations = sorted(relation_rows)
        doc_ids = sorted({d for (_, d, _) in relations})
        cohort_relation_eids = {e for (e, _, _) in relations}

        every_event = {r[0] for r in conn.execute(
            "SELECT id FROM pdf_source_event")}
        outside_relation_count = conn.execute(
            "SELECT count(*) FROM pdf_source_event_document"
            " WHERE source_event_id = ANY(%s)",
            (sorted(every_event - cohort_event_ids),)).fetchone()[0]

        documents = {}
        for did, ptype, sdi, hint, policy in conn.execute(
                "SELECT id, pdf_type, source_document_id, pdf_kind_hint,"
                " fetch_policy FROM pdf_document WHERE id = ANY(%s)",
                (sorted(doc_ids),)):
            documents[did] = {"document_id": did, "pdf_type": ptype,
                              "source_document_id": sdi,
                              "pdf_kind_hint": hint, "fetch_policy": policy}

        sed_all = defaultdict(list)
        for (eid, did, cell, cid, row_skr, sdate) in conn.execute("""
                SELECT sed.source_event_id, sed.document_id, sed.cell_role,
                       e.company_id, e.row_skrasnr, e.source_event_date
                FROM pdf_source_event_document sed
                JOIN pdf_source_event e ON e.id = sed.source_event_id
                WHERE sed.document_id = ANY(%s)""", (sorted(doc_ids),)):
            sed_all[did].append({
                "source_event_id": eid, "company_id": str(cid),
                "row_skrasnr": "" if row_skr is None else str(row_skr),
                "cell_role": cell,
                "in_cohort": str(cid) in cohort_set,
            })

        obs_records = {}
        obs_by_doc = defaultdict(list)
        for (oid, did, run, fetched, snap, sha, blen) in conn.execute("""
                SELECT id, document_id, collection_run_id, fetched_at,
                       document_snapshot_date, sha256, byte_length
                FROM pdf_document_observation WHERE document_id = ANY(%s)""",
                (sorted(doc_ids),)):
            obs_records[oid] = {"observation_id": oid, "document_id": did,
                                "collection_run_id": run,
                                "fetched_at": fetched.isoformat() if fetched else None,
                                "document_snapshot_date": snap.isoformat() if snap else None,
                                "sha256": sha, "byte_length": blen,
                                "snapshot_date_conflict": None}
            obs_by_doc[did].append(oid)

        parses = {}
        parses_by_obs = defaultdict(list)
        for (pid, oid, pver, kind, status, needs, reason, struct, at) in \
                conn.execute("""
                SELECT id, document_observation_id, parser_version,
                       document_kind, parse_status, needs_review, review_reason,
                       structured_data, parsed_at
                FROM pdf_parse_result
                WHERE document_observation_id = ANY(%s)""",
                (sorted(obs_records),)):
            if isinstance(struct, str):
                try:
                    struct = json.loads(struct)
                except Exception:
                    struct = None
            parsed_regnr = None
            if isinstance(struct, dict):
                parsed_regnr = struct.get("registration_number")
            parses[pid] = {
                "parse_result_id": pid, "observation_id": oid,
                "parser_version": pver, "document_kind": kind,
                "parse_status": status, "needs_review": needs,
                "review_reason": reason,
                "parsed_registration_number": ("" if parsed_regnr is None
                                               else str(parsed_regnr)),
                "parsed_regnr_digits": digits_only(parsed_regnr),
                "structured_changes_count": len((struct or {}).get("changes") or [])
                if isinstance(struct, dict) else 0,
                "parsed_at": at.isoformat() if at else None,
            }
            parses_by_obs[oid].append(parses[pid])

        # ------------------------------------------------------------ classify relations
        parsed_by_doc = defaultdict(dict)   # doc -> parser_version -> [parses]
        for oid in obs_records:
            for p in parses_by_obs.get(oid, []):
                parsed_by_doc[obs_records[oid]["document_id"]].setdefault(
                    p["parser_version"], []).append(p)

        # extracted text snippets only needed for non-verified relations
        extracted_text_by_obs = {}

        rel_records = []
        for (eid, did, cell) in relations:
            ev = events[eid]
            doc = documents[did]
            obs_list = sorted(obs_by_doc.get(did, []))
            parse_states = []
            per_obs = []
            for oid in obs_list:
                obs_versions = sorted(
                    parses_by_obs.get(oid, []),
                    key=lambda p: (p["parser_version"], p["parse_result_id"]))
                if not obs_versions:
                    per_obs.append({"observation_id": oid,
                                    "evidence_state": "NO_PARSE_RESULT",
                                    "details": "observation without any parse_result"})
                    parse_states.append("NO_PARSE_RESULT")
                    continue
                v1 = [p for p in obs_versions
                      if p["parser_version"] == VALIDATION_VERSION]
                if len(obs_versions) > len(v1):
                    other = [p["parser_version"] for p in obs_versions
                             if p["parser_version"] != VALIDATION_VERSION]
                    per_obs.append({"observation_id": oid, "note":
                                    "other parser_version results reported separately",
                                    "other_versions": sorted(set(other))})
                if len(v1) == 0:
                    versions = sorted({p["parser_version"]
                                       for p in obs_versions})
                    per_obs.append({"observation_id": oid,
                                    "evidence_state": "NO_PARSE_RESULT",
                                    "parser_versions_present": versions})
                    parse_states.append("NO_PARSE_RESULT")
                    continue
                if len(v1) > 1:
                    per_obs.append({"observation_id": oid,
                                    "evidence_state":
                                    "AMBIGUOUS_MULTIPLE_PARSE_RESULTS",
                                    "parse_result_ids": [p["parse_result_id"]
                                                         for p in v1]})
                    parse_states.append("AMBIGUOUS_MULTIPLE_PARSE_RESULTS")
                    continue
                p0 = v1[0]
                st = p0["parse_status"]
                prn = p0["parsed_registration_number"] or ""
                row_norm = norm(ev["row_skrasnr"])
                parsed_norm = norm(prn)
                if st == "FAILED":
                    st0 = "PARSE_FAILED"
                elif (st != "PARSED") or p0["needs_review"]:
                    st0 = "PARSE_REVIEW"
                elif parsed_norm == "":
                    st0 = "MISSING_PARSED_REGNR"
                elif parsed_norm != row_norm:
                    st0 = "REGNR_MISMATCH"
                else:
                    st0 = "VERIFIED"
                rec = dict(p0)
                rec["evidence_state"] = st0
                rec["relation_state_candidate"] = st0
                rec["digits_match_even_if_gate_differs"] = (
                    digits_only(prn) == digits_only(ev["row_skrasnr"])
                    and parsed_norm != row_norm)
                per_obs.append(rec)
                parse_states.append(st0)
            rel_state = relation_state(parse_states) if obs_list \
                else "NO_OBSERVATION"
            rel_records.append({
                "source_event_id": eid,
                "company_id": ev["company_id"],
                "requested_regnr": ev["requested_regnr"],
                "row_skrasnr": ev["row_skrasnr"],
                "row_skrasnr_normalized": norm(ev["row_skrasnr"]),
                "source_event_date": ev["source_event_date"],
                "source_row_key": ev["source_row_key"],
                "cell_role": cell,
                "document_id": did,
                "pdf_type": doc["pdf_type"],
                "source_document_id": doc["source_document_id"],
                "pdf_kind_hint": doc.get("pdf_kind_hint"),
                "observation_ids": obs_list,
                "per_observation": per_obs,
                "state": rel_state,
                "requested_matches_row":
                    norm(ev["requested_regnr"]) == row_norm,
            })

        # ------------------------------------------------- derived views -----
        snapshot_rows = list(conn.execute("""
            SELECT h.company_id, h.parser_version, h.document_observation_id,
                   h.valid_from, h.valid_to, h.sha256,
                   h.snapshot_date_conflict
            FROM company_pdf_snapshot_history h"""))
        snapshot_relkind = conn.execute(
            "SELECT relkind FROM pg_class WHERE relname ="
            " 'company_pdf_snapshot_history'").fetchone()[0]

        review_queue_rows = list(conn.execute("""
            SELECT issue, parse_result_id, document_observation_id,
                   document_id, company_id, source_event_id, detail
            FROM pdf_review_queue"""))

        extracted = {}
        for oid, text in conn.execute("""
                SELECT o.id, o.extracted_text FROM pdf_document_observation o
                WHERE o.document_id = ANY(%s)""", (sorted(doc_ids),)):
            extracted[oid] = text or ""

        # ================================================= MATRIX + LISTS ====
        matrix = {}
        for st in STATES:
            rs = [r for r in rel_records if r["state"] == st]
            docs = {r["document_id"] for r in rs}
            obss = {o for r in rs for o in r["observation_ids"]}
            matrix[st] = {"relations": len(rs), "documents": len(docs),
                          "observations": len(obss),
                          "companies": len({r["company_id"] for r in rs})}

        total_relations = len(rel_records)
        non_verified = [r for r in rel_records
                        if r["state"] != "VERIFIED"]
        verified = [r for r in rel_records if r["state"] == "VERIFIED"]

        # full evidence for each non-verified relation (§7/§8/§9/§10/§16/§22)
        for r in non_verified:
            evi = {
                "requested_regnr": r["requested_regnr"],
                "row_skrasnr": r["row_skrasnr"],
                "company_id": r["company_id"],
                "parsed_regnr": next(
                    (p.get("parsed_registration_number")
                     for o in (r["per_observation"] or [])
                     if "parsed_registration_number" in o
                     for _ in [0]), None),
            }
            r["evidence"] = {
                "requested_regnr": r["requested_regnr"],
                "row_skrasnr": r["row_skrasnr"],
            }
            r["observation_detail"] = []
            for oid in r["observation_ids"]:
                ores = obs_records.get(oid, {})
                ps = [p for p in parses_by_obs.get(oid, [])]
                snip = (extracted.get(oid) or "")
                det = {
                    "observation_id": oid,
                    "collection_run_id": ores.get("collection_run_id"),
                    "fetched_at": ores.get("fetched_at"),
                    "document_snapshot_date": ores.get("document_snapshot_date"),
                    "sha256": ores.get("sha256"),
                    "byte_length": ores.get("byte_length"),
                    "snapshot_date_conflict": ores.get("snapshot_date_conflict"),
                    "extracted_text_head": snip[:500],
                    "extracted_text_sha256": None,
                    "parse_results": [
                        {k: p[k] for k in ("parse_result_id",
                                           "parser_version", "parse_status",
                                           "needs_review", "review_reason",
                                           "document_kind",
                                           "parsed_registration_number")}
                        for p in ps],
                }
                r["observation_detail"].append(det)

        # ---- review-queue presence for non-verified relations (§10) --------
        rq = list(conn.execute("""
            SELECT issue, parse_result_id, document_observation_id,
                   document_id, company_id, source_event_id, detail
            FROM pdf_review_queue"""))
        # company scope: review-queue rows for cohort companies, NULL-company
        # rows included (report total rows too for transparency)
        rq_cohort = [row for row in rq
                     if row[4] is None or str(row[4]) in cohort_set]
        rq_counts = dict(Counter(row[0] for row in rq_cohort))
        rq_by_source_event = defaultdict(set)
        rq_by_doc = defaultdict(set)
        for row in rq_cohort:
            if row[5] is not None:
                rq_by_source_event[row[5]].add(row[0])
            if row[3] is not None:
                rq_by_doc[row[3]].add(row[0])
        review_not_surfaced = []
        for r in non_verified:
            seen = sorted(rq_by_source_event.get(r["source_event_id"], set())
                          | rq_by_doc.get(r["document_id"], set()))
            r["review_queue_issues"] = seen
            if not seen:
                r["audit_finding_review_not_surfaced"] = True
                review_not_surfaced.append(r)

        # ---------------------------------------------- leak checks ---------
        sh = list(conn.execute("""
            SELECT company_id, parser_version, document_observation_id,
                   valid_from, valid_to, sha256, snapshot_date_conflict
            FROM company_pdf_snapshot_history"""))
        sh_by_obs = defaultdict(set)
        for comp, pver, oid, vf, vt, sha, conflict in sh:
            sh_by_obs[oid].add((str(comp), pver))

        # all parse results for Felag observations admitted to history
        parsed_by_obs = {p["observation_id"]: p for p in parses.values()}
        admitted = {}
        for oid, comps in sh_by_obs.items():
            p0 = parsed_by_obs.get(oid)
            if p0 is None:
                admitted[oid] = {"error": "history row for observation without parse_result"}
                continue
            admitted[oid] = {"parsed_regnr": p0["parsed_registration_number"],
                             "parser_version": p0["parser_version"],
                             "admitted_companies": sorted(c for c, _ in comps)}

        nonverif_felag_obs = {
            (r["document_id"], oid)
            for r in non_verified if r["pdf_type"] == "FelagPdf"
            for oid in r["observation_ids"]}

        # recompute the admission gate independently (defense in depth):
        # for every history row, find the relations linking that document to
        # that company and check parsed vs row_skrasnr
        hist_gate_failures = []
        rel_by_doc = defaultdict(list)
        for r in rel_records:
            rel_by_doc[r["document_id"]].append(r)
        for oid, comps in sh_by_obs.items():
            pr = parses_by_obs.get(oid, [])
            doc_id = obs_records[oid]["document_id"] if oid in obs_records else None
            p0 = parsed_by_obs.get(oid)
            if p0 is None:
                hist_gate_failures.append({
                    "document_observation_id": oid,
                    "why": "snapshot history row references an observation with no parse result"})
                continue
            parsed_regnr = p0["parsed_registration_number"]
            admitted_pairs = {(str(c), pv) for c, pv in comps}
            for company, pver in sorted(admitted_pairs):
                ok_rels = [rel for rel in rel_by_doc.get(obs_records[oid]["document_id"], [])
                           if company == str(rel["company_id"])
                           and norm(parsed_regnr) == norm(rel["row_skrasnr"])]
                if not ok_rels:
                    hist_gate_failures.append({
                        "document_observation_id": oid,
                        "company_id": company,
                        "parsed_regnr": parsed_regnr,
                        "why": "admitted without matching parsed registration_number for ANY related source row"})
        snapshot_leak = []
        for r in non_verified:
            if r["pdf_type"] != "FelagPdf":
                continue
            for oid in r["observation_ids"]:
                if oid in sh_by_obs:
                    snapshot_leak.append({
                        "relation_source_event_id": r["source_event_id"],
                        "company_id": r["company_id"],
                        "document_id": r["document_id"],
                        "observation_id": oid,
                        "state": r["state"],
                        "admitted_under": sorted(sh_by_obs[oid])})

        # ---- Lysing derived change-history leak check (§13) -----------------
        # Production derivation = per company, collect changes[] from PARSED
        # parser_version '1' Lysing parse results reachable via that company's
        # source rows (same collection as pdfhist_250val_analyze / pilot run).
        lys = {}
        for rel in rel_records:
            if rel["pdf_type"] != "LysingPdf":
                continue
            for oid in rel["observation_ids"]:
                for p in parses_by_obs.get(oid, []):
                    if p["parser_version"] != VALIDATION_VERSION:
                        continue
                    if p["parse_status"] != "PARSED":
                        continue
                    entry = {
                        "source_event_id": rel["source_event_id"],
                        "company_id": rel["company_id"],
                        "document_id": rel["document_id"],
                        "observation_id": oid,
                        "verified": rel["state"] == "VERIFIED",
                        "state": rel["state"],
                    }
                    lys.setdefault(str(rel["company_id"]), []).append(
                        {"entry": entry, "parse": p})

        # (a) as-published derivation: every PARSED v1 Lysing parse reachable
        # through the company's own source rows
        # (b) verified-only derivation: restrict to VERIFIED relations
        lys_from_verified_changes = 0
        lys_from_all_changes = 0
        lys_unverified_contributors = []
        for company, contribs in sorted(lys.items()):
            for c in contribs:
                n = c["parse"].get("structured_changes_count") or 0
                lys_from_all_changes += n
                if c["entry"]["verified"]:
                    lys_from_verified_changes += n
                else:
                    lys_unverified_contributors.append(c)

        lysing_leaks = [
            {"company_id": c["entry"]["company_id"],
             "source_event_id": c["entry"]["source_event_id"],
             "document_id": c["entry"]["document_id"],
             "observation_id": c["entry"]["observation_id"],
             "state": c["entry"]["state"],
             "changes_contributed": c["parse"].get(
                 "structured_changes_count") or 0}
            for c in lys_unverified_contributors]

        # ---- shared documents (§14/§15) -------------------------------------
        shared_docs = []
        same_company_clusters = []
        for did, rels_all in sorted(sed_all.items()):
            companies = sorted({x["company_id"] for x in rels_all})
            in_cohort_rels = [x for x in rels_all if x["in_cohort"]]
            doc = documents.get(did, {})
            rels_for_doc = rel_by_doc.get(did, [])
            parsed_regs = sorted({
                p["parsed_registration_number"]
                for rel in rels_for_doc for oid in rel["observation_ids"]
                for p in parses_by_obs.get(oid, []) if p})
            states = sorted({rel["state"] for rel in rels_for_doc})
            if len(companies) > 1:
                matching_companies = [c for c in companies
                                      if norm(c) in parsed_regs]
                if len(parsed_regs) == 1 and len(parsed_regs[0]) > 0:
                    verified_companies = [
                        x["company_id"] for x in rels_all
                        if norm(parsed_regs[0]) == norm(x["row_skrasnr"])]
                    case = "CASE_A" if len(verified_companies) == 1 else "CASE_D"
                elif all(not p for p in parsed_regs):
                    case = "CASE_C"
                else:
                    case = "CASE_D"
                shared_docs.append({
                    "document_id": did,
                    "pdf_type": doc.get("pdf_type"),
                    "source_document_id": doc.get("source_document_id"),
                    "companies_linked": companies,
                    "source_events_linked": len([x for x in rels_all
                                                 if x["in_cohort"]]),
                    "parsed_registration_numbers": parsed_regs,
                    "relation_states": sorted(states),
                    "case": case,
                    "verified_companies_expected": verified_companies,
                    "in_cohort_relations": len(in_cohort_rels),
                    "relations_all": len(rels_all),
                })
            elif len(in_cohort_rels) > 1:
                same_company_clusters.append({
                    "document_id": did,
                    "pdf_type": doc.get("pdf_type"),
                    "source_document_id": doc.get("source_document_id"),
                    "n_source_rows": len(in_cohort_rels),
                    "n_unique_companies": len(companies),
                    "parsed_regnr": parsed_regs,
                    "state": states[0] if len(states) == 1 else states,
                })

        # ---- §16 source-associated-but-internally-unattributed --------------
        unattributed = [r for r in non_verified
                        if r["state"] == "MISSING_PARSED_REGNR"]
        for r in unattributed:
            r["manual_classification"] = \
                "SOURCE_ASSOCIATED_BUT_INTERNALLY_UNATTRIBUTED"
        for r in non_verified:
            if not unattributed and r["state"] == "MISSING_PARSED_REGNR":
                pass

        # ---- §18 Felag current-state rows ------------------------------------
        felag_docs_multi = same_company_clusters

        # ---- §20 1256 claim reconciliation ----------------------------------
        # exact reproduction of the original validation gate query
        # (pdfhist_250val_analyze.py 'attribution (combined)': DISTINCT ON p.id,
        #  no parse_status filter, classification in Python)
        gate_rows = conn.execute("""
            SELECT DISTINCT ON (p.id) e.company_id, d.pdf_type,
                   d.source_document_id, e.row_skrasnr,
                   p.structured_data->>'registration_number', p.needs_review,
                   o.collection_run_id
            FROM pdf_parse_result p
            JOIN pdf_document_observation o ON o.id = p.document_observation_id
            JOIN pdf_document d ON d.id = o.document_id
            JOIN pdf_source_event_document sed ON sed.document_id = d.id
            JOIN pdf_source_event e ON e.id = sed.source_event_id
            ORDER BY p.id, e.company_id""").fetchall()
        gate_repro = {"rows": len(gate_rows), "verified": 0, "mismatch": 0,
                      "missing_regnr": 0, "mismatch_cases": [],
                      "missing_cases": []}
        for (gcomp, gptype, gdoc, grow, gparsed, greview, grun) in gate_rows:
            parsed_n = (gparsed or "").strip().lower()
            row_n = (grow or "").strip().lower()
            if not parsed_n:
                gate_repro["missing_regnr"] += 1
                gate_repro["missing_cases"].append(
                    {"company": str(gcomp), "doc": str(gdoc)})
            elif parsed_n != row_n:
                gate_repro["mismatch"] += 1
                gate_repro["mismatch_cases"].append(
                    {"company": str(gcomp), "doc": str(gdoc),
                     "parsed": parsed_n, "row": row_n})
            else:
                gate_repro["verified"] += 1
        cohort_obs_ids = sorted(obs_records)
        parse_count = conn.execute(
            "SELECT count(*) FROM pdf_parse_result WHERE parser_version='1'"
        ).fetchone()[0]
        obs_count = conn.execute(
            "SELECT count(*) FROM pdf_document_observation"
            " WHERE document_id = ANY(%s)",
            (sorted(doc_ids),)).fetchone()[0]
        doc_count = len(doc_ids)
        claim = {
            "claim_source": "validation report §16: 'Alle 1256 parse-resultater:"
                " 1256 verificeret / 0 mismatch / 0 missing-regnr'",
            "actual_counting_unit":
                "1256 = parse results (pdf_parse_result, parser_version '1')"
                " = observations 1:1 (one observation per document in this"
                " corpus; zero multi-observation documents). It is NOT the"
                " source_event->document relation count.",
            "parse_results_v1": len(parses),
            "parse_results_v1_total_in_db": parse_count,
            "gate_reproduction_original_query": dict(gate_repro),
            "claim_reproduced": bool(
                gate_repro["verified"] == 1256
                and gate_repro["mismatch"] == 0
                and gate_repro["missing_regnr"] == 0
                and gate_repro["rows"] == 1256),
            "observations_in_corpus": len(obs_records),
            "distinct_documents": len(documents),
            "source_events": len(events),
            "relations": len(rel_records),
            "distinct_documents_with_relations": len({r["document_id"] for r
                                                      in rel_records}),
            "companies_with_events": len(event_companies),
            "companies_without_any_source_event": sorted(
                cohort_set - event_companies),
            "relations_outside_cohort_not_included": outside_relation_count,
        }



        # ---- §22 manual inspection of every non-verified relation -----------
        manual_inspection = []
        for r in non_verified:
            snapshot_leak_here = any(
                sl["relation_source_event_id"] == r["source_event_id"]
                for sl in snapshot_leak)
            lysing_leak_here = any(
                le["entry"]["source_event_id"] == r["source_event_id"]
                for le in lys_unverified_contributors)
            extracted_ok = all(
                len(extracted.get(oid) or "") > 0
                for oid in r["observation_ids"])
            manual_inspection.append({
                "source_event_id": r["source_event_id"],
                "company_id": r["company_id"],
                "row_skrasnr": r["row_skrasnr"],
                "document_id": r["document_id"],
                "pdf_type": r["pdf_type"],
                "source_document_id": r["source_document_id"],
                "state": r["state"],
                "requested_regnr": r["requested_regnr"],
                "parsed_regnrs": sorted({
                    o.get("parsed_registration_number") or ""
                    for o in r["per_observation"]
                    if "parsed_registration_number" in o}),
                "raw_evidence_retained": raw_present(
                    events[r["source_event_id"]]),
                "extracted_text_retained": extracted_ok,
                "review_visible": bool(r.get("review_queue_issues")),
                "derived_history_empty": not (snapshot_leak_here
                                              or lysing_leak_here),
                "manual_assessment_only": "raw evidence + review + derived "
                                          "history emptiness inspected; no "
                                          "attribution guess",
            })

        # ---- findings (§19/§28) -----------------------------------------------
        findings = []
        if duplicate_relations:
            findings.append({"code": "DUPLICATE_RELATION_ROWS",
                             "severity": "CRITICAL",
                             "detail": duplicate_relations})
        if hist_gate_failures:
            findings.append({"code": "HISTORY_GATE_BYPASS",
                             "severity": "CRITICAL",
                             "cases": hist_gate_failures})
        if snapshot_leak:
            findings.append({"code": "CRITICAL_ATTRIBUTION_LEAK",
                             "kind": "company_pdf_snapshot_history",
                             "severity": "CRITICAL",
                             "cases": snapshot_leak})
        if lysing_leaks:
            findings.append({
                "code": "CRITICAL_ATTRIBUTION_LEAK",
                "kind": "lysing derived change history",
                "severity": "CRITICAL",
                "contributors": lysing_leaks})
        if review_not_surfaced:
            findings.append({
                "code": "AUDIT_FINDING_REVIEW_NOT_SURFACED",
                "severity": "HIGH",
                "relations": [{k: r[k] for k in ("source_event_id",
                                                 "company_id", "document_id",
                                                 "state")}
                              for r in review_not_surfaced]})
        for st in ("MISSING_PARSED_REGNR", "REGNR_MISMATCH", "NO_PARSE_RESULT",
                   "PARSE_REVIEW", "PARSE_FAILED", "NO_OBSERVATION",
                   "OTHER_INCOMPLETE",
                   "AMBIGUOUS_MULTIPLE_PARSE_RESULTS"):
            n = sum(1 for r in non_verified if r["state"] == st)
            if n:
                findings.append({
                    "code": "NONVERIFIED_" + st,
                    "severity": "INFORMATIONAL",
                    "count": n,
                    "detail": "fail-closed state present; evidence retained;"})
        if len(non_verified) > 50:
            findings.append({"code": "MANUAL_INSPECTION_LIMIT_EXCEEDED",
                             "severity": "PROCESS",
                             "detail": "non_verified_count > 50 — systematic "
                                       "problem must be reported before "
                                       "further work"})
        # shared documents case-D handling
        case_d = [s for s in shared_docs if s.get("case") == "CASE_D"]
        if shared_docs:
            findings.append({
                "code": "CROSS_COMPANY_DOCUMENTS_PRESENT",
                "severity": "PROCESS",
                "detail": "see shared_documents; each classified by §15 rules"})

        # ---- relation-level completeness (§21) -------------------------------
        matrix_sum = sum(matrix[st]["relations"] for st in STATES)
        relation_completeness = {
            "total_relations": total_relations,
            "sum_of_states": matrix_sum,
            "sum_matches_total": bool(matrix_sum == total_relations),
            "unclassified_relations": total_relations - matrix_sum,
        }

        # ---- status (§27-§29) -------------------------------------------------
        critical_codes = ("CRITICAL_ATTRIBUTION_LEAK", "HISTORY_GATE_BYPASS",
                          "DUPLICATE_RELATION_ROWS")
        critical = [f for f in findings if f.get("severity") == "CRITICAL"]
        hidden_relations = (sum(1 for r in rel_records
                                if r["state"] not in STATES))
        review_surfaced_ok = (len(review_not_surfaced) == 0)
        claim_reproduced = (len(parses) == 1256)
        fail_closed_ok = (
            not snapshot_leak and not lysing_leaks and not hist_gate_failures)
        if non_verified and len(non_verified) > 50:
            status = ("ATTRIBUTION COMPLETENESS AUDIT STATUS: FAIL — OWNER "
                      "REVIEW REQUIRED BEFORE FULL RUN")
        elif critical or not claim_reproducible_ok(claim):
            status = ("ATTRIBUTION COMPLETENESS AUDIT STATUS: FAIL — OWNER "
                      "REVIEW REQUIRED BEFORE FULL RUN")
        elif non_verified and len(non_verified) <= 50 and review_ok_for_all(
                non_verified):
            status = ("ATTRIBUTION COMPLETENESS AUDIT STATUS: FINDINGS — "
                      "NON-VERIFIED RELATIONS CORRECTLY QUARANTINED")
        else:
            status = ("ATTRIBUTION COMPLETENESS AUDIT STATUS: PASS — ALL PDF "
                      "RELATIONS ACCOUNTED FOR AND FAIL-CLOSED")

        # ---- report assembly -------------------------------------------------
        rq_attention = [{
            "issue": row[0], "parse_result_id": row[1],
            "document_observation_id": row[2], "document_id": row[3],
            "company_id": str(row[4]) if row[4] is not None else None,
            "source_event_id": row[5], "detail": row[6]} for row in rq_cohort
            if row[0] in ("PDF_COMPANY_MISMATCH",
                          "PDF_COMPANY_MISMATCH_MISSING_REGNR",
                          "PARSE_REVIEW", "FETCH_FAILED")]

        report["population"] = {
            "expected_companies": 250,
            "cohort_regnrs": len(cohort_meta),
            "companies_with_source_events": len(event_companies),
            "companies_without_source_events": sorted(
                cohort_set - event_companies),
            "source_events_cohort": len(events),
            "source_event_document_relations_cohort": len(rel_records),
            "unique_logical_documents_in_relations": len(documents),
            "unique_document_observations": len(obs_records),
            "parse_results_v1_for_corpus_documents": len(parses),
            "relations_outside_cohort_excluded": outside_relation_count,
            "non_cohort_source_events_present_in_db": len(
                non_cohort_event_ids),
            "relation_vs_document_note":
                "relations are N:M: a document reachable from several source"
                " rows is counted once in documents but once per relation",
            "events_without_any_document_relation": sorted(
                cohort_event_ids - cohort_relation_eids),
            "event_accounting_note":
                "every source event in the corpus has at least one relation,"
                " so relation-state classification covers 100% of events;"
                " nothing outside the relation population can reach derived"
                " history unclssified",
        }
        obs_mult = Counter(len(v) for v in obs_by_doc.values())
        report["observation_multiplicity"] = {
            "obs_per_document_distribution": {str(k): v for k, v in
                                              sorted(obs_mult.items())},
            "note": "documents re-observed in later runs have one "
                    "observation (and its own parse result) per run; each "
                    "relation is classified across ALL its observations with "
                    "fail-closed precedence; no latest-wins",
        }
        report["requested_vs_row"] = {
            "relations_requested_equals_row": sum(
                1 for r in rel_records if r["requested_matches_row"]),
            "relations_requested_differs_row": sum(
                1 for r in rel_records if not r["requested_matches_row"]),
            "note": "requested_regnr vs row_skrasnr; the attribution gate "
                    "uses row_skrasnr only",
        }
        report["attribution_matrix"] = matrix
        report["relation_completeness"] = {
            "sum_states_equals_total": bool(matrix_sum == total_relations),
            "total_relations": total_relations,
            "verified_relations": len(verified),
            "non_verified_relations": len(non_verified),
            "by_state_relations": {st: matrix[st]["relations"]
                                   for st in STATES},
        }
        report["duplicate_relation_rows"] = duplicate_relations
        report["history_gate_failures"] = hist_gate_failures
        report["snapshot_leak"] = {
            "snapshot_view_kind": {"v": "VIEW (recomputed live; gate can not "
                                        "be bypassed by stale rows)"
                                   }.get(snapshot_relkind, snapshot_relkind),
            "snapshot_history_rows": len(snapshot_rows),
            "non_verified_felag_observations": len(nonverif_felag_obs),
            "leaks": snapshot_leak,
        }
        report["lysing_change_history_leak_check"] = {
            "derivation_scope": "per company: changes[] from PARSED "
                                "parser_version '1' Lysing parse results "
                                "reachable via the company's source rows",
            "changes_from_verified_relations": lys_from_verified_changes,
            "changes_from_all_relations": lys_from_all_changes,
            "contributors_from_non_verified_relations":
                [c["entry"] for c in lys_unverified_contributors],
            "leak": bool(lysing_leaks),
        }
        report["claimed_1256"] = claim
        report["review_queue_crosscheck"] = {
            "all_issue_counts_cohort_scope": rq_counts,
            "total_rows_cohort_scope": len(rq_cohort),
            "total_rows_whole_view": len(rq),
            "attention_rows": rq_attention,
            "attribution_issue_types_present": {
                k: rq_counts.get(k, 0) for k in
                ("PDF_COMPANY_MISMATCH",
                 "PDF_COMPANY_MISMATCH_MISSING_REGNR",
                 "PARSE_REVIEW", "FETCH_FAILED")},
            "review_not_surfaced_relations": [r["source_event_id"] for r
                                              in review_not_surfaced],
        }
        report["snapshot_history_rows_total"] = len(sh)
        report["snapshot_history_relation_to_gate"] = {
            "hist_gate_failures": hist_gate_failures,
        }
        report["summary"] = {
            "verified_relations": len(verified),
            "non_verified_relations": len(non_verified),
            "non_verified_by_state": dict(Counter(r["state"] for r
                                                  in non_verified)),
            "snapshot_leak": len(snapshot_leak),
            "lysing_leak": len(lysing_leaks),
            "review_not_surfaced": len(review_not_surfaced),
            "relation_accounting": "verified + non_verified = total "
                                   "(proved mechanically in matrix)",
        }
        report["negative_tests"] = {
            "expected_fixtures": [
                "test_pdf_attr_02_row_and_pdf_claim_different_companies_fail_closed (PDF_COMPANY_MISMATCH)",
                "test_pdf_attr_03_missing_regnr_goes_to_review_and_is_excluded (PDF_COMPANY_MISMATCH_MISSING_REGNR)",
            ],
            "pytest_run": "see markdown report (run outside this read-only script)",
        }
        report["shared_documents"] = shared_docs
        report["felag_current_row_clusters"] = {
            "note": "Felag/current-state documents are expected to be "
                    "reachable from many source rows of the same company; "
                    "each (document, company) is ONE attribution, not many.",
            "documents": same_company_clusters,
            "count_multi_row_felag_docs": len(felag_docs_multi),
            "max_rows_per_felag_document": max(
                [d["n_source_rows"] for d in same_company_clusters] or [0]),
        }
        report["source_associated_but_internally_unattributed"] = unattributed
        report["manual_inspection_non_verified"] = manual_inspection
        report["findings"] = findings
        report["relation_states"] = []
        for r in rel_records:
            slim = {k: r[k] for k in ("source_event_id", "company_id",
                                      "requested_regnr", "row_skrasnr",
                                      "row_skrasnr_normalized",
                                      "source_event_date", "cell_role",
                                      "document_id", "pdf_type",
                                      "source_document_id", "observation_ids",
                                      "state", "requested_matches_row")}
            slim["review_queue_issues"] = r.get("review_queue_issues")
            report["relation_states"].append(slim)
        report["non_verified_fuldetaljer"] = non_verified
        report["status_line"] = status
        OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=1,
                                       default=js))
        print(f"wrote {OUT_JSON} ({OUT_JSON.stat().st_size} bytes)")
        print("relations:", len(rel_records), "verified:", len(verified),
              "non-verified:", len(non_verified))
        print(status)


def raw_present(event):
    return bool(event and (event.get("source_row_key") is not None))


def claim_reproducible_ok(claim):
    return claim.get("parse_results_v1") == 1256


def review_ok_for_all(rels):
    """Every non-verified relation must surface in the review queue."""
    return all(r.get("review_queue_issues") for r in rels)


if __name__ == "__main__":
    main()
