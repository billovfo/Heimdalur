#!/usr/bin/env python3
"""Heimdal Phase 1 collector — company inventory + raw get-table acquisition.

Authorized scope (Sole Human Owner, 2026-08-01): PostgreSQL storage, resumable
search-cbi enumeration, resumable get-table level=1 raw acquisition, SHA-256
content deduplication with permanent per-request provenance, gap detection,
sampled entity-type validation, completion report.

Authorized scope (Sole Human Owner, 2026-08-02, canonical relation storage
only): DIRECT_OWNERSHIP / REGISTERED_CONTROL_ACTOR / entity-status extraction,
indirect ownership calculation (SoT §15), INFERRED source-group role
normalization (SoT §18/§19 prerequisite), and person-to-person / company-to-
company relation contexts (SoT §18/§19) — all built from already-stored
get-table snapshots, no new upstream request. See HEIMDAL-PRODUCT-SOURCE-OF-
TRUTH.md §14/§14a/§14b/§15/§18/§19 and the Direction Matrix in
HEIMDAL-CURRENT-RELATIONS-SOURCE-CONTRACT.md §15.5.

NOT authorized and deliberately absent: read API, WebUI, scheduled snapshots,
PDF history, deployment.

Data tiers (audit 2026-11 A-1/A-3). RAW tier — back it up, never rebuild it:
request_attempt and response_content (append-only, trigger-enforced, A-14),
collection_run, company_inventory, enumeration_key, and canonical_entity.
canonical_entity is raw-tier although it is computed: its ids are the portal's
public URL ids and are referenced by watchlists, change events and analytics,
and they cannot be re-derived from raw (a first assignment is deterministic,
ORDER BY source_entity_id, but it will not reproduce ids assigned earlier).
DERIVED tier — rebuildable from raw: registry_ownership.*, entity_status,
input_parse_status. `process-relations --rebuild` reproduces relation_id
exactly (attempt order, one transaction), so pins on relation ids survive.

Commands:
    init                apply schema.sql (pre-cutover DBs only; refuses a post-cutover DB)
    enumerate           sweep the registration-number key space to its terminal condition
    queue               mark registered companies QUEUED, print the denominator
    acquire             fetch get-table level=1 for every QUEUED company
    retry               flip RETRYABLE_ERROR back to QUEUED
    validate-types      sampled get-graph COLORLABEL validation
    parse-inputs        WP-C1: frozen input enumeration + per-body parse status (local only)
    process-relations   canonical relation + entity-status extraction from local snapshots
                        (--rebuild: TRUNCATE … RESTART IDENTITY and re-derive, one transaction)
    compute-ownership   indirect ownership calc from local canonical relations (SoT §15)
    normalize-roles     tag registry_ownership.relation rows with INFERRED source group (SoT §18/§19 prereq)
    build-relation-contexts  person-to-person + company-to-company relation contexts (SoT §18/§19)
    assign-canonical-ids     assign stable canonical entity IDs (SoT §3.1/§3.2, Fase 0 of portal/API)
    taxonomy-audit      WP-C4: full-population relation-type audit across the frozen input set
    backfill-company-locations  classify COMPANY inventory addresses into company_location (idempotent)
    backfill-pdf-company-locations  resumably enrich company_location from catalogued felag_pdf headers
    report              collection completion report incl. gap detection
"""
import argparse
import hashlib
import itertools
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone

import psycopg

COLLECTOR_VERSION = "heimdal-collect/1.0.0"
SOURCE_SYSTEM = "sg.vs.fo"
BASE = "https://sg.vs.fo"
# Required, no default (A-15). Checked in db(), not here: offline tests import
# the pure functions without a database.
DB_URI = os.environ.get("HEIMDAL_DB")

# Operational limits (owner decision 2026-08-01, interval raised twice
# 2026-08-01 by owner request: 3.0 -> 1.5 -> 1.0). Conservative by requirement:
# source support is temporarily unavailable, so every stop is a hard stop.
MIN_SECONDS_BETWEEN_REQUESTS = 1.0
MAX_ATTEMPTS_PER_REQUEST = 2          # retry_number 0 and 1
CONCURRENCY = 1
SEARCH_SIZE = 101                     # the only size value reconnaissance verified
GET_TABLE_LEVEL = 1
RELATION_GROUPS = "5,4,2,3"
HTTP_TIMEOUT = 60
RETRY_BACKOFF_FACTOR = 5              # the one retry waits 5x the request gap first (A-7)
MAX_BODY_BYTES = 16 * 1024 * 1024     # largest stored body is ~10 KB; PDFs stay well below (A-13)
ALLOWED_HOSTS = {"sg.vs.fo", "www.skraseting.fo", "skraseting.fo"}  # A-4: API + felag PDFs
USER_AGENT = ("Heimdal-Collector/1.0 (bounded public-register collection, permitted; "
              "1 req/s, concurrency 1)")  # owner D11 2026-11-06: no private contact address

PERMISSION = {
    "permission_status": "GRANTED",
    "permission_scope": "FULL_DATA_COLLECTION",
    "permission_granted_by": "NOT_RECORDED",
    "permission_granted_at": "NOT_RECORDED",
    "permission_channel": "NOT_RECORDED",
    "owner_attestation_recorded_at": date(2026, 8, 1),
    "source_owner_contact_required_before_collection": False,
    "source_support_availability": "TEMPORARILY_UNAVAILABLE_DUE_TO_SUMMER_LEAVE",
}


class HardStop(Exception):
    """A stop condition fired. The run halts and does not restart itself."""


class EntityFetchFailed(HardStop):
    """This one entity's request exhausted its attempts (5xx / network error).

    Distinct from HardStop's other triggers (403/429/Retry-After/malformed
    body/unexpected shape), which stay unconditionally fatal — those signal an
    access or contract problem, not a single flaky record. Callers that don't
    catch this specifically still get the old fail-the-whole-run behaviour,
    since it subclasses HardStop. `status` is the last HTTP status (None: network).
    """

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# --- parsing -----------------------------------------------------------------
# Two display formats, both free text. Neither is a structured field.

# get-table fromName/toName: "{name}[ #{regnr}] - {type}, {address}"
# Greedy first group takes the RIGHTMOST " - " that still leaves a valid
# "{type-without-comma}, {address}" tail. Entity names can contain " - " —
# a leftmost split corrupted 2 of 588 observed names, greedy parsed 588/588.
_TABLE_NAME = re.compile(r"^(.*) - ([^,]*), (.*)$", re.S)

# search-cbi "d": "{name} ({type}[ {regnr}] , {address})"
_SEARCH_DISPLAY = re.compile(r"^(.*) \((.*)\)$", re.S)
_REGNR_SUFFIX = re.compile(r"\s+(S\d+)$")
_REGNR_IN_NAME = re.compile(r"\s+#(\d+)$")
_HAS_REGNR = re.compile(r"#\d+ - ")

# cbTypeName for cbTypeId=1, e.g. "Eigari 15.68%". cbTypeId=101's "Atkvøðurættur
# N%" also carries a "%" but is a different concept (voting rights, not
# ownership) — dispatch is always on cbTypeId, never on this pattern alone.
_OWNERSHIP_PERCENT = re.compile(r"^Eigari\s+([\d.]+)%$")

# Direction Matrix (HEIMDAL-CURRENT-RELATIONS-SOURCE-CONTRACT.md §15.5).
CBTYPE_DIRECT_OWNERSHIP = 1
CBTYPE_REGISTERED_CONTROL_ACTOR = 41
CBTYPE_OVE = 81

# Source group (Nevnd/Stjórn/Eigarar/Annað) per cbTypeId — INFERRED, never
# OBSERVED (owner decision 2026-08-02): get-table edges carry only cbTypeId/
# cbTypeName, never the source's own groupId (§3.1 vs §3.2, source contract).
# Confirming the real mapping would need new upstream requests (testing
# relations=2/3/4/5 filtering one group at a time), which RELATION_PROCESSING_
# FROM_LOCAL_SNAPSHOTS forbids — so this is a labelled best-guess from
# cbTypeName text, revisable, never treated as certain.
#
# Gendered role pairs collapse to one neutral canonical_role_label (owner
# instruction 2026-08-02): Næstforkvinna -> Næstformaður, Nevndarforkvinna ->
# Nevndarformaður. raw_source_relation_label keeps the original text verbatim
# regardless — only this derived label is neutral.
CBTYPE_GROUP = {
    1:   ("EIGARAR", "Eigari"),
    41:  ("EIGARAR", "Veruligur Eigari"),
    81:  ("EIGARAR", "O.V.E."),
    101: ("EIGARAR", "Atkvøðurættur"),
    22:  ("NEVND", "Nevndarformaður"),
    34:  ("NEVND", "Nevndarformaður"),   # Nevndarforkvinna -> neutral
    37:  ("NEVND", "Næstformaður"),
    30:  ("NEVND", "Næstformaður"),      # Næstforkvinna -> neutral
    32:  ("NEVND", "Nevndarlimur"),
    27:  ("NEVND", "Varalimur"),
    29:  ("NEVND", "Starvsfólkavalt umboð"),
    121: ("NEVND", "Bestyrelsesmedlem"),
    122: ("NEVND", "Bestyrelsesformand"),
    21:  ("STJORN", "Stjóri"),
    24:  ("STJORN", "Daglig leiðsla"),
    36:  ("STJORN", "Forstjóri"),
    23:  ("STJORN", "Filialleiðari"),
    33:  ("ANNAD", "Ábyrgdarhavandi"),
}
GROUP_CONFIDENCE = "INFERRED"          # the only value this column ever takes


def classify_role(cb_type_id):
    """cbTypeId -> (group, gender-neutral label), or (None, None) if unknown."""
    return CBTYPE_GROUP.get(cb_type_id, (None, None))


# Two distinct tokens mark a person in get-table labels — OBSERVED (2026-08-02):
# "Persónur" for Faroese persons, "Natural Person" for foreign ones (e.g.
# "Jane Doe - Natural Person, 2400 København NV, "). Missing the second
# form silently mis-sorted 56 rows (9 DIRECT_OWNERSHIP, 47
# REGISTERED_CONTROL_ACTOR) as company-to-company edges.
PERSON_TYPE_TEXTS = frozenset({"Persónur", "Natural Person"})


def entity_side_type(label):
    """get-table label -> its own type token (a person marker or a company
    type), or None if unparseable. Person-vs-company classification must read
    this, never guess from company_inventory membership: a foreign or
    otherwise unregistered COMPANY is absent from that inventory too (it only
    covers the S-number sweep) and would otherwise be misread as a person.
    """
    parsed = parse_table_name(label)
    return parsed[2] if parsed else None


def is_person_type(type_text):
    return type_text in PERSON_TYPE_TEXTS


# §3.1 entity type — INFERRED from the label's own type token (owner decision
# 2026-08-02, Fase 0 of the portal/API tier). Confirmed exhaustive against the
# live database: 46 distinct tokens across all 9,801 entities in
# registry_ownership.relation, 0 unparseable. Same discipline as CBTYPE_GROUP — a
# labelled best-guess from token text, never claimed as OBSERVED, revisable if
# a new token appears. Faroese company/legal forms plus foreign equivalents
# (Icelandic, Danish, Norwegian, Dutch, German, Baltic, Polish, Spanish, US/UK)
# all map to COMPANY; associations/foundations/political parties to
# ORGANIZATION; government bodies to PUBLIC_AUTHORITY.
ENTITY_KIND_MAP = {
    "Persónur": "PERSON", "Natural Person": "PERSON",
    "Sp/F": "COMPANY", "ÍVF": "COMPANY", "P/F": "COMPANY", "Í/F": "COMPANY",
    "Gr": "COMPANY", "Fil": "COMPANY", "LVAÁ": "COMPANY", "FVAÁ": "COMPANY",
    "Ehf": "COMPANY", "Eft/gr": "COMPANY", "Spk": "COMPANY",
    "Aps": "COMPANY", "A/S": "COMPANY", "AS": "COMPANY", "ASA": "COMPANY",
    "AB": "COMPANY", "Hf.": "COMPANY", "efh.": "COMPANY", "BV": "COMPANY",
    "MB": "COMPANY", "SIA": "COMPANY", "AG": "COMPANY", "Ltd.": "COMPANY",
    "Limited": "COMPANY", "SL": "COMPANY", "sp. z o.o.": "COMPANY",
    "GmbH": "COMPANY", "Inc.": "COMPANY", "UAB": "COMPANY", "Búgv": "COMPANY",
    "Fakfelag": "ORGANIZATION", "Áhugafelag": "ORGANIZATION",
    "Samognarar": "ORGANIZATION", "Ikki vinnurekandi grunnur": "ORGANIZATION",
    "Útlendskir grunnar o.l.": "ORGANIZATION", "Ognarfelag": "ORGANIZATION",
    "Forening": "ORGANIZATION", "Fond": "ORGANIZATION",
    "Politiskur flokkur": "ORGANIZATION", "FØ Eind": "ORGANIZATION",
    "Landstýrið": "PUBLIC_AUTHORITY", "Kommuna": "PUBLIC_AUTHORITY",
    "Kommunalt felag": "PUBLIC_AUTHORITY", "Almennur stovnur": "PUBLIC_AUTHORITY",
}
ENTITY_KIND_CONFIDENCE = "INFERRED"     # the only value this column ever takes


def entity_kind(type_token):
    """Raw type token -> one of PERSON/COMPANY/ORGANIZATION/PUBLIC_AUTHORITY,
    or UNKNOWN_ENTITY if the token has never been observed (§3.4)."""
    return ENTITY_KIND_MAP.get(type_token, "UNKNOWN_ENTITY")


def relation_basis(rel_type_a, group_a, rel_type_b, group_b):
    """SoT §19 relation_basis for two companies sharing one person, given each
    side's canonical_relation_type and inferred_group. Same-category shares get
    their own specific basis; anything else falls back to CROSS_ROLE_PERSON —
    there is no SoT category for e.g. "owner at A, control actor at B".
    """
    if rel_type_a == rel_type_b == "DIRECT_OWNERSHIP":
        return "SHARED_OWNER"
    if rel_type_a == rel_type_b == "REGISTERED_CONTROL_ACTOR":
        return "SHARED_BENEFICIAL_OWNER"
    if group_a == group_b == "NEVND":
        return "SHARED_BOARD_PERSON"
    if group_a == group_b == "STJORN":
        return "SHARED_MANAGEMENT_PERSON"
    if group_a == group_b == "ANNAD":
        return "SHARED_OTHER_RELATION"
    return "CROSS_ROLE_PERSON"

_FOLD = str.maketrans({"ø": "o", "Ø": "o", "ð": "d", "Ð": "d",
                       "æ": "ae", "Æ": "ae", "þ": "th", "Þ": "th"})


def normalize_name(text):
    """Casefold + accent-fold, for local company search."""
    folded = unicodedata.normalize("NFKD", text.translate(_FOLD))
    return "".join(c for c in folded if not unicodedata.combining(c)).casefold().strip()


def parse_table_name(text):
    """get-table name text -> (name, registration_number|None, type_text) or None."""
    m = _TABLE_NAME.match(text)
    if not m:
        return None
    name, type_text, _address = m.group(1).strip(), m.group(2).strip(), m.group(3)
    if not type_text:
        return None
    regnr = None
    rm = _REGNR_IN_NAME.search(name)
    if rm:
        regnr, name = "S" + rm.group(1), name[: rm.start()].strip()
    return name, regnr, type_text


def parse_search_location(text):
    """search-cbi 'd' -> (name, registration_number|None, type_text, address) or None.

    The address tail (everything after the first comma) is retained for geo
    classification; the three-field parse_search_display stays available for
    callers that predate it.
    """
    m = _SEARCH_DISPLAY.match(text)
    if not m:
        return None
    name, inner = m.group(1).strip(), m.group(2)
    type_part, _separator, address = inner.partition(",")
    type_part = type_part.strip()
    if not type_part:
        return None
    regnr = None
    rm = _REGNR_SUFFIX.search(type_part)
    if rm:
        regnr, type_part = rm.group(1), type_part[:rm.start()].strip()
    return (name, regnr, type_part, address.strip()) if type_part else None


def parse_search_display(text):
    """search-cbi 'd' -> (name, registration_number|None, type_text) or None."""
    parsed = parse_search_location(text)
    return parsed[:3] if parsed else None


# Geo classification (task 2026-08-20 company-city-map). A Faroese placement
# is never guessed: only an exactly-one three-digit postcode that exists in
# geo_postcode maps to FAROESE_CITY, and only when it is not bound to a PO/parcel
# box marker (review round 1: a box number is a box number, not a city). Explicit
# foreign-country markers are evaluated first, so a recognized foreign address is
# FOREIGN even when its text happens to contain a valid Faroese postcode token.
# Markers are the only explicit foreign signals — an unrecognized foreign country
# is UNKNOWN, never FOREIGN.
_POSTCODE_3 = re.compile(r"\b\d{3}\b")
# PO/parcel box markers, case-insensitive. The bare English "Box" is included
# alongside the ruling's listed forms because it is the same marker as "PO Box"
# and appears 30x in the source data (e.g. "c/o ... Box 188") — a box number is
# never a postal code.
_PO_BOX_NUMBER = re.compile(r"(?:postrúm|pakkaboks|\bboks\b|po\s*box|\bbox\b)\s*(\d{3})", re.I)
_FOREIGN_MARKERS = {
    "danmark": "DK", "denmark": "DK",
    "norge": "NO", "norway": "NO",
    "sverige": "SE", "sweden": "SE",
    "ísland": "IS", "iceland": "IS",
    "england": "GB", "united kingdom": "GB",
}


def classify_company_location(address, postcodes):
    """Address tail -> {postal_code, country_code, location_status}.

    postcodes is a key set (e.g. all postal_code values from geo_postcode).
    Foreign markers are checked first (FOREIGN); then a placement is claimed
    only when exactly one three-digit postcode remains after PO-box markers
    are stripped and it is a verified reference key (FAROESE_CITY); otherwise
    UNKNOWN.
    """
    text = address or ""
    folded = text.casefold()
    for marker, country in _FOREIGN_MARKERS.items():
        if marker in folded:
            return {"postal_code": None, "country_code": country,
                    "location_status": "FOREIGN"}
    matches = _POSTCODE_3.findall(_PO_BOX_NUMBER.sub("", text))
    if len(matches) == 1 and matches[0] in postcodes:
        return {"postal_code": matches[0], "country_code": "FO",
                "location_status": "FAROESE_CITY"}
    return {"postal_code": None, "country_code": None, "location_status": "UNKNOWN"}


# PDF company-header extraction (task 2026-08-20 company-city-map). Only the
# registered office header between "Adressa:" and the next "Kommuna:" line is
# ever classified — everything after the Kommuna boundary (board member home
# addresses) is deliberately out of scope. Both markers must be present or the
# result is None; no caller may inspect later PDF text.
_FELAG_PDF_ADDRESS = re.compile(r"^Adressa:\s*(.*?)^Kommuna:\s*", re.M | re.S)


def classify_felag_pdf_address(text, postcodes):
    """Company PDF header -> classification dict, or None without both markers."""
    match = _FELAG_PDF_ADDRESS.search(text)
    if not match:
        return None
    return classify_company_location(match.group(1), postcodes)


def extract_felag_pdf_location(pdf_bytes, postcodes):
    """pdf_bytes -> location dict or None. Extracted text never leaves this scope.

    Runs through the bounded extractor (byte, page and time caps; audit D-04).
    Imported here: importing the heimdal package builds the Flask app (D-14).
    """
    from heimdal.pdfhist.extract import TextExtractionError, extract_text
    try:
        text = extract_text(pdf_bytes)
    except TextExtractionError:
        return None
    return classify_felag_pdf_address(text, postcodes)


def classify_edge(edge):
    """One get-table edge -> ("STATUS", entity_status fields) or ("RELATION", registry_ownership.relation fields).

    Direction Matrix (HEIMDAL-CURRENT-RELATIONS-SOURCE-CONTRACT.md §15.5):
    only cbTypeId 1 (Eigari N%) and 41 (Veruligur Eigari) have an OBSERVED
    subject/object direction. A cbTypeId=81 (O.V.E.) self-edge is an entity
    status, not a relation (§14a.2). Everything else — including a cbTypeId=81
    row that is NOT a self-edge, the §14a.2 anomaly — is stored raw with no
    claimed direction: never guessed, never dropped.

    KNOWN DOCUMENTATION TENSION (flagged by code-review audit, 2026-08-02, not
    changed — no live evidence of a wrong direction, only 3 explainable >100%
    cases across 4,422 owned companies): the Direction Matrix's cbTypeId=1 row
    gives OBSERVED_AND_OWNER_ACCEPTED with no person-vs-company carve-out, so
    every DIRECT_OWNERSHIP edge gets that same confidence here — but §15.4 of
    the same contract doc separately warns that direction for company-to-
    company edges specifically "remains UNCONFIRMED... must not be inferred".
    The two statements aren't reconciled in the source doc itself. Revisit the
    confidence model (e.g. a weaker label for the company-to-company subset)
    only if live evidence of a direction error actually turns up — don't
    preemptively downgrade real, already-verified data on a documentation gap
    alone.
    """
    cb_type_id, cb_type_name = edge["cbTypeId"], edge["cbTypeName"]
    from_id, to_id = edge["fromId"], edge["toId"]

    if cb_type_id == CBTYPE_OVE and from_id == to_id:
        return "STATUS", {"source_entity_id": from_id,
                           "canonical_status_type": "NO_REGISTERED_CONTROL_ACTOR",
                           "raw_source_label": cb_type_name}

    fields = {"canonical_relation_type": "OTHER_SOURCE_RELATION",
              "canonical_subject_entity_id": None, "canonical_object_entity_id": None,
              "semantic_direction_confidence": "UNCONFIRMED",
              "raw_percentage_label": None, "parsed_percentage": None,
              "percentage_parse_status": None, "semantic_status": None}

    if cb_type_id == CBTYPE_OVE:
        # §14a.2: O.V.E. with different From/To (checked above for the
        # self-edge case) is an anomaly outside the evidence base — flagged,
        # never guessed at, per HEIMDAL-CURRENT-RELATIONS-SOURCE-CONTRACT.md's
        # own requirement (found missing during independent audit, 2026-08-02).
        fields["semantic_status"] = "UNRESOLVED_SOURCE_ANOMALY"

    if cb_type_id == CBTYPE_DIRECT_OWNERSHIP:
        m = _OWNERSHIP_PERCENT.match(cb_type_name)
        pct = None
        if m:
            try:
                pct = float(m.group(1))          # regex allows e.g. "1.2.3" — float() can still fail
            except ValueError:
                m = None
        fields.update(canonical_relation_type="DIRECT_OWNERSHIP",
                      canonical_subject_entity_id=from_id, canonical_object_entity_id=to_id,
                      semantic_direction_confidence="OBSERVED_AND_OWNER_ACCEPTED",
                      raw_percentage_label=cb_type_name,
                      parsed_percentage=pct,
                      percentage_parse_status="PARSED" if m else "FAILED")
    elif cb_type_id == CBTYPE_REGISTERED_CONTROL_ACTOR:
        fields.update(canonical_relation_type="REGISTERED_CONTROL_ACTOR",
                      canonical_subject_entity_id=from_id, canonical_object_entity_id=to_id,
                      semantic_direction_confidence="OBSERVED_FOR_SUPPLIED_EXAMPLES")

    return "RELATION", fields


# --- indirect ownership (SoT §15) --------------------------------------------
# Recomputed from scratch each run, purely locally, from DIRECT_OWNERSHIP edges
# already in registry_ownership.relation. No upstream request.

# ponytail: sample path list capped per (ancestor, X) pair — the percentage and
# path_count returned are always exact (plain scalar DP, no combinatorial
# storage), only the human-readable example paths are capped. Raise the cap if
# a future dataset needs deeper path listings than this. Deliberate deviation
# from SoT §15/§17.2 "alle bidragende ejerstier" (audit C-10): the UI states the
# truncation; real data peaks at 10 paths, so nothing is truncated today.
PATH_SAMPLE_CAP = 20


def find_cycle_components(edges):
    """edges: iterable of (owner_id, owned_id). Returns a list of frozensets,
    each one either a self-loop (owner==owned, a singleton) or a strongly
    connected component of size > 1. Iterative Tarjan's SCC (no recursion —
    real ownership chains can in principle run deeper than Python's default
    recursion limit).
    """
    adj = {}
    for owner, owned in edges:
        adj.setdefault(owner, []).append(owned)
        adj.setdefault(owned, [])

    index_of, lowlink, on_stack = {}, {}, {}
    stack, counter, components, self_loops = [], [0], [], set()

    for start in adj:
        if start in index_of:
            continue
        work = [(start, iter(adj[start]))]
        index_of[start] = lowlink[start] = counter[0]; counter[0] += 1
        stack.append(start); on_stack[start] = True
        while work:
            v, it = work[-1]
            pushed = False
            for w in it:
                if w == v:
                    self_loops.add(v)
                    continue
                if w not in index_of:
                    index_of[w] = lowlink[w] = counter[0]; counter[0] += 1
                    stack.append(w); on_stack[w] = True
                    work.append((w, iter(adj[w])))
                    pushed = True
                    break
                elif on_stack.get(w):
                    lowlink[v] = min(lowlink[v], index_of[w])
            if not pushed:
                work.pop()
                if work:
                    lowlink[work[-1][0]] = min(lowlink[work[-1][0]], lowlink[v])
                if lowlink[v] == index_of[v]:
                    comp = []
                    while True:
                        w = stack.pop(); on_stack[w] = False
                        comp.append(w)
                        if w == v:
                            break
                    if len(comp) > 1:
                        components.append(frozenset(comp))
    # A node can have BOTH a self-loop AND membership in a larger genuine
    # cycle (found live, 2026-08-02: entity 2537 owns 28.2% of itself AND is
    # mutually cyclic with 2335: 2537->33.3%->2335->48.7%->2537). Adding it as
    # a SECOND, separate singleton component here would give it two different
    # component indices, so cycle_of[2537] would only remember the last one —
    # breaking the same-component check for its real cycle partner and
    # leaving a genuine cyclic edge un-excluded (caught by the DAG assert in
    # compute_indirect_ownership). Only add the singleton when the self-loop
    # is the node's ONLY cyclic membership; a node already in a bigger
    # component has its self-loop edge excluded via that component instead
    # (same_cycle(n, n) is trivially true for any n present in cycle_of).
    covered = {n for comp in components for n in comp}
    components.extend(frozenset([n]) for n in self_loops if n not in covered)
    return components


def compute_indirect_ownership(direct_edges, cycle_of):
    """direct_edges: iterable of (owner_id, owned_id, percentage).
    cycle_of: dict mapping entity_id -> its cycle-component index, for every
    entity that sits in a self-loop or cross-ownership cycle (build via
    `{n: i for i, comp in enumerate(find_cycle_components(...)) for n in comp}`).

    Bug found 2026-08-02 (independently, by two audit passes): an earlier
    version excluded an entity ENTIRELY — both sides, all its edges — the
    moment it appeared in any cycle. SoT §15 only withholds the indirect
    percentage for the cyclic relationship itself ("ingen indirekte procent
    vises" at cycle, but "de direkte relationer vises" otherwise); it says
    nothing about an unrelated, non-cyclic edge that a cycle member happens to
    also have (e.g. a company with a 22% self-holding that also owns 17% of a
    completely different, non-cyclic subsidiary). An edge is now excluded only
    when BOTH endpoints are in the SAME cycle component — i.e. the edge is
    itself part of that cycle — never merely because one endpoint is a cycle
    member somewhere else in the graph.

    Returns a list of dicts: owner_entity_id, owned_entity_id,
    computed_percentage (exact), path_count (exact, indirect paths only),
    contributing_paths (capped sample, each length >= 2 hops), paths_truncated.
    """
    def same_cycle(o, w):
        return o in cycle_of and w in cycle_of and cycle_of[o] == cycle_of[w]

    direct = [(o, w, p) for o, w, p in direct_edges if not same_cycle(o, w)]
    direct_pct = {(o, w): p for o, w, p in direct}

    incoming = {}
    for owner, owned, pct in direct:
        incoming.setdefault(owned, []).append((owner, pct))

    nodes = {n for o, w, _ in direct for n in (o, w)}
    indeg = {n: 0 for n in nodes}
    for owner, owned, _ in direct:
        indeg[owned] += 1
    topo, frontier = [], [n for n in nodes if indeg[n] == 0]
    outgoing = {}
    for owner, owned, _ in direct:
        outgoing.setdefault(owner, []).append(owned)
    while frontier:
        n = frontier.pop()
        topo.append(n)
        for m in outgoing.get(n, []):
            indeg[m] -= 1
            if indeg[m] == 0:
                frontier.append(m)
    assert len(topo) == len(nodes), "same-cycle-component exclusion should leave a pure DAG"

    # contribution[X][A] = {"pct": exact float, "count": exact int, "paths": capped sample}
    contribution = {}
    for x in topo:
        entry = contribution.setdefault(x, {})
        for y, pct_edge in incoming.get(x, []):
            d = entry.setdefault(y, {"pct": 0.0, "count": 0, "paths": []})
            d["pct"] += pct_edge
            d["count"] += 1
            if len(d["paths"]) < PATH_SAMPLE_CAP:
                d["paths"].append(((y, x), pct_edge))
            for a, src in contribution.get(y, {}).items():
                dst = entry.setdefault(a, {"pct": 0.0, "count": 0, "paths": []})
                dst["pct"] += src["pct"] * pct_edge / 100.0
                dst["count"] += src["count"]
                if len(dst["paths"]) < PATH_SAMPLE_CAP:
                    for path, p in src["paths"]:
                        if len(dst["paths"]) >= PATH_SAMPLE_CAP:
                            break
                        dst["paths"].append((path + (x,), p * pct_edge / 100.0))

    results = []
    for x, ancestors in contribution.items():
        for a, entry in ancestors.items():
            if same_cycle(a, x):
                continue    # belt-and-suspenders: no indirect % between cycle-mates, any route
            d_pct = direct_pct.get((a, x), 0.0)
            d_exists = (a, x) in direct_pct
            indirect_count = entry["count"] - (1 if d_exists else 0)
            if indirect_count <= 0:
                continue
            sample = [(p, v) for p, v in entry["paths"] if len(p) > 2]
            results.append({
                "owner_entity_id": a, "owned_entity_id": x,
                "computed_percentage": entry["pct"] - d_pct,
                "path_count": indirect_count,
                "contributing_paths": [{"path": list(p), "percentage": v} for p, v in sample],
                "paths_truncated": indirect_count > len(sample),
            })
    return results


# --- response shape validation ----------------------------------------------
# An unexpected response shape is a hard stop (owner decision). The raw body is
# always stored and hashed first, so the offending payload survives the stop.

def _is_row_array(payload, keys):
    return isinstance(payload, list) and all(
        isinstance(r, dict) and keys <= r.keys() for r in payload)


GET_TABLE_KEYS = {"fromId", "fromName", "cbTypeId", "cbTypeName", "toId", "toName"}


def _is_get_table(payload):
    """The one get-table key rule (A-2): an array of objects with exactly the
    six known keys. Acquisition (SHAPES) and every reader of stored bodies
    (validate_get_table_body) apply this same rule."""
    return _is_row_array(payload, GET_TABLE_KEYS) and all(
        r.keys() == GET_TABLE_KEYS for r in payload)


def validate_get_table_body(raw):
    """Validate one stored get-table body's bytes. Returns (status, reason, rows).

    WP-C1 parse harness: OK requires valid JSON, a JSON array, and every
    element an object with exactly the six known keys. Anything else is a
    per-body FAILED status with its reason — a malformed body is a finding,
    never a stop. The raw bytes are never modified.
    """
    if not raw or not raw.strip():
        return "FAILED", "EMPTY_BODY", None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "FAILED", "NON_JSON", None
    if not isinstance(payload, list):
        return "FAILED", "NOT_ARRAY", None
    if not _is_get_table(payload):
        return "FAILED", "BAD_ROW_KEYS", None
    return "OK", None, len(payload)


SHAPES = {
    "search-cbi": lambda p: _is_row_array(p, {"r", "d"}),
    "get-table": _is_get_table,
    "get-graph": lambda p: isinstance(p, dict) and isinstance(p.get("data"), str),
}


# --- HTTP --------------------------------------------------------------------

_last_request_at = 0.0
_consecutive_5xx = 0


def _sleep_for_rate_limit():
    global _last_request_at
    gap = time.monotonic() - _last_request_at
    if gap < MIN_SECONDS_BETWEEN_REQUESTS:
        time.sleep(MIN_SECONDS_BETWEEN_REQUESTS - gap)
    _last_request_at = time.monotonic()


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """A-13: follow a redirect only within the request's own host. An off-host
    redirect comes back as an HTTPError (3xx), which fetch() hard-stops on."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            raise urllib.error.HTTPError(newurl, code, f"off-host redirect refused ({msg})",
                                         headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_capped(resp):
    """At most MAX_BODY_BYTES + 1 bytes (A-13). A declared Content-Length within
    the cap is read in full, so a short body still raises IncompleteRead (A-5)."""
    length = getattr(resp, "length", None)
    if length is not None and length <= MAX_BODY_BYTES:
        return resp.read()
    return resp.read(MAX_BODY_BYTES + 1)


def _http_get(url):
    """One raw request, unpaced: only fetch() calls it, after the rate-limit wait.

    Returns (status, body, headers, error_category, error_text). headers stays
    an HTTPMessage, so .get() is case-insensitive (A-7). A non-HTTP error
    (network, timeout, TLS, a failed or short body read) has no body: body is
    None and the text goes to error_text, never into response_content (A-5).
    Reads are capped at MAX_BODY_BYTES (A-13).
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                status, headers, category = r.status, r.headers, "OK"
                body = _read_capped(r)
        except urllib.error.HTTPError as e:
            status, headers = e.code, e.headers
            category = "HTTP_5XX" if e.code >= 500 else "HTTP_4XX"
            body = _read_capped(e)
    except Exception as e:                                  # network, timeout, TLS, read
        return None, None, {}, type(e).__name__.upper(), str(e) or repr(e)
    if len(body) > MAX_BODY_BYTES:
        return status, None, headers, "BODY_TOO_LARGE", f"body over {MAX_BODY_BYTES} bytes"
    return status, body, headers, category, None


def fetch(conn, run_id, endpoint, path, params, entity_id=None, store_body=True):
    """Perform one bounded request with provenance. Returns (attempt_id, sha, payload).

    Raw bytes and the attempt row are committed BEFORE the payload is validated
    or used, so nothing is ever processed that was not durably stored first.
    path is relative to BASE or an absolute URL; either way its host must be in
    ALLOWED_HOSTS (A-4). endpoint "felag-pdf" returns the raw bytes unparsed;
    with store_body=False the body is hashed but not stored (attempt sha NULL)
    and stop messages name the host only, never the URL.
    """
    global _consecutive_5xx
    url = urllib.parse.urljoin(BASE, path)
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise HardStop(f"refusing a request outside the host allowlist: {parts.hostname}")
    shown = url if store_body else f"{endpoint} at {parts.hostname}"

    status = None
    for retry_number in range(MAX_ATTEMPTS_PER_REQUEST):
        if retry_number:
            time.sleep(RETRY_BACKOFF_FACTOR * MIN_SECONDS_BETWEEN_REQUESTS)  # A-7 backoff
        _sleep_for_rate_limit()
        requested_at = datetime.now(timezone.utc)           # A-10: after the wait
        status, body, headers, error_category, error_text = _http_get(url)
        received_at = datetime.now(timezone.utc)
        sha = hashlib.sha256(body).hexdigest() if body is not None else None
        stored_sha = sha if store_body else None

        if stored_sha is not None:
            conn.execute(
                "INSERT INTO response_content (response_sha256, response_bytes, content_type) "
                "VALUES (%s, %s, %s) ON CONFLICT (response_sha256) DO NOTHING",
                (sha, body, headers.get("Content-Type")))
        attempt_id = conn.execute(
            "INSERT INTO request_attempt (collection_run_id, endpoint, parameters,"
            " requested_source_entity_id, requested_at, response_received_at, http_status,"
            " error_category, retry_number, collector_version, response_sha256, error_text)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING attempt_id",
            (run_id, endpoint, json.dumps(params), entity_id, requested_at, received_at,
             status, error_category, retry_number, COLLECTOR_VERSION, stored_sha,
             error_text)).fetchone()[0]
        conn.commit()                                       # stored before processed

        retry_after = headers.get("Retry-After")
        if status in (403, 429):
            raise HardStop(f"HTTP {status} on {shown}"
                           + (f" (Retry-After: {retry_after})" if retry_after else ""))
        if retry_after:
            raise HardStop(f"Retry-After: {retry_after} on {shown}")
        if error_category == "BODY_TOO_LARGE":
            raise HardStop(f"{error_text} (HTTP {status}) on {shown}")

        if status == 200:
            _consecutive_5xx = 0
            if endpoint == "felag-pdf":
                return attempt_id, sha, body                # the caller classifies the bytes
            try:
                payload = json.loads(body)
            except ValueError as e:
                raise HardStop(f"unparseable body from {shown} (sha {sha}): {e}")
            if not SHAPES[endpoint](payload):
                raise HardStop(f"unexpected response shape from {shown} (sha {sha})")
            return attempt_id, sha, payload

        if error_category == "HTTP_5XX":
            _consecutive_5xx += 1
            if _consecutive_5xx >= 2:
                raise EntityFetchFailed(f"repeated 5xx (last: HTTP {status} on {shown})", status)
        elif status is not None:
            raise HardStop(f"unexpected HTTP {status} on {shown}")
        # network error or first 5xx: fall through to the one permitted retry

    raise EntityFetchFailed(f"{MAX_ATTEMPTS_PER_REQUEST} attempts exhausted on {shown}", status)


def _reset_5xx_counter():
    """Give the next entity a fresh attempt budget after a caught EntityFetchFailed."""
    global _consecutive_5xx
    _consecutive_5xx = 0


# --- run bookkeeping ---------------------------------------------------------

def db():
    if not DB_URI:
        sys.exit("HEIMDAL_DB is not set: export HEIMDAL_DB=postgresql://... for the target"
                 " database (there is no default, so a forgotten variable never writes to live heimdal)")
    return psycopg.connect(DB_URI)


# concurrency=1 is an operational requirement, not a convention. The rate limiter
# is per-process, so two collectors would each wait 3s independently and halve the
# real interval. A session-level advisory lock makes single-writer an invariant.
COLLECTOR_LOCK = 0x4845_494D          # "HEIM"


def claim_collector_lock(conn):
    if not conn.execute("SELECT pg_try_advisory_lock(%s)", (COLLECTOR_LOCK,)).fetchone()[0]:
        sys.exit("another collector is already running against this database "
                 "(concurrency=1); wait for it to finish or stop it first")


def start_run(conn, run_type, extra_config=None):
    config = {"min_seconds_between_requests": MIN_SECONDS_BETWEEN_REQUESTS,
              "concurrency": CONCURRENCY,
              "max_attempts_per_request": MAX_ATTEMPTS_PER_REQUEST,
              "search_size": SEARCH_SIZE, "get_table_level": GET_TABLE_LEVEL,
              "relation_groups": RELATION_GROUPS, **(extra_config or {})}
    run_id = conn.execute(
        "INSERT INTO collection_run (run_type, collector_version, config,"
        " permission_status, permission_scope, permission_granted_by, permission_granted_at,"
        " permission_channel, owner_attestation_recorded_at,"
        " source_owner_contact_required_before_collection, source_support_availability)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING collection_run_id",
        (run_type, COLLECTOR_VERSION, json.dumps(config), PERMISSION["permission_status"],
         PERMISSION["permission_scope"], PERMISSION["permission_granted_by"],
         PERMISSION["permission_granted_at"], PERMISSION["permission_channel"],
         PERMISSION["owner_attestation_recorded_at"],
         PERMISSION["source_owner_contact_required_before_collection"],
         PERMISSION["source_support_availability"])).fetchone()[0]
    conn.commit()
    return run_id


def end_run(conn, run_id, status, reason=None):
    conn.execute("UPDATE collection_run SET run_status=%s, stop_reason=%s, ended_at=now()"
                 " WHERE collection_run_id=%s", (status, reason, run_id))
    conn.commit()


def run_guard(conn, run_id, body):
    """Run body; record COMPLETED or STOPPED. A hard stop exits non-zero.

    Any other exit (a crash, A-8/C-6) rolls back the body's uncommitted work,
    records STOPPED with the error, and re-raises, so no run stays RUNNING.
    """
    try:
        body()
    except HardStop as e:
        end_run(conn, run_id, "STOPPED", str(e))
        print(f"\nHARD STOP: {e}\nRun {run_id} recorded as STOPPED. Restarting is a human action.",
              file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        conn.rollback()
        end_run(conn, run_id, "STOPPED", "interrupted by operator")
        print(f"\nInterrupted. Run {run_id} STOPPED; rerun to resume.", file=sys.stderr)
        sys.exit(130)
    except BaseException as e:
        conn.rollback()
        end_run(conn, run_id, "STOPPED", f"crashed: {type(e).__name__}: {e}")
        raise
    end_run(conn, run_id, "COMPLETED")


# --- commands ----------------------------------------------------------------

def cmd_init(_args):
    sql = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")).read()
    with db() as conn:
        try:
            conn.execute(sql)
        except psycopg.errors.RaiseException as e:  # schema.sql's post-cutover guard (A-6)
            sys.exit(f"init refused: {e.diag.message_primary}. A post-cutover DB is migrated"
                     " with `python3 -m heimdal.canonical_migrations --apply` (audit B-17)")
        conn.commit()
        tables = [r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1").fetchall()]
    print("schema applied. tables:", ", ".join(tables))


def _upsert_inventory(conn, run_id, sha, rows):
    """Store discovered entities. Re-discovery only refreshes last_confirmed_at."""
    new = 0
    for row in rows:
        parsed = parse_search_display(row["d"])
        if parsed:
            name, regnr, type_text = parsed
            typing_status = "PARSED"
        else:
            name, regnr, type_text, typing_status = row["d"], None, None, "UNKNOWN_ENTITY"
        inserted = conn.execute(
            "INSERT INTO company_inventory (source_system, source_entity_id, registration_number,"
            " raw_display_name, normalized_search_name, raw_entity_type_text, parsed_entity_type,"
            " typing_status, discovery_method, inventory_run_id, raw_response_reference)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (source_system, source_entity_id)"
            " DO UPDATE SET last_confirmed_at = now()"
            " RETURNING (xmax = 0) AS is_new",
            (SOURCE_SYSTEM, row["r"], regnr, row["d"], normalize_name(name), type_text,
             type_text, typing_status, "SEARCH_CBI_REGISTRATION_NUMBER_ENUMERATION",
             run_id, sha)).fetchone()[0]
        new += bool(inserted)
        if entity_kind(type_text) == "COMPANY":
            _upsert_company_location(conn, SOURCE_SYSTEM, row["r"], row["d"])
    return new


_UPSERT_COMPANY_LOCATION = """
INSERT INTO company_location
    (source_system, source_entity_id, postal_code, country_code, location_status)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (source_system, source_entity_id) DO UPDATE SET
    postal_code = EXCLUDED.postal_code,
    country_code = EXCLUDED.country_code,
    location_status = EXCLUDED.location_status,
    updated_at = now()
"""


def _upsert_company_location(conn, source_system, source_entity_id, raw_display_name,
                             postcodes=None):
    """Classify one inventory display label and upsert its company_location row.

    Returns the location_status it wrote. The caller gates on entity_kind ==
    COMPANY, so person/organization/authority labels never reach this table.
    postcodes is the geo_postcode key set; when omitted it is loaded once here.
    """
    if postcodes is None:
        postcodes = {row[0] for row in conn.execute("SELECT postal_code FROM geo_postcode")}
    parsed = parse_search_location(raw_display_name)
    address = parsed[3] if parsed else ""
    fields = classify_company_location(address, postcodes)
    conn.execute(_UPSERT_COMPANY_LOCATION, (source_system, source_entity_id,
                                            fields["postal_code"], fields["country_code"],
                                            fields["location_status"]))
    return fields["location_status"]


def cmd_enumerate(args):
    """Sweep the registration-number key space, one exact lookup per number.

    search-cbi's S-form is an exact registration-number lookup: q=S2 returns the
    company registered as S2, not S2730. Prefix or substring descent therefore
    cannot enumerate anything, and a worklist built that way reports a terminal
    condition it never actually reached.
    """
    with db() as conn:
        existing = conn.execute("SELECT coalesce(max(key_number), 0)"
                                " FROM enumeration_key").fetchone()[0]
        if args.max > existing:
            conn.execute("INSERT INTO enumeration_key (search_key, key_number)"
                         " SELECT 'S' || g, g FROM generate_series(%s::int, %s::int) g"
                         " ON CONFLICT (search_key) DO NOTHING", (existing + 1, args.max))
            conn.commit()
            print(f"worklist seeded/extended to S{args.max}")

        todo = conn.execute("SELECT count(*) FROM enumeration_key"
                            " WHERE status='PENDING'").fetchone()[0]
        claim_collector_lock(conn)
        run_id = start_run(conn, "INVENTORY",
                           {"method": "SEARCH_CBI_EXACT_REGISTRATION_NUMBER_SWEEP",
                            "max_key_number": args.max})
        eta = todo * MIN_SECONDS_BETWEEN_REQUESTS / 3600
        print(f"inventory run {run_id} — {todo} keys pending, "
              f"{MIN_SECONDS_BETWEEN_REQUESTS}s apart (~{eta:.1f}h)")

        def body():
            done = 0
            while True:
                row = conn.execute(
                    "SELECT search_key FROM enumeration_key WHERE status='PENDING'"
                    " ORDER BY key_number LIMIT 1").fetchone()
                if row is None:
                    break
                key = row[0]
                attempt_id, sha, rows = fetch(conn, run_id, "search-cbi", "/api/search-cbi",
                                              {"size": SEARCH_SIZE, "q": key})
                new = _upsert_inventory(conn, run_id, sha, rows)
                conn.execute("UPDATE enumeration_key SET status=%s, row_count=%s,"
                             " attempt_id=%s, updated_at=now() WHERE search_key=%s",
                             ("HIT" if rows else "MISS", len(rows), attempt_id, key))
                conn.commit()
                done += 1
                if rows or done % 100 == 0:
                    total = conn.execute("SELECT count(*) FROM company_inventory").fetchone()[0]
                    print(f"  {key:<9} {'HIT ' if rows else 'MISS'} +{new}"
                          f"  inventory={total}  {done}/{todo}")

        run_guard(conn, run_id, body)
        _print_sweep_summary(conn)


def cmd_backfill_company_locations(_args):
    """Idempotently classify every COMPANY inventory label into company_location.

    Re-runs safe: each row upserts on (source_system, source_entity_id). Prints
    the three status totals. Persons/organizations/authorities are excluded by
    the canonical_entity entity_kind='COMPANY' join.
    """
    counts = {"FAROESE_CITY": 0, "FOREIGN": 0, "UNKNOWN": 0}
    with db() as conn:
        postcodes = {row[0] for row in conn.execute("SELECT postal_code FROM geo_postcode")}
        rows = conn.execute(
            "SELECT ci.source_entity_id, ci.raw_display_name"
            " FROM company_inventory ci"
            " JOIN canonical_entity ce"
            "   ON ce.source_system = ci.source_system"
            "  AND ce.source_entity_id = ci.source_entity_id"
            " WHERE ce.entity_kind = 'COMPANY'"
            " ORDER BY ci.source_entity_id").fetchall()
        for source_entity_id, raw_display_name in rows:
            status = _upsert_company_location(conn, SOURCE_SYSTEM, source_entity_id,
                                              raw_display_name, postcodes=postcodes)
            counts[status] += 1
        conn.commit()
    for status, n in counts.items():
        print(f"{status}: {n}")


# --- PDF location backfill (task 2026-08-20 company-city-map, Task 3) ---------
#
# Resumable enrichment of company_location from catalogued Skráseting Føroya
# felag_pdf headers. Source-system-aware: candidates join company_inventory to
# the canonical entity (entity_kind='COMPANY') and to source.documents by the
# stripped registration number, so a person's PDF can never be applied to a
# company. Deterministic one-document choice: DISTINCT ON per entity, highest
# document id wins. Transient-only: PDF bytes are hashed, classified in memory,
# and never stored or logged; only the private provenance table keeps the
# outcome. Output is aggregate counts only — no URL, hash, address, or PDF text.

_PDF_LOCATION_CANDIDATES = """
SELECT DISTINCT ON (ci.source_system, ci.source_entity_id)
       ci.source_system, ci.source_entity_id, d.id, d.source_url
FROM company_inventory ci
JOIN canonical_entity ce
  ON ce.source_system = ci.source_system AND ce.source_entity_id = ci.source_entity_id
JOIN source.documents d
  ON lower(d.registration_no) = lower(regexp_replace(ci.registration_number, '^S', '', 'i'))
LEFT JOIN company_location_pdf_fetch pf
  ON pf.source_system = ci.source_system AND pf.source_entity_id = ci.source_entity_id
WHERE ce.entity_kind = 'COMPANY'
  AND d.document_kind = 'felag_pdf'
  AND d.parse_status = 'parsed'
  AND (pf.outcome IS NULL OR pf.outcome = 'FAILED')
ORDER BY ci.source_system, ci.source_entity_id, d.id DESC
"""


def select_pdf_location_candidates(conn):
    """(source_system, source_entity_id, source_document_id, source_url) rows for
    every COMPANY with a catalogued, parsed felag_pdf that has not yet been
    APPLIED/NO_LOCATION. FAILED rows are re-eligible (resumable)."""
    return conn.execute(_PDF_LOCATION_CANDIDATES).fetchall()


def apply_pdf_location_result(conn, source_system, source_entity_id, fields):
    """Write one PDF classification to company_location, or leave it untouched.

    Only FAROESE_CITY/FOREIGN replace an existing placement; an UNKNOWN result
    (or a missing header) retains the current row. Returns APPLIED/NO_LOCATION.
    """
    if fields and fields.get("location_status") in ("FAROESE_CITY", "FOREIGN"):
        conn.execute(_UPSERT_COMPANY_LOCATION, (source_system, source_entity_id,
                                                fields.get("postal_code"),
                                                fields.get("country_code"),
                                                fields.get("location_status")))
        return "APPLIED"
    return "NO_LOCATION"


_UPSERT_PDF_FETCH = """
INSERT INTO company_location_pdf_fetch
    (source_system, source_entity_id, source_document_id, source_url,
     response_sha256, outcome, http_status, failure_kind, attempt_count)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (source_system, source_entity_id) DO UPDATE SET
    source_document_id = EXCLUDED.source_document_id,
    source_url = EXCLUDED.source_url,
    response_sha256 = EXCLUDED.response_sha256,
    outcome = EXCLUDED.outcome,
    http_status = EXCLUDED.http_status,
    failure_kind = EXCLUDED.failure_kind,
    attempt_count = EXCLUDED.attempt_count,
    fetched_at = now()
"""


def _record_pdf_fetch(conn, source_system, source_entity_id, document_id, url,
                      outcome, sha=None, http_status=None, failure_kind=None, attempt_count=1):
    conn.execute(_UPSERT_PDF_FETCH, (source_system, source_entity_id, document_id, url,
                                     sha, outcome, http_status, failure_kind, attempt_count))


def _process_pdf_candidate(conn, run_id, postcodes, source_system, source_entity_id,
                           document_id, url):
    """Fetch one felag_pdf through fetch(), classify its header, and record the
    outcome. Returns APPLIED/NO_LOCATION/FAILED.

    fetch() brings the collector's safeguards (A-4): pacing and backoff, the
    host allowlist, the size cap, a request_attempt row per attempt, and hard
    stops on 403/429/Retry-After/any other unexpected status. The PDF bytes are
    hashed, never stored (store_body=False). When both attempts fail (5xx or
    network), the FAILED outcome is recorded and EntityFetchFailed re-raised
    for the caller's consecutive-failure stop. A FAILED outcome carries a
    failure_kind and no SHA (schema-enforced).
    """
    try:
        attempt_id, sha, body = fetch(conn, run_id, "felag-pdf", url, None,
                                      entity_id=source_entity_id, store_body=False)
    except EntityFetchFailed as e:
        kind = "HTTP_5XX" if (e.status or 0) >= 500 else "NETWORK"
        _record_pdf_fetch(conn, source_system, source_entity_id, document_id, url,
                          outcome="FAILED", http_status=e.status, failure_kind=kind,
                          attempt_count=MAX_ATTEMPTS_PER_REQUEST)
        raise
    attempt_count = conn.execute("SELECT retry_number + 1 FROM request_attempt"
                                 " WHERE attempt_id=%s", (attempt_id,)).fetchone()[0]
    fields = extract_felag_pdf_location(body, postcodes)
    if fields is None:
        _record_pdf_fetch(conn, source_system, source_entity_id, document_id, url,
                          outcome="FAILED", http_status=200,
                          failure_kind="INVALID_PDF", attempt_count=attempt_count)
        return "FAILED"
    outcome = apply_pdf_location_result(conn, source_system, source_entity_id, fields)
    _record_pdf_fetch(conn, source_system, source_entity_id, document_id, url,
                      outcome=outcome, sha=sha, http_status=200,
                      attempt_count=attempt_count)
    return outcome


def print_pdf_location_report(counts):
    for key in ("APPLIED", "NO_LOCATION", "FAILED", "UNMATCHED"):
        print(f"{key}: {counts.get(key, 0)}")


_UNMATCHED_PDF_COMPANIES = """
SELECT count(*) FROM canonical_entity ce
WHERE ce.entity_kind = 'COMPANY'
  AND NOT EXISTS (
    SELECT 1 FROM company_inventory ci
    JOIN source.documents d
      ON lower(d.registration_no) = lower(regexp_replace(ci.registration_number, '^S', '', 'i'))
    WHERE ci.source_system = ce.source_system
      AND ci.source_entity_id = ce.source_entity_id
      AND d.document_kind = 'felag_pdf' AND d.parse_status = 'parsed'
  )
"""


def cmd_backfill_pdf_company_locations(args):
    """Resumably fetch each COMPANY's catalogued felag_pdf and enrich company_location.

    Concurrency, SIGINT, and hard-stop behaviour match the other collector runs
    (claim_collector_lock / start_run / run_guard). Each outcome commits before
    the next candidate. Output is the four aggregate counts only.
    """
    with db() as conn:
        claim_collector_lock(conn)
        postcodes = {row[0] for row in conn.execute("SELECT postal_code FROM geo_postcode")}
        candidates = select_pdf_location_candidates(conn)
        run_id = start_run(conn, "PDF_LOCATION_ENRICHMENT",
                           {"method": "SKRASETING_FELAG_PDF_HEADER_CLASSIFICATION",
                            "limit": args.limit, "candidates": len(candidates)})

        def body():
            done = consecutive_failures = 0
            for source_system, source_entity_id, document_id, url in candidates:
                if args.limit and done >= args.limit:
                    break
                try:
                    _process_pdf_candidate(conn, run_id, postcodes, source_system,
                                           source_entity_id, document_id, url)
                    consecutive_failures = 0
                except EntityFetchFailed as e:   # same stop rule as acquire
                    conn.commit()                # the FAILED outcome
                    _reset_5xx_counter()
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        raise HardStop(f"3 different PDFs failed in a row (last: {e})")
                conn.commit()
                done += 1

        run_guard(conn, run_id, body)

        counts = {"APPLIED": 0, "NO_LOCATION": 0, "FAILED": 0, "UNMATCHED": 0}
        for outcome, n in conn.execute(
                "SELECT outcome, count(*) FROM company_location_pdf_fetch GROUP BY outcome"):
            counts[outcome] = n
        counts["UNMATCHED"] = conn.execute(_UNMATCHED_PDF_COMPANIES).fetchone()[0]
    print_pdf_location_report(counts)


# A sweep is only exhausted if the ceiling sits well above the highest number
# found. Otherwise "no PENDING keys" means "I finished the range I happened to
# seed", which is not the same thing and must never be read as completeness.
TRAILING_MISS_THRESHOLD = 100


def sweep_state(conn):
    """(pending, highest_hit, ceiling, trailing_misses, exhausted)"""
    pending, top, ceiling = conn.execute(
        "SELECT count(*) FILTER (WHERE status='PENDING'),"
        " max(key_number) FILTER (WHERE status='HIT'),"
        " max(key_number) FILTER (WHERE status <> 'PENDING') FROM enumeration_key").fetchone()
    trailing = (ceiling - top) if (ceiling is not None and top is not None) else 0
    return pending, top, ceiling, trailing, (
        pending == 0 and top is not None and trailing >= TRAILING_MISS_THRESHOLD)


def _print_sweep_summary(conn):
    hits, misses, pending, top = conn.execute(
        "SELECT count(*) FILTER (WHERE status='HIT'), count(*) FILTER (WHERE status='MISS'),"
        " count(*) FILTER (WHERE status='PENDING'), max(key_number) FILTER (WHERE status='HIT')"
        " FROM enumeration_key").fetchone()
    ceiling = conn.execute("SELECT max(key_number) FROM enumeration_key"
                           " WHERE status <> 'PENDING'").fetchone()[0]
    print(f"\nkey space probed: {hits} HIT, {misses} MISS, {pending} PENDING")
    if top is not None:
        print(f"highest registration number found: S{top}")
        print(f"trailing consecutive misses above it: {(ceiling or top) - top}"
              "   (a short run here means the sweep ceiling may be too low)")
    _print_denominator(conn)
    print(f"terminal condition {'REACHED' if pending == 0 else 'NOT reached'}:"
          f" {pending} PENDING keys left")


def _print_denominator(conn):
    total, registered = conn.execute(
        "SELECT count(*), count(registration_number) FROM company_inventory").fetchone()
    print(f"\ninventory entities: {total}   with registration number (denominator): {registered}")
    return registered


def cmd_queue(_args):
    with db() as conn:
        n = conn.execute("UPDATE company_inventory SET inventory_status='QUEUED'"
                         " WHERE registration_number IS NOT NULL"
                         " AND inventory_status='DISCOVERED'").rowcount
        conn.commit()
        denominator = _print_denominator(conn)
        print(f"queued {n} companies")

        pending, top, ceiling, trailing, exhausted = sweep_state(conn)
        if exhausted:
            print("collection_scope = FULL_POPULATION_CANDIDATE"
                  "  (FULL_POPULATION_CLAIMED is an owner decision)")
        else:
            print("collection_scope = PARTIAL_POPULATION")
            if pending:
                print(f"  {pending} keys still PENDING")
            elif top is not None and trailing < TRAILING_MISS_THRESHOLD:
                print(f"  sweep ceiling S{ceiling} is only {trailing} above the highest number"
                      f" found (S{top}); {TRAILING_MISS_THRESHOLD} trailing misses required"
                      " before the key space counts as exhausted")
        return denominator


def cmd_acquire(args):
    with db() as conn:
        claim_collector_lock(conn)
        run_id = start_run(conn, "ACQUISITION", {"endpoint": "get-table"})
        todo = conn.execute("SELECT count(*) FROM company_inventory"
                            " WHERE inventory_status='QUEUED'").fetchone()[0]
        limit = args.limit or todo
        print(f"acquisition run {run_id} — {todo} queued, fetching {min(limit, todo)}"
              f" at level={GET_TABLE_LEVEL}")

        def body():
            done = 0
            consecutive_entity_failures = 0
            while done < limit:
                row = conn.execute(
                    "SELECT source_entity_id FROM company_inventory WHERE inventory_status='QUEUED'"
                    " ORDER BY source_entity_id LIMIT 1").fetchone()
                if row is None:
                    break
                entity_id = row[0]
                try:
                    _attempt_id, _sha, edges = fetch(
                        conn, run_id, "get-table", f"/api/get-table/{entity_id}",
                        {"level": GET_TABLE_LEVEL, "pkCbType": RELATION_GROUPS,
                         "relations": RELATION_GROUPS}, entity_id=entity_id)
                except EntityFetchFailed as e:
                    # One entity's own request failed out. Register it for manual
                    # follow-up (`retry` requeues RETRYABLE_ERROR) and move on — but
                    # several DIFFERENT entities failing back to back is the source
                    # itself in distress, not one bad record, so that still hard-stops.
                    conn.execute("UPDATE company_inventory SET inventory_status='RETRYABLE_ERROR'"
                                 " WHERE source_system=%s AND source_entity_id=%s",
                                 (SOURCE_SYSTEM, entity_id))
                    conn.commit()
                    _reset_5xx_counter()
                    consecutive_entity_failures += 1
                    print(f"  {entity_id}  REGISTERED (RETRYABLE_ERROR): {e}")
                    if consecutive_entity_failures >= 3:
                        raise HardStop(f"3 different entities failed in a row"
                                       f" (last: {entity_id}, {e})")
                    continue
                consecutive_entity_failures = 0
                # An empty array is a successful fetch with zero observed relations.
                # It is never NOT_FOUND: a nonexistent id returns the same body, and
                # inventory membership — not relation count — establishes existence.
                conn.execute("UPDATE company_inventory SET inventory_status='FETCHED'"
                             " WHERE source_system=%s AND source_entity_id=%s",
                             (SOURCE_SYSTEM, entity_id))
                conn.commit()
                done += 1
                if done % 25 == 0 or done == limit:
                    print(f"  {done}/{min(limit, todo)}  last={entity_id} edges={len(edges)}")

        run_guard(conn, run_id, body)
        _print_status_counts(conn)


def cmd_retry(_args):
    with db() as conn:
        n = conn.execute("UPDATE company_inventory SET inventory_status='QUEUED'"
                         " WHERE inventory_status='RETRYABLE_ERROR'").rowcount
        conn.commit()
        print(f"requeued {n} companies")


def _print_status_counts(conn):
    print("\ninventory status:")
    for status, n in conn.execute(
            "SELECT inventory_status, count(*) FROM company_inventory"
            " GROUP BY 1 ORDER BY 2 DESC").fetchall():
        print(f"  {status:<16} {n}")


_NODE = re.compile(r"<nodes ([^>]*)/>")
_ATTR = re.compile(r'(\w+)="(.*?)"')


def cmd_validate_types(args):
    """Sampled COLORLABEL validation. One get-graph call validates many entities."""
    with db() as conn:
        # one company per distinct raw type token, rarest first
        sample = conn.execute(
            "SELECT DISTINCT ON (raw_entity_type_text) source_entity_id, raw_entity_type_text"
            " FROM company_inventory WHERE inventory_status='FETCHED'"
            " ORDER BY raw_entity_type_text, source_entity_id").fetchall()[: args.sample]
        if not sample:
            print("nothing FETCHED yet — run acquire first")
            return
        claim_collector_lock(conn)
        run_id = start_run(conn, "TYPE_VALIDATION", {"sample_size": len(sample)})
        stats = {"compared": 0, "matched": 0, "conflicts": 0, "unknown": 0}
        conflicts = []

        def body():
            for entity_id, _type_text in sample:
                _a, _s, graph = fetch(conn, run_id, "get-graph", f"/api/get-graph/{entity_id}",
                                      {"level": GET_TABLE_LEVEL, "pkCbType": RELATION_GROUPS,
                                       "relations": RELATION_GROUPS}, entity_id=entity_id)
                colorlabel = {}
                for node in _NODE.findall(graph["data"]):
                    attrs = dict(_ATTR.findall(node))
                    if "ID" in attrs and "COLORLABEL" in attrs:
                        colorlabel[int(attrs["ID"])] = attrs["COLORLABEL"]

                body_bytes = conn.execute(
                    "SELECT rc.response_bytes FROM request_attempt ra"
                    " JOIN response_content rc USING (response_sha256)"
                    " WHERE ra.endpoint='get-table' AND ra.requested_source_entity_id=%s"
                    " AND ra.http_status=200 ORDER BY ra.attempt_id DESC LIMIT 1",
                    (entity_id,)).fetchone()
                if not body_bytes:
                    continue
                for edge in json.loads(bytes(body_bytes[0])):
                    for side in ("from", "to"):
                        eid, text = edge[side + "Id"], edge[side + "Name"]
                        if eid not in colorlabel:
                            continue
                        parsed = parse_table_name(text)
                        stats["compared"] += 1
                        if parsed is None:
                            stats["unknown"] += 1
                        elif parsed[2] == colorlabel[eid]:
                            stats["matched"] += 1
                        else:
                            stats["conflicts"] += 1
                            conflicts.append((eid, parsed[2], colorlabel[eid]))
                            # bug found 2026-08-02 (code-review audit): a detected
                            # conflict was only ever printed, never persisted —
                            # schema.sql's typing_status CHECK declares 'CONFLICT'
                            # but nothing wrote it. No-ops for eid values outside
                            # company_inventory (e.g. a person node in the graph).
                            conn.execute(
                                "UPDATE company_inventory SET typing_status='CONFLICT'"
                                " WHERE source_system=%s AND source_entity_id=%s",
                                (SOURCE_SYSTEM, eid))

        run_guard(conn, run_id, body)
        print(f"\nget-graph requests: {len(sample)}")
        print(f"compared={stats['compared']} matched={stats['matched']}"
              f" conflicts={stats['conflicts']} unknown={stats['unknown']}")
        for eid, parsed, label in conflicts[:10]:
            print(f"  CONFLICT id={eid} get-table={parsed!r} COLORLABEL={label!r}")


# The frozen input set (WP-C1, A-2): the LATEST get-table HTTP-200 attempt of
# each FETCHED company, so a re-acquired entity contributes one body, not two.
# One definition for parse-inputs, process-relations, taxonomy-audit and report.
# Alias ra: attempt_id, response_sha256, source_entity_id.
INPUT_SET_SQL = (
    " FROM (SELECT DISTINCT ON (ra.requested_source_entity_id)"
    " ra.attempt_id, ra.response_sha256, ra.requested_source_entity_id AS source_entity_id"
    " FROM request_attempt ra"
    " JOIN company_inventory ci ON ci.source_entity_id = ra.requested_source_entity_id"
    " WHERE ra.endpoint='get-table' AND ra.http_status=200"
    " AND ci.inventory_status='FETCHED'"
    " ORDER BY ra.requested_source_entity_id, ra.attempt_id DESC) ra")
# The input set with its bodies: (attempt_id, sha, source_entity_id, bytes).
INPUT_BODIES_SQL = (
    "SELECT ra.attempt_id, ra.response_sha256, ra.source_entity_id, rc.response_bytes"
    + INPUT_SET_SQL + " JOIN response_content rc ON rc.response_sha256 = ra.response_sha256")


def input_edges(attempt_id, sha, entity_id, raw, skipped):
    """A body's edges under the one key rule (validate_get_table_body, A-2).
    A body that is not OK yields no edges and is itemised in `skipped`."""
    status, reason, _rows = validate_get_table_body(bytes(raw))
    if status != "OK":
        skipped.append((attempt_id, sha, entity_id, reason))
        return []
    return json.loads(bytes(raw))


def print_skipped_bodies(skipped):
    for attempt_id, sha, entity_id, reason in skipped:
        print(f"  SKIPPED {reason:<12} attempt={attempt_id} entity={entity_id} body={sha}")
    print(f"input bodies skipped (not OK under the get-table key rule): {len(skipped)}")


def input_set_rows(conn):
    """The frozen input set: one row per FETCHED company's get-table body."""
    return conn.execute(
        "SELECT ra.response_sha256, ra.source_entity_id" + INPUT_SET_SQL
        + " ORDER BY ra.source_entity_id").fetchall()


def input_failures(conn):
    """Itemized FAILED bodies, scoped to the frozen input set: only input-set
    companies may appear, even when a body's sha is shared by other attempts."""
    return conn.execute(
        "SELECT ips.response_sha256, ips.failure_reason, ips.company_count,"
        " array_agg(ra.source_entity_id ORDER BY ra.source_entity_id)"
        + INPUT_SET_SQL +
        " JOIN input_parse_status ips ON ips.response_sha256 = ra.response_sha256"
        " WHERE ips.parse_status='FAILED'"
        " GROUP BY 1,2,3 ORDER BY ips.response_sha256").fetchall()


def parse_inputs(conn):
    """WP-C1: recompute INPUT_PARSE_STATUS from local snapshots. Returns a
    dict report; pure local reads + writes, no upstream request, no lock.
    """
    conn.execute("DELETE FROM input_parse_status")
    input_rows = conn.execute(INPUT_BODIES_SQL + " ORDER BY ra.source_entity_id").fetchall()
    bodies = {}
    for _attempt_id, sha, eid, raw in input_rows:
        bodies.setdefault(sha, [raw, 0, []])[1] += 1
        bodies[sha][2].append(eid)
    for sha, (raw, n, companies) in sorted(bodies.items()):
        status, reason, rows = validate_get_table_body(bytes(raw))
        conn.execute(
            "INSERT INTO input_parse_status (response_sha256, parse_status,"
            " failure_reason, row_count, company_count)"
            " VALUES (%s,%s,%s,%s,%s)",
            (sha, status, reason, rows, n))
    conn.commit()
    return {"input_rows": len(input_rows), "distinct_bodies": len(bodies),
            "fingerprint": fingerprint_input_set(bodies)}


def fingerprint_input_set(bodies):
    """Deterministic fingerprint of the frozen input set: the sorted (sha,
    company_count) list, including each body's company membership."""
    lines = [f"{sha} {len(v[2])} {','.join(map(str, v[2]))}"
             for sha, v in sorted(bodies.items())]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def cmd_parse_inputs(_args):
    """WP-C1 frozen input enumeration + per-body parse status (local data only)."""
    with db() as conn:
        report = parse_inputs(conn)
        rows = conn.execute(
            "SELECT parse_status, COUNT(*), SUM(company_count) FROM input_parse_status"
            " GROUP BY 1").fetchall()
        failures = input_failures(conn)
    print(f"frozen input set: {report['input_rows']} input rows"
          f" ({report['distinct_bodies']} distinct bodies)")
    print(f"fingerprint: {report['fingerprint']}")
    for status, n, companies in rows:
        print(f"  {status:<6} {n} bodies / {companies} companies")
    for sha, reason, n, companies in failures:
        print(f"  FAILED {reason:<12} body={sha} companies={companies} (n={n})")
    print(f"failures itemized: {len(failures)}")


def cmd_process_relations(args):
    """Canonical relation + entity-status extraction from local get-table snapshots.

    Reads-only against RESPONSE_CONTENT/REQUEST_ATTEMPT — no upstream request.
    Scoped to the frozen input set (WP-C1, INPUT_SET_SQL): the latest get-table
    HTTP-200 attempt of each FETCHED company; a body that fails the get-table
    key rule is skipped and itemised (A-2). Attempts are read in attempt_id
    order and everything runs in ONE transaction, so relation_id does not
    depend on the query plan or on a crash (A-1/C-9). The evidence pointer is
    written in the same transaction (A-9).
    Idempotent: ON CONFLICT DO NOTHING dedups an edge seen from both
    endpoints' get-table responses, so a re-run just no-ops on what's stored.
    --rebuild starts the transaction with TRUNCATE … RESTART IDENTITY CASCADE
    (which also empties the two context tables: run compute-ownership and
    build-relation-contexts next); a killed rebuild rolls back to the old rows.
    """
    with db() as conn:
        claim_collector_lock(conn)
        run_id = start_run(conn, "RELATION_PROCESSING", {"rebuild": args.rebuild})
        stats = {"edges": 0, "DIRECT_OWNERSHIP": 0, "REGISTERED_CONTROL_ACTOR": 0,
                 "OTHER_SOURCE_RELATION": 0, "entity_status": 0,
                 "ove_non_self_edge_anomaly": 0, "percentage_parse_failed": 0,
                 "evidence_pointers": 0}
        skipped = []

        def body():
            if args.rebuild:  # inside the transaction: the identity restart rolls back too
                conn.execute("TRUNCATE registry_ownership.relation, entity_status"
                             " RESTART IDENTITY CASCADE")
            attempts = conn.execute(INPUT_BODIES_SQL + " ORDER BY ra.attempt_id").fetchall()
            for attempt_id, sha, entity_id, raw in attempts:
                for edge in input_edges(attempt_id, sha, entity_id, raw, skipped):
                    stats["edges"] += 1
                    kind, fields = classify_edge(edge)
                    if kind == "STATUS":
                        conn.execute(
                            "INSERT INTO entity_status (source_entity_id,"
                            " canonical_status_type, raw_source_label, source_attempt_id)"
                            " VALUES (%s,%s,%s,%s)"
                            " ON CONFLICT (source_entity_id, canonical_status_type) DO NOTHING",
                            (fields["source_entity_id"], fields["canonical_status_type"],
                             fields["raw_source_label"], attempt_id))
                        stats["entity_status"] += 1
                        continue
                    if edge["cbTypeId"] == CBTYPE_OVE:
                        stats["ove_non_self_edge_anomaly"] += 1
                    stats[fields["canonical_relation_type"]] += 1
                    if fields["percentage_parse_status"] == "FAILED":
                        stats["percentage_parse_failed"] += 1
                    conn.execute(
                        "INSERT INTO registry_ownership.relation (raw_from_source_entity_id,"
                        " raw_from_source_entity_label, raw_to_source_entity_id,"
                        " raw_to_source_entity_label, raw_source_relation_type_id,"
                        " raw_source_relation_label, canonical_relation_type,"
                        " canonical_subject_entity_id, canonical_object_entity_id,"
                        " semantic_direction_confidence, raw_percentage_label,"
                        " parsed_percentage, percentage_parse_status, semantic_status,"
                        " source_attempt_id)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                        " ON CONFLICT (raw_from_source_entity_id, raw_to_source_entity_id,"
                        " raw_source_relation_type_id) DO NOTHING",
                        (edge["fromId"], edge["fromName"], edge["toId"], edge["toName"],
                         edge["cbTypeId"], edge["cbTypeName"], fields["canonical_relation_type"],
                         fields["canonical_subject_entity_id"], fields["canonical_object_entity_id"],
                         fields["semantic_direction_confidence"], fields["raw_percentage_label"],
                         fields["parsed_percentage"], fields["percentage_parse_status"],
                         fields["semantic_status"], attempt_id))
            stats["evidence_pointers"] = conn.execute(
                "INSERT INTO registry_ownership.relation_evidence_pointer"
                " (relation_id, source_attempt_id, response_sha256, collection_run_id)"
                " SELECT r.relation_id, r.source_attempt_id, ra.response_sha256, ra.collection_run_id"
                " FROM registry_ownership.relation r"
                " JOIN request_attempt ra ON ra.attempt_id = r.source_attempt_id"
                " ON CONFLICT (relation_id) DO NOTHING").rowcount
            conn.commit()

        run_guard(conn, run_id, body)
        print_skipped_bodies(skipped)
        print(f"evidence pointers written: {stats['evidence_pointers']}")
        if args.rebuild:
            print("rebuild: context tables emptied; run compute-ownership and"
                  " build-relation-contexts next")
        print(f"\nedges scanned: {stats['edges']}")
        print(f"  DIRECT_OWNERSHIP          {stats['DIRECT_OWNERSHIP']}"
              f"  (percentage parse failed: {stats['percentage_parse_failed']})")
        print(f"  REGISTERED_CONTROL_ACTOR  {stats['REGISTERED_CONTROL_ACTOR']}")
        print(f"  OTHER_SOURCE_RELATION     {stats['OTHER_SOURCE_RELATION']}"
              f"  (incl. {stats['ove_non_self_edge_anomaly']} O.V.E. non-self-edge anomalies, §14a.2)")
        print(f"  entity_status NO_REGISTERED_CONTROL_ACTOR  {stats['entity_status']}")


def cmd_compute_ownership(_args):
    """Indirect ownership (SoT §15), recomputed from scratch from local
    DIRECT_OWNERSHIP canonical relations. No upstream request.
    """
    with db() as conn:
        claim_collector_lock(conn)
        run_id = start_run(conn, "INDIRECT_OWNERSHIP")

        def body():
            rows = conn.execute(
                "SELECT canonical_subject_entity_id, canonical_object_entity_id, parsed_percentage,"
                " relation_id, raw_percentage_label"
                " FROM registry_ownership.relation WHERE canonical_relation_type='DIRECT_OWNERSHIP'"
                " ORDER BY canonical_subject_entity_id, canonical_object_entity_id").fetchall()
            # C-6: an unparsed percentage is never 0 %: the edge is left out and itemised.
            direct_edges = [(o, w, float(p)) for o, w, p, _rid, _label in rows if p is not None]
            for o, w, _p, rid, label in (r for r in rows if r[2] is None):
                print(f"  SKIPPED relation {rid} ({o} -> {w}): percentage not parsed {label!r}")
            print(f"direct ownership edges skipped (no parsed percentage): {len(rows) - len(direct_edges)}")

            components = find_cycle_components((o, w) for o, w, _ in direct_edges)
            cycle_of = {n: i for i, comp in enumerate(components) for n in comp}
            results = compute_indirect_ownership(direct_edges, cycle_of)

            conn.execute("TRUNCATE registry_ownership.indirect_ownership, registry_ownership.ownership_cycle_member")
            for i, comp in enumerate(components):
                for entity_id in comp:
                    conn.execute(
                        "INSERT INTO registry_ownership.ownership_cycle_member (cycle_group_id, entity_id)"
                        " VALUES (%s,%s) ON CONFLICT DO NOTHING", (i, entity_id))
            for r in results:
                conn.execute(
                    "INSERT INTO registry_ownership.indirect_ownership (owner_entity_id, owned_entity_id,"
                    " computed_percentage, path_count, contributing_paths, paths_truncated)"
                    " VALUES (%s,%s,%s,%s,%s,%s)",
                    (r["owner_entity_id"], r["owned_entity_id"], r["computed_percentage"],
                     r["path_count"], json.dumps(r["contributing_paths"]), r["paths_truncated"]))
            conn.commit()

            print(f"\ndirect ownership edges (input):  {len(direct_edges)}")
            print(f"cycle components:                {len(components)}"
                  f"  ({sum(1 for c in components if len(c) == 1)} self-loops,"
                  f" {sum(1 for c in components if len(c) > 1)} multi-entity cycles)")
            print(f"entities in a cycle component:   {len(cycle_of)}")
            print(f"indirect ownership rows:         {len(results)}")
            print(f"  truncated path samples:        {sum(1 for r in results if r['paths_truncated'])}")

        run_guard(conn, run_id, body)


def _tag_roles(conn):
    """The actual UPDATE loop behind `normalize-roles` — factored out so
    `build-relation-contexts` can call it unconditionally as its own first
    step (bug found 2026-08-02: nothing previously enforced running
    `normalize-roles` before `build-relation-contexts`, and skipping it failed
    SILENTLY — every group-based relation_basis check just fell through to the
    CROSS_ROLE_PERSON default with no error). Idempotent, cheap (a handful of
    UPDATEs keyed on cbTypeId), safe to call every time. Only rows whose tags
    differ are written (audit C-7): a re-run touches nothing in the canonical
    layer. Returns the number of rows changed.
    """
    tagged = 0
    for cb_type_id, (group, label) in CBTYPE_GROUP.items():
        cur = conn.execute(
            "UPDATE registry_ownership.relation SET inferred_group=%s,"
            " inferred_group_confidence=%s, canonical_role_label=%s"
            " WHERE raw_source_relation_type_id=%s"
            " AND (inferred_group, inferred_group_confidence, canonical_role_label)"
            " IS DISTINCT FROM (%s, %s, %s)",
            (group, GROUP_CONFIDENCE, label, cb_type_id, group, GROUP_CONFIDENCE, label))
        tagged += cur.rowcount
    return tagged


def cmd_normalize_roles(_args):
    """Tag every registry_ownership.relation row with an INFERRED source group + gender-
    neutral role label (SoT §18/§19 prerequisite). Derived entirely from
    raw_source_relation_type_id already stored — no upstream request. Idempotent
    plain UPDATE by cbTypeId, safe to rerun any time (e.g. after CBTYPE_GROUP
    gains a newly-observed cbTypeId).
    """
    with db() as conn:
        claim_collector_lock(conn)
        run_id = start_run(conn, "ROLE_NORMALIZATION")
        stats = {"tagged": 0}

        def body():
            stats["tagged"] = _tag_roles(conn)
            conn.commit()

        run_guard(conn, run_id, body)
        unmapped = conn.execute(
            "SELECT count(*) FROM registry_ownership.relation WHERE inferred_group IS NULL").fetchone()[0]
        print(f"\ntagged {stats['tagged']} rows across {len(CBTYPE_GROUP)} known cbTypeIds"
              f"  (confidence: {GROUP_CONFIDENCE})")
        print(f"still unmapped (unknown cbTypeId, none observed so far): {unmapped}")


def _person_company_edges(conn):
    """Every registry_ownership.relation row with exactly one person side (per
    entity_side_type) — yields (person_id, company_id, relation_id,
    canonical_relation_type, inferred_group, canonical_role_label,
    parsed_percentage). Skips company-company rows (handled separately for
    §19) and anything unparseable (never guessed).
    """
    rows = conn.execute(
        "SELECT relation_id, raw_from_source_entity_id, raw_from_source_entity_label,"
        " raw_to_source_entity_id, raw_to_source_entity_label, canonical_relation_type,"
        " inferred_group, canonical_role_label, parsed_percentage FROM registry_ownership.relation").fetchall()
    out = []
    for (rid, from_id, from_label, to_id, to_label, rel_type, group, label, pct) in rows:
        from_type, to_type = entity_side_type(from_label), entity_side_type(to_label)
        from_is_person, to_is_person = is_person_type(from_type), is_person_type(to_type)
        if from_is_person and not to_is_person and to_type is not None:
            out.append((from_id, to_id, rid, rel_type, group, label, pct))
        elif to_is_person and not from_is_person and from_type is not None:
            out.append((to_id, from_id, rid, rel_type, group, label, pct))
    return out


def _company_company_edges(conn, canonical_relation_type):
    """DIRECT_OWNERSHIP or REGISTERED_CONTROL_ACTOR rows where neither side is
    a person and the two sides differ (excludes self-edges — §14 sum-check
    note for ownership, and the analogous company-is-own-control-actor case) —
    yields (subject_company_id, object_company_id, relation_id, parsed_percentage).
    """
    rows = conn.execute(
        "SELECT relation_id, raw_from_source_entity_id, raw_from_source_entity_label,"
        " raw_to_source_entity_id, raw_to_source_entity_label, parsed_percentage"
        " FROM registry_ownership.relation WHERE canonical_relation_type=%s", (canonical_relation_type,)).fetchall()
    out = []
    for (rid, from_id, from_label, to_id, to_label, pct) in rows:
        if from_id == to_id:
            continue
        if is_person_type(entity_side_type(from_label)) or is_person_type(entity_side_type(to_label)):
            continue
        out.append((from_id, to_id, rid, pct))
    return out


def _entity_type_map(conn):
    """entity_id -> its own label type token, first-seen across all registry_ownership.relation rows."""
    m = {}
    rows = conn.execute(
        "SELECT raw_from_source_entity_id, raw_from_source_entity_label,"
        " raw_to_source_entity_id, raw_to_source_entity_label FROM registry_ownership.relation").fetchall()
    for from_id, from_label, to_id, to_label in rows:
        for eid, label in ((from_id, from_label), (to_id, to_label)):
            if eid not in m:
                t = entity_side_type(label)
                if t is not None:
                    m[eid] = t
    return m


def cmd_build_relation_contexts(_args):
    """Person-to-person (SoT §18) and company-to-company (SoT §19) relation
    contexts, computed entirely from local registry_ownership.relation/registry_ownership.indirect_ownership
    — no upstream request. Recomputed from scratch each run, like
    INDIRECT_OWNERSHIP.
    """
    with db() as conn:
        claim_collector_lock(conn)
        run_id = start_run(conn, "RELATION_CONTEXTS")
        stats = {"person_contexts": 0, "company_contexts": 0}

        def body():
            _tag_roles(conn)    # self-sufficient: never depends on normalize-roles having run first
            edges = _person_company_edges(conn)
            conn.execute("TRUNCATE registry_ownership.person_relation_context, registry_ownership.company_relation_context")

            by_company = defaultdict(list)
            for person_id, company_id, rid, rel_type, group, label, pct in edges:
                by_company[company_id].append((person_id, rid, group, label))

            for company_id, people in by_company.items():
                for (pa, ra, ga, la), (pb, rb, gb, lb) in itertools.combinations(people, 2):
                    if pa == pb:
                        continue
                    a, b = (pa, ra, ga, la), (pb, rb, gb, lb)
                    if a[0] > b[0]:
                        a, b = b, a
                    conn.execute(
                        "INSERT INTO registry_ownership.person_relation_context (person_a_entity_id, person_b_entity_id,"
                        " company_entity_id, person_a_relation_id, person_a_group, person_a_role_label,"
                        " person_b_relation_id, person_b_group, person_b_role_label, is_cross_role)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (a[0], b[0], company_id, a[1], a[2], a[3], b[1], b[2], b[3], a[2] != b[2]))
                    stats["person_contexts"] += 1

            for owner_id, owned_id, rid, pct in _company_company_edges(conn, "DIRECT_OWNERSHIP"):
                conn.execute(
                    "INSERT INTO registry_ownership.company_relation_context (company_a_entity_id, company_b_entity_id,"
                    " relation_basis, related_entity_id, company_a_relation, company_b_relation,"
                    " source_relation_id) VALUES (%s,%s,'DIRECT_OWNERSHIP',NULL,%s,NULL,%s)",
                    (owner_id, owned_id, f"Eigari {pct}%" if pct is not None else None, rid))
                stats["company_contexts"] += 1

            for actor_id, company_id, rid, _pct in _company_company_edges(conn, "REGISTERED_CONTROL_ACTOR"):
                conn.execute(
                    "INSERT INTO registry_ownership.company_relation_context (company_a_entity_id, company_b_entity_id,"
                    " relation_basis, related_entity_id, company_a_relation, company_b_relation,"
                    " source_relation_id) VALUES (%s,%s,'DIRECT_CONTROL_ACTOR',NULL,'Veruligur Eigari',NULL,%s)",
                    (actor_id, company_id, rid))
                stats["company_contexts"] += 1

            entity_types = _entity_type_map(conn)
            io_rows = conn.execute(
                "SELECT owner_entity_id, owned_entity_id, computed_percentage"
                " FROM registry_ownership.indirect_ownership").fetchall()
            for owner_id, owned_id, pct in io_rows:
                if owner_id == owned_id:
                    continue
                if is_person_type(entity_types.get(owner_id)) or is_person_type(entity_types.get(owned_id)):
                    continue
                conn.execute(
                    "INSERT INTO registry_ownership.company_relation_context (company_a_entity_id, company_b_entity_id,"
                    " relation_basis, related_entity_id, company_a_relation, company_b_relation)"
                    " VALUES (%s,%s,'INDIRECT_OWNERSHIP',NULL,%s,NULL)",
                    (owner_id, owned_id, f"{pct}%"))
                stats["company_contexts"] += 1

            by_person = defaultdict(list)
            for person_id, company_id, rid, rel_type, group, label, pct in edges:
                by_person[person_id].append((company_id, rid, rel_type, group, label))

            for person_id, companies in by_person.items():
                for (ca, ra, ta, ga, la), (cb, rb, tb, gb, lb) in itertools.combinations(companies, 2):
                    if ca == cb:
                        continue
                    basis = relation_basis(ta, ga, tb, gb)
                    a, b = (ca, ra, la), (cb, rb, lb)
                    if a[0] > b[0]:
                        a, b = b, a
                    conn.execute(
                        "INSERT INTO registry_ownership.company_relation_context (company_a_entity_id, company_b_entity_id,"
                        " relation_basis, related_entity_id, company_a_relation, company_b_relation,"
                        " source_relation_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (a[0], b[0], basis, person_id, a[2], b[2], a[1]))
                    stats["company_contexts"] += 1

            conn.commit()

        run_guard(conn, run_id, body)
        print(f"\nperson_relation_context rows:  {stats['person_contexts']}")
        print(f"registry_ownership.company_relation_context rows: {stats['company_contexts']}")
        basis_counts = conn.execute(
            "SELECT relation_basis, count(*) FROM registry_ownership.company_relation_context"
            " GROUP BY 1 ORDER BY 1").fetchall()
        for basis, n in basis_counts:
            print(f"  {basis:<25} {n}")


def assign_canonical_ids(conn):
    """Append-only canonical ID assignment (SoT §3.1/§3.2). No upstream
    request — reads only already-stored labels. Returns {"assigned",
    "already_had_id"}.

    APPEND-ONLY: an entity that already has a canonical_entity_id keeps it
    forever (ON CONFLICT DO NOTHING). This table is never TRUNCATEd and
    rebuilt — rebuilding would reassign IDs and silently break every link
    anyone has already published, which is exactly what §3.2 requires never
    happens. Safe and cheap to rerun after new data arrives: only entities
    without an ID yet get one.

    display_name is the PARSED name only — never the raw label. A raw label
    can carry a private home address (owner decision 2026-08-02), so on parse
    failure we fall back to the inventory's name-only normalized form, or a
    neutral placeholder, and the raw label never reaches a display field.

    Deterministic (A-3): labels are read in order and new ids are assigned in
    source_entity_id order, so a first assignment does not depend on the plan.
    canonical_entity is still raw-tier (module docstring): it is never rebuilt.
    """
    labels = {}          # source_entity_id -> (format, label)
    safe_names = {}      # source_entity_id -> normalized_search_name (name-only)
    for eid, label in conn.execute(
            "SELECT raw_from_source_entity_id, raw_from_source_entity_label FROM registry_ownership.relation"
            " UNION SELECT raw_to_source_entity_id, raw_to_source_entity_label FROM registry_ownership.relation"
            " ORDER BY 1, 2"):
        labels.setdefault(eid, ("table", label))
    for eid, label, folded in conn.execute(
            "SELECT source_entity_id, raw_display_name, normalized_search_name"
            " FROM company_inventory ORDER BY source_entity_id, source_system"):
        labels.setdefault(eid, ("search", label))
        safe_names.setdefault(eid, folded)

    stats = {"assigned": 0, "already_had_id": 0}
    for eid, (fmt, label) in sorted(labels.items()):
        parsed = parse_table_name(label) if fmt == "table" else parse_search_display(label)
        name, _regnr, type_token = parsed if parsed else (None, None, None)
        display = name or safe_names.get(eid) or f"entity {eid}"
        cur = conn.execute(
            "INSERT INTO canonical_entity (source_system, source_entity_id, entity_kind,"
            " entity_kind_confidence, raw_type_token, display_name)"
            " VALUES (%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (source_system, source_entity_id) DO NOTHING",
            (SOURCE_SYSTEM, eid, entity_kind(type_token), ENTITY_KIND_CONFIDENCE,
             type_token, display))
        stats["assigned" if cur.rowcount else "already_had_id"] += 1
    conn.commit()
    return stats


def cmd_assign_canonical_ids(_args):
    """WP-C5: assign canonical entity IDs. See assign_canonical_ids()."""
    with db() as conn:
        claim_collector_lock(conn)
        run_id = start_run(conn, "CANONICAL_ENTITY_ASSIGNMENT")

        def body():
            stats = assign_canonical_ids(conn)
            print(f"\nnewly assigned canonical IDs: {stats['assigned']}")
            print(f"already had a stable ID (untouched): {stats['already_had_id']}")

        run_guard(conn, run_id, body)
        kind_counts = conn.execute(
            "SELECT entity_kind, count(*) FROM canonical_entity GROUP BY 1 ORDER BY 1").fetchall()
        for kind, n in kind_counts:
            print(f"  {kind:<18} {n}")


def cmd_collision_report(_args):
    """WP-C5: same-name collision report (SoT §6.3 / §29 identity-same-name,
    option A). Entities with identical normalized display names but distinct
    source IDs (pkCbi) are listed together with their distinguishing
    attributes — never auto-merged, never hidden. Reads local storage only.
    One source ID = one canonical entity; the report only surfaces the
    collision, it never merges (D-5: merge machinery deferred).
    """
    with db() as conn:
        print("=" * 72)
        print("HEIMDAL WP-C5 — SAME-NAME COLLISION REPORT")
        print("(one source ID = one canonical entity; nothing here is merged)")
        print(f"generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
        print("=" * 72)

        rows = conn.execute(
            "SELECT ce.source_entity_id, ce.canonical_entity_id, ce.entity_kind,"
            " ce.raw_type_token, ce.display_name, ci.registration_number"
            " FROM canonical_entity ce"
            " LEFT JOIN company_inventory ci"
            "   ON ci.source_system = ce.source_system AND ci.source_entity_id = ce.source_entity_id"
            " ORDER BY ce.source_entity_id").fetchall()

        groups = {}
        for eid, cid, kind, token, name, regnr in rows:
            groups.setdefault(normalize_name(name), []).append(
                (eid, cid, kind, token, name, regnr))

        collisions = {norm: members for norm, members in groups.items()
                      if len({m[0] for m in members}) > 1}
        n_rows = sum(len(m) for m in collisions.values())
        print(f"\nentities with a canonical ID:       {len(rows)}")
        print(f"distinct normalized names:          {len(groups)}")
        print(f"collision groups (same normalized name, distinct pkCbi): {len(collisions)}")
        print(f"entities listed in collisions:      {n_rows}"
              f"  (all kept distinct, none merged)")

        for norm in sorted(collisions):
            members = collisions[norm]
            print(f"\n  {norm!r} — {len(members)} entities")
            for eid, cid, kind, token, name, regnr in sorted(members, key=lambda m: m[0]):
                print(f"    pkCbi {eid:<7} canonical {cid:<5} {kind:<18}"
                      f" {name!r}  token={token!r}  regnr={regnr or '-'}")


def cmd_report(_args):
    """Completion report. Reads only local storage — no upstream request."""
    with db() as conn:
        print("=" * 72)
        print("HEIMDAL PHASE 1 — COLLECTION COMPLETION REPORT")
        print(f"generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
        print("=" * 72)

        pending, top, ceiling, trailing, exhausted = sweep_state(conn)
        print("\nENUMERATION (exact registration-number sweep)")
        for status, n in conn.execute("SELECT status, count(*) FROM enumeration_key"
                                      " GROUP BY 1 ORDER BY 1").fetchall():
            print(f"  {status:<8} {n}")
        print(f"  worklist drained:     {pending == 0}")
        print(f"  highest number found: S{top}   ceiling probed: S{ceiling}")
        print(f"  trailing misses:      {trailing} (>= {TRAILING_MISS_THRESHOLD} required)")
        print(f"  key space exhausted:  {exhausted}")

        total, registered = conn.execute(
            "SELECT count(*), count(registration_number) FROM company_inventory").fetchone()
        print("\nDENOMINATOR")
        print(f"  entities discovered:                 {total}")
        print(f"  registered companies (denominator):  {registered}")
        print(f"  without registration number:         {total - registered}"
              "   (persons / foreign / non-registered)")
        _print_status_counts(conn)

        attempts, contents, dedup = conn.execute(
            "SELECT (SELECT count(*) FROM request_attempt),"
            " (SELECT count(*) FROM response_content),"
            " (SELECT count(*) FROM request_attempt WHERE response_sha256 IS NOT NULL)").fetchone()
        stored = conn.execute("SELECT coalesce(sum(byte_length),0) FROM response_content").fetchone()[0]
        print("\nREQUESTS AND CONTENT")
        print(f"  request attempts (provenance rows):  {attempts}")
        print(f"  attempts with a stored body:         {dedup}")
        print(f"  distinct response bodies (SHA-256):  {contents}")
        print(f"  deduplicated away:                   {dedup - contents}")
        print(f"  stored bytes:                        {stored:,}")
        for endpoint, n, e in conn.execute(
                "SELECT endpoint, count(*), count(*) FILTER (WHERE error_category<>'OK')"
                " FROM request_attempt GROUP BY 1 ORDER BY 1").fetchall():
            print(f"    {endpoint:<12} {n:>7} attempts, {e} errors")

        # Gap detection: entities named inside get-table responses that carry a
        # registration number but are absent from the inventory. Identifier
        # extraction only — no relation is read, interpreted or stored.
        known = {r[0] for r in conn.execute(
            "SELECT source_entity_id FROM company_inventory").fetchall()}
        gaps, observed, skipped = {}, set(), []
        for row in conn.execute(INPUT_BODIES_SQL + " ORDER BY ra.attempt_id").fetchall():
            for edge in input_edges(*row, skipped):
                for side in ("from", "to"):
                    eid, text = edge[side + "Id"], edge[side + "Name"]
                    observed.add(eid)
                    if eid not in known and _HAS_REGNR.search(text):
                        gaps[eid] = text
        print("\nGAP DETECTION")
        print(f"  distinct entities observed in responses: {len(observed)}")
        print(f"  observed but not in inventory:           {len(observed - known)}"
              "   (expected: persons / foreign entities)")
        print(f"  INVENTORY GAPS (registered, not found):  {len(gaps)}")
        for eid, text in list(gaps.items())[:10]:
            print(f"    gap id={eid} {text[:70]}")
        print_skipped_bodies(skipped)

        unresolved = conn.execute(
            "SELECT count(*) FROM company_inventory"
            " WHERE inventory_status IN ('DISCOVERED','QUEUED','RETRYABLE_ERROR')").fetchone()[0]
        claimable = exhausted and not gaps and unresolved == 0
        print("\nPOPULATION LABEL")
        print(f"  key space exhausted:          {exhausted}")
        print(f"  every company terminal:       {unresolved == 0}")
        print(f"  no unexplained inventory gap: {not gaps}")
        print(f"  -> {'FULL_POPULATION_CLAIMED is defensible (owner decision)' if claimable else 'PARTIAL_POPULATION'}")

        for rid, rtype, rstatus, reason in conn.execute(
                "SELECT collection_run_id, run_type, run_status, stop_reason"
                " FROM collection_run WHERE run_status='STOPPED'").fetchall():
            print(f"\n  STOPPED run {rid} ({rtype}): {reason}")
        for rid, rtype, started in conn.execute(  # A-8: a crash used to leave these behind
                "SELECT collection_run_id, run_type, started_at FROM collection_run"
                " WHERE run_status='RUNNING' AND ended_at IS NULL ORDER BY 1").fetchall():
            print(f"\n  RUNNING run {rid} ({rtype}) since {started:%Y-%m-%d %H:%M}, never ended:"
                  " stale unless a collector is running now")

        rel_counts = dict(conn.execute(
            "SELECT canonical_relation_type, count(*) FROM registry_ownership.relation"
            " GROUP BY 1").fetchall())
        status_count = conn.execute("SELECT count(*) FROM entity_status").fetchone()[0]
        if rel_counts or status_count:
            print("\nrelation processing (canonical storage only, owner-authorized 2026-08-02):")
            for t in ("DIRECT_OWNERSHIP", "REGISTERED_CONTROL_ACTOR", "OTHER_SOURCE_RELATION"):
                print(f"  {t:<25} {rel_counts.get(t, 0)}")
            print(f"  entity_status NO_REGISTERED_CONTROL_ACTOR  {status_count}")

            group_counts = conn.execute(
                "SELECT inferred_group, count(*) FROM registry_ownership.relation"
                " GROUP BY 1 ORDER BY 1 NULLS LAST").fetchall()
            if group_counts:
                print("  source group tags (SoT §18/§19 prereq, INFERRED not OBSERVED):")
                for group, n in group_counts:
                    print(f"    {group or 'untagged':<10} {n}")

            io_count = conn.execute("SELECT count(*) FROM registry_ownership.indirect_ownership").fetchone()[0]
            cycle_groups = conn.execute(
                "SELECT count(DISTINCT cycle_group_id) FROM registry_ownership.ownership_cycle_member").fetchone()[0]
            cycle_entities = conn.execute(
                "SELECT count(DISTINCT entity_id) FROM registry_ownership.ownership_cycle_member").fetchone()[0]
            if io_count or cycle_groups:
                print(f"  indirect ownership rows (SoT §15)          {io_count}")
                print(f"  cycle-excluded entities                    {cycle_entities}"
                      f"  ({cycle_groups} groups: self-loops + cross-ownership cycles)")

                prc_count = conn.execute("SELECT count(*) FROM registry_ownership.person_relation_context").fetchone()[0]
                crc_count = conn.execute("SELECT count(*) FROM registry_ownership.company_relation_context").fetchone()[0]
                if prc_count or crc_count:
                    print(f"  registry_ownership.person_relation_context rows (SoT §18)     {prc_count}")
                    print(f"  registry_ownership.company_relation_context rows (SoT §19)    {crc_count}")
                    for basis, n in conn.execute(
                            "SELECT relation_basis, count(*) FROM registry_ownership.company_relation_context"
                            " GROUP BY 1 ORDER BY 1").fetchall():
                        print(f"    {basis:<25} {n}")
                    print("  portals / API: NOT STARTED")
                else:
                    print("  relation contexts / portals / API: NOT STARTED")

                # ponytail: known source-data quirk (2026-08-02, undersøgt efter bruger-rapport
                # om SoT §16.2's historiske >100%-ejerskabsfejl). Self-kanter (subject=object,
                # fx et selskab der "ejer" en andel af sig selv) skal ekskluderes fra sum-tjek,
                # ellers ser lovlige tilfælde (Jarðhiti, NAX) forkert ud som fejl.
                over100 = conn.execute(
                    "SELECT ci.registration_number, ci.raw_display_name, s.total"
                    " FROM (SELECT canonical_object_entity_id AS eid, SUM(parsed_percentage) AS total"
                    "       FROM registry_ownership.relation"
                    "       WHERE canonical_relation_type='DIRECT_OWNERSHIP'"
                    "         AND canonical_subject_entity_id != canonical_object_entity_id"
                    "         AND parsed_percentage IS NOT NULL"
                    "       GROUP BY 1 HAVING SUM(parsed_percentage) > 100.5) s"
                    " JOIN company_inventory ci ON ci.source_entity_id = s.eid"
                    " ORDER BY s.total DESC").fetchall()
                if over100:
                    print(f"  known direct-ownership sum >100% (source data quirk, not a bug): {len(over100)}")
                    for regnr, name, total in over100:
                        print(f"    {regnr or '?':<8} {name:<50} {total:.2f}%")
            else:
                print("  indirect ownership calc / relation contexts / portals / API: NOT STARTED")
        else:
            print("\nrelation processing: NOT STARTED (requires separate owner authorization)")


# WP-C4: per-type disposition proposal for the owner (SoT §12 audit
# precondition, owner decision D-4). Everything beyond the decided types stays
# OTHER_SOURCE_RELATION, raw display only, until per-type owner decisions.
# Direction per the source contract's §15.5 matrix; 81's row is split because
# self-edge (NOT_AN_EDGE) and non-self anomaly (UNCONFIRMED) differ.
TAXONOMY_DISPOSITION = {
    1:   ("DIRECT_OWNERSHIP", "OWNER_TO_OWNED"),
    41:  ("REGISTERED_CONTROL_ACTOR", "REGISTERED_ACTOR_TO_ENTITY"),
    81:  ("ENTITY_STATUS self / OTHER_SOURCE_RELATION anomaly", "NOT_AN_EDGE / UNCONFIRMED"),
    101: ("OTHER_SOURCE_RELATION — voting rights, own display subsection",
         "UNCONFIRMED"),
}
TAXONOMY_FALLBACK_DISPOSITION = ("OTHER_SOURCE_RELATION, raw display only", "UNCONFIRMED")


def cmd_taxonomy_audit(_args):
    """WP-C4: full-population cbTypeId taxonomy audit across the frozen input
    set (get-table HTTP-200 bodies for FETCHED companies). Reads local storage
    only. Proves SoT §12's audit precondition: the sum over all types equals
    the total raw row count — 100% coverage, no type dropped, none promoted.
    """
    with db() as conn:
        print("=" * 72)
        print("HEIMDAL WP-C4 — FULL-POPULATION RELATION-TYPE TAXONOMY AUDIT")
        print(f"generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
        print("=" * 72)

        # (cbTypeId, cbTypeName) -> raw rows / distinct (fromId, toId), over the
        # bodies that pass the key rule (A-2). Python str order = the DB's C collation.
        counts, pairs, skipped = defaultdict(int), defaultdict(set), []
        bodies = conn.execute(INPUT_BODIES_SQL + " ORDER BY ra.attempt_id").fetchall()
        for row in bodies:
            for e in input_edges(*row, skipped):
                key = (int(e["cbTypeId"]), e["cbTypeName"])
                counts[key] += 1
                pairs[key].add((str(e["fromId"]), str(e["toId"])))
        rows = [(t, label, counts[t, label], len(pairs[t, label])) for t, label in sorted(counts)]

        total_raw = sum(r[2] for r in rows)
        attempts = len(bodies)
        print(f"\nfrozen input set: {attempts} get-table HTTP-200 attempts")
        print_skipped_bodies(skipped)
        print(f"distinct (cbTypeId, cbTypeName) labels observed: {len(rows)}")
        print(f"total raw rows:                        {total_raw}")

        canonical = dict(conn.execute(
            "SELECT raw_source_relation_type_id, count(*) FROM registry_ownership.relation"
            " GROUP BY 1").fetchall())
        status = conn.execute("SELECT count(*) FROM entity_status").fetchone()[0]

        print(f"\n{'cbTypeId':>8} {'cbTypeName':<24} {'raw':>6} {'pairs':>6} "
              f"disposition / direction")
        print("-" * 72)
        for t, label, n, pairs in rows:
            disp, direction = TAXONOMY_DISPOSITION.get(
                t, TAXONOMY_FALLBACK_DISPOSITION)
            print(f"{t:>8} {label:<24} {n:>6} {pairs:>6} {disp} | {direction}")

        per_type = {}
        for t, _label, n, _pairs in rows:
            per_type[t] = per_type.get(t, 0) + n

        print("\nPER-TYPE SUMMARY (sum over types = total raw rows)")
        print(f"{'cbTypeId':>8} {'raw rows':>9} {'canonical':>10} {'group tag':>10}")
        print("-" * 72)
        for t in sorted(per_type):
            n = per_type[t]
            can = canonical.get(t, 0)
            can += status if t == CBTYPE_OVE else 0
            group, _label = classify_role(t)
            print(f"{t:>8} {n:>9} {can:>10} {group or '(unmapped!)':>10}")
        assert sum(per_type.values()) == total_raw

        dedup = total_raw - sum(canonical.values()) - status
        print(f"\nRECONCILIATION (100% coverage proof)")
        print(f"  sum over all types (raw):            {sum(per_type.values())}")
        print(f"  total raw rows:                      {total_raw}"
              f"  -> {'COVERED' if sum(per_type.values()) == total_raw else 'GAP!'}")
        print(f"  registry_ownership.relation rows:             {sum(canonical.values())}")
        print(f"  entity_status rows (81 self-edges):  {status}")
        print(f"  endpoint-dedup duplicates (§5.4):    {dedup}")
        for t in (CBTYPE_DIRECT_OWNERSHIP, CBTYPE_REGISTERED_CONTROL_ACTOR, CBTYPE_OVE):
            dup = per_type.get(t, 0) - canonical.get(t, 0) - (status if t == CBTYPE_OVE else 0)
            print(f"    type {t}: {dup}")

        unmapped = conn.execute(
            "SELECT count(*) FROM registry_ownership.relation"
            " WHERE inferred_group IS NULL").fetchone()[0]
        observed_tags = conn.execute(
            "SELECT count(*) FROM registry_ownership.relation"
            " WHERE inferred_group_confidence <> 'INFERRED'").fetchone()[0]
        print(f"\nROLE-GROUP TAGS (SoT §18/§19 prereq)")
        print(f"  untagged canonical rows:             {unmapped}")
        print(f"  non-INFERRED confidences:            {observed_tags}"
              f" (schema CHECK allows only INFERRED)")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in [("init", cmd_init), ("queue", cmd_queue),
                     ("retry", cmd_retry), ("report", cmd_report)]:
        sub.add_parser(name).set_defaults(fn=fn)
    e = sub.add_parser("enumerate")
    e.add_argument("--max", type=int, required=True,
                   help="highest registration number to probe (sweep ceiling)")
    e.set_defaults(fn=cmd_enumerate)
    a = sub.add_parser("acquire"); a.add_argument("--limit", type=int); a.set_defaults(fn=cmd_acquire)
    v = sub.add_parser("validate-types"); v.add_argument("--sample", type=int, default=10)
    v.set_defaults(fn=cmd_validate_types)
    pi = sub.add_parser("parse-inputs")
    pi.set_defaults(fn=cmd_parse_inputs)
    pr = sub.add_parser("process-relations")
    pr.add_argument("--rebuild", action="store_true",
                    help="TRUNCATE relation + entity_status RESTART IDENTITY CASCADE and"
                         " re-derive them in the same transaction (reproduces relation_id)")
    pr.set_defaults(fn=cmd_process_relations)
    sub.add_parser("compute-ownership").set_defaults(fn=cmd_compute_ownership)
    sub.add_parser("normalize-roles").set_defaults(fn=cmd_normalize_roles)
    sub.add_parser("build-relation-contexts").set_defaults(fn=cmd_build_relation_contexts)
    sub.add_parser("assign-canonical-ids").set_defaults(fn=cmd_assign_canonical_ids)
    sub.add_parser("collision-report").set_defaults(fn=cmd_collision_report)
    sub.add_parser("taxonomy-audit").set_defaults(fn=cmd_taxonomy_audit)
    sub.add_parser("backfill-company-locations").set_defaults(fn=cmd_backfill_company_locations)
    bpdf = sub.add_parser("backfill-pdf-company-locations")
    bpdf.add_argument("--limit", type=int)
    bpdf.set_defaults(fn=cmd_backfill_pdf_company_locations)
    args = p.parse_args()
    # A-13: every urlopen() in this process refuses redirects that leave the host.
    urllib.request.install_opener(urllib.request.build_opener(_SameHostRedirect))
    args.fn(args)


if __name__ == "__main__":
    main()
