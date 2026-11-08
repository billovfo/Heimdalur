# HEIMDAL HUMAN-REVIEW PRE-P1 CONTRACT FREEZE v1

**Documentation/contract correction only — no code, no migration, P1 not started.**
Freezes 3 fixes on top of P0 audit (PASS WITH CORRECTIONS); preserves all P0
contracts (REVIEW-CASE-KEY-v1, ROLE:-ban, no name dedup, NOT-ELIGIBLE
populations, F3a +0 / F3b +25/+12, `apply_approved_human_links` boundary).

## Fix 1 — Score/application (G1/G2)

Frozen input carries `score_snapshot_ref` (opaque pointer to the sealed
score snapshot), never a live score value; retained historical score fields
are provenance bytes the application MUST NOT branch on. Full contract:
`p0/application_score_contract.json`. Guards extended accordingly.

## Fix 2 — Decision immutability (G3/G4/G11)

Append-only record; immutable payload (decision, choice, reviewer, time,
note, shown packet/alternatives/score-ref, evidence pin, reason incl.
NO_MATCH=REJECT+reason); controlled-mutable lifecycle metadata
(applied_run, stale reason/at, batch) via named functions only. Third table
evaluated and rejected. Full contract:
`p0/decision_immutability_contract.json` (+ `final_two_table_contract.json`:
2 tables sufficient — versions in-row, payload/lifecycle split, supersede
chain).

## Fix 3 — Packet freeze (G5–G7)

Queue = derived summaries (no pre-generation of ~5,300). First open freezes
packet v1; display renders frozen packet; decisions pin shown_packet_hash;
regeneration appends sealed versions, never silent replace. Full contract:
`p0/packet_freeze_contract.json`. Not-eligible: 4 reason codes, computed +
reportable, never silently dropped (`p0/not_eligible_contract.json`).

## Synchronized docs (G8/G9/G13)

Updated: `data_model.md`, `architecture.md`, `projection_application.md`,
`review_lifecycle.md`, `api_plan.md`, `test_plan.md`, `score_v1.md`,
`implementation_phases.md` (now 5-phase, P1 scope frozen per
`p0/p1_scope_frozen.json`), plus plan md/json append-only sections.
Stale docs remaining: **0**. Phases: **5**. P1 ready: **YES**.

Gates G1–G17: all PASS. Next: **P1 persistence + case reader implementation**.
