"""C-1: historical current-state replay backtest (run-level).

Eligible population: companies with >= 2 FelagPdf snapshot rows in
company_pdf_snapshot_history whose valid_from dates differ (an earlier
snapshot A exists, then a later snapshot B with at least one Lysing change
window between them). Full enumeration first; then replay for each case.

Isolation: the projection input = snapshot A + Lysing VERIFIED evidence with
governing date in (A.date, B.date]. Snapshot B is compared ONLY after the
projection has been produced (facit).
"""
from __future__ import annotations

import os  # DSNs from tools/dev_env.sh, auth via PGPASSFILE (I-4)
import json
import re
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))

import psycopg
import unicodedata

from pdfhist_c1_replay import project, compare, norm_entity, event_date

DSN = os.environ["HEIMDAL_PDFHIST_DB"]
RUN = 424245


def squash(s):
    return re.sub(r"\s+", " ",
                  unicodedata.normalize("NFC", s or "")).strip().lower()


def main():
    conn = psycopg.connect(DSN)
    # -- 1. eligible population (FULL enumeration) ---------------------------
    rows = conn.execute("""
        SELECT company_id, count(DISTINCT valid_from::date) AS ddates,
               count(*)
        FROM company_pdf_snapshot_history
        GROUP BY 1 HAVING count(DISTINCT valid_from::date) > 1
        ORDER BY 1""").fetchall()
    # A<B needs distinct valid_from dates with parses on both sides
    eligible = []
    for cid, nd, n in rows:
        snaps = conn.execute("""
            SELECT s.valid_from::date, s.document_observation_id,
                   s.structured_data
            FROM company_pdf_snapshot_history s
            WHERE s.company_id=%s
            ORDER BY s.valid_from, s.document_observation_id""", (cid,)).fetchall()
        by_date = {}
        for vf, obs, sd in snaps:
            if sd is None:
                continue
            by_date.setdefault(str(vf), []).append(
                {"observation_id": obs,
                 "structured": sd if isinstance(sd, dict) else json.loads(sd)})
        if len(by_date) > 1:
            dates = sorted(by_date)
            eligible.append({"company_id": cid,
                             "A_date": dates[0], "B_date": dates[-1],
                             "A_obs": by_date[dates[0]][0]["observation_id"],
                             "B_obs": by_date[dates[-1]][0]["observation_id"],
                             "snapshot_rows": n})
    population = conn.execute("""
        SELECT count(DISTINCT company_id) FROM company_pdf_snapshot_history""").fetchone()[0]
    history_rows = conn.execute(
        "SELECT count(*) FROM company_pdf_snapshot_history").fetchone()[0]
    print("total distinct companies with snapshot history:", population,
          "rows:", history_rows)
    print("eligible A<B companies:", [e["company_id"] for e in eligible])

    results = []
    for e in eligible:
        a = conn.execute("""
            SELECT p.structured_data FROM company_pdf_snapshot_history s
            JOIN pdf_parse_result p ON
                 p.document_observation_id=s.document_observation_id
            WHERE s.company_id=%s AND s.document_observation_id=%s""",
            (e["company_id"], e["A_obs"])).fetchone()[0]
        b = conn.execute("""
            SELECT p.structured_data FROM company_pdf_snapshot_history s
            JOIN pdf_parse_result p ON
                 p.document_observation_id=s.document_observation_id
            WHERE s.company_id=%s AND s.document_observation_id=%s""",
            (e["company_id"], e["B_obs"])).fetchone()[0]
        if isinstance(a, str):
            a = json.loads(a)
        if isinstance(b, str):
            b = json.loads(b)
        # changes in (A_date, B_date] from VERIFIED Lysing observations
        evs = []
        ch_rows = conn.execute("""
            SELECT DISTINCT p.structured_data->'changes'
            FROM pdf_parse_result p
            JOIN pdf_document_observation o ON o.id=p.document_observation_id
            JOIN pdf_source_event_document ld ON ld.document_id=o.document_id
            JOIN pdf_source_event ev2 ON ev2.id=ld.source_event_id
            JOIN pdf_document d ON d.id=o.document_id
            WHERE ev2.company_id=%s AND o.collection_run_id=%s
              AND p.document_kind='REGISTRATION_NOTICE'
        """, (e["company_id"], RUN)).fetchall()
        for (chs,) in ch_rows:
            for ch in chs or []:
                evs.append(ch)
        keyed = [{"type": c.get("type"), "action": c.get("action"),
                  "entity": c.get("entity"),
                  "entity_normalized": c.get("entity_normalized"),
                  "new_value": c.get("entity"),
                  "raw_text": c.get("raw_text"),
                  "effective_date": c.get("effective_date"),
                  "registration_date": c.get("registration_date"),
                  "notice_date": c.get("notice_date")} for c in evs]
        in_window = [e2 for e2 in keyed if event_date(e2)
                     and e["A_date"] < event_date(e2) <= e["B_date"]]
        projected, flags, undated = project(a, in_window, e["A_date"])
        field_comparison = compare(projected, b)
        classes = {}
        for f, r2 in field_comparison.items():
            classes[f] = r2["class"]
        material_errors = [f for f, c in classes.items()
                           if c == "PROJECTION_ERROR"]
        results.append({
            "company_id": e["company_id"],
            "snapshot_A": {"observation_id": e["A_obs"], "date": e["A_date"]},
            "snapshot_B": {"observation_id": e["B_obs"], "date": e["B_date"]},
            "input_changes": in_window,
            "n_changes_in_window": len(in_window),
            "projected_state": projected,
            "expected_state": b,
            "field_comparison": field_comparison,
            "same_day_flags": flags,
            "uncertainties": (["NO_CHANGE_IN_WINDOW_EXACT_REPLAY"]
                              if not in_window else []),
            "result": ("EXACT_REPLAY_NO_CHANGES" if not in_window
                       and not material_errors else
                       "PROJECTION_ERROR" if material_errors
                       else "VERIFIED_REPLAY"),
        })
    out = {
        "population": {
            "companies_with_snapshot_history": population,
            "snapshot_history_rows": history_rows,
            "eligible_A_lt_B_companies": len(eligible),
            "eligible": eligible,
        },
        "methodology": __doc__.strip(),
        "tests": results,
    }
    with open("historical_current_state_backtest.json", "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    for r in results:
        print(r["company_id"], r["result"], "changes:", r["n_changes_in_window"],
              "classes:", sorted(set(r["field_comparison"][f]["class"]
                                     for f in r["field_comparison"])))


if __name__ == "__main__":
    main()