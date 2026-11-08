#!/usr/bin/env python3
"""Audit lane B check (2026-11): the canonical DB must be portal-runnable.

Read-only (session default_transaction_read_only=on, SELECT only). Asserts:
  B-1  every relation the portal/collector code reads exists (registry_ownership.*,
       registry_analytics.*, the public read views and tables);
  B-2  the ownership read views depend on registry_ownership.* only (pg_depend) and
       v_direct_ownership is non-empty. Catches the silent revert where re-running schema.sql + schema_read.sql after
       the Phase 1 cutover recreates empty public.canonical_relation & co and
       re-points the views at them (portal then shows 0 owners, no error).
  B-6  schema_pep.sql seeds no PEP record, v_pep_actors joins pep_record to a person
       by the identity link canonical_entity_id only (no name match), and no
       pep_record row lacks that link (owner D14).
  B-9  heimdal_reader is read-only in this DB: it can log in, holds SELECT on exactly
       registry_ownership.*, registry_analytics.* and the public relations the portal
       reads, no write privilege on any relation or sequence, no CREATE, and the role
       has default_transaction_read_only=on here.
  B-12 v_role_relation exposes plain columns (split by direction, no CASE).
  B-16 every FK of registry_ownership (and request_attempt.response_sha256) has a
       leading index.
  B-17 the canonical DDL ledger holds every sql/migrations file with its checksum.
Run: python3 tools/audit/check_b.py [DSN]   (default: $HEIMDAL_DB, else $HEIMDAL_READ_DB;
     an owner DSN: heimdal_reader itself may not read pep_record or the ledger)
Exit 1 while a finding is present. Live `heimdal` fails B-1 as of 2026-11-05.
"""
import os
import re
import sys
from pathlib import Path

import psycopg

REQUIRED = {
    "registry_ownership": ["relation", "indirect_ownership", "ownership_cycle_member",
                           "person_relation_context", "company_relation_context",
                           "relation_evidence_pointer", "build_meta"],
    "registry_analytics": ["analytics_build", "person_company_observation",
                           "person_company_period", "person_person_overlap", "person_group",
                           "person_group_member", "person_group_observation",
                           "person_group_observation_member"],
    "public": ["canonical_entity", "company_inventory", "entity_status", "request_attempt",
               "response_content", "collection_run", "enumeration_key", "input_parse_status",
               "geo_postcode", "company_location", "company_location_pdf_fetch",
               "entity_change_event", "user_watchlist", "pep_record",
               "v_entity", "v_direct_ownership", "v_control_registration", "v_role_relation",
               "v_source_anomaly", "v_provenance", "v_ownership_cycle_observations",
               "v_same_surname_share_observations", "v_structure_observations",
               "v_registration_patterns", "v_entity_change_feed", "v_pep_actors",
               "v_user_watchlist"],
}

dsn = sys.argv[1] if len(sys.argv) > 1 else (
    os.environ.get("HEIMDAL_DB") or os.environ.get("HEIMDAL_READ_DB", ""))
assert dsn, "pass a DSN or set HEIMDAL_DB / HEIMDAL_READ_DB"
c = psycopg.connect(dsn, autocommit=True)
c.execute("SET default_transaction_read_only = on")
missing = [f"{s}.{t}" for s, ts in REQUIRED.items() for t in ts
           if c.execute("SELECT to_regclass(%s)", (f"{s}.{t}",)).fetchone()[0] is None]
print("B-1 missing relations:", missing or "none")
assert not missing, f"B-1 FAIL: {len(missing)} required relations missing"
READ_VIEWS = ["v_direct_ownership", "v_control_registration", "v_role_relation",
              "v_source_anomaly", "v_ownership_cycle_observations"]
bad = c.execute("""SELECT DISTINCT v.relname || ' -> ' || tn.nspname || '.' || t.relname
    FROM pg_depend d JOIN pg_rewrite r ON r.oid = d.objid JOIN pg_class v ON v.oid = r.ev_class
    JOIN pg_class t ON t.oid = d.refobjid JOIN pg_namespace tn ON tn.oid = t.relnamespace
    WHERE v.relname = ANY(%s) AND t.relkind = 'r' AND t.relname IN ('canonical_relation',
      'ownership_cycle_member', 'canonical_relation_legacy', 'ownership_cycle_member_legacy')
      AND tn.nspname <> 'registry_ownership'""", (READ_VIEWS,)).fetchall()
n = c.execute("SELECT count(*) FROM public.v_direct_ownership").fetchone()[0]
print("B-2 read views on a non-registry_ownership source:", [b[0] for b in bad] or "none",
      f"| v_direct_ownership rows={n}")
assert not bad and n > 0, "B-2 FAIL: read views not wired to populated registry_ownership"
ddl = re.sub(r"--[^\n]*", "", (Path(__file__).resolve().parents[2] / "schema_pep.sql").read_text())
seeded = bool(re.search(r"\bINSERT\b", ddl, re.I))
join = c.execute("SELECT pg_get_viewdef('public.v_pep_actors')").fetchone()[0].split("FROM", 1)[1]
name_join = bool(re.search(r"~~|display_name|person_name", join)) or "pr.canonical_entity_id" not in join
linked = c.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public'"
                   " AND table_name = 'pep_record' AND column_name = 'canonical_entity_id'").fetchone()[0]
unlinked = c.execute("SELECT count(*) FROM pep_record" + (" WHERE canonical_entity_id IS NULL"
                                                          if linked else "")).fetchone()[0]
print(f"B-6 schema_pep.sql seeds rows: {seeded} | v_pep_actors name join: {name_join}"
      f" | pep_record rows without identity link: {unlinked}")
assert not (seeded or name_join or unlinked), "B-6 FAIL: PEP records can flag a person by name"

# B-9 -- the public relations the portal reads (sql/migrations/0007_heimdal_reader.sql).
PORTAL_PUBLIC = {"canonical_entity", "company_inventory", "entity_status", "collection_run",
                 "geo_postcode", "company_location", "entity_change_event", "user_watchlist",
                 *(t for t in REQUIRED["public"] if t.startswith("v_"))}
SCHEMAS = ["public", "registry_ownership", "registry_analytics"]
role = c.execute("SELECT rolcanlogin, rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls"
                 " FROM pg_roles WHERE rolname = 'heimdal_reader'").fetchone()
setting = c.execute("SELECT count(*) FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole"
                    " JOIN pg_database d ON d.oid = s.setdatabase WHERE r.rolname = 'heimdal_reader'"
                    " AND d.datname = current_database()"
                    " AND 'default_transaction_read_only=on' = ANY(s.setconfig)").fetchone()[0]
rels = c.execute("""SELECT n.nspname, cl.relname, cl.relkind,
        has_table_privilege('heimdal_reader', cl.oid, 'SELECT'),
        cl.relkind <> 'S' AND has_table_privilege('heimdal_reader', cl.oid,
                                                  'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),
        cl.relkind = 'S' AND has_sequence_privilege('heimdal_reader', cl.oid, 'USAGE,UPDATE')
    FROM pg_class cl JOIN pg_namespace n ON n.oid = cl.relnamespace
    WHERE n.nspname = ANY(%s) AND cl.relkind IN ('r', 'v', 'm', 'p', 'S')""", (SCHEMAS,)).fetchall() \
    if role else []
readable = {f"{s}.{t}" for s, t, k, sel, _, _ in rels if sel}
expected = {f"{s}.{t}" for s, t, k, _, _, _ in rels
            if k != "S" and (s != "public" or t in PORTAL_PUBLIC)}
writable = sorted(f"{s}.{t}" for s, t, _, _, w, q in rels if w or q)
create = [s for s in SCHEMAS if role and c.execute(
    "SELECT has_schema_privilege('heimdal_reader', %s, 'CREATE')", (s,)).fetchone()[0]]
b9 = [] if role else ["role heimdal_reader missing"]
if role and (not role[0] or role[1]):
    b9.append("heimdal_reader cannot log in or holds an admin attribute")
if role and not setting:
    b9.append("no default_transaction_read_only=on for heimdal_reader in this DB")
if readable != expected:
    b9.append(f"SELECT drift: missing {sorted(expected - readable)}, extra {sorted(readable - expected)}")
if writable or create:
    b9.append(f"write privileges: {writable[:8]} CREATE on {create}")
print("B-9 heimdal_reader:", b9 or f"read-only, SELECT on {len(readable)} relations")
assert not b9, "B-9 FAIL: heimdal_reader is not a working read-only role"

# B-12 -- v_role_relation split by direction, so person_id/company_id lookups use indexes.
case = "CASE" in c.execute("SELECT pg_get_viewdef('public.v_role_relation')").fetchone()[0].upper()
print("B-12 v_role_relation derives its columns with CASE:", case)
assert not case, "B-12 FAIL: v_role_relation is not split by direction (sql/migrations/0008)"

# B-16 -- FK columns without a leading index.
noidx = [r[0] for r in c.execute("""SELECT con.conrelid::regclass || '.' || con.conname
    FROM pg_constraint con WHERE con.contype = 'f'
      AND (con.connamespace = 'registry_ownership'::regnamespace
           OR con.conrelid = 'public.request_attempt'::regclass)
      AND NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = con.conrelid
            AND (i.indkey::int2[])[0:cardinality(con.conkey) - 1] @> con.conkey
            AND (i.indkey::int2[])[0:cardinality(con.conkey) - 1] <@ con.conkey)""").fetchall()]
print("B-16 FKs without a leading index:", noidx or "none")
assert not noidx, "B-16 FAIL: FK columns without a leading index"

# B-17 -- the canonical DDL ledger is at the repo's head.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from heimdal.canonical_migrations import FILES  # noqa: E402
from heimdal.pdfhist.migrations import checksum_of  # noqa: E402
want = {name: checksum_of(path) for name, path in FILES}
got = dict(c.execute("SELECT name, checksum FROM schema_migrations").fetchall()) \
    if c.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0] else {}
behind = sorted(n for n in want if got.get(n) != want[n])
print("B-17 ledger entries missing or changed:", behind or "none")
assert not behind, "B-17 FAIL: run python3 -m heimdal.canonical_migrations --apply"
print("check_b: OK")
