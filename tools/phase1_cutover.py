#!/usr/bin/env python3
"""HEIMDAL Phase 1 — Ownership source separation / shadow cutover.

Creates registry_ownership, shadow-populates it from public (preserving
relation_id exactly via OVERRIDING SYSTEM VALUE), reconciles, retargets the
ownership views, renames public ownership tables to *_legacy.

Staged + fail-fast: any reconciliation mismatch aborts before destructive steps.
Nothing writes to pdfhist_pilot. canonical_entity/entity_status/entity_change_event/
request_attempt/response_content/collection_run remain public.
"""
import json, os, sys, datetime, psycopg
from psycopg import sql
os.environ["PGTZ"] = "UTC"  # B-8: hashes serialise timestamptz::text, so pin the session TZ

TS = datetime.datetime.now(datetime.UTC).isoformat()
R = {"started_at": TS, "stages": {}}
DB = os.environ.get("HEIMDAL_DB") or sys.exit("HEIMDAL_DB is not set (no default: a forgotten variable must never reach a live DB)")
# B-13: the stage record goes to a NEW file; tools/phase1_db_stages.json is the 2026-09 evidence.
OUT = os.environ.get("PHASE1_STAGES_JSON", "tools/phase1_db_stages.json")
if os.path.exists(OUT):
    sys.exit(f"{OUT} exists (tracked evidence is never overwritten): set PHASE1_STAGES_JSON to a new path")

OWN_TABLES = ["canonical_relation", "indirect_ownership", "ownership_cycle_member",
              "person_relation_context", "company_relation_context"]
REL_VIEWS = ["v_direct_ownership", "v_control_registration", "v_role_relation", "v_source_anomaly"]
CYCLE_VIEW = "v_ownership_cycle_observations"

def stage(name):
    def deco(fn):
        def wrapped(*a, **k):
            print(f"\n=== STAGE {name} ===", flush=True)
            out = fn(*a, **k)
            R["stages"][name] = out if out is not None else {"ok": True}
            return out
        return wrapped
    return deco

def hash_relation(c, qualified):
    cols = ("relation_id, raw_from_source_entity_id, raw_from_source_entity_label,"
            " raw_to_source_entity_id, raw_to_source_entity_label, raw_source_relation_type_id,"
            " raw_source_relation_label, canonical_relation_type,"
            " coalesce(canonical_subject_entity_id::text,''), coalesce(canonical_object_entity_id::text,''),"
            " semantic_direction_confidence, coalesce(raw_percentage_label,''),"
            " coalesce(parsed_percentage::text,''), coalesce(percentage_parse_status,''),"
            " coalesce(semantic_status,''), source_attempt_id,"
            " coalesce(inferred_group,''), coalesce(inferred_group_confidence,''),"
            " coalesce(canonical_role_label,''), first_stored_at::text")
    return c.execute(
        f"SELECT md5(coalesce(string_agg(concat_ws('|', {cols}), E'\\n' ORDER BY relation_id),''))"
        f" FROM {qualified}").fetchone()[0]

def view_snapshot(c, view):
    """count + md5 of every column of a view (schema-agnostic). B-13: the rows are
    hashed in a total order (first column, then the whole row), never in whatever
    order a subquery happens to return; with a unique first column the hash equals
    the 2026-09 one."""
    cols = [d.name for d in c.execute(f"SELECT * FROM public.{view} LIMIT 0").description]
    row = "concat_ws('|', " + ", ".join(f"coalesce(t.\"{col}\"::text, '')" for col in cols) + ")"
    n, h = c.execute(f"SELECT count(*), md5(coalesce(string_agg({row}, E'\\n'"
                     f" ORDER BY t.\"{cols[0]}\", {row}), '')) FROM public.{view} t").fetchone()
    return {"count": n, "hash": h}

def table_fingerprint(c, schema_table, pk):
    n = c.execute(f"SELECT count(*) FROM {schema_table}").fetchone()[0]
    return {"count": n}

# ---------------------------------------------------------------- STAGE 0
@stage("0_preconditions")
def s0(c):
    locks = c.execute("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND database ="
                      " (SELECT oid FROM pg_database WHERE datname = current_database())").fetchone()[0]
    acts = c.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND state='active' AND pid <> pg_backend_pid()").fetchone()[0]
    assert locks == 0, f"advisory lock held: {locks}"
    assert acts == 0, f"active sessions: {acts}"
    c.execute("SAVEPOINT s0")
    return {"advisory_locks": 0, "active_sessions": 0, "cutover_window_verified": True}

# ---------------------------------------------------------------- STAGE 1
@stage("1_pre_cutover_inventory")
def s1(c):
    inv = {}
    for t in OWN_TABLES:
        cons = c.execute("""SELECT contype, count(*) FROM pg_constraint con
            JOIN pg_class cl ON cl.oid=con.conrelid JOIN pg_namespace ns ON ns.oid=cl.relnamespace
            WHERE ns.nspname='public' AND cl.relname=%s GROUP BY contype""", (t,)).fetchall()
        idx = c.execute("SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename=%s", (t,)).fetchone()[0]
        inv[t] = {"constraints": {k: v for k, v in cons}, "indexes": idx,
                  "count": c.execute(f"SELECT count(*) FROM public.{t}").fetchone()[0]}
    # business baselines via views (public source)
    biz = {}
    for v in REL_VIEWS + [CYCLE_VIEW]:
        biz[v] = view_snapshot(c, v)
    biz["relation_type_counts"] = dict(c.execute(
        "SELECT canonical_relation_type, count(*) FROM public.canonical_relation GROUP BY 1").fetchall())
    biz["indirect"] = {"count": c.execute("SELECT count(*) FROM public.indirect_ownership").fetchone()[0]}
    biz["cycles"] = {"groups": c.execute("SELECT count(DISTINCT cycle_group_id) FROM public.ownership_cycle_member").fetchone()[0]}
    biz["contexts"] = {"person": c.execute("SELECT count(*) FROM public.person_relation_context").fetchone()[0],
                       "company": c.execute("SELECT count(*) FROM public.company_relation_context").fetchone()[0]}
    biz["sample_company10_direct"] = c.execute(
        "SELECT count(*) FROM public.v_direct_ownership WHERE owned_id=10").fetchone()[0]
    biz["sample_company10_control"] = c.execute(
        "SELECT count(*) FROM public.v_control_registration WHERE registered_id=10").fetchone()[0]
    biz["sample_person_roles"] = c.execute(
        "SELECT count(*) FROM public.v_role_relation WHERE person_id IN (SELECT person_id FROM public.v_role_relation GROUP BY person_id ORDER BY count(*) DESC LIMIT 1)").fetchone()[0]
    R["pre_business_baseline"] = biz
    R["pre_table_inventory"] = inv
    return inv

# ---------------------------------------------------------------- STAGE 2
@stage("2_create_target_schema")
def s2(c):
    c.execute("CREATE SCHEMA IF NOT EXISTS registry_ownership")
    # relation: exact semantic parity via LIKE INCLUDING ALL (copies PK, identity,
    # natural UNIQUE, CHECKs incl. percentage invariant, FK -> public.request_attempt,
    # defaults, indexes). Constraint/index names are schema-scoped -> no collision.
    c.execute("CREATE TABLE registry_ownership.relation (LIKE public.canonical_relation INCLUDING ALL)")
    c.execute("CREATE TABLE registry_ownership.indirect_ownership (LIKE public.indirect_ownership INCLUDING ALL)")
    c.execute("CREATE TABLE registry_ownership.ownership_cycle_member (LIKE public.ownership_cycle_member INCLUDING ALL)")
    c.execute("CREATE TABLE registry_ownership.person_relation_context (LIKE public.person_relation_context INCLUDING ALL)")
    c.execute("CREATE TABLE registry_ownership.company_relation_context (LIKE public.company_relation_context INCLUDING ALL)")
    # B-13: LIKE ... INCLUDING ALL copies no FOREIGN KEY, so the FKs are declared
    # here (once a separate step, schema_registry_ownership_fk_fix.sql). Same names.
    fks = [("relation", "relation_source_attempt_id_fkey", "source_attempt_id",
            "public.request_attempt (attempt_id)"),
           ("company_relation_context", "company_relation_context_source_relation_id_fkey",
            "source_relation_id", "registry_ownership.relation (relation_id)"),
           ("person_relation_context", "person_relation_context_person_a_relation_id_fkey",
            "person_a_relation_id", "registry_ownership.relation (relation_id)"),
           ("person_relation_context", "person_relation_context_person_b_relation_id_fkey",
            "person_b_relation_id", "registry_ownership.relation (relation_id)")]
    for t, name, col, ref in fks:
        c.execute(f"ALTER TABLE registry_ownership.{t} ADD CONSTRAINT {name} FOREIGN KEY ({col}) REFERENCES {ref}")
    rewired = [f"{t}.{name}" for t, name, _, _ in fks]
    # typed provenance bridge — materialises EXISTING acquisition metadata only
    c.execute("""CREATE TABLE registry_ownership.relation_evidence_pointer (
        relation_id        bigint PRIMARY KEY REFERENCES registry_ownership.relation (relation_id) ON DELETE CASCADE,
        source_attempt_id  bigint NOT NULL REFERENCES public.request_attempt,
        response_sha256    char(64) REFERENCES public.response_content,
        collection_run_id  bigint NOT NULL REFERENCES public.collection_run,
        materialized_at    timestamptz NOT NULL DEFAULT now())""")
    c.execute("""CREATE TABLE registry_ownership.build_meta (
        build_id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        schema_version           text NOT NULL,
        source_relation_count    bigint NOT NULL,
        source_relation_max_id   bigint NOT NULL,
        max_source_attempt_id    bigint,
        max_collection_run_id    bigint,
        built_at                 timestamptz NOT NULL DEFAULT now(),
        content_hash             text NOT NULL,
        status                   text NOT NULL)""")
    # parity assert: same columns, same order (canonical_relation -> target name `relation`)
    for t in OWN_TABLES:
        tname = "relation" if t == "canonical_relation" else t
        src = [r[0] for r in c.execute("""SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position""", (t,)).fetchall()]
        tgt = [r[0] for r in c.execute("""SELECT column_name FROM information_schema.columns
            WHERE table_schema='registry_ownership' AND table_name=%s ORDER BY ordinal_position""", (tname,)).fetchall()]
        assert src == tgt, f"{t}: column mismatch {set(src)^set(tgt)}"
    return {"rewired_fks": rewired, "columns_parity": "verified for all 5 tables"}

# ---------------------------------------------------------------- STAGE 3
@stage("3_shadow_population")
def s3(c):
    pop = {}
    # relation — exact id preservation
    rel_cols = [r[0] for r in c.execute("""SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='canonical_relation' ORDER BY ordinal_position""").fetchall()]
    collist = ", ".join(rel_cols)
    c.execute(f"INSERT INTO registry_ownership.relation ({collist}) OVERRIDING SYSTEM VALUE"
              f" SELECT {collist} FROM public.canonical_relation")
    pop["relation_rows"] = c.execute("SELECT count(*) FROM registry_ownership.relation").fetchone()[0]
    mx = c.execute("SELECT max(relation_id) FROM registry_ownership.relation").fetchone()[0]
    # PART A live result: last_value=21533, is_called=true -> setval(GREATEST(max_id, last_value), true)
    src_last = c.execute("SELECT last_value FROM public.canonical_relation_relation_id_seq").fetchone()[0]
    c.execute("SELECT setval('registry_ownership.relation_relation_id_seq', GREATEST(%s, %s), true)", (mx, src_last))
    nxt = c.execute("SELECT last_value, is_called FROM registry_ownership.relation_relation_id_seq").fetchone()
    pop["sequence"] = {"max_id": mx, "src_seq_last_value": src_last, "setval_to": nxt[0], "is_called": nxt[1],
                       "next_generated": nxt[0] + 1}
    assert nxt[0] + 1 > mx, "sequence unsafe"
    for t in ("indirect_ownership", "person_relation_context", "company_relation_context"):
        cols = [r[0] for r in c.execute("""SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position""", (t,)).fetchall()]
        cl = ", ".join(cols)
        c.execute(f"INSERT INTO registry_ownership.{t} ({cl}) OVERRIDING SYSTEM VALUE SELECT {cl} FROM public.{t}")
        seqn = c.execute("""SELECT pg_get_serial_sequence(%s, %s)""",
                         (f"registry_ownership.{t}", cols[0])).fetchone()[0]
        c.execute(f"SELECT setval(%s, GREATEST((SELECT max({cols[0]}) FROM registry_ownership.{t}), 1), true)", (seqn,))
        pop[t] = c.execute(f"SELECT count(*) FROM registry_ownership.{t}").fetchone()[0]
    c.execute("""INSERT INTO registry_ownership.ownership_cycle_member (cycle_group_id, entity_id)
        SELECT cycle_group_id, entity_id FROM public.ownership_cycle_member""")
    pop["ownership_cycle_member"] = c.execute("SELECT count(*) FROM registry_ownership.ownership_cycle_member").fetchone()[0]
    # evidence pointer — materialise existing acquisition metadata (no invention)
    c.execute("""INSERT INTO registry_ownership.relation_evidence_pointer
        (relation_id, source_attempt_id, response_sha256, collection_run_id)
        SELECT r.relation_id, r.source_attempt_id, ra.response_sha256, ra.collection_run_id
        FROM registry_ownership.relation r JOIN public.request_attempt ra ON ra.attempt_id = r.source_attempt_id""")
    pop["evidence_pointer_rows"] = c.execute("SELECT count(*) FROM registry_ownership.relation_evidence_pointer").fetchone()[0]
    pop["evidence_null_sha256"] = c.execute(
        "SELECT count(*) FROM registry_ownership.relation_evidence_pointer WHERE response_sha256 IS NULL").fetchone()[0]
    return pop

# ---------------------------------------------------------------- STAGE 4
@stage("4_reconciliation")
def s4(c):
    rec = {}
    checks = {}
    checks["relation_count"] = (
        c.execute("SELECT count(*) FROM public.canonical_relation").fetchone()[0],
        c.execute("SELECT count(*) FROM registry_ownership.relation").fetchone()[0])
    checks["relation_id_set_equality"] = (
        c.execute("SELECT count(*) FROM (SELECT relation_id FROM public.canonical_relation EXCEPT SELECT relation_id FROM registry_ownership.relation) t").fetchone()[0],
        c.execute("SELECT count(*) FROM (SELECT relation_id FROM registry_ownership.relation EXCEPT SELECT relation_id FROM public.canonical_relation) t").fetchone()[0])
    checks["content_hash"] = (hash_relation(c, "public.canonical_relation"),
                              hash_relation(c, "registry_ownership.relation"))
    for t in ("indirect_ownership", "person_relation_context", "company_relation_context"):
        checks[f"{t}_count"] = (
            c.execute(f"SELECT count(*) FROM public.{t}").fetchone()[0],
            c.execute(f"SELECT count(*) FROM registry_ownership.{t}").fetchone()[0])
    checks["ownership_cycle_member_count"] = (
        c.execute("SELECT count(*) FROM public.ownership_cycle_member").fetchone()[0],
        c.execute("SELECT count(*) FROM registry_ownership.ownership_cycle_member").fetchone()[0])
    # full-row equality for contexts (incl. context_id + FK columns)
    for t, pk in (("person_relation_context", "person_a_relation_id"), ("company_relation_context", "source_relation_id")):
        cols = [r[0] for r in c.execute("""SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position""", (t,)).fetchall()]
        cl = ", ".join(cols)
        d1 = c.execute(f"SELECT count(*) FROM (SELECT {cl} FROM public.{t} EXCEPT SELECT {cl} FROM registry_ownership.{t}) t").fetchone()[0]
        d2 = c.execute(f"SELECT count(*) FROM (SELECT {cl} FROM registry_ownership.{t} EXCEPT SELECT {cl} FROM public.{t}) t").fetchone()[0]
        checks[f"{t}_row_equality"] = (d1, d2)
    d1 = c.execute("""SELECT count(*) FROM (SELECT owner_entity_id, owned_entity_id, computed_percentage, path_count, contributing_paths, paths_truncated FROM public.indirect_ownership
        EXCEPT SELECT owner_entity_id, owned_entity_id, computed_percentage, path_count, contributing_paths, paths_truncated FROM registry_ownership.indirect_ownership) t""").fetchone()[0]
    d2 = c.execute("""SELECT count(*) FROM (SELECT owner_entity_id, owned_entity_id, computed_percentage, path_count, contributing_paths, paths_truncated FROM registry_ownership.indirect_ownership
        EXCEPT SELECT owner_entity_id, owned_entity_id, computed_percentage, path_count, contributing_paths, paths_truncated FROM public.indirect_ownership) t""").fetchone()[0]
    checks["indirect_row_equality"] = (d1, d2)
    fk_v = (c.execute("SELECT count(*) FROM registry_ownership.person_relation_context prc LEFT JOIN registry_ownership.relation r ON r.relation_id = prc.person_a_relation_id WHERE r.relation_id IS NULL").fetchone()[0]
        + c.execute("SELECT count(*) FROM registry_ownership.person_relation_context prc LEFT JOIN registry_ownership.relation r ON r.relation_id = prc.person_b_relation_id WHERE r.relation_id IS NULL").fetchone()[0]
        + c.execute("SELECT count(*) FROM registry_ownership.company_relation_context crc LEFT JOIN registry_ownership.relation r ON r.relation_id = crc.source_relation_id WHERE crc.source_relation_id IS NOT NULL AND r.relation_id IS NULL").fetchone()[0])
    checks["context_fk_violations"] = (fk_v, 0)
    checks["percentage_fields_equal"] = (
        c.execute("SELECT count(*) FROM public.canonical_relation WHERE parsed_percentage IS NOT NULL").fetchone()[0],
        c.execute("SELECT count(*) FROM registry_ownership.relation WHERE parsed_percentage IS NOT NULL").fetchone()[0])
    checks["canonical_subject_object_equal"] = (
        c.execute("SELECT count(*) FROM public.canonical_relation WHERE canonical_subject_entity_id IS NOT NULL").fetchone()[0],
        c.execute("SELECT count(*) FROM registry_ownership.relation WHERE canonical_subject_entity_id IS NOT NULL").fetchone()[0])
    # evidence pointer coverage
    checks["evidence_pointer_coverage"] = (
        c.execute("SELECT count(*) FROM registry_ownership.relation").fetchone()[0],
        c.execute("SELECT count(*) FROM registry_ownership.relation_evidence_pointer").fetchone()[0])
    rec["checks"] = {k: {"src": v[0], "tgt": v[1], "equal": v[0] == v[1]} for k, v in checks.items()}
    # content hash must be equal, all counts equal, 0 FK violations
    assert checks["relation_count"][0] == checks["relation_count"][1]
    assert checks["content_hash"][0] == checks["content_hash"][1], "CONTENT HASH MISMATCH"
    assert all(v[0] == v[1] for v in checks.values()), f"reconciliation mismatch: {checks}"
    # business outputs identical through views? (views still public — compare target queries directly)
    biz = {}
    biz["type_counts"] = dict(c.execute(
        "SELECT canonical_relation_type, count(*) FROM registry_ownership.relation GROUP BY 1").fetchall())
    assert biz["type_counts"] == R["pre_business_baseline"]["relation_type_counts"]
    return rec

# ---------------------------------------------------------------- STAGE 5
@stage("5_view_retarget")
def s5(c):
    import re
    swapped = {}
    tmap = {"canonical_relation": "registry_ownership.relation",
            "person_relation_context": "registry_ownership.person_relation_context",
            "company_relation_context": "registry_ownership.company_relation_context",
            "ownership_cycle_member": "registry_ownership.ownership_cycle_member",
            "indirect_ownership": "registry_ownership.indirect_ownership"}
    for v in REL_VIEWS + [CYCLE_VIEW]:
        d = c.execute("SELECT definition FROM pg_views WHERE schemaname='public' AND viewname=%s", (v,)).fetchone()[0]
        nd = re.sub(r"(?<![.\w])(canonical_relation|person_relation_context|company_relation_context|ownership_cycle_member|indirect_ownership)(?![\w])",
                    lambda m: tmap[m.group(1)], d)
        leftover = re.findall(r"(?<![.\w])(canonical_relation|person_relation_context|company_relation_context|ownership_cycle_member|indirect_ownership)(?![\w])", nd)
        assert not leftover, f"{v}: unreplaced refs {leftover}"
        c.execute(f"CREATE OR REPLACE VIEW public.{v} AS {nd.rstrip().rstrip(';')}")
        swapped[v] = {"tables_swapped": [t for t in tmap if t in d],
                      "depends_on_canonical_relation": "canonical_relation" in d,
                      "depends_on_entity_change_event": "entity_change_event" in d}
    # verify view outputs unchanged vs pre baseline
    post = {}
    for v in REL_VIEWS + [CYCLE_VIEW]:
        post[v] = view_snapshot(c, v)
    mism = {v: (R["pre_business_baseline"][v], post[v]) for v in post
            if R["pre_business_baseline"][v]["count"] != post[v]["count"] or R["pre_business_baseline"][v]["hash"] != post[v]["hash"]}
    assert not mism, f"VIEW OUTPUT MISMATCH after retarget: {mism}"
    return {"views": swapped, "post_retort_snapshot": post, "view_output_reconciliation": "IDENTICAL"}

# ---------------------------------------------------------------- STAGE 6
@stage("6_legacy_rename")
def s6(c):
    ren = {}
    for t in OWN_TABLES:
        legacy = f"{t}_legacy"
        writer_target = "registry_ownership.relation" if t == "canonical_relation" else f"registry_ownership.{t}"
        c.execute(f"ALTER TABLE public.{t} RENAME TO {legacy}")
        comment = (f"LEGACY_READ_ONLY - pre-Phase-1 ownership source table; writer target is now "
                   f"{writer_target}; rollback/reconciliation use only").replace("'", "''")
        c.execute(f"COMMENT ON TABLE public.{legacy} IS '{comment}'")
        ren[t] = legacy
    return {"renamed": ren, "legacy_preserved": True, "destructive_cleanup": "NONE (B31)"}

# ---------------------------------------------------------------- STAGE 7
@stage("7_smoke_tests")
def s7(c):
    sm = {}
    # 7a identity fixture — collector-style insert WITHOUT id, then ROLLBACK
    c.execute("SAVEPOINT smoke_identity")
    att = c.execute("SELECT min(attempt_id) FROM public.request_attempt").fetchone()[0]
    pre_max = c.execute("SELECT max(relation_id) FROM registry_ownership.relation").fetchone()[0]
    c.execute("""INSERT INTO registry_ownership.relation
        (raw_from_source_entity_id, raw_from_source_entity_label, raw_to_source_entity_id,
         raw_to_source_entity_label, raw_source_relation_type_id, raw_source_relation_label,
         canonical_relation_type, canonical_subject_entity_id, canonical_object_entity_id,
         semantic_direction_confidence, raw_percentage_label, parsed_percentage,
         percentage_parse_status, semantic_status, source_attempt_id)
        VALUES (999999001, 'SMOKE fixture A', 999999002, 'SMOKE fixture B', 1, 'SMOKE',
                'DIRECT_OWNERSHIP', NULL, NULL, 'UNCONFIRMED', %s, NULL, 'FAILED', NULL, %s)""",
                ("50 percent", att))
    gen_id = c.execute("SELECT max(relation_id) FROM registry_ownership.relation").fetchone()[0]
    sm["identity_fixture"] = {"generated_id": gen_id, "previous_max": pre_max, "generated_gt_prev_max": gen_id > pre_max}
    c.execute("ROLLBACK TO SAVEPOINT smoke_identity")
    after = c.execute("SELECT max(relation_id) FROM registry_ownership.relation").fetchone()[0]
    sm["identity_fixture"]["row_not_left_behind"] = after == pre_max
    assert sm["identity_fixture"]["generated_gt_prev_max"] and sm["identity_fixture"]["row_not_left_behind"]
    # 7b collector-style ON CONFLICT fixture — exact collector SQL, existing edge, ROLLBACK
    c.execute("SAVEPOINT smoke_conflict")
    edge = c.execute("""SELECT raw_from_source_entity_id, raw_from_source_entity_label,
        raw_to_source_entity_id, raw_to_source_entity_label, raw_source_relation_type_id,
        raw_source_relation_label, canonical_relation_type, canonical_subject_entity_id,
        canonical_object_entity_id, semantic_direction_confidence, raw_percentage_label,
        parsed_percentage, percentage_parse_status, semantic_status, source_attempt_id
        FROM registry_ownership.relation LIMIT 1""").fetchone()
    before = c.execute("SELECT count(*) FROM registry_ownership.relation").fetchone()[0]
    c.execute("""INSERT INTO registry_ownership.relation (raw_from_source_entity_id,
        raw_from_source_entity_label, raw_to_source_entity_id, raw_to_source_entity_label,
        raw_source_relation_type_id, raw_source_relation_label, canonical_relation_type,
        canonical_subject_entity_id, canonical_object_entity_id, semantic_direction_confidence,
        raw_percentage_label, parsed_percentage, percentage_parse_status, semantic_status,
        source_attempt_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (raw_from_source_entity_id, raw_to_source_entity_id,
        raw_source_relation_type_id) DO NOTHING""", tuple(edge))
    inserted = c.execute("SELECT count(*) FROM registry_ownership.relation").fetchone()[0] - before
    sm["collector_on_conflict"] = {"rows_inserted_on_existing_edge": inserted, "on_conflict_works": inserted == 0,
                                   "fk_to_shared_request_attempt": "verified via source_attempt_id in fixture"}
    c.execute("ROLLBACK TO SAVEPOINT smoke_conflict")
    sm["post_smoke_row_count"] = c.execute("SELECT count(*) FROM registry_ownership.relation").fetchone()[0]
    assert sm["post_smoke_row_count"] == c.execute("SELECT count(*) FROM public.canonical_relation_legacy").fetchone()[0]
    return sm

# ---------------------------------------------------------------- STAGE 8
@stage("8_source_separation_assertions")
def s8(c):
    a = {}
    a["registry_ownership_tables"] = [r[0] for r in c.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='registry_ownership' ORDER BY 1").fetchall()]
    a["no_pdf_tables_in_registry_ownership"] = not c.execute("""SELECT count(*) FROM information_schema.tables
        WHERE table_schema='registry_ownership' AND (table_name LIKE 'pdf_%')""").fetchone()[0]
    a["canonical_entity_in_public"] = c.execute("""SELECT count(*) FROM information_schema.tables
        WHERE table_schema='public' AND table_name='canonical_entity'""").fetchone()[0] == 1
    a["entity_status_in_public"] = c.execute("""SELECT count(*) FROM information_schema.tables
        WHERE table_schema='public' AND table_name='entity_status'""").fetchone()[0] == 1
    a["entity_change_event_in_public"] = c.execute("""SELECT count(*) FROM information_schema.tables
        WHERE table_schema='public' AND table_name='entity_change_event'""").fetchone()[0] == 1
    a["request_attempt_in_public"] = c.execute("""SELECT count(*) FROM information_schema.tables
        WHERE table_schema='public' AND table_name='request_attempt'""").fetchone()[0] == 1
    a["response_content_in_public"] = c.execute("""SELECT count(*) FROM information_schema.tables
        WHERE table_schema='public' AND table_name='response_content'""").fetchone()[0] == 1
    a["collection_run_in_public"] = c.execute("""SELECT count(*) FROM information_schema.tables
        WHERE table_schema='public' AND table_name='collection_run'""").fetchone()[0] == 1
    assert all(v is True or (isinstance(v, list) and v) for v in a.values()), a
    return a

# ---------------------------------------------------------------- STAGE 9
@stage("9_build_meta")
def s9(c):
    """B-5: the ACTIVE ownership generation the analytics builders pin
    (phase2_build.collect, entity_links). Hash = stage-4 hash, taken in UTC (B-8)."""
    row = c.execute("""INSERT INTO registry_ownership.build_meta
        (schema_version, source_relation_count, source_relation_max_id, max_source_attempt_id,
         max_collection_run_id, content_hash, status)
        SELECT 'phase1-v1', count(*), max(relation_id),
               (SELECT max(attempt_id) FROM public.request_attempt),
               (SELECT max(collection_run_id) FROM public.collection_run), %s, 'ACTIVE'
        FROM registry_ownership.relation
        RETURNING build_id, source_relation_count, source_relation_max_id, max_source_attempt_id,
                  max_collection_run_id, content_hash, status""",
        (R["stages"]["4_reconciliation"]["checks"]["content_hash"]["tgt"],)).fetchone()
    return dict(zip(("build_id", "source_relation_count", "source_relation_max_id",
                     "max_source_attempt_id", "max_collection_run_id", "content_hash", "status"), row))

def main():
    c = psycopg.connect(DB, autocommit=False)
    c.cursor().execute("SET default_transaction_read_only = off")
    try:
        s0(c); s1(c); s2(c); s3(c); s4(c); s5(c); s6(c); s7(c); s8(c); s9(c)
        c.commit()
        R["transaction"] = "single transaction, COMMITTED"
    except Exception as e:
        c.rollback()
        R["transaction"] = f"ROLLED BACK due to: {type(e).__name__}: {e}"
        raise
    finally:
        c.close()
    R["finished_at"] = datetime.datetime.now(datetime.UTC).isoformat()
    json.dump(R, open(OUT, "w"), indent=1, default=str)
    print(f"\nWROTE {OUT}")

if __name__ == "__main__":
    main()