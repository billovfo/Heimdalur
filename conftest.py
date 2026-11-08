"""Root pytest guard (audit 2026-11 H-1, H-4): tests never reach a live DB.

Fail-closed: before collection, every DSN the code resolves must name a
non-live database, or the session aborts. Live means `heimdal`,
`pdfhist_pilot` or any name containing `_recovered_`. An unset variable counts
as its code default, so a bare environment is refused too (config.DB_URI
defaults to live `heimdal`). Set up a test env with `source tools/test_env.sh`.
A deliberate live run needs HEIMDAL_ALLOW_LIVE=1.

os.environ is isolated per test module: a module's import-time env changes
apply only while its own tests run, and everything is restored after the
module, so results no longer depend on which files run together.
"""
import getpass
import importlib.util
import os
from pathlib import Path
from unittest import mock

import pytest
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parent
LIVE_DBS = ("heimdal", "pdfhist_pilot")


def _load(rel):
    # Load one module file without running heimdal/__init__.py (which builds
    # the app), so the guard resolves DSNs exactly as the code does.
    spec = importlib.util.spec_from_file_location(
        "_guard_" + rel.replace("/", "_")[:-3], ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _resolved_dsns():
    config, db = _load("heimdal/config.py"), _load("heimdal/pdfhist/db.py")
    env = os.environ.get
    return {
        "heimdal.config.DB_URI (HEIMDAL_READ_DB)": config.DB_URI,
        "qa_dsn() (HEIMDAL_QA_DB)": db.qa_dsn(),
        "admin_dsn() (HEIMDAL_TEST_ADMIN_DSN)": db.admin_dsn(),
        "test_dsn() (HEIMDAL_TEST_DB)": db.test_dsn(),
        # No code default: unset means the code refuses to connect.
        "HEIMDAL_DB": env("HEIMDAL_DB"),
        "HEIMDAL_PG_DSN": env("HEIMDAL_PG_DSN"),
        "HEIMDAL_PDFHIST_PROJECTION_DSN": env("HEIMDAL_PDFHIST_PROJECTION_DSN"),
        "HEIMDAL_PDFHIST_DB": env("HEIMDAL_PDFHIST_DB"),
    }


def _dbname(dsn):
    # libpq fallback order when the DSN names no database.
    d = conninfo_to_dict(dsn)
    return (d.get("dbname") or os.environ.get("PGDATABASE") or d.get("user")
            or os.environ.get("PGUSER") or getpass.getuser())


def live_dsn_hits():
    hits = []
    for label, dsn in _resolved_dsns().items():
        if dsn:
            name = _dbname(dsn)
            if name in LIVE_DBS or "_recovered_" in name:
                hits.append(f"{label} -> {name}")
    return hits


def _refuse_live(when):
    if os.environ.get("HEIMDAL_ALLOW_LIVE") == "1":
        return
    try:
        hits = live_dsn_hits()
    except ValueError as exc:  # db.test_db_name() refuses a non-test name
        hits = [str(exc)]
    if hits:
        pytest.exit(
            f"REFUSED {when}: live database DSN(s) in the environment:\n  "
            + "\n  ".join(hits)
            + "\nRun `source tools/test_env.sh` first "
              "(HEIMDAL_ALLOW_LIVE=1 only for a deliberate live run).")


def pytest_configure(config):
    _refuse_live("before collection")


_IMPORT_ENV = {}


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector):
    if not isinstance(collector, pytest.Module):
        return (yield)
    with mock.patch.dict(os.environ):  # undo the module's import-time env ...
        try:
            return (yield)
        finally:  # ... but keep it for the module's own tests
            _IMPORT_ENV[collector.nodeid] = dict(os.environ)


@pytest.fixture(scope="session")
def baseline_projection():
    """The baseline projection run id (tests_baseline.py), or skip (H-6).

    Skips unless HEIMDAL_PDFHIST_PROJECTION_DSN holds that exact ACTIVE run:
    the default test DB holds no run, or only synthetic ones from the isolated
    tests, and the projection tests' names and counts belong to run 33."""
    from tests_baseline import PROJECTION_HASH, PROJECTION_RUN, active_projection_run
    row = active_projection_run(os.environ.get("HEIMDAL_PDFHIST_PROJECTION_DSN"))
    if row != (PROJECTION_RUN, PROJECTION_HASH):
        pytest.skip(f"projection DB has ACTIVE run {row and row[0]}, not baseline run "
                    f"{PROJECTION_RUN}: point HEIMDAL_PDFHIST_PROJECTION_DSN at a "
                    f"run-{PROJECTION_RUN} clone (tests_baseline.py)")
    return PROJECTION_RUN


@pytest.fixture
def active_projection(baseline_projection, monkeypatch):
    """baseline_projection with both PDF history flags on for this test."""
    monkeypatch.setenv("PDF_HISTORY_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("PDF_HISTORY_UI_ENABLED", "true")
    return baseline_projection


@pytest.fixture(autouse=True, scope="module")
def _module_env(request):
    env = _IMPORT_ENV.get(request.node.nodeid, dict(os.environ))
    with mock.patch.dict(os.environ, env, clear=True):
        _refuse_live(f"in {request.node.nodeid}")
        yield
