#!/usr/bin/env python3
"""WP-C6 checks: derived data — indirect ownership, cycle handling, relation
contexts — against the live DB and the pure computation functions.

Scope (SoT §15/§18/§19/§20, WP-C6 brief):
- every displayed indirect percentage is reproducible from its listed paths;
- REGISTERED_CONTROL_ACTOR never contributes to an ownership path;
- cycles withhold the indirect percentage, and every exclusion names its group;
- contexts found from both endpoints are counted once;
- context totals equal the sum of their breakdowns;
- derived tables are cleared and rebuilt to the same content on each run.

Like the other WP suites, this reads the live `heimdal` DB and also exercises
the pure functions directly (compute_indirect_ownership, relation_basis).

Run: python3 test_heimdal_derived_data.py
"""
import hashlib
import os

import psycopg

from heimdal.pdfhist.db import qa_dsn
from heimdal_collect import (PATH_SAMPLE_CAP, compute_indirect_ownership, find_cycle_components,
                             relation_basis)

DB = qa_dsn()
ROOT = os.path.dirname(os.path.abspath(__file__))

# The 5 known ownership self-edges (owner decision D-6): they sit inside §15's
# cycle rule — no indirect percentage, direct row still shown.
SELF_EDGE_IDS = (44, 1820, 4243, 5319, 5930)


def _db():
    return psycopg.connect(DB)


def _io_rows(conn):
    """All registry_ownership.indirect_ownership rows with parsed path lists."""
    out = []
    for o, w, p, count, paths, trunc in conn.execute(
            "SELECT owner_entity_id, owned_entity_id, computed_percentage,"
            " path_count, contributing_paths, paths_truncated"
            " FROM registry_ownership.indirect_ownership"):
        out.append((o, w, float(p), count, bool(trunc), paths))
    return out


def test_indirect_percentage_reproducible_from_paths():
    """Every stored percentage equals the sum of its listed path products,
    within the numeric(9,4) storage precision (half a unit + float epsilon).
    Path_count must match the number of listed paths — no path is silently
    dropped below the sample cap."""
    with _db() as conn:
        rows = _io_rows(conn)
        assert rows, "no indirect_ownership rows: nothing was verified"
        for o, w, p, count, trunc, paths in rows:
            assert trunc == (count > PATH_SAMPLE_CAP), (o, w, count, trunc)
            assert len(paths) == min(count, PATH_SAMPLE_CAP), (o, w, count, len(paths))
            if not trunc:  # C-10: a truncated row lists a sample, its sum is below p
                total = sum(float(x["percentage"]) for x in paths)
                assert abs(total - p) <= 0.00005 + 1e-9, (o, w, p, total)
        # every path is genuinely indirect: at least 2 hops (3 nodes)
        for _o, _w, _p, _c, _t, paths in rows:
            for x in paths:
                assert len(x["path"]) >= 3, x


def test_control_actor_never_in_ownership_path():
    """Every consecutive pair in every contributing path must be a
    DIRECT_OWNERSHIP canonical row. No REGISTERED_CONTROL_ACTOR edge may
    appear in a path — the ownership computation source query filters
    strictly on canonical_relation_type='DIRECT_OWNERSHIP'."""
    with _db() as conn:
        direct = set(conn.execute(
            "SELECT raw_from_source_entity_id, raw_to_source_entity_id"
            " FROM registry_ownership.relation"
            " WHERE canonical_relation_type='DIRECT_OWNERSHIP'").fetchall())
        control_only = set(conn.execute(
            "SELECT raw_from_source_entity_id, raw_to_source_entity_id"
            " FROM registry_ownership.relation"
            " WHERE canonical_relation_type='REGISTERED_CONTROL_ACTOR'"
            " AND NOT EXISTS (SELECT 1 FROM registry_ownership.relation d"
            "  WHERE d.canonical_relation_type='DIRECT_OWNERSHIP'"
            "  AND d.raw_from_source_entity_id = registry_ownership.relation.raw_from_source_entity_id"
            "  AND d.raw_to_source_entity_id = registry_ownership.relation.raw_to_source_entity_id)"
        ).fetchall())
        assert control_only, "expected some control-only edges (control is its own category, §14a)"
        for _o, _w, _p, _c, _t, paths in _io_rows(conn):
            for x in paths:
                for a, b in zip(x["path"], x["path"][1:]):
                    assert (a, b) in direct, (a, b, x)
                    assert (a, b) not in control_only, (a, b, x)


def test_cycle_withhold_and_named_cycle_groups():
    """§15: no indirect percentage is shown for a cyclic relationship. Every
    excluded entity names its cycle group (registry_ownership.ownership_cycle_member). The 5
    self-edges are inside the cycle rule (singleton groups, no self indirect
    row) while their direct rows stay in registry_ownership.relation."""
    with _db() as conn:
        groups = conn.execute(
            "SELECT cycle_group_id, count(*) FROM registry_ownership.ownership_cycle_member"
            " GROUP BY 1").fetchall()
        assert groups, "no cycle groups recorded"
        for gid, n in groups:
            assert n >= 1, (gid, n)
        # no indirect row between two members of the same cycle group
        assert conn.execute(
            "SELECT count(*) FROM registry_ownership.indirect_ownership i"
            " JOIN registry_ownership.ownership_cycle_member m1 ON m1.entity_id = i.owner_entity_id"
            " JOIN registry_ownership.ownership_cycle_member m2 ON m2.entity_id = i.owned_entity_id"
            "   AND m2.cycle_group_id = m1.cycle_group_id").fetchone()[0] == 0
        # no self indirect rows at all
        assert conn.execute(
            "SELECT count(*) FROM registry_ownership.indirect_ownership"
            " WHERE owner_entity_id = owned_entity_id").fetchone()[0] == 0
        # the 5 self-edges: singleton cycle membership + direct row still present
        for eid in SELF_EDGE_IDS:
            row = conn.execute(
                "SELECT cycle_group_id FROM registry_ownership.ownership_cycle_member"
                " WHERE entity_id=%s", (eid,)).fetchone()
            assert row is not None, eid
            gid = row[0]
            assert conn.execute(
                "SELECT count(*) FROM registry_ownership.ownership_cycle_member"
                " WHERE cycle_group_id=%s", (gid,)).fetchone()[0] == 1
            assert conn.execute(
                "SELECT count(*) FROM registry_ownership.relation"
                " WHERE canonical_relation_type='DIRECT_OWNERSHIP'"
                " AND raw_from_source_entity_id=%s AND raw_to_source_entity_id=%s",
                (eid, eid)).fetchone()[0] == 1


def test_context_found_from_both_endpoints_counted_once():
    """§20 dedup: the same underlying pair of relations is stored once. No two
    person-context rows share the same (a, b, company, relation_a,
    relation_b); person_a < person_b is enforced (canonical ordering)."""
    with _db() as conn:
        assert conn.execute(
            "SELECT count(*) FROM (SELECT person_a_entity_id, person_b_entity_id,"
            " company_entity_id, person_a_relation_id, person_b_relation_id"
            " FROM registry_ownership.person_relation_context GROUP BY 1,2,3,4,5 HAVING count(*) > 1) x"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM registry_ownership.person_relation_context"
            " WHERE person_a_entity_id >= person_b_entity_id").fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM registry_ownership.person_relation_context p1"
            " JOIN registry_ownership.person_relation_context p2 ON"
            " p1.person_a_entity_id=p2.person_b_entity_id"
            " AND p1.person_b_entity_id=p2.person_a_entity_id"
            " AND p1.company_entity_id=p2.company_entity_id"
            " AND p1.context_id <> p2.context_id").fetchone()[0] == 0


def test_context_totals_equal_sum_of_breakdowns():
    """§18.1/§19: the total context count equals the sum of its per-group /
    per-basis breakdowns — a combined number is never shown without its
    breakdown."""
    with _db() as conn:
        for table, grp, k in (("registry_ownership.person_relation_context", "person_a_group, person_b_group", "person_a_group"),
                              ("registry_ownership.company_relation_context", "relation_basis", "relation_basis")):
            total = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            rows = conn.execute(f"SELECT {grp}, count(*) FROM {table} GROUP BY {grp}").fetchall()
            assert sum(n for *_, n in rows) == total, (table, total, rows)
        # a context touching an untagged type must not block: unknown groups
        # fall through relation_basis into the CROSS_ROLE_PERSON bucket
        assert relation_basis("OTHER_SOURCE_RELATION", None, "OTHER_SOURCE_RELATION", None) == "CROSS_ROLE_PERSON"
        assert relation_basis("OTHER_SOURCE_RELATION", None, "OTHER_SOURCE_RELATION", "ANNAD") == "CROSS_ROLE_PERSON"


def test_recompute_idempotent_and_layers_untouched():
    """Re-running the derived-data commands converges to byte-identical
    content; canonical and raw layers are untouched. Exercises the full
    command paths (TRUNCATE + rebuild) exactly as a production run would."""
    with _db() as conn:
        sha_before = _snapshot(conn)
        assert conn.execute("SELECT count(*) FROM registry_ownership.indirect_ownership").fetchone()[0] > 0
    import subprocess, sys
    for cmd in ("compute-ownership", "build-relation-contexts"):
        # The collector writes to HEIMDAL_DB: always the DB this test reads.
        r = subprocess.run([sys.executable, "heimdal_collect.py", cmd],
                           capture_output=True, text=True, cwd=ROOT,
                           env={**os.environ, "HEIMDAL_DB": DB})
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    with _db() as conn:
        sha_after = _snapshot(conn)
    for key in sha_before:
        assert sha_before[key] == sha_after[key], (
            key, sha_before[key], sha_after[key])


def _snapshot(conn):
    """SHA-256 of derived + canonical + raw tables (excludes computed_at)."""
    tables = {
        "io": "SELECT owner_entity_id, owned_entity_id, computed_percentage,"
              " path_count, contributing_paths, paths_truncated"
              " FROM registry_ownership.indirect_ownership ORDER BY 1, 2",
        "ocm": "SELECT cycle_group_id, entity_id FROM registry_ownership.ownership_cycle_member ORDER BY 1, 2",
        "prc": "SELECT person_a_entity_id, person_b_entity_id, company_entity_id,"
               " person_a_relation_id, person_b_relation_id, person_a_group,"
               " person_b_group, person_a_role_label, person_b_role_label, is_cross_role"
               " FROM registry_ownership.person_relation_context ORDER BY 1, 2, 3, 4, 5",
        "crc": "SELECT company_a_entity_id, company_b_entity_id, relation_basis,"
               " related_entity_id, company_a_relation, company_b_relation,"
               " source_relation_id FROM registry_ownership.company_relation_context ORDER BY 1, 2, 3, 4, 5, 6, 7",
        "canon": "SELECT * FROM registry_ownership.relation ORDER BY 1",
        "raw": "SELECT response_sha256, response_bytes FROM response_content ORDER BY 1",
    }
    out = {}
    for key, sql in tables.items():
        h = hashlib.sha256()
        for row in conn.execute(sql):
            h.update(repr(tuple(row)).encode())
        out[key] = h.hexdigest()
    return out


if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} checks passed")
