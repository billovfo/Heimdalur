"""B-5 (audit 2026-11) on a scratch copy of the heimdal test clone: a replay of
tools/phase1_cutover.py writes the ACTIVE registry_ownership.build_meta row that
phase2_build and entity_links pin, and it reproduces the clone's own 2026-09 row
(the stage-4 hash is taken in UTC, B-8). The copy is turned back into the
pre-cutover shape first: registry_ownership dropped, *_legacy renamed back.
B-13: the same replay declares the FKs, hashes views order-independently and
refuses to overwrite its stage record."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent
COLS = "schema_version, status, content_hash, source_relation_count, source_relation_max_id"
TABLES = ("canonical_relation", "indirect_ownership", "ownership_cycle_member",
          "person_relation_context", "company_relation_context")


def test_phase1_replay_writes_the_active_build_meta(tmp_path):
    clone_dsn = os.environ["HEIMDAL_READ_DB"]
    base, clone = clone_dsn.rsplit("/", 1)
    scratch = clone + "_p1"
    assert re.fullmatch(r"[a-z_]*_test[a-z0-9_]*", scratch), scratch
    with psycopg.connect(clone_dsn) as c:
        want = c.execute(f"SELECT {COLS} FROM registry_ownership.build_meta WHERE status = 'ACTIVE'").fetchall()
    assert want, "the clone has no ACTIVE build_meta to compare with"
    dsn = f"{base}/{scratch}"
    with psycopg.connect(os.environ["HEIMDAL_TEST_ADMIN_DSN"], autocommit=True) as adm:
        adm.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
        adm.execute(f'CREATE DATABASE "{scratch}" TEMPLATE "{clone}"')
        try:
            with psycopg.connect(dsn, autocommit=True) as c:
                c.execute("DROP SCHEMA registry_ownership CASCADE")
                for t in TABLES:
                    c.execute(f"ALTER TABLE public.{t}_legacy RENAME TO {t}")
                for f in ("schema_read.sql", "schema_compliance.sql"):  # the pre-cutover views
                    c.execute((ROOT / f).read_text())
            (tmp_path / "tools").mkdir()  # the script writes tools/phase1_db_stages.json in its cwd
            subprocess.run([sys.executable, str(ROOT / "tools/phase1_cutover.py")], cwd=tmp_path,
                           env={**os.environ, "HEIMDAL_DB": dsn}, check=True, capture_output=True)
            with psycopg.connect(dsn) as c:
                got = c.execute(f"SELECT {COLS} FROM registry_ownership.build_meta").fetchall()
                fks = c.execute("SELECT count(*) FROM pg_constraint WHERE contype = 'f'"
                                " AND connamespace = 'registry_ownership'::regnamespace").fetchone()[0]
            again = subprocess.run([sys.executable, str(ROOT / "tools/phase1_cutover.py")], cwd=tmp_path,
                                   env={**os.environ, "HEIMDAL_DB": dsn}, capture_output=True, text=True)
        finally:
            adm.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
    assert got == want
    # B-13: stage 2 declares the 4 FKs LIKE cannot copy (+ the evidence pointer's 4), the
    # view hashes are order-independent and equal the 2026-09 evidence, and the record
    # is never overwritten.
    stages = json.loads((tmp_path / "tools/phase1_db_stages.json").read_text())
    assert len(stages["stages"]["2_create_target_schema"]["rewired_fks"]) == 4 and fks == 8
    evidence = json.loads((ROOT / "tools/phase1_db_stages.json").read_text())["pre_business_baseline"]
    for view in ("v_direct_ownership", "v_control_registration", "v_role_relation", "v_source_anomaly"):
        assert stages["pre_business_baseline"][view] == evidence[view], view
    assert again.returncode != 0 and "never overwritten" in again.stderr
