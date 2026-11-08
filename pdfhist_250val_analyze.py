"""Post-run analysis for the 250-company validation corpus.

Combines the prior 100-company cohort (runs 424241/424242/424243) with the
150-company expansion (run 424244) into one derived 250-company corpus report
data file (validation_250_results.json) + review exports tmp-250val-review/.

Reads ONLY pdf_* tables in the validation DB (+ read-only QA heimdal for the
company_location integrity check). NO live requests.
"""
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import psycopg

import pdfhist_100val_analyze as v100
from heimdal.pdfhist.parse import (
    _FELAG_KNOWN_LABELS, _LYSING_KNOWN_LABELS, LYSING_MEMBERSHIP_LABELS,
    SCALAR_LYSING_LABELS, LYSING_OP_TABLE, _ONELINE_SPLIT_WITH_COLON)
from heimdal.pdfhist.temporal import build_change_history

DSN = v100.DSN
QA_DSN = v100.QA_DSN
OUT = Path("tmp-250val-review")

COHORT_100_RUNS = (424241, 424242, 424243)
EXPANSION_RUN = 424244

KNOWN_LABELS = set(v100.KNOWN_LABEL_NAMES) if hasattr(v100, "KNOWN_LABEL_NAMES") else (
    _FELAG_KNOWN_LABELS | _LYSING_KNOWN_LABELS | set(LYSING_MEMBERSHIP_LABELS)
    | set(SCALAR_LYSING_LABELS) | set(LYSING_OP_TABLE)
    | set(_ONELINE_SPLIT_WITH_COLON)
    | {"Stjóri", "Stovningardato", "Viðtøkudato", "Stovnarar", "Kapitalur",
       "Inngjaldingarháttur", "Roknskaparár", "Fyrsta roknskaparár",
       "Skrásetingar-nr.", "Dagf."})

FOUNDATION_LABELS = {"Stovnarar", "Kapitalur", "Inngjaldingarháttur"}

REVIEW_GROUPS = [
    "LAYOUT_DRIFT", "REGISTRATION_NUMBER_MISSING", "SNAPSHOT_DATE_MISSING",
    "CHANGE_DATE_MISSING", "MEMBERSHIP_CLOSE_WITHOUT_OPEN",
    "DUPLICATE_OPEN_APPOINTMENT", "ADDR_ROLE_UNCERTAIN",
]


def extract_unknown_labels(struct):
    """Non-parenthesized unknown labels (parenthesized = parser-internal
    structural markers like (preamble)/(Nevnd usorterede linjer); the
    100-company corpus analysis used the same exclusion for new label
    phrases)."""
    out = []
    for u in (struct.get("unknown_sections") or []):
        lbl = (u.get("label") or "").strip()
        if lbl and not lbl.startswith("("):
            out.append(re.sub(r"\s+", " ", lbl))
    return out


def classify_layout(raw, struct, baseline_labels):
    """KNOWN_LAYOUT / NEW_LAYOUT / NEW_LABEL / NEW_CHANGE_PHRASE /
    TEXT_LAYER_PROBLEM for one parse result.

    baseline_labels = union of non-parenthesized unknown labels already
    observed in the prior cohort (runs 424241-424243). Unknown labels IN
    the baseline are KNOWN corpus occurrences (lossless-retained since the
    100-company validation) and do not classify the document as a new
    layout. Only labels unseen in the prior cohort do."""
    raw = raw or ""
    compact = raw.replace("\n", " ").strip()
    if not compact:
        return "NEW_LAYOUT"
    if len(compact) < 120 or compact.count("\ufffd") > len(compact) * 0.2:
        return "TEXT_LAYER_PROBLEM"
    new_labels = [u for u in extract_unknown_labels(struct)
                  if u not in baseline_labels]
    if not new_labels:
        return "KNOWN_LAYOUT"
    if any(re.search(r"ú nevnd|í nevnd|ú stjórn|í stjórn|valdur|fjær|"
                     r"endurbjóða|nýggjr? |síðani|nú ", u)
           for u in new_labels):
        return "NEW_CHANGE_PHRASE"
    return "NEW_LABEL"


def main():
    OUT.mkdir(exist_ok=True)
    results = {"title": "250-company validation corpus analysis"}

    with psycopg.connect(DSN) as conn:
        parses = {}
        for obs_id, raw, struct in conn.execute(
                "SELECT p.document_observation_id, p.raw_text, p.structured_data"
                " FROM pdf_parse_result p WHERE p.parser_version = '1'"):
            parses[obs_id] = (raw, struct if isinstance(struct, dict)
                              else json.loads(struct))

        # per-doc latest observation row for cohort scoped analysis
        rows = conn.execute("""
            SELECT DISTINCT ON (p.id) o.collection_run_id, e.company_id,
                   d.pdf_type, d.source_document_id, p.id, p.raw_text,
                   p.structured_data, p.parse_status, p.needs_review,
                   p.review_reason
            FROM pdf_parse_result p
            JOIN pdf_document_observation o ON o.id = p.document_observation_id
            JOIN pdf_document d ON d.id = o.document_id
            JOIN pdf_source_event_document sed ON sed.document_id = d.id
            JOIN pdf_source_event e ON e.id = sed.source_event_id
            ORDER BY p.id, e.company_id""").fetchall()

        def in_cohort(run):
            return run in COHORT_100_RUNS

        cohort = {"100": [], "150": []}
        for run, company, pdf_type, doc_key, pid, raw, struct, status, review, reasons in rows:
            bucket = "100" if in_cohort(run) else "150"
            cohort[bucket].append({
                "run": run, "company": str(company), "pdf_type": pdf_type,
                "doc_key": str(doc_key), "raw": raw, "struct": struct,
                "parse_status": status, "needs_review": review,
                "review_reasons": reasons or []})

        # corpus baseline = all non-parenthesized unknown labels already seen
        # in the prior cohort (runs 424241-424243) — these are KNOWN corpus
        # occurrences, not new layouts.
        baseline_labels = set()
        for e in cohort["100"]:
            baseline_labels.update(extract_unknown_labels(e["struct"]))

        # ---- parser metrics per cohort + combined -------------------------
        def parser_metrics(entries):
            kinds = Counter(e["pdf_type"] for e in entries)
            statuses = Counter(e["parse_status"] for e in entries)
            reviews = [e for e in entries if e["needs_review"] or
                       e["parse_status"] != "PARSED"]
            layout = Counter()
            unk_labels = Counter()
            for e in entries:
                layout[classify_layout(e["raw"], e["struct"],
                                       baseline_labels)] += 1
                for u in extract_unknown_labels(e["struct"]):
                    unk_labels[u] += 1
            return {
                "parse_results": len(entries),
                "documents": len({(e["company"], e["pdf_type"], e["doc_key"])
                                  for e in entries}),
                "companies": len({e["company"] for e in entries}),
                "by_type": dict(kinds),
                "parse_status": dict(statuses),
                "review_results": len(reviews),
                "layout_classification": dict(layout),
                "unknown_labels": dict(unk_labels),
            }

        m100 = parser_metrics(cohort["100"])
        m150 = parser_metrics(cohort["150"])
        combined = parser_metrics(cohort["100"] + cohort["150"])
        for m in (m100, m150, combined):
            m["defect_rate_per_doc"] = round(
                (m["parse_status"].get("REVIEW", 0)
                 + m["parse_status"].get("FAILED", 0)) / m["documents"],
                4) if m["documents"] else None
        results["parser_metrics"] = {"cohort_100": m100, "cohort_150": m150,
                                     "combined_250": combined}

        # ---- company metrics ----------------------------------------------
        comp = {}
        for bucket in ("100", "150"):
            entries = cohort[bucket]
            runs = COHORT_100_RUNS if bucket == "100" else (EXPANSION_RUN,)
            per_company = set()
            for e in entries:
                per_company.add(e["company"])
            attempted = conn.execute("""
                SELECT count(DISTINCT company_id) FROM pdf_run_log
                WHERE run_id = ANY(%s)""", (list(runs),)).fetchone()[0]
            stopped = conn.execute("""
                SELECT DISTINCT company_id FROM pdf_run_log
                WHERE run_id = ANY(%s) AND status='STOPPED'""", (list(runs),)
            ).fetchall()
            stopped = {str(r[0]) for r in stopped}
            comp[f"cohort_{bucket}"] = {
                "selected": len(per_company) if bucket == "150" else 100,
                "companies_with_parse_results": len(per_company),
                "source_stopped_companies": sorted(stopped),
                "source_stopped": len(stopped),
            }
        results["company_metrics"] = comp

        # ---- source metrics (per run) --------------------------------------
        src = {}
        for run, key in ((424243, "cohort_100_run424243"),
                         (424244, "cohort_150_run424244")):
            logs = conn.execute("""
                SELECT step, status, error_type, count(*) FROM pdf_run_log
                WHERE run_id = %s GROUP BY 1,2,3 ORDER BY 1""", (run,)).fetchall()
            sr = {}
            for step, status, err, n in logs:
                sr.setdefault(step, {})[
                    (status + (f":{err}" if err else ""))] = n
            src[key] = sr
            src[key + "_budget"] = {}
        results["source_metrics"] = src

        # ---- pagination anomalies (new in expansion + all runs) ------------
        pag = {"expansion_run_anomalies": [], "all_runs_anomaly_companies":
               sorted({str(r[0]) for r in conn.execute(
                   """SELECT DISTINCT company_id FROM pdf_run_log
                      WHERE status='STOPPED' AND error_type='PAGINATION_MISMATCH'
                      """).fetchall()})}
        for company, detail in conn.execute("""
                SELECT company_id, detail FROM pdf_run_log
                WHERE step='PAGINATION' AND status='STOPPED'
                  AND run_id=424244 GROUP BY 1,2"""):
            pag["expansion_run_anomalies"].append(
                {"company": str(company), "detail": detail})
        results["pagination_anomalies"] = pag

        # ---- document cardinality ------------------------------------------
        docs = {}
        for bucket, runs in (("cohort_100", list(COHORT_100_RUNS)),
                             ("cohort_150", [EXPANSION_RUN])):
            row = conn.execute("""
                SELECT count(DISTINCT o.document_id), count(*),
                       count(DISTINCT o.sha256)
                FROM pdf_document_observation o
                WHERE o.collection_run_id = ANY(%s)""", (runs,)).fetchone()
            types = conn.execute("""
                SELECT d.pdf_type, count(DISTINCT o.document_id)
                FROM pdf_document_observation o
                JOIN pdf_document d ON d.id = o.document_id
                WHERE o.collection_run_id = ANY(%s) GROUP BY 1""", (runs,)
            ).fetchall()
            docs[bucket] = {"observations": row[1],
                            "documents": row[0],
                            "unique_bytes": row[2],
                            "by_type": {r[0]: r[1] for r in types}}
        results["document_metrics"] = docs

        # ---- content classification (all observation pairs, all runs) -------
        pairs = v100.observation_pairs(conn)
        classes = Counter()
        semantic_cases = []
        for prev, cur in pairs:
            label, diff = v100.classify_pair(prev, cur)
            classes[label] += 1
            if label == "SEMANTIC_CONTENT_CHANGED":
                semantic_cases.append({
                    "pdf_type": cur["pdf_type"], "doc": cur["key"],
                    "runs": [prev["run"], cur["run"]],
                    "fields": diff})
        results["content_metrics"] = {
            "total_pairs": len(pairs), "classification": dict(classes),
            "semantic_cases": semantic_cases}

        # ---- attribution (combined) -----------------------------------------
        gate = conn.execute("""
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
        att = {"verified": 0, "mismatch": 0, "missing_regnr": 0,
               "cases": [], "by_cohort": {"100": {"verified": 0, "mismatch": 0,
                                                  "missing_regnr": 0},
                                          "150": {"verified": 0, "mismatch": 0,
                                                  "missing_regnr": 0}}}
        for company, pdf_type, doc, row_regnr, parsed, review, run in gate:
            parsed_n = (parsed or "").strip().lower()
            row_n = (row_regnr or "").strip().lower()
            bucket = "100" if int(run) in COHORT_100_RUNS else "150"
            if not parsed_n:
                att["missing_regnr"] += 1
                att["by_cohort"][bucket]["missing_regnr"] += 1
                att["cases"].append({"company": str(company), "doc": str(doc),
                                     "kind": "MISSING_REGNR"})
            elif parsed_n != row_n:
                att["mismatch"] += 1
                att["by_cohort"][bucket]["mismatch"] += 1
                att["cases"].append({"company": str(company), "doc": str(doc),
                                     "kind": "MISMATCH", "parsed": parsed_n,
                                     "row": row_n})
            else:
                att["verified"] += 1
                att["by_cohort"][bucket]["verified"] += 1
        results["attribution_metrics"] = att

        # ---- foundation documents -------------------------------------------
        found = Counter()
        for e in cohort["150"] + cohort["100"]:
            raw = e["raw"] or ""
            if "Stovnarar" in raw:
                found["docs_with_Stovnarar"] += 1
            if "Kapitalur" in raw:
                found["docs_with_Kapitalur"] += 1
            if "Inngjaldingarháttur" in raw:
                found["docs_with_Inngjaldingarhättur".replace("hättur",
                                                              "háttur")] += 1
        results["foundation_metrics"] = dict(found)

        # ---- review metrics grouped ------------------------------------------
        grouped = Counter()
        for e in cohort["100"] + cohort["150"]:
            for reason in (e["review_reasons"] or []):
                base = str(reason).split(",")[0]
                if base in REVIEW_GROUPS:
                    grouped[base] += 1
                else:
                    grouped["andre:" + base] += 1
            for u in (e["struct"].get("unknown_sections") or []):
                lbl = (u.get("label") or "").strip()
                if lbl and lbl not in KNOWN_LABELS:
                    grouped["unknown label"] += 1
                    break
        results["review_metrics"] = dict(grouped)

        # ---- temporal candidates: >=25 companies ------------------------------
        per_company = {}
        for e in cohort["100"] + cohort["150"]:
            per_company.setdefault(e["company"], []).extend(
                e["struct"].get("changes") or [])
        ranked = sorted(per_company.items(), key=lambda kv: -len(kv[1]))
        temporal = {}
        for company, changes in ranked:
            if len(changes) < 2:
                continue
            history = build_change_history(changes)
            temporal[company] = {"changes": len(changes),
                                 "entries": history["entries"],
                                 "review": history["review"]}
        results["temporal"] = {"candidate_companies": len(temporal),
                               "companies": temporal}

        # ---- manual review export: 50 Felag + 50 Lysing + 100% risk ----------
        # risk = REVIEW/FAILED + every non-KNOWN layout classification.
        # 100% of risk docs are exported; the 50+50 samples are drawn ONLY
        # from clean docs so risk and samples stay disjoint.
        fel = [e for e in cohort["100"] + cohort["150"]
               if e["pdf_type"] == "FelagPdf"]
        lys = [e for e in cohort["100"] + cohort["150"]
               if e["pdf_type"] == "LysingPdf"]

        def layout_of(e):
            return classify_layout(e["raw"], e["struct"], baseline_labels)

        risk = [e for e in cohort["100"] + cohort["150"]
                if e["needs_review"] or e["parse_status"] != "PARSED"
                or layout_of(e) != "KNOWN_LAYOUT"]
        sample = [(e, "RISK") for e in risk]
        for pool, tag, target in ((fel, "SAMPLE_FELAG", 50),
                                  (lys, "SAMPLE_LYSING", 50)):
            risk_keys = {(e["company"], e["pdf_type"], e["doc_key"], e["run"])
                         for e in risk}
            eligible = [e for e in pool
                        if (e["needs_review"] is not True
                            and e["parse_status"] == "PARSED"
                            and (e["company"], e["pdf_type"], e["doc_key"],
                                 e["run"]) not in risk_keys)]
            if len(eligible) > target:
                step = len(eligible) / target
                chosen = [eligible[int(i * step)]
                          for i in range(min(target, len(eligible)))]
            else:
                chosen = eligible
            sample.extend((e, tag) for e in chosen)
        index = []
        exported = set()
        for e, tag in sample:
            key = (e["company"], e["pdf_type"], e["doc_key"], e["run"])
            if key in exported:
                continue
            exported.add(key)
            name = f"c{e['company']}_{e['pdf_type']}_{e['doc_key']}_{e['run']}"
            (OUT / f"{name}.txt").write_text(e["raw"] or "", encoding="utf-8")
            index.append({
                "company": e["company"], "doc": e["doc_key"],
                "pdf_type": e["pdf_type"], "run": e["run"], "group": tag,
                "needs_review": e["needs_review"],
                "reasons": e["review_reasons"],
                "regnr": e["struct"].get("registration_number"),
                "layout": layout_of(e),
                "changes": len(e["struct"].get("changes") or []),
                "unknown_labels": extract_unknown_labels(e["struct"]),
                "file": f"{name}.txt"})
        (OUT / "review_index.json").write_text(
            json.dumps(index, indent=1, default=str, ensure_ascii=False),
            encoding="utf-8")
        risk_index = [r for r in index if r["group"] == "RISK"]
        results["manual_review_export"] = {
            "risk_100pct": len(risk_index),
            "felag_sample": len([r for r in index
                                 if r["group"] == "SAMPLE_FELAG"]),
            "lysing_sample": len([r for r in index
                                  if r["group"] == "SAMPLE_LYSING"]),
            "total_files": len(index)}

        # ---- safety -----------------------------------------------------------
        leaks = []
        token_re = re.compile(r"s=[A-Za-z0-9_-]{20,}")
        for detail, in conn.execute("SELECT coalesce(detail,'') FROM pdf_run_log"):
            if token_re.search(detail):
                leaks.append(detail[:60])
        for raw_text, in conn.execute("SELECT raw_text FROM pdf_parse_result"):
            if token_re.search(raw_text or ""):
                leaks.append((raw_text or "")[:50])
        refusals = conn.execute("""
            SELECT count(*) FROM pdf_run_log
            WHERE error_type LIKE 'SOURCE_REFUSAL%'
               OR coalesce(detail,'') LIKE 'SOURCE_REFUSAL%'""").fetchone()[0]
        results["safety_metrics"] = {"token_leaks": leaks,
                                     "source_refusals": refusals}

    # company_location after (read-only QA DB)
    with psycopg.connect(QA_DSN) as qa:
        row = qa.execute("""SELECT count(*), md5(string_agg(t::text, '|'
                            ORDER BY t::text)) FROM company_location t""").fetchone()
    before = json.load(open("validation_expansion_location_before.json"))
    after = {"rowcount": row[0], "checksum_md5": row[1]}
    results["company_location"] = {
        "before": before["company_location_before"], "after": after,
        "unchanged": after == before["company_location_before"]}

    json.dump(results, open("validation_250_results.json", "w"),
              indent=1, ensure_ascii=False, default=str)
    print(json.dumps({k: v for k, v in results.items()
                      if k != "temporal"}, indent=1, ensure_ascii=False,
                     default=str))


if __name__ == "__main__":
    main()