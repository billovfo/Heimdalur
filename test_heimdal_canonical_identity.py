#!/usr/bin/env python3
"""WP-C5 checks: canonical entity identity.

Covers the WP-C5 acceptance criteria against the live database (the frozen
4,527 get-table bodies from WP-C1):

  * every entity observed in any body (fromId/toId endpoints) has exactly one
    canonical_entity row — no missing, no orphan duplicates per source ID;
  * typing per SoT §3.1/§13: the parsed type token from the label's own text
    is primary; UNKNOWN_ENTITY itemization is empty and the token map is
    exhaustive over the live token set (no guess ever entered);
  * both person spellings ("Persónur", "Natural Person") type as PERSON;
  * address privacy: a PERSON's display_name is the parsed name only, never
    the raw label (which carries a private home address) — the structural
    fallback also never writes a raw label;
  * append-only idempotence: a re-run of assign_canonical_ids leaves every
    existing canonical ID unchanged;
  * same-name collisions are listed with distinguishing attributes and are
    never merged (distinct pkCbi always keep distinct canonical IDs);
  * entity_kind is never derived from a relation type (REGISTERED_CONTROL_ACTOR
    spans PERSON and COMPANY).

Run: python3 test_heimdal_canonical_identity.py
"""
import json
import re

import psycopg

from heimdal.pdfhist.db import qa_dsn
from heimdal_collect import (ENTITY_KIND_MAP, PERSON_TYPE_TEXTS,
                             assign_canonical_ids, entity_kind, normalize_name,
                             parse_table_name)

INPUT_SET_JOIN = (
    " FROM request_attempt ra"
    " JOIN company_inventory ci ON ci.source_entity_id = ra.requested_source_entity_id"
    " JOIN response_content rc ON rc.response_sha256 = ra.response_sha256"
    " WHERE ra.endpoint='get-table' AND ra.http_status=200"
    " AND ci.inventory_status='FETCHED'")

# Frozen with the input set (WP-C1, reconciled 2026-08-14; re-measured on the
# recovered clone, see tests_baseline.py): canonical_entity holds the observed
# fromId/toId endpoints plus the inventory-discovered companies that never
# appear as a relation endpoint (4 FETCHED with an empty body, 1 RETRYABLE_ERROR).
from tests_baseline import (CANONICAL_ROWS, COLLISION_ENTITIES,  # noqa: E402
                            COLLISION_GROUPS, NATURAL_PERSON_ROWS,
                            OBSERVED_ENTITIES, PERSON_ROWS)

# Address markers that must never appear in a PERSON display_name (they do
# appear in raw labels, e.g. "Anna Testsen - Persónur, Testgøta 1
# Tórshavn Føroyar").
_ADDR = re.compile(
    r"(Flat |Box |c/o |England|Denmark|Danmark|London|København|Reykjavík|"
    r"street|Road|Avenue|gøta\s+\d+|vegur\s+\d+|\d{4}\s+[A-Z])", re.I)


def _db():
    return psycopg.connect(qa_dsn())


def _observed_endpoints(conn):
    """Every fromId/toId endpoint across the frozen input set."""
    observed = set()
    for (raw,) in conn.execute(
            "SELECT rc.response_bytes" + INPUT_SET_JOIN).fetchall():
        for e in json.loads(bytes(raw)):
            observed.add(int(e["fromId"]))
            observed.add(int(e["toId"]))
    return observed


def test_coverage_no_missing_no_orphan_duplicates():
    with _db() as conn:
        observed = _observed_endpoints(conn)
        canonical = conn.execute(
            "SELECT source_entity_id, canonical_entity_id FROM canonical_entity"
            " ORDER BY source_entity_id").fetchall()
    assert len(observed) == OBSERVED_ENTITIES, len(observed)
    assert len(canonical) == CANONICAL_ROWS, len(canonical)

    ids = [eid for eid, _ in canonical]
    missing = sorted(observed - set(ids))
    assert not missing, f"observed entities without a canonical row: {missing[:10]}"

    # one source ID -> at most one canonical row (orphan duplicates impossible)
    seen = set()
    dup = [eid for eid in ids if eid in seen or seen.add(eid)]
    assert not dup, f"source IDs with >1 canonical row: {dup[:10]}"

    # the 5 extra rows beyond observed endpoints are all inventory-discovered
    # companies (search display labels), never relation endpoints
    extras = sorted(set(ids) - observed)
    assert len(extras) == CANONICAL_ROWS - OBSERVED_ENTITIES, extras
    with _db() as conn:
        kinds = conn.execute(
            "SELECT source_entity_id, entity_kind FROM canonical_entity"
            " WHERE source_entity_id = ANY(%s) ORDER BY 1", (extras,)).fetchall()
    assert all(k == "COMPANY" for _, k in kinds)


def test_two_person_spellings_type_as_person():
    assert PERSON_TYPE_TEXTS == {"Persónur", "Natural Person"}
    assert entity_kind("Persónur") == "PERSON"
    assert entity_kind("Natural Person") == "PERSON"
    with _db() as conn:
        rows = conn.execute(
            "SELECT raw_type_token, count(*) FROM canonical_entity"
            " WHERE entity_kind='PERSON' GROUP BY 1 ORDER BY 1").fetchall()
        n = conn.execute("SELECT count(*) FROM canonical_entity"
                         " WHERE entity_kind='PERSON'").fetchone()[0]
    assert dict(rows) == {"Persónur": PERSON_ROWS - NATURAL_PERSON_ROWS,
                          "Natural Person": NATURAL_PERSON_ROWS}
    assert n == PERSON_ROWS


def test_unknown_entity_and_conflict_itemization_is_empty():
    with _db() as conn:
        unknown = conn.execute("SELECT count(*) FROM canonical_entity"
                               " WHERE entity_kind='UNKNOWN_ENTITY'").fetchone()[0]
        null_token = conn.execute("SELECT count(*) FROM canonical_entity"
                                  " WHERE raw_type_token IS NULL").fetchone()[0]
        conflicts = conn.execute("SELECT count(*) FROM company_inventory"
                                 " WHERE typing_status='CONFLICT'").fetchone()[0]
        inv_unknown = conn.execute("SELECT count(*) FROM company_inventory"
                                   " WHERE typing_status='UNKNOWN_ENTITY'").fetchone()[0]
        tokens = conn.execute("SELECT DISTINCT raw_type_token FROM canonical_entity").fetchall()
    assert unknown == 0
    assert null_token == 0
    assert conflicts == 0
    assert inv_unknown == 0
    live_tokens = {t for (t,) in tokens}
    assert live_tokens <= set(ENTITY_KIND_MAP), live_tokens - set(ENTITY_KIND_MAP)
    assert all(entity_kind(t) != "UNKNOWN_ENTITY" for t in live_tokens)
    # parse failure -> UNKNOWN_ENTITY, never a guess
    assert entity_kind(None) == "UNKNOWN_ENTITY"
    assert entity_kind("never-observed token") == "UNKNOWN_ENTITY"


def test_append_only_rerun_leaves_all_ids_unchanged():
    with _db() as conn:
        before = dict(conn.execute(
            "SELECT source_entity_id, canonical_entity_id FROM canonical_entity").fetchall())
        assign_canonical_ids(conn)
        after = dict(conn.execute(
            "SELECT source_entity_id, canonical_entity_id FROM canonical_entity").fetchall())
    assert len(before) == CANONICAL_ROWS
    assert before == after, "re-run reassigned existing canonical IDs — append-only violated"
    assert len(after) == CANONICAL_ROWS


def test_person_display_name_is_parsed_name_never_raw_label():
    with _db() as conn:
        people = conn.execute(
            "SELECT source_entity_id, display_name FROM canonical_entity"
            " WHERE entity_kind='PERSON' ORDER BY source_entity_id").fetchall()
    assert len(people) == PERSON_ROWS
    leaky = [d for _, d in people if _ADDR.search(d or "")]
    assert not leaky, f"PERSON display_name carries an address: {leaky[:5]}"

    # every display_name is exactly the name parsed from that entity's own raw
    # label — the address portion never reaches the display field
    mismatches = []
    with _db() as conn:
        for eid, display in people:
            raw = conn.execute(
                "SELECT raw_from_source_entity_label FROM registry_ownership.relation"
                " WHERE raw_from_source_entity_id=%s"
                " AND raw_from_source_entity_label LIKE '%% - %%' LIMIT 1",
                (eid,)).fetchone()
            if raw:
                parsed = parse_table_name(raw[0])
                if parsed and parsed[0] != display:
                    mismatches.append((eid, parsed[0], display))
    assert not mismatches, f"display_name != parsed name: {mismatches[:5]}"


def test_collision_groups_never_merged_never_hidden():
    with _db() as conn:
        rows = conn.execute(
            "SELECT source_entity_id, canonical_entity_id, entity_kind, display_name"
            " FROM canonical_entity").fetchall()
        multi = conn.execute(
            "SELECT canonical_entity_id FROM canonical_entity"
            " GROUP BY 1 HAVING count(*) > 1").fetchall()
    assert not multi, "two source IDs share a canonical ID — a merge happened"

    groups = {}
    for eid, cid, kind, name in rows:
        groups.setdefault(normalize_name(name), []).append((eid, cid, kind, name))
    collisions = {n: m for n, m in groups.items() if len({x[0] for x in m}) > 1}
    assert len(collisions) == COLLISION_GROUPS, len(collisions)
    n_rows = sum(len(m) for m in collisions.values())
    assert n_rows == COLLISION_ENTITIES, n_rows
    # each collision member is its own canonical entity (distinct IDs, never
    # hidden, never merged) and lists its distinguishing kind
    for norm, members in collisions.items():
        cids = {m[1] for m in members}
        assert len(cids) == len(members), norm
        assert any(m[2] != members[0][2] or m[0] != members[0][0]
                   for m in members[1:])


def test_kind_never_derived_from_relation_type():
    with _db() as conn:
        actor_kinds = conn.execute(
            "SELECT DISTINCT ce.entity_kind FROM registry_ownership.relation cr"
            " JOIN canonical_entity ce ON ce.source_entity_id = cr.canonical_subject_entity_id"
            " WHERE cr.canonical_relation_type='REGISTERED_CONTROL_ACTOR'").fetchall()
        kind_tokens = conn.execute(
            "SELECT entity_kind, count(DISTINCT source_entity_id) FROM canonical_entity"
            " GROUP BY 1 ORDER BY 1").fetchall()
    assert {"PERSON", "COMPANY"} <= {k for (k,) in actor_kinds}
    # the kind distribution matches label-token typing only, no relation type
    # ever influences canonical_entity.entity_kind
    assert dict(kind_tokens) == {"COMPANY": 4680, "ORGANIZATION": 39,
                                 "PERSON": PERSON_ROWS, "PUBLIC_AUTHORITY": 4}


if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} checks passed")
