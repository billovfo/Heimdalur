"""J-2 (audit 2026-11), offline: the 10-random audit finalizer computes its
verdict from the export and never writes PASS. Synthetic export only."""
import copy
import json

from pdfhist_250_audit_finalize import finalize


def export(verified=True, findings=()):
    return {"companies": [{
        "registration_number": "1",
        "registration_gate": [{"verified": True}, {"verified": verified}],
        "completeness": [{"assessment": "FULLY_STRUCTURED", "manual_agent_control": None},
                         {"assessment": "REVIEW_REQUIRED", "manual_agent_control": None}],
        "audit_findings": list(findings)}],
        "presentation_observations": [{"id": "OBS-1", "material": False}]}


def test_clean_export_is_not_measured_never_pass():
    out = finalize(export())
    assert out["status_line"].startswith("10-RANDOM-COMPANY DATA AUDIT STATUS: NOT MEASURED")
    assert out["mechanical_verification"]["registration_gate_checked"] == 2
    assert out["mechanical_verification"]["documents_review_required"] == 1
    assert "presentation_observations" not in out
    assert all(ci["manual_agent_control"] == "NOT MEASURED" for ci in out["companies"][0]["completeness"])
    assert "PASS" not in json.dumps(out)


def test_gate_deviation_or_finding_fails_and_findings_are_kept():
    assert "FAIL — 1 registration gate" in finalize(export(verified=False))["status_line"]
    f = {"severity": "MATERIAL_ERROR"}
    out = finalize(export(findings=[f]))
    assert "1 finding(s)" in out["status_line"] and out["status_line"].split(": ")[1].startswith("FAIL")
    assert out["companies"][0]["audit_findings"] == [f]
    assert finalize(copy.deepcopy(out)) == out  # idempotent re-run
