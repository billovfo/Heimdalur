"""C-1 (audit 2026-11), offline: phase2_build.collect() names an ownership/control
observation's person and company by canonical_entity_id, resolved from the raw
SOURCE ids in registry_ownership.relation, never the source ids themselves.

Synthetic rows only. The canonical and source id spaces overlap on purpose
(canonical 100 is a different person than source 100), so reading the source
ids as canonical ids, as build 8 did, yields a valid-looking but wrong pair."""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import phase2_build  # noqa: E402

ENTITIES = [  # canonical_entity_id, source_entity_id, entity_kind
    {"canonical_entity_id": 1, "source_entity_id": 100, "entity_kind": "PERSON"},
    {"canonical_entity_id": 2, "source_entity_id": 200, "entity_kind": "COMPANY"},
    {"canonical_entity_id": 3, "source_entity_id": 300, "entity_kind": "PERSON"},
    {"canonical_entity_id": 100, "source_entity_id": 7, "entity_kind": "PERSON"},   # trap
    {"canonical_entity_id": 200, "source_entity_id": 8, "entity_kind": "COMPANY"},  # trap
]
T = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)


def rel(rid, subj, obj, kind, pct=None):
    return {"relation_id": rid, "raw_from_source_entity_id": subj, "raw_to_source_entity_id": obj,
            "canonical_subject_entity_id": subj, "canonical_object_entity_id": obj,
            "canonical_relation_type": kind, "inferred_group": None, "parsed_percentage": pct,
            "source_attempt_id": 50 + rid, "first_stored_at": T}


RELATIONS = [rel(1, 100, 200, "DIRECT_OWNERSHIP", 40.0),
             rel(2, 300, 200, "REGISTERED_CONTROL_ACTOR"),
             rel(3, 999, 200, "DIRECT_OWNERSHIP", 10.0)]  # source 999 has no canonical entity


class Conn:
    """Answers each query by the first SQL fragment it contains."""
    def __init__(self, answers):
        self.answers = answers

    def execute(self, sql, params=None):
        for frag, rows in self.answers:
            if frag in sql:
                return Rows(rows)
        raise AssertionError(f"unexpected query: {sql[:80]}")


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


def test_ownership_observations_name_canonical_endpoints(tmp_path, monkeypatch):
    p0 = tmp_path / "phase0.json"
    p0.write_text(json.dumps({"measured_at": "2026-09-01T00:00:00Z"}))
    monkeypatch.setattr(phase2_build, "PHASE0_FILE", str(p0))
    ch = Conn([("registry_ownership.build_meta", [{
                   "build_id": 1, "schema_version": "phase1-v1", "source_relation_count": 3,
                   "source_relation_max_id": 3, "max_source_attempt_id": 53,
                   "max_collection_run_id": 1, "content_hash": "0" * 32}]),
               ("public.canonical_entity", ENTITIES),
               ("relation_evidence_pointer", []),
               ("registry_ownership.relation r", RELATIONS)])
    pd = Conn([("SET search_path", []),
               ("FROM projection_run", [{"projection_run_id": 33, "projection_version": "v",
                                         "code_version": "c", "content_hash": "h",
                                         "status": "ACTIVE", "completed_at": T}]),
               ("link_code_version", [{"link_code_version": "v2"}]),
               ("AUDITOR_CHANGE", [{"n": 0}]),
               ("FROM entity_link", []), ("FROM projection_warning", []),
               ("FROM company_person_role", []), ("FROM company_history_event", [])])
    B = phase2_build.collect(ch, pd)
    got = {(o["source_relation_id"], o["role_context"], o["canonical_person_id"],
            o["canonical_company_id"], o["ownership_percentage"])
           for o in B["observations"] if o["source_family"] == "OWNERSHIP_GRAPH"}
    assert got == {(1, "OWNERSHIP", 1, 2, 40.0), (2, "CONTROL_ACTOR", 3, 2, None)}
    assert B["cov"]["ownership_unresolved_endpoints"] == 1  # relation 3 fails closed
    assert B["cov"]["ownership_observations"] == 2
