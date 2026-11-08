"""Audit B-6/C-5 regressions (owner D14), offline: PEP comes only from records
linked to a person by canonical_entity_id, never by name, and with no linked
record screening says "not loaded" instead of "not PEP". Synthetic names only."""
import re
from pathlib import Path

from heimdal.modules.screening.service import batch_screen_entities

ROOT = Path(__file__).resolve().parent


class FakeConn:
    def __init__(self, answer):
        self.sql, self.answer = [], answer

    def execute(self, sql, params=None):
        self.sql.append(sql)
        rows = self.answer(sql, params)
        return type("Cur", (), {"fetchall": lambda s: rows,
                                "fetchone": lambda s: rows[0] if rows else None})()


def _answer(pep_rows):
    def answer(sql, params):
        if "translate(lower(display_name)" in sql:
            return [{"canonical_entity_id": i, "display_name": n, "entity_kind": "PERSON",
                     "registration_number": None, "key": k}
                    for i, n, k in ((11, "Testa Testersen", "testa testersen"),
                                    (12, "Nomen Nescio", "nomen nescio"))]
        if "EXISTS (SELECT FROM v_pep_actors)" in sql:
            return [{"loaded": bool(pep_rows)}]
        if "FROM v_pep_actors" in sql:
            return [r for r in pep_rows if r["canonical_person_id"] in params[0]]
        return []
    return answer


def test_schema_pep_seeds_nothing_and_links_by_id_only():
    sql = (ROOT / "schema_pep.sql").read_text()
    code = re.sub(r"--[^\n]*", "", sql)
    assert not re.search(r"\bINSERT\b", code, re.I)
    assert not re.search(r"\bI?LIKE\b|~~", code, re.I)
    view = code[code.index("CREATE OR REPLACE VIEW v_pep_actors"):]
    assert "ce.canonical_entity_id = pr.canonical_entity_id" in view
    assert "person_name" not in view.split("FROM", 1)[1]  # no name join


def test_no_pep_data_loaded_means_not_loaded_on_every_row():
    rows = batch_screen_entities(FakeConn(_answer([])), ["Testa Testersen", "S1", "Ingen Findes"])
    assert [r["has_pep"] for r in rows] == [None, None, None]
    assert all(r["pep_title"] is None for r in rows)


def test_only_the_linked_person_is_pep():
    pep = [{"canonical_person_id": 11, "office_title": "Testembede"}]
    rows = {r["query"]: r for r in
            batch_screen_entities(FakeConn(_answer(pep)), ["Testa Testersen", "Nomen Nescio"])}
    assert rows["Testa Testersen"]["has_pep"] is True
    assert rows["Testa Testersen"]["pep_title"] == "Testembede"
    assert rows["Nomen Nescio"]["has_pep"] is False and rows["Nomen Nescio"]["pep_title"] is None


def test_screening_ui_and_csv_show_not_loaded():
    js = (ROOT / "static/bulk_screening.js").read_text()  # moved out of the template (G-05)
    assert "r.has_pep === null ? 'PEP-data ikke indlæst'" in js
    assert "r.has_pep === null ? 'NOT_LOADED'" in js
