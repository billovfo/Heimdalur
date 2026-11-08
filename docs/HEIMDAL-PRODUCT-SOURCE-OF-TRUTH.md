---
title: Heimdal Product Source of Truth
document_type: GOVERNING_PRODUCT_DEFINITION
status: PENDING_OWNER_APPROVAL
owner: Sole Human Owner
owner_approval_required: true
implementation_authorized: false
document_version: 2.4
generated_at: 2026-11-06
previous_version: 2.3
source_permission: GRANTED
reconnaissance_status: COMPLETED_FOR_PLANNED_ACQUISITION
additional_reconnaissance_required: false
---

# Heimdal Product Source of Truth

> Dette dokument er **ikke normativt**, før Sole Human Owner har godkendt det eksplicit.
> `implementation_authorized: false` gælder indtil godkendelse er givet skriftligt.
> v2.4 beskriver det, der faktisk er bygget og kører (§1a). Godkender ejeren v2.4, sættes
> `status: APPROVED` og `implementation_authorized: true` for det byggede scope, og datoen noteres i §0.7.

## 0. Versionsnote

### 0.1–0.2 (v1.0 → v1.2, samme dag — sammenfattet)

v1.0 var den første konsolidering af produktkravet, skrevet efter undersøgelse af den kørende database, `heimdal_dump.sql`, 8 eksempel-`felag_pdf`'er, en fuld data-audit (`heimdal-data-audit-f2425f9b…`) og flere recovery-kørsler samme dag. v1.1 indarbejdede Sole Human Owner's svar på 9 åbne beslutninger (§29). v1.2 reviderede én af dem (`co-involvement-network-metric`, A→B). Alle ni beslutninger fra v1.0–v1.2 er bevaret i §29 og forbliver normative — for **Fase 2**, se nedenfor.

### 0.3 Ændringer i v2.0 — strategisk kursændring

Sole Human Owner har truffet en ny, overordnet strategisk beslutning, der ændrer rækkefølgen af hele produktet:

> Heimdals første produkt skal bygges på de aktuelle og strukturerede relationsdata fra `sg.vs.fo`. Data skal hentes, valideres og gemmes lokalt som snapshots. Heimdal må ikke være afhængig af, at et udokumenteret tredjeparts-API er tilgængeligt ved hvert brugeropslag. PDF-baseret historik sættes på pause og bliver en senere, separat produktfase.

Dette er **ikke** en tilbagerulning af v1.0–v1.2. Det er en omprioritering af rækkefølgen. De ni ejerbeslutninger i §29 handler næsten alle om det PDF-baserede historiske produkt (konkurs/likvidation-model, bogført egenkapital, historiske adresser osv.) og forbliver gyldige — de gælder nu **Fase 2**, ikke Fase 1. To beslutninger er fase-agnostiske og gælder begge faser uændret: `identity-same-name` og `organization-vs-company` (canonical entity-identitet er fælles infrastruktur, se §3).

### 0.4 Ændringer i v2.1 — kontrolregistrering og From/To-semantik

Ren dokumentationspræcisering, ingen strategiændring. Sole Human Owner har defineret Heimdals brugervendte terminologi for source-relationstypen `Veruligur Eigari` og source-statussen `O.V.E.` (§14a: **Registreret kontrolaktør** / **Ingen registreret kontrolaktør**), samt normeret, hvordan upstream-felterne From/Frá og To/Til skal bevares og fortolkes pr. relationstype (§14b). Begge var tidligere kun løst beskrevet (§14, §16.1, §17.1) uden en klar afgrænsning fra ejerskabsbegrebet — v2.1 retter dette, så en registreret kontrolaktør aldrig fremstilles som ejerskab, ledelse eller uafhængigt verificeret kontrol. Ingen andre relationstyper er ændret. Se §29a for de seks nye ejerbeslutninger.

### 0.5 Ændringer i v2.2 — virksomhedsregister og indsamlingsrækkefølge

Ren dokumentationspræcisering af Fase 1's arbejdsrækkefølge, ingen strategiændring. Sole Human Owner har besluttet: (1) et lokalt virksomhedsregister skal etableres og bruges som `get-table`-indsamlingskø, **før** fuld relationel indsamling; (2) `get-table` er den primære relationskilde — `get-graph` er ikke længere obligatorisk pr. virksomhed; (3) rå indsamling (Fase B) skal være fuldstændig afsluttet, før relationel behandling (Fase C) begynder, og Fase C skal kunne genkøres fra lokale raw snapshots uden nye upstream-kald; (4) Fase 1's data er Heimdals egne indsamlingsobservationer, ikke en påstand om relationens juridiske eller historiske alder — den slags hører til Fase 2. Se ny §8a og de opdaterede §10/§11/§13/§24, samt §29b for de seks nye ejerbeslutninger.

**Hvad der udløste beslutningen:** en levende, undersøgt gennemgang (browser DevTools) af `https://sg.vs.fo/yvirlit/` viste et fuldt struktureret, tilsyneladende dagsaktuelt relations-API (ejerskab, Nevnd, Stjórn, Annað) — se §6 og det nye companion-dokument `HEIMDAL-CURRENT-RELATIONS-SOURCE-CONTRACT.md` for den fulde, klassificerede observation.

**Hvad der IKKE ændres:** PDF-kilder, PDF-pipelines og alle eksisterende recovery-artefakter (`gate03/*`, `*-recovery/*`) **slettes ikke**. De klassificeres som `DEFERRED_HISTORICAL_SOURCE_MATERIAL` (§28) og forbliver den fremtidige kilde til Fase 2.

Se `HEIMDAL-SOT-v1.2-to-v2.0-diff.md` for den fulde, eksplicitte ændringsbeskrivelse.

### 0.6 Ændringer i v2.3 — reconnaissance afsluttet, tilladelse givet, plan afgrænset

v2.3 synkroniserer afsluttet company-discovery-reconnaissance, fuld source-tilladelse og den nye fem-work-package acquisition-plan. **Dette er ikke en ny produktstrategi** — ingen produktbeslutning, faseafgrænsning eller terminologi er ændret. Tre ting er blevet afgjort, som v2.2 udtrykkeligt lod stå åbne:

1. **Discovery-metoden er valgt** (§8): `search-cbi`-enumeration over registreringsnumre. v2.2's formulering om, at høstalgoritmen først kunne besluttes efter reconnaissance, er dermed indfriet, ikke omgjort.
2. **Source-tilladelse er givet** (§9): Sole Human Owner attesterer fuld tilladelse til dataindsamling. v2.2's krav om en source-use assessment før høst er dermed opfyldt gennem ejerattestation.
3. **`get-table`s adfærd er målt** (§6, §8a.6): `level=N` opfører sig som BFS-radius N, og responses er ikke start-entity-uafhængige. Den tidligere antagelse om byte-identiske responses fra begge ender af en relation er **falsificeret** for større komponenter og erstattet.

Uændret: Fase 1/Fase 2-afgrænsningen, raw acquisition før relation processing, lokal PostgreSQL som runtime- og primærdatabase, kontrolregistreringsterminologien (§14a), From/To-semantikken (§14b), relation processing som senere scope, og `implementation_authorized=false`.

### 0.7 Ændringer siden v2.3 — dokumentet følger det byggede system (audit 2026-11)

v2.3 blev aldrig godkendt, og arbejdet efter den gik længere end teksten (audit 2026-11, fund J-1). v2.4 er **ikke en ny produktstrategi**. Den beskriver det byggede system, så ejeren kan godkende eller afvise ét konkret scope (ejerbeslutning D5, 2026-11-06).

1. **Ny §1a, byggetilstand:** hvad der er bygget, hvad der er slået til, og hvilke flag der er slået fra.
2. **Fase 2 (PDF-historik) er bygget, men slået fra** (§26, §27). v2.3 sagde `STATUS = PAUSED`. Pipelinen blev bygget efter `HEIMDAL-PDF-HISTORIK-MASTERPLAN-v3.1.2.md`, uden at en ejerbeslutning ophævede pausen. Godkendes v2.4, godkendes dette med tilbagevirkende kraft.
3. **Udgangspunktet er den genskabte 2026-09-04-tilstand** (§1a.1). Data, der blev erklæret tabt i september, var ikke tabt (audit R-1).
4. **Registry analytics er bygget og parkeret** med flag fra (D6).
5. **PEP er tom**, indtil der findes en kildebelagt, dateret import (D14).
6. **Personreview er bygget.** De 12 genskabte beslutninger er PENDING.
7. **Kørselsform:** kun 127.0.0.1, uden login, håndhævet i koden. Den eneste eksterne request er OSM-kortfliser (D12).
8. **Acquisition-planen er implementeret** (§30), selv om v2.3 kaldte den "ikke implementeret".
9. **Kendte afvigelser** fra v2.3-teksten står i §1a.4, og ejerbeslutningerne fra audit 2026-11 i §29c.

Uændret: produktvisionen, canonical ID-modellen, Fase 1-reglerne (§5–§25), terminologien (§14a, §14b) og ejerbeslutningerne i §29–§29b.

Ejergodkendelse af v2.4: *afventer* (dato og ordlyd noteres her).

---

## 1. Formål

Dette dokument fastlægger, hvilket produkt Heimdal skal være, og i hvilken rækkefølge det bygges. Det forhindrer stadig de mønstre, v1.0 identificerede (gate-arbejde uden produktværdi, store evidenssystemer uden canonical entities, rankings før kernefunktioner — se v1.2's §1, bevaret i ånd). v2.0 tilføjer et nyt, lige så vigtigt princip: **byg ikke et produkt, der er en tynd klient oven på et fremmed, udokumenteret API** — se §5.

Dokumentet er organiseret i tre dele:

- **Del A — Fælles fundament**: produktvision, canonical entity- og ID-model, som begge faser bruger.
- **Del B — Fase 1 (nu): aktuelle relationer fra sg.vs.fo.** Dette er det primære, aktive produktscope.
- **Del C — Fase 2 (bygget, slået fra; §27): PDF-baseret historik.** Bevaret fra v1.0–v1.2, men blokerer ikke Fase 1.

## 1a. Byggetilstand (as-built, 2026-11-06)

Tallene kommer fra `docs/STATUS.md`, som er målt med `tools/status_check.py`. Hvis STATUS og dette afsnit er uenige om et tal, gælder STATUS. Dette afsnit beskriver scope.

### 1a.1 Data

- Dev-databaserne er `heimdal_recovered_20260904` (canonical) og `pdfhist_pilot_recovered_20260904` (PDF-historik og projection). De rummer 2026-09-04-tilstanden, som blev genskabt 2026-11-06 fra den stoppede `heimdal-qa`-container (fix-plan W2, ejerbeslutning D1 og D2).
- Der er 9.806 canonical entities.
- `heimdal` (2026-08-03-restoren) og den tomme `pdfhist_pilot` er urørte fallbacks.

### 1a.2 Komponenter

| Komponent | Bygget | Slået til i dev | Bemærkning |
|---|---|---|---|
| Fase 1: company inventory og rå `get-table`-indsamling (`heimdal_collect.py`) | ja | køres manuelt | Raw tier er append-only. Genopbygning sker fra lokale snapshots uden upstream-kald (W5a) |
| Fase 1: canonical graf og `registry_ownership` (direkte og indirekte ejerskab, cyklusdetektion) | ja | ja | Ved en cyklus tilbageholdes den indirekte procent, og stien får en markør (D8) |
| Portal (Flask, `heimdal/`): virksomheds- og personportaler, søgning, ejerskabsoversigt, koncerner, netværk, find forbindelse | ja | ja | |
| Portal, ud over v2.3-teksten: screening og bulk-screening, compliance-matrix, dossier/KYC, Geo-Kort, ændringsfeed (snapshots), overvågning (watchlist), CSV-eksport | ja | ja | Ikke beskrevet i v2.3. Godkendes med v2.4 |
| Registry analytics (plan v1.3.1, faser 1–5), build 13 | ja | **nej, flag fra** | Parkeret (D6): build 13's ejerskabsobservationer er forkerte (audit C-1). Koden er rettet (W4c). En genopbygning kræver en ejerbeslutning |
| Fase 2: PDF-historik (`heimdal/pdfhist/`, MASTERPLAN v3.1.2): corpus, parsing, projection, entity links og Skráseting-visning | ja | **nej, flag fra** | Projection-run 33 er ACTIVE, med 16.400 PDF'er, der er sha256-verificeret. `PDF_HISTORY_INTEGRATION_ENABLED` og `PDF_HISTORY_UI_ENABLED` er `false` i `tools/dev_env.sh`. Efter D7 slås de til, når W3 og W4d er grønne. Det er et separat ejerskridt |
| Personreview (`/persons/review`, kræver `HEIMDAL_REVIEW_UI=1`) | ja | kun med flag | 12 genskabte ejerbeslutninger er PENDING (5 APPROVE_LINK, 7 DEFER). Rækkefølgen er: de anvendes på en SHADOW-run, ejeren gennemser dry-run, og først derefter aktiveres de (W4a). Livscyklussen er den, der findes i koden (D9) |
| PEP-screening | kun skema | tom | Der er ingen PEP-data, og der matches ikke på navn. UI'en viser "PEP-data ikke indlæst", indtil der findes en kildebelagt, dateret import (D14) |

### 1a.3 Drift

- **Kun lokalt.** Postgres 18 og portalen lytter kun på 127.0.0.1. Portalen håndhæver det selv (`heimdal/modules/shared/web.py`): en ikke-loopback-klient får 403, en fremmed Host-header får 400, og en state-ændrende request fra en fremmed side får 403.
- **Intet login og ingen auth.** Portalen må derfor aldrig bindes til andet end 127.0.0.1, før auth-opgaven er løst.
- **Ingen produktion og ingen deployment** (§26).
- **Én ekstern request:** OpenStreetMap-kortfliser på Geo-Kort (D12, `docs/DRIFT-RUNBOOK-v1.md` §9).
- **Definition of done:** `./tools/gate.sh`.

### 1a.4 Kendte afvigelser fra v2.3-teksten (godkendes eller afvises med v2.4)

1. §27 sagde PAUSED, men Fase 2 er bygget (1a.2).
2. §15 kræver "alle bidragende ejerstier". UI'en viser højst 20 eksempelstier pr. række (`PATH_SAMPLE_CAP`) og siger det, når listen er afkortet. Procent og antal stier er altid eksakte. I dag har ingen række mere end 10 stier (audit C-10).
3. Portalfunktionerne i 1a.2, der står som "ud over v2.3-teksten".
4. §30 kaldte acquisition-planen "ikke implementeret". Den er implementeret i `heimdal_collect.py`.

---

# DEL A — FÆLLES FUNDAMENT

## 2. Produktvision

Heimdal er:

> Et lokalt, snapshotbaseret relationssystem, som indsamler aktuelle strukturerede virksomhedsdata fra autoritative offentlige kilder og gør dem søgbare som klikbare person-, virksomheds- og ejerskabsportaler.

Fase 2 udvider senere visionen til også at dække virksomheders livscyklus og historik, som beskrevet i v1.0's oprindelige produktvision (§27).

Enhver oplysning skal fortsat kunne føres tilbage til sin kilde — et rå, gemt svar med tidsstempel og hash, ikke en påstand uden proveniens.

## 3. Canonical entity- og ID-model

Denne model er fælles infrastruktur for begge faser. Data fra sg.vs.fo (Fase 1) og data fra PDF-kilder (Fase 2) skal kunne pege på **den samme** canonical entity, når de faktisk er den samme virksomhed eller person.

### 3.1 Entity types

```
PERSON
COMPANY
ORGANIZATION
PUBLIC_AUTHORITY
UNKNOWN_ENTITY
```

Klassifikationsregler (uændret fra v1.2, **OWNER DECISION** `organization-vs-company`, valgt A): `SP/F`, `P/F`, `ÍVF` m.fl. = `COMPANY`. Myndigheder/domstole = `PUBLIC_AUTHORITY`, aldrig `PERSON`. En navngiven, ikke-fysisk aktør med eget registreringsnummer i kilden = `COMPANY`; uden eget registreringsnummer = `ORGANIZATION`. Kun dokumenterede fysiske personer = `PERSON`.

**Regel, opdateret i v2.3 (§13 nedenfor):** upstream's egen typeangivelse er **primær kilde** for typeklassifikation — men den hentes i praksis fra `get-table`s navnetekst, ikke fra `get-graph`. Reconnaissance målte, at `get-table`-navneteksten og `get-graph`s strukturerede `COLORLABEL` gav samme type for alle sammenlignede entities (62/62, 0 konflikter), og `get-graph` er derfor **sampled validation**, ikke en obligatorisk primærkilde pr. virksomhed. Navnebaseret heuristik (præfiks-parsing af `SP/F`/`P/F`/`ÍVF`) er kun **fallback**. Et upstream-typefelt må aldrig stiltiende overskrives af en navneheuristik; en parsefejl eller en konflikt giver `UNKNOWN_ENTITY`, ikke et gæt.

### 3.2 Canonical IDs — stabile, adskilt fra source IDs

Hver canonical person og hver canonical virksomhed skal have sit eget stabile, unikke Heimdal-ID:

```
Person:      canonical_person_id, source_entity_id, entity_type=PERSON
Virksomhed:  canonical_company_id, source_entity_id, entity_type=COMPANY
```

Krav:

```
Canonical IDs er stabile og genbruges aldrig.
Upstream source IDs (f.eks. sg.vs.fo's pkCbi, eller en PDF-kildes registreringsnummer)
  gemmes separat og må aldrig sammenblandes med canonical ID.
To forskellige personer må ikke få samme canonical ID, alene fordi navnene er ens
  (jf. identity-same-name, §29).
Den samme entity må ikke få et nyt canonical ID, blot fordi den høstes igen.
Entity merges og splits dokumenteres eksplicit (se nedenfor).
```

Hvis en entity skifter upstream-ID, navn eller selskabsform, ændres dens Heimdal-ID **ikke** automatisk. Hvis to tidligere separate entities senere vurderes at være samme entity, bruges:

```
canonical redirect   — gamle links fortsætter med at virke
merge record         — dokumenterer hvornår og hvorfor
source identity mapping — hvilke source-IDs peger på hvilket canonical ID
```

### 3.3 Klikbarhed — universel regel

Enhver person og virksomhed, der vises i Heimdal — i søgeresultater, ejerskabstabeller, ejerskabsgrafer, Nevnd/Stjórn/Annað-lister, relationslister, netværksvisninger, ejerstier, fælles-virksomheds-/bestyrelsesvisninger — **skal** være klikbar og pege på `/persons/:canonical_person_id` eller `/companies/:canonical_company_id`. Ingen frontendkomponent må konstruere links ud fra navn, normaliseret navn, en tilfældig database-row-ID, et relations-ID eller en slug alene. Ingen genereret person- eller virksomhedsroute må give 404.

### 3.4 ORGANIZATION, PUBLIC_AUTHORITY og UNKNOWN_ENTITY

§3.1 definerer fem entity types, men §3.2/§3.3 og §16–§21 beskriver kun fulde canonical ID'er, portaler og klikadfærd for `PERSON` og `COMPANY`. Da `ORGANIZATION` og `PUBLIC_AUTHORITY` kan optræde som modpart i en `Annað`-relation (§12) og derfor kan vises som en netværksnode (§21), skal de **ikke** kunne give 404, selvom de ikke har en fuld portal i Fase 1.

Reglen: `ORGANIZATION` og `PUBLIC_AUTHORITY` får samme `canonical_entity_id` + `source_entity_id`-adskillelse som §3.2, men **ingen** fuld portal (ingen ejerskabs-/co-involvement-sektioner, da disse typer normalt ikke selv ejer eller er ejet i kildens model). De peger på en fælles, minimal fallback-route `/entities/:canonical_entity_id`, som viser navn, entity type, source-ID og en klikbar liste af de relationer, den optræder i — intet mere. `UNKNOWN_ENTITY` bruger samme fallback-route, indtil typen kan afklares. Dette holder "ingen route giver 404"-garantien intakt uden at kræve fulde portaler for typer, produktvisionen ikke fokuserer på (§2).

## 4. Product decision rule

Uændret fra v1.2. Enhver fremtidig work unit skal besvare:

```
Hvilket konkret brugerspørgsmål løser dette?
Hvilken canonical entity, relation eller fact forbedres?
På hvilken portal bliver værdien synlig?
Hvordan kan brugeren efterprøve resultatet?
Kan samme værdi leveres enklere?
```

---

# DEL B — FASE 1: AKTUELLE RELATIONER FRA SG.VS.FO

Dette er det **primære, aktive** produktscope. Fase 2 (Del C) blokerer intet heri.

## 5. Kritisk arkitekturprincip

```
sg.vs.fo er en upstream datakilde.
sg.vs.fo må ikke være Heimdals runtime-database.
Heimdals frontend må ikke hente sine produktdata direkte fra sg.vs.fo ved hvert brugeropslag.
Heimdals brugeroplevelse må ikke afhænge af, at det eksterne site
  eller dets udokumenterede endpoints er online.
```

Den korrekte kæde er:

```
sg.vs.fo
  → kontrolleret dataindsamling
  → immutable raw snapshots
  → validering og reconciliation
  → canonical lokal database
  → Heimdals eget read API
  → WebUI
```

**Ikke** `WebUI → live forespørgsel direkte til sg.vs.fo`. Dette er blocking (§25).

## 6. Observed source interface

Interfacet er **ikke officielt dokumenteret**. Alt nedenfor er observeret gennem browserens DevTools mod den faktiske, levende side `https://sg.vs.fo/yvirlit/`, 2026-07-31, og skal behandles som:

```
OBSERVED_SOURCE_INTERFACE
NOT_OFFICIALLY_DOCUMENTED
NOT_ASSUMED_STABLE
```

Fuld detalje, klassificeret felt for felt som `OBSERVED`/`INFERRED`/`UNCONFIRMED`/`NOT_AVAILABLE`, ligger i det selvstændige companion-dokument `HEIMDAL-CURRENT-RELATIONS-SOURCE-CONTRACT.md`. Sammenfatning:

- Endpoints: `/api/search-cbi?size={n}&q={term}`, `/api/relations`, `/api/get-cve?id={pkCbi}`, `/api/get-table/{pkCbi}?level=N&pkCbType=...&relations=...`, `/api/get-graph/{pkCbi}?...`, `/api/get-info`.
- **`/api/search-cbi` er kildens egen søgefunktion og Heimdals primære discovery-metode (§8).** Kildens offentlige hjælpetekst dokumenterer selv søgning på `S` + registreringsnummer. Observeret: `size=101` understøttet; `from`-parameteren blev observeret **ignoreret** (ingen offset-pagination); tom `q` giver tomt resultat; matchning er substring over rækkens visningstekst.
- Query-felter: `pkCbi`, `level`, `pkCbType`, `relations`.
- Topniveau-relationsgrupper (`/api/relations`, direkte observeret svar): `2=Nevnd`, `3=Stjórn`, `4=Eigarar`, `5=Annað`.
- Fine-grained relationstyper observeret i `get-table`-svar: `cbTypeId=21` (Stjóri), `22` (Nevndarformaður), `32` (Nevndarlimur), `1` (Eigari N.N% — procenten er indlejret i `cbTypeName`-teksten, ikke et separat numerisk felt), `41` (Veruligur Eigari — en selvstændig relationsrække, ikke en flag-kolonne på ejerskabsrækken). Desuden observeret: source-værdien `O.V.E.` (`Ongin veruligur eigari`), som optræder som en self-edge-lignende række (From = To = samme entity). Begge canonicaliseres **ikke** som ejerskab — se §14a for den fulde, normative behandling ("Registreret kontrolaktør" / "Ingen registreret kontrolaktør") og §14b for From/To-semantikken.
- Entity-type udtrækkes af `get-table`s navnetekst (observeret format `{navn} - {type}, {adresse}`) og valideres stikprøvevist mod `get-graph`s `COLORLABEL` (§13).
- `/api/get-info` viste sync-tidsstempler fra selve undersøgelsesdagen (`update_end: "2026-07-31T18:07:59Z"`) — kilden synes at synkronisere med kort mellemrum, men frekvensen er **UNCONFIRMED** over tid.
- **`get-table` returnerer en N-hop uorienteret nabolagskugle omkring den forespurgte entity** (`N_HOP_UNDIRECTED_NEIGHBOURHOOD_BALL_AROUND_START_ENTITY`) — ikke nødvendigvis en hel forbundet komponent, og ikke start-entity-uafhængigt. Den tidligere antagelse om byte-identiske responses fra begge ender af en relation gjaldt kun, fordi eksemplet var en 3-node-komponent, hvor kuglen fra ethvert medlem dækker hele komponenten; den er **falsificeret** som generel regel: to level-4-forespørgsler i samme større komponent gav 588 hhv. 317 entities. `OBSERVED`.
- **`level=N` opførte sig som BFS-radius N** (`LEVEL_N_EQUALS_BFS_RADIUS_N`). Målt fra samme startentity: level 1 → 18 entities, level 2 → 62, level 4 → 588, og den største hop-afstand i level-4-svaret var præcis 4. `OBSERVED`. Kildens egen hjælpetekst kalder feltet «Stødd á Grannalagi» (nabolagsstørrelse), hvilket stemmer med målingen.
- **Et tomt svar er tvetydigt:** et ikke-eksisterende source-ID returnerede HTTP 200 med `[ ]`, byte-identisk med et legitimt tomt relationssvar. HTTP 200 `[ ]` er derfor ikke i sig selv bevis for `NOT_FOUND` (§24). `OBSERVED`.
- Ingen `robots.txt`-restriktion fundet (HTTP 404 på `/robots.txt`). Ingen eksplicitte rate-limit-headers observeret. Session-cookie (`cookiesession1`) sat af serveren.

Disse observationer er **ikke** en officiel eller garanteret API-kontrakt. Source-tilladelse (§9) gør ikke interfacet officielt dokumenteret eller stabilt — klassifikationen `NOT_OFFICIALLY_DOCUMENTED` / `NOT_ASSUMED_STABLE` gælder uændret. Reconnaissance er afsluttet i det omfang, den planlagte indsamling kræver (`reconnaissance_status: COMPLETED_FOR_PLANNED_ACQUISITION`); yderligere generel reconnaissance er ikke en forudsætning for at bygge collectoren. Collectoren skal fortsat gemme hver rå response, så kontrakten kan revalideres lokalt uden nye upstream-kald.

## 7. Data skal hentes og gemmes lokalt — tre lag

### 7.1 Raw source archive

Gemmer det præcise upstream-svar uden semantisk ændring:

```
request URL, request parameters, request timestamp
response status, response headers (hvor relevante), response body
response SHA-256
source sync timestamp (hvor tilgængeligt, jf. get-info)
collector version
```

Raw data er immutable. Et senere normaliseringsproblem skal kunne rettes uden nødvendigvis at genhente fra upstream.

### 7.2 Normalized source snapshot

Gemmer de strukturerede entities og relationer fra hvert snapshot:

```
source entity IDs, source entity types
source relation group, source relation type ID, source relation label
source relation direction
source percentage text, parsed percentage
snapshot ID, first observed, last observed
```

### 7.3 Canonical product graph

Normaliserer til Heimdals produktmodel:

```
entities
direct_relations
ownership_edges
governance_relations
beneficial_owner_relations
other_relations
source_snapshots
```

Afledte data — herunder indirekte ejerskab — genberegnes fra direkte relationer; de gemmes ikke som selvstændig sandhed.

## 8. Ikke kun API-kald — entity-universe discovery

Beslutningen betyder, at Heimdal skal **hente data**, ikke blot forbinde sin WebUI direkte til upstream-API'et ved hvert opslag. Upstream-endpoints må bruges som indsamlingsmekanisme, hvis brugen er teknisk og juridisk forsvarlig (§9) — men løsningen skal desuden kunne:

```
opdage source entities uafhængigt af én enkelt endpointtype
gemme alle hentede payloads lokalt
genoptage afbrudte høstninger
revalidere tidligere snapshots
opdage ændringer i responseformat (schema-drift)
opdage manglende eller fjernede records
reprocessere data uden nyt upstream-kald
```

Source discovery må **ikke** baseres alene på graph traversal fra enkelte kendte entities — en ren "start ved kendt ID og følg relationer"-strategi vil aldrig finde entities uden nogen kendt forbindelse til et allerede-kendt startpunkt.

**Besluttet i v2.3 efter afsluttet reconnaissance** (`HEIMDAL-COMPANY-DISCOVERY-RECONNAISSANCE.md`):

```
primær metode      = SEARCH_CBI_REGISTRATION_NUMBER_ENUMERATION
gap detection      = ENTITIES_OBSERVED_IN_GET_TABLE_AS_GAP_DETECTOR
```

`search-cbi`-enumeration over registreringsnumre er den primære metode til at etablere virksomhedsinventoryet. Den finder også **isolerede virksomheder**, som ingen relationer har, hvilket graph traversal per definition ikke kan. Entities observeret i `get-table`-responses bruges udelukkende til **gap detection** — de måler den primære metodes dækning og må aldrig træde i stedet for den. Relationstraversering er ikke bevis for et komplet virksomhedsunivers.

**Metoden til en denominator er identificeret; den faktiske denominator er ikke etableret endnu.** Den foreligger først, når enumerationen er kørt til sin dokumenterede terminalbetingelse (§24, og acquisition-planens §5).

## 8a. Rækkefølge: virksomhedsregister før relationel behandling

**OWNER DECISION** (§29b): Sole Human Owner har besluttet en konkret rækkefølge for Fase 1's opbygning, som er normativ for `HEIMDAL-CURRENT-DATA-ACQUISITION-PLAN.md`.

### 8a.1 Virksomhedsregisteret er indsamlingskøen

Virksomhedsidentifikation er **første del** af dataindsamlingen — den er ikke relationel behandling. For fuld Fase 1-indsamling skal Heimdal først etablere et lokalt, målbart register over de virksomheder, der skal kunne søges og indgå i indsamlingspopulationen. Det minimale virksomhedsregister skal logisk kunne bevare:

```
source_company_id, source_system
display_name, raw_entity_type
registration_number (hvis kilden leverer det)
discovery_method, discovered_at
raw_response_reference
collection_scope
```

Registeret bruges til: lokal søgning efter virksomhed, direkte opslag af source ID, opbygning af en kontrolleret `get-table`-kø, optælling af forventede virksomheder, registrering af hentede/manglende/fejlende virksomheder, og dokumentation af virksomhedsuniversets denominator (§24). **Heimdal må ikke afhænge af live upstream-søgning i runtime** — søgning i produktet sker mod Heimdals lokale database (§22), aldrig direkte mod sg.vs.fo, i overensstemmelse med §5.

### 8a.2 Pilot vs. fuld population

```
collection_scope = PARTIAL_POPULATION       — en afgrænset pilot; må ikke hævde fuld coverage
collection_scope = FULL_POPULATION_CLAIMED  — kun når denominator OG fetch-status pr. virksomhed
                                               faktisk er dokumenteret (§24)
```

Relationstraversering alene må ikke antages at finde alle virksomheder — isolerede virksomheder, eller virksomheder uden observerede relationer til allerede kendte entities, kan ellers blive overset (§8's advarsel mod ren graph-traversal-discovery gælder skærpet her).

### 8a.3 Personer og andre aktører opdages under råindsamlingen

Heimdal behøver **ikke** identificere alle personer eller andre aktører, før `get-table`-indsamlingen begynder. Personer, organisationer og andre entities, som observeres i `get-table`-responses, registreres i det lokale entity-register som en del af råindsamlingen (§8a.5, Fase B). Dette ændrer ikke rækkefølgen: virksomheder styrer den primære indsamlingskø; andre entities opdages undervejs; relationer behandles først efter råindsamlingen. Der må **ikke** udføres identity merge, relationel canonicalisering eller indirekte ejerskabsberegning under selve råindsamlingen.

### 8a.4 `get-table` er den primære relationskilde

Normativ prioritet:

```
1. search-cbi — virksomhedsdiscovery/enumeration (§8, §8a.1)
2. get-table — level=1 pr. virksomhed (§8a.6)
3. get-cve — kun ved et dokumenteret metadata-behov
4. get-graph — kun som stikprøvevalidering eller fallback
```

`get-table` er den primære source-response til aktuelle relationer. `get-graph` må **ikke** være et obligatorisk request for hver virksomhed, medmindre en senere, begrænset undersøgelse dokumenterer, at nødvendige produktdata ikke findes i `get-table`. Begrundelse: lavere requestvolumen, mindre belastning af upstream, færre fejlpunkter, enklere collector, mindre risiko for overengineering. (Konsekvens for entity typing: se §13.)

### 8a.5 Tre adskilte faser

**Fase A — Entity inventory.** Input: offentligt identificerbare virksomheder fra den valgte discovery-metode (§8). Output: et lokalt, søgbart virksomhedsregister (§8a.1) og en kontrolleret indsamlingskø. Ingen relationel behandling.

**Fase B — Raw acquisition.** For hver virksomhed i indsamlingskøen: udfør det tilladte `get-table`-request; gem URL og parametre; gem HTTP-status; gem response body uændret; gem request- og response-tidspunkt; beregn SHA-256; registrér collector-version; registrér hvilke source entities svaret indeholder; registrér fetch-status. Rå værdier som From, relation label, To, relation type ID, group ID og rå procenttekst gemmes her (§7.1, §7.2). **Ingen canonical relation må være nødvendig for at gennemføre råindsamlingen.**

**Fase C — Relation processing.** Først efter den relevante råindsamling: parse entities; fortolk From og To (§14b); canonicalisér relationstyper; registrér direkte ejerskab (§14); registrér kontrolaktører (§14a.1); omdan O.V.E. til entity-status (§14a.2); beregn indirekte ejerskab (§15); beregn relation contexts (§18, §19); byg portalprojektioner (§16, §17). **Relation processing skal kunne genkøres fra de lokale raw snapshots uden nye upstream-requests.** Ingen relationel afledning må foregå i WebUI'en (§5), og ingen relationel behandling må være nødvendig for i sig selv at afgøre, om et upstream-response er blevet indsamlet og bevaret korrekt (det afgøres af Fase B's fetch-status alene, §24).

### 8a.6 Komponentrespons, level og deduplikering — afklaret i v2.3

Reconnaissance-spørgsmålet fra v2.2 er besvaret (`HEIMDAL-COMPANY-DISCOVERY-RECONNAISSANCE.md`):

```
get_table_component_behavior = N_HOP_UNDIRECTED_NEIGHBOURHOOD_BALL_AROUND_START_ENTITY
get_table_level_semantics    = LEVEL_N_EQUALS_BFS_RADIUS_N
get_table_default_level      = 1
content_deduplication_safe   = true
request_provenance_preserved = true
```

`get-table` returnerer ikke nødvendigvis en hel forbundet komponent, og responsen afhænger af start-entity. Antagelsen om byte-identiske responses fra begge ender er falsificeret (§6).

**Normativ acquisition-standard: `level=1`.** Begrundelse: level 1 er per konstruktion den forespurgte virksomheds egne kanter, og i den målte stikprøve dækkede en level-1-gennemgang drevet af virksomhedsinventoryet alle observerede relevante kanter — ingen observeret kant havde to endepunkter uden registreringsnummer. Level 4 gav ~284.771 bytes mod ~4.252 for samme entity uden at tilføje kanter, som inventoryets øvrige poster ikke selv leverer.

Klassifikation, så forskellen ikke går tabt:

```
konkret måling (18/62/588 entities, BFS-radius)      = OBSERVED
generalisering til den planlagte collector (level=1)  = ACCEPTED_PLANNING_RULE
```

`level=1` er altså den aktuelle standard for virksomhedsdrevet råindsamling — **ikke** en matematisk bevist komplethed for alle fremtidige source-data. Gap detection (§8) er det, der løbende falsificerer reglen, hvis den holder op med at passe.

**Request vs. content, uændret og nu bekræftet:** identisk response content må lagres én gang efter SHA-256, og flere request attempts må pege på samme content. Men **hver udførte request skal fortsat have sin egen requestproveniens** — hvilken virksomhed der blev forespurgt, hvornår, hvilket HTTP-resultat den fik, og hvilken response-hash den pegede på. Content-deduplikering må aldrig fjerne requesthistorik.

## 9. Ansvarlig og konservativ dataindsamling

Interfacet er udokumenteret, og der er ingen kendt SLA eller kendte brugsgrænser. Collectoren skal derfor være konservativ:

```
lav, konfigurerbar request-rate
lav, konfigurerbar concurrency
exponential backoff
respekt for 429 og Retry-After
stop eller markant nedskalering ved 403, 429 og 5xx
begrænsede retries
checkpoint og resume
lokal caching
ingen gentagne requests for uændrede records uden grund
tydelig identifikation i User-Agent, hvis juridisk og teknisk passende
ingen omgåelse af adgangskontrol
ingen CAPTCHA-bypass
ingen IP-rotation for at omgå begrænsninger
ingen skjult parallel massehøst
```

**Før fuld høst** skulle følgende undersøges og dokumenteres. Det er sket i den afsluttede reconnaissance (`HEIMDAL-COMPANY-DISCOVERY-RECONNAISSANCE.md` §2) og afgjort ved ejerattestation (§9.1 nedenfor):

```
robots.txt
offentlige brugsbetingelser
tekniske begrænsninger
rate-limit-adfærd
response-cache headers
session- eller cookiekrav
mulighed for officiel dataadgang
kontakt- eller tilladelsesbehov
```

### 9.1 Source-tilladelse (v2.3) — OWNER ATTESTATION

v2.2's krav om en source-use assessment før høst er opfyldt gennem en autoritativ Sole Human Owner-attestation:

```
permission_status                                = GRANTED
permission_scope                                 = FULL_DATA_COLLECTION
source_owner_contact_required_before_collection  = false
source_support_availability                      = TEMPORARILY_UNAVAILABLE_DUE_TO_SUMMER_LEAVE
permission_granted_by                            = NOT_RECORDED
permission_granted_at                            = NOT_RECORDED
permission_channel                               = NOT_RECORDED
owner_attestation_recorded_at                    = 2026-08-01
```

Ukendte administrative detaljer er `NOT_RECORDED` og blokerer ikke den tekniske plan.

Betydning:

```
systematisk virksomheds-enumeration        = TILLADT
rå get-table-indsamling                    = TILLADT
manglende offentlige ToS                   = IKKE en blocker
afventning af sourcekontaktens tilbagekomst = IKKE påkrævet
```

**To ting, tilladelsen ikke ændrer.** For det første er interfacet stadig `NOT_OFFICIALLY_DOCUMENTED` og `NOT_ASSUMED_STABLE` (§6) — tilladelse til at hente data er ikke en garanti for, hvordan data ser ud. For det andet gælder den konservative collector-adfærd ovenfor uændret: at have lov til at hente alt er ikke en grund til at hente det hurtigt. At source-supporten midlertidigt er utilgængelig, er ikke en adgangsblokering, men det er grunden til, at stopreglerne ved 403, 429 og gentagne 5xx er hårde stop uden automatisk genstart — der er indtil videre ingen, der kan kontaktes, hvis collectoren opfører sig forkert.

Sole Human Owner skal fortsat **separat autorisere implementationen**. `implementation_authorized` er uændret `false`.

## 10. Ingen live runtime-afhængighed

Heimdals produkt skal fortsat fungere, når upstream er utilgængeligt, langsomt, ændret, rate-limited eller fjernet. WebUI'en læser **altid** fra Heimdals lokale database. Upstream-status påvirker kun: freshness, nye snapshots, ændringsdetektion — aldrig adgang til allerede indsamlede data.

Brugeren skal kunne se:

```
Data senest hentet: <timestamp>
Upstream synkroniseringstid: <timestamp, hvis tilgængeligt>
Snapshot ID: <id>
Freshness status: CURRENT | STALE | UPDATE_FAILED | UNKNOWN
```

**OWNER DECISION** (`CURRENT_SOURCE_OBSERVATION_NOT_HISTORICAL_VALIDITY`, §29b): Fase 1 må **kun** påstå: *"Dette var de oplysninger, upstream viste, da Heimdal indsamlede det lokale snapshot."* Primær brugerformulering skal gå i retning af **"Kildens viste oplysninger ved Heimdals seneste indsamling"** — ikke et sprog, der kan læses som en påstand om relationens faktiske alder. `Freshness status`-feltet ovenfor beskriver derfor kun, hvor frisk *Heimdals egen indsamling* er — ikke hvor gammel relationen selv er.

Fase 1 må **ikke** påstå: hvornår en relation begyndte; hvor gammel relationen er; hvornår en ejerandel blev registreret; hvornår en bestyrelsesrolle begyndte; om oplysningerne har været uændrede i flere år; eller at relationen juridisk er gyldig fra en bestemt dato. Historiske datoer og relationernes faktiske alder undersøges først gennem PDF- og dokumentkilder i **Fase 2** (Del C) — som ikke blokerer Fase 1.

## 11. Snapshotbaseret historik fremadrettet

Selvom upstream primært viser aktuel tilstand, gemmer Heimdal regelmæssige snapshots, så det fremadrettet er muligt at observere: en relation optræder første gang; en relation er fortsat til stede; en relation er ikke længere til stede; en ejerandel ændres; en person tilføjes eller fjernes fra Nevnd; en person tilføjes eller fjernes fra Stjórn.

Systemet må **kun** kalde disse datoer:

```
first_observed_at
last_observed_at
missing_since_snapshot
```

Det må **ikke** kalde dem juridisk `valid_from`, `valid_to`, `appointed_at`, `resigned_at` — medmindre upstream-kilden selv dokumenterer sådanne datoer (den gør det ikke i det observerede interface, §6). Disse tre felter er **Heimdals egne observationsdata** (hvornår Heimdal så det, ikke hvornår det skete) og må ikke omtales, som om de var juridiske eller historiske gyldighedsdatoer.

## 12. Fase 1 relationstyper

Fase 1 omfatter kun **aktuelle** relationer fra de fire observerede grupper: `Nevnd`, `Stjórn`, `Eigarar`, `Annað`. Fine-grained typer bevares med:

```
source_group_id, source_group_name
source_relation_type_id, source_relation_type_name
raw_label
normalized_relation_type
normalization_status
```

Observerede eksempler (ikke en komplet taksonomi): `Stjóri`, `Nevndarformaður`, `Nevndarlimur`, `Eigari N.N%`, `Veruligur Eigari`, `O.V.E.` (`Ongin veruligur eigari`). De to sidste canonicaliseres **ikke** som almindelige `Eigarar`-gruppe-relationer — se §14a for deres særskilte behandling (Registreret kontrolaktør / entity-status). En full-population taxonomy-audit skal ske, før implementationen erklærer alle `Annað`-relationer forstået — den hører til relation processing og ligger uden for den aktuelle acquisition-plan (§30). Ukendte relationstyper gemmes som `OTHER_SOURCE_RELATION` med original label bevaret — de kasseres aldrig.

## 13. Entity typing

Se §3.1 for den fælles regel. **Opdateret i v2.3 efter måling:**

```
primær kilde   = GET_TABLE_NAME_TEXT_GREEDY_RIGHTMOST_SEPARATOR_PARSE
validering     = GET_GRAPH_COLORLABEL_SAMPLED_ONLY
fallback       = UNKNOWN_ENTITY
```

Entity type parses af den fritekst-type, der indgår i `get-table`s navnefelt (observeret mønster `"{navn} - {type}, {adresse}"`). Parsing skal bruge den **højre relevante separator**: entity-navne kan selv indeholde `" - "`, og en venstresplit ødelagde to af 588 observerede navne, mens en højresplit parsede alle 588.

`get-graph`s `COLORLABEL` er **stikprøvevalidering**, ikke en obligatorisk primærkilde. Observeret stikprøve: 62 sammenligninger, 62 matches, 0 konflikter, 0 ukendte. Ét `get-graph`-kald returnerer mange noder, så validering kan udføres **uden et request pr. virksomhed**. `get-graph` må ikke gøres obligatorisk pr. virksomhed.

En parsefejl eller en konflikt mellem parset type og `COLORLABEL` giver `UNKNOWN_ENTITY` (eller `typing_status=CONFLICT`) med den rå navne- og typeværdi bevaret ordret — **aldrig et navnebaseret gæt**. Navneheuristik (præfiks-parsing af `SP/F`/`P/F`/`ÍVF`) forbliver sidste fallback.

**Entity typing må ikke blokere råindsamlingen** og er ikke nødvendig for at gemme en raw response; den kan udføres efter Fase B.

## 14. Ejerskab — Fase 1's første prioritet

En direkte ejerrelation skal mindst bevare:

```
owner_entity, owned_entity
source relation type, source relation label
ownership percentage, snapshot
raw source payload
```

Hvis procenten er indlejret i tekst som `Eigari 50%` (bekræftet observeret format, §6), gemmes:

```
raw_percentage_label
parsed_percentage
parse_status
```

Et parseproblem må aldrig omdanne en ukendt procent til nul. Denne §14 gælder udelukkende `Eigari N%`-relationer. Kontrolregistrering (`Veruligur Eigari`/`O.V.E.`) er **ikke** en ejerskabsrelation og er derfor beskrevet separat i §14a — den må ikke slås sammen med en procentbaseret ejerrelation.

## 14a. Kontrolregistrering (Registreret kontrolaktør)

**OWNER DECISION**: Sole Human Owner har besluttet Heimdals brugervendte terminologi for source-relationstypen `Veruligur Eigari` og source-statussen `Ongin veruligur eigari` (observeret forkortelse `O.V.E.`). De færøske sourcetermer bevares i raw data og kan vises i kildeoplysninger/tekniske detaljer, men er **ikke** de primære brugervendte betegnelser. UI-sektionsoverskrift: **"Kontrolregistrering"**.

### 14a.1 Registreret kontrolaktør (source: `Veruligur Eigari`)

```
canonical_relation_type = REGISTERED_CONTROL_ACTOR
primary_ui_label        = "Registreret kontrolaktør"
ui_section               = "Kontrolregistrering"
source_assertion_class  = SOURCE_REPORTED
```

Relationen må **ikke** canonicaliseres som `DIRECT_OWNERSHIP`, `INDIRECT_OWNERSHIP`, `ULTIMATE_OWNER`, `BOARD_ROLE`, `MANAGEMENT_ROLE` eller `MEMBERSHIP`. Normativt:

```
counts_as_direct_ownership     = false
included_in_indirect_ownership = false
ownership_percentage           = NOT_APPLICABLE
control_percentage             = NOT_AVAILABLE
```

Betegnelsen må ikke i sig selv fremstilles som dokumentation for juridisk ejerskab, økonomisk ejerskab, en bestemt ejerandel, fuld beslutningsmagt, operationel ledelse, bestyrelsesmedlemskab, eller faktisk/uafhængigt verificeret kontrol. Den præcise produktforklaring, vist i UI'en:

> "En aktør, som kilden har placeret i sin særskilte kontrolregistrering. Registreringen er ikke nødvendigvis det samme som direkte ejerskab, en ejerandel, en ledelsesrolle eller uafhængigt verificeret kontrol."

Bemærk ordet **"aktør"**, ikke "person", i selve kategorinavnet — en registreret kontrolaktør kan være knyttet til en entity, som upstream klassificerer som `PERSON`, `COMPANY`, `ORGANIZATION`, `PUBLIC_AUTHORITY` eller `UNKNOWN_ENTITY`. Heimdal må ikke overskrive upstream entity type alene på grund af relationstypen `REGISTERED_CONTROL_ACTOR`.

**Hvorfor denne forsigtighed er reel, ikke teoretisk:** et fremlagt eksempel — vedtægterne for *Ognarfelag Tryggingartakaranna* — dokumenterer en medlems- og governancestruktur, hvor forsikringstagere kan være medlemmer, repræsentanter vælges af medlemmerne, repræsentantskabet vælger bestyrelsen, og bestyrelsen udpeger ledelsen. En upstream-registrering som `Veruligur Eigari` for en sådan entity må ikke automatisk fortolkes som almindeligt juridisk eller økonomisk ejerskab. Heimdal tager ikke stilling til, om den konkrete upstream-registrering juridisk er korrekt — kun til, at produktet ikke må fremstille registreringen som stærkere, end sourcefeltet understøtter.

Virksomhedsportalen (§16) og personportalen (§17) viser Registreret kontrolaktør i deres egen **"Kontrolregistrering"**-sektion, adskilt fra ejerskabssektionerne.

### 14a.2 Ingen registreret kontrolaktør (source: `Ongin veruligur eigari` / `O.V.E.`)

```
canonical_storage_class = ENTITY_STATUS
canonical_status_type   = NO_REGISTERED_CONTROL_ACTOR
primary_ui_label        = "Ingen registreret kontrolaktør"
ui_section               = "Kontrolregistrering"
source_assertion_class  = SOURCE_REPORTED
```

`O.V.E.` gemmes **som en status på den relevante virksomhed**, ikke som en canonical relation fra virksomheden til sig selv. Eksempel på upstream-række (fremlagt, `OBSERVED`):

```
From: Betri Banki    Relation: O.V.E.    To: Betri Banki
```

canonicaliseres som:

```
entity            = Betri Banki
status            = NO_REGISTERED_CONTROL_ACTOR
raw_source_label  = "O.V.E."
```

— **ikke** som en graf-kant `Betri Banki → O.V.E. → Betri Banki`. Canonical self-edge er forbudt for `O.V.E.`.

Statussen betyder **kun**: "Kilden rapporterer ingen registreret kontrolaktør for denne entity i dette snapshot." Den betyder **ikke**: at virksomheden ikke har juridiske ejere; at den ikke har medlemmer; at den ikke har bestyrelse; at den ikke har ledelse; at ingen aktør har indflydelse; at Heimdal uafhængigt har verificeret fraværet; eller at der ikke findes fejl/mangler i upstream-systemet.

Hvis `O.V.E.` observeres med **forskellige** From- og To-entities (ikke det forventede self-edge-mønster), må systemet ikke gætte en fortolkning — det registreres som `semantic_status = UNRESOLVED_SOURCE_ANOMALY` og dokumenteres i `HEIMDAL-CURRENT-RELATIONS-SOURCE-CONTRACT.md`.

## 14b. Raw From/To — bevaring og semantisk fortolkning

For alle upstream-rækker bevares altid, uændret:

```
raw_from_source_entity_id
raw_from_source_entity_label
raw_to_source_entity_id
raw_to_source_entity_label
raw_source_relation_label
raw_source_relation_type_id
snapshot_id
raw_response_reference
```

Raw From og To må **aldrig** omskrives eller byttes i raw archive (§7.1). Semantisk fortolkning sker separat — der skelnes derfor mellem `raw_from_entity`/`raw_to_entity` (transportfelter, som de kom fra upstream) og `canonical_subject_entity`/`canonical_object_entity` (den semantiske betydning, tildelt **pr. relationstype**). Raw-retning og semantisk retning må ikke sammenblandes.

**From og To er transportfelter fra upstream. Deres semantiske betydning skal defineres pr. relationstype. Der findes ikke én universel regel om, at From altid er person, ejer eller aktiv part.**

**14b.1 From/To for `Eigari N%` (direkte ejerskab, jf. §14).** Fremlagte, `OBSERVED` eksempler:

```
Betri        → Eigari 100.0% → Betri Banki
Betri Banki  → Eigari 100.0% → Betri Heim
```

Normativ fortolkning: `From/Frá = ejer`, `To/Til = ejet entity`.

```
canonical_relation_type       = DIRECT_OWNERSHIP
canonical_subject_entity      = owner
canonical_object_entity       = owned_entity
semantic_direction            = OWNER_TO_OWNED
semantic_direction_confidence = OBSERVED_AND_OWNER_ACCEPTED
```

"Betri → Eigari 100.0% → Betri Banki" canonicaliseres som *Betri ejer 100 % af Betri Banki*; "Betri Banki → Eigari 100.0% → Betri Heim" som *Betri Banki ejer 100 % af Betri Heim*. Dette giver den lokalt afledte ejersti Betri → 100 % Betri Banki → 100 % Betri Heim, og den afledte indirekte ejerandel 100 % × 100 % = 100 % (§15). Raw relationstekst og parsed procent opbevares fortsat separat (§14). **Denne From/To-regel gælder kun `Eigari N%` og må ikke automatisk generaliseres til andre relationstyper.**

**14b.2 From/To for `Veruligur Eigari` (Registreret kontrolaktør, jf. §14a.1).** Fremlagt, `OBSERVED` eksempel: `From: Aktør A` · `Relation: Veruligur Eigari` · `To: Ognarfelag Tryggingartakaranna`.

```
canonical_relation_type       = REGISTERED_CONTROL_ACTOR
canonical_subject_entity      = registered_actor
canonical_object_entity       = registered_entity
semantic_direction            = REGISTERED_ACTOR_TO_ENTITY
semantic_direction_confidence = OBSERVED_FOR_SUPPLIED_EXAMPLES
```

Canonicaliseres som "Aktør A → Registreret kontrolaktør for → Ognarfelag Tryggingartakaranna" — **ikke** som "Aktør A ejer Ognarfelag Tryggingartakaranna." Der tildeles ingen ejerprocent, og relationen indgår ikke i direkte eller indirekte ejerskabsberegninger (§14, §15).

**14b.3 From/To for `O.V.E.` (jf. §14a.2).** From/To har ingen canonical edge-betydning her:

```
raw_from_entity          = preserved
raw_to_entity             = preserved
canonical_subject_entity = the affected entity
canonical_object_entity  = NULL
canonical_relation_type  = NULL
canonical_status_type    = NO_REGISTERED_CONTROL_ACTOR
semantic_direction        = NOT_AN_EDGE
```

**14b.4 Andre relationstyper.** From/To-semantik for øvrige relationstyper (Nevnd/Stjórn-roller, Annað) er ikke ændret af denne sektion og forbliver, som de allerede er beskrevet i §12 og §17.3 — denne §14b generaliserer ikke automatisk til dem. Den fulde, klassificerede direction matrix (inkl. `UNKNOWN_RELATION_TYPE`) ligger i `HEIMDAL-CURRENT-RELATIONS-SOURCE-CONTRACT.md`.

## 15. Indirekte ejerskab

Beregnes lokalt fra gemte direkte ejerrelationer. For hvert resultat vises: samlet beregnet procent, alle bidragende ejerstier, produktet af procenter på hver sti, snapshotgrundlag.

Ved cyklus (**OWNER DECISION** `cross-ownership-cycles`, uændret fra v1.1, gælder begge faser): ingen indirekte procent vises; de direkte relationer vises; cyklussen forklares. Der vises intet samlet kontrolflag (**OWNER DECISION** `control-definition`, uændret, gælder begge faser).

## 16. Virksomhedsportal (Fase 1)

`/companies/:canonical_company_id`. Skal mindst vise:

```
navn, source entity ID, virksomhedstype
snapshotdato, upstream freshness
rå kildehenvisning
```

**16.1 Hvem ejer virksomheden?** Direkte ejere, ejerandele, indirekte ejere, alle ejerstier. Alle ejere er klikbare. (Registreret kontrolaktør vises **ikke** her — se 16.1a; den er ikke en ejerskabsfakt.)

**16.1a Kontrolregistrering.** Egen sektion, adskilt fra 16.1: Registreret kontrolaktør (§14a.1) eller status "Ingen registreret kontrolaktør" (§14a.2), med kildehenvisning og snapshotdato. Registrerede kontrolaktører er klikbare, men tælles ikke med i 16.1's ejerskabslister eller i indirekte ejerskabsberegninger (§14a.1, §15).

**16.2 Hvad ejer virksomheden?** Virksomheder ejet direkte, virksomheder ejet indirekte, ejerandel i hver, alle ejerstier. Alle ejede virksomheder er klikbare. **Indgående og udgående ejerskab må ikke blandes i samme liste.** Dette er ikke en teoretisk forsigtighedsregel: den ældre PDF-æras ejerskabskilde (`eigaraviðurskifti`) havde et dokumenteret, konkret eksempel på præcis denne fejl (kildeartefaktet `edges.jsonl` var indekseret efter *fetch-subjekt*, ikke *kant-subjekt* — hvert snapshot indeholdt både indgående ejere og udgående besiddelser for samme selskab, og naiv sammenblanding gav historisk 865 falske over-100%-selskaber mod 3 reelle). Samme retningsfejl er strukturelt mulig i `get-table`/`get-graph`-responser fra sg.vs.fo (§6), da `fromId`/`toId`-retningen for virksomhed-ejer-virksomhed-relationer er `UNCONFIRMED` (se `HEIMDAL-CURRENT-RELATIONS-SOURCE-CONTRACT.md` §4) — reglen gælder derfor Fase 1 fra start, ikke kun som en historisk lære.

**16.3 Personer knyttet til virksomheden.** Alle aktuelle personer under Nevnd, Stjórn, Eigarar, Annað. For hver: navn, canonical person-ID, relationstype, source group, source relation label, snapshot. Alle personer klikbare.

**16.4 Relation til andre virksomheder.** Se §19 (company-to-company relation contexts).

## 17. Personportal (Fase 1)

`/persons/:canonical_person_id`. Skal mindst vise:

```
navn, source entity ID, upstream entity type
seneste snapshot, freshness-status, identitetsstatus
```

**17.1 Direkte ejerskab.** Alle virksomheder personen direkte ejer: virksomhed, ejerandel, relationstype, snapshotdato, kildehenvisning. Virksomhedens navn klikbart. (Registreret kontrolaktør vises **ikke** her — se 17.1a.)

**17.1a Kontrolregistrering.** Egen sektion: virksomheder, hvor personen er registreret som kontrolaktør (§14a.1), med kildehenvisning og snapshotdato. Adskilt fra og tælles ikke med i 17.1/17.2's ejerskabstal.

**17.2 Indirekte ejerskab.** Alle virksomheder personen ejer indirekte: samlet beregnet indirekte ejerandel, alle bidragende ejerstier, procentberegning for hver sti, snapshotgrundlag, eventuelle cykler eller uklarheder. Alle virksomheder i en ejersti klikbare.

**17.3 Roller i virksomheder.** Alle aktuelle relationer i de fire source-grupper (Nevnd, Stjórn, Eigarar, Annað): virksomhed, source group, source relation type, source relation label, normaliseret relationstype, snapshotdato. Source-relationen bevares, også når den endnu ikke er normaliseret.

**17.4 Relation til andre personer.** Se §18 (person-to-person relation contexts).

## 18. Person-til-person relationer — forklarligt relationstal

Personportalen skal vise, hvilke andre personer personen er knyttet til gennem virksomheder. Et tal som "Person A er knyttet til Person B i 19 aktuelle relation contexts" **må aldrig stå alene** — brugeren skal kunne se præcis, hvad de 19 forbindelser består af.

### 18.1 Opdeling

For Person A og Person B vises: antal fælles virksomheder, antal aktuelle fælles Nevnd-relationer, antal aktuelle fælles Stjórn-relationer, antal aktuelle fælles ejerrelationer, antal aktuelle relationer gennem Annað, antal cross-role-relationer.

**Cross-role** betyder f.eks.: Person A er i Nevnd, Person B er i Stjórn, i samme virksomhed. Eller: Person A er ejer, Person B er i Nevnd. Eller: Person A er i Stjórn, Person B har en relation under Annað. Alle kombinationer mellem Nevnd, Stjórn, Eigarar, Annað skal kunne registreres og forklares.

### 18.2 Fælles ejerskab

For hver fælles virksomhed: Person A's ejerandel, Person B's ejerandel, direkte eller indirekte ejerskab, ejerstier, snapshotdato. Fælles kontrolregistrering (begge er Registreret kontrolaktør for samme virksomhed, §14a.1) vises som en separat, tydeligt mærket kategori — ikke sammenblandet med ejerandelstallene.

### 18.3 Fælles virksomhedsroller

For hver fælles virksomhed: Person A's relation, Person B's relation, source group for hver, relationstype for hver, samme snapshot. Eksempel: Virksomhed X — Person A: Nevndarformaður; Person B: Stjóri. Dette er en gyldig personrelation, selvom de to personer ikke sidder i samme gruppe.

### 18.4 Relation context — definition

En **relation context** skal mindst bestå af:

```
person_a, person_b, company
person_a_relation, person_b_relation
snapshot
```

Det samme context må **ikke tælles flere gange**, fordi det forekommer i både `get-table`- og `get-graph`-respons, eller fordi det blev fundet fra begge ender af samme relation (§20 — deduplicering). UI'en viser summen med en fold-ud-visning, f.eks.:

```
Person A ↔ Person B
19 aktuelle forbindelser
  6 fælles virksomheder
  4 fælles Nevnd-relationer
  2 fælles Stjórn-relationer
  3 Nevnd↔Stjórn-relationer
  2 fælles ejerrelationer
  2 relationer gennem Annað
```

Disse tal er kun et eksempel på præsentationen — de må ikke hardcodes.

## 19. Virksomhed-til-virksomhed relationer

Virksomhedsportalen skal vise, hvordan virksomheden er knyttet til andre virksomheder: direkte ejerskab, indirekte ejerskab, samme direkte ejer, samme registrerede kontrolaktør (§14a.1), fælles bestyrelsespersoner, fælles ledelsespersoner, fælles personer på tværs af Nevnd/Stjórn/Annað.

For Virksomhed A og Virksomhed B vises: antal fælles personer, antal fælles ejere, antal fælles Nevnd-personer, antal fælles Stjórn-personer, antal cross-role-personer, direkte ejerforbindelse, indirekte ejerforbindelse. Eksempel: "Virksomhed A er knyttet til Virksomhed B gennem 7 personer" — foldes ud til hver person og deres relation i hver virksomhed.

En **company relation context** skal mindst bestå af:

```
company_a, company_b
relation_basis, related_entity
company_a_relation, company_b_relation
snapshot
```

`relation_basis` ∈ `{DIRECT_OWNERSHIP, INDIRECT_OWNERSHIP, SHARED_OWNER, SHARED_BENEFICIAL_OWNER, SHARED_BOARD_PERSON, SHARED_MANAGEMENT_PERSON, CROSS_ROLE_PERSON, SHARED_OTHER_RELATION}`. Et samlet virksomhedsrelationstal må aldrig stå uden denne opdeling.

## 20. Datakvalitet og deduplicering

Relationstal må ikke overdrives gennem dubletter. Systemet skal genkende samme relation, selv når den returneres fra flere traversal-startpunkter, fra både `get-table` og `get-graph`, fra begge ender af samme relation, eller i flere overlappende API-responses (§6 — bekræftet at samme relation fremkommer symmetrisk fra begge ender).

Den samme direkte source-relation gemmes kun én gang pr. snapshot. En deterministisk relation identity baseres mindst på: `snapshot, from source entity, to source entity, source relation type, source relation label, relation direction`. Rå payloads må gerne gemmes flere gange i raw archive (§7.1) — canonical relationer må ikke dubleres (§7.3).

## 21. Netværksvisning

Bruger samme canonical IDs og portalroutes som alt andet. Node types: `PERSON`, `COMPANY`, `ORGANIZATION`, `PUBLIC_AUTHORITY`. Klik på en personnode åbner `/persons/:canonical_person_id`; klik på en virksomhedsnode åbner `/companies/:canonical_company_id`; klik på en `ORGANIZATION`- eller `PUBLIC_AUTHORITY`-node åbner `/entities/:canonical_entity_id` (§3.4).

Edge types bevarer faktisk betydning: `OWNS`, `BENEFICIAL_OWNER_OF`, `BOARD_MEMBER_OF`, `BOARD_CHAIR_OF`, `MANAGES`, `OTHER_SOURCE_RELATION`. Afledte edges er tydeligt markeret: `SHARES_COMPANY_WITH`, `SHARES_BOARD_WITH`, `RELATED_THROUGH_COMPANY` — og kan foldes ud til de direkte source-relationer, de bygger på.

## 22. Search

Søgning finder personer, virksomheder, source entity IDs, canonical IDs, virksomhedsnavne, personnavne. Alle resultater viser navn, entity type, canonical ID, relevant source ID, og linker til korrekt portal. En virksomhed må ikke vises som person; en person må ikke vises som virksomhed.

## 23. Co-involvement i Fase 1

Fase 1 har kun aktuelle snapshots. Produktet må derfor vise:

```
CURRENT_SHARED_COMPANIES
CURRENT_SHARED_BOARD_MEMBERSHIPS
CURRENT_SHARED_MANAGEMENT_RELATIONS
```

Det må **ikke** vise autoritative historiske tal som antal historiske bestyrelsesperioder, samlet historisk overlap, første/seneste fælles bestyrelsesperiode — disse hører til Fase 2.

v1.2's beslutning om `SIMULTANEOUS_BOARD_PERIODS` (§29, `co-involvement-network-metric`) flyttes derfor til `PHASE_2_HISTORICAL_METRIC`. Den er **ikke** Fase 1's default. Fase 1's default er: **antal aktuelle fælles bestyrelsesrelationer**, baseret på samme snapshot (jf. §18.1's opdeling).

## 24. Source completeness (Fase 1)

Sondringen fra v1.0's kildeundersøgelse bevares og gælder nu sg.vs.fo:

```
harvested population completeness  — alle allerede kendte, høstede entities har status
source universe completeness       — alle relevante entities hos kilden er opdaget
```

Det er ikke nok at hente relationer for allerede kendte entities (§8). Systemet skal dokumentere: hvordan entity-universet opdages, hvor mange entities upstream indeholder, hvor mange der er hentet, hvor mange der mangler, hvor mange der fejlede, hvor mange der kræver retry. Dette er den `denominator`, `collection_scope` (§8a.2) refererer til — en `FULL_POPULATION_CLAIMED`-status er kun gyldig, når disse tal faktisk er dokumenterede, ikke antaget.

**Præcisering, v2.3.** Metoden til denominatoren er identificeret (§8): antallet af distinkte registreringsnumre fundet, når `search-cbi`-enumerationen har nået sin dokumenterede terminalbetingelse. Den er **ikke etableret endnu** — `denominator_method_identified=true`, `denominator_established=false`. Et fejlende request fjerner ikke en virksomhed fra denominatoren; den bliver stående med sin status.

**HTTP 200 `[ ]` er ikke bevis for `NOT_FOUND`.** Et ikke-eksisterende source-ID og en virksomhed uden relationer gav byte-identiske svar (§6). Virksomhedsinventoryet er derfor den autoritative indikator for, om en virksomhed indgår i populationen: for en inventoried company registreres HTTP 200 `[ ]` som et **succesfuldt hentet raw response med nul observerede relationer**. Råindsamlingen må ikke konstruere entity-eksistens ud fra antal relationer.

Hver source entity skal have en terminal acquisition-status:

```
DISCOVERED, FETCHED, FETCHED_NO_RELATIONS, RETRY_PENDING,
RATE_LIMITED, FORBIDDEN, NOT_FOUND, INVALID_RESPONSE,
SOURCE_CHANGED, EXCLUDED_WITH_REASON
```

Ingen silent skips.

## 25. Blocking requirements (Fase 1)

```
Heimdal serverer produktdata fra sin lokale database, ikke direkte fra upstream.
Alle rå upstream-responses gemmes med timestamp og hash.
Ingen source entity forsvinder uden terminal status.
Alle relationer bevarer source group, type og raw label.
Alle ejerprocenter bevarer både raw og parsed værdi.
Registreret kontrolaktør (source: Veruligur Eigari) bevares som separat relationstype, tæller ikke som ejerskab, og indgår ikke i indirekte ejerskabsberegning.
Ingen registreret kontrolaktør (source: O.V.E.) gemmes som entity-status, aldrig som en canonical self-edge.
Raw From og To bevares altid ordret; canonical subject/object tildeles pr. relationstype, aldrig efter én universel regel.
Alle indirekte ejerandele kan forklares med konkrete stier.
Ejerskabscykler detekteres.
Hver person har et stabilt canonical person-ID.
Hver virksomhed har et stabilt canonical company-ID.
Source IDs og canonical IDs opbevares separat.
Alle personer og virksomheder har klikbare portaler.
Ingen genereret portalroute giver 404.
Personportalen viser direkte og indirekte ejerskab med ejerstier.
Personportalen viser roller under Nevnd, Stjórn, Eigarar og Annað.
Personportalen viser relationer til andre personer, forklarligt (§18).
Cross-role-relationer (Nevnd↔Stjórn, Eigarar↔Nevnd, m.fl.) bevares.
Virksomhedsportalen viser både hvem der ejer virksomheden og hvad den selv ejer,
  i adskilte lister.
Virksomhedsportalen viser relationer til andre virksomheder, forklarligt (§19).
Samme source-relation tælles ikke flere gange pga. overlappende upstream-responser.
Alle UI-visninger viser snapshot- og freshness-tidspunkt.
Upstream-fejl gør ikke allerede gemte data utilgængelige.
Collector respekterer rate-limit- og adgangsfejl.
Ingen adgangskontrol eller teknisk begrænsning omgås.
Fase 1 viser aktuelle snapshotrelationer og fremsætter ikke udokumenterede
  historiske påstande.
PDF-fasen (Fase 2) blokerer ikke Fase 1.
```

## 26. Explicit non-goals for Fase 1

```
PDF historical extraction
historiske lifecycle-events
historiske bestyrelsesperioder
konkursanalyse
likvidationshistorik
fusioner
historiske adresser
bogført egenkapital
Top persons
AI-genereret risikoscore
samlet control flag
produktion eller deployment
```

Disse må ikke snige sig ind som blokeringer for det aktuelle relationsprodukt.

**v2.4:** Listen gælder Fase 1. PDF-udtræk og de historiske punkter er bygget som Fase 2 (§27). De er slået fra og blokerer ikke Fase 1. `produktion eller deployment` gælder fortsat for hele produktet.

---

# DEL C — FASE 2: PDF-BASERET HISTORIK (BYGGET, SLÅET FRA)

## 27. Status

```
PHASE_2_HISTORICAL_ENRICHMENT
STATUS = BUILT_FLAGS_OFF        (v2.3: PAUSED)
```

**v2.4:** Fase 2 er bygget efter `HEIMDAL-PDF-HISTORIK-MASTERPLAN-v3.1.2.md`, som er normativ for PDF-reglerne. Dataene ligger i dev-DB'en som projection-run 33 (§1a.2). Integration og UI er slået fra i `tools/dev_env.sh`. Fase 2 må fortsat ikke blokere Fase 1, og ingen Fase 1-funktion må afhænge af PDF-data. Portalen er fail-closed: er flagene fra, eller kan projection-DB'en ikke nås, viser den Fase 1 uændret.

Afsnittet nedenfor er bevaret fra v2.3:

Eksisterende PDF-data, PDF-pipeline og alle recovery-artefakter (`gate03/*`, `fable-recovery`, `frontier-recovery`, `webui-recovery`) **slettes ikke**. De klassificeres som `DEFERRED_HISTORICAL_SOURCE_MATERIAL`. Ingen Fase 1-implementation må afhænge af PDF-events eller de eksisterende GATE-03-tabeller.

Fase 2 kan senere levere: historiske bestyrelsesperioder, historiske ledelsesperioder, konkurs, likvidation, fusion, stiftelse, ophør, navnehistorik, historisk kapital, historisk adresse.

## 28. Bevaret indhold fra v1.0–v1.2 (nu Fase 2-scope)

Alt indhold nedenfor er **uændret i substans** fra v1.2 og forbliver normativt **for Fase 2**. Det er ikke blokerende for Fase 1 (§25, §26).

### 28.1 Company identity, navne, dokumenter (PDF-kilde)

Company identity-felter afledt af `felag_pdf`: `registration_number`, `current_registered_name`, `company_type`, `current_status`, `address`, `municipality`, `purpose`, `signing_rule`. Navne og aliaser: `current_registered_name`, `former_registered_name`, `alias`, `hjánavn`, `trading_name`, `domain_or_brand_name`, hver med `valid_from`/`valid_to`/`source_document`/`source_page`/`name_type`. Kildedokumenter: `document_id`, `document_type`, `source_url`, `source_registry_id`, `pdf_sha256`, `publication_or_extraction_date`, `page_count`.

**Vigtig arkitektur-nuance i v2.0:** Fase 1's entity-poster (fra sg.vs.fo) er **tynde** — navn, type, adresse-som-tekst. Purpose, signing_rule, registreret kapital, hjánøvn og fuld navnehistorik findes **kun** i PDF-kilden og ankommer først med Fase 2. En virksomhedsportal i Fase 1 vil derfor mangle disse felter — det skal vises som `Ikke tilgængelig i Fase 1`, ikke som en fejl.

### 28.2 Company lifecycle

```
INCORPORATED, REGISTERED, RENAMED, ALIAS_ADDED, ALIAS_REMOVED,
COMPANY_TYPE_CHANGED, ADDRESS_CHANGED, CAPITAL_CHANGED,
BOARD_CHANGED, MANAGEMENT_CHANGED, OWNERSHIP_CHANGED,
MERGER_ANNOUNCED, MERGED_INTO, MERGED_FROM, SPLIT,
LIQUIDATION_STARTED, LIQUIDATION_ENDED,
BANKRUPTCY_STARTED, BANKRUPTCY_ENDED,
DISSOLVED, CEASED, REACTIVATED
```

Datotyper holdes adskilt: stiftelsesdato, registreringsdato, publikationsdato, ikrafttrædelsesdato, konkursdato, likvidationsdato, opløsningsdato, ophørsdato. `effective_date` er i dag en erklæret proxy for `document_date` (`legacy_document_date_proxy`) — dette forbliver synligt i UI'en. `SPLIT` og merger-typerne er `DESIRED_BUT_SOURCE_NOT_YET_CONFIRMED`.

### 28.3 Mergers, bankruptcy, capital, roles — bevaret

Fusionsmodel (`predecessor_company`/`successor_company`), konkurs-/likvidationsmodel (§8 i v1.2, delt `temporal_relationship`-struktur pr. `event_class`), kapitalmodel (`Partapeningur` ≠ egenkapital, `Roknskapir dato` ≠ egenkapitalbeløb), og fuld rollemodel for bestyrelse/ledelse (§11 i v1.2) er alle uændrede og forbliver normative for Fase 2. Se `HEIMDAL-SOURCE-CAPABILITY-MATRIX.md` (nu opdelt CURRENT/HISTORICAL) for den fulde felt-for-felt evidens fra de 8 undersøgte `felag_pdf`-eksempler og data-audittens 14 fund (AUD-A1 til AUD-D3).

### 28.4 Data completeness (Fase 2, PDF-kilde)

18.431 registerposter totalt (`lysing_pdf` 12.428, `felag_pdf` 5.290, `search_page` 622, `association_detail_json` 91). Hvert dokument har én terminal disposition: `PROCESSED`, `PROCESSED_NO_RELEVANT_DATA`, `MISSING`, `INVALID`, `UNREADABLE`, `EXCLUDED_WITH_REASON`, `REQUIRES_HUMAN_REVIEW`.

---

# DEL D — FÆLLES AFSLUTTENDE MATERIALE

## 29. OWNER DECISIONS (v1.0–v1.2, bevaret)

Ni beslutninger blev truffet 2026-07-31 (v1.1–v1.2). De forbliver normative. Fase-tilhørsforhold er markeret.

| decision_id | Valgt | Fase |
|---|---|---|
| control-definition | A — RAW_FACTS_ONLY_NO_CONTROL_FLAG | Begge (§15) |
| cross-ownership-cycles | A — DETECT_AND_WITHHOLD_AMBIGUOUS_PERCENTAGE | Begge (§15) |
| bankruptcy-relations-scope | B — INCLUDE_ALL_DOCUMENTED_ROLES_WITH_ROLE_CATEGORIES | Fase 2 |
| historical-addresses | A — CURRENT_ADDRESS_IN_INITIAL_PRODUCT | Fase 2 |
| book-equity-priority | B — SEPARATE_FUTURE_ACCOUNTING_SOURCE (non_blocking) | Fase 2 |
| identity-same-name | A — MULTI_ATTRIBUTE_EVIDENCE_AND_UNRESOLVED_COLLISIONS | Begge (§3.2) |
| organization-vs-company | A — REGISTRATION_NUMBER_BASED_CLASSIFICATION | Begge (§3.1) |
| liquidation-relation-model-scope | B — SHARED_LIFECYCLE_MODEL_WITH_EXPLICIT_EVENT_CLASS | Fase 2 |
| co-involvement-network-metric | B — SIMULTANEOUS_BOARD_PERIODS | **Flyttet til Fase 2** (§23) |

Fuld begrundelse for hver (spørgsmål, muligheder, konsekvens) er bevaret uændret fra v1.2 og kan læses i `HEIMDAL-SOT-v1.2-to-v2.0-diff.md` (som citerer dem i sin helhed) samt i versionshistorikken.

## 29a. OWNER DECISIONS (v2.1 — kontrolregistrering og From/To-semantik)

### decision_id: REGISTERED_CONTROL_ACTOR_TERMINOLOGY
```
source_term="Veruligur Eigari"
primary_ui_label="Registreret kontrolaktør"
ui_section="Kontrolregistrering"
canonical_relation_type="REGISTERED_CONTROL_ACTOR"
counts_as_ownership=false
included_in_indirect_ownership=false
```
Se §14a.1 for den normative tekst.

### decision_id: NO_REGISTERED_CONTROL_ACTOR_TERMINOLOGY
```
source_term="Ongin veruligur eigari"
source_abbreviation="O.V.E."
primary_ui_label="Ingen registreret kontrolaktør"
canonical_storage_class="ENTITY_STATUS"
canonical_status_type="NO_REGISTERED_CONTROL_ACTOR"
canonical_self_edge_forbidden=true
```
Se §14a.2 for den normative tekst.

### decision_id: RELATION_SPECIFIC_DIRECTION_SEMANTICS
Raw From og To bevares altid ordret. Canonical subject og object tildeles efter den specifikke source-relationstype. Ingen universel From/To-fortolkning er tilladt. Se §14b for den normative tekst.

### decision_id: DIRECT_OWNERSHIP_DIRECTION
```
For source-relationer der matcher Eigari N%:
From=owner
To=owned entity
canonical_direction=OWNER_TO_OWNED
```
Se §14b.1.

### decision_id: REGISTERED_CONTROL_ACTOR_DIRECTION
```
For source-relationen Veruligur Eigari:
From=registered actor
To=registered entity
canonical_direction=REGISTERED_ACTOR_TO_ENTITY
```
Denne relation er ikke ejerskab. Se §14b.2.

### decision_id: OVE_IS_STATUS_NOT_EDGE
```
For O.V.E.:
raw From og To bevares;
ingen canonical relation edge oprettes;
den berørte entity får status NO_REGISTERED_CONTROL_ACTOR.
```
Se §14b.3.

## 29b. OWNER DECISIONS (v2.2 — virksomhedsregister og indsamlingsrækkefølge)

### decision_id: CURRENT_SOURCE_OBSERVATION_NOT_HISTORICAL_VALIDITY
Fase 1 registrerer, hvornår Heimdal observerede source-data. Fase 1 påstår ikke relationens juridiske eller historiske alder. Historisk gyldighed og kildedokument-datoer hører til Fase 2. Se §10, §11.

### decision_id: COMPANY_INVENTORY_BEFORE_FULL_RELATION_ACQUISITION
Heimdal etablerer et lokalt, målbart register over de virksomheder, der indgår i den valgte indsamlingspopulation. Virksomhedsregisteret bruges som input-kø til `get-table`. En delvis pilot-population er kun tilladt, når den eksplicit er mærket `PARTIAL_POPULATION`. Se §8a.1, §8a.2.

### decision_id: GET_TABLE_PRIMARY_RELATION_SOURCE
`get-table` er den primære upstream-response til aktuel relationsindsamling. `get-graph` og `get-cve` bruges kun til dokumenteret validering, fallback eller metadatabehov. Se §8a.4.

### decision_id: RAW_ACQUISITION_BEFORE_RELATION_PROCESSING
Alle relevante `get-table`-responses gemmes lokalt og immutabelt, før canonical relationsbehandling begynder. Rå indsamling og relationel behandling er adskilte faser. Se §8a.5 (Fase A/B/C).

### decision_id: RELATION_PROCESSING_FROM_LOCAL_SNAPSHOTS
Canonical relationer, ejerskabsberegninger og relation contexts produceres fra Heimdals lokale raw snapshots — ikke direkte fra levende upstream-responses. Se §8a.5 (Fase C), §9.

### decision_id: COMPANY_SEARCH_FROM_LOCAL_INVENTORY
Virksomhedssøgning bruger Heimdals lokale virksomhedsregister. Produktet må ikke afhænge af live upstream-søgning i runtime. Se §8a.1, §22.

## 29c. OWNER DECISIONS (audit 2026-11)

Truffet 2026-11-05 og 2026-11-06 ("Ja til dine anbefalinger"). Fuld tekst: `docs/audit-2026-11/02-FIX-PLAN.md` §1.

| # | Beslutning | Valgt |
|---|---|---|
| D1 | Genskab `heimdal-qa`-dumpene | ja, i nye databaser |
| D2 | Dev-database efter genskabelsen | den genskabte 2026-09-04-tilstand |
| D3 | De gamle `heimdal-*`-containere og forældreløse volumes | slettes efter backup |
| D4 | Persondata i git-historikken | fjernes fra HEAD, ingen history rewrite nu |
| D5 | SOT-governance | v2.4 skrives efter virkeligheden, derefter godkender ejeren |
| D6 | Registry analytics | parkeret med flag fra |
| D7 | PDF-historik | run 33 bevares; flagene slås til efter W3 og W4d |
| D8 | Ejerskabscykler og self-edges | markør: stien går gennem en cyklus og er tilbageholdt |
| D9 | Review-livscyklus | dokumentationen skæres ned til det, der findes |
| D10 | Menneskelige links på tværs af builds | genanvendes i hver projection-build |
| D11 | Collectorens User-Agent | ingen privat kontaktadresse |
| D12 | OSM-kortfliser | accepteret som dokumenteret ekstern afhængighed |
| D13 | FK'en `source.documents` | droppes |
| D14 | PEP | seed fjernes; PEP er tom, indtil der findes en kildebelagt import |
| D15 | Review-godkendelser uden for de viste kandidater | forbudt |

## 30. Companion documents

```
HEIMDAL-PRODUCT-SOURCE-OF-TRUTH.summary.json       — struktureret sammenfatning
HEIMDAL-SOURCE-CAPABILITY-MATRIX.md                — CURRENT (sg.vs.fo) vs HISTORICAL (PDF), felt for felt
HEIMDAL-CURRENT-RELATIONS-SOURCE-CONTRACT.md        — det observerede sg.vs.fo-interface, klassificeret
HEIMDAL-COMPANY-DISCOVERY-RECONNAISSANCE.md         — afsluttet reconnaissance, evidensgrundlag for v2.3
HEIMDAL-CURRENT-DATA-ACQUISITION-PLAN.md            — CURRENT FIVE-WORK-PACKAGE COMPANY INVENTORY
                                                      AND RAW ACQUISITION PLAN, implementeret i
                                                      heimdal_collect.py (v2.3: "ikke implementeret")
HEIMDAL-SOT-v1.2-to-v2.0-diff.md                    — eksplicit ændringsbeskrivelse
HEIMDAL-PDF-HISTORIK-MASTERPLAN-v3.1.2.md           — normativ for Fase 2 (PDF-historik), fra v2.4
STATUS.md                                          — aktuel tilstand og tal, fra v2.4
```

Ingen af dem må indføre krav, der ikke findes i dette dokument. Dette markdown-dokument er det normative.

**Acquisition-planens form (v2.3).** Den tidligere lineære rækkefølge `WU-ACQ-01 … WU-ACQ-08` er **superseded**. Den aktuelle plan har fem work packages og dækker company inventory, råindsamling og completion/gap review; relation processing ligger uden for den:

```
1. Permission record and preflight
2. Company inventory enumeration
3. Inventory quality and queue preparation
4. Raw get-table level=1 acquisition
5. Completion report and relation-processing handoff
```

## 31. Final quality review (uændret princip fra v1.0)

Før implementation skal en cold reviewer bekræfte: at produktet stadig er enkelt og forståeligt; at WebUI ikke afhænger direkte af upstream; at datahøst og lokal lagring er normative krav, ikke forslag; at API'et ikke beskrives som officielt dokumenteret; at konservativ source-brug er påkrævet, også når tilladelse er givet; at entity-universe discovery hviler på en dokumenteret metode og ikke på graph traversal alene; at raw snapshots er immutable; at Fase 1 og Fase 2 ikke sammenblandes noget sted; at PDF-systemet er udsat, ikke slettet, og ikke blokerer Fase 1; at ejerbeslutningerne fra v1.1–v1.2 er bevaret og korrekt fase-tildelt; at canonical ID-modellen er fælles infrastruktur, ikke en Fase 1-detalje; at person- og virksomhedsrelationstal aldrig fremstår uforklarlige; at cross-role- og virksomhed-til-virksomhed-relationer er dækket; at deduplicering af overlappende upstream-responser er et eksplicit krav; at `implementation_authorized` stadig er `false` (fra v2.4: indtil ejeren skriftligt har godkendt det byggede scope i §1a).
