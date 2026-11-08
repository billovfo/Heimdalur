"""Graph module (routes/service/repository, F-11) — offline, no Flask app or DB."""
from flask import Blueprint

from heimdal.modules.graph.repository import GraphRepository
from heimdal.modules.graph.routes import graph_bp
from heimdal.modules.graph.service import find_entity_paths, get_entity_network_graph


def test_graph_repository_has_methods():
    methods = [m for m in dir(GraphRepository) if not m.startswith('_')]
    assert "get_adjacent_edges" in methods
    assert "get_entity_by_id" in methods
    assert "get_entities_by_ids" in methods
    assert "get_ownership_rows" in methods
    assert "get_control_rows" in methods
    assert "get_role_rows" in methods
    # F-10: the snapshot-history port went with the dishonest "historical" endpoints
    assert "get_snapshot_dates" not in methods


def test_find_entity_paths_same_entity():
    """Same entity → found=True, length=0."""
    class FakeRepo:
        def get_adjacent_edges(self, node_id):
            return []
        def get_entities_by_ids(self, ids):
            return []
    result = find_entity_paths(FakeRepo(), 5708, 5708)
    assert result["found"] is True
    assert result["length"] == 0


def test_find_entity_paths_disconnected():
    """No edges → found=False."""
    class FakeRepo:
        def get_adjacent_edges(self, node_id):
            return []
        def get_entities_by_ids(self, ids):
            return [{"canonical_entity_id": cid, "display_name": f"Entity {cid}", "entity_kind": "COMPANY", "registration_number": None} for cid in ids]
    result = find_entity_paths(FakeRepo(), 5708, 5761, max_depth=2)
    assert result["found"] is False
    assert len(result["paths"]) == 0


def test_network_graph_unknown_entity_is_none():
    class FakeRepo:
        def get_entity_by_id(self, canonical_entity_id):
            return None
    assert get_entity_network_graph(FakeRepo(), 1) is None


def test_graph_blueprint_exists():
    assert isinstance(graph_bp, Blueprint)
    assert graph_bp.name == "graph"
