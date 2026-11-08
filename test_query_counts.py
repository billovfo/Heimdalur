"""W6d, offline. F-13: entity() and the PDF-history flags resolve once per
request, and eigarayvirlit's persons list comes from the rows it also draws.
E-13: the review queue reads every document sha in one query, not one per row."""
from heimdal.app import app
from heimdal.modules.entities.service import persons_behind
from heimdal.modules.review import service as review
from heimdal.modules.shared.entity import entity
from heimdal.pdfhist import consumer, flags


class FakeConn:
    """Counts execute() calls; every row is `row`."""
    def __init__(self, row):
        self.row, self.calls = row, 0

    def execute(self, sql, params=None):
        self.calls += 1
        return self

    def fetchone(self):
        return self.row

    def close(self):
        pass


def test_entity_is_read_once_per_request():
    c = FakeConn({"canonical_entity_id": 1})
    with app.test_request_context():
        assert entity(c, 1) is entity(c, 1)
        entity(c, 2)
        assert c.calls == 2
    with app.test_request_context():
        entity(c, 1)  # a new request reads again
    assert c.calls == 3
    entity(c, 1)
    entity(c, 1)  # outside a request: no memo
    assert c.calls == 5


def test_flags_resolve_once_per_request(monkeypatch):
    monkeypatch.delenv("PDF_HISTORY_INTEGRATION_ENABLED", raising=False)
    monkeypatch.delenv("PDF_HISTORY_UI_ENABLED", raising=False)
    mirror = FakeConn((True,))  # DB mirror: both switches on
    opened = []
    monkeypatch.setattr(consumer, "projection_dsn", lambda: "postgresql://fake/none")
    monkeypatch.setattr(consumer, "_read_only_conn", lambda dsn: opened.append(dsn) or mirror)
    with app.test_request_context():
        assert all(consumer.ui_exposure_allowed() for _ in range(4))
        assert flags.integration_enabled(mirror) and flags.ui_enabled(mirror)
    assert len(opened) == 1 and mirror.calls == 2  # one connection, one read per switch
    with app.test_request_context():
        consumer.ui_exposure_allowed()
    assert len(opened) == 2  # the memo never outlives its request
    flags.ui_enabled(mirror)
    flags.ui_enabled(mirror)  # outside a request: every call reads
    assert mirror.calls == 6


def test_persons_behind_uses_the_given_rows():
    owners = [{"owner_id": 7, "owner_name": "P", "owner_kind": "PERSON", "parsed_percentage": 40},
              {"owner_id": 8, "owner_name": "Co", "owner_kind": "COMPANY", "parsed_percentage": 60}]
    path = {"percentage": 30, "nodes": [], "hops": []}
    rows = [{"counterparty": {"canonical_entity_id": 9, "display_name": "Q",
                              "entity_kind": "PERSON"}, "paths": [path]}]
    tree = persons_behind(owners, rows)
    assert [(p["canonical_entity_id"], p["best_pct"], p["direct"]) for p in tree] == \
        [(7, 40, True), (9, 30, False)]
    assert tree[1]["paths"] == [path] and rows[0]["paths"] == [path]  # rows untouched


def test_classify_rows_reads_all_shas_in_one_query():
    class ShaConn:
        calls, ids = 0, None

        def execute(self, sql, params):
            self.calls += 1
            self.ids = params[0]
            return self

        def fetchall(self):
            return [{"id": i, "sha256": f"sha{i}"} for i in self.ids if i != 2]  # 2: no row

    rows = [{"person_role_id": n, "source_observation_id": n % 4 or None,
             "source_scope": "HISTORICAL_EVENT", "source_event_id": n,
             "entity_normalized": f"p{n}"} for n in range(40)]
    c = ShaConn()
    eligible, counts = review.classify_rows(c, rows)
    assert c.calls == 1 and c.ids == [1, 2, 3]
    assert {r["source_observation_id"]: r["doc_sha256"] for r in eligible} == {1: "sha1", 3: "sha3"}
    assert len(eligible) == 20 and counts == {"NO_STABLE_SOURCE_IDENTITY": 20}
