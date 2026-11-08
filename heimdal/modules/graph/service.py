# heimdal/modules/graph/service.py
"""Pathfinder BFS and the node-link network graph, over a GraphRepository."""
from itertools import islice

from heimdal.modules.shared.templates import pct


def _register_node(nodes, node_id, name, kind):
    """Add a peripheral node to the graph dict if not already present."""
    if node_id not in nodes:
        nodes[node_id] = {
            "id": node_id,
            "name": name,
            "kind": kind,
            "regnr": None,
            "is_center": False,
            "degree": 0
        }


# ponytail: a node cap, not a time cap. 2,000 of ~9,800 entities is "too broad".
PATH_NODE_CAP = 2000


def _paths_of_length(fwd, start, to, hops, limit):
    """The first `limit` paths of exactly `hops` steps from start to `to`, in
    adjacency order (= the order the old path-queue BFS found them). `reaches`
    prunes prefixes that cannot end at `to`, so this never enumerates dead ends."""
    memo = {}

    def reaches(u, k):
        if (u, k) not in memo:
            memo[u, k] = any(v == to if k == 1 else v != to and reaches(v, k - 1)
                             for v, _, _ in fwd.get(u, ()))
        return memo[u, k]

    def walk(path, k):
        for v, e_type, e_label in fwd.get(path[-1][0], ()):
            if k == 1:
                if v == to:
                    yield path + [(v, e_type, e_label)]
            elif v != to and reaches(v, k - 1):
                yield from walk(path + [(v, e_type, e_label)], k - 1)

    return list(islice(walk([(start, None, None)], hops), limit))


def find_entity_paths(repo, from_canonical_id, to_canonical_id, max_depth=4, max_paths=5):
    """Find the shortest paths and parallel connections between two canonical entities.

    Traverses across DIRECT_OWNERSHIP (both ways), REGISTERED_CONTROL_ACTOR (both ways),
    and ROLE relations (NEVND/STJORN/ANNAD).

    Level-synchronous BFS (audit F-4/B-12c): `depth` is the visited set, each
    level is one batched adjacency query (<= max_depth queries), and more than
    PATH_NODE_CAP visited nodes stops the search as too broad. The answer is the
    old path-queue BFS's: every path through nodes at their BFS depth, shortest
    first, then one length longer for as long as each longer length still adds
    paths, up to max_paths.
    """
    if from_canonical_id == to_canonical_id:
        return {"found": True, "length": 0, "paths": [], "nodes": {}, "explanation": "Samme entitet valgt."}

    depth = {from_canonical_id: 0}
    fwd = {}  # node -> [(next node, edge_type, edge_label)]: next is one level deeper, or the target
    frontier = [from_canonical_id]
    found_paths = []
    too_broad = False
    for d in range(max_depth):
        by_source = {}
        for e in repo.get_adjacent_edges(frontier):
            by_source.setdefault(e["source_id"], []).append(e)
        next_frontier = []
        hit = False
        for u in frontier:
            for e in by_source.get(u, ()):
                v = e["target_id"]
                if v == to_canonical_id:
                    hit = True
                elif v not in depth:
                    depth[v] = d + 1
                    next_frontier.append(v)
                elif depth[v] != d + 1:
                    continue  # seen at a shallower level
                fwd.setdefault(u, []).append((v, e["edge_type"], e["edge_label"]))
        if hit:
            found_paths += _paths_of_length(fwd, from_canonical_id, to_canonical_id, d + 1,
                                            max_paths - len(found_paths))
        if len(found_paths) >= max_paths or (found_paths and not hit) or not next_frontier:
            break
        if len(depth) > PATH_NODE_CAP:
            too_broad = True
            break
        frontier = next_frontier

    if not found_paths:
        too_broad_text = (f"Søgningen blev for bred (over {PATH_NODE_CAP} enheder) og blev"
                          " stoppet uden at finde en forbindelse.")
        return {
            "found": False,
            "length": 0,
            "paths": [],
            "nodes": {},
            "intermediary_hubs": [],
            "truncated": too_broad,
            "summary_explanation": too_broad_text if too_broad else "Ingen forbindelse fundet inden for 4 hops rækkevidde.",
            "explanation": too_broad_text if too_broad else "Ingen forbindelse fundet inden for rækkevidde."
        }

    all_node_ids = {nid for pth in found_paths for nid, _, _ in pth}
    nodes_map = {n["canonical_entity_id"]: dict(n) for n in repo.get_entities_by_ids(list(all_node_ids))}

    # Calculate intermediary hub frequencies (nodes appearing in middle of paths)
    hub_counts = {}
    for pth in found_paths:
        if len(pth) > 2:
            for nid, _, _ in pth[1:-1]:
                hub_counts[nid] = hub_counts.get(nid, 0) + 1

    intermediary_hubs = [
        {"canonical_entity_id": nid, "display_name": nodes_map[nid]["display_name"], "entity_kind": nodes_map[nid]["entity_kind"], "path_count": count}
        for nid, count in sorted(hub_counts.items(), key=lambda x: x[1], reverse=True)
        if nid in nodes_map
    ]

    formatted_paths = []
    for pth in found_paths:
        hops = []
        for i in range(len(pth) - 1):
            u_id, _, _ = pth[i]
            v_id, e_type, e_label = pth[i+1]
            hops.append({
                "from_node": nodes_map.get(u_id),
                "to_node": nodes_map.get(v_id),
                "edge_type": e_type,
                "edge_label": e_label or e_type
            })
        formatted_paths.append(hops)

    shortest_len = len(formatted_paths[0])
    summary_exp = f"Fundet {len(formatted_paths)} relationel(le) sti(er). Korteste forbindelse er {shortest_len} hop."

    return {
        "found": True,
        "length": shortest_len,
        "path_count": len(formatted_paths),
        "paths": formatted_paths,
        "nodes": nodes_map,
        "intermediary_hubs": intermediary_hubs,
        "truncated": too_broad,  # longer parallel paths may be missing
        "summary_explanation": summary_exp,
        "explanation": summary_exp
    }


def get_entity_network_graph(repo, canonical_entity_id, min_pct=0.0):
    """Build a structured node-link graph centered on canonical_entity_id.
    Includes ownership, roles, and control registrations, respecting min_pct."""
    center = repo.get_entity_by_id(canonical_entity_id)
    if not center:
        return None

    nodes = {
        canonical_entity_id: {
            "id": canonical_entity_id,
            "name": center["display_name"],
            "kind": center["entity_kind"],
            "regnr": center.get("registration_number"),
            "is_center": True,
            "degree": 0
        }
    }
    edges = []

    # 1. Direct Ownership (Inbound & Outbound)
    direct_rows = repo.get_ownership_rows(canonical_entity_id)

    for r in direct_rows:
        pct_val = float(r["parsed_percentage"]) if r["parsed_percentage"] is not None else None
        if min_pct > 0.0:
            if pct_val is None or pct_val < min_pct:
                continue

        _register_node(nodes, r["owner_id"], r["owner_name"], r["owner_kind"])
        _register_node(nodes, r["owned_id"], r["owned_name"], r["owned_kind"])

        edges.append({
            "id": f"own_{r['relation_id']}",
            "source": r["owner_id"],
            "target": r["owned_id"],
            "type": "DIRECT_OWNERSHIP",
            "label": f"{pct(r['parsed_percentage'])} %" if r["parsed_percentage"] is not None else (r["raw_percentage_label"] or "Ejer"),
            "parsed_percentage": pct_val,
            "confidence": r["semantic_direction_confidence"]
        })

    # 2. Control Registrations (Inbound & Outbound)
    control_rows = repo.get_control_rows(canonical_entity_id)

    for r in control_rows:
        _register_node(nodes, r["actor_id"], r["actor_name"], r["actor_kind"])
        _register_node(nodes, r["registered_id"], r["registered_name"], "COMPANY")
        edges.append({
            "id": f"ctrl_{r['relation_id']}",
            "source": r["actor_id"],
            "target": r["registered_id"],
            "type": "CONTROL",
            "label": r["raw_source_relation_label"] or "Kontrolaktør",
            "parsed_percentage": None,
            "confidence": r["semantic_direction_confidence"]
        })

    # 3. Roles (Inbound & Outbound)
    role_rows = repo.get_role_rows(canonical_entity_id)

    for r in role_rows:
        _register_node(nodes, r["person_id"], r["person_name"], "PERSON")
        _register_node(nodes, r["company_id"], r["company_name"], "COMPANY")
        edges.append({
            "id": f"role_{r['relation_id']}",
            "source": r["person_id"],
            "target": r["company_id"],
            "type": "ROLE",
            "label": r["canonical_role_label"] or r["inferred_group"] or "Rolle",
            "role_group": r["inferred_group"],
            "parsed_percentage": None,
            "confidence": r["semantic_direction_confidence"]
        })

    # Calculate degrees
    for edge in edges:
        if edge["source"] in nodes:
            nodes[edge["source"]]["degree"] += 1
        if edge["target"] in nodes:
            nodes[edge["target"]]["degree"] += 1

    return {
        "center_node": nodes[canonical_entity_id],
        "nodes": list(nodes.values()),
        "edges": edges,
        "links": edges,
        "metrics": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "ownership_edges": sum(1 for e in edges if e["type"] == "DIRECT_OWNERSHIP"),
            "role_edges": sum(1 for e in edges if e["type"] == "ROLE"),
            "control_edges": sum(1 for e in edges if e["type"] == "CONTROL")
        }
    }

