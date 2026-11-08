# HEIMDAL PERSON HUMAN-REVIEW SYSTEM PLAN v1 — PLANNING ONLY

No code written, nothing mutated. Grounded in: Flask portal
(`heimdal/app.py` factory + per-module Blueprints), projection lifecycle
(`heimdal/pdfhist/projection.py`: build/validate/activate/retire/
rollback_to/content_hash), link tiers (`entity_links.py::match_person`,
`build_entity_links`, `link_row_checksum`, methods EXACT_MULTI_ATTRIBUTE /
R-ADDR / COV-HISTORICAL), readers (`entities/service.py`,
`shared/entity.py`, `consumer.py`), templates (`base.html`, `person.html`
partials), and the completed program (673-queue, 62 strong reviewed, 16
proposed, H5/H4 ≫ H3 ≫ H2/H1/H0, 0 H6).

## Product goal (§1/§40)

One unresolved case at a time — inspect → decide → next — with complete
provenance. Success = safely reviewed identities per owner minute, not
maximum links. Binding (§2): automatic links ≠ human-approved links;
`HUMAN-REVIEW-PERSON-v1` provenance; score can never auto-link (§6,
mechanically enforced by import-graph separation + no threshold endpoint).

## Plan decisions D1–D25

- **D1 UI location:** extend portal — `Personer → Unresolved → Human
  review`; new `heimdal/modules/review/` Blueprint, reuse base/macros +
  person partials. **D24 MVP:** persistence, reader+score, queue+case UI,
  decisions+provenance, batch apply, tests. **D25 NOT-BUILD:** new
  service/app, ML scoring, threshold auto-approve, bulk-decide API, auth
  system, canonical-creation flow, mobile UX, event bus, Sona, analytics
  changes, generic adjudication platform.
- **D2 persistence:** 2 new tables (candidates/evidence as frozen JSONB in
  the case row — 4 tables rejected as overengineering). **D3 case identity:**
  one unresolved source identity = stable source key
  (`doc_sha|event_id|occ_index|norm_name|company_anchor`), never bare
  `person_role_id`, never normalized-name dedup; 1 case → N occurrences
  via occurrence list with per-occurrence provenance. **D4/D5:**
  candidates JSONB array (id, name, companies, roles, observations,
  per-candidate evidence); evidence JSONB in supporting/contradictory/
  missing buckets with source/provenance/retrieved-at/independence.
- **D6 lifecycle (shrunk to what the code does, owner D9 2026-11-06; the
  8-state case model is withdrawn):** a case has no lifecycle. Its row holds
  the sealed packet versions; its `status` is only ever written QUEUED. The
  queue is derived: UNRESOLVED, eligible occurrences of the ACTIVE run. Only
  the newest decision of a case is live. Decision `application_status`:
  PENDING → FROZEN (freeze) → APPLIED | CONFLICT | STALE (apply);
  PENDING → CONFLICT | STALE (freeze); PENDING/FROZEN/APPLIED → STALE when a
  newer decision supersedes it (`stale_reason` SUPERSEDED) or QA reopens it
  (QA-REOPENED). STALE and CONFLICT are terminal; a new decision is the way
  back. APPLIED = applied to run `applied_projection_run_id`, and every later
  link build re-applies it (D18, owner D10). Approvals only among the shown
  candidates (owner D15). **D20 stale:** freeze-time revalidation against the
  SHADOW target run; drift → STALE, never auto-applied.
- **D7/D8/D9 score v1:** `PERSON-REVIEW-SCORE-v1`, deterministic
  rules-based review-confidence 0–100 (F1 name +18, F2 variant +12, F3
  company +25, F4 role +12, F5 temporal +8, F6 official-external +10, F7
  R-ADDR address +5, F8 uniqueness +5; P1 alternatives −8 each/max −24, P2
  contradiction −25 each/max −50, P3 H1/H0-only ×0.7; contradiction caps
  49, name-only caps 35). Forbidden: network/social/ownership/popularity
  (grep-tested). **D10:** evidence first, score subordinate alongside with
  component breakdown; candidates alphabetical, never score-sorted (bias
  mitigation B+D: §17 — automation/confirmation/order/anchoring addressed;
  approve is arm+confirm, never single-key).
- **D11 queue:** expected review value (confidence × decisionability ÷
  effort): H5 → H4 → strong-H3 → rest, single-candidate first, score desc,
  stable `case_key` tiebreak; views Highest value/Needs review/Deferred/
  Ambiguous/Reviewed/Recently decided/Low value. **D12 keyboard:** A
  (arm)+Enter confirm, R/M/D, N/J/P/K, Esc; all actions also buttons.
- **D13 multi-candidate:** equal cards + A/B tabs, explicit choice or none/
  ambiguous/defer, per-candidate evidence. **D14 no-candidate:** NO MATCH
  (=REJECT w/ reason) or DEFER; canonical creation separate, not in MVP.
- **D15/D16 permissions:** no auth infra exists — MVP = local-owner model
  (same trust as `activate()` today), reviewer id recorded, four
  capability constants reserved; decide ≠ apply (separate endpoints +
  confirmations). **D17 provenance:** `link_method=
  HUMAN-REVIEW-PERSON-v1`, `match_metadata` carries case/decision/score/
  packet/owner-note. **D18 application:** one extra tier at the end of
  `build_entity_links` (automatic tiers win conflicts; never overwrite;
  checksum covers tier); freeze → validate → build → repeat → activate →
  analytics rebuild → repeat → activate. As built (audit 2026-11 W4a): the
  first application is the review batch (freeze → dry-run → apply, each
  naming a SHADOW `target_run_id`); the build tier re-applies the APPLIED
  approvals and records per-decision outcomes in the run's `metrics_json`
  (`human_review_tier`). **D19:** analytics unchanged.
- **D22 calibration:** ONLY after ≥300 owner decisions (≥60 approve +
  ≥60 reject; proposals excluded as labels); logistic on score components;
  precision/recall/Brier/reliability/FPR review before any `probability`
  wording. **D23 Sona:** evidence-source adapter boundary only;
  `SONA HIT ≠ IDENTITY PROOF` forever; not in MVP.

## Leanness challenge (§33)

No new service; portal routes extended; 2 tables; scoring deterministic
code; existing projection/analytics lifecycles reused unchanged. Small
review subsystem, not a platform. Scale (§34): ~5.3k unresolved — plain
SQL pagination + prefetch, immediate navigation, no distributed anything.
Privacy (§35): URL/minimal summary/type/timestamp only.

## Estimates (§39) + throughput

Backend 6–9d · scoring 3–4d · API 3–4d · UI 6–8d · projection integration
3–5d · tests 5–7d · acceptance 3d → **MVP 26–40 eng-days, critical path
~18–24d; full v1 (+dashboard/requeue/calibration-harness) 34–50d.**
Reviewer: high-confidence **20–30/hr**, ambiguous **6–10/hr** (≈16
proposals ⇒ first productive day; 673-queue ⇒ ~4–6 owner-days to clear
high-value bands). Production rollout excluded.

## Tests (§29/§30) + rollback (§31) + dashboard (§25) + history (§27)

Per `test_plan.md` (incl. adversarial: same-name/company collisions, equal
canonicals, stale/deleted targets, post-decision rescore, duplicate submit,
double apply, COMPANY-kind candidate blocked — all observed failure modes
from the execution program). Rollback per layer; decisions immutable
(supersede). Dashboard: counts only (remaining/reviewable/pending/applied/
rejected/ambiguous/deferred, time, approval by H-class/score band,
stale rate, calibration, cases/hour). History drawer per identity with
suppression of rejected candidates absent material change.

## P0 CORRECTION (2026-09-04, audit v1 — facts override §D3/D9/estimate/phases)

1. **Case identity:** universal `doc_sha|event|occ_index|norm|company` key
   withdrawn — CURRENT scope has no event id (verified: 7,759 CURRENT rows,
   event NULL) and `occurrence_index` is persisted nowhere (COV
   reconstructs it by name-scan, failing closed on duplicates). Replaced by
   source-specific **`REVIEW-CASE-KEY-v1`** (`PDF-HIST` / `PDF-CURRENT`
   contracts, positions persisted at generation). Ownership-derived
   identities are NOT-ELIGIBLE. `ROLE:<id>` confirmed per-build surrogate.
2. **Score:** F3 split into **F3a +0** (context display only) / **F3b
   +25/+12** (independent evidence); added contradiction cap 49 and weak
   caps (H1/H0 35, H2 55). Probes over 673 cases PASS.
3. **Application:** human step is standalone
   `heimdal/pdfhist/human_links.py::apply_approved_human_links` with
   frozen-decision input (not an inline tier); pytest import-graph guards.
4. **Estimate/phases:** lean MVP **22–31d** (best 22/expected 31/worst 44),
   full v1 30–44d, **5 phases** (P1–P5 per `p0/implementation_phases_revised.json`).
   12–20d not feasible (determinism proof, packet pinning, portal conventions).

## P0 IMPLEMENTATION CONTRACT FREEZE (2026-09-04, pre-P1 — 3 fixes, no redesign)

1. **Score/application:** frozen input carries `score_snapshot_ref` (opaque
   audit pointer), never raw score for branching; application code MUST NOT
   branch on score fields (APPLICATION-SCORE-v1). Guards extended to
   write-only provenance serializer + threshold-route grep.
2. **Immutability:** decision records are append-only with immutable payload
   (decision/choice/reviewer/time/note/shown packet+alternatives+score-ref/
   evidence pin) and controlled-mutable lifecycle metadata
   (applied_run/stale reason/batch) via named functions only. NO MATCH =
   REJECT + reason. No third table.
3. **Packet freeze:** queue = lightweight derived summaries (no
   pre-generation); first open materializes sealed packet_version=1;
   display renders frozen packet; decisions pin shown_packet_hash;
   regeneration appends sealed versions, never silent replace.
   Case table holds in-row `packet_versions[]`; not-eligible rows use 4
   reason codes (computed, reportable, never silently dropped).
   Full contracts: `docs/person-human-review-system/p0/` (6 freeze JSONs).
   Phases: 5 (P1 scope frozen in `p0/p1_scope_frozen.json`).
