# HEIMDAL REGISTRY PROJECTION — CONSUMER WIRING (SHADOW MODE, INTEGRATION OFF)

## 1. Scope

Application consumer/read-path mod den ACTIVE `registry_projection`
implementeret som et separat, read-only adapter-modul
(`heimdal/pdfhist/consumer.py`). Hele integrationen forbliver deaktiveret —
runtime-adfærden er uændret efter implementationen.

## 2. Owner authorization boundary

Owner godkendte udelukkende implementering af consumer-laget i shadow mode.
Ikke godkendt og ikke udført: enable-integration, enable-UI, API-exposure,
canonical mutation, projection rebuild, crawler/parser-arbejde.

## 3. Existing consumer path

Eksisterende Heimdal consumers (`heimdal/modules/*`, fx
`modules/entities/repository.py`) læser canonical QA-tabeller
(`company_inventory`, `canonical_entity`). Denne opgave har ikke ændret dem.
Adapteren tilbyder den feature-gated overlay-path, der kan tages i brug når
owner senere slår integrationen til.

## 4. Consumer module design

`heimdal/pdfhist/consumer.py` — klart adskilt fra projection builder:

- **Builder** = `heimdal/pdfhist/projection.py` + admin CLI (ejer lifecycle
  writes). **Consumer** = `consumer.py` (read-only adapter; ejer ingenting).
- Interface (§32): `get_projected_company_state(registration_number)`,
  `get_projected_company_field(registration_number, field)`,
  `get_projection_warnings(registration_number)` (admin/shadow-verification,
  IKKE user-facing), `get_projected_company_history(registration_number)`
  (SEPARAT, §21), `compare_with_canonical(registration_number)`,
  `shadow_compare_population()`, `merge_pdf_current_state()`.
- Egen-session er pinned `default_transaction_read_only = on` (§34); med
  caller-session bruges den som den er. Modulet udfører aldrig writes.

## 5. Feature gate

Al PDF-projection-consumption ligger bag `PDF_HISTORY_INTEGRATION_ENABLED`
via EKSISTERENDE `heimdal/pdfhist/flags.py` (ingen parallel
flag-implementation, §11). Resolution-fejl → behandlet som disabled (§27,
fail-closed, med operational event). `false` → projection-path påvirker IKKE
returned current state (§10).

## 6. Disabled-mode semantics

Integration=false → `get_projected_company_state` returnerer None og
`merge_pdf_current_state(canonical, None)` returnerer canonical uændret.
Ingen fill/silent enrichment/name/address/board override/warning-injection
fra projection (§12). Bevist af `test_consumer_disabled_mode_unchanged`
(§38-kernen: output før == output efter wiring).

## 7. Enabled-mode semantics

Deterministisk (§13): flag-override (test-only) → load ACTIVE via `v_active_run`
→ no projection row → canonical fallback (§25) → ellers eksplicit
field-by-field merge via `FIELD_CONTRACT`. Ingen generel coalesce (§14).

## 8. Field authority mapping

`FIELD_CONTRACT`: de 11 approved snapshot-felter = `PDF_AUTHORITATIVE`
(inkl. eksplicit KNOWN_EMPTY — ingen tavs fallback til canonical);
`company_form` = `CANONICAL_RETAINED` (§6); `auditor` = `EXCLUDED_NOT_SAFE`
(§5). Read-time gate filtrerer kun `AUTHORITATIVE_SNAPSHOT` + approved
field_name (§29) — ikke-full-revalidation per request.

## 9. Auditor exclusion

Auditor findes ALDRIG som authoritative current-state value i consumeren
(APPROVED_FIELDS udelader feltet; read-time gate). Consumer-modellen eksponerer
kun `pdf_auditor_status = "AUDITOR_CURRENT_STATE_UNAVAILABLE"` — aldrig
`auditor = null` (§5).

## 10. company_form exclusion

Consumeren kan ikke hente `company_form` fra projection
(`get_projected_company_field` returnerer None; merge tilføjer kun policy-
markøren). Eksisterende canonical kilde bevares uændret (§6).

## 11. Board / management rules

Board/management læses kun fra snapshot-authoritative rows
(`test_consumer_board_management_snapshot_only…`). Historiske Lysing-events
er KUN tilgængelige via den separate `get_projected_company_history`-metode
og kan aldrig override current membership; ingen replay i application layer
(§7/§21).

## 12. Null semantics

Feltets `warning_code` bevares pr. felt (KNOWN_EMPTY /
NOT_PRESENT_IN_SOURCE / kilde-mangler → feltet fraværende = NOT_AVAILABLE);
ingen semantic collapse (§18). Mappingen er dokumenteret i modulet og
rapporten.

## 13. Provenance

Hvert PDF-derived felt bærer `source: {document_id, observation_id,
parse_result_id, snapshot_date}` + `authority_state`; state-niveau bærer
`projection_run_id`/`projection_version`/`registration_number`
(§19). Response-modellen (§33): registration_number, projection_found,
projection_run_id, snapshot_date, fields{value, authority, source},
warnings, provenance.

## 14. Warning handling

Warnings læsbare for shadow/admin verification (`get_projection_warnings`,
`consumer_shadow_comparison.json`) men IKKE automatisk user-facing i denne
opgave (§20). Injektion til normal user response sker ikke (§12).

## 15. ACTIVE-view usage

Consumeren læser udelukkende gennem `v_active_run` /
`v_active_company_current_state` / `v_active_company_current_state_field`
(§8). Ingen hardcoded run_id/version; `test_consumer_active_version_switch`
beviser at consumer følger pointeren vA→vB (§49).

## 16. Failure / fallback semantics

- ACTIVE mangling/inkonsistent (§28/§9) → fail closed → canonical, ingen
  guessed generation.
- Projection read-fejl (§26) → fail closed → canonical + operational event
  (hook-observerbar i test).
- No-projection case (§25) → canonical fallback uden fejl.
- Flag-resolution fejl (§27) → disabled.

## 17. Shadow comparison

READ-ONLY `shadow_compare_population()` kan køre mens integration=false
(§22): læser canonical + ACTIVE projection, sammenligner, rapporterer —
ændrer aldrig runtime-resultat. Identitetsbro = den validerede registration-
number-normalisering (§16; QA 'S'-præfiks → digits). Navnesammenligning
bruger dokumenteret strukturel normalisering (strip '(type S-nr, adresse)'
+ legal-form præfiks + whitespace/case-fold).

## 18. 4527-company results

4.527 compared · 4.527 projection found · 4.527 canonical found · 0 missing ·
**4.519 name_same · 8 name_different** · 4.527 med (by-design) warnings ·
0 errors.

## 19. Known seven name differences

6 af de kendte 7 genfundet præcist (5711, 6766, 7712, 8083, 8560, 9138).
**8257** (NORMALIZATION_ONLY i v1/v2-review) løses af den dokumenterede
sammenligningsregel — konsistent med dens klassifikation, ikke en afvigelse.

## 20. Unexpected differences

2 nye, rapporteret — IKKE rettet (§24):

| regnr | canonical base | projection name | sandsynlig klasse |
|---|---|---|---|
| 6869 | JPR Kranakoyring | Sp/F JPR Kranakoyring í likvidatión | EXPECTED_LEGAL_STATUS_SUFFIX |
| 7403 | Nordic Aquaculture Contractors | Í/F Nordic Aquaculture Contractors [NAC] | SOURCE_FRESHER_THAN_CANONICAL |

## 21. Tests

Nye consumer-tests: 13 (disabled-mode, enabled read, no-ACTIVE fallback, DB-
failure fail-closed, auditor, company_form, board/management+history-separate,
same-day, source-date artifact, non-approved rows, flag-matrix, active version
switch, read-only session). Fuld suite: **161 collected, 161 passed, 0 failed,
0 skipped — ALL GREEN** (129-base + 13 consumer + 19 entities consumer-tests).

## 22. Canonical immutability

`company_inventory` 4531/`a927fde4…` · `company_location` 4531/`008170ae…` ·
`canonical_entity` 9806/`e84821d6…` — UNCHANGED vs. activation-baselinen.

## 23. Evidence / projection immutability

Alle 8 evidens-tabeller row-count-identiske med baselinen; v2 content_hash
uændret (`542b763e…`); ingen lifecycle-ændring: v2 = ACTIVE, v1 = SHADOW,
ingen rebuild/activation/retire (§53/§54).

## 24. Final runtime state

`v2 = ACTIVE` · consumer-kode implementeret · consumer runtime usage =
DISABLED (`PDF_HISTORY_INTEGRATION_ENABLED=false`,
`PDF_HISTORY_UI_ENABLED=false`, `ui_exposure_allowed=false`) · canonical
behavior/data, evidence og projection content uændrede (§58). Read-only
session-håndhævelse testet (`test_consumer_read_only_session_enforced`).

## 25. Next owner decision

Kun:

```text
ENABLE PDF_HISTORY_INTEGRATION?
```

Ved ja skal første enable-step stadig have `PDF_HISTORY_UI_ENABLED=false`
(integration uden user-facing UI). UI/API er separate senere beslutninger
(§36/§37).

## 26. Final status

CODE READY · DATA READY · ACTIVE PROJECTION READY · RUNTIME INTEGRATION OFF ·
UI OFF. Aktivér integration senere = lille, reversibel driftsbeslutning.

REGISTRY PROJECTION CONSUMER STATUS: PASS — CONSUMER WIRED AND VERIFIED, RUNTIME INTEGRATION REMAINS DISABLED