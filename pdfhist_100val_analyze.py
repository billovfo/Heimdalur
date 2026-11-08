"""Post-run analysis for the 100-company validation (run 424243).

Reads ONLY: pdf_* tables in the validation DB (+ read-only QA heimdal for the
company_location integrity check). Produces validation_results.json and
exports the manual-review working set to tmp-100val-review/.

Owner's classification rule (analysis-level, NO schema changes):
  A  raw hash same                                  -> BYTE_IDENTICAL
  B  raw changed, normalized text same              -> BYTE_CHANGED_TEXT_SAME
  C  raw changed, text changed, structured same     -> TEXT_CHANGED_STRUCTURED_SAME
  D  structured_data changed                        -> SEMANTIC_CONTENT_CHANGED
"""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import psycopg

from heimdal.pdfhist.normalize import normalize_whitespace

DSN = "postgresql://postgres@localhost:5432/pdfhist_pilot"
QA_DSN = "postgresql://postgres@localhost:5432/heimdal"
RUN = 424243
OUT = Path("tmp-100val-review")


def text_hash(raw_text):
    normalized = "\n".join(normalize_whitespace(line)
                          for line in (raw_text or "").splitlines())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def struct_hash(struct):
    return hashlib.sha256(json.dumps(
        struct, sort_keys=True, ensure_ascii=False, default=str
    ).encode("utf-8")).hexdigest()


def field_diff(old, new):
    """Deterministic top-level(+1) JSON field diff - no generic platform."""
    diffs = []
    keys = sorted(set(old) | set(new))
    for key in keys:
        if key not in old:
            diffs.append(f"+{key}")
            continue
        if key not in new:
            diffs.append(f"-{key}")
            continue
        a, b = old[key], new[key]
        if isinstance(a, dict) and isinstance(b, dict):
            sub = sorted(set(a) | set(b))
            changed = [k for k in sub if a.get(k) != b.get(k)]
            if changed:
                diffs.append(f"{key}.{','.join(changed)}")
        elif a != b:
            diffs.append(key)
    return diffs


def observation_pairs(conn):
    """All consecutive observation pairs per logical document (all runs)."""
    rows = conn.execute("""
        SELECT d.id, d.pdf_type, d.source_document_id, o.id,
               o.collection_run_id, o.sha256
        FROM pdf_document_observation o
        JOIN pdf_document d ON d.id = o.document_id
        ORDER BY d.id, o.fetched_at, o.id""").fetchall()
    parses = {}
    for obs_id, raw_text, struct in conn.execute(
            "SELECT p.document_observation_id, p.raw_text,"
            " p.structured_data FROM pdf_parse_result p"
            " WHERE p.parser_version = '1'").fetchall():
        parses[obs_id] = (raw_text, struct if isinstance(struct, dict)
                          else json.loads(struct))
    pairs = []
    previous = None
    for doc_id, pdf_type, doc_key, obs_id, run_id, sha in rows:
        current = {"doc_id": doc_id, "pdf_type": pdf_type, "key": doc_key,
                   "obs_id": obs_id, "run": run_id, "sha": sha,
                   "parse": parses.get(obs_id)}
        if previous and previous["doc_id"] == doc_id and current["parse"] \
                and previous["parse"]:
            pairs.append((previous, current))
        previous = current
    return pairs


def classify_pair(prev, cur):
    if prev["sha"] == cur["sha"]:
        return "BYTE_IDENTICAL", None
    if text_hash(prev["parse"][0]) == text_hash(cur["parse"][0]):
        return "BYTE_CHANGED_TEXT_SAME", None
    if struct_hash(prev["parse"][1]) == struct_hash(cur["parse"][1]):
        return "TEXT_CHANGED_STRUCTURED_SAME", None
    return "SEMANTIC_CONTENT_CHANGED", field_diff(prev["parse"][1],
                                                  cur["parse"][1])


def felag_label_coverage(parses):
    """parses: list of (company, doc_key, raw_text, struct) for FelagPdf."""
    labels = {
        "Dagf.": ("Dagf.", lambda s: bool(s.get("snapshot_date"))),
        "Skrásetingar-nr": ("Skrásetingar-nr",
                            lambda s: bool(s.get("registration_number"))),
        "Navn": ("Navn:", lambda s: bool(s.get("company_name"))),
        "Adressa": ("Adressa:", lambda s: bool(s.get("company_address"))),
        "Postrúm": ("Postrúm",
                    lambda s: bool((s.get("company_address") or {})
                                   .get("postrum"))),
        "Kommuna": ("Kommuna:",
                    lambda s: bool((s.get("company_address") or {})
                                   .get("kommuna"))),
        "Nevnd": ("Nevnd:", lambda s: len(s.get("board") or []) > 0),
        "Stjórn": ("Stjórn:", lambda s: len(s.get("management") or []) > 0),
        "Partapeningur": ("Partapeningur:", lambda s: bool(s.get("capital"))),
        "Endamál": ("Endamál:", lambda s: bool(s.get("purpose"))),
        "Tekningarreglur": ("Tekningarreglur:",
                            lambda s: bool(s.get("signing_rules"))),
        "Roknskapir dato": ("Roknskapir dato:",
                            lambda s: len(s.get("accounts_dates") or []) > 0),
    }
    coverage = {name: {"containing": 0, "structured": 0, "unknown": 0,
                       "review": 0} for name in labels}
    unknown_labels = Counter()
    direct_stjori = 0
    for company, doc_key, raw, struct in parses:
        for name, (marker, check) in labels.items():
            if marker in raw:
                coverage[name]["containing"] += 1
                if check(struct):
                    coverage[name]["structured"] += 1
                else:
                    coverage[name]["unknown"] += 1
        if struct.get("needs_review"):
            for name in coverage:
                if labels[name][0] in raw:
                    coverage[name]["review"] += 1
        for section in struct.get("unknown_sections") or []:
            label = section.get("label", "?")
            unknown_labels[label] += 1
        if "Stjórn:" not in raw and "Stjóri:" in raw \
                and len(struct.get("management") or []) > 0:
            direct_stjori += 1
    return coverage, unknown_labels, direct_stjori


def main():
    OUT.mkdir(exist_ok=True)
    results = {"run": RUN}

    with psycopg.connect(DSN) as conn:
        # -- pair classification ------------------------------------------------
        pairs = observation_pairs(conn)
        classes = Counter()
        felag_classes = Counter()
        semantic_cases = []
        text_changed_cases = []
        for prev, cur in pairs:
            label, diff = classify_pair(prev, cur)
            classes[label] += 1
            if cur["pdf_type"] == "FelagPdf":
                felag_classes["FELAG_" + label] += 1
            if label == "SEMANTIC_CONTENT_CHANGED":
                semantic_cases.append({
                    "pdf_type": cur["pdf_type"], "doc": cur["key"],
                    "runs": [prev["run"], cur["run"]],
                    "sha_prev": prev["sha"][:12], "sha_cur": cur["sha"][:12],
                    "fields": diff})
            if label == "TEXT_CHANGED_STRUCTURED_SAME":
                text_changed_cases.append({
                    "pdf_type": cur["pdf_type"], "doc": cur["key"],
                    "runs": [prev["run"], cur["run"]]})
        results["observation_pairs"] = {
            "total_pairs": len(pairs),
            "classification": dict(classes),
            "felag_classification": dict(felag_classes),
            "semantic_cases": semantic_cases,
            "text_changed_cases": text_changed_cases}

        # -- run metrics ---------------------------------------------------------
        records = conn.execute("""
            SELECT count(*) FILTER (WHERE parse_status='PARSED'),
                   count(*) FILTER (WHERE parse_status='REVIEW'),
                   count(*) FILTER (WHERE parse_status='FAILED')
            FROM pdf_parse_result p
            JOIN pdf_document_observation o ON o.id=p.document_observation_id
            WHERE o.collection_run_id = %s""", (RUN,)).fetchone()
        results["parse_counts"] = {"PARSED": records[0],
                                  "REVIEW": records[1], "FAILED": records[2]}

        obs = conn.execute("""SELECT count(*),
            count(DISTINCT sha256) FROM pdf_document_observation
            WHERE collection_run_id = %s""", (RUN,)).fetchone()
        results["observations_run"] = {"observations": obs[0],
                                       "unique_raw_hashes": obs[1]}

        docs = conn.execute("""SELECT d.pdf_type, count(DISTINCT o.document_id)
            FROM pdf_document_observation o
            JOIN pdf_document d ON d.id = o.document_id
            WHERE o.collection_run_id = %s GROUP BY 1""", (RUN,)).fetchall()
        results["fetched_documents_by_type"] = {r[0]: r[1] for r in docs}

        queue = conn.execute("SELECT issue, count(*) FROM pdf_review_queue"
                             " GROUP BY 1 ORDER BY 1").fetchall()
        results["review_queue"] = {r[0]: r[1] for r in queue}

        run_logs = conn.execute("""SELECT step, status, error_type, count(*)
            FROM pdf_run_log WHERE run_id = %s GROUP BY 1,2,3
            ORDER BY 1,2,3""", (RUN,)).fetchall()
        results["run_log"] = [list(r) for r in run_logs]

        events = conn.execute("SELECT count(*) FROM pdf_source_event"
                              ).fetchone()[0]
        results["source_events_total"] = events

        junctions = conn.execute("SELECT count(*) FROM"
                                 " pdf_source_event_document").fetchone()[0]
        results["junctions_total"] = junctions

        # -- attribution gate --------------------------------------------------
        gate_rows = conn.execute("""
            SELECT DISTINCT ON (p.id) e.company_id, d.pdf_type,
                   d.source_document_id, e.row_skrasnr,
                   p.structured_data->>'registration_number' AS parsed_regnr,
                   p.needs_review
            FROM pdf_parse_result p
            JOIN pdf_document_observation o ON o.id = p.document_observation_id
            JOIN pdf_document d ON d.id = o.document_id
            JOIN pdf_source_event_document sed ON sed.document_id = d.id
            JOIN pdf_source_event e ON e.id = sed.source_event_id
            WHERE o.collection_run_id = %s
            ORDER BY p.id, e.company_id""", (RUN,)).fetchall()
        verified = mismatch = missing = 0
        mismatch_cases = []
        for company, pdf_type, doc, row_regnr, parsed, review in gate_rows:
            parsed = (parsed or "").strip().lower()
            row_regnr = (row_regnr or "").strip().lower()
            if not parsed:
                missing += 1
                mismatch_cases.append({"company": company, "doc": doc,
                                       "kind": "MISSING_REGNR",
                                       "needs_review": review})
            elif parsed != row_regnr:
                mismatch += 1
                mismatch_cases.append({"company": company, "doc": doc,
                                       "kind": "MISMATCH",
                                       "parsed": parsed, "row": row_regnr})
            else:
                verified += 1
        results["attribution"] = {
            "verified": verified, "mismatch": mismatch,
            "missing_regnr": missing,
            "cases": mismatch_cases}


        # -- felag + lysing parse inventories (run docs only) ------------------
        felag_parses, lysing_parses = [], []
        rows = conn.execute("""
            SELECT DISTINCT ON (p.id) e.company_id, d.source_document_id,
                   d.pdf_type, p.raw_text, p.structured_data
            FROM pdf_parse_result p
            JOIN pdf_document_observation o ON o.id = p.document_observation_id
            JOIN pdf_document d ON d.id = o.document_id
            JOIN pdf_source_event_document sed ON sed.document_id = d.id
            JOIN pdf_source_event e ON e.id = sed.source_event_id
            WHERE o.collection_run_id = %s
            ORDER BY p.id, e.company_id""", (RUN,)).fetchall()
        for company, doc_key, pdf_type, raw, struct in rows:
            struct = struct if isinstance(struct, dict) else json.loads(struct)
            entry = (company, doc_key, raw, struct)
            (felag_parses if pdf_type == "FelagPdf"
             else lysing_parses).append(entry)
        coverage, unknown_labels, direct_stjori = \
            felag_label_coverage(felag_parses)
        results["felag_label_coverage"] = coverage
        results["felag_unknown_labels"] = dict(unknown_labels)
        results["felag_direct_stjori_docs"] = direct_stjori

        change_types = Counter()
        change_actions = Counter()
        zero_change_docs = []
        lysing_unknown = Counter()
        new_phrases = Counter()
        known_label_names = set()
        from heimdal.pdfhist.parse import (
            _FELAG_KNOWN_LABELS, _LYSING_KNOWN_LABELS, LYSING_MEMBERSHIP_LABELS,
            SCALAR_LYSING_LABELS, LYSING_OP_TABLE,
            _ONELINE_SPLIT_WITH_COLON)
        known_label_names |= _FELAG_KNOWN_LABELS | _LYSING_KNOWN_LABELS \
            | set(LYSING_MEMBERSHIP_LABELS) | set(SCALAR_LYSING_LABELS) \
            | set(LYSING_OP_TABLE) | set(_ONELINE_SPLIT_WITH_COLON) \
            | {"Stjóri", "Stovningardato", "Viðtøkudato", "Stovnarar",
               "Kapitalur", "Inngjaldingarháttur", "Roknskaparár",
               "Fyrsta roknskaparár", "Skrásetingar-nr.", "Dagf."}
        for company, doc_key, raw, struct in lysing_parses:
            changes = struct.get("changes") or []
            if not changes:
                zero_change_docs.append({"company": company, "doc": doc_key,
                                         "labels": [u.get("label")
                                                    for u in struct.get(
                                                        "unknown_sections")
                                                    or []]})
            for change in changes:
                change_types[change["type"]] += 1
                change_actions[change["action"]] += 1
            for section in struct.get("unknown_sections") or []:
                label = (section.get("label") or "?").strip()
                lysing_unknown[label] += 1
                if label not in known_label_names and not label.startswith("("):
                    new_phrases[label] += 1
        results["lysing_changes"] = {
            "docs": len(lysing_parses),
            "change_type_counts": dict(change_types),
            "change_action_counts": dict(change_actions),
            "zero_change_docs": zero_change_docs,
            "unknown_label_counts": dict(lysing_unknown),
            "new_label_phrases": dict(new_phrases)}

        # -- temporal candidates: companies with multi-change history ---------
        from heimdal.pdfhist.temporal import build_change_history
        temporal = {}
        per_company_changes = {}
        for company, doc_key, raw, struct in lysing_parses:
            per_company_changes.setdefault(company, []).extend(
                struct.get("changes") or [])
        ranked = sorted(per_company_changes.items(),
                        key=lambda kv: -len(kv[1]))[:15]
        for company, changes in ranked:
            if len(changes) < 2:
                continue
            history = build_change_history(changes)
            temporal[str(company)] = {
                "changes": len(changes),
                "entries": history["entries"],
                "review": history["review"]}
        results["temporal_candidates"] = temporal

        # -- manual review export ----------------------------------------------
        flagged, felag_sample, lysing_sample = [], [], []
        review_flags = {c["doc"] for c in mismatch_cases}
        for company, doc_key, raw, struct in felag_parses + lysing_parses:
            if struct.get("needs_review") or doc_key in review_flags:
                flagged.append((company, doc_key, raw, struct,
                                "FLAGGED"))
        def pick(items, target):
            if len(items) <= target:
                return items
            step = len(items) / float(target)
            return [items[int(i * step)] for i in range(target)]
        parsed_felag = [e for e in felag_parses
                        if not e[3].get("needs_review")]
        parsed_lysing = [e for e in lysing_parses
                         if not e[3].get("needs_review")]
        for company, doc_key, raw, struct in pick(parsed_felag, 30):
            felag_sample.append((company, doc_key, raw, struct, "SAMPLE_FELAG"))
        for company, doc_key, raw, struct in pick(parsed_lysing, 30):
            lysing_sample.append((company, doc_key, raw, struct,
                                  "SAMPLE_LYSING"))
        index = []
        for company, doc_key, raw, struct, group in (
                flagged + felag_sample + lysing_sample):
            name = f"c{company}_{doc_key}"
            (OUT / f"{name}.txt").write_text(raw, encoding="utf-8")
            index.append({
                "company": company, "doc": doc_key, "group": group,
                "needs_review": struct.get("needs_review"),
                "reasons": struct.get("review_reasons"),
                "regnr": struct.get("registration_number"),
                "changes": len(struct.get("changes") or []),
                "change_types": sorted({c["type"] for c in
                                        struct.get("changes") or []}),
                "unknown_labels": [u.get("label") for u in
                                   struct.get("unknown_sections") or []],
                "file": f"{name}.txt"})
        json.dump(index, open(OUT / "review_index.json", "w"), indent=1,
                  default=str, ensure_ascii=False)
        results["manual_review_export"] = {
            "flagged": len(flagged),
            "felag_sample": len(felag_sample),
            "lysing_sample": len(lysing_sample)}

        # -- safety: token sweep + company_location after ----------------------
        token_re = re.compile(r"s=[A-Za-z0-9_-]{20,}")
        leaks = []
        for detail, in conn.execute(
                "SELECT coalesce(detail,'') FROM pdf_run_log"):
            if token_re.search(detail):
                leaks.append(detail[:50])
        for raw_text, in conn.execute(
                "SELECT raw_text FROM pdf_parse_result"):
            if token_re.search(raw_text or ""):
                leaks.append(raw_text[:50])
        results["token_leaks"] = leaks

    with psycopg.connect(QA_DSN) as qa:
        row = qa.execute("SELECT count(*), md5(string_agg(t::text, '|'"
                         " ORDER BY t::text)) FROM company_location t"
                         ).fetchone()
        results["company_location_after"] = {
            "rowcount": row[0], "checksum_md5": row[1]}

    json.dump(results, open("validation_results.json", "w"), indent=1,
              default=str, ensure_ascii=False)
    print(json.dumps(results, indent=1, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()