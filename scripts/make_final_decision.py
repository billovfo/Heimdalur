import json

c = json.load(open('completeness_remediation_closure.json'))
b = json.load(open('historical_current_state_backtest.json'))
a = json.load(open('auditor_current_state_replay.json'))
v = json.load(open('human_pdf_render_verification.json'))

# J-2 (audit 2026-11): every verdict is computed from the four inputs, or is
# NOT MEASURED where they hold no evidence. No literal PASS.
NM = "NOT MEASURED"
OK_FIELD = {"EXACT_MATCH", "NORMALIZATION_ONLY"}


def verdict(ok, measured=True):
    return NM if not measured else ("PASS" if ok else "FAIL")


def field_defects(t):
    return sum(1 for f in t['field_comparison'].values() if f['class'] not in OK_FIELD)


c1_defects = sum(field_defects(t) for t in b['tests'])
c1 = verdict(c1_defects == 0, bool(b['tests']))
cls = v['classifications']
c2_material = cls.get('MATERIAL_VISUAL_EXTRACTION_ERROR')
c2 = verdict(c2_material == 0, v['total'] > 0 and c2_material is not None
             and sum(cls.values()) == v['total'])
c3_mismatches = c['attribution_run_424246']['mismatches']
c3_hard_stops = c['run']['hard_stops_403_429_retry_after']
c3_states = {x['company']: x['exception_final_state'] for x in c['before_after']}
c3_states["c6692"] = c['c6692']['classification']
c3_states["c9113"] = c['c9113']['final_state']
c3_safe = {"COMPLETED", "SOURCE_DATA_ANOMALY", "EXPECTED_NO_SOURCE_ROWS"}
c3 = verdict(c3_mismatches == 0 and c3_hard_stops == 0 and set(c3_states.values()) <= c3_safe)
c4_excluded = a['decision']['included_in_canonical_promotion'] is False
c4 = f"EXCLUDED (decision {a['decision']['decision']})" if c4_excluded else NM

decision = {
 "generated_for": "HEIMDAL PDF-HISTORIK FINAL CLOSURE AUDIT v1",
 "workstreams": {
  "C_1_historical_current_state_backtest": {
    "result": c1,
    "population": {"companies_with_snapshot_history":
                   b['population']['companies_with_snapshot_history'],
                   "snapshot_history_rows": b['population']['snapshot_history_rows'],
                   "eligible_A_lt_B": len(b['population']['eligible'])},
    "tests": [{"company_id": t['company_id'], "A": t['snapshot_A'],
               "B": t['snapshot_B'],
               "changes_in_window": t['n_changes_in_window'],
               "field_classes": {f: t['field_comparison'][f]['class']
                                 for f in t['field_comparison']},
               "projection_errors": field_defects(t), "result": t['result']}
              for t in b['tests']],
    "engine_fixtures": NM + " here: run test_pdfhist_c1_replay.py",
    "same_day_policy": "engine flags SAME_DAY_UNORDERED, never silently orders (fixture-bound)",
    "finding": {"id": "F-PHANTOM-BOARD", "severity": "medium", "systemic": True,
      "summary": ("282/6803 BOARD_CHANGE rows (164 APPOINTED + 118 REMOVED) have "
                  "phantom role-fragment entities e.g. '(nevndarlimur)': 4.15%"),
      "discovered_in": "auxiliary no-anchor replay c5273 (doc 7090)",
      "explanation_and_containment": (
        "root cause: parser glues role fragments from wrapped 'Farin ur nevndini:' "
        "lines into entity; contained because the section-46 promotion exposes "
        "board/management ONLY from FelagPdf snapshot parses, never from change replay")},
    "unexplained_material_projection_defects": c1_defects},
  "C_2_human_pdf_render_verification": {
    "result": c2, "n": v['total'],
    "classifications": v['classifications'],
    "material_extraction_errors": c2_material,
    "minor_artifacts": [
      "doc 15735 obs 7756: word-split inside 'Endamal'/'sae r' in purpose change "
      "raw_text (content verified against print)",
      "doc 15633 obs 7712: wrap-split 'nae stformaður' in board change raw_texts "
      "(content present)"]},
  "C_3_completeness_remediation": {
    "result": c3, "run_id": c['run']['run_id'], "concurrency": c['run']['concurrency'],
    "search_gap_s": c['run']['search_gap_s'], "pdf_gap_s": c['run']['pdf_gap_s'],
    "companies": [{"company": c['before_after'][i]['company'],
                   "exception_final_state": c['before_after'][i]['exception_final_state']}
                  for i in range(3)],
    "attribution_mismatches": c3_mismatches,
    "hard_stops_403_429": c3_hard_stops,
    "exceptions_final": c3_states,
    "append_only_disclosure": c['run']['attempts_disclosure']},
  "C_4_auditor_current_state_coverage": {
    "result": c4,
    "state_distribution": a['metrics']['state_distribution'],
    "manual_review": {"n": a['manual_review']['n'], "consistent": a['manual_review']['consistent'],
                      "flagged": a['manual_review']['flagged'],
                      "parser_misses": a['manual_review']['total_parser_misses']},
    "decision": a['decision']['statement'],
    "excluded_from_canonical_promotion": c4_excluded}}}

gate = {
 "C_1": f"{c1_defects} unexplained material projection defects -> {c1}",
 "C_2": f"{c2_material} material extraction defects -> {c2} "
        f"({cls.get('VISUALLY_VERIFIED')} verified / {cls.get('MINOR_VISUAL_EXTRACTION_ARTIFACT')} minor)",
 "C_3": f"exception states {sorted(set(c3_states.values()))}; {c3_mismatches} attribution "
        f"mismatches; {c3_hard_stops} hard stops -> {c3}",
 "C_4": f"auditor deterministically validated OR excluded -> {c4}"}
decision["promotion_gate_43"] = gate
results = (c1, c2, c3)
decision["terminal_status_45"] = (
    "FAIL" if "FAIL" in results else
    "PASS WITH EXCLUSIONS — READY FOR PROMOTION WITH DOCUMENTED FIELD LIMITATIONS"
    if set(results) == {"PASS"} and c4_excluded else NM)
decision["promotable_fields_46"] = {
 "from_felag_snapshot_parse": ["name", "aliases", "address", "company_form",
   "capital", "purpose", "signing_rules", "board", "management",
   "accounts_dates", "snapshot_date (Dagf.)"],
 "from_lysing_event_stream": [
   "event history rows (type/action/entity/dates/raw_text)",
   "auditor EVENT history with left-censor notice (not current state)"],
 "governance": "company_form must NOT be sourced from Felag projection evidence "
               "(section-39); board/management from snapshot parse only"}
decision["excluded_fields_46"] = {
 "excluded": [
   "AUDITOR CURRENT STATE (decision B: not safe for canonical use)",
   ("board/management CURRENT STATE derived from Lysing change replay alone "
    "(phantom-entity + implicit-replacement risk) - snapshot-sourced board IS promotable"),
   "c6692: zero-row result retained; later rows observed but NOT ingested "
   "(single observation)",
   "event-only types (ARTICLES_CHANGE/OTHER_CHANGE) as state mutations"]}

with open('final_closure_decision.json', 'w', encoding='utf-8') as fh:
    json.dump(decision, fh, indent=1, ensure_ascii=False)
print('final_closure_decision.json written; status:',
      decision['terminal_status_45'])