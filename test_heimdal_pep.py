#!/usr/bin/env python3
"""PEP on the heimdal clone (audit B-6/C-5, owner D14): v_pep_actors flags only
the person a pep_record row is linked to by canonical_entity_id. A row that
merely carries a person's name flags nobody. Writes roll back; no name is
written into this file or printed."""
import psycopg
from psycopg.rows import dict_row
from heimdal.config import DB_URI


def conn():
    return psycopg.connect(DB_URI, row_factory=dict_row)


def test_no_unlinked_pep_record_and_nothing_flagged_without_an_import():
    with conn() as c:
        assert c.execute("SELECT count(*) AS n FROM pep_record"
                         " WHERE canonical_entity_id IS NULL").fetchone()["n"] == 0
        linked = c.execute("SELECT count(*) AS n FROM pep_record pr JOIN canonical_entity ce"
                           " USING (canonical_entity_id) WHERE ce.entity_kind = 'PERSON'"
                           ).fetchone()["n"]
        assert c.execute("SELECT count(*) AS n FROM v_pep_actors").fetchone()["n"] == linked


def test_a_name_flags_nobody_and_a_link_flags_one_person():
    with conn() as c:
        try:
            # Two persons sharing one display name (a homonym pair), picked by SQL.
            a, b = c.execute(  # unpacking: exactly two
                "SELECT canonical_entity_id FROM canonical_entity WHERE entity_kind = 'PERSON'"
                " AND display_name = (SELECT display_name FROM canonical_entity"
                "  WHERE entity_kind = 'PERSON' GROUP BY display_name HAVING count(*) > 1"
                "  ORDER BY display_name LIMIT 1) ORDER BY canonical_entity_id LIMIT 2"
            ).fetchall()
            c.execute("INSERT INTO pep_record (person_name, office_title, pep_category)"
                      " SELECT display_name, 'Testembede', 'POLITICAL' FROM canonical_entity"
                      " WHERE canonical_entity_id = %s", (a["canonical_entity_id"],))
            assert c.execute("SELECT count(*) AS n FROM v_pep_actors").fetchone()["n"] == 0
            c.execute("INSERT INTO pep_record (person_name, office_title, pep_category,"
                      " canonical_entity_id) VALUES ('Testa Testersen', 'Testembede', 'POLITICAL', %s)",
                      (a["canonical_entity_id"],))
            got = c.execute("SELECT canonical_person_id FROM v_pep_actors").fetchall()
            assert [r["canonical_person_id"] for r in got] == [a["canonical_entity_id"]]  # never b
        finally:
            c.rollback()
