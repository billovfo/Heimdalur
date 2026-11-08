# heimdal/modules/graph/repository.py
"""SQL for the pathfinder and the network graph. The service takes the repository
as an object, so the offline BFS tests pass a fake (test_pathfinder_bfs.py)."""
from heimdal.modules.shared.db import either_side


class GraphRepository:
    """Graph reads on one psycopg connection."""

    def __init__(self, conn):
        self._conn = conn

    def get_adjacent_edges(self, node_ids):
        """Every edge of every node in node_ids, as one query (one BFS level, F-4).
        Ordered by source node, then the arm order below, then target."""
        return self._conn.execute("""
            SELECT source_id, target_id, edge_type, edge_label FROM (
            -- Direct ownership outgoing
            SELECT owner_id AS source_id, owned_id AS target_id, 'DIRECT_OWNERSHIP' AS edge_type,
                   ('Ejer ' || parsed_percentage::text || '%%') AS edge_label, 1 AS arm
            FROM v_direct_ownership WHERE owner_id = ANY(%(ids)s)
            UNION ALL
            -- Direct ownership incoming
            SELECT owned_id, owner_id, 'DIRECT_OWNERSHIP',
                   ('Ejes af (' || parsed_percentage::text || '%%)'), 2
            FROM v_direct_ownership WHERE owned_id = ANY(%(ids)s)
            UNION ALL
            -- Control actor outgoing
            SELECT actor_id, registered_id, 'REGISTERED_CONTROL_ACTOR', 'Veruligur Eigari fyri', 3
            FROM v_control_registration WHERE actor_id = ANY(%(ids)s)
            UNION ALL
            -- Control actor incoming
            SELECT registered_id, actor_id, 'REGISTERED_CONTROL_ACTOR', 'Hevur Veruligan Eigara', 4
            FROM v_control_registration WHERE registered_id = ANY(%(ids)s)
            UNION ALL
            -- Role person -> company
            SELECT person_id, company_id, 'ROLE', canonical_role_label, 5
            FROM v_role_relation WHERE person_id = ANY(%(ids)s)
            UNION ALL
            -- Role company -> person
            SELECT company_id, person_id, 'ROLE', canonical_role_label, 6
            FROM v_role_relation WHERE company_id = ANY(%(ids)s)
            ) e ORDER BY source_id, arm, target_id, edge_label
        """, {"ids": list(node_ids)}).fetchall()

    def get_entity_by_id(self, canonical_entity_id):
        return self._conn.execute(
            "SELECT canonical_entity_id, source_entity_id, entity_kind, display_name, registration_number, inventory_status, typing_status, raw_type_token FROM v_entity WHERE canonical_entity_id = %s",
            (canonical_entity_id,)).fetchone()

    def get_entities_by_ids(self, ids):
        return self._conn.execute(
            "SELECT canonical_entity_id, display_name, entity_kind, registration_number"
            " FROM v_entity WHERE canonical_entity_id = ANY(%s)",
            (list(ids),)).fetchall()

    def get_ownership_rows(self, canonical_entity_id):
        return self._conn.execute(either_side(
            "relation_id, owner_id, owner_name, owner_kind, owned_id, owned_name, owned_kind,"
            " parsed_percentage, raw_percentage_label, semantic_direction_confidence",
            "v_direct_ownership", "owner_id", "owned_id"), {"id": canonical_entity_id}).fetchall()

    def get_control_rows(self, canonical_entity_id):
        return self._conn.execute(either_side(
            "relation_id, actor_id, actor_name, actor_kind, registered_id, registered_name,"
            " raw_source_relation_label, semantic_direction_confidence",
            "v_control_registration", "actor_id", "registered_id"), {"id": canonical_entity_id}).fetchall()

    def get_role_rows(self, canonical_entity_id):
        return self._conn.execute(either_side(
            "relation_id, person_id, person_name, company_id, company_name,"
            " canonical_role_label, inferred_group, semantic_direction_confidence",
            "v_role_relation", "person_id", "company_id"), {"id": canonical_entity_id}).fetchall()
