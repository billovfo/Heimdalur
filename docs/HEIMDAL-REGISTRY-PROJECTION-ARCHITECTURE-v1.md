# HEIMDAL REGISTRY PROJECTION ARCHITECTURE v1

**Opgave:** HEIMDAL PDF-HISTORIK: ISOLERET DATABASE-LAG + KILL SWITCH + VERSIONERET PROJECTION
**Status ved udlevering:** v1 bygget som SHADOW (kører IKKE aktivt). Begge kill switches er FALSK. Aktivering er en separat ejerbeslutning.
**LIVE SOURCE REQUESTS:** 0 (alt materiale kommer fra eksisterende evidens i `pdfhist_pilot`).

---

## 1. Purpose

Et isoleret, versioneret database-lag (`registry_projection`) der oversætter den
immutable PDF-evidens (Felag-snapshots + verificerede Lysing-begivenheder) til et
forbrugerklart format — uden nogensinde at mutere evidens, kanoniske tabeller
eller uden automatisk eksponering. Laget er en **projektion**: genopbyggelig,
drop-bar og aldrig en sandhedskilde.

## 2. Scope and non-goals

**Scope:** læsning af PDF-evidens; deterministisk shadow-builder; versioneret
livscyklus (BUILDING→SHADOW→ACTIVE→RETIRED/FAILED); kill switches; læsbar
feltautoritet; read-only sammenligning med kanonisk Heimdal.

**Non-goals (ikke i denne leverance):** aktivering, UI-visning, API-overflade,
tilbageførsel til kanoniske tabeller, crawling af nye kilder, rettelse af
kildedata. Ejerbeslutning kræves for alle fem (jf. §71 STOP).

## 3. Placement decision: why `registry_projection` lives in `pdfhist_pilot`

Projektionen placeres i `pdfhist_pilot` (`postgresql://postgres@localhost:5432/pdfhist_pilot`),
ikke i heimdal-QA:

1. **Evidens samlokalisering:** alle FK-bare kilder (`pdf_source_event`,
   `pdf_parse_result`, `pdf_document_observation`, `company_pdf_snapshot_history`)
   bor i `pdfhist_pilot`. Fremmednøgler fra projektion → evidens er dermed
   databasemæssigt håndhævet, ikke bare dokumenteret.
2. **Isolation-invariant bevares:** heimdal-QA forbliver 100 % fri for `pdf_*`-
   tabeller og får **nul nye objekter** i denne leverance.
3. **Én skrivemål-database:** builderen skriver kun i `pdfhist_pilot`;
   heimdal læses via en separat read-only forbindelse (§ 37-script).

## 4. Schema isolation and immutability contract

- Alle objekter bor i schema `registry_projection` (navnet var frit i begge
  databaser og er verificeret frit før CREATE; hvis navnet havde eksisteret,
  skulle bygget være STOPPET).
- **Evidens er read-only:** `pdf_run_log`, `pdf_source_event`, `pdf_document`,
  `pdf_document_content`, `pdf_document_observation`,
  `pdf_source_event_document`, `pdf_parse_result`,
  `company_pdf_snapshot_history` — checksum-verificeret uændret før/efter
  komplet livscyklus (se § 20).
- **Kanoniske tabeller er read-only:** `company_inventory`,
  `company_location`, `canonical_entity` — md5-checksums uændret
  (`company_location` = `008170aeeae4abe1e24ca4940dc0716a`, 4531 rækker).
- Ingen lagringsprocedurer; al logik i Python-builderen (`heimdal/pdfhist/projection.py`),
  idempotent SQL-migration (`heimdal/pdfhist/projection_migration.sql`).

## 5. Table inventory

| Tabel | Formål | Nøgle-invariant |
|---|---|---|
| `projection_run` | én række per generation | partial unique index: højst ÉN `status='ACTIVE'` |
| `company_current_state` | 1 række pr. firma pr. run | UNIQUE(run, company) |
| `company_current_state_field` | felt niveau med autoritets-state | UNIQUE(run, company, field_name) |
| `company_history_event` | historiske begivenheder | UNIQUE(run, source_event_id, event_type, action, entity_key, source_document_id) |
| `company_person_role` | bestyrelse/styring/revisor-rækker | UNIQUE(run, company, role_type, entity_normalized, source_scope) |
| `company_address` | adresser med rolle (kollaps forbudt) | UNIQUE(run, company, address_role, source_scope) |
| `projection_warning` | deterministiske advarsler | UNIQUE(run, company, warning_code) |
| `projection_setting` | kill switch-spejl + aktiv run | key PK; seeded false/null |

Views: `v_active_run`, `v_active_company_current_state`,
`v_active_company_current_state_field` (forbrugerlæsning via AKTIV generation).

## 6. Field authority model

`company_current_state_field.authority_state` — præcis seks tilstande:

`AUTHORITATIVE_SNAPSHOT` | `HISTORICAL_EVENT_ONLY` | `SOURCE_NOT_AUTHORITATIVE` |
`SAME_DAY_UNORDERED` | `SOURCE_ANOMALY` | `NOT_AVAILABLE`

Autoritetskontrakt pr. felt:

- **Felag-snapshot er autoritet** (AUTHORITATIVE_SNAPSHOT) for:
  `registration_number`, `company_name`, `alias_names`, `company_address`,
  `capital`, `purpose`, `signing_rules`, `board`, `management`,
  `accounts_dates`, `snapshot_date`.
- **Revisor:** aldrig som aktuel tilstand. Feltet eksisterer ikke;
  `SOURCE_NOT_AUTHORITATIVE`/`NOT_AVAILABLE`-semantik udtrykkes som advarslen
  `AUDITOR_CURRENT_STATE_UNAVAILABLE` (null er aldrig "ingen revisor").
- **company_form:** `EXCLUDED_FROM_PDF_CURRENT_STATE` — ikke læsbar med
  autoritet i PDF-materialet; advarslen `COMPANY_FORM_NOT_FROM_PDF` altid sat.
- **Bestyrelse/styring:** snapshot-ONLY for aktuel tilstand (F-01-fundet fra
  closure-audittet: Lysing-genererede fantomroller er udelukket); Lysing-
  begivenheder bevares som HISTORICAL_EVENT-rækker i `company_history_event`
  og `company_person_role` med `source_scope='HISTORICAL_EVENT'`.

## 7. Snapshot population rule

Population for en generation = seneste VERIFICEREDE Felag-snapshot pr. firma
for den angivne `source_collection_run_id` (v1: run 424245 —
closure-auditens godkendte kilde-run). Firmaer uden snapshot i runnet
kommer ikke ind i populationen — der fabrikeres aldrig en falsk komplet
tilstand. v1-population: **4276 firmaer** (af 4527 i samlet snapshot-historik).

## 8. History event derivation

Kun Lysing-relationer der kan tilskrives VERIFICEREDE kilderækker:

1. Join-kæde `pdf_source_event → pdf_source_event_document(cell_role=
   LYSING_CELL) → pdf_document(LysingPdf) → pdf_document_observation →
   pdf_parse_result(document_kind='REGISTRATION_NOTICE', parse_status='PARSED')`.
2. **Hård invariant (§ 46):** `normalize(registration_number i parse) ==
   normalize(row_skrasnr i pdf_source_event)` — ellers frasorteres
   bidragyderen og tæller i `non_verified_contributors_rejected`.
3. Strukturerede `changes` mappes til 10 begivenhedstyper
   (NAME_CHANGE, ADDRESS_CHANGE, CAPITAL_CHANGE, PURPOSE_CHANGE,
   SIGNING_RULES_CHANGE, BOARD_CHANGE, MANAGEMENT_CHANGE, AUDITOR_CHANGE,
   FISCAL_CHANGE, OTHER_CHANGE) med OPEN/CLOSE-action-sekvens
   (APPOINTED/ADDED/JOINED vs REMOVED/RESIGNED/LEFT).

## 9. Same-day and date-artifact policy (no guessing)

- **Samme dag:** hvis samme entitet har både OPEN og CLOSE-begivenhed med
  samme styrende dato (effective → registration → notice), gættes ALDRIG
  rækkefølgen. Konsekvens: advarslen `SAME_DAY_UNORDERED`, begivenhederne
  bevares råt, og aktuel tilstand påvirkes IKKE (den kommer udelukkende fra
  snapshot). v1: 238 advarsler.
- **Datoinstrumenter:** datoer uden for vinduet `1990-01-01..2027-12-31`
  bevares råt, markeres `SOURCE_DATE_ARTIFACT` og bruges ALDRIG til
  rækkefølge. v1: 13 firmaer / 48 rå-datoforekomster.

## 10. Deterministic builder

`heimdal/pdfhist/projection.py` — ren, stateless funktion af (evidens,
kode-version). `CODE_VERSION = 'registry-projection-v1'`. Determinisme er
bevist: to uafhængige builds af samme evidens giver **identisk content_hash**
`b6e910d1a2ce5c8a826f59fded303ff52ab805bb1fa8e69e56777ada574e82e3` (§ 52).

## 11. Content hash definition

sha256 over alle seks indholdstabeller for en generation, rækker ordnet efter
ALLE kolonner, felter adskilt med `\x1e`, rækker med `\x1f`. Enhver
semantisk forskel ændrer hash; enhver semantisk lighed giver samme hash.

## 12. Lifecycle state machine

```
BUILDING ──build ok──> SHADOW ──activate (eksplicit)──> ACTIVE ──ny ACTIVE──> RETIRED
    │                     │                                  │
    └──build error──> FAILED (egen commit)                    └──retire──> RETIRED
RETIRED ──rollback (eksplicit)──> ACTIVE
```

Regler: højst ét AKTIVT (DB-enforced); aktivering kræver status SHADOW og er
ALDRIG automatisk; rulning tilbage kræver RETIRED-mål og sker i én transaktion
uden genopbygning; fejlende build markeres FAILED i sin egen committede
transaktion og rører intet andet (§ 64); `delete-non-active` afviser altid
AKTIVE generationer.

## 13. Atomic activation

Én transaktion: gammel AKTIV → RETIRED, mål-SHADOW → AKTIV,
`projection_setting.active_projection_run_id` opdateres. Enten skifter alle
tre, eller ingen gør. Bevist i tests (`test_atomic_activation`).

## 14. Rollback without rebuild

Rollback fra v(n) til v(n-1) kræver at v(n-1) er RETIRED og genopbygger
INGEN data: ÉN transaktion (v(n) → RETIRED, v(n-1) → AKTIV, setting opdateres).
Bevist i tests (`test_rollback`) og i livscyklus-beviset på reelle data.

## 15. Kill switch design (config layer, not docs)

`heimdal/pdfhist/flags.py` — enforcement i kode:

- `PDF_HISTORY_INTEGRATION_ENABLED` (default **false**)
- `PDF_HISTORY_UI_ENABLED` (default **false**)
- Præcedens: miljøvariabel > DB-spejl (`projection_setting`) > default false.
- Ugyldig miljøværdi → **fail-closed** (false), aldrig raise-and-leak.
- `ui_exposure_allowed(conn) = integration_enabled AND ui_enabled` — UI er
  strukturelt umulig at eksponere alene (§ 24).
- `PDF_HISTORY_API_ENABLED`: bevidst IKKE implementeret — der findes ingen
  reel API-overflade i dette trin (§ 22).

## 16. Disabled-mode semantics

Når integration er slået fra: kanonisk sti er den eneste adfærd; projektionen
findes i databasen men må ikke forbruges (flags returnerer false; visninger
er kun en datasky, ingen forbrugerkode kører). Fail-closed: alle fejl i
flag-opløsning resulterer i false. "AKTIV generation + integration slået fra"
er en lovlig, understøttet tilstand (§ 67).

## 17. Read-only canonical comparison (§ 37/38)

`scripts/compare_projection_canonical.py` — to forbindelser (pdfhist_pilot +
heimdal), begge med `SET default_transaction_read_only = on`:

- Identitet: cifre-normaliseret `registration_number` (kanoniske tokens har
  `S`-præfiks — formatforskel rapporteres separat, ikke som mismatch).
- v1-resultat: 4276 sammenlignede, 4269 identiske/udvidede navne, 7 reelle
  navneforskelle (alle konkurs-/omdøbningsvarianter, rapporteret — aldrig
  auto-korrigeret), 0 manglende, 4276 format-only `S`-præfiksforskelle.
- Kanonisk har ingen aktuel tilstand for adresse/kapital/bestyrelse/styring/
  formål/signeringsregler → rapporteret som NOT_COMPARABLE, aldrig mismatch.

## 18. Admin CLI (§ 60)

`scripts/registry_projection_admin.py`: `apply-migration`, `build-shadow`,
`validate`, `activate`, `retire`, `rollback`, `delete-non-active`, `status`,
`enable/disable-integration`, `enable/disable-ui`. DSN fra
`HEIMDAL_PDFHIST_DB` (default pilot-DSN). Ingen kommando aktiverer automatisk.

## 19. Relationship to crawler and parser (§ 59)

Builderen er en **separat livscyklusfase**: den læser udelukkende færdig
evidens (`pdf_parse_result` + `pdf_source_event`), deler ingen proces med
crawleren og kan genkøres når som helst uden at røre indsamlingen. Krawlere
og parsere kender IKKE til `registry_projection`.

## 20. Immutability evidence (§ 53/§ 54)

Streaming-checksums (md5 af sha256-kæde, orden efter PK) for ALLE evidens-
tabeller før/efter komplet livscyklus (build → activate → build → rollback →
delete → CRUD-demo): alle uændrede. Kanoniske checksums (string_agg-opskrift):
`company_inventory` `a927fde476bf0e9a6aa4f6940bce640a` (4531),
`company_location` `008170aeeae4abe1e24ca4940dc0716a` (4531), `canonical_entity`
`e84821d687bfee6d2c362c87b85d7b85` (9806) — alle uændrede. Filer:
`projection_evidence_baseline.json`, `projection_canonical_baseline.json`.

## 21. Validation (§ 47)

`projection.validate()` mekanisk efter hvert build: non-VERIFIED=0 (genberegnet
attributionsgate over indsatte begivenheder), brudte provenance-refs=0,
dublet af aktuel tilstand=0, flere AKTIVE=0, aktiv-setting-konsistens.
v1: ALLE grønne.

## 22. Test coverage (§ 55)

`test_registry_projection.py` — 22 tests dækker: skemaisolation, versionering,
ét-AKTIV-invariant (DB-niveau via partial unique index), shadow-build,
atomisk aktivering, rollback, aktiv-delete-nægtet, kill switch (default,
DB, env-overstyring, fail-closed), UI-switch (inkl. ui-true+integration-false
→ ingen eksponering), slået-fra-fallback, bestyrelse/styring snapshot-ONLY,
revisor-udelukkelse (historik beholdes), company_form-udelukkelse,
samme-dag-advarsel + autoritet, datoinstrument-advarsel (råt bevaret),
non-VERIFIED-udelukkelse, provenans-kompletthed, rebuild-idempotens
(identisk hash), anomali-politik-advarsel, evidens+offentlig immutabilitet
gennem livscyklus, FAILED-markering overlever rollback.

## 23. Known limitations (honest reporting)

1. **4276 af 4527 firmaer** i v1: 251 firmaer har deres seneste snapshot i
   andre collection-runs end 424245 (population = run-scoped pr. opgave).
2. **Kanonisk sammenligning er name/registration-level**: heimdal-QA har
   ingen kanonisk aktuel tilstand for adresse/kapital/bestyrelse m.m.
3. **7 reelle navneforskelle** i sammenligningen er kilde-troskabsforskelle
   (PDF-navne med "í konkurs"/"u. tvingsilsavtøku" vs. ældre kanonisk navngivning)
   — rapporteret, ikke rettet.
4. Exception-policy-firmaerne 6692/9113 ligger UDEN FOR v1-populationen;
   policestierne er dækket af tests, ikke af reelle rækker.
5. `company_address`-historik: 0 rækker i v1 — der findes ingen
   ADDRESS_CHANGE-begivenheder i Lysing-evidensen (verificeret: 0 parser).

## 24. Operational runbook

```bash
PYTHONPATH=. python3 scripts/registry_projection_admin.py apply-migration
PYTHONPATH=. python3 scripts/registry_projection_admin.py build-shadow --source-run 424245 --version vN
PYTHONPATH=. python3 scripts/registry_projection_admin.py validate --run-id <id>
PYTHONPATH=. python3 scripts/registry_projection_admin.py activate --run-id <id>   # EJERBESLUTNING
PYTHONPATH=. python3 scripts/registry_projection_admin.py rollback --run-id <id>
PYTHONPATH=. python3 scripts/registry_projection_admin.py delete-non-active --run-id <id>
PYTHONPATH=. python3 scripts/registry_projection_admin.py status
PYTHONPATH=. python3 scripts/compare_projection_canonical.py [--run-id <id>]
```

## 25. Decision log

1. **Placering i pdfhist_pilot** — FK-mulighed + heimdal-QA forbliver pdf-fri.
2. **Partial unique index** i stedet for UNIQUE(status) — flere SHADOW'er
   skal kunne eksistere samtidig; kun AKTIV er unik.
3. **Revisor/company_form udeladt af SNAPSHOT_FIELDS** — feltautoritets-
   kontrakten gælder; udelukkelsen er synlig som advarsler, ikke tavshed.
4. **FAILED i egen transaktion** — fejlmarkering skal overleve caller-rollback.
5. **Sammenlignings-identitet på cifre-normaliseret regnr** — source_entity_id
   er et andet id-rum (bevist: firma 81 = P/F Hvalnes i projektionen vs.
   J.J. Smíð S6242 i inventory).
6. **Env > DB-spejl præcedens** med fail-closed parsing — drift kan altid
   slå funktionen fra uden DB-adgang.