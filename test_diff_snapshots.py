#!/usr/bin/env python3
"""Unit and integration tests for diff_snapshots.py batch diffing engine."""
import pytest
import psycopg
from psycopg.rows import dict_row
import diff_snapshots
from heimdal.pdfhist.db import qa_dsn

TEST_COMPANY_WITH_ROLES = 5708

def conn():
    return psycopg.connect(qa_dsn(), row_factory=dict_row)

def test_record_change_event():
    with conn() as c:
        initial_count = c.execute("SELECT count(*) AS n FROM entity_change_event").fetchone()["n"]
        ev_id = diff_snapshots.record_change_event(
            c,
            canonical_entity_id=TEST_COMPANY_WITH_ROLES,
            change_category="STATUS_CHANGED",
            field_name="test_field",
            old_value="OLD_VAL",
            new_value="NEW_VAL",
            summary="Automatisk test af diff_snapshots hændelseslogning"
        )
        assert ev_id is not None
        new_count = c.execute("SELECT count(*) AS n FROM entity_change_event").fetchone()["n"]
        assert new_count == initial_count + 1

def test_detect_relation_changes_run():
    with conn() as c:
        res = diff_snapshots.detect_relation_changes(c)
        assert "changes_recorded" in res
        assert isinstance(res["changes_recorded"], int)
