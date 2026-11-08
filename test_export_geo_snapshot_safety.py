"""Audit 2026-11 W3c regressions, offline (fake connections, no DB):
F-9/E-14/G-11 CSV formula injection, G-01 geo demo fallback, G-07 raw summary
tokens, B-7 fabricated snapshot seed."""
import csv
import io
import re
from pathlib import Path

import psycopg
import pytest

from heimdal.app import create_app
from heimdal.modules.geo import routes as geo_routes
from heimdal.modules.review import routes as review_routes
from heimdal.modules.shared.csv_export import csv_response
from heimdal.modules.shared.labels import change_summary_label

ROOT = Path(__file__).resolve().parent
EVIL = ["=HYPERLINK(\"http://x\",\"y\")", "+1", "-2+3", "@SUM(A1)", "\tx", "\rx"]


class FakeConn:
    """Answers every query with no rows, like a DB whose company_location is empty."""
    def __init__(self, raise_exc=None, rows=()):
        self.raise_exc, self.rows, self.sql = raise_exc, list(rows), []

    def execute(self, sql, params=None):
        if self.raise_exc:
            raise self.raise_exc
        self.sql.append(sql)
        return self

    def fetchall(self):
        return self.rows

    def fetchone(self):
        last = self.sql[-1]
        if "FROM geo_postcode" in last:
            return (1,)  # the postcode table knows the city
        if "FILTER" in last:
            return {"faroe_islands": 0, "foreign": 0, "unknown": 7}
        return None

    def close(self):
        pass


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_csv_response_neutralises_formula_cells_and_quotes_filename():
    resp = csv_response([dict(zip("abcdef", EVIL)) | {"n": -5, "q": 'a "b"'}],
                        list("abcdef") + ["n", "q"], "x.csv")
    row = next(csv.DictReader(io.StringIO(resp.get_data(as_text=True), newline="")))
    assert [row[k] for k in "abcdef"] == ["'" + v for v in EVIL]
    assert row["n"] == "-5"          # numbers are data, not formulas
    assert row["q"] == 'a "b"'       # embedded quotes survive the round trip
    assert resp.headers["Content-Disposition"] == 'attachment; filename="x.csv"'


def test_review_export_goes_through_csv_response(client, monkeypatch):
    row = {k: None for k in ("decision_id", "case_key", "reviewer", "decided_at", "decision",
                             "chosen_canonical_id", "reason_code", "shown_packet_hash",
                             "application_status", "company", "role")}
    row |= {"owner_note": "=cmd|' /C calc'!A0", "source_name": "@Navn"}
    monkeypatch.setattr(review_routes, "review_allowed", lambda: True)
    monkeypatch.setattr(review_routes, "_projection_conn", lambda: FakeConn(rows=[row]))
    resp = client.get("/persons/review/export")
    assert resp.status_code == 200
    assert resp.headers["Content-Disposition"].startswith('attachment; filename="')
    out = next(csv.DictReader(io.StringIO(resp.get_data(as_text=True), newline="")))
    assert out["owner_note"] == "'=cmd|' /C calc'!A0"
    assert out["source_name"] == "'@Navn"
    assert out["owner_decision"] == ""


def test_no_csv_writer_outside_the_shared_helper():
    hits = [str(p) for p in (ROOT / "heimdal").rglob("*.py")
            if p.name != "csv_export.py" and re.search(r"csv\.(writer|DictWriter)\(", p.read_text())]
    assert hits == []


def test_geo_empty_location_table_means_empty_map(client, monkeypatch):
    fake = FakeConn()
    monkeypatch.setattr(geo_routes, "conn", lambda: fake)
    body = client.get("/api/geo/clusters").get_json()
    assert body == {"cities": [], "coverage": {"faroe_islands": 0, "foreign": 0, "unknown": 7}}
    resp = client.get("/api/geo/companies", query_string={"city": "Tórshavn"})
    assert resp.status_code == 200 and resp.get_json() == {"companies": []}
    # every company query is joined to company_location: no "any 20 companies" fallback
    assert all("company_location" in s for s in fake.sql if "canonical_entity" in s)


def test_geo_missing_tables_is_503_not_demo_data(client, monkeypatch):
    monkeypatch.setattr(geo_routes, "conn",
                        lambda: FakeConn(raise_exc=psycopg.errors.UndefinedTable("no company_location")))
    assert client.get("/api/geo/clusters").status_code == 503
    assert client.get("/api/geo/companies?city=Tórshavn").status_code == 503


def test_geo_repository_has_no_demo_data():
    src = (ROOT / "heimdal/modules/geo/repository.py").read_text()
    assert "_DEMO" not in src and "Tórshavn" not in src


def test_change_summary_tokens_become_labels_and_names_stay():
    assert change_summary_label("Selskab registreret i snapshot med status RETRYABLE_ERROR") == \
        "Selskab registreret i snapshot med status Midlertidig fejl — prøves igen"
    assert "STATUS_CHANGED" not in change_summary_label("x STATUS_CHANGED y")
    assert change_summary_label("Ny kontrolaktør registreret: P/F FAROE SEAFOOD") == \
        "Ny kontrolaktør registreret: P/F FAROE SEAFOOD"
    assert change_summary_label(None) is None


def test_snapshot_ddl_creates_tables_only_with_natural_key():
    sql = (ROOT / "schema_snapshots.sql").read_text()
    code = "\n".join(line.split("--")[0] for line in sql.splitlines())
    assert not re.search(r"\bINSERT\b", code, re.I)
    assert re.search(r"CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_change_event_natural", code)
