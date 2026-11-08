#!/usr/bin/env python3
"""Audit lane D check (PDF history / Skraseting). Offline: opens NO database
connection and makes no network request. Exits 1 while any finding is open.

  D-01 flags OFF + no projection DSN must not raise (person/history 500s)
  D-02 integration ON + UI OFF must not read the projection for history views
  D-03 extract_text enforces its byte, page and wall-clock limits: oversized,
       many-page and content-bomb PDFs get ExtractLimitExceeded in bounded time
  D-04 the test-DB bootstrap must refuse live database names before connecting
  D-03b history: dedup before LIMIT (person), newest first (company), and the
       company history page renders the truncation badge
  D-11 flags OFF must open no projection connection at all, and the company
       page must not claim "no dated registrations" behind the closed gate
  D-06 the Lysing parser keeps board changes (lys_38088) and flags unsorted
       membership lines + takes the letter date (founding lys_33208)
  D-07 activate() refuses a SHADOW that fails validate()
  D-08 entity links: no swallowed exceptions, no `or True`, an unresolvable
       ownership pin raises, the frozen COV map path comes from the caller
  D-09 delete_non_active() deletes entity_link rows before the run
  D-10 a single-run build picks the newest Felag snapshot (valid_from DESC)
  D-13 one date precedence (temporal.governing_date) for build, consumer, UI

Run from the repo root:  python tools/audit/check_d.py
"""
import os
import re
import signal
import sys
import time
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
for k in ("HEIMDAL_PDFHIST_PROJECTION_DSN", "PDF_HISTORY_INTEGRATION_ENABLED",
          "PDF_HISTORY_UI_ENABLED"):
    os.environ.pop(k, None)

from heimdal.pdfhist import consumer, extract, migrate  # noqa: E402
from heimdal.pdfhist import entity_links, projection, temporal  # noqa: E402
from heimdal.pdfhist.fixture_pdf import make_hostile_pdf  # noqa: E402
from heimdal.pdfhist.parse import parse_lysing_pdf  # noqa: E402
import heimdal.modules.entities.routes as routes  # noqa: E402
from heimdal.modules.skraseting import views as skr_views  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def check_d01_flags_off_without_dsn():
    os.environ["PDF_HISTORY_INTEGRATION_ENABLED"] = "false"
    os.environ["PDF_HISTORY_UI_ENABLED"] = "false"
    for name, call in (
            ("get_skraseting_person_history",
             lambda: consumer.get_skraseting_person_history(1, limit=101)),
            ("get_projected_company_history",
             lambda: consumer.get_projected_company_history("10", limit=101)),
            ("ui_exposure_allowed", consumer.ui_exposure_allowed)):
        try:
            result = call()
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"{name} raised {type(exc).__name__} with "
                                 "flags off and no DSN") from None
        assert not result, f"{name} returned data with flags off"


def check_d02_ui_off_no_projection_read():
    os.environ["PDF_HISTORY_INTEGRATION_ENABLED"] = "true"
    os.environ["PDF_HISTORY_UI_ENABLED"] = "false"
    reads = []

    @contextmanager
    def fake_connect(conn):
        yield object()  # flags resolve from env; any query would fail
    orig = consumer._connect, consumer._resolve_active_run
    consumer._connect = fake_connect
    consumer._resolve_active_run = lambda c: reads.append(1)
    try:
        routes._skraseting_person_history(None, 1)
        routes._skraseting_company_history("S10")
    finally:
        consumer._connect, consumer._resolve_active_run = orig
    assert not reads, (f"{len(reads)} projection read(s) for history views "
                       "with PDF_HISTORY_UI_ENABLED=false")


class _Timeout(BaseException):
    pass


def check_d03_hostile_pdf_bounded():
    """The limit failure arrives in bounded time, whatever pypdf's speed: the
    bomb needs minutes of pypdf work on any machine, and the child is killed."""
    op = b"BT /F1 1 Tf 0 0 Td (A) Tj ET\n"
    cases = (
        ("oversized", b"%PDF-1.7\n" + b" " * extract.MAX_PDF_BYTES, 2),
        ("many pages", make_hostile_pdf(op, pages=extract.MAX_PAGES + 1), 5),
        ("content bomb", make_hostile_pdf(op * (20_000_000 // len(op))),
         extract.EXTRACT_TIMEOUT_S + 5))

    def on_alarm(*_):
        raise _Timeout()
    old = signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(int(extract.EXTRACT_TIMEOUT_S) + 30)  # the check never hangs
    try:
        for name, pdf, bound in cases:
            started = time.monotonic()
            try:
                extract.extract_text(pdf)
            except extract.ExtractLimitExceeded:
                pass
            except extract.TextExtractionError as exc:
                raise AssertionError(f"{name} PDF: {exc!r}, not the limit "
                                     "failure") from None
            else:
                raise AssertionError(f"{name} PDF ({len(pdf)} B) passed the "
                                     "extraction limits")
            took = time.monotonic() - started
            assert took <= bound, (f"{name} PDF limit failure took {took:.1f}"
                                   f" s (bound {bound} s)")
    except _Timeout:
        raise AssertionError("extract_text still running: limits not "
                             "enforced") from None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def check_d04_bootstrap_refuses_live_names():
    unreachable = "postgresql://audit@127.0.0.1:1/none?connect_timeout=1"
    for name in ("heimdal", "pdfhist_pilot"):
        try:
            migrate.bootstrap_test_database(unreachable, name)
        except ValueError:
            continue
        except Exception as exc:  # noqa: BLE001 - it tried to connect
            raise AssertionError(f"bootstrap_test_database({name!r}) has no "
                                 f"name guard (got {type(exc).__name__})"
                                 ) from None
        raise AssertionError(f"bootstrap_test_database({name!r}) returned")


class _SqlRecorder:
    """Fake projection connection: records SQL, ACTIVE run 33, company 1."""
    def __init__(self):
        self.sqls = []

    def execute(self, sql, params=()):
        self.sqls.append(" ".join(sql.split()))
        return self

    def fetchall(self):
        return [(33, "v2", "ACTIVE")] if "v_active_run" in self.sqls[-1] else []

    def fetchone(self):
        return (1, "10", None, None, None, None)


def _depth_at(sql, pos):
    return sql[:pos].count("(") - sql[:pos].count(")")


def check_d03b_history_dedup_before_limit():
    rec = _SqlRecorder()
    consumer.get_skraseting_person_history(1, conn=rec,
                                           integration_enabled=True, limit=101)
    person = rec.sqls[-1]
    assert "DISTINCT ON" in person and "LIMIT" in person, \
        "person history has no SQL dedup"
    assert _depth_at(person, person.index("DISTINCT ON")) >= 1 and \
        _depth_at(person, person.rindex("LIMIT")) == 0, \
        "person history LIMIT is not applied after the dedup subquery"
    assert "OVER ()" in person, \
        "person history count/first date are not taken over the full set"
    rec = _SqlRecorder()
    consumer.get_projected_company_history("10", conn=rec,
                                           integration_enabled=True, limit=101)
    company = rec.sqls[-1]
    order = company[company.rindex("ORDER BY"):]
    assert "DESC" in order.split("LIMIT")[0], \
        f"company history LIMIT does not keep the newest ({order[:80]})"
    tpl = open(os.path.join(ROOT, "templates/company_history.html"),
               encoding="utf-8").read()
    assert "{% if skraseting_truncated %}" in tpl, \
        "company_history.html never renders skraseting_truncated"


def check_d11_flags_off_opens_no_connection():
    os.environ["PDF_HISTORY_INTEGRATION_ENABLED"] = "false"
    os.environ["PDF_HISTORY_UI_ENABLED"] = "false"
    os.environ["HEIMDAL_PDFHIST_PROJECTION_DSN"] = \
        "postgresql://audit@127.0.0.1:1/none?connect_timeout=1"
    opened = []

    def fake_open(dsn):
        opened.append(dsn)
        raise OSError("check_d: no projection connection allowed")
    orig = consumer._read_only_conn
    consumer._read_only_conn = fake_open
    try:
        for call in (consumer.ui_exposure_allowed, consumer.integration_flag,
                     lambda: consumer.get_projected_company_state("10"),
                     lambda: consumer.get_projected_company_history("10"),
                     lambda: consumer.get_entity_link("COMPANY", "10"),
                     lambda: consumer.get_skraseting_roles_for_person(1),
                     lambda: consumer.get_skraseting_person_history(1),
                     lambda: skr_views.skraseting_view(None, 1)):
            try:
                call()
            except Exception:  # noqa: BLE001 - D-01 covers raising
                pass
    finally:
        consumer._read_only_conn = orig
        del os.environ["HEIMDAL_PDFHIST_PROJECTION_DSN"]
    assert not opened, (f"{len(opened)} projection connection(s) opened "
                        "with PDF_HISTORY_INTEGRATION_ENABLED=false")
    tpl = open(os.path.join(ROOT, "templates/company.html"),
               encoding="utf-8").read()
    assert re.search(r"skraseting_allowed\(\) %}\s*<p[^>]*>Ingen daterede "
                     r"registreringer", tpl), \
        "company.html claims 'Ingen daterede registreringer' with flags off"


def _src(path):
    return open(os.path.join(ROOT, path), encoding="utf-8").read()


def check_d06_lysing_keeps_board_changes():
    # real register PDFs live outside git (I-5): HEIMDAL_FIXTURES_DIR
    from heimdal.pdfhist.testsupport import FIXTURE_DIR as fixtures, pii_digest
    if not os.path.isdir(fixtures):
        print(f"SKIP check_d06: no fixtures in HEIMDAL_FIXTURES_DIR={fixtures}")
        return
    notice = parse_lysing_pdf(open(os.path.join(
        fixtures, "lys_38088.bin.txt"), encoding="utf-8").read())
    board = [c["entity"] for c in notice["changes"]
             if c["type"] == "BOARD_CHANGE" and c["action"] == "APPOINTED"]
    assert pii_digest(board) == 'fe96bf61ffb40d34', \
        f"lys_38088: {len(board)} board appointments, not the 3 pinned ones"
    founding = parse_lysing_pdf(open(os.path.join(
        fixtures, "lys_33208_20-06-2022.bin.txt"), encoding="utf-8").read())
    assert "MEMBERSHIP_UNSORTED" in founding["review_reasons"], \
        "unsorted founding Stjórn line has no review flag"
    assert founding["notice_date"] == "2022-06-20", \
        f"lys_33208 notice_date {founding['notice_date']} (founding date?)"


class _TxRecorder(_SqlRecorder):
    """Fake connection with transaction(); every status query says SHADOW."""
    @contextmanager
    def transaction(self):
        yield

    def fetchone(self):
        return ("SHADOW",)


def check_d07_activate_requires_validate():
    rec = _TxRecorder()
    orig = projection.validate
    projection.validate = lambda c, r: {
        "authority_policy_violations": 1, "broken_provenance_refs": 66,
        "non_VERIFIED_contributors": 0, "content_hash_matches": True}
    try:
        projection.activate(rec, 1)
    except ValueError:
        pass
    finally:
        projection.validate = orig
    assert not any(q.startswith("UPDATE") for q in rec.sqls), \
        "activate() promoted a SHADOW with broken provenance refs"


def check_d08_entity_links_fail_closed():
    src = _src("heimdal/pdfhist/entity_links.py")
    assert "except Exception" not in src and "except NameError" not in src, \
        "entity_links swallows exceptions"
    assert "person_names or True" not in src, "COV guard is `... or True`"
    assert "/home/" not in src and '"docs/cov-historical' not in src, \
        "frozen COV map path is hard-coded"
    assert entity_links._load_cov_historical_frozen(None) == {}

    class _NoPin(_SqlRecorder):
        def fetchone(self):
            return None
    try:
        entity_links._resolve_ownership_generation(
            _NoPin(), ownership_generation_pin="own-build:99")
    except RuntimeError:
        return
    raise AssertionError("an unresolvable ownership pin does not raise")


def check_d09_delete_non_active_fk_order():
    rec = _TxRecorder()
    projection.delete_non_active(rec, 1)
    deletes = [q for q in rec.sqls if q.startswith("DELETE")]
    assert deletes and "entity_link" in deletes[0], \
        "delete_non_active() does not delete entity_link rows first"


def check_d10_build_shadow_newest_snapshot():
    rec = _SqlRecorder()
    projection._latest_snapshots(rec, 1)
    assert "valid_from DESC" in rec.sqls[-1], \
        "build-shadow --source-run picks the oldest Felag snapshot"


def check_d13_one_date_precedence():
    assert hasattr(temporal, "governing_date"), \
        "no shared date-precedence helper"
    assert temporal.governing_date(
        {"notice_date": "c", "registration_date": "b",
         "effective_date": "a"}) == "a"
    rec = _SqlRecorder()
    consumer.get_projected_company_history("10", conn=rec,
                                           integration_enabled=True)
    assert temporal.governing_date_sql() in rec.sqls[-1], \
        "company history does not order by the shared precedence"
    for path, inline in (
            ("heimdal/pdfhist/projection.py",
             r'ch\.get\("effective_date"\) or ch\.get'),
            ("heimdal/pdfhist/consumer.py", r"COALESCE\((he\.)?registration_date"),
            ("heimdal/modules/entities/routes.py",
             r'ev\.get\("registration_date"\)\s*or')):
        assert not re.search(inline, _src(path)), \
            f"{path} has its own date precedence"


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    failed = 0
    for fn in (check_d01_flags_off_without_dsn,
               check_d02_ui_off_no_projection_read,
               check_d03_hostile_pdf_bounded,
               check_d04_bootstrap_refuses_live_names,
               check_d03b_history_dedup_before_limit,
               check_d11_flags_off_opens_no_connection,
               check_d06_lysing_keeps_board_changes,
               check_d07_activate_requires_validate,
               check_d08_entity_links_fail_closed,
               check_d09_delete_non_active_fk_order,
               check_d10_build_shadow_newest_snapshot,
               check_d13_one_date_precedence):
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as exc:
            failed += 1
            print("FAIL", fn.__name__, "-", exc)
    sys.exit(1 if failed else 0)
