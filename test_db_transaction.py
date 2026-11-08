"""Canonical transaction primitive contract tests (P0.2a).

commit-on-success, rollback-on-error (incl. BaseException), close always,
loud failures, dict rows. Runs against the throwaway pdfhist_test database.
"""
from __future__ import annotations

import psycopg
import pytest

from heimdal.modules.shared import db as shared_db
from heimdal.pdfhist.testsupport import ensure_test_database


@pytest.fixture(scope="module")
def dsn():
    dsn = ensure_test_database()
    yield dsn
    with psycopg.connect(dsn) as c:  # B-14: no tx_probe left in the test DB
        c.execute("DROP TABLE IF EXISTS tx_probe")


def _scratch(conn, name="tx_probe"):
    conn.execute(f"CREATE TABLE IF NOT EXISTS {name} (id bigint PRIMARY KEY, v text)")
    conn.execute(f"DELETE FROM {name}")


def test_commit_on_success(dsn):
    with shared_db.tx(dsn) as c:
        _scratch(c)
        c.execute("INSERT INTO tx_probe VALUES (1, 'a')")
    with psycopg.connect(dsn) as check:
        assert check.execute("SELECT v FROM tx_probe WHERE id=1").fetchone()[0] == "a"


def test_rollback_on_error(dsn):
    with pytest.raises(RuntimeError):
        with shared_db.tx(dsn) as c:
            _scratch(c)
            c.execute("INSERT INTO tx_probe VALUES (2, 'b')")
            raise RuntimeError("injected")
    with psycopg.connect(dsn) as check:
        assert check.execute("SELECT count(*) FROM tx_probe WHERE id=2").fetchone()[0] == 0


def test_rollback_on_base_exception(dsn):
    with pytest.raises(KeyboardInterrupt):
        with shared_db.tx(dsn) as c:
            _scratch(c)
            c.execute("INSERT INTO tx_probe VALUES (3, 'c')")
            raise KeyboardInterrupt()
    with psycopg.connect(dsn) as check:
        assert check.execute("SELECT count(*) FROM tx_probe WHERE id=3").fetchone()[0] == 0


def test_connection_closed(dsn):
    with shared_db.tx(dsn) as c:
        inner = c
    assert inner.closed != 0  # closed (non-zero) after context exit


def test_loud_connect_failure():
    with pytest.raises(Exception):
        with shared_db.tx("postgresql://127.0.0.1:1/nope"):
            pass


def test_write_dsn_fail_closed(monkeypatch):
    monkeypatch.delenv("HEIMDAL_PG_DSN", raising=False)
    with pytest.raises(RuntimeError):
        shared_db.write_dsn()


def test_dict_rows(dsn):
    with shared_db.tx(dsn) as c:
        row = c.execute("SELECT 1 AS one").fetchone()
        assert isinstance(row, dict) and row["one"] == 1
