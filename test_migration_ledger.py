"""Ledger contract proofs on a scratch database (P1.2).

1. empty ledger -> baseline registers existing migrations;
2. ledger with >=1 row -> baseline refused;
3. changed checksum after baseline -> hard failure;
4. no normal code path can re-run a changed migration.
Uses an isolated scratch DB (created/dropped per module).
"""
from __future__ import annotations

import psycopg
import pytest

from heimdal.pdfhist import migrations as ledger
from heimdal.pdfhist.db import admin_dsn


@pytest.fixture(scope="module")
def sconn():
    admin = psycopg.connect(admin_dsn(), autocommit=True)
    admin.execute("DROP DATABASE IF EXISTS pdfhist_ledger_drill")
    admin.execute("CREATE DATABASE pdfhist_ledger_drill")
    admin.close()
    conn = psycopg.connect(admin_dsn().rsplit("/", 1)[0] + "/pdfhist_ledger_drill")
    conn.autocommit = True
    yield conn
    conn.close()
    admin = psycopg.connect(admin_dsn(), autocommit=True)
    admin.execute("DROP DATABASE IF EXISTS pdfhist_ledger_drill")
    admin.close()


def test_empty_ledger_baseline_registers(sconn):
    done = ledger.baseline(sconn)
    assert done == ["projection", "review", "review-immutability"]
    st = ledger.status(sconn)
    assert all(v[0] == "APPLIED" for v in st.values())


def test_nonempty_ledger_baseline_refused(sconn):
    with pytest.raises(RuntimeError):
        ledger.baseline(sconn)


def test_changed_checksum_hard_failure(sconn, monkeypatch, tmp_path):
    import hashlib
    real = dict(ledger.MIGRATION_FILES)
    tampered = tmp_path / "review_migration.sql"
    tampered.write_text("-- tampered")
    monkeypatch.setattr(ledger, "MIGRATION_FILES",
                        (("projection", real["projection"]), ("review", tampered)))
    st = ledger.status(sconn)
    assert st["review"][0] == "MISMATCH"
    with pytest.raises(RuntimeError):
        ledger.apply(sconn)


def test_no_rerun_path_in_normal_code():
    import inspect
    src = inspect.getsource(ledger)
    assert "--force" not in src and "force_mode" not in src
    assert "def rerun" not in src and "def re_run" not in src
    assert "baseline_used" not in src.replace(
        "No baseline_used flag, no DB-identity tracking.", "")
