"""Pathfinder BFS (audit F-4/B-12c), offline: one adjacency query per level,
a node cap, and the same paths as the old path-queue BFS it replaced."""
import random
from collections import deque

from heimdal.modules.graph.service import PATH_NODE_CAP, find_entity_paths


def old_bfs_paths(adj, start, to, max_depth=4, max_paths=5):
    """The pre-F-4 algorithm verbatim (one query per popped path), as the reference."""
    queue = deque([[(start, None, None)]])
    visited_depth = {start: 0}
    found_paths = []
    min_length_found = None
    while queue:
        current_path = queue.popleft()
        curr_node, _, _ = current_path[-1]
        curr_depth = len(current_path) - 1
        if min_length_found is not None and curr_depth > min_length_found:
            break
        if curr_depth >= max_depth:
            continue
        for nxt, e_type, e_label in adj.get(curr_node, []):
            if nxt == to:
                found_paths.append(current_path + [(nxt, e_type, e_label)])
                min_length_found = curr_depth + 1
                if len(found_paths) >= max_paths:
                    break
            elif nxt not in [n for n, _, _ in current_path]:
                if nxt not in visited_depth or visited_depth[nxt] >= curr_depth + 1:
                    visited_depth[nxt] = curr_depth + 1
                    queue.append(current_path + [(nxt, e_type, e_label)])
        if len(found_paths) >= max_paths:
            break
    return [[(p[i][0], p[i + 1][1], p[i + 1][2], p[i + 1][0]) for i in range(len(p) - 1)]
            for p in found_paths]


class FakeRepo:
    def __init__(self, adj):
        self.adj, self.calls = adj, 0

    def get_adjacent_edges(self, node_ids):
        self.calls += 1
        return [{"source_id": u, "target_id": v, "edge_type": t, "edge_label": lbl}
                for u in node_ids for v, t, lbl in self.adj.get(u, [])]

    def get_entities_by_ids(self, ids):
        return [{"canonical_entity_id": i, "display_name": f"E{i}", "entity_kind": "COMPANY",
                 "registration_number": None} for i in ids]


def random_graph(rng, n, m):
    """Undirected-style adjacency (each edge seen from both ends, like the six
    UNION arms), with parallel edges and self-loops."""
    adj = {}
    for k in range(m):
        a, b = rng.randrange(n), rng.randrange(n)
        t = rng.choice(["DIRECT_OWNERSHIP", "ROLE", "REGISTERED_CONTROL_ACTOR"])
        adj.setdefault(a, []).append((b, t, f"{t}-{k}-out"))
        adj.setdefault(b, []).append((a, t, f"{t}-{k}-in"))
    for edges in adj.values():
        rng.shuffle(edges)
    return adj


def new_paths(result):
    return [[(h["from_node"]["canonical_entity_id"], h["edge_type"], h["edge_label"],
              h["to_node"]["canonical_entity_id"]) for h in p] for p in result["paths"]]


def test_same_paths_as_old_bfs_and_one_query_per_level():
    rng = random.Random(20261106)
    compared = found = longer = 0
    for _ in range(300):
        n = rng.randrange(5, 120)
        adj = random_graph(rng, n, rng.randrange(n, 4 * n))
        a, b = rng.randrange(n), rng.randrange(n)
        if a == b:
            continue
        repo = FakeRepo(adj)
        result = find_entity_paths(repo, a, b)
        expected = old_bfs_paths(adj, a, b)
        assert new_paths(result) == expected, (a, b)
        assert result["found"] == bool(expected)
        assert result["length"] == (len(expected[0]) if expected else 0)
        assert repo.calls <= 4  # one batched adjacency query per BFS level
        compared += 1
        found += bool(expected)
        longer += any(len(p) > len(expected[0]) for p in expected)
    # the sample must exercise found, not-found and the longer "parallel" paths
    assert compared > 250 and 0 < found < compared and longer > 0


def test_node_cap_answers_too_broad():
    hub = {0: [(i, "ROLE", "r") for i in range(1, PATH_NODE_CAP + 2)]}
    repo = FakeRepo(hub)
    result = find_entity_paths(repo, 0, -1)
    assert result["found"] is False and result["truncated"] is True
    assert "for bred" in result["summary_explanation"]
    assert repo.calls == 1


def test_disconnected_and_direct():
    repo = FakeRepo({1: [(2, "ROLE", "Nevndarlimur")], 2: [(1, "ROLE", "Nevndarlimur")]})
    assert find_entity_paths(repo, 1, 3)["found"] is False
    direct = find_entity_paths(FakeRepo(repo.adj), 1, 2)
    assert direct["found"] is True and direct["length"] == 1
    assert new_paths(direct) == [[(1, "ROLE", "Nevndarlimur", 2)]]
