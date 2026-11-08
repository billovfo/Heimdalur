# HEIMDAL PDF-HISTORIK — MASTERPLAN v3.1.2 (LEAN + TEMPORAL, FINAL IMPLEMENTATION-CONSISTENCY PATCH)

**Status: normativ masterplan for PDF-historik. Supersider `HEIMDAL-PDF-HISTORIK-MASTERPLAN-v3.1.1.md` (patchen retter 4 konkrete implementation-consistency-fejl; arkitekturen er GODKENDT og genåbnes ikke). Dette er den SIDSTE plan-patch før lokal implementation. v2.2.5's parser-authority-, promotion-, CURRENT-/event-sourcing-framework er IKKE genindført.** Live execution er fortsat en separat ejerbeslutning.

En-linjes-flowet:

> Find virksomhedens PDF'er, gem alle observationer og originale bytes, parse alle data, verificer at PDF'en tilhører virksomheden, og udled historiske gyldighedsperioder korrekt — kun fra verificerede relationer.

**Patch-princip (uændret):** Fixes bruger kun: DDL-rækkefølge, FK, UNIQUE, VIEW-filter, VIEW-partitionering, copy/reuse af eksisterende metadata, lille algoritmeregel. **v3.1.2 tilføjer 0 nye tabeller** (7 tabeller + 2 derived views) og 0 nye framework-lag.

**Changelog v3.1.2 (implementation-consistency patch):**

1. fixed executable DDL order (content oprettes FØR observation — FK må ikke pege fremad),
2. parse reuse kopierer også deterministisk observations-metadata (`extracted_text`, `document_snapshot_date`),
3. snapshot history er scoped pr. `parser_version` (ingen blandede timelines; ingen implicit "latest wins"),
4. uverificeret PDF/virksomhed-relation (regnr-mismatch eller manglende regnr) er fail-closed: evidence bevares, men ekskluderes fra afledt historie.

---

## 1. Mål og krav (uændret i omfang fra v3.1.1)

1. **Alle data bevares**: original bytes pr. observation (hash-dedup), fuld udtrukket tekst, struktureret parse; uforstået → `unknown_sections[]` + needs_review. Parseren må gerne undlade at forstå; den må ALDRIG smide information væk.
2. **Temporal gyldighed**: FelagPdf = FULL_STATE_SNAPSHOT (valid til næste snapshot); LysingPdf = DELTA (per-egenskab gyldighed med 3 semantiktyper, §12).
3. **PDF-indhold kan ændre sig**: logical document 1:N observations; ingen observation overskrives nogensinde.
4. **Fail-closed tilknytning**: kun verificerede PDF↔virksomhed-relationer må fodre afledt historik (§8/§11/§12).
5. `company_location` / eksist. Heimdal-state: urørt.

## 2. Source facts (uændret fra v3.1.1)

sg.vs.fo / www.skraseting.fo som run-config; search på regnr uden S; alle sider + count-header sanity (PAGINATION_MISMATCH → STOP company, log); exact skrasnr pr. række (substring-fælden); N:M row↔document (1 FelagPdf + 0..1 LysingPdf pr. række); ephemeral `s`-token (max 1 fresh-search retry); FelagPdf = snapshot med "Dagf."; LysingPdf = ændringsmeddelelser; 403/429/Retry-After → STOP; CONCURRENCY=1; gaps ≥ 1.0/1.2 s.

## 3. Temporal semantics (uændret, fortsat bindende)

- **FelagPdf = FULL_STATE_SNAPSHOT**: `valid_from = snapshot_date` (fra `Dagf.`), `valid_to = LEAD(snapshot_date)` over samme virksomhed OG samme parser_version (§11); interval `[from, to)`; sidste snapshot `valid_to = NULL`.
- **LysingPdf = DELTA**: 0..N changes pr. PDF med tre semantiktyper (§12) — "næste change lukker" gælder KUN scalar-familien.
- **Blandingsforbud**: ingen fælles "valid_to = næste PDF"-regel på tværs af dokumenttyper.
- Alle datoer forbliver semantisk separate; `fetched_at` er provenance, ALDRIG automatisk snapshot-dato.

## 4. Data model (7 tabeller + 2 derived views; 0 nye tabeller)

**PATCH 1 — eksekverbar CREATE-rækkefølge (FK-mål findes FØR FK):**
`pdf_run_log → pdf_source_event → pdf_document → pdf_document_content → pdf_document_observation → pdf_source_event_document → pdf_parse_result`

```sql
-- 1) Run-log — bærer fetch/outcome (retries er lokale; ingen attempt_count-kolonne)
CREATE TABLE pdf_run_log (
  id          bigserial PRIMARY KEY,
  run_id      bigint NOT NULL,
  company_id  bigint,
  document_id bigint,
  step        text NOT NULL,           -- SEARCH/PAGINATION/EVENT/FETCH/PARSE/REVIEW/ACCOUNT
  status      text NOT NULL,           -- OK/SKIPPED/WARN/REVIEW/STOPPED/FAILED
  error_type  text,
  detail      text,                    -- token/URL-redigeret
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- 2) Verificerede hændelsesrækker (uændret)
CREATE TABLE pdf_source_event (
  id                bigserial PRIMARY KEY,
  company_id        bigint NOT NULL,
  requested_regnr   text NOT NULL,
  row_skrasnr       text NOT NULL,
  source_row_key    text NOT NULL UNIQUE,
  source_event_date date,
  raw_text          text NOT NULL,
  page_no           int  NOT NULL,
  row_ordinal       int  NOT NULL,
  discovered_at     timestamptz NOT NULL DEFAULT now()
);

-- 3) LOGISK kilde-reference (uændret)
CREATE TABLE pdf_document (
  id             bigserial PRIMARY KEY,
  source_system  text NOT NULL DEFAULT 'sg.vs.fo',
  pdf_type       text NOT NULL,
  source_document_id text NOT NULL,
  pdf_kind_hint  text,                  -- FULL_STATE_SNAPSHOT | REGISTRATION_NOTICE
  fetch_policy   text NOT NULL DEFAULT 'RECHECK_ON_ENCOUNTER',
                 CHECK (fetch_policy IN ('RECHECK_ON_ENCOUNTER')),
  UNIQUE (source_system, pdf_type, source_document_id)
);

-- 4) Content-store (uændret) — SKAL oprettes før observation (PATCH 1)
CREATE TABLE pdf_document_content (
  sha256      text PRIMARY KEY,
  byte_length integer NOT NULL,
  content     bytea NOT NULL
);

-- 5) OBSERVATION — FK til content er nu eksekverbar (målet findes allerede)
CREATE TABLE pdf_document_observation (
  id                      bigserial PRIMARY KEY,
  document_id             bigint NOT NULL REFERENCES pdf_document(id),
  collection_run_id       bigint NOT NULL,
  fetched_at              timestamptz NOT NULL,          -- observationstid (provenance, IKKE identitet)
  document_snapshot_date  date,                          -- udfyldes af parse/reuse (Dagf.)
  sha256                  text NOT NULL
                          REFERENCES pdf_document_content(sha256),   -- PATCH 1: mål eksisterer nu FØRST
  byte_length             integer NOT NULL,
  extracted_text          text,
  UNIQUE (document_id, collection_run_id)
);

-- 6) Row-document N:M (uændret)
CREATE TABLE pdf_source_event_document (
  source_event_id bigint NOT NULL REFERENCES pdf_source_event(id),
  document_id     bigint NOT NULL REFERENCES pdf_document(id),
  cell_role       text NOT NULL
                  CHECK (cell_role IN ('FELAG_CELL','LYSING_CELL','UNCLASSIFIED_CELL')),
  PRIMARY KEY (source_event_id, document_id)
);

-- 7) Parse-resultat (uændret identitet; reuse defineret i §7)
CREATE TABLE pdf_parse_result (
  id                      bigserial PRIMARY KEY,
  document_observation_id bigint NOT NULL REFERENCES pdf_document_observation(id),
  parser_version          text NOT NULL DEFAULT '1',
  document_kind           text CHECK (document_kind IN
                            ('COMPANY_SNAPSHOT','REGISTRATION_NOTICE','INVOICE','OTHER')),
  raw_text                text NOT NULL,
  structured_data         jsonb NOT NULL,   -- inkl. changes[] (entity + entity_normalized)
  parse_status            text NOT NULL CHECK (parse_status IN ('PARSED','REVIEW','FAILED')),
  needs_review            boolean NOT NULL DEFAULT false,
  review_reason           text,
  parsed_at               timestamptz NOT NULL DEFAULT now(),
  UNIQUE (document_observation_id, parser_version)
);
```

**Derived views:** `company_pdf_snapshot_history` (§11), `pdf_review_queue` (§13); change-history er algoritme i kode (§12).

**Relationer:**

```text
company_inventory → pdf_source_event → N:M → pdf_source_event_document → pdf_document (logical)
pdf_document → 1:N → pdf_document_observation → N:1 → pdf_document_content (FK på sha256)
pdf_document_observation → 1:N → pdf_parse_result
```

**Naturkeys:** source_event (uændret), document `UNIQUE (source_system, pdf_type, source_document_id)`, observation `UNIQUE (document_id, collection_run_id)`, content PK `sha256`, parse `UNIQUE (document_observation_id, parser_version)`, junction PK.

**source_row_key-begrænsning [uændret fra v3.1.1]:** `source_row_key` sikrer deterministisk replay-idempotens for **samme observerede row-layout**. Reordning af rækker/sider i kilden er tilladt source-observation-drift og må IKKE få PDF/content til at dubleres, fordi documents canonicaliseres separat.

**Der skrives kun til de 7 tabeller.** `company_location` og al anden Heimdal-state urørt.

## 5. Search + pagination (uændret)

CONCURRENCY = 1; search gap ≥ 1.0 s; PDF gap ≥ 1.2 s; alle sider; exact skrasnr-verificering; count-header sanity; PAGINATION_MISMATCH → STOP company + log. `source_row_key` = `'sg.vs.fo|' || company_id || '|' || page_no || '|' || row_ordinal || '|' || coalesce(source_event_date,'') || '|' || sha256(normalize(raw_text))`.

## 6. Fetch + version detection (uændret fra v3.1.1)

```text
for pdf_ref in row.pdf_refs:
  doc = ensure pdf_document (UNIQUE skip; kind_hint/fetch_policy sat)
  insert pdf_source_event_document (uanset fetch)                     # link ALTID først

  if exists pdf_document_observation(doc, this run):                   # PATCH 3 i v3.1.1
      continue                                                          # max 1 fetch pr. document pr. run

  fetched = false
  for attempt in 1..2:                                                 # PATCH 2 i v3.1.1: lokalt
    sleep(GAP_PDF >= 1.2s); s = token(...)                             # memory-only, redigeret
    resp = download(...)                                                # timeout; ingen cross-host redirects
    if invalid-token and attempt == 1: s = fresh_search(row).token; continue
    if 403/429/Retry-After: STOP run (SOURCE_REFUSAL)
    if not (HTTP 200 and body.startswith('%PDF-') and plausible type):
      log FAILED(FETCH_INVALID, doc, attempt); continue
    hash = sha256(body)
    INSERT pdf_document_content (hash) ON CONFLICT DO NOTHING           # content FØRST (FK-mål)
    INSERT pdf_document_observation (doc, run, fetched_at=now(), sha=hash)
    prior = latest observation(doc) with earlier fetched_at
    if prior and prior.sha256 != hash: log CONTENT_CHANGED(doc)
    fetched = true; break

  if not fetched: log FAILED(FETCH_RETRY_EXHAUSTED, doc)               # → review-kø via pdf_run_log
```

Idempotens: samme document to steder i samme run → ét fetch + ét observation (OBS-RUN-01); næste run → ny observation (OBS-RUN-02). Forskellig hash → ny observation; ældre overskrives ALDRIG.

## 7. Parse + reuse — PATCH 2 (metadata følger med)

```text
for obs in observations without parse_result(obs, parser_version):

  prior_obs  = any other observation o2 with o2.sha256 == obs.sha256
  prior_parse = parse_result(prior_obs, same parser_version)

  if prior_parse:
      INSERT pdf_parse_result: copy (document_kind, raw_text, structured_data,
        parse_status, needs_review, review_reason) from prior_parse;
        new document_observation_id + parsed_at = now()
      if obs.extracted_text IS NULL:          # PATCH 2: deterministisk reuse
          obs.extracted_text = prior_obs.extracted_text
      if obs.document_snapshot_date IS NULL:  # PATCH 2: krav for snapshot-history
          obs.document_snapshot_date = prior_obs.document_snapshot_date
      log PARSE_REUSE(sha256, from=prior_parse.id)
  else:
      parse bytes + extract text (pypdf etc.) → new parse_result
      udfyld obs.extracted_text (fra raw_text, deterministisk) og obs.document_snapshot_date (Dagf.)
```

- **Konsistens-invariant:** `document_snapshot_date` og `extracted_text` er deterministic outputs af (bytes, parser_version) — reuse er ren materialisering, ingen ny parserlogik.
- Uden PATCH 2 kunne en reused observation have gyldigt parse-result men MANGLE i snapshot-history (den filtreres på `document_snapshot_date IS NOT NULL`).

## 8. FelagPdf parser + verification gate (PATCH 4)

Snapshot parsing uændret: `Dagf.` → `snapshot_date`; BOARD/MANAGEMENT/CAPITAL/PURPOSE/SIGNING_RULES/Roknskapir-datoer + `unknown_sections[]`; role-aware adresse (rolle fra sektion-label, aldrig postcode-nærhed); ukendt layout → LAYOUT_DRIFT.

**Verification gate (PATCH 4, bindende):** for hver kobling source_event → observation → parse sammenlignes `normalize(parsed registration_number)` med `normalize(source_event.row_skrasnr)`:

```python
if parsed_registration_number is missing:
    review(PDF_COMPANY_MISMATCH_MISSING_REGNR)   # fail-closed
    do_not_derive_history()
elif normalize(parsed reg.nr) != normalize(row_skrasnr):
    review(PDF_COMPANY_MISMATCH)                  # evidence + parse + review-signal BEVARES
    do_not_derive_history()                       # ekskluderet fra afledt historik
```

- **Evidence bevares ALTID** (bytes, tekst, parse, review-signal). Kun de **afledte** histories (snapshot/change) filtreres — fail-closed, ikke et attribution-framework.
- Mismatch må aldrig mutere eller slette noget: det er et VIEW-filter + review-signal.

## 9. LysingPdf parser (uændret fra v3.1.1)

0..N changes med `entity` (verbatim) + `entity_normalized`; dato-separation (effective/registration/notice/source_event_date); REPORTER_ADDRESS; `unknown_sections[]`. Ikke-verifiede relationer (§8-gate) indgår IKKE i change-history.

## 10. structured_data contracts (uændret fra v3.1.1)

FelagPdf = COMPANY_SNAPSHOT (snapshot_date/company_address/board/management/capital/purpose/signing_rules/accounts_dates/unknown_sections/dates); LysingPdf = REGISTRATION_NOTICE (notice_date/journal_number/reporter/changes med entity + entity_normalized). `registration_number` er påkrævet felt i begge — bruges af verification-gaten (§8).

## 11. Snapshot validity — PATCH 3 (parser_version-scoped) + PATCH 4 (verified filter)

```sql
CREATE VIEW company_pdf_snapshot_history AS
WITH verified_company_observation AS (
  SELECT DISTINCT                                        -- join kan ikke duplikere (v3.1.1-fix bevaret)
         e.company_id,
         p.parser_version,                               -- PATCH 3: version er dimension
         o.id AS document_observation_id,
         o.document_snapshot_date, o.sha256, o.fetched_at, p.structured_data
  FROM pdf_parse_result p
  JOIN pdf_document_observation o ON o.id = p.document_observation_id
  JOIN pdf_document d ON d.id = o.document_id
  JOIN pdf_source_event_document sed ON sed.document_id = d.id
  JOIN pdf_source_event e ON e.id = sed.source_event_id
  WHERE d.pdf_type = 'FelagPdf'
    AND p.parse_status = 'PARSED'
    AND o.document_snapshot_date IS NOT NULL
    AND lower(btrim(p.structured_data->>'registration_number'))   -- PATCH 4: fail-closed gate
        = lower(btrim(e.row_skrasnr))
)
SELECT company_id, parser_version, document_observation_id,
       document_snapshot_date AS valid_from,
       LEAD(document_snapshot_date)
         OVER (PARTITION BY company_id, parser_version              -- PATCH 3: ingen cross-version LEAD()
               ORDER BY document_snapshot_date, document_observation_id) AS valid_to,
       sha256, fetched_at, structured_data,
       EXISTS (SELECT 1 FROM verified_company_observation v2
               WHERE v2.company_id        = verified_company_observation.company_id
                 AND v2.parser_version     = verified_company_observation.parser_version
                 AND v2.document_snapshot_date = verified_company_observation.document_snapshot_date
                 AND v2.sha256 != verified_company_observation.sha256) AS snapshot_date_conflict
FROM verified_company_observation;
```

- Output pr. række: `company_id, parser_version, document_observation_id, valid_from, valid_to, sha256, fetched_at, structured_data, snapshot_date_conflict`.
- **parser_version = '1' og '2' har hver sin timeline**; applikation/operator vælger eksplicit version. Ingen ACTIVE/CANDIDATE/DEPRECATED, ingen "latest parser wins" i viewet, ingen implicit authority (PARSER-TL-01).
- Konflikt-semantik uændret: samme dato + forskellig hash (inden for samme company + version) → begge bevaret, REVIEW, intervaller PROVISIONELLE. Manglende Dagf. → NULL + REVIEW(SNAPSHOT_DATE_MISSING).
- Viewet returnerer KUN verificerede relationer; mismatch/missing-regnr er usynlige her og synlige i `pdf_review_queue` (§13).

## 12. Change/property validity (uændret mapping + PATCH 4-filter)

**Mapping (config/kode; ikke DB-tabel) — GODKENDT, uændret:**

| Change type | Semantik |
|---|---|
| ADDRESS_CHANGE | SCALAR_REPLACEMENT |
| NAME_CHANGE | SCALAR_REPLACEMENT |
| CAPITAL_CHANGE | SCALAR_REPLACEMENT |
| PURPOSE_CHANGE | SCALAR_REPLACEMENT |
| SIGNING_RULE_CHANGE | SCALAR_REPLACEMENT |
| COMPANY_FORM_CHANGE | SCALAR_REPLACEMENT |
| BOARD_CHANGE | SET_MEMBERSHIP |
| MANAGEMENT_CHANGE | SET_MEMBERSHIP |
| AUDITOR_CHANGE | SET_MEMBERSHIP |
| ARTICLES_CHANGE | EVENT_ONLY |
| OTHER_CHANGE | EVENT_ONLY |

**A — SCALAR_REPLACEMENT:** `value A @ t1; value B @ t2` → `A = [t1,t2)`, `B = [t2,∞)`. To værdier samme order_date → REVIEW(SCALAR_DATE_CONFLICT).
**B — SET_MEMBERSHIP:** APPOINTED/ADDED/JOINED åbner entity @ t; REMOVED/RESIGNED/LEFT lukker KUN den entity @ t (SET-01/02/03). Intervals `[appointment, removal)`; uden removal → NULL.
**C — EVENT_ONLY:** ARTICLES/OTHER bevares med dato + raw evidence, UDEN afledt valid_to (EVENT-01).
Order-dato: `effective_date` → `registration_date` → `notice_date` → `source_event_date`; uden nogen → REVIEW(CHANGE_DATE_MISSING).

**PATCH 4-filter:** change-history udledes KUN fra verificerede company-document relationer (samme gate som §11). Mismatch → `review(PDF_COMPANY_MISMATCH)` + `do_not_derive_history()` for den relation — evidence bevares.

## 13. Review queue (uændret fra v3.1.1)

```sql
CREATE VIEW pdf_review_queue AS
SELECT 'PARSE_REVIEW' AS issue, ... FROM pdf_parse_result WHERE needs_review
UNION ALL
SELECT 'FETCH_FAILED', ... FROM pdf_run_log WHERE step = 'FETCH' AND status = 'FAILED'
UNION ALL
SELECT 'PDF_COMPANY_MISMATCH', ... WHERE normalize(parsed reg.nr) != normalize(row_skrasnr)
UNION ALL
SELECT 'PDF_COMPANY_MISMATCH_MISSING_REGNR', ... WHERE parsed reg.nr mangler
UNION ALL
SELECT 'SNAPSHOT_DATE_CONFLICT', ... FROM company_pdf_snapshot_history WHERE snapshot_date_conflict
UNION ALL
SELECT 'SOURCE_NAME_MISMATCH', ... WHERE post-pass navne-crosscheck matcher ikke
UNION ALL
SELECT 'RUN_WARNING', ... FROM pdf_run_log WHERE status IN ('WARN','REVIEW','STOPPED')
```

UNION ALL-operatørliste (skitse; exact SQL verificeres ved implementation). Ingen workflow-tabeller, ingen status-machine, ingen approvals. Warnings kan ikke gå tabt — herunder de relations, der nu er ekskluderet fra derived history (§11/§12).

## 14. Restart / idempotens (uændret + PATCH 2)

- INSERT-or-skip på naturkeys; content før observation; fetch-idempotens pr. run (UNIQUE document+run); parse pr. observation+parser_version.
- Crash efter fetch: observation + content gemt; genstart parser uden ny fetch og **materialiserer** evt. manglende `extracted_text`/`document_snapshot_date` via reuse (PATCH 2). RESTART-01/SHARED-01/02 uændret.
- Per-company stop i `pdf_run_log`; ingen leases; CONCURRENCY=1.

## 15. Stop conditions + accounting (uændret)

`SOURCE_REFUSAL | REQUEST_BUDGET | PAGINATION_MISMATCH | MASS_INVALID_PDF | LAYOUT_DRIFT | TOKEN_LEAK`; 8 count-queries; SUCCESS kun ved in-flight = 0; review-køen er operatør-listen.

## 16. Tests (46 fixtures fra v3.1.1 + 5 nye + 15–30 schema-assertions)

```text
DDL-01            schema executes cleanly from empty DB in documented CREATE order
PARSE-REUSE-02    same hash, new observation: 0 ekstra parser-kald, nyt parse_result,
                  extracted_text udfyldt, document_snapshot_date udfyldt,
                  observation dukker korrekt op i snapshot-history
PARSER-TL-01      samme observationer parsed med parser 1 og 2:
                  parser-1 timeline indeholder kun parser-1 rækker; kun parser-2 rækker i
                  parser-2 timeline; ingen cross-version LEAD()
PDF-ATTR-02       row company=5019, PDF reg.nr=5015:
                  parse/evidence bevaret, review-signal findes,
                  0 snapshot-history rækker for 5019, 0 afledte Lysing-changes for 5019
PDF-ATTR-03       row company=5019, PDF reg.nr mangler:
                  REVIEW, ekskluderet fra derived historik indtil afklaret
```

Skema-behavior assertions (uændret fra v3.1.1) kompletteres med: FK-mål findes før FK (DDL-01), reuse-metadata komplet (PARSE-REUSE-02), ingen cross-version partition (PARSER-TL-01), fail-closed gates (PDF-ATTR-02/03).

## 17. Rollout (uændret: offline fixtures → 5–10 → 100 → full; Step 2-inspektion uændret)

## 18. Deferred (uændret fra v3.1.1)

`specialtabeller, event sourcing, current-state, knowledge-time/as-of, OCR, UI, deployment-roller, multi-runner, token-tabel, parser authority registry, promotion gates, 300+ DDL-prober.` Ingen nye semantic families i denne patch — fixture-behov håndteres som parser-iteration efter implementation.

## 19. Implementation flow (efter patch)

```text
for company:
    search + verify source rows
    for row:
        save event
        for PDF:
            ensure logical document
            link row → document
            if not observed this run:
                fetch; hash; ensure content; save observation
            if parse missing:
                if same bytes already parsed with this parser:
                    reuse parse + deterministic observation metadata
                else:
                    parse bytes
            verify PDF registration number against source company
            if mismatch or missing regnr:
                review; exclude relation from derived history
derive Felag snapshots:
    per company + parser_version; DISTINCT company/observation; LEAD(snapshot_date)
derive Lysing changes:
    only verified company relations; scalar / membership / event-only rules
all uncertainty:
    pdf_review_queue
```

## Acceptance-invariants (alle dækket)

1. Schema eksekverer fra tom DB i dokumenteret rækkefølge; alle FK-mål findes før FK (DDL-01).
2. Same-hash parse reuse giver komplett observations-metadata (PARSE-REUSE-02).
3. Snapshot history blander ALDRIG parser-versioner (PARSER-TL-01); ingen implicit authority.
4. PDF/virksomhed-mismatch og manglende regnr går ALDRIG ind i afledt historik; fail-closed (PDF-ATTR-02/03).
5. Evidence bevares altid (bytes, tekst, parse, review-signal); parseren må ikke kassere.
6. Samme hash kan have mange observationer; forskellig hash overskriver aldrig; fetch max 1 pr. document pr. run.
7. 7 tabeller + 2 derived views uændret; ingen authority-model; ingen UPDATE af gammel parse.
8. `company_location` og øvrig Heimdal-state urørt.

MASTERPLAN v3.1.2 STATUS: READY FOR LOCAL IMPLEMENTATION