"""Offline tests for tools/route_matrix.py (lane X5).

A tiny Flask app: a 200 route, a 500 route, a GET that writes, a converter
without a sample, and Flask's static route.  No DB and no network are used.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from flask import Blueprint, Flask

ROOT = Path(__file__).parent
_spec = importlib.util.spec_from_file_location(
    "route_matrix", ROOT / "tools" / "route_matrix.py")
route_matrix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(route_matrix)

CALLS = []
_PASSWORD = "hunter" + "2"


def _url_with_credentials(host="127.0.0.1:8431", path="/path"):
    """A URL carrying credentials; built from parts so no source line holds one."""
    return "http://user:%s@%s%s" % (_PASSWORD, host, path)


@pytest.fixture
def in_process_env(monkeypatch):
    """Explicit read-only DSNs the in-process guard requires."""
    monkeypatch.setenv(
        "HEIMDAL_READ_DB",
        "postgres" + "ql://reader@127.0.0.1:1/route_matrix_test")
    monkeypatch.delenv("HEIMDAL_PDFHIST_PROJECTION_DSN", raising=False)
    for name in route_matrix.PDF_HISTORY_FLAGS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _depth_0():
    return _depth_1()


def _depth_1():
    return _depth_2()


def _depth_2():
    return "ok"


_STORE = object()


def _write_helper():
    CALLS.append("called")
    return "INSERT INTO route_matrix_probe VALUES (1)"


def _make_app(*, boom=False, raise_error=False, no_sample=True,
              writes=True, only_ok=False):
    app = Flask(__name__)

    @app.get("/")
    def root():
        return "ok"

    if only_ok:
        return app
    if boom:
        @app.get("/boom")
        def boom_route():
            return "boom", 500
    if raise_error:
        @app.get("/raise")
        def raise_route():
            raise RuntimeError("offline probe")
    if no_sample:
        @app.get("/entity/<int:entity_id>")
        def entity(entity_id):
            return "entity %d" % entity_id
    if writes:
        @app.get("/writes")
        def write_route():
            return _write_helper()
    return app


def _by_route(report):
    return {entry["route"]: entry for entry in report["routes"]}


def test_discovery_skips_only_static():
    gets, static = route_matrix.discover(_make_app())
    assert [record["rule"] for record in static] == ["/static/<path:filename>"]
    rules = {record["rule"] for record in gets}
    assert "/" in rules
    assert "/entity/<int:entity_id>" in rules
    assert "/writes" in rules
    assert "/static/<path:filename>" not in rules


def test_sample_filling_and_write_skip():
    CALLS.clear()
    report = route_matrix.run(_make_app(), {"entity_id": 7})
    routes = _by_route(report)
    assert routes["/"]["status"] == 200
    assert routes["/entity/<int:entity_id>"]["url"] == "/entity/7"
    assert routes["/entity/<int:entity_id>"]["status"] == 200
    assert routes["/writes"]["status_class"] == "SKIPPED_WRITES"
    assert "review.case" not in report["detected_writes"]  # no such route here
    assert CALLS == []  # the GET that writes was never requested
    assert report["skipped_static"][0]["route"] == "/static/<path:filename>"
    assert route_matrix.exit_code(report) == 0


def test_no_sample_is_counted_and_exit_2():
    report = route_matrix.run(_make_app(writes=False), {})
    entry = _by_route(report)["/entity/<int:entity_id>"]
    assert entry["status"] is None
    assert entry["status_class"] == "NO_SAMPLE"
    assert entry["missing"] == ["entity_id"]
    assert report["summary"]["NO_SAMPLE"] == 1
    assert route_matrix.exit_code(report) == 2


def test_500_and_exception_classification():
    report = route_matrix.run(_make_app(boom=True, writes=False), {})
    routes = _by_route(report)
    assert routes["/boom"]["status"] == 500
    assert routes["/boom"]["status_class"] == "5xx"
    assert route_matrix.exit_code(report) == 1  # 5xx wins over NO_SAMPLE

    report = route_matrix.run(
        _make_app(boom=False, raise_error=True, no_sample=False, writes=False), {})
    entry = _by_route(report)["/raise"]
    assert entry["status_class"] == "EXCEPTION"
    assert entry["error_class"] == "RuntimeError"
    assert route_matrix.exit_code(report) == 1


def test_cli_json_summary_and_exit_codes(tmp_path, capsys, in_process_env):
    ids = tmp_path / "ids.json"
    ids.write_text('{"entity_id": 7}', encoding="utf-8")
    out = tmp_path / "report.json"
    code = route_matrix.main(
        ["--ids", str(ids), "--json", str(out)], app=_make_app())
    captured = capsys.readouterr()
    assert code == 0
    assert "SUMMARY" in captured.err
    assert "routes=" in captured.err
    assert "5xx=0" in captured.err
    saved = out.read_text(encoding="utf-8")
    assert '"status_class": "SKIPPED_WRITES"' in saved
    assert "postgresql://" not in captured.out + captured.err + saved

    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    code = route_matrix.main(
        ["--ids", str(empty), "--json", str(out)], app=_make_app(writes=False))
    captured = capsys.readouterr()
    assert code == 2
    assert "NO_SAMPLE=1" in captured.err

    code = route_matrix.main(
        ["--ids", str(empty), "--json", str(out)],
        app=_make_app(boom=True, writes=False))
    captured = capsys.readouterr()
    assert code == 1
    assert "5xx=1" in captured.err


def test_http_mode_sets_host_header_and_timeout(monkeypatch):
    seen = {}

    class Response:
        status = 302

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Opener:
        def open(self, request, timeout):
            seen["url"] = request.full_url
            seen["host"] = request.get_header("Host")
            seen["timeout"] = timeout
            return Response()

    monkeypatch.setattr(
        route_matrix, "build_opener", lambda *handlers: Opener())
    report = route_matrix.run(
        _make_app(), {"entity_id": 7},
        base_url="http://127.0.0.1:8431", timeout=7)
    assert report["mode"] == "http"
    assert seen["url"] == "http://127.0.0.1:8431/entity/7"
    assert seen["host"] == "127.0.0.1:8431"
    assert seen["timeout"] == 7
    assert _by_route(report)["/"]["status_class"] == "3xx"


def test_redact_never_echoes_a_dsn_or_password():
    # Built at runtime so this file holds no password-bearing DSN literal.
    scheme = "postgres" + "ql"
    dsn = "%s://postgres:%s@127.0.0.1:5432/heimdal_test_x5" % (
        scheme, _PASSWORD)
    keyword = "pass" + "word=" + _PASSWORD
    text = route_matrix.redact(dsn + " " + keyword)
    assert "postgresql://" not in text
    assert _PASSWORD not in text


def test_ids_override_rejects_unknown_or_bad_values(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"company_id": "seven"}', encoding="utf-8")
    try:
        route_matrix.load_ids(str(bad))
    except ValueError as exc:
        assert "positive integer" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("bad id accepted")

    unknown = tmp_path / "unknown.json"
    unknown.write_text('{"not_a_sample": 1}', encoding="utf-8")
    try:
        route_matrix.load_ids(str(unknown))
    except ValueError as exc:
        assert "unknown" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unknown key accepted")


# ---------------------------------------------------------------------------
# Fix round 1
# ---------------------------------------------------------------------------

def test_in_process_refuses_without_explicit_read_db(monkeypatch, capsys):
    monkeypatch.delenv("HEIMDAL_READ_DB", raising=False)
    monkeypatch.delenv("HEIMDAL_PDFHIST_PROJECTION_DSN", raising=False)
    for name in route_matrix.PDF_HISTORY_FLAGS:
        monkeypatch.delenv(name, raising=False)
    code = route_matrix.main([], app=_make_app(only_ok=True))
    captured = capsys.readouterr()
    assert code == 1
    assert "HEIMDAL_READ_DB" in captured.err
    assert "refusing" in captured.err
    assert captured.out == ""  # no report for a refused run


def test_in_process_requires_projection_dsn_when_pdf_flag_on(
        monkeypatch, capsys):
    monkeypatch.setenv(
        "HEIMDAL_READ_DB",
        "postgres" + "ql://reader@127.0.0.1:1/route_matrix_test")
    monkeypatch.setenv("PDF_HISTORY_UI_ENABLED", "true")
    monkeypatch.delenv("HEIMDAL_PDFHIST_PROJECTION_DSN", raising=False)
    code = route_matrix.main([], app=_make_app(only_ok=True))
    captured = capsys.readouterr()
    assert code == 1
    assert "HEIMDAL_PDFHIST_PROJECTION_DSN" in captured.err

    monkeypatch.setenv(
        "HEIMDAL_PDFHIST_PROJECTION_DSN",
        "postgres" + "ql://reader@127.0.0.1:1/route_matrix_test")
    code = route_matrix.main([], app=_make_app(only_ok=True))
    captured = capsys.readouterr()
    assert code == 0
    assert "SUMMARY" in captured.err


def test_http_mode_does_not_require_read_db(monkeypatch, capsys):
    monkeypatch.delenv("HEIMDAL_READ_DB", raising=False)
    monkeypatch.delenv("HEIMDAL_PDFHIST_PROJECTION_DSN", raising=False)
    for name in route_matrix.PDF_HISTORY_FLAGS:
        monkeypatch.delenv(name, raising=False)

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr(
        route_matrix, "build_opener", lambda *handlers: Opener())
    code = route_matrix.main(
        ["--base-url", "http://127.0.0.1:8431"],
        app=_make_app(only_ok=True))
    captured = capsys.readouterr()
    assert code == 0
    assert "SUMMARY" in captured.err


def test_redact_url_userinfo_and_ids_report():
    assert _PASSWORD not in route_matrix.redact(_url_with_credentials())
    assert "<redacted>" in route_matrix.redact(_url_with_credentials())

    app = Flask(__name__)
    bp = Blueprint("search", __name__)

    @bp.get("/api/search")
    def api_search():
        return "ok"

    app.register_blueprint(bp)
    secret_q = _url_with_credentials(host="127.0.0.1", path="/q")
    report = route_matrix.run(app, {"search_q": secret_q})
    payload = json.dumps(report)
    assert _PASSWORD not in payload
    assert report["ids"]["search_q"] != secret_q
    entry = _by_route(report)["/api/search"]
    assert _PASSWORD not in entry["url"]


def test_ids_and_base_url_credentials_are_rejected(tmp_path, capsys,
                                                   in_process_env):
    ids = tmp_path / "secret-ids.json"
    ids.write_text(json.dumps(
        {"search_q": _url_with_credentials(host="host", path="/db")}),
        encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        route_matrix.main(["--ids", str(ids)], app=_make_app(only_ok=True))
    assert raised.value.code == 2
    captured = capsys.readouterr()
    assert _PASSWORD not in captured.out + captured.err

    with pytest.raises(SystemExit) as raised:
        route_matrix.main(
            ["--base-url", _url_with_credentials()],
            app=_make_app(only_ok=True))
    assert raised.value.code == 2
    captured = capsys.readouterr()
    assert _PASSWORD not in captured.out + captured.err


def test_review_case_pin_skips_even_when_body_is_read_only():
    app = Flask(__name__)
    bp = Blueprint("review", __name__)
    calls = []

    @bp.get("/persons/review/<int:person_role_id>")
    def case(person_role_id):
        calls.append(person_role_id)
        return "read only"

    app.register_blueprint(bp)
    detected = route_matrix.writes_on_get(app)
    assert detected["review.case"] == (
        "GET seals/inserts a review packet on first open")
    report = route_matrix.run(app, {"person_role_id": 444012})
    entry = _by_route(report)["/persons/review/<int:person_role_id>"]
    assert entry["status_class"] == "SKIPPED_WRITES"
    assert calls == []


def test_write_behind_a_local_import_is_detected():
    app = Flask(__name__)
    bp = Blueprint("local", __name__)

    @bp.get("/local-write")
    def local_write():
        from heimdal.modules.review import repository as repo
        return str(repo.seal_packet(None, case_key="x", packet={}))

    app.register_blueprint(bp)
    detected = route_matrix.writes_on_get(app)
    assert "local.local_write" in detected
    assert "SQL write" in detected["local.local_write"]
    report = route_matrix.run(app, {})
    assert _by_route(report)["/local-write"]["status_class"] == "SKIPPED_WRITES"


_WRITE_CONST = "UPDATE route_matrix_probe SET value = 1"


def test_module_level_sql_constant_is_detected():
    app = Flask(__name__)
    bp = Blueprint("const", __name__)

    @bp.get("/const-write")
    def const_write():
        db = object()
        return db.execute(_WRITE_CONST)

    app.register_blueprint(bp)
    detected = route_matrix.writes_on_get(app)
    assert "const.const_write" in detected
    assert "SQL write" in detected["const.const_write"]


def test_unresolved_call_fails_closed():
    app = Flask(__name__)
    bp = Blueprint("opaque", __name__)

    @bp.get("/unresolved")
    def unresolved():
        return _STORE.save()

    app.register_blueprint(bp)
    detected = route_matrix.writes_on_get(app)
    assert "opaque.unresolved" in detected
    assert "unresolved call to save" in detected["opaque.unresolved"]


def test_depth_overflow_fails_closed(monkeypatch):
    app = Flask(__name__)
    bp = Blueprint("deep", __name__)

    @bp.get("/deep")
    def deep():
        return _depth_0()

    app.register_blueprint(bp)
    monkeypatch.setattr(route_matrix, "_MAX_CALL_DEPTH", 1)
    detected = route_matrix.writes_on_get(app)
    assert "deep.deep" in detected
    assert "deeper than 1" in detected["deep.deep"]


def test_run_rejects_base_url_credentials():
    with pytest.raises(ValueError):
        route_matrix.run(
            _make_app(only_ok=True), {},
            base_url=_url_with_credentials())


def test_no_redirect_handler_refuses_to_follow():
    request = route_matrix.Request("http://127.0.0.1:1/source")
    handler = route_matrix._NoRedirect()
    assert handler.redirect_request(
        request, None, 302, "Found", {}, "http://127.0.0.1:1/target") is None


def test_skipped_writes_are_printed_with_reason(tmp_path, capsys,
                                                in_process_env):
    ids = tmp_path / "ids.json"
    ids.write_text('{"entity_id": 7}', encoding="utf-8")
    out = tmp_path / "report.json"
    code = route_matrix.main(
        ["--ids", str(ids), "--json", str(out)], app=_make_app())
    captured = capsys.readouterr()
    assert code == 0
    assert "SKIPPED_WRITES /writes -> /writes" in captured.err
    assert "SQL write" in captured.err
