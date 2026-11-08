"""DB connection conventions (follows heimdal_collect.py: psycopg v3).

* HEIMDAL_PDFHIST_DB  - database this pipeline writes to when integrated into a
  real run (NOT used by the offline tests).
* HEIMDAL_TEST_ADMIN_DSN - admin DSN used ONLY to create/drop the dedicated test
  database (never the QA/heimdal database).
* HEIMDAL_TEST_DB - the dedicated test database used by the offline test suite.
* HEIMDAL_QA_DB - READ-ONLY: used exclusively by the company_location-untouched
  test to capture a rowcount/checksum before/after test ingestions.

The offline pipeline never opens a write connection to the QA heimdal database.
"""
from __future__ import annotations

import os
import re

import psycopg

# CANONICAL-TEST-DSN (sole DSN defaults in the tree; see gate 3). Test-only
# bootstrap values WITHOUT passwords — authentication comes from PGPASSFILE
# (tools/.dev-pgpass, 0600, git-ignored) or PGPASSWORD, never from literals.
DEFAULT_ADMIN_DSN = "postgresql://postgres@localhost:5432/postgres"
DEFAULT_TEST_DB = "pdfhist_test"
DEFAULT_QA_DSN = "postgresql://postgres@localhost:5432/heimdal"


def admin_dsn() -> str:
    return os.environ.get("HEIMDAL_TEST_ADMIN_DSN", DEFAULT_ADMIN_DSN)


def qa_dsn() -> str:
    # HEIMDAL_READ_DB is the canonical portal read DSN; HEIMDAL_QA_DB is its
    # test alias. Single resolution point (P1.1).
    return os.environ.get(
        "HEIMDAL_QA_DB",
        os.environ.get("HEIMDAL_READ_DB", DEFAULT_QA_DSN))


def require_test_db_name(name: str) -> str:
    """Only a name that says "test" may be dropped and re-created (audit D-05).

    heimdal, pdfhist_pilot and the *_recovered_* copies never match.
    """
    if not re.fullmatch(r"[a-z_]*_test[a-z0-9_]*", name):
        raise ValueError(f"refusing database {name!r}: not a test database "
                         "name (must match ^[a-z_]*_test[a-z0-9_]*$)")
    return name


def test_db_name() -> str:
    return require_test_db_name(
        os.environ.get("HEIMDAL_TEST_DB", DEFAULT_TEST_DB))


def test_dsn() -> str:
    override = os.environ.get("HEIMDAL_PDFHIST_TEST_DB")
    if override:
        return override
    return f"{admin_dsn().rsplit('/', 1)[0]}/{test_db_name()}"


def connect(dsn: str):
    return psycopg.connect(dsn)