---
title: Heimdal Source Capability Matrix
document_type: SUPPORTING_EVIDENCE_MATRIX
status: CURRENT_RELATIONS_STRATEGY_DEFINED_PENDING_OWNER_APPROVAL
normative_document: HEIMDAL-PRODUCT-SOURCE-OF-TRUTH.md (v2.3)
implementation_authorized: false
generated_at: 2026-07-31
updated_at: 2026-08-01
---

# Heimdal Source Capability Matrix

Ikke normativt i sig selv — `HEIMDAL-PRODUCT-SOURCE-OF-TRUTH.md` er den styrende kilde. **Del 1 er opdateret 2026-08-01** mod SoT v2.3 og den afsluttede reconnaissance (`HEIMDAL-COMPANY-DISCOVERY-RECONNAISSANCE.md`); Del 2 (PDF, Fase 2) er uændret. Matricen adskiller to kildekategorier, der begge fodrer den samme canonical entity-model (§3 i SoT), men på forskellige tidspunkter:

- **CURRENT STRUCTURED RELATIONS SOURCE** — `sg.vs.fo`, Fase 1, primær aktiv scope.
- **HISTORICAL PDF SOURCES** — Skráseting Føroya `felag_pdf`/`lysing_pdf`, Fase 2, udsat.

For hvert ønsket produktfelt angives: available in current source, available in PDF source, direct or derived, current or historical, phase, source contract confidence.

Confidence-niveauer for `sg.vs.fo`-kolonnen bruger klassifikationen fra `HEIMDAL-CURRENT-RELATIONS-SOURCE-CONTRACT.md`: `OBSERVED` (set direkte i en levende respons i denne execution), `INFERRED` (rimelig slutning, ikke direkte set), `UNCONFIRMED` (uafklaret), `NOT_AVAILABLE` (bekræftet fravær).

## Del 1 — CURRENT STRUCTURED RELATIONS SOURCE (sg.vs.fo, Fase 1)

| Product field | Available in current source | Direct or derived | Confidence | Notes |
|---|---|---|---|---|
| entity name (person/company) | Ja — `fromName`/`toName` (get-table), `LABEL` (get-graph) | Direct | OBSERVED | Adresse er sammenkædet i samme tekststreng, ikke separat felt |
| entity type | Ja — **primær production extraction er `get-table`s navnetekst** (`GET_TABLE_NAME_TEXT_GREEDY_RIGHTMOST_SEPARATOR_PARSE`); `get-graph COLORLABEL` er **sampled validation** | Direct, kræver parsing | OBSERVED | 588/588 navne parsede med højresplit (venstresplit ødelagde 2). Validering: **62/62 match, 0 konflikter** mod `COLORLABEL`. 22 rå typetokens observeret. Fallback `UNKNOWN_ENTITY`. `get-graph` ikke obligatorisk pr. virksomhed |
| source entity ID | Ja — `pkCbi` / `fromId`/`toId`/`ID` | Direct | OBSERVED | |
| address (som tekst) | Delvist — indlejret i entity-navnetekst | Direct, ustruktureret | OBSERVED | Ikke et separat, struktureret felt |
| relation group (Nevnd/Stjórn/Eigarar/Annað) | Ja — `/api/relations`, `groupId`/`groupName` | Direct | OBSERVED | 4 grupper bekræftet fra levende respons |
| fine-grained relation type | Ja — `cbTypeId`/`cbTypeName` | Direct | OBSERVED | 11 værdier set (1, 21, 22, 29, 30, 32, 34, 36, 37, 41, 81=`O.V.E.`); stadig ikke en komplet taksonomi. Taxonomy-audit hører til relation processing |
| ownership percentage | Ja, som tekst i `cbTypeName` (f.eks. "Eigari 80.0%") | Direct, kræver parsing | OBSERVED | Intet separat numerisk felt observeret |
| beneficial owner (Veruligur Eigari) | Ja — separat `cbTypeId=41`-relation | Direct | OBSERVED | Selvstændig relation, ikke et flag på ejerskabsrelationen |
| voting rights | Ikke observeret | — | UNCONFIRMED | Intet felt set i de undersøgte responser |
| relation direction | Ja — `fromId`→`toId` | Direct | OBSERVED | |
| snapshot / sync freshness | Ja — `/api/get-info` (`update_end`, `sync_start`, `sync_end`, `update_start`) | Direct | OBSERVED | Tidsstempler fra undersøgelsesdagen selv, tyder på hyppig synkronisering — frekvens over tid UNCONFIRMED |
| multi-hop ownership chains | Ja — flertrins ejerkæder observeret i kilden | Derived (skal beregnes lokalt) | OBSERVED (kæder findes), beregning er Fase C | Bekræftet på en reel flertrinsstruktur; selve beregningen er relation processing og ligger uden for den aktuelle acquisition-plan |
| company purpose / signing rules | Nej | — | NOT_AVAILABLE | Kun i PDF-kilden, se Del 2 |
| registered capital | Nej | — | NOT_AVAILABLE | Kun i PDF-kilden |
| name history / hjánøvn | Nej | — | NOT_AVAILABLE | Kun i PDF-kilden |
| lifecycle events (stiftelse/konkurs/likvidation/fusion) | Nej | — | NOT_AVAILABLE | Kun i PDF-kilden — sg.vs.fo viser kun aktuel tilstand |
| historical relation intervals (valid_from/valid_to) | Nej | — | NOT_AVAILABLE | Kun `first_observed_at`/`last_observed_at` kan konstrueres lokalt fra gentagne snapshots |
| company discovery method | Ja — `/api/search-cbi?size={n}&q={term}`, enumereret over registreringsnumre (`SEARCH_CBI_REGISTRATION_NUMBER_ENUMERATION`) | Direct | OBSERVED | **Discovery method identified; denominator not yet executed.** Kildens egen hjælpetekst dokumenterer `S` + registreringsnummer. Finder også isolerede virksomheder uden relationer. Entities set i `get-table` er **gap detection**, ikke primær metode. `size=101` understøttet; `from` ignoreret |
| full entity universe size (denominator) | Metode identificeret, tal ikke etableret | Derived (kræver kørsel) | `denominator_method_identified=true`, `denominator_established=false` | Denominator = antal distinkte registreringsnumre, når enumerationen har nået sin dokumenterede terminalbetingelse. Et completeness-claim (`FULL_POPULATION_CLAIMED`) kræver gennemført enumeration, fetch-status pr. virksomhed og dokumenterede gaps — se SoT §24 |
| traversal / `level` | Ja — `get-table` returnerer en observeret N-hop nabolagskugle omkring start-entity; `level=N` opførte sig som BFS-radius N | Direct | OBSERVED | Målt 18/62/588 entities ved level 1/2/4; ikke start-entity-uafhængigt (588 vs. 317 fra to startpunkter i samme komponent). **Acquisition-standard er `level=1`** (`ACCEPTED_PLANNING_RULE`, ikke bevist komplethed) |
| tomt relationssvar | HTTP 200 `[ ]` for både ikke-eksisterende ID og virksomhed uden relationer | — | OBSERVED | `empty_array_proves_not_found=false`. Inventoryet afgør eksistens; for en inventoried company er `[ ]` et succesfuldt hentet svar med nul relationer |
| rate limits / ToS / source use | Ingen ToS publiceret, robots.txt 404, ingen rate-limit-headers | — | OBSERVED (delvist) + OWNER ATTESTATION | **Full data collection permission is granted by Sole Human Owner attestation** (SoT §9.1); sourcekontakt er ikke en precondition. Source support er midlertidigt utilgængelig (sommerferie). Konservativ operationel adfærd er fortsat påkrævet — hårde stop ved 403/429/gentagne 5xx. Rate-limit-tærskler forbliver UNCONFIRMED |

## Del 2 — HISTORICAL PDF SOURCES (Skráseting Føroya, Fase 2, udsat)

Uændret fra v1.2's matrix (samme evidensgrundlag: 8 eksempel-`felag_pdf`'er + 2026-07-31 data-audit af 18.431 registerposter). Klassifikationsnøgle uændret: `DIRECTLY_SUPPORTED_BY_SUPPLIED_EXAMPLES`, `SUPPORTED_BY_OTHER_IDENTIFIED_SOURCE`, `DERIVABLE_FROM_DIRECT_FACTS`, `DESIRED_BUT_SOURCE_NOT_YET_CONFIRMED`, `NOT_AVAILABLE`.

| Product field | Meaning | Source document type(s) | Classification | Phase | Known limitations |
|---|---|---|---|---|---|
| registration number | Unikt registreringsnummer | felag_pdf | DIRECTLY_SUPPORTED_BY_SUPPLIED_EXAMPLES | 2 | Ingen |
| current company name | Aktuelt navn | felag_pdf | DIRECTLY_SUPPORTED_BY_SUPPLIED_EXAMPLES | 2 | Type indlejret i navnet |
| hjánøvn / aliases | Alternative navne/brands | felag_pdf | DIRECTLY_SUPPORTED_BY_SUPPLIED_EXAMPLES | 2 | Ingen valid_from/valid_to i kilden |
| former names | Tidligere navne | lysing_pdf (RENAMED-events) | SUPPORTED_BY_OTHER_IDENTIFIED_SOURCE | 2 | Ikke modelleret som temporale intervaller i nuværende skema |
| address (historisk) | Postadresse over tid | felag_pdf/lysing_pdf | SUPPORTED_BY_OTHER_IDENTIFIED_SOURCE | 2 | Kun aktuel adresse bekræftet i eksemplerne |
| board members / chair | Nevndarlimur/Nevndarformaður | felag_pdf | DIRECTLY_SUPPORTED_BY_SUPPLIED_EXAMPLES | 2 | Kun nuværende sammensætning i felag_pdf |
| management | Stjóri | felag_pdf | DIRECTLY_SUPPORTED_BY_SUPPLIED_EXAMPLES | 2 | Ingen |
| liquidator | Likvidator | felag_pdf | DIRECTLY_SUPPORTED_BY_SUPPLIED_EXAMPLES | 2 | AUD-B1: 978 uindfangede transitioner i eventkontrakten |
| registered capital | Partapeningur | felag_pdf | DIRECTLY_SUPPORTED_BY_SUPPLIED_EXAMPLES | 2 | Må ikke vises som egenkapital |
| book equity | Regnskabsmæssig egenkapital | ikke identificeret kilde | NOT_AVAILABLE | 2 | AUD-D3: intet struktureret persisteringsmål |
| accounting dates | Roknskapir dato | felag_pdf | DIRECTLY_SUPPORTED_BY_SUPPLIED_EXAMPLES | 2 | Ikke en egenkapitalværdi |
| purpose / signing rules | Endamál/Tekningarreglur | felag_pdf | DIRECTLY_SUPPORTED_BY_SUPPLIED_EXAMPLES | 2 | Fritekst |
| incorporation / registration date | Stiftelses-/registreringsdato | lysing_pdf | SUPPORTED_BY_OTHER_IDENTIFIED_SOURCE | 2 | Ikke bekræftet i de 8 eksempler |
| bankruptcy / liquidation / dissolution date | Livscyklusdatoer | lysing_pdf | SUPPORTED_BY_OTHER_IDENTIFIED_SOURCE | 2 | Ikke bekræftet i de 8 eksempler (undtagen 1 fritekst-tilfælde) |
| merger predecessor/successor | Fusionsdata | lysing_pdf (formodet) | DESIRED_BUT_SOURCE_NOT_YET_CONFIRMED | 2 | Ingen konkret sag observeret |
| voting rights, share class | — | — | NOT_AVAILABLE | 2 | Bekræftet fraværende i eigaraviðurskifti-API'et (den ældre snapshot-baserede variant, adskilt fra det nyligt observerede sg.vs.fo/yvirlit-interface i Del 1) |

**Vigtig note om to forskellige ejerskabskilder:** v1.2's matrix dokumenterede `eigaraviðurskifti` som en batch-snapshot JSON-API, adskilt fra det interaktive `sg.vs.fo/yvirlit/`-interface, der blev observeret direkte via DevTools til v2.0. Begge synes at være samme underliggende datakilde (sg.vs.fo), men muligvis forskellige endpoints eller versioner. **UNCONFIRMED** — og fortsat uafklaret efter reconnaissance, som ikke undersøgte den ældre API. Det blokerer ikke company inventory eller råindsamling og hører til Fase 2; det må ikke antages.

## Population (Fase 2, uændret)

18.431 registerposter totalt — `lysing_pdf` 12.428, `felag_pdf` 5.290, `search_page` 622, `association_detail_json` 91. 17.718 PDF-poster, 18.406 unikke PDF-SHA256, 17.665 parsed, 53 pending.
