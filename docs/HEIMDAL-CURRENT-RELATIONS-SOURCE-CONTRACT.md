---
title: Heimdal Current Relations Source Contract
document_type: SOURCE_CONTRACT_RECONSTRUCTION
status: PROPOSED
official_status: NOT_OFFICIAL_UPSTREAM_DOCUMENTATION
reconnaissance_status: COMPLETED_FOR_PLANNED_ACQUISITION
additional_reconnaissance_required: false
interface_stability: NOT_ASSUMED_STABLE
source_permission: GRANTED
normative_document: HEIMDAL-PRODUCT-SOURCE-OF-TRUTH.md (v2.3, §6, §8, §8a, §9.1, §13, §14a, §14b)
generated_at: 2026-07-31
updated_at: 2026-08-01
---

# Heimdal Current Relations Source Contract

This document is **not official upstream documentation**. `sg.vs.fo` publishes no known public API specification for the endpoints below. It was first reconstructed by observing browser network traffic (Chrome DevTools) against the live, public page `https://sg.vs.fo/yvirlit/` on 2026-07-31, for a single example entity chain (person `pkCbi=22315`, company `pkCbi=22312`).

**Updated 2026-08-01** from the completed bounded reconnaissance (`HEIMDAL-COMPANY-DISCOVERY-RECONNAISSANCE.md`, 21 upstream requests, report `f1abd49d…`), which tested a far larger sample and **falsified one of the original inferences** — see §3.2 and §6. Reconnaissance is now `COMPLETED_FOR_PLANNED_ACQUISITION`: no further general reconnaissance is required before the collector is built.

The interface remains `NOT_OFFICIALLY_DOCUMENTED` and `NOT_ASSUMED_STABLE`. **Source permission (SoT §9.1) does not change this** — permission to collect the data is not a guarantee about the shape of the data, and every raw response must still be stored so the contract can be revalidated locally without new upstream calls.

Every claim below is classified:

- **OBSERVED** — seen directly in a real response during this investigation
- **INFERRED** — a reasonable reading of an observed response, not itself directly confirmed
- **UNCONFIRMED** — genuinely unresolved; requires further reconnaissance
- **NOT_AVAILABLE** — actively checked for and not present

## 1. Observed endpoints

| Endpoint | Method | OBSERVED example | Status |
|---|---|---|---|
| `/api/search-cbi?size={n}&q={term}` | GET | `?size=100&q=Betri` → `[{"r":22,"d":"Betri (P/F S3690 , Kongabrúgvin Postrúm 329)"}, …]` | OBSERVED |
| `/api/relations` | GET | Returns the 4-group taxonomy (below) | OBSERVED |
| `/api/get-cve?id={pkCbi}` | GET | `?id=22315` → `{"r":22315,"d":"[person A] (Persónur, Ognarvegur 9  Skopun Føroyar)"}` | OBSERVED |
| `/api/get-table/{pkCbi}?level={n}&pkCbType={csv}&relations={csv}` | GET | `/api/get-table/22315?level=4&pkCbType=5,4,2,3&relations=5,4,2,3` → flat edge array | OBSERVED |
| `/api/get-graph/{pkCbi}?level={n}&pkCbType={csv}&relations={csv}` | GET | same params → `{"data": "<xml-string>"}` with `<nodes>`/`<links>` | OBSERVED |
| `/api/get-info` | GET | `{"update_end":"2026-07-31T18:07:59Z","sync_start":"2026-07-31T18:00:03Z","sync_end":"2026-07-31T17:07:59Z","update_start":"2026-07-31T18:07:58Z"}` | OBSERVED |
| `/strapi//pages?name=help`, `/strapi//pages?name=contact` | GET | Static CMS content pages, not product data | OBSERVED (irrelevant to acquisition) |

The public HTML page itself, `https://sg.vs.fo/yvirlit/?q={base64}`, is a client-rendered app. Its `q` parameter is a **URL-encoded, then base64-encoded** query string. OBSERVED example:

```
q = "P3JlbGF0aW9ucz01JnJlbGF0aW9ucz00JnJlbGF0aW9ucz0yJnJlbGF0aW9ucz0zJnBrQ2JpPTIyMzE1JmxldmVsPTQ%3D"
→ urldecode → base64-decode → "?relations=5&relations=4&relations=2&relations=3&pkCbi=22315&level=4"
```

The `get-graph` response's node `LINK` attribute reuses this exact same `q=`-encoding scheme for cross-links between entities — OBSERVED.

## 2. Query parameters

| Parameter | OBSERVED values | Meaning | Confidence |
|---|---|---|---|
| `pkCbi` | `22315`, `22312` | The source entity's primary key ("Cbi" — meaning UNCONFIRMED, likely "central business/person identifier") | OBSERVED (values), INFERRED (name meaning) |
| `level` | `1`, `2`, `4` | **BFS radius**: `level=N` returns exactly the entities within N undirected hops of the queried entity | **OBSERVED** (2026-08-01) — measured from one start entity: level 1 → 18 entities, level 2 → 62, level 4 → 588, nested (L1 ⊂ L2 ⊂ L4), and the maximum hop distance present in the level-4 response was exactly 4. The source's own help page calls the field «Stødd á Grannalagi» (neighbourhood size), matching the measurement |
| `pkCbType` | `5,4,2,3` | Identical to `relations` in every observed request | **Explained** (2026-08-01): the source's own client sets `pkCbType` **and** `relations` from a single field, so their always-identical values are a client-side artifact, not two independent filters. Whether the *server* treats them independently is still UNCONFIRMED and untested |
| `relations` | `5,4,2,3` | Filters which of the 4 relation groups to include | OBSERVED as accepting the 4 group IDs; UNCONFIRMED whether omitting a group ID actually excludes it (not tested) |

## 3. Response schemas

### 3.0 `/api/search-cbi` — OBSERVED (added 2026-08-01)

Heimdal's **primary company-discovery endpoint** (SoT §8). Flat array of `{r, d}` objects:

```json
[{"r": 22, "d": "Betri (P/F S3690 , Kongabrúgvin Postrúm 329)"}]
```

- `r` = `pkCbi`, the same source entity ID used by every other endpoint — OBSERVED.
- `d` = display text, pattern `"{name} ({type}[ {registration_number}] , {address})"` — OBSERVED. The registration number is `S`-prefixed here (`S3690`) and `#`-prefixed in `get-table` (`#3690`); confirmed to be the **same number** by direct comparison of the same entity across both endpoints — OBSERVED.
- **`S` + registration number search is documented by the source itself** in its public help page: *"Tað er eisini møguligt at finna eitt ávíst felag við at skriva "S" og síðani skrásetingarnummar."* An exact registration-number query returned exactly one row — OBSERVED.
- Matching is **substring** over the display text (a query matched inside an address token), so results include rows matched on name, type, number or address; dedupe by `r` — OBSERVED.
- **The index covers persons as well as companies** (`Persónur` rows returned). Persons and foreign entities (`Aps`, `A/S`, `AS`, `ASA`, …) carry **no registration number** — OBSERVED.
- Empty `q` returns `[ ]` — there is **no browsable full index** — OBSERVED.
- `size` is server-honored: `size=2` returned 2 rows, `size=101` returned 101. **Values above 101 were not tested** and must not be assumed to work — OBSERVED (≤101), UNCONFIRMED (>101).
- **`from` is ignored**: `size=100&from=100&q=a` returned a byte-identical body to the same query without `from`. There is **no offset pagination** — OBSERVED.
- No total-count field is returned. A denominator therefore exists only as a property of a completed enumeration, not as something the source reports — OBSERVED.

### 3.1 `/api/relations` — OBSERVED, full response captured

```json
[
  {"groupId": 5, "groupName": "Annað"},
  {"groupId": 4, "groupName": "Eigarar"},
  {"groupId": 2, "groupName": "Nevnd"},
  {"groupId": 3, "groupName": "Stjórn"}
]
```

This is almost certainly the **complete** top-level group taxonomy (small, static-looking list) — OBSERVED, but not formally confirmed as exhaustive/stable across time.

### 3.2 `/api/get-table/{pkCbi}` — OBSERVED, full response captured for one entity pair

Flat array of edge objects:

```json
{
  "fromId": 22315,
  "fromName": "[person A] - Persónur, Ognarvegur 9  Skopun Føroyar",
  "cbTypeId": 21,
  "cbTypeName": "Stjóri",
  "toId": 22312,
  "toName": "RN Smíð #9261 - ÍVF, Ognarvegur 9 "
}
```

- `fromName`/`toName`: entity display name, registration number, entity type and address are concatenated as free text — **not** structured fields. Pattern `"{name}[ #{registration_number}] - {type}, {address}"`, **OBSERVED** on 588 of 588 entities (2026-08-01), upgraded from INFERRED. Parse with the rightmost `" - "` — see §12.1. The same entity carried the identical name string in every row of a response (0 of 588 varied).
- `cbTypeId`/`cbTypeName`: fine-grained relation type. OBSERVED values: `21`=Stjóri, `22`=Nevndarformaður, `32`=Nevndarlimur, `1`=Eigari (with the percentage baked directly into `cbTypeName`, e.g. `"Eigari 80.0%"`), `41`=Veruligur Eigari. **No numeric percentage field exists separately** — OBSERVED. The full `cbTypeId` taxonomy beyond these 5 values is UNCONFIRMED.
- Direction: `fromId`→`toId` with `fromName` always the person/owner side and `toName` the company side in the observed sample — INFERRED as the general direction convention, not confirmed for all relation types (e.g. is a company-owns-company edge also `from`=owner, `to`=owned? UNCONFIRMED, not observed in this investigation).
- **Component behavior — the original "symmetric traversal" reading is FALSIFIED (2026-08-01).** The first investigation queried `get-table/22315` (person) and `get-table/22312` (company) — two ends of the same relation — and got **byte-identical** results, which was read as "returns the full connected component regardless of start node." Reconnaissance showed that conclusion held only because that example was a trivial 3-node component, where the `level`-hop ball around *any* member covers the whole component. On a large real component the responses differ: `get-table/22?level=4` returned 1203 edges over 588 entities, `get-table/1176?level=4` returned 506 edges over 317 entities, and the second set is a **strict subset** of the first. The correct model is:

  ```
  get_table_component_behavior = N_HOP_UNDIRECTED_NEIGHBOURHOOD_BALL_AROUND_START_ENTITY
  ```

  `get-table` returns an N-hop ball centred on the queried entity — **not** necessarily a whole connected component, and **not** start-entity-independent. OBSERVED. Only 18 of the 1203 edges in the `pkCbi=22` response touched `pkCbi=22` itself, so a response routinely contains entities that were not known in advance.

### 3.3 `/api/get-graph/{pkCbi}` — OBSERVED, full response captured

```json
{"data": "<data><nodes ID=\"...\" LABEL=\"...\" SIZEVALUE=\"100\" COLORVALUE=\"18\" COLORLABEL=\"ÍVF\" INFOSTRING=\"...\" LINK=\"/yvirlit/?q=...\"/>...<links FROMID=\"...\" TOID=\"...\" LABEL=\"Stjóri,Nevndarformaður,Eigari 80.0%,Veruligur Eigari\" INFOSTRING=\"...\"/></data>"}
```

- The `data` field is an XML-like string embedded inside a JSON string value — OBSERVED, unusual but confirmed exact format.
- `<nodes>`: `ID` (= pkCbi), `LABEL` (display name only), `SIZEVALUE` (OBSERVED always `"100"` in this sample — meaning UNCONFIRMED, possibly a graph-rendering weight), `COLORVALUE` (numeric, OBSERVED `18` for the ÍVF company node and `10` for both person nodes — INFERRED as a type-to-color mapping, exact scheme UNCONFIRMED), `COLORLABEL` (**structured entity type label** — OBSERVED values `"Persónur"` and `"ÍVF"` only; this is the field the Product Source of Truth §3.1/§13 designates as primary entity-type source when available), `INFOSTRING` (same concatenated name/type/address text as `get-table`'s `fromName`/`toName`), `LINK` (deep link back into the same `/yvirlit/?q=...` UI for that node — reuses the base64 query-string scheme).
- `<links>`: `FROMID`, `TOID`, `LABEL` and `INFOSTRING` are **comma-joined combined labels** for ALL relation types between that pair (e.g. `"Stjóri,Nevndarformaður,Eigari 80.0%,Veruligur Eigari"` as one string) — OBSERVED. This is coarser than `get-table`, which returns one row per relation type. **Any collector building canonical relations must use `get-table`, not `get-graph`, as the structured source — `get-graph` is view-layer convenience data that would need re-splitting the combined label string, which is unnecessary re-parsing work when `get-table` already gives it structured.**

### 3.4 `/api/get-cve?id={id}` — OBSERVED

```json
{"r": 22315, "d": "[person A] (Persónur, Ognarvegur 9  Skopun Føroyar)"}
```

A simple id→display-name lookup. `r` echoes the input id; `d` is a formatted display string (name + type + address in parentheses) — same underlying data as `get-table`'s name field, different formatting. Purpose beyond display convenience is UNCONFIRMED.

### 3.5 `/api/get-info` — OBSERVED

```json
{"update_end": "2026-07-31T18:07:59Z", "sync_start": "2026-07-31T18:00:03Z", "sync_end": "2026-07-31T17:07:59Z", "update_start": "2026-07-31T18:07:58Z"}
```

All four timestamps fell on the day of this investigation, within the hour of the request. This is the strongest evidence for the owner's premise that the data is "current, not historical." The **cadence** of these sync cycles (hourly? daily? on-demand?) is UNCONFIRMED — only a single observation was made.

## 4. Direction semantics

INFERRED, not fully confirmed: for a person-owns/manages/sits-on-board-of-company relation, `fromId` is the person and `toId` is the company. Whether this convention holds symmetrically for company-owns-company relations, or whether `Eigarar`-group edges can run the opposite direction in some cases, is **UNCONFIRMED** — not tested in this investigation (the sampled example had only person→company edges).

## 5. Percentage encoding

**OBSERVED, confirmed format**: `cbTypeName: "Eigari {N.N}%"` — a formatted string, not a numeric field. Collector must parse this string; a parse failure must produce `parse_status=FAILED` with the raw label preserved, never a silent zero (Product Source of Truth §14).

## 6. Depth / level semantics — RESOLVED 2026-08-01

```
get_table_level_semantics = LEVEL_N_EQUALS_BFS_RADIUS_N
get_table_default_level   = 1   (planned acquisition standard)
```

Measured against a genuinely multi-tier structure (the Betri group), varying `level` from one start entity: level 1 → 18 entities, level 2 → 62, level 4 → 588; the sets are nested; and BFS distances computed over the level-4 response confirmed that every returned entity lies within exactly N hops, with the maximum distance equal to the requested level. `OBSERVED`.

**Planned acquisition standard is `level=1`** (SoT §8a.6). Level 1 is by construction the queried company's own edges, it returned ~4,252 bytes against ~284,771 for `level=4` on the same entity, and in the measured sample an inventory-driven level-1 pass reached every observed edge — no observed edge had two endpoints lacking a registration number. Classification discipline matters here:

```
the measurement (18/62/588, BFS radius)            = OBSERVED
generalising level=1 to the planned collector      = ACCEPTED_PLANNING_RULE
```

`level=1` is the current standard, **not** a proof of completeness for all future source data. Gap detection (SoT §8) is what falsifies the rule if it stops holding.

## 7. Pagination behavior

**UNCONFIRMED / NOT_AVAILABLE in observed responses.** No pagination parameters or "has more" indicators were seen in any of the 5 endpoints. `get-table`/`get-graph` appear to return the full result for the requested `pkCbi` + `level` in one response. Whether a large enough entity-universe traversal would ever produce a response too large to return in one call, and how the API would behave in that case, is UNCONFIRMED.

## 8. Error behavior — partially RESOLVED 2026-08-01

**An empty response is ambiguous. OBSERVED:** `get-table/99999999?level=1` — a nonexistent `pkCbi` — returned **HTTP 200 with `[ ]`**, byte-identical (same SHA-256) to a legitimate empty relation result. There is no 404 and no error envelope.

Normative consequences (SoT §24):

- HTTP 200 `[ ]` is **not** in itself evidence of `NOT_FOUND`.
- The **company inventory** is the authoritative indicator of whether a company belongs to the population.
- For an inventoried company, HTTP 200 `[ ]` is recorded as a **successfully fetched raw response with zero observed relations**.
- Raw acquisition must never infer entity existence from relation count.

Still **NOT_AVAILABLE / not tested**: malformed `level`/`relations`/`pkCbType` values, and any non-200 status shape. Not blocking — the collector treats any unexpected status or body as `INVALID_RESPONSE`, stores it raw, and flags it.

## 9. Rate-limit observations

**Partial OBSERVED, not a rate-limit characterisation.** `robots.txt` returned HTTP 404 (no restrictions declared) on both investigations. No `X-RateLimit-*`, `Retry-After`, or similar headers were present on any observed response. No 429 or throttling was encountered across 21 bounded reconnaissance requests at ≥3s spacing and concurrency 1 — still far too low a volume to characterise real rate-limit thresholds, which therefore remain `UNCONFIRMED`.

**Source-use is settled by owner attestation, not by this document (2026-08-01).** SoT §9.1 records `permission_status=GRANTED`, `permission_scope=FULL_DATA_COLLECTION`, `source_owner_contact_required_before_collection=false`, `source_support_availability=TEMPORARILY_UNAVAILABLE_DUE_TO_SUMMER_LEAVE`. The absence of published terms of use is not a blocker. The conservative collector behaviour in SoT §9 still applies unchanged — permission is not a reason to collect fast, and with source support temporarily unavailable the 403/429/repeated-5xx stops are hard stops with no automatic restart.

For the record, the source's own public pages state (OBSERVED): all displayed information is public; the system does not store user behaviour; the system is *"under development"* and *"not yet updated regularly"* (help page last edited 2022-08-04); the registrar's contact is `skr@vs.fo`, Skráseting Føroya, Sigmundargøta 13, FO-110 Tórshavn.

## 10. Session / cookie requirements

**RESOLVED 2026-08-01: no session is required.** The server sets a `cookiesession1` cookie (PHP session pattern, consistent with the observed `Apache/2.4.6 (CentOS) … PHP/5.4.16` server header), but all 21 reconnaissance requests were made **without sending any cookie** and every endpoint — `search-cbi`, `get-table`, `get-graph`, `get-info`, `relations`, the CMS pages — returned normal data. No referring page load is needed either. `OBSERVED`.

## 11. Source sync timestamps

See §3.5 — `/api/get-info` is the mechanism. This is the field Heimdal's freshness display (Product Source of Truth §10) should surface as "Upstream synkroniseringstid."

## 12. `get-table` vs `get-graph` overlap

Both return the same underlying edges for the same query. `get-table` is row-per-relation-type (structured, machine-friendly). `get-graph` combines all relation types between a pair into one comma-joined label string (view-optimized for the graph UI) and additionally provides node-level metadata (`COLORLABEL` entity type, `LINK`) not present in `get-table`.

**Superseded by OWNER DECISION `GET_TABLE_PRIMARY_RELATION_SOURCE` (Product Source of Truth §8a.4, §29b):** `get-table` is the primary source for the collector. `get-graph` is **not** a mandatory request per company. The earlier recommendation to always fetch both is withdrawn.

### 12.1 Entity typing from `get-table` — VALIDATED 2026-08-01

```
primary    = GET_TABLE_NAME_TEXT_GREEDY_RIGHTMOST_SEPARATOR_PARSE
validation = GET_GRAPH_COLORLABEL_SAMPLED_ONLY
fallback   = UNKNOWN_ENTITY
```

The name text follows `"{name}[ #{registration_number}] - {type}, {address}"`. **The split must use the rightmost `" - "`**: entity names can themselves contain `" - "`, and a leftmost split corrupted 2 of 588 observed names (e.g. `Búsetur #6479 - Í/F` was mis-read as the type), while a rightmost split parsed **588 of 588**. OBSERVED.

Validation against `get-graph`'s structured `COLORLABEL` for the same entities: **62 compared, 62 matched, 0 conflicts, 0 unknown.** OBSERVED. Both endpoints returned the identical entity set for identical parameters.

Because one `get-graph` call returns many nodes (62 at `level=2`), sampled validation needs **no request per company**. A parse failure or a parsed-vs-`COLORLABEL` conflict yields `UNKNOWN_ENTITY` / `typing_status=CONFLICT` with the raw text preserved verbatim — never a name-based guess. Typing must not block raw acquisition and may run after Fase B.

Raw type tokens observed (22 distinct, not a closed taxonomy): `Persónur`, `Sp/F`, `P/F`, `Aps`, `ÍVF`, `Fakfelag`, `Gr`, `A/S`, `Samognarar`, `Í/F`, `FVAÁ`, `FØ Eind`, `Útlendskir grunnar o.l.`, `Landstýrið`, `AS`, `Natural Person`, `Ognarfelag`, `Spk`, `ASA`, `Ikki vinnurekandi grunnur`, `LVAÁ`, `Áhugafelag`. Note two spellings for persons (`Persónur`, `Natural Person`). Entities **without** a registration number were persons and foreign/non-registered bodies (291 of 588 in the sample).

## 13. Known unknowns (summary)

**Resolved by the 2026-08-01 reconnaissance:**

```
- level semantics on a real multi-tier chain          -> BFS radius N (§6)
- component behavior / start-entity dependence        -> N-hop ball, start-dependent (§3.2)
- error shape for nonexistent entities                -> HTTP 200 [ ], ambiguous (§8)
- pagination behavior                                 -> size honored to 101; from ignored (§3.0)
- cookie/session necessity, referring page load       -> not required (§10)
- pkCbType vs relations                               -> one client field sets both (§2)
- entity typing from get-table                        -> validated 62/62 vs COLORLABEL (§12.1)
- company discovery method                            -> search-cbi S-number enumeration (§3.0)
- content deduplication vs request provenance         -> content dedup by SHA-256 is safe;
                                                         each request keeps its own provenance
                                                         (SoT §8a.6) — no longer blocking Fase B
```

**Still open — none of these block company inventory or raw acquisition:**

```
- full cbTypeId taxonomy (now 11 values seen, incl. 81=O.V.E., 37, 29, 34, 30, 36) — relation processing scope
- full entity-type taxonomy beyond the 22 observed raw tokens
- direction convention for company-to-company edges — relation processing scope, see §4 and §15.4
- true rate-limit thresholds and 429 behavior (21 requests is too few to characterise)
- whether search-cbi accepts size values above 101 (untested; the plan does not depend on it)
- whether any registration-number prefix other than "S" exists (only "S" is documented and observed;
  gap detection would surface it)
- sync/update cadence over time — two same-day observations, both at :00:03, suggest hourly; INFERRED.
  The source's own help page states the data is "not yet updated regularly", which is not reconcilable
  from outside and must not be resolved by assumption
- relationship (if any) between this /yvirlit/ interface and the older eigaraviðurskifti snapshot JSON API
  used by the PDF-era pipeline (Capability Matrix Part 2 note) — Fase 2 scope
```

## 14. Captured example payload hashes

The original 2026-07-31 DevTools observations were read from the network panel and **not** hashed. The 2026-08-01 reconnaissance archived all 21 responses with timestamp and SHA-256 before analysis; the full ledger is in `HEIMDAL-COMPANY-DISCOVERY-RECONNAISSANCE.md` §2. Key payloads:

| Payload | SHA-256 (first 16) |
|---|---|
| `/api/search-cbi?size=100&q=Betri` | `b6a91072f46d6b5e` |
| `/api/get-table/22?level=4&…` (1203 edges, 588 entities) | `068d053a1c0bb203` |
| `/api/get-table/1176?level=4&…` (506 edges, 317 entities) | `c13c02aca366f821` |
| `/api/get-table/22?level=1&…` (18 entities) | `4f0aef08f0801dc2` |
| `/api/get-graph/22?level=2&…` (62 nodes) | `faa514d42b464541` |
| `/api/get-table/99999999?level=1&…` → `[ ]` | `5984eac0c5c6d947` |

The raw-archive discipline (SoT §7.1) applies from the collector's first request onward: every captured payload hashed, every request keeping its own provenance.

## 15. Control registration and From/To direction semantics (v2.1 addition)

This section documents evidence for the Product Source of Truth's §14a (Kontrolregistrering) and §14b (Raw From/To). All examples below are **owner-supplied**, presented directly by Sole Human Owner for this clarification, and are classified `OBSERVED` per that instruction — they are concrete, specific cases, not a claim that every possible upstream relation follows the same pattern.

### 15.1 Direct ownership — From=owner, To=owned entity (`OBSERVED`)

```
Betri        → Eigari 100.0% → Betri Banki
Betri Banki  → Eigari 100.0% → Betri Heim
```

Both rows follow `From/Frá = ejer`, `To/Til = ejet entity`. This is the evidence base for SoT §14b.1's `canonical_direction = OWNER_TO_OWNED` rule, and for the locally-derivable ownership chain Betri → 100% Betri Banki → 100% Betri Heim (100% × 100% = 100% indirect).

### 15.2 Registered control actor — From=registered actor, To=registered entity (`OBSERVED`)

```
From: Aktør A    Relation: Veruligur Eigari    To: Ognarfelag Tryggingartakaranna
```

This is the evidence base for SoT §14b.2's `canonical_direction = REGISTERED_ACTOR_TO_ENTITY` rule. `Ognarfelag Tryggingartakaranna`'s own presented bylaws document a membership/governance structure (policyholders can be members, representatives elected by members, the representative assembly elects the board, the board appoints management) — this is why a `Veruligur Eigari` registration for this specific entity must not be read as ordinary legal or economic ownership without further evidence. This example is the concrete case behind SoT §14a.1's caution, not a general claim about how every `Veruligur Eigari` registration should be read for every entity.

### 15.3 O.V.E. — status, not self-edge (`OBSERVED`)

```
From: Betri Banki    Relation: O.V.E.    To: Betri Banki
```

`From` and `To` are the same entity. Per SoT §14a.2/§14b.3, this must **not** be stored as a canonical relation edge from an entity to itself. It is stored as an entity status: `entity=Betri Banki, status=NO_REGISTERED_CONTROL_ACTOR, raw_source_label="O.V.E."`. If a future observation shows `O.V.E.` with genuinely different From/To entities (not this self-referencing pattern), that is an anomaly outside this evidence base — SoT §14a.2 requires it be flagged `semantic_status=UNRESOLVED_SOURCE_ANOMALY`, not guessed at.

**OBSERVED (2026-08-02, found during an independent audit of the relation-processing layer): this anomaly is real, not just a theoretical case.** 8 rows exist with `cbTypeId=81`/`cbTypeName="O.V.E."` where From ≠ To:

```
Nema #49 (P/F)                        <-> 2 #2 (P/F)
Palli hjá Mariannu #2450 (P/F)        <-> 01.05.2018 #6763 (Sp/F)
Palli hjá Mariannu #2450 (P/F)        <-> Northpelagic #5209 (P/F)
Pelagos #5951 (P/F)                   <-> Pelagos Kollafjørður #3263 (P/F)
TR #6767 (Sp/F)                       <-> 11.04.2017 #6483 (Sp/F)
Varðin #931 (P/F)                     <-> Varðin Invest #6240 (P/F)
Vindfelagið í Neshaga og Porkeri #6387 (P/F) <-> Vindfelagið í Húsahaga #6386 (P/F)
MOWI (ASA, Bergen, Norway)            <-> Mowi Faroes #1296 (P/F)
```

All 8 are stored in `canonical_relation` as `OTHER_SOURCE_RELATION` with `semantic_direction_confidence=UNCONFIRMED` and `semantic_status=UNRESOLVED_SOURCE_ANOMALY` — never guessed, never dropped, never given a direction. This was initially implemented (the rows were correctly kept out of the ENTITY_STATUS table and out of any canonical self-edge) but the `semantic_status` flag itself was missing until this audit; it has since been added to the schema and backfilled onto these 8 rows.

### 15.4 From/To is relation-type-dependent — explicit warning

**From and To are transport fields from upstream. Their semantic meaning must be defined per relation type. There is no universal rule that From is always the person, the owner, or the active party.** §15.1 and §15.2 above assign opposite semantic roles to the `From` field (owner vs. registered actor) even though both happen to place a person/actor in `From` in these particular examples — this is a coincidence of the two sampled cases, not a confirmed general pattern. The direction convention for other relation types (Nevnd/Stjórn roles, `Annað`, and especially company-to-company edges within any of these categories) remains `UNCONFIRMED` (§4) and must not be inferred from §15.1/§15.2.

### 15.5 Direction matrix

| source_relation | raw_from_role | raw_to_role | canonical_subject | canonical_object | canonical_storage | semantic_direction | confidence | allowed_use | forbidden_use |
|---|---|---|---|---|---|---|---|---|---|
| Eigari N% | owner | owned entity | owner | owned_entity | RELATION — `DIRECT_OWNERSHIP` | `OWNER_TO_OWNED` | `OBSERVED_AND_OWNER_ACCEPTED` | ownership %, indirect ownership calculation, ownership paths | presenting as control/board/management |
| Veruligur Eigari | registered actor | registered entity | registered_actor | registered_entity | RELATION — `REGISTERED_CONTROL_ACTOR` | `REGISTERED_ACTOR_TO_ENTITY` | `OBSERVED_FOR_SUPPLIED_EXAMPLES` | display as "Registreret kontrolaktør" with raw source reference | ownership %, indirect ownership calculation, control percentage, board/management role, "ejer"-language |
| O.V.E. | (self) | (self) | affected_entity | NULL | ENTITY_STATUS — `NO_REGISTERED_CONTROL_ACTOR` | `NOT_AN_EDGE` | `OBSERVED_FOR_SUPPLIED_EXAMPLE` | entity status display "Ingen registreret kontrolaktør" | canonical self-edge creation; inferring absence of owners, members, board, or management |
| UNKNOWN_RELATION_TYPE | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNRESOLVED | RAW ONLY | `UNCONFIRMED` | `UNCONFIRMED` | `RAW_DISPLAY_ONLY` | `OWNERSHIP_CALCULATION_OR_DERIVED_RELATIONS` |

This matrix covers only the four rows required by this clarification. It is not a full relation-type taxonomy — `Stjóri`, `Nevndarformaður`, `Nevndarlimur` and other `Annað`-group relations are out of scope here and remain as previously documented (§3, §6 of this document; SoT §12).

**Known internal tension, flagged 2026-08-02 (code-review audit), not yet resolved:** the `Eigari N%` row above gives `OBSERVED_AND_OWNER_ACCEPTED` with no person-vs-company carve-out — the collector therefore applies that same confidence to every `DIRECT_OWNERSHIP` edge, company-to-company included. §15.4 above, however, warns that direction for company-to-company edges specifically "remains `UNCONFIRMED`... must not be inferred". This document does not reconcile the two statements. Live audit (2026-08-02) found no evidence of an actual direction error from this (only 3 companies, out of 4,422 owned, have an explainable >100% direct-ownership sum — see heimdal_collect.py's `report` output), so the stored confidence has been left as-is rather than speculatively downgraded. If a future observation contradicts a company-to-company `Eigari` direction, this is the tension to revisit first.
