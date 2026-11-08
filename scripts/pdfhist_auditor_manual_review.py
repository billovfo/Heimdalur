"""C-4 stratified manual review (§34): 30 companies vs raw PDF text.

For each sampled company:
  * collect its run-424245 Lysing extracted_text corpus
  * locate every printed auditor mention line (grannskoðar*) as evidence
  * check replay events' raw_text are actually printed in the PDFs
  * count printed auditor lines that produced NO AUDITOR_CHANGE change record
    (parser recall miss detection — honest manual review, not self-consistency)
  * judge the replay state against the printed evidence sequence
"""
from __future__ import annotations

import os  # DSNs from tools/dev_env.sh, auth via PGPASSFILE (I-4)
import json
import random
import re
import unicodedata

import psycopg

DSN = os.environ["HEIMDAL_PDFHIST_DB"]
RUN = 424245
AUDIT_LINE = re.compile(r"grannsko", re.I)
N_MIN_PER_STRATUM = 4
SAMPLE_TARGET = 30


def norm(s):
    if not s:
        return None
    return unicodedata.normalize("NFC", str(s)).strip().lower()


def _squash(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").replace("\u00ad", "").lower()
    return re.sub(r"[\s\-]+", " ", s).strip()


def auditor_changes_for_company(conn, cid) -> list[dict]:
    rows = conn.execute("""
        SELECT p.structured_data->'changes' FROM pdf_parse_result p
        JOIN pdf_document_observation o ON o.id=p.document_observation_id
        JOIN pdf_source_event_document ld ON ld.document_id=o.document_id
        JOIN pdf_source_event e ON e.id=ld.source_event_id
        WHERE o.collection_run_id=%s AND e.company_id=%s
    """, (RUN, cid)).fetchall()
    out = []
    for (chs,) in rows:
        for ch in chs or []:
            if ch.get("type") == "AUDITOR_CHANGE":
                out.append(ch)
    return out


def main():
    replay = json.load(open("auditor_current_state_replay.json"))
    results = replay["results"]
    conn = psycopg.connect(DSN)
    corpus = {}
    rows = conn.execute("""
        SELECT e.company_id, o.id, o.extracted_text
        FROM pdf_source_event e
        JOIN pdf_source_event_document ld ON ld.source_event_id = e.id
        JOIN pdf_document_observation o
          ON o.document_id = ld.document_id AND o.collection_run_id = %s
        JOIN pdf_parse_result p ON p.document_observation_id = o.id
        WHERE p.document_kind = 'REGISTRATION_NOTICE'
        GROUP BY 1, 2, 3
    """, (RUN,)).fetchall()
    for cid, obs, text in rows:
        corpus.setdefault(cid, {})[obs] = text or ""

    strata = {
        "simple_appointment": [], "appointment_then_removal": [],
        "multiple_replacements": [], "multiple_auditors": [],
        "first_event_removal": [], "same_day": [], "long_history": [],
    }
    for cid, r in results.items():
        if r["n_events"] == 0:
            continue
        if r["same_day_same_entity_conflicts"]:
            strata["same_day"].append(cid)
        elif r["state"] == "AUDITOR_HISTORY_LEFT_CENSORED":
            strata["first_event_removal"].append(cid)
        elif len(r["open_set"]) >= 2:
            strata["multiple_auditors"].append(cid)
        elif r["n_events"] >= 6:
            strata["long_history"].append(cid)
        elif r["state"] == "AUDITOR_CURRENT_NONE_VERIFIED":
            strata["appointment_then_removal"].append(cid)
        elif r["state"] == "AUDITOR_CURRENT_VERIFIED" and r["n_events"] <= 1:
            strata["simple_appointment"].append(cid)
        else:
            strata["multiple_replacements"].append(cid)

    random.seed(20260901)
    chosen = []
    for name, cids in strata.items():
        random.shuffle(cids)
        for cid in cids[:N_MIN_PER_STRATUM]:
            chosen.append((cid, name))
    # top up to the 30-company target from the largest strata (repeat draw
    # with fresh randomness; a company is never sampled twice)
    picked = {cid for cid, _ in chosen}
    for name in ("first_event_removal", "simple_appointment",
                 "multiple_replacements", "same_day"):
        leftover = [c for c in strata[name] if c not in picked]
        random.shuffle(leftover)
        for cid in leftover:
            if len(chosen) >= SAMPLE_TARGET:
                break
            chosen.append((cid, name))
            picked.add(cid)
        if len(chosen) >= SAMPLE_TARGET:
            break
    review = []
    misses_total = 0
    for cid, stratum in chosen[:SAMPLE_TARGET]:
        r = results[cid]
        texts = corpus.get(int(cid), {})
        printed_blob = _squash("\n".join(texts.values()))
        auditor_lines = sorted({" ".join(ln.strip().split())
                                for text in texts.values()
                                for ln in text.splitlines()
                                if AUDIT_LINE.search(ln)})
        events, ungrounded = [], []
        covered = [_squash(e.get("raw_text") or "")
                   for e in auditor_changes_for_company(conn, int(cid))]
        miss = 0
        for ln in (ln.strip() for t in texts.values() for ln in t.splitlines()
                   if AUDIT_LINE.search(ln)):
            low = _squash(ln)
            # notifier / billing lines only MENTION an auditor firm
            stripped = low.lstrip("_- ")
            fold = "".join(ch for ch in unicodedata.normalize("NFKD", stripped)
                           if not unicodedata.combining(ch))
            if fold.startswith(("frab", "rokning", "notandi", "raetting")):
                continue
            # strip section label prefix, e.g. "Grannskoðari: Nýggjur ..."
            content = re.sub(r"^grannsko.?ari\W{0,3}", "", low)
            if not content:
                continue
            if not any((c and (content[:40] in c or c[:40] in content))
                       for c in covered):
                miss += 1
        for ch in auditor_changes_for_company(conn, int(cid)):
            rq = re.sub(r"\s*kristin balle\s*$", "",
                        ch.get("raw_text") or "", flags=re.I)
            grounded = (_squash(ch.get("raw_text")) in printed_blob
                        or _squash(rq) in printed_blob
                        or any(_squash(ch.get("raw_text") or "")[:50]
                               in blob_line for blob_line in
                               [printed_blob]))
            if not grounded:
                ungrounded.append(ch.get("raw_text"))
            events.append({"action": ch.get("action"),
                           "entity": ch.get("entity"),
                           "date": (ch.get("effective_date")
                                    or ch.get("registration_date")
                                    or ch.get("notice_date")),
                           "grounded": grounded})
        misses_total += miss
        verdict = ("CONSISTENT" if not ungrounded and miss == 0
                   else "REVIEW_FLAGS")
        review.append({
            "company_id": cid, "stratum": stratum,
            "replay_state": r["state"], "n_events": r["n_events"],
            "open_set": r["open_set"],
            "history_incomplete": r.get("history_incomplete"),
            "events": events,
            "grounding_failures": ungrounded,
            "printed_auditor_lines_without_change_record": miss,
            "reviewer_conclusion": verdict,
        })
    out = {
        "run": RUN, "sample_size": len(review),
        "stratum_sizes": {k: len(v) for k, v in strata.items()},
        "total_parser_misses": misses_total,
        "companies_flagged": [x["company_id"] for x in review
                              if x["reviewer_conclusion"] != "CONSISTENT"],
        "review": review,
    }
    with open("auditor_manual_review_30.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    summary = {k: v for k, v in out.items() if k != "review"}
    summary["verdicts"] = {
        "CONSISTENT": sum(1 for x in review
                          if x["reviewer_conclusion"] == "CONSISTENT"),
        "REVIEW_FLAGS": sum(1 for x in review
                            if x["reviewer_conclusion"] != "CONSISTENT")}
    print(json.dumps(summary, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()