"""Deterministic entity-link builder (read-only canonical, projection writes).

Task: KNYT PDF-DATA TIL EKSISTERENDE VIRKSOMHEDER OG PERSONER.

Binding rules implemented:

* COMPANY links are deterministic by registration number ONLY
  (REGISTRATION_NUMBER_EXACT). No name/address/fuzzy matching ever produces
  a company link. 1 canonical candidate -> LINKED, 0 -> UNRESOLVED,
  >1 -> AMBIGUOUS (no silent winner).
* PERSON links are scoped to the concrete role occurrence
  (company_person_role.id -- no global source person identity exists,
  section 14). An automatic LINKED requires:
      1. the role occurrence's company is itself LINKED (regnr-anchored), and
      2. exact normalized name match against canonical PERSON display names,
      and 3. that candidate person holds an EXISTING canonical
      company-role relation to the SAME canonical company with a compatible
      role group (independently sourced secondary attribute), and
      4. the candidate set is UNIQUE (exactly one such person).
  Name alone never links (section 11).
  R-WS: canonical side keyed with normalize_person_name (whitespace-collapsed)
  as normalization-symmetry fix. R-ADDR: strict deterministic address
  identity-core comparator (section 5.1 v1.1) — only for HISTORICAL_EVENT
  BOARD/MANAGEMENT where company LINKED and legacy UNRESOLVED.
* The builder only INSERTs into registry_projection.entity_link and only
  READS projection/canonical data. It never touches source/current-state
  rows, canonical tables, or evidence (sections 21/22/50).
* Idempotent: same run + same canonical state + same link_code_version =>
  identical link rows (delete+reinsert inside one transaction; section 51).
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re

import psycopg

from .cov_historical import cov_source_key
from .normalize import normalize_registration_number

_logger = logging.getLogger(__name__)

LINK_CODE_VERSION = "v2"

# source role_type -> compatible canonical v_role_relation inferred_group
_ROLE_GROUP_COMPATIBILITY = {
    "BOARD": "NEVND",
    "MANAGEMENT": "STJORN",
    # AUDITOR has no canonical role-relation equivalent; historical auditor
    # occurrences therefore fail closed to UNRESOLVED (section 18).
}

# --- R-ADDR constants (frozen v1.1 §5.1) ---
_COUNTRIES = {"føroyar", "danmark", "noreg", "ísland", "island", "grænland", "sverige"}
_GLUED_SPLIT_RE = re.compile(r"^(\d+)([a-zæøáðíóúýþ]+)$")
_PURE_DIGIT_RE = re.compile(r"^\d+$")

# --- ownership generation pin (determinism fix §4-8) ---
# Reuse existing registry_ownership.build_meta generation. No new snapshot
# system. Format: "own-build:<build_id>"  (e.g. "own-build:1")
_BUILD_META_COLS = ("build_id", "schema_version", "source_relation_count",
                    "source_relation_max_id", "max_source_attempt_id",
                    "max_collection_run_id", "content_hash", "status")


def _resolve_ownership_generation(canonical_conn, ownership_generation_pin=None, ownership_build_meta_id=None):
    """Deterministically resolve ownership generation pin at build start.

    Preferred: explicit generation supplied to the candidate build
    (ownership_build_meta_id, else ownership_generation_pin). Fallback:
    active build_meta row (status='ACTIVE'). Never uses MAX(latest) per-row.

    D-08: raises when the pin cannot be resolved (malformed pin, unknown
    build id, no ACTIVE generation, missing registry_ownership schema), so
    R-ADDR can never fall back to unpinned, mutable registry_ownership.

    Returns dict with keys: build_id, generation_pin, content_hash,
    source_relation_count, source_relation_max_id, max_source_attempt_id,
    max_collection_run_id, schema_version, status.
    """
    if ownership_build_meta_id is None and ownership_generation_pin is not None:
        m = re.fullmatch(r"own-build:(\d+)", str(ownership_generation_pin).strip())
        if not m:
            raise ValueError(f"malformed ownership generation pin {ownership_generation_pin!r}")
        ownership_build_meta_id = int(m.group(1))
    sql = ("SELECT " + ", ".join(_BUILD_META_COLS)
           + " FROM registry_ownership.build_meta ")
    if ownership_build_meta_id is not None:
        row = canonical_conn.execute(sql + "WHERE build_id=%s",
                                     (int(ownership_build_meta_id),)).fetchone()
    else:
        row = canonical_conn.execute(
            sql + "WHERE status='ACTIVE' ORDER BY build_id DESC LIMIT 1").fetchone()
    if row is None:
        raise RuntimeError(
            "ownership generation pin unresolved: no registry_ownership."
            + (f"build_meta row {ownership_build_meta_id}"
               if ownership_build_meta_id is not None else "build_meta ACTIVE row"))
    meta = dict(row) if isinstance(row, dict) else dict(zip(_BUILD_META_COLS, row))
    as_int = lambda v: int(v) if v is not None else None  # noqa: E731
    return {
        "build_id": int(meta["build_id"]),
        "generation_pin": f"own-build:{int(meta['build_id'])}",
        "content_hash": meta["content_hash"],
        "source_relation_count": as_int(meta["source_relation_count"]),
        "source_relation_max_id": as_int(meta["source_relation_max_id"]),
        "max_source_attempt_id": as_int(meta["max_source_attempt_id"]),
        "max_collection_run_id": as_int(meta["max_collection_run_id"]),
        "schema_version": meta["schema_version"], "status": meta["status"],
    }


def normalize_person_name(name):
    """Same normalization family as canonical display-name matching:
    lowercase + trim + whitespace collapse. Never more (no fuzzy)."""
    if not name:
        return ""
    return " ".join(name.split()).lower()


def _is_pure_digit(token: str) -> bool:
    return bool(_PURE_DIGIT_RE.match(token))


def _tokenize_address(text: str | None) -> list[str]:
    """Strict deterministic tokenizer (§5.1 rules 1-4).

    1. lowercase
    2. strip PDF noise from 'skráseting føroya' onward before tokenization
    3. non [a-záðéíóúüýþæø0-9 ] -> spaces; collapse
    4. remove country tokens
    5. split glued house token ^\\d+[a-zæøáðíóúýþ]+$ into number+suffix
    """
    if not text:
        return []
    s = text.lower()
    # rule 3: PDF page-noise tail
    s = re.sub(r"skráseting føroya.*$", "", s)
    s = re.sub(r"[^a-záðéíóúüýþæø0-9 ]", " ", s)
    parts = s.split()
    filtered = [t for t in parts if t and t not in _COUNTRIES]
    out: list[str] = []
    for tok in filtered:
        m = _GLUED_SPLIT_RE.match(tok)
        if m:
            out.append(m.group(1))
            out.append(m.group(2))
        else:
            out.append(tok)
    return out


def _address_match_strict(source_tokens: list[str], pdf_tokens: list[str]) -> bool:
    """Exact identity-core matching only (§5.1 rule 5).

    Source tokens must be consumed in order against PDF tokens from position 0.
    A PDF token skipped between consumed source tokens is allowed only if it is
    pure-digit. PDF tail after last source token allowed only if all pure-digit.
    No generic subsequence.
    """
    if not source_tokens or not pdf_tokens:
        return False
    i = j = 0
    while j < len(source_tokens):
        if i >= len(pdf_tokens):
            return False
        if source_tokens[j] == pdf_tokens[i]:
            j += 1
            i += 1
        else:
            if _is_pure_digit(pdf_tokens[i]):
                i += 1
            else:
                return False
    while i < len(pdf_tokens):
        if not _is_pure_digit(pdf_tokens[i]):
            return False
        i += 1
    return True


def _extract_pdf_address_with_raw(raw_text: str | None, normalized_name: str):
    """Extract PDF address like docs/pdf-person-resolution-opportunities/tools/address_anchor_shadow.py.

    Locate normalized name with word boundaries/whitespace flexibility in
    lowercased raw text, rest after name, leading comma removal, cut '(',
    strip noise tail, collapse, strip punctuation, require digit.
    Returns (raw_rest, tokens) or None.
    """
    if not raw_text or not normalized_name:
        return None
    low = raw_text.lower()
    esc = re.escape(normalized_name).replace(r"\ ", r"\s+")
    pat = re.compile(r"\b" + esc + r"\b")
    m = pat.search(low)
    if not m:
        return None
    rest = low[m.end():]
    if rest.startswith(","):
        rest = rest[1:]
    rest = rest.strip()
    cut = rest.find("(")
    if cut >= 0:
        rest = rest[:cut]
    rest = re.sub(r"skráseting føroya.*$", "", rest)
    raw_rest_collapsed = re.sub(r"\s+", " ", rest).strip(" ,;")
    if not raw_rest_collapsed or not re.search(r"\d", raw_rest_collapsed):
        return None
    tokens = _tokenize_address(raw_rest_collapsed)
    if not tokens:
        return None
    return raw_rest_collapsed, tokens


def _parse_raw_label(label: str | None):
    """'Name - Persónur, addr' -> (name_tokens, addr_tokens, name_raw, addr_raw)."""
    if not label or " - Persónur, " not in label:
        return None
    name, addr = label.split(" - Persónur, ", 1)
    name_tokens = _tokenize_address(name)
    addr_tokens = _tokenize_address(addr)
    if not name_tokens or not addr_tokens:
        return None
    return name_tokens, addr_tokens, name.strip(), addr.strip()


def _load_raw_person_address_map(canonical_conn, ownership_pin_meta):
    """Load global source identity map from registry_ownership.relation.

    JOIN canonical_entity entity_kind PERSON, parse 'Name - Persónur, addr',
    map raw source entity to canonical person. Returns
    (key_map, raw_to_canonical, raw_to_label, raw_to_attempt_ids, raw_to_collection_ids) where key_map is
    (name_tokens_tuple, addr_tokens_tuple) -> {raw_ids, canonical_ids, labels, name_raw, addr_raw, addr_tokens, name_tokens}.
    Duplicate raw identities are retained for fail-closed handling.

    The loader is PINNED: it only sees rows/entities belonging to the pinned
    ownership generation (relation_id <= source_relation_max_id AND evidence
    collection/attempt ids within generation). This makes R-ADDR
    reproducible (PDF projection generation + ownership generation pin +
    link code). D-08: there is no unpinned read; a pin without
    source_relation_max_id and every query error raise.
    """
    key_map: dict[tuple, dict] = {}
    raw_to_canonical: dict = {}
    raw_to_label: dict = {}
    raw_to_attempt_ids: dict[int, set[int]] = {}
    raw_to_collection_ids: dict[int, set[int]] = {}
    if (ownership_pin_meta or {}).get("source_relation_max_id") is None:
        raise RuntimeError("R-ADDR needs a pinned ownership generation "
                           "(source_relation_max_id)")
    max_rel = int(ownership_pin_meta["source_relation_max_id"])
    max_att = ownership_pin_meta.get("max_source_attempt_id")
    max_coll = ownership_pin_meta.get("max_collection_run_id")
    # pinned query: only relations within generation, via evidence pointer
    # Filter through versioned relation/evidence rows (not assuming raw id == pin)
    sql = """SELECT cr.raw_from_source_entity_id AS rid,
                    cr.raw_from_source_entity_label AS lbl,
                    rep.source_attempt_id AS attempt_id,
                    rep.collection_run_id AS coll_id
             FROM registry_ownership.relation cr
             JOIN registry_ownership.relation_evidence_pointer rep ON rep.relation_id = cr.relation_id
             JOIN canonical_entity ce ON ce.source_entity_id = cr.raw_from_source_entity_id
             WHERE ce.entity_kind = 'PERSON'
               AND cr.relation_id <= %s"""
    params: tuple = (max_rel,)
    if max_att is not None and max_coll is not None:
        sql += """
               AND rep.source_attempt_id <= %s
               AND rep.collection_run_id <= %s"""
        params += (int(max_att), int(max_coll))
    rows = canonical_conn.execute(sql, params).fetchall()
    # collect attempt provenance per raw entity (deterministic sets) and the
    # first label per raw entity for key_map parsing
    seen_rid_lbl: dict[int, str] = {}
    for row in rows:
        if isinstance(row, dict):
            rid, lbl, aid, cid = row["rid"], row["lbl"], row.get("attempt_id"), row.get("coll_id")
        else:
            rid, lbl, aid, cid = row[0], row[1], row[2], row[3]
        if rid is None:
            continue
        if aid is not None:
            raw_to_attempt_ids.setdefault(int(rid), set()).add(int(aid))
        if cid is not None:
            raw_to_collection_ids.setdefault(int(rid), set()).add(int(cid))
        if rid not in seen_rid_lbl and lbl is not None:
            seen_rid_lbl[rid] = lbl
    canon_rows = canonical_conn.execute(
        "SELECT source_entity_id, canonical_entity_id FROM canonical_entity WHERE entity_kind = 'PERSON'"
    ).fetchall()
    canon_of = {}
    for row in canon_rows:
        if isinstance(row, dict):
            sid=row["source_entity_id"]; cid=row["canonical_entity_id"]
        else:
            sid, cid=row
        canon_of[sid]=cid
    for rid, lbl in seen_rid_lbl.items():
        raw_to_label[rid] = lbl
        cid = canon_of.get(rid)
        if cid is not None:
            raw_to_canonical[rid] = cid
        parsed = _parse_raw_label(lbl)
        if parsed is None:
            continue
        name_toks, addr_toks, name_raw, addr_raw = parsed
        key = (tuple(name_toks), tuple(addr_toks))
        entry = key_map.setdefault(key, {
            "raw_ids": set(),
            "canonical_ids": set(),
            "labels": {},
            "name_raw": name_raw,
            "addr_raw": addr_raw,
            "addr_tokens": addr_toks,
            "name_tokens": name_toks,
        })
        entry["raw_ids"].add(rid)
        if cid is not None:
            entry["canonical_ids"].add(cid)
        entry["labels"][rid] = lbl
    return key_map, raw_to_canonical, raw_to_label, raw_to_attempt_ids, raw_to_collection_ids


def load_company_identity_map(conn):
    """normalized regnr -> distinct canonical COMPANY entities (read-only).

    Read from the canonical Heimdal DB: company_inventory.registration_number
    ('S5534') joined to canonical_entity via source_entity_id.
    """
    rows = conn.execute(
        """SELECT ci.registration_number, ce.canonical_entity_id, ce.display_name
           FROM company_inventory ci
           JOIN canonical_entity ce ON ce.source_entity_id = ci.source_entity_id
           WHERE ce.entity_kind = 'COMPANY'""").fetchall()
    mapping = {}
    for row in rows:
        if isinstance(row, dict):
            regnr = row["registration_number"]
            cid = row["canonical_entity_id"]
            display = row["display_name"]
        else:
            regnr, cid, display = row
        key = normalize_registration_number(regnr)
        if not key:
            continue
        entries = mapping.setdefault(key, [])
        if all(entry["canonical_entity_id"] != cid for entry in entries):
            entries.append({"canonical_entity_id": cid, "display_name": display})
    return mapping


def _load_person_role_relations_legacy(conn):
    """Legacy helper for tiering: lower(btrim(person_name)) key (v1)."""
    rows = conn.execute(
        """SELECT DISTINCT company_id, lower(btrim(person_name)) AS pname,
                  person_id
           FROM v_role_relation
           WHERE inferred_group IN ('NEVND','STJORN','ANNAD')""").fetchall()
    mapping = {}
    for row in rows:
        if isinstance(row, dict):
            company_id = row["company_id"]
            pname = row["pname"]
            person_id = row["person_id"]
        else:
            company_id, pname, person_id = row
        entries = mapping.setdefault((company_id, pname), [])
        if all(pid != person_id for pid in entries):
            entries.append(person_id)
    return mapping


def load_person_role_relations(conn):
    """(canonical company id, normalized person name) -> canonical person ids.

    Built read-only from v_role_relation, restricted to canonical company-role
    relations (NEVND/STJORN/ANNAD). Ownership-only relations (EIGARAR) are NOT
    sufficient co-occurrence evidence for a board/management source role.
    R-WS: keys canonical side with normalize_person_name (whitespace-collapsed).
    """
    rows = conn.execute(
        """SELECT DISTINCT company_id, person_name, person_id
           FROM v_role_relation
           WHERE inferred_group IN ('NEVND','STJORN','ANNAD')"""
    ).fetchall()
    mapping = {}
    for row in rows:
        if isinstance(row, dict):
            company_id = row["company_id"]
            person_name = row["person_name"]
            person_id = row["person_id"]
        else:
            company_id, person_name, person_id = row
        pname = normalize_person_name(person_name)
        if not pname:
            continue
        entries = mapping.setdefault((company_id, pname), [])
        if all(pid != person_id for pid in entries):
            entries.append(person_id)
    return mapping


def load_person_display_names(conn):
    rows = conn.execute(
        "SELECT canonical_entity_id, display_name FROM canonical_entity "
        "WHERE entity_kind = 'PERSON'").fetchall()
    out={}
    for row in rows:
        if isinstance(row, dict):
            cid=row["canonical_entity_id"]; display=row["display_name"]
        else:
            cid, display=row
        out[cid]=display
    return out


def match_company(key, identity_map):
    """Deterministic company link decision for one normalized regnr.

    Returns (status, canonical_entity_id, candidates, method).
    """
    candidates = identity_map.get(key, []) if key else []
    if len(candidates) == 1:
        return ("LINKED", candidates[0]["canonical_entity_id"], candidates,
                "REGISTRATION_NUMBER_EXACT")
    if len(candidates) > 1:
        return ("AMBIGUOUS", None, candidates, "REGISTRATION_NUMBER_EXACT")
    return ("UNRESOLVED", None, [], "REGISTRATION_NUMBER_EXACT")


def match_person(normalized_name, role_type, company_canonical_id,
                 role_relations, person_names):
    """Mechanical person policy (sections 11-13).

    1. no company anchor or no compatible role group -> UNRESOLVED
    2. candidates = canonical persons with EXACT normalized name AND an
       existing canonical company-role relation to the SAME company with a
       compatible role group
    3. exactly 1 candidate -> LINKED (EXACT_MULTI_ATTRIBUTE)
    4. >1 -> AMBIGUOUS ; 0 -> UNRESOLVED
    Name alone can never link. Addresses never participate in this tier.
    """
    if company_canonical_id is None:
        return ("UNRESOLVED", None, [], "EXACT_MULTI_ATTRIBUTE")
    compatible = _ROLE_GROUP_COMPATIBILITY.get(role_type)
    if compatible is None:
        return ("UNRESOLVED", None, [], "EXACT_MULTI_ATTRIBUTE")
    if not normalized_name:
        return ("UNRESOLVED", None, [], "EXACT_MULTI_ATTRIBUTE")
    person_ids = role_relations.get((company_canonical_id, normalized_name), [])
    candidates = [{"canonical_entity_id": pid,
                   "display_name": person_names.get(pid)}
                  for pid in person_ids]
    if len(candidates) == 1:
        return ("LINKED", candidates[0]["canonical_entity_id"], candidates,
                "EXACT_MULTI_ATTRIBUTE")
    if len(candidates) > 1:
        return ("AMBIGUOUS", None, candidates, "EXACT_MULTI_ATTRIBUTE")
    return ("UNRESOLVED", None, [], "EXACT_MULTI_ATTRIBUTE")


def _cov_record_key(rec):
    """Immutable source key of one frozen record (its own key if present)."""
    return rec.get("immutable_source_key") or (
        f"{rec['document_sha256']}|{rec['source_event_id']}|{rec['occurrence_index']}")


def _cov_normalized_name(rec):
    """Normalised frozen person name, or None when it cannot be normalised."""
    try:
        return normalize_person_name(rec.get("entity_normalized")
                                     or rec.get("person_name"))
    except (TypeError, ValueError, AttributeError):
        return None


def _name_tokens(name):
    """Ordered lower-case word tokens of a person name (no fuzzy matching)."""
    return [token for token in re.findall(r"\w+", normalize_person_name(name))
            if token]


def _cov_target_name_matches(rec, canonical_name):
    """Fail-closed target guard (review #6, made strict in fix round 1).

    A frozen record may link only when BOTH the first and the last normalised
    token of its own person name appear in the canonical target's display
    name. One shared first name or surname (the three known-stale H1 targets)
    is not enough. A record without any frozen name cannot prove the target
    and stays fail-closed.
    """
    frozen_name = rec.get("person_name") or rec.get("entity_normalized")
    if not frozen_name:
        return False
    frozen_tokens = _name_tokens(frozen_name)
    canonical_tokens = set(_name_tokens(canonical_name))
    if not frozen_tokens or not canonical_tokens:
        return False
    return (frozen_tokens[0] in canonical_tokens
            and frozen_tokens[-1] in canonical_tokens)


def _load_cov_frozen(path):
    """Load the frozen H1 map and its NAME_FALLBACK index (read-only).

    Returns (by_key, by_name):
      by_key  immutable_source_key -> record (unchanged public shape)
      by_name (document_sha256, source_event_id, entity_normalized,
              role_type) -> [record, ...]

    The name index only holds records that carry the four key parts; a
    minimal record (key + canonical id only) stays positional-only. A record
    whose name/event cannot be normalised is skipped for the name index only
    and stays in by_key. The artefact (85 identities, immutable source-key
    v1; untracked, originally
    docs/cov-historical-person-source-implementation/h1_frozen.json) is keyed
    on the surrogate source_event_id of the DB it was frozen against. D-08:
    the path comes only from the caller or HEIMDAL_COV_H1_FROZEN; no path
    means the COV tier is off. A configured but unreadable file raises.
    """
    if not path:
        return {}, {}
    by_key = {}
    by_name: dict[tuple, list] = {}
    for rec in json.loads(pathlib.Path(path).read_text(encoding="utf-8")):
        by_key[_cov_record_key(rec)] = rec
        doc_sha = rec.get("document_sha256")
        evt = rec.get("source_event_id")
        role_type = rec.get("role_type")
        try:
            norm = _cov_normalized_name(rec)
            name_key = (doc_sha, int(evt), norm, role_type)
        except (TypeError, ValueError, AttributeError):
            continue
        if doc_sha is not None and evt is not None and norm and role_type:
            by_name.setdefault(name_key, []).append(rec)
    return by_key, by_name


def _load_cov_historical_frozen(path):
    """Load only the immutable-key map of the frozen H1 COV-HISTORICAL map."""
    return _load_cov_frozen(path)[0]


def build_entity_links(projection_run_id, *, projection_conn,
                       canonical_conn, link_code_version=LINK_CODE_VERSION,
                       ownership_generation_pin=None, ownership_build_meta_id=None,
                       cov_frozen_path=None):
    """Build entity_link rows for one projection generation (idempotent).

    Reads projection + canonical (read-only), writes ONLY
    registry_projection.entity_link, inside ONE transaction.
    v2 tiering: preserve v1 decisions first using legacy role map; only legacy
    UNRESOLVED may be resolved by normalized R-WS then strict R-ADDR.

    Determinism fix: R-ADDR loader is PINNED to an explicit ownership generation
    (registry_ownership.build_meta). The pin is resolved ONCE at build start
    (explicit pin or ACTIVE generation), never per-row MAX(latest). The pinned
    generation's relation/evidence snapshot is the only source seen by R-ADDR.
    Provenance ownership_generation_pin / build_meta_id / content_hash and
    source_attempt provenance are retained on every R-ADDR row.

    D-08: nothing is swallowed. An unresolvable ownership pin, a missing
    registry_ownership schema or any read error fails the build. The frozen
    COV-HISTORICAL map is read from cov_frozen_path (default env
    HEIMDAL_COV_H1_FROZEN); without one the COV tier is off (stats record
    COV_HISTORICAL_FROZEN_PATH).
    """
    identity_map = load_company_identity_map(canonical_conn)
    legacy_relations = _load_person_role_relations_legacy(canonical_conn)
    normalized_relations = load_person_role_relations(canonical_conn)
    person_names = load_person_display_names(canonical_conn)

    # Deterministic ownership generation pin resolved ONCE at build start
    ownership_pin_meta = _resolve_ownership_generation(
        canonical_conn, ownership_generation_pin=ownership_generation_pin,
        ownership_build_meta_id=ownership_build_meta_id)

    # R-ADDR global source map — PINNED to explicit generation
    raw_addr_map, raw_to_canonical, raw_to_label, raw_to_attempt_ids, raw_to_collection_ids = _load_raw_person_address_map(
        canonical_conn, ownership_pin_meta=ownership_pin_meta)

    # history raw_text by source_event_id — deterministic: smallest id wins for duplicate source_event_id
    # X3c: the same notice may hold several membership events, so also index
    # the raw text by (source_event_id, entity_key). The history entity_key is
    # the person-role entity_normalized (projection.py person-role INSERT);
    # smallest id wins here too.
    history_raw: dict = {}
    history_raw_by_role: dict = {}
    hrows = projection_conn.execute(
        "SELECT source_event_id, entity_key, raw_text FROM registry_projection.company_history_event WHERE projection_run_id = %s ORDER BY id ASC",
        (projection_run_id,),
    ).fetchall()
    for row in hrows:
        if isinstance(row, dict):
            sid=row["source_event_id"]; ekey=row["entity_key"]; rt=row["raw_text"]
        else:
            sid, ekey, rt=row
        if sid is not None and rt is not None:
            history_raw.setdefault(sid, rt)
            if ekey is not None:
                history_raw_by_role.setdefault((sid, ekey), rt)

    stats = {"COMPANY": {"LINKED": 0, "UNRESOLVED": 0, "AMBIGUOUS": 0},
             "PERSON": {"LINKED": 0, "UNRESOLVED": 0, "AMBIGUOUS": 0},
             "link_code_version": link_code_version,
             "R_WS_LINKED": 0,
             "R_WS_AMBIGUOUS": 0,
             "R_ADDR_ELIGIBLE": 0,
             "R_ADDR_ATTEMPTED": 0,
             "R_ADDR_LINKED": 0,
             "R_ADDR_AMBIGUOUS": 0,
             "R_ADDR_UNRESOLVED": 0,
             "R_ADDR_NO_ADDRESS": 0,
             "COV_HISTORICAL_ELIGIBLE": 0,
             "COV_HISTORICAL_ATTEMPTED": 0,
             "COV_HISTORICAL_LINKED": 0,
             "COV_HISTORICAL_FALLBACK_LINKED": 0,
             "COV_HISTORICAL_AMBIGUOUS": 0,
             "COV_HISTORICAL_TARGET_NAME_MISMATCH": 0,
             "COV_HISTORICAL_OUTSIDE_H1": 0}

    # COV-HISTORICAL v1.1 frozen H1 map (read-only artifact)
    cov_frozen_path = cov_frozen_path or os.environ.get("HEIMDAL_COV_H1_FROZEN")
    stats["COV_HISTORICAL_FROZEN_PATH"] = cov_frozen_path
    cov_frozen, cov_frozen_by_name = _load_cov_frozen(cov_frozen_path)
    # immutable frozen keys already linked in THIS build (positional or
    # fallback); the name fallback never links one record twice.
    cov_used_keys: set[str] = set()
    # Preload COV helpers: observation sha and parse structured for occurrence_index derivation
    cov_obs_sha: dict[int, str] = {}
    cov_parse_struct: dict[int, dict] = {}
    if cov_frozen:
        # observation sha / parse structured_data live in projection_conn's
        # DB (pdfhist_pilot); collect the ids the frozen map needs
        needed_obs = {int(v.get("source_observation_id")) for v in cov_frozen.values() if v.get("source_observation_id") is not None}
        needed_parse = {int(v.get("source_parse_result_id")) for v in cov_frozen.values() if v.get("source_parse_result_id") is not None}
        for oid in needed_obs:
            row = projection_conn.execute("SELECT sha256 FROM pdf_document_observation WHERE id=%s", (oid,)).fetchone()
            if row:
                sha = row["sha256"] if isinstance(row, dict) else row[0]
                if sha:
                    cov_obs_sha[int(oid)] = sha
        for pid in needed_parse:
            row = projection_conn.execute("SELECT structured_data FROM pdf_parse_result WHERE id=%s", (pid,)).fetchone()
            if row:
                sd = row["structured_data"] if isinstance(row, dict) else row[0]
                if isinstance(sd, str):
                    sd = json.loads(sd or "{}")
                cov_parse_struct[int(pid)] = sd or {}

    with projection_conn.transaction():
        # Hard guard: only a SHADOW run may be (re)built. ACTIVE is never
        # mutated; RETIRED and FAILED must stay exactly as they were, because
        # rollback_to() restores a RETIRED run (review #4).
        st = projection_conn.execute("SELECT status FROM registry_projection.projection_run WHERE projection_run_id=%s", (projection_run_id,)).fetchone()
        if st is None:
            raise ValueError(f"unknown projection_run_id {projection_run_id}")
        run_status = st["status"] if isinstance(st, dict) else st[0]
        if run_status != "SHADOW":
            raise RuntimeError(
                f"projection guard: refusing to mutate {run_status} "
                f"projection {projection_run_id} (only SHADOW may be built)")
        projection_conn.execute(
            "DELETE FROM registry_projection.entity_link "
            "WHERE projection_run_id = %s", (projection_run_id,))
        # record ownership generation pin onto projection_run (inside same tx, no schema migration)
        projection_conn.execute(
            "UPDATE registry_projection.projection_run SET metrics_json = "
            "COALESCE(metrics_json,'{}'::jsonb) || %s::jsonb "
            "WHERE projection_run_id=%s",
            (json.dumps({
                "ownership_generation_pin": ownership_pin_meta["generation_pin"],
                "ownership_build_meta_id": ownership_pin_meta["build_id"],
                "ownership_content_hash": ownership_pin_meta["content_hash"],
                "ownership_source_relation_max_id": ownership_pin_meta["source_relation_max_id"],
                "ownership_max_source_attempt_id": ownership_pin_meta["max_source_attempt_id"],
                "ownership_max_collection_run_id": ownership_pin_meta["max_collection_run_id"],
            }), projection_run_id))

        # ---- COMPANY: registration-number deterministic --------------------
        companies = projection_conn.execute(
            """SELECT company_id, registration_number
               FROM registry_projection.company_current_state
               WHERE projection_run_id = %s ORDER BY company_id""",
            (projection_run_id,)).fetchall()
        company_anchor = {}
        for row in companies:
            if isinstance(row, dict):
                company_id=row["company_id"]; regnr=row["registration_number"]
            else:
                company_id, regnr=row
            key = normalize_registration_number(regnr) if regnr else None
            status, canonical_id, candidates, method = match_company(
                key, identity_map)
            company_anchor[company_id] = canonical_id if status == "LINKED" else None
            projection_conn.execute(
                """INSERT INTO registry_projection.entity_link
                       (projection_run_id, entity_type, source_entity_key,
                        canonical_entity_id, registration_number,
                        person_role_id, link_status, link_method,
                        match_metadata, link_code_version)
                   VALUES (%s, 'COMPANY', %s, %s, %s, NULL, %s, %s, %s, %s)""",
                (projection_run_id, key or "", canonical_id, key,
                 status, method,
                 json.dumps({"registration_number_normalized": key,
                             "canonical_candidates": candidates},
                            ensure_ascii=False),
                 link_code_version))
            stats["COMPANY"][status] += 1

        # ---- PERSON: role-occurrence scoped, tiered policy ---------------
        roles = projection_conn.execute(
            """SELECT pr.id, pr.company_id, pr.role_type, pr.person_name,
                      pr.entity_normalized, pr.role_title, pr.source_scope,
                      pr.source_document_id, pr.source_event_id,
                      pr.source_observation_id, pr.source_parse_result_id
               FROM registry_projection.company_person_role pr
               WHERE pr.projection_run_id = %s ORDER BY pr.id""",
            (projection_run_id,)).fetchall()
        for row in roles:
            if isinstance(row, dict):
                role_id=row["id"]; company_id=row["company_id"]; role_type=row["role_type"]; person_name=row["person_name"]; entity_normalized=row["entity_normalized"]; role_title=row["role_title"]; source_scope=row["source_scope"]; source_document_id=row["source_document_id"]; source_event_id=row["source_event_id"]; source_observation_id=row.get("source_observation_id"); source_parse_result_id=row.get("source_parse_result_id")
            else:
                # 11-col row: id, company_id, role_type, person_name, entity_normalized, role_title, source_scope, source_document_id, source_event_id, source_observation_id, source_parse_result_id
                if len(row) == 11:
                    role_id, company_id, role_type, person_name, entity_normalized, role_title, source_scope, source_document_id, source_event_id, source_observation_id, source_parse_result_id = row
                else:
                    role_id, company_id, role_type, person_name, entity_normalized, role_title, source_scope, source_document_id, source_event_id = row
                    source_observation_id = None; source_parse_result_id = None
            normalized = normalize_person_name(entity_normalized or person_name)
            anchor = company_anchor.get(company_id)
            address_text_source = None

            # Tier 1: legacy v1 decision (preserved)
            legacy_status, legacy_cid, legacy_cands, legacy_method = match_person(
                normalized, role_type, anchor, legacy_relations, person_names)

            # Existing linked/ambiguous targets never reevaluated
            if legacy_status != "UNRESOLVED":
                status, canonical_id, candidates, method = legacy_status, legacy_cid, legacy_cands, legacy_method
                resolution_rule = None
                rule_version = None
                pdf_address_raw = None
                pdf_address_normalized = None
                pdf_tokens_for_meta = None
                raw_source_id = None
                raw_source_label = None
                raw_address_normalized = None
                address_match_mode = None
                raw_candidates_for_meta = None
            else:
                # Tier 2: R-WS (normalized key)
                ws_status, ws_cid, ws_cands, ws_method = match_person(
                    normalized, role_type, anchor, normalized_relations, person_names)
                if ws_status != "UNRESOLVED":
                    # R-WS resolved
                    if ws_status == "LINKED":
                        method = "EXACT_MULTI_ATTRIBUTE"
                        stats["R_WS_LINKED"] += 1
                    else:
                        method = "EXACT_MULTI_ATTRIBUTE"
                        stats["R_WS_AMBIGUOUS"] += 1
                    status, canonical_id, candidates = ws_status, ws_cid, ws_cands
                    resolution_rule = "R-WS"
                    rule_version = "v1.1"
                    pdf_address_raw = None
                    pdf_address_normalized = None
                    pdf_tokens_for_meta = None
                    raw_source_id = None
                    raw_source_label = None
                    raw_address_normalized = None
                    address_match_mode = None
                    raw_candidates_for_meta = None
                else:
                    # Tier 3: R-ADDR eligibility
                    eligible = (
                        role_type in ("BOARD", "MANAGEMENT")
                        and source_scope == "HISTORICAL_EVENT"
                        and anchor is not None
                        and bool(normalized)
                        and source_event_id is not None
                    )
                    if not eligible:
                        status, canonical_id, candidates, method = ws_status, ws_cid, ws_cands, ws_method
                        resolution_rule = None
                        rule_version = None
                        pdf_address_raw = None
                        pdf_address_normalized = None
                        pdf_tokens_for_meta = None
                        raw_source_id = None
                        raw_source_label = None
                        raw_address_normalized = None
                        address_match_mode = None
                        raw_candidates_for_meta = None
                    else:
                        stats["R_ADDR_ELIGIBLE"] += 1
                        # X3c: prefer the role's own membership event text
                        # (source_event_id + entity_normalized as stored);
                        # fall back to the notice's first event text only.
                        role_raw_text = (
                            history_raw_by_role.get(
                                (source_event_id, entity_normalized))
                            if entity_normalized is not None else None)
                        if role_raw_text:
                            raw_text = role_raw_text
                            address_text_source = "ROLE_EVENT"
                        else:
                            raw_text = history_raw.get(source_event_id)
                            address_text_source = ("FIRST_EVENT"
                                                   if raw_text else None)
                        if not raw_text:
                            stats["R_ADDR_NO_ADDRESS"] += 1
                            status, canonical_id, candidates, method = ws_status, ws_cid, ws_cands, ws_method
                            resolution_rule = None
                            rule_version = None
                            pdf_address_raw = None
                            pdf_address_normalized = None
                            pdf_tokens_for_meta = None
                            raw_source_id = None
                            raw_source_label = None
                            raw_address_normalized = None
                            address_match_mode = None
                            raw_candidates_for_meta = None
                        else:
                            extracted = _extract_pdf_address_with_raw(raw_text, normalized)
                            if extracted is None:
                                stats["R_ADDR_NO_ADDRESS"] += 1
                                status, canonical_id, candidates, method = ws_status, ws_cid, ws_cands, ws_method
                                resolution_rule = None
                                rule_version = None
                                pdf_address_raw = None
                                pdf_address_normalized = None
                                pdf_tokens_for_meta = None
                                raw_source_id = None
                                raw_source_label = None
                                raw_address_normalized = None
                                address_match_mode = None
                                raw_candidates_for_meta = None
                            else:
                                pdf_address_raw, pdf_tokens = extracted
                                pdf_tokens_for_meta = pdf_tokens
                                pdf_address_normalized = " ".join(pdf_tokens) if pdf_tokens else ""
                                stats["R_ADDR_ATTEMPTED"] += 1
                                # name tokens for strict matching
                                pdf_name_tokens = _tokenize_address(normalized)
                                matched_entries: list[tuple] = []
                                candidates_set: set[int] = set()
                                for key, info in raw_addr_map.items():
                                    src_name_tup, src_addr_tup = key
                                    if src_name_tup != tuple(pdf_name_tokens):
                                        continue
                                    if _address_match_strict(list(src_addr_tup), pdf_tokens):
                                        candidates_set.update(info["canonical_ids"])
                                        matched_entries.append((key, info))
                                has_duplicate_raw = any(len(info["raw_ids"]) > 1 for _, info in matched_entries)
                                # also fail closed if same key has multiple canonicals already in candidates_set
                                if not matched_entries:
                                    stats["R_ADDR_UNRESOLVED"] += 1
                                    status = "UNRESOLVED"
                                    canonical_id = None
                                    candidates = []
                                    method = "EXACT_MULTI_ATTRIBUTE"
                                    resolution_rule = "R-ADDR"
                                    rule_version = "v1.1"
                                    raw_source_id = None
                                    raw_source_label = None
                                    raw_address_normalized = None
                                    address_match_mode = "strict_identity_core"
                                    raw_candidates_for_meta = []
                                elif len(matched_entries) == 1 and len(candidates_set) == 1 and not has_duplicate_raw:
                                    stats["R_ADDR_LINKED"] += 1
                                    _, info = matched_entries[0]
                                    canonical_id = next(iter(candidates_set))
                                    candidates = [{"canonical_entity_id": canonical_id,
                                                   "display_name": person_names.get(canonical_id)}]
                                    status = "LINKED"
                                    method = "EXACT_MULTI_ATTRIBUTE"
                                    resolution_rule = "R-ADDR"
                                    rule_version = "v1.1"
                                    raw_source_id = next(iter(info["raw_ids"])) if info["raw_ids"] else None
                                    raw_source_label = info["labels"].get(raw_source_id) if raw_source_id else None
                                    raw_address_normalized = " ".join(info["addr_tokens"]) if info.get("addr_tokens") else ""
                                    address_match_mode = "strict_identity_core"
                                    raw_candidates_for_meta = matched_entries
                                else:
                                    stats["R_ADDR_AMBIGUOUS"] += 1
                                    all_cands = [{"canonical_entity_id": pid,
                                                  "display_name": person_names.get(pid)}
                                                 for pid in sorted(candidates_set)]
                                    status = "AMBIGUOUS"
                                    canonical_id = None
                                    candidates = all_cands
                                    method = "EXACT_MULTI_ATTRIBUTE"
                                    resolution_rule = "R-ADDR"
                                    rule_version = "v1.1"
                                    raw_source_id = None
                                    raw_source_label = None
                                    raw_address_normalized = None
                                    address_match_mode = "strict_identity_core"
                                    raw_candidates_for_meta = matched_entries

            # ---- COV-HISTORICAL v1.1 : direct coverage bridge for frozen H1 only ----
            if status == "UNRESOLVED" and cov_frozen and role_type in ("BOARD", "MANAGEMENT") and source_scope == "HISTORICAL_EVENT" and source_event_id is not None:
                stats["COV_HISTORICAL_ELIGIBLE"] += 1
                # Need doc sha
                _doc_sha = cov_obs_sha.get(int(source_observation_id)) if source_observation_id is not None else None
                if _doc_sha is None and source_observation_id is not None:
                    _row = projection_conn.execute("SELECT sha256 FROM pdf_document_observation WHERE id=%s", (int(source_observation_id),)).fetchone()
                    if _row:
                        _doc_sha = _row["sha256"] if isinstance(_row, dict) else _row[0]
                        if _doc_sha:
                            cov_obs_sha[int(source_observation_id)] = _doc_sha
                # Need structured_data
                _sd = cov_parse_struct.get(int(source_parse_result_id)) if source_parse_result_id is not None else None
                if _sd is None and source_parse_result_id is not None:
                    _row2 = projection_conn.execute("SELECT structured_data FROM pdf_parse_result WHERE id=%s", (int(source_parse_result_id),)).fetchone()
                    if _row2:
                        _sd_raw = _row2["structured_data"] if isinstance(_row2, dict) else _row2[0]
                        _sd = json.loads(_sd_raw or "{}") if isinstance(_sd_raw, str) else (_sd_raw or {})
                        cov_parse_struct[int(source_parse_result_id)] = _sd
                # occurrence_index = original position of the first change whose
                # entity_normalized equals this role's normalized name
                _occ_idx = None
                if _doc_sha and _sd is not None:
                    _occ_idx = next(
                        (_idx for _idx, _ch in enumerate(_sd.get("changes") or [])
                         if normalized and (_ch.get("entity_normalized") or _ch.get("entity") or "").strip().lower() == normalized),
                        None)
                _cov_key = cov_source_key(_doc_sha, int(source_event_id), int(_occ_idx)) if _occ_idx is not None else None
                _rec = cov_frozen.get(_cov_key) if _cov_key is not None else None
                if _rec is not None:
                    _frozen_name = (_rec.get("entity_normalized")
                                    or _rec.get("person_name"))
                    if _frozen_name and _cov_normalized_name(_rec) != normalized:
                        # the positional key names a different frozen person: a
                        # parser shift, not this role's record. Treat it as a
                        # miss. A minimal record without a name stays
                        # positional-only.
                        _rec = None
                _cov_match = "POSITIONAL" if _rec is not None else None
                _cov_ambiguous = False
                if _rec is None:
                    # Parser v2 drops a non-person change, shifting every later
                    # positional key in the same document/event. Fall back to
                    # the frozen name: link only a single, unused record.
                    _cands = []
                    if _doc_sha:
                        _cands = cov_frozen_by_name.get(
                            (_doc_sha, int(source_event_id), normalized,
                             role_type), [])
                    if len(_cands) > 1:
                        _cov_ambiguous = True
                    elif len(_cands) == 1:
                        _rec = _cands[0]
                        _cov_match = "NAME_FALLBACK"
                if _cov_ambiguous:
                    stats["COV_HISTORICAL_AMBIGUOUS"] += 1
                elif _rec is None:
                    stats["COV_HISTORICAL_OUTSIDE_H1"] += 1
                else:
                    stats["COV_HISTORICAL_ATTEMPTED"] += 1
                    _canonical = _rec.get("canonical_person_id")
                    _rec_key = _cov_record_key(_rec)
                    if _rec_key in cov_used_keys:
                        # a frozen record links at most once per build
                        # (positional and fallback both claim keys here)
                        stats["COV_HISTORICAL_AMBIGUOUS"] += 1
                    elif _canonical is None:
                        stats["COV_HISTORICAL_OUTSIDE_H1"] += 1
                    elif int(_canonical) not in person_names:
                        # fail closed: the frozen target is no canonical PERSON in
                        # this DB (D-08: this guard was a no-op before)
                        stats["COV_HISTORICAL_AMBIGUOUS"] += 1
                    elif not _cov_target_name_matches(
                            _rec, person_names.get(int(_canonical))):
                        # fail closed: the frozen source name and the canonical
                        # target's name share no token (review #6 stale target)
                        stats["COV_HISTORICAL_TARGET_NAME_MISMATCH"] += 1
                    else:
                        status = "LINKED"
                        canonical_id = int(_canonical)
                        candidates = [{"canonical_entity_id": canonical_id, "display_name": person_names.get(canonical_id)}]
                        method = "EXACT_MULTI_ATTRIBUTE"
                        resolution_rule = "COV-HISTORICAL-PERSON-SOURCE"
                        rule_version = "v1.1"
                        # preserve pdf address fields as None for COV
                        pdf_address_raw = None
                        pdf_address_normalized = None
                        pdf_tokens_for_meta = None
                        raw_source_id = None
                        raw_source_label = None
                        raw_address_normalized = None
                        address_match_mode = None
                        raw_candidates_for_meta = None
                        cov_used_keys.add(_rec_key)
                        stats["COV_HISTORICAL_LINKED"] += 1
                        if _cov_match == "NAME_FALLBACK":
                            stats["COV_HISTORICAL_FALLBACK_LINKED"] += 1
                        # COV-specific metadata extensions, injected below
                        _cov_document_sha256 = _doc_sha
                        _rec_occ = _rec.get("occurrence_index")
                        _cov_occurrence_index = (int(_rec_occ) if _rec_occ is not None
                                                 else (int(_occ_idx) if _occ_idx is not None else None))
                        _cov_immutable_key = _rec_key
                        _cov_canonical_provenance = _rec

            # Build metadata — keep base metadata, add full R-ADDR provenance when applicable
            metadata = {
                "source_person_name": person_name,
                "normalized_name": normalized,
                "role_type": role_type,
                "role_title": role_title,
                "source_scope": source_scope,
                "source_document_id": source_document_id,
                "source_event_id": source_event_id,
                "person_role_id": role_id,
                "company_canonical_entity_id": anchor,
                "company_link_status": "LINKED" if anchor else "NOT_LINKED",
                "canonical_candidates": candidates,
                "address_used": (resolution_rule == "R-ADDR"),
            }
            if resolution_rule is not None:
                metadata["resolution_rule"] = resolution_rule
                metadata["rule_version"] = rule_version
                metadata["pdf_person_role_id"] = role_id
                metadata["pdf_source_event_id"] = source_event_id
                metadata["pdf_source_document_id"] = source_document_id
                metadata["pdf_normalized_name"] = normalized
                if resolution_rule == "R-ADDR" and address_text_source is not None:
                    metadata["address_text_source"] = address_text_source
                if pdf_address_raw is not None:
                    metadata["pdf_address_raw"] = pdf_address_raw
                if pdf_address_normalized is not None:
                    metadata["pdf_address_normalized"] = pdf_address_normalized
                if pdf_tokens_for_meta is not None:
                    metadata["pdf_address_tokens"] = pdf_tokens_for_meta
                if raw_source_id is not None:
                    metadata["raw_source_entity_id"] = raw_source_id
                if raw_source_label is not None:
                    metadata["raw_source_label"] = raw_source_label
                if raw_address_normalized is not None:
                    metadata["raw_address_normalized"] = raw_address_normalized
                if address_match_mode is not None:
                    metadata["address_match_mode"] = address_match_mode
                # canonical provenance
                if canonical_id is not None:
                    metadata["canonical_person_id"] = canonical_id
                # for ambiguous, keep all candidates and raw identities
                if raw_candidates_for_meta is not None:
                    # store candidate raw ids and canonicals for audit
                    all_raw_ids = []
                    all_canonical = []
                    for _, info in raw_candidates_for_meta:
                        all_raw_ids.extend(sorted(info["raw_ids"]))
                        all_canonical.extend(sorted(info["canonical_ids"]))
                    if all_raw_ids:
                        metadata["candidate_raw_entity_ids"] = sorted(set(all_raw_ids))
                    if all_canonical:
                        # already in canonical_candidates but keep explicit
                        metadata["candidate_canonical_ids"] = sorted(set(all_canonical))
                # also keep method candidates
                if resolution_rule == "R-WS":
                    metadata["ws_normalized_key"] = normalized
                # COV-HISTORICAL provenance (isolated, not R-WS/R-ADDR)
                if resolution_rule == "COV-HISTORICAL-PERSON-SOURCE":
                    metadata["coverage_rule"] = "COV-HISTORICAL-PERSON-SOURCE"
                    metadata["rule_version"] = "v1.1"
                    metadata["source_key_version"] = "COV-HISTORICAL-SOURCE-KEY v1"
                    metadata["immutable_source_key"] = _cov_immutable_key
                    metadata["cov_match"] = _cov_match
                    metadata["document_sha256"] = _cov_document_sha256
                    metadata["occurrence_index"] = _cov_occurrence_index
                    metadata["canonical_person_id"] = canonical_id
                    metadata["canonical_mapping_provenance"] = _cov_canonical_provenance
                # R-ADDR generation provenance (determinism fix): explicit pin + source attempt
                if resolution_rule == "R-ADDR" and ownership_pin_meta is not None:
                    metadata["ownership_generation_pin"] = ownership_pin_meta["generation_pin"]
                    metadata["ownership_build_meta_id"] = ownership_pin_meta["build_id"]
                    metadata["ownership_content_hash"] = ownership_pin_meta["content_hash"]
                    metadata["ownership_source_relation_max_id"] = ownership_pin_meta["source_relation_max_id"]
                    metadata["ownership_max_source_attempt_id"] = ownership_pin_meta["max_source_attempt_id"]
                    metadata["ownership_max_collection_run_id"] = ownership_pin_meta["max_collection_run_id"]
                    # source attempt provenance: deterministic set for the raw identity/identities
                    attempt_ids_union: set[int] = set()
                    coll_ids_union: set[int] = set()
                    if raw_source_id is not None:
                        attempt_ids_union.update(raw_to_attempt_ids.get(int(raw_source_id), set()))
                        coll_ids_union.update(raw_to_collection_ids.get(int(raw_source_id), set()))
                    elif raw_candidates_for_meta:
                        for _, info in raw_candidates_for_meta:
                            for rid in info.get("raw_ids", set()):
                                attempt_ids_union.update(raw_to_attempt_ids.get(int(rid), set()))
                                coll_ids_union.update(raw_to_collection_ids.get(int(rid), set()))
                    if attempt_ids_union:
                        sorted_aids = sorted(attempt_ids_union)
                        # deterministic single + full set
                        metadata["source_attempt_id"] = sorted_aids[0]
                        metadata["source_attempt_ids"] = sorted_aids
                    if coll_ids_union:
                        sorted_cids = sorted(coll_ids_union)
                        metadata["source_collection_run_id"] = sorted_cids[0]
                        metadata["source_collection_run_ids"] = sorted_cids

            projection_conn.execute(
                """INSERT INTO registry_projection.entity_link
                       (projection_run_id, entity_type, source_entity_key,
                        canonical_entity_id, registration_number,
                        person_role_id, link_status, link_method,
                        match_metadata, link_code_version)
                   VALUES (%s, 'PERSON', %s, %s, NULL, %s, %s, %s, %s, %s)""",
                (projection_run_id, "ROLE:%s" % role_id, canonical_id, role_id,
                 status, method, json.dumps(metadata, ensure_ascii=False),
                 link_code_version))
            stats["PERSON"][status] += 1

        # Human tier last, same transaction (owner D10, audit E-5): the
        # owner's applied approvals survive every rebuild of the links.
        from .human_links import reapply_current_decisions
        human = reapply_current_decisions(projection_conn, projection_run_id,
                                          canonical_conn)
        if human is not None:
            stats["HUMAN_REVIEW"] = human

    _logger.info("entity links built for run %s: %s",
                 projection_run_id, stats)
    return stats


def link_row_checksum(conn, projection_run_id):
    """Stable sha256 over the ordered link rows (idempotency proof, section 51)."""
    import hashlib
    rows = conn.execute(
        """SELECT entity_type, source_entity_key, canonical_entity_id,
                  registration_number, person_role_id, link_status,
                  link_method, match_metadata::text, link_code_version
           FROM registry_projection.entity_link
           WHERE projection_run_id = %s
           ORDER BY entity_type, source_entity_key""",
        (projection_run_id,)).fetchall()
    digest = hashlib.sha256()
    for row in rows:
        digest.update(repr(row).encode("utf-8"))
    return digest.hexdigest()
