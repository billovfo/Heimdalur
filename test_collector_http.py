"""Collector HTTP layer and stop rules, offline (audit 2026-11 A-12).

No network and no DB: urllib.request.urlopen is scripted per test, and every
other socket path raises. The fake connection records the SQL it is sent.
Covers A-4 (PDF backfill through fetch), A-5, A-7, A-8, A-10, A-13 and the
acquire / PDF consecutive-failure stops.
"""
import http.client
import io
import socket
import types
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

import heimdal_collect as hc

ROW = {"fromId": 1, "fromName": "a - P/F, x", "cbTypeId": 1, "cbTypeName": "Eigari 80.0%",
       "toId": 2, "toName": "b - P/F, y"}


class Cur:
    def __init__(self, rows):
        self.rows = list(rows)
        self.rowcount = len(self.rows) or 1

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class FakeConn:
    """Answers by substring (first match wins); RETURNING gets a fresh id."""

    def __init__(self, *answers):
        self.sql, self.commits, self.rollbacks = [], 0, 0
        self.answers = answers

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        self.sql.append((sql, params))
        for key, rows in self.answers:
            if key in sql:
                return Cur(rows(params) if callable(rows) else rows)
        return Cur([(len(self.sql),)] if "RETURNING" in sql else [])

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def params(self, prefix):
        return [p for s, p in self.sql if s.startswith(prefix)]


class Resp(io.BytesIO):
    def __init__(self, body, status=200, headers=b""):
        super().__init__(body)
        self.status, self.headers = status, hdrs(headers)


def hdrs(raw):
    return http.client.parse_headers(io.BytesIO(raw + b"\r\n"))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("network attempted in an offline test")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(hc, "MIN_SECONDS_BETWEEN_REQUESTS", 0)
    monkeypatch.setattr(hc, "_consecutive_5xx", 0)


def script(monkeypatch, *steps):
    """Each step: an exception to raise, a Resp, or (status, headers, body) -> HTTPError."""
    calls, it = [], iter(steps)

    def fake(req, timeout=None):
        calls.append(req.full_url)
        s = next(it)
        if isinstance(s, BaseException):
            raise s
        if isinstance(s, Resp):
            return s
        status, headers, body = s
        raise urllib.error.HTTPError(req.full_url, status, "x", hdrs(headers), io.BytesIO(body))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return calls


def get_table(conn):
    return hc.fetch(conn, 1, "get-table", "/api/get-table/7", {"level": 1}, entity_id=7)


def attempts(conn):
    """request_attempt params as dicts (column order of fetch's INSERT)."""
    cols = ("run", "endpoint", "params", "entity", "requested_at", "received_at", "status",
            "category", "retry", "version", "sha", "error_text")
    return [dict(zip(cols, p)) for p in conn.params("INSERT INTO request_attempt")]


def test_ok_body_is_stored_and_committed_before_it_is_returned(monkeypatch):
    script(monkeypatch, Resp(b'[{"fromId": 1, "fromName": "a - P/F, x", "cbTypeId": 1,'
                             b' "cbTypeName": "Eigari 80.0%", "toId": 2, "toName": "b - P/F, y"}]'))
    conn = FakeConn()
    _aid, sha, payload = get_table(conn)
    assert payload == [ROW] and conn.commits == 1
    assert conn.params("INSERT INTO response_content")[0][0] == sha
    [a] = attempts(conn)
    assert (a["status"], a["sha"], a["error_text"]) == (200, sha, None)


def test_requested_at_is_taken_after_the_rate_limit_wait(monkeypatch):  # A-10
    script(monkeypatch, Resp(b"[]"))
    monkeypatch.setattr(hc, "MIN_SECONDS_BETWEEN_REQUESTS", 0.3)
    monkeypatch.setattr(hc, "_last_request_at", hc.time.monotonic())
    t0 = datetime.now(timezone.utc)
    conn = FakeConn()
    get_table(conn)
    assert attempts(conn)[0]["requested_at"] - t0 >= timedelta(seconds=0.25)


def test_network_error_text_goes_to_the_attempt_not_into_response_content(monkeypatch):  # A-5
    script(monkeypatch, TimeoutError("The read operation timed out"), TimeoutError("again"))
    conn = FakeConn()
    with pytest.raises(hc.EntityFetchFailed) as e:
        get_table(conn)
    assert e.value.status is None
    assert not conn.params("INSERT INTO response_content")
    assert [(a["sha"], a["category"], a["error_text"], a["retry"]) for a in attempts(conn)] == [
        (None, "TIMEOUTERROR", "The read operation timed out", 0),
        (None, "TIMEOUTERROR", "again", 1)]


def test_a_short_body_is_an_error_not_a_body(monkeypatch):  # A-5 with the A-13 cap
    class Sock:
        def makefile(self, _mode):
            return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabcd")
    r = http.client.HTTPResponse(Sock())
    r.begin()
    with pytest.raises(http.client.IncompleteRead):      # the real stdlib response
        hc._read_capped(r)
    script(monkeypatch, http.client.IncompleteRead(b"abcd", 6), Resp(b"[]"))
    conn = FakeConn()
    get_table(conn)
    first, second = attempts(conn)
    assert (first["sha"], first["category"]) == (None, "INCOMPLETEREAD")
    assert "4 bytes read" in first["error_text"] and second["status"] == 200


def test_oversize_body_is_refused_and_not_stored(monkeypatch):  # A-13
    monkeypatch.setattr(hc, "MAX_BODY_BYTES", 8)
    script(monkeypatch, Resp(b"[" + b" " * 40 + b"]"))
    conn = FakeConn()
    with pytest.raises(hc.HardStop, match="body over 8 bytes"):
        get_table(conn)
    assert not conn.params("INSERT INTO response_content")
    assert attempts(conn)[0]["category"] == "BODY_TOO_LARGE"


def test_lowercase_retry_after_is_a_hard_stop(monkeypatch):  # A-7
    calls = script(monkeypatch, (503, b"retry-after: 3600", b"busy"))
    conn = FakeConn()
    with pytest.raises(hc.HardStop) as e:
        get_table(conn)
    assert type(e.value) is hc.HardStop and "Retry-After: 3600" in str(e.value)
    assert len(calls) == 1 and conn.commits == 1          # the attempt was stored first


def test_the_retry_waits_a_backoff_first(monkeypatch):  # A-7
    events = []
    monkeypatch.setattr(hc, "MIN_SECONDS_BETWEEN_REQUESTS", 0.01)
    monkeypatch.setattr(hc.time, "sleep", lambda s: events.append(("sleep", s)))
    steps = iter([(500, b"", b"oops"), Resp(b"[]")])

    def fake(req, timeout=None):
        events.append(("request",))
        s = next(steps)
        if isinstance(s, Resp):
            return s
        raise urllib.error.HTTPError(req.full_url, s[0], "x", hdrs(s[1]), io.BytesIO(s[2]))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    get_table(FakeConn())
    between = events[events.index(("request",)) + 1:-1]
    assert ("sleep", hc.RETRY_BACKOFF_FACTOR * 0.01) in between


@pytest.mark.parametrize("status", [403, 429])
def test_403_and_429_hard_stop_after_the_attempt_is_stored(monkeypatch, status):
    calls = script(monkeypatch, (status, b"", b"denied"))
    conn = FakeConn()
    with pytest.raises(hc.HardStop, match=f"HTTP {status}"):
        get_table(conn)
    assert len(calls) == 1 and attempts(conn)[0]["status"] == status and conn.commits == 1


def test_two_5xx_fail_the_entity_not_the_run(monkeypatch):
    script(monkeypatch, (500, b"", b"a"), (502, b"", b"b"))
    with pytest.raises(hc.EntityFetchFailed) as e:
        get_table(FakeConn())
    assert e.value.status == 502


def test_a_malformed_200_is_stored_then_stops_the_run(monkeypatch):
    script(monkeypatch, Resp(b"<html>oops</html>"))
    conn = FakeConn()
    with pytest.raises(hc.HardStop, match="unparseable"):
        get_table(conn)
    assert conn.params("INSERT INTO response_content")


def test_one_get_table_key_rule_for_acquisition_and_stored_bodies():  # A-2
    extra = [dict(ROW, extra=1)]
    assert not hc.SHAPES["get-table"](extra)
    assert hc.validate_get_table_body(b'[{"fromId": 1, "fromName": "a", "cbTypeId": 1,'
                                      b' "cbTypeName": "x", "toId": 2, "toName": "b",'
                                      b' "extra": 1}]')[:2] == ("FAILED", "BAD_ROW_KEYS")
    assert hc.SHAPES["get-table"]([ROW]) and hc.SHAPES["get-table"]([])
    skipped = []
    assert hc.input_edges(5, "s", 7, b"<html>", skipped) == []
    assert skipped == [(5, "s", 7, "NON_JSON")]


@pytest.mark.parametrize("url", ["https://evil.example/x.pdf", "http://www.skraseting.fo/x.pdf"])
def test_requests_outside_the_host_allowlist_are_refused(monkeypatch, url):  # A-4
    conn = FakeConn()
    with pytest.raises(hc.HardStop, match="host allowlist"):
        hc.fetch(conn, 1, "felag-pdf", url, None, store_body=False)
    assert not conn.sql                                   # no request, nothing recorded


def test_redirects_that_leave_the_host_are_refused():  # A-13
    handler = hc._SameHostRedirect()
    req = urllib.request.Request("https://sg.vs.fo/api/get-table/1")
    with pytest.raises(urllib.error.HTTPError, match="off-host redirect refused"):
        handler.redirect_request(req, io.BytesIO(b""), 302, "Found", hdrs(b""),
                                 "https://evil.example/api")
    same = handler.redirect_request(req, io.BytesIO(b""), 302, "Found", hdrs(b""),
                                    "https://sg.vs.fo/api/get-table/2")
    assert same.full_url == "https://sg.vs.fo/api/get-table/2"


def ended(conn):
    return conn.params("UPDATE collection_run SET run_status")


@pytest.mark.parametrize("exc, code", [(hc.HardStop("stop"), 2), (KeyboardInterrupt(), 130)])
def test_run_guard_records_stops_as_stopped(exc, code):
    conn = FakeConn()

    def body():
        raise exc
    with pytest.raises(SystemExit) as e:
        hc.run_guard(conn, 9, body)
    assert e.value.code == code and ended(conn)[0][0] == "STOPPED"


def test_run_guard_records_a_crash_as_stopped_and_reraises():  # A-8 / C-6
    conn = FakeConn()

    def body():
        raise ConnectionResetError("reset while reading an error body")
    with pytest.raises(ConnectionResetError):
        hc.run_guard(conn, 9, body)
    assert conn.rollbacks == 1
    assert ended(conn) == [("STOPPED", "crashed: ConnectionResetError: reset while reading"
                                       " an error body", 9)]


def test_acquire_stops_after_three_different_entities_fail_in_a_row(monkeypatch):
    queued = [11, 12, 13, 14]
    conn = FakeConn(("pg_try_advisory_lock", [(True,)]),
                    ("ORDER BY source_entity_id LIMIT 1", lambda p: [(queued[0],)] if queued else []),
                    ("SELECT count(*) FROM company_inventory", lambda p: [(len(queued),)]),
                    ("UPDATE company_inventory", lambda p: queued.remove(p[1]) or []))
    monkeypatch.setattr(hc, "db", lambda: conn)
    calls = script(monkeypatch, *[(500, b"", b"down")] * 6)
    with pytest.raises(SystemExit) as e:
        hc.cmd_acquire(types.SimpleNamespace(limit=None))
    assert e.value.code == 2 and len(calls) == 6 and queued == [14]
    assert [p[0] for p in conn.params("UPDATE company_inventory")] == ["sg.vs.fo"] * 3
    assert ended(conn)[0][0] == "STOPPED" and "3 different entities" in ended(conn)[0][1]


def pdf_conn(candidates):
    return FakeConn(("pg_try_advisory_lock", [(True,)]),
                    ("SELECT postal_code FROM geo_postcode", []),
                    ("SELECT count(*) FROM canonical_entity", [(0,)]),   # before: also joins documents
                    ("JOIN source.documents d ON lower", candidates),
                    ("SELECT retry_number + 1", [(1,)]))


def test_pdf_backfill_goes_through_fetch_and_stores_no_pdf_bytes(monkeypatch):  # A-4
    monkeypatch.setattr(hc, "extract_felag_pdf_location", lambda body, _pc: {
        "location_status": "FOREIGN", "postal_code": None, "country_code": "DK"})
    script(monkeypatch, Resp(b"%PDF-1.7 fake"))
    conn = pdf_conn([])
    out = hc._process_pdf_candidate(conn, 3, set(), "sg.vs.fo", 7, 70,
                                    "https://www.skraseting.fo/doc.pdf")
    assert out == "APPLIED" and not conn.params("INSERT INTO response_content")
    [a] = attempts(conn)
    assert (a["endpoint"], a["sha"], a["status"], a["entity"]) == ("felag-pdf", None, 200, 7)
    [pdf] = conn.params("INSERT INTO company_location_pdf_fetch")
    assert pdf[4] and pdf[5] == "APPLIED"                 # the hash is kept, the bytes are not


def test_pdf_backfill_honours_retry_after(monkeypatch):  # A-4 / A-7
    calls = script(monkeypatch, (503, b"Retry-After: 3600", b"busy"))
    with pytest.raises(hc.HardStop, match="Retry-After"):
        hc._process_pdf_candidate(pdf_conn([]), 3, set(), "sg.vs.fo", 7, 70,
                                  "https://www.skraseting.fo/doc.pdf")
    assert len(calls) == 1


def test_pdf_backfill_stops_during_an_outage(monkeypatch):  # A-4: was 5 x 2 requests, no stop
    candidates = [("sg.vs.fo", i, 100 + i, f"https://www.skraseting.fo/private-doc-{i}.pdf")
                  for i in range(5)]
    conn = pdf_conn(candidates)
    monkeypatch.setattr(hc, "db", lambda: conn)
    calls = script(monkeypatch, *[(503, b"", b"down")] * 10)
    with pytest.raises(SystemExit) as e:
        hc.cmd_backfill_pdf_company_locations(types.SimpleNamespace(limit=None))
    assert e.value.code == 2 and len(calls) == 6
    failed = conn.params("INSERT INTO company_location_pdf_fetch")
    assert [(f[1], f[5], f[6], f[7]) for f in failed] == [
        (i, "FAILED", 503, "HTTP_5XX") for i in range(3)]
    reason = ended(conn)[0][1]
    assert "3 different PDFs" in reason and "private-doc" not in reason   # host only, no URL
