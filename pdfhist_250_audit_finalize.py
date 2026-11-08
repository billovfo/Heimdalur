"""Close the 10-random-company audit JSON with a computed verdict, then leave
rendering to pdfhist_250_audit_render.py.

READ-ONLY audit: touches only the existing audit JSON export; no DB connection.

Audit 2026-11 J-2: this script used to clear every finding, mark every document
"manual control OK" and write a literal PASS with literal counts. Now the
verdict comes from the export's own mechanical results (registration gate,
findings). The manual control pass left no recorded evidence, so it is
NOT MEASURED, and the script never writes PASS.
"""
import json
from pathlib import Path

JSON_IN = Path("docs/HEIMDAL-PDF-HISTORIK-10-RANDOM-DATA-AUDIT-v1.json")
NOT_MEASURED = "NOT MEASURED"


def finalize(data):
    """Verdict from the export's mechanical results only (J-2)."""
    companies = data["companies"]
    gate = [g for c0 in companies for g in c0.get("registration_gate") or []]
    docs = [ci for c0 in companies for ci in c0.get("completeness") or []]
    findings = [f for c0 in companies for f in c0.get("audit_findings") or []]
    gate_failed = sum(1 for g in gate if not g["verified"])
    for c0 in companies:
        c0["manual_control_note"] = NOT_MEASURED
        for ci in c0.get("completeness") or []:
            ci["manual_agent_control"] = NOT_MEASURED
    data.pop("presentation_observations", None)  # asserted "not material" literals (J-2)
    data["mechanical_verification"] = {
        "registration_gate_checked": len(gate), "registration_gate_failed": gate_failed,
        "documents": len(docs),
        "documents_review_required": sum(ci["assessment"] == "REVIEW_REQUIRED" for ci in docs),
        "manual_agent_control": NOT_MEASURED}
    if gate_failed or findings:
        verdict = f"FAIL — {gate_failed} registration gate deviation(s), {len(findings)} finding(s)"
    else:
        verdict = f"{NOT_MEASURED} — no mechanical failure; the manual control pass left no evidence"
    data["audit_conclusion"] = {
        "verdict": verdict,
        "text": (f"Registration gate: {len(gate) - gate_failed}/{len(gate)} verified. "
                 f"Documents: {len(docs)}. Findings recorded: {len(findings)}. "
                 "Material correctness of the stored data was not measured."),
        "findings_total": len(findings)}
    data["status_line"] = f"10-RANDOM-COMPANY DATA AUDIT STATUS: {verdict}"
    return data


def main():
    data = finalize(json.loads(JSON_IN.read_text()))
    JSON_IN.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print(f"patched {JSON_IN} ({JSON_IN.stat().st_size} bytes)")


if __name__ == "__main__":
    main()