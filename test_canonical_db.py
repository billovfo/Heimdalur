"""Canonical DB lane W5b (audit 2026-11 B-9, B-12, B-14, B-17, C-7). Isolated.

Runs on the heimdal test clone (HEIMDAL_DB, made by tools/test_db_refresh.sh,
which applies the canonical ledger) and on two throwaway DBs: the pdfhist test
DB and <HEIMDAL_TEST_DB>_ledger. Every write to the clone is rolled back or
undone (the watchlist toggle runs twice).
B-17: an empty DB + the ledger has exactly the clone's catalog; a 2nd apply is a no-op.
B-9:  the portal reads as heimdal_reader (read-only session), writes through tx().
B-12: either_side() returns exactly the rows of the OR form.
B-14: the pdfhist test DB carries the production ledger.
C-7:  _tag_roles() writes only rows whose tags differ.
"""
import collections
import os

import psycopg
import pytest

import heimdal_collect as hc
from heimdal import canonical_migrations
from heimdal.modules.shared import db as shared_db
from heimdal.pdfhist import db as pdb

CLONE = os.environ.get("HEIMDAL_DB", "")
READER = CLONE + "?options=-c%20role%3Dheimdal_reader%20-c%20default_transaction_read_only%3Don"
SCHEMAS = ["public", "registry_ownership", "registry_analytics"]
CATALOG = """
SELECT 'col ' || table_schema || '.' || table_name || '.' || column_name || ' ' || data_type
       || ' ' || is_nullable || ' ' || coalesce(column_default, '')
  FROM information_schema.columns WHERE table_schema = ANY(%(s)s)
UNION ALL SELECT 'con ' || conrelid::regclass || ' ' || conname || ' ' || pg_get_constraintdef(oid)
  FROM pg_constraint WHERE connamespace::regnamespace::text = ANY(%(s)s)
UNION ALL SELECT 'idx ' || indexdef FROM pg_indexes WHERE schemaname = ANY(%(s)s)
UNION ALL SELECT 'view ' || schemaname || '.' || viewname || ' ' || definition
  FROM pg_views WHERE schemaname = ANY(%(s)s)
UNION ALL SELECT 'trg ' || pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal
UNION ALL SELECT 'fn ' || oid::regprocedure || ' ' || prosrc
  FROM pg_proc WHERE pronamespace::regnamespace::text = ANY(%(s)s)
UNION ALL SELECT 'acl ' || relnamespace::regnamespace || '.' || relname || ' ' || coalesce(relacl::text, '')
  FROM pg_class WHERE relnamespace::regnamespace::text = ANY(%(s)s)
UNION ALL SELECT 'nsp ' || nspname || ' ' || coalesce(nspacl::text, '')
  FROM pg_namespace WHERE nspname = ANY(%(s)s)
UNION ALL SELECT 'defacl ' || defaclnamespace::regnamespace || ' ' || defaclobjtype::text || ' ' || defaclacl::text
  FROM pg_default_acl
UNION ALL SELECT 'setting ' || setrole::regrole || ' ' || array_to_string(setconfig, ',')
  FROM pg_db_role_setting WHERE setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database())
"""


def catalog(dsn):
    with psycopg.connect(dsn) as c:
        return sorted(r[0] for r in c.execute(CATALOG, {"s": SCHEMAS}))


@pytest.fixture(scope="module")
def clone():
    assert CLONE, "HEIMDAL_DB must name the heimdal test clone (tools/test_env.sh)"
    with psycopg.connect(CLONE) as c:
        st = canonical_migrations.status(c)
        c.rollback()
    behind = [n for n, (s, _) in st.items() if s != "APPLIED"]
    assert not behind, f"clone not at the ledger head {behind}: run tools/test_db_refresh.sh"
    return CLONE


def test_empty_db_plus_ledger_has_the_clone_catalog(clone):
    name = pdb.require_test_db_name(pdb.test_db_name() + "_ledger")
    dsn = f"{pdb.admin_dsn().rsplit('/', 1)[0]}/{name}"
    with psycopg.connect(pdb.admin_dsn(), autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {name}")
    try:
        with psycopg.connect(dsn) as c:
            assert canonical_migrations.apply(c) == [n for n, _ in canonical_migrations.FILES]
            c.commit()
            assert canonical_migrations.apply(c) == []  # idempotent: nothing pending
        assert catalog(dsn) == catalog(clone)
    finally:
        with psycopg.connect(pdb.admin_dsn(), autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def test_portal_reads_as_heimdal_reader_and_writes_through_tx(clone, monkeypatch):
    from heimdal.app import create_app
    monkeypatch.setattr(shared_db, "DB_URI", READER)
    monkeypatch.setenv("HEIMDAL_PG_DSN", clone)
    with psycopg.connect(READER) as c:
        assert c.execute("SELECT current_user, current_setting('transaction_read_only')"
                         ).fetchone() == ("heimdal_reader", "on")
        person = c.execute("SELECT min(person_id) FROM v_role_relation").fetchone()[0]
        company = c.execute("SELECT min(owned_id) FROM v_direct_ownership").fetchone()[0]
    with psycopg.connect(READER, autocommit=True) as w:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # no write grant at all
            with w.transaction():
                w.execute("SET TRANSACTION READ WRITE")
                w.execute("DELETE FROM user_watchlist WHERE false")
    app = create_app()
    app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    cl = app.test_client()
    for url in ("/", f"/persons/{person}", f"/companies/{company}",
                f"/persons/{person}/network", f"/companies/{company}/network", "/overvaagning"):
        assert cl.get(url).status_code == 200, url
    got = [cl.post("/api/watchlist/toggle", json={"canonical_entity_id": company}) for _ in "ab"]
    assert [r.status_code for r in got] == [200, 200]
    assert {r.get_json()["status"] for r in got} == {"added", "removed"}  # the clone ends as it began


def test_either_side_returns_the_or_rows(clone):
    views = (("relation_id, owner_id, owned_id, parsed_percentage", "v_direct_ownership",
              "owner_id", "owned_id"),
             ("relation_id, actor_id, registered_id", "v_control_registration",
              "actor_id", "registered_id"),
             ("relation_id, person_id, company_id, canonical_role_label", "v_role_relation",
              "person_id", "company_id"))
    with psycopg.connect(clone) as c:
        ids = [r[0] for r in c.execute(
            "(SELECT owner_id FROM v_direct_ownership WHERE owner_id = owned_id LIMIT 2)"
            " UNION ALL (SELECT owned_id FROM v_direct_ownership GROUP BY 1 ORDER BY count(*) DESC LIMIT 2)"
            " UNION ALL (SELECT person_id FROM v_role_relation GROUP BY 1 ORDER BY count(*) DESC LIMIT 2)"
            " UNION ALL (SELECT registered_id FROM v_control_registration LIMIT 1)")]
        assert len(ids) == 7
        for cols, view, a, b in views:
            for x in ids:
                ors = c.execute(f"SELECT {cols} FROM {view} WHERE {a} = %(id)s OR {b} = %(id)s",
                                {"id": x}).fetchall()
                union = c.execute(shared_db.either_side(cols, view, a, b), {"id": x}).fetchall()
                assert collections.Counter(union) == collections.Counter(ors), (view, x)


def test_tag_roles_writes_only_changed_rows(clone):
    c = psycopg.connect(clone)
    try:
        hc._tag_roles(c)
        assert hc._tag_roles(c) == 0  # a re-run touches no canonical row (C-7)
        rid = c.execute("SELECT min(relation_id) FROM registry_ownership.relation"
                        " WHERE raw_source_relation_type_id = ANY(%s)",
                        (list(hc.CBTYPE_GROUP),)).fetchone()[0]
        assert rid is not None
        c.execute("UPDATE registry_ownership.relation SET canonical_role_label = 'drifted'"
                  " WHERE relation_id = %s", (rid,))
        assert hc._tag_roles(c) == 1
    finally:
        c.rollback()
        c.close()


def test_pdfhist_test_db_carries_the_production_ledger():
    from heimdal.pdfhist import migrations
    from heimdal.pdfhist.testsupport import ensure_test_database
    with psycopg.connect(ensure_test_database()) as c:
        assert {s for s, _ in migrations.status(c).values()} == {"APPLIED"}
