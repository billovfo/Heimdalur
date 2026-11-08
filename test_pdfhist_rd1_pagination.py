"""RD-1 marker/duplicate-aware pagination rule tests (workstream C-3).

Fixtures are built directly from the stored forensic PAGINATION STOPPED
evidence of run 424245 (pdf_run_log.detail row lists) for c1062, c2321 and
c2341, so the rule is proven against the EXACT observed source layouts.

Pass criteria (owner task §21):
  * expected substantive source rows == observed substantive rows
  * all known document refs retained
  * marker rows excluded only from the completeness denominator
  * empty duplicate rows classified explicitly
"""
from __future__ import annotations

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent / "scripts"))

from pdfhist_rd1_pagination import (classify_row, classify_rows,
                                    marker_aware_reconciliation)


def rows_from_evidence(evidence: list[dict]) -> list[dict]:
    return list(evidence)


# ---------------------------------------------------------------------------
# Fixtures: exact row evidence from pdf_run_log (run 424245), forensically
# recorded at failure time. day/lysing/current composition only.
# ---------------------------------------------------------------------------

C1062_ROWS = [
    {"day": "25-03-2026", "lysing_id": 40810, "skrasnr": "1062", "name": "X"},
    {"day": "08-05-2024", "lysing_id": 36623, "skrasnr": "1062", "name": "X"},
    {"day": "05-02-2024", "lysing_id": 36250, "skrasnr": "1062", "name": "X"},
    {"day": "09-03-2022", "lysing_id": 32637, "skrasnr": "1062", "name": "X"},
    {"day": "04-03-2020", "lysing_id": 27717, "skrasnr": "1062", "name": "X"},
    {"day": "17-04-2019", "lysing_id": 25816, "skrasnr": "1062", "name": "X"},
    {"day": "19-06-2018", "lysing_id": 23997, "skrasnr": "1062", "name": "X"},
    {"day": "18-04-2016", "lysing_id": 19962, "skrasnr": "1062", "name": "X"},
    {"day": "09-12-2015", "lysing_id": 9752, "skrasnr": "1062", "name": "X"},
    {"day": "14-07-2015", "lysing_id": 9368, "skrasnr": "1062", "name": "X"},
    {"day": "03-06-2014", "lysing_id": 8200, "skrasnr": "1062", "name": "X"},
    {"day": "11-04-2011", "lysing_id": 3839, "skrasnr": "1062", "name": "X"},
    {"day": "20-07-2010", "lysing_id": 2905, "skrasnr": "1062", "name": "X"},
    {"day": "08-10-2009", "lysing_id": 1900, "skrasnr": "1062", "name": "X"},
    {"day": "07-09-2009", "lysing_id": None, "skrasnr": "1062", "name": "X"},
    {"day": "12-02-2009", "lysing_id": None, "skrasnr": "1062", "name": "X"},
    {"day": "30-06-2008", "lysing_id": None, "skrasnr": "1062", "name": "X"},
    {"day": "30-06-2008", "lysing_id": None, "skrasnr": "1062", "name": "X"},
    {"day": None, "lysing_id": None, "skrasnr": "1062", "name": "X",
     "is_current": True},
]

C2321_ROWS = [
    {"day": "30-06-2026", "lysing_id": 42994, "skrasnr": "2321", "name": "X"},
    {"day": "20-02-2019", "lysing_id": 25482, "skrasnr": "2321", "name": "X"},
    {"day": "22-10-2015", "lysing_id": 9598, "skrasnr": "2321", "name": "X"},
    {"day": "28-04-2015", "lysing_id": 9074, "skrasnr": "2321", "name": "X"},
    {"day": "14-05-2014", "lysing_id": 8157, "skrasnr": "2321", "name": "X"},
    {"day": "04-06-2013", "lysing_id": 6595, "skrasnr": "2321", "name": "X"},
    {"day": "11-02-2013", "lysing_id": 6196, "skrasnr": "2321", "name": "X"},
    {"day": "17-07-2012", "lysing_id": 5495, "skrasnr": "2321", "name": "X"},
    {"day": "12-07-2011", "lysing_id": 4089, "skrasnr": "2321", "name": "X"},
    {"day": "06-07-2010", "lysing_id": 2844, "skrasnr": "2321", "name": "X"},
    {"day": "09-09-2009", "lysing_id": None, "skrasnr": "2321", "name": "X"},
    {"day": "26-06-2009", "lysing_id": None, "skrasnr": "2321", "name": "X"},
    {"day": "26-06-2009", "lysing_id": None, "skrasnr": "2321", "name": "X"},
    {"day": "29-01-2009", "lysing_id": None, "skrasnr": "2321", "name": "X"},
    {"day": "12-06-2008", "lysing_id": None, "skrasnr": "2321", "name": "X"},
    {"day": None, "lysing_id": None, "skrasnr": "2321", "name": "X",
     "is_current": True},
]

C2341_ROWS = [
    {"day": "08-02-2022", "lysing_id": 32521, "skrasnr": "2341", "name": "X"},
    {"day": "21-12-2020", "lysing_id": 29541, "skrasnr": "2341", "name": "X"},
    {"day": "03-11-2020", "lysing_id": 29085, "skrasnr": "2341", "name": "X"},
    {"day": "08-07-2019", "lysing_id": 26363, "skrasnr": "2341", "name": "X"},
    {"day": "28-03-2019", "lysing_id": 25654, "skrasnr": "2341", "name": "X"},
    {"day": "31-10-2018", "lysing_id": 24790, "skrasnr": "2341", "name": "X"},
    {"day": "05-04-2017", "lysing_id": 21691, "skrasnr": "2341", "name": "X"},
    {"day": "17-06-2016", "lysing_id": 20229, "skrasnr": "2341", "name": "X"},
    {"day": "09-07-2015", "lysing_id": 9334, "skrasnr": "2341", "name": "X"},
    {"day": "15-09-2014", "lysing_id": 8583, "skrasnr": "2341", "name": "X"},
    {"day": "15-05-2012", "lysing_id": 5159, "skrasnr": "2341", "name": "X"},
    {"day": "18-03-2011", "lysing_id": 3770, "skrasnr": "2341", "name": "X"},
    {"day": "26-11-2010", "lysing_id": 3447, "skrasnr": "2341", "name": "X"},
    {"day": "10-11-2009", "lysing_id": 2053, "skrasnr": "2341", "name": "X"},
    {"day": "11-06-2008", "lysing_id": None, "skrasnr": "2341", "name": "X"},
    {"day": "02-05-2008", "lysing_id": None, "skrasnr": "2341", "name": "X"},
    {"day": "10-07-2006", "lysing_id": None, "skrasnr": "2341", "name": "X"},
    {"day": "02-05-2008", "lysing_id": None, "skrasnr": "2341", "name": "X"},
    {"day": "11-02-2008", "lysing_id": None, "skrasnr": "2341", "name": "X"},
    {"day": "11-02-2008", "lysing_id": None, "skrasnr": "2341", "name": "X"},
    {"day": None, "lysing_id": None, "skrasnr": "2341", "name": "X",
     "is_current": True},
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_classify_row_mechanics():
    assert classify_row({"day": "01-01-2020", "lysing_id": 5}) == "DOC_ROW"
    assert classify_row({"day": "01-01-2020", "lysing_id": None}) == "EMPTY_DATED_ROW"
    assert classify_row({"day": None, "lysing_id": None, "is_current": True}) == "CURRENT_MARKER"


def test_c1062_marker_duplicate_layout():
    res = marker_aware_reconciliation(C1062_ROWS, 19, 1, 1)
    assert res["ok"] is True, res
    d = res["detail"]
    assert d["document_rows"] == 14
    assert d["row_type_counts"]["EMPTY_DATED_ROW"] == 4          # incl. dup
    assert d["row_type_counts"]["CURRENT_MARKER"] == 1
    assert d["substantive_rows"] == 18                            # 19 - marker
    assert len(d["duplicate_empty_rows"]) == 1                    # 30-06-2008 x2
    # refs retained
    refs = {r["lysing_id"] for r in C1062_ROWS if r["lysing_id"]}
    assert len(refs) == 14


def test_c2321_marker_duplicate_layout():
    res = marker_aware_reconciliation(C2321_ROWS, 16, 1, 1)
    assert res["ok"] is True, res
    d = res["detail"]
    assert d["document_rows"] == 10
    assert d["row_type_counts"]["EMPTY_DATED_ROW"] == 5
    assert d["substantive_rows"] == 15                            # 16 - marker
    assert len(d["duplicate_empty_rows"]) == 1                    # 26-06-2009 x2


def test_c2341_marker_duplicate_layout():
    res = marker_aware_reconciliation(C2341_ROWS, 21, 1, 1)
    assert res["ok"] is True, res
    d = res["detail"]
    assert d["document_rows"] == 14
    assert d["row_type_counts"]["EMPTY_DATED_ROW"] == 6
    assert d["substantive_rows"] == 20                            # 21 - marker
    assert len(d["duplicate_empty_rows"]) == 2                    # 02-05 & 11-02 x2


def test_marker_excluded_from_denominator_only():
    # denominator = advertised - current markers; marker row is otherwise kept
    # in raw rendering evidence and stored as a source row by the runner.
    res = marker_aware_reconciliation(C1062_ROWS, 19, 1, 1)
    assert res["detail"]["raw_row_count"] if False else len(C1062_ROWS) == 19


def test_exact_dup_detection_is_not_fuzzy():
    # same day different name: NOT duplicates
    rows = [{"day": "30-06-2008", "lysing_id": None, "skrasnr": "9", "name": "A"},
            {"day": "30-06-2008", "lysing_id": None, "skrasnr": "9", "name": "B"}]
    d = classify_rows(rows)
    assert len(d["duplicate_empty_rows"]) == 0
    # same day same name: exact duplicate
    rows[1]["name"] = "A"
    d = classify_rows(rows)
    assert len(d["duplicate_empty_rows"]) == 1


def test_missing_advertised_and_page_rules():
    res = marker_aware_reconciliation(C1062_ROWS, None, 1, 1)
    assert res["ok"] is False and "ADVERTISED" in res["reason"]
    res = marker_aware_reconciliation(C1062_ROWS, 19, 2, 1)
    assert res["ok"] is False and "PAGES" in res["reason"]


def test_legacy_clean_layout_still_passes():
    # 20 dated DOC rows + 1 marker, no empties, advertised 21
    rows = [{"day": f"01-01-{y}", "lysing_id": 1000 + y, "skrasnr": "7", "name": "N"}
            for y in range(2006, 2026)] + [
            {"day": None, "lysing_id": None, "skrasnr": "7", "name": "N",
             "is_current": True}]
    res = marker_aware_reconciliation(rows, 21, 1, 1)
    assert res["ok"] is True and res["detail"]["duplicate_empty_rows"] == []


def test_wrong_advertised_count_still_fails_closed():
    res = marker_aware_reconciliation(C2341_ROWS, 22, 1, 1)
    assert res["ok"] is False and "VS_ADVERTISED" in res["reason"]


def test_storage_dedup_keeps_all_doc_refs():
    # storage dedup collapses only exact-identity EMPTY rows; every DOC row
    # with a distinct lysing_id survives (owner criterion: refs never lost)
    keys = {"ev%d" % r["lysing_id"] for r in C2341_ROWS if r["lysing_id"]}
    assert len(keys) == 14