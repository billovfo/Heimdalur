"""J-2 (audit 2026-11): the closure/report/phase-0 scripts compute their verdicts.

Offline: make_final_decision.py runs on synthetic inputs in a temp dir; the other
two scripts need real audit JSON or a DB, so they are checked statically for a
literal PASS verdict.
"""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _inputs(material=0, field_class="EXACT_MATCH", tests=True):
    test = {"company_id": 1, "snapshot_A": "a", "snapshot_B": "b", "n_changes_in_window": 0,
            "field_comparison": {"name": {"class": field_class}}, "result": "EXACT_REPLAY_NO_CHANGES"}
    return {
        "historical_current_state_backtest.json": {
            "population": {"companies_with_snapshot_history": 1, "snapshot_history_rows": 2,
                           "eligible": [1]}, "tests": [test] if tests else []},
        "human_pdf_render_verification.json": {
            "total": 2, "classifications": {"VISUALLY_VERIFIED": 2 - material,
                                            "MINOR_VISUAL_EXTRACTION_ARTIFACT": 0,
                                            "MATERIAL_VISUAL_EXTRACTION_ERROR": material}},
        "completeness_remediation_closure.json": {
            "before_after": [{"company": f"c{i}", "exception_final_state": "COMPLETED"} for i in range(3)],
            "attribution_run_424246": {"mismatches": 0},
            "run": {"run_id": 1, "concurrency": 1, "search_gap_s": 1.0, "pdf_gap_s": 1.0,
                    "hard_stops_403_429_retry_after": 0, "attempts_disclosure": []},
            "c6692": {"classification": "SOURCE_DATA_ANOMALY"},
            "c9113": {"final_state": "EXPECTED_NO_SOURCE_ROWS"}},
        "auditor_current_state_replay.json": {
            "metrics": {"state_distribution": {}},
            "manual_review": {"n": 1, "consistent": 1, "flagged": [], "total_parser_misses": 0},
            "decision": {"decision": "B", "statement": "x", "included_in_canonical_promotion": False}},
    }


def _decide(tmp_path, **kw):
    for name, data in _inputs(**kw).items():
        (tmp_path / name).write_text(json.dumps(data))
    subprocess.run([sys.executable, "-I", str(ROOT / "scripts/make_final_decision.py")],
                   cwd=tmp_path, check=True, capture_output=True)
    d = json.loads((tmp_path / "final_closure_decision.json").read_text())
    return {k[:3]: v["result"] for k, v in d["workstreams"].items()}, d["terminal_status_45"]


def test_final_decision_passes_only_on_clean_inputs(tmp_path):
    results, status = _decide(tmp_path)
    assert results == {"C_1": "PASS", "C_2": "PASS", "C_3": "PASS", "C_4": "EXCLUDED (decision B)"}
    assert status.startswith("PASS WITH EXCLUSIONS")


def test_final_decision_fails_on_a_material_error(tmp_path):
    results, status = _decide(tmp_path, material=1)
    assert results["C_2"] == "FAIL" and status == "FAIL"
    results, status = _decide(tmp_path, field_class="MATERIAL_MISMATCH")
    assert results["C_1"] == "FAIL" and status == "FAIL"


def test_final_decision_without_evidence_is_not_measured(tmp_path):
    results, status = _decide(tmp_path, tests=False)
    assert results["C_1"] == "NOT MEASURED" and status == "NOT MEASURED"


def test_gen_report_has_no_literal_pass_verdict():
    tree = ast.parse((ROOT / "scripts/gen_report.py").read_text(encoding="utf-8"))
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    verdict = re.compile(r"\*\*PASS(\*\*| —)|alle opfyldt")  # "**PASS:** n" is a count label
    assert not [s for s in literals if verdict.search(s)]


def test_phase0_assumption_checks_are_computed():
    tree = ast.parse((ROOT / "tools/phase0_measure.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript)
                and getattr(node.targets[0].slice, "value", None) in ("assumption_checks", "phase0_status")):
            values = node.value.values if isinstance(node.value, ast.Dict) else [node.value]
            literal = [v.value for v in values if isinstance(v, ast.Constant)]
            assert not literal, literal
