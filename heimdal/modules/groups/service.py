# heimdal/modules/groups/service.py
"""Corporate groups: hierarchy tree, Sankey flow, control metrics."""
from heimdal.modules.shared.entity import entity
from heimdal.modules.groups import repository


def ultimate_parent_of(conn, canonical_entity_id):
    """Find the Ultimate Parent (Moderselskab / Top Holding) via >50% ownership chain."""
    curr_id = canonical_entity_id
    visited = [curr_id]
    
    while True:
        parent = repository.select_majority_parent(conn, curr_id)
        
        if not parent or parent["owner_id"] in visited:
            break
        curr_id = parent["owner_id"]
        visited.append(curr_id)
        
    has_majority_owned = repository.has_majority_owned(conn, canonical_entity_id)
    
    is_in_group = len(visited) > 1 or has_majority_owned
    top_parent_id = visited[-1]
    top_ent = entity(conn, top_parent_id)
    
    return {
        "is_in_group": bool(is_in_group),
        "is_ultimate_parent": top_parent_id == canonical_entity_id,
        "ultimate_parent": top_ent,
        "chain_depth": len(visited) - 1,
        "chain": visited
    }


def corporate_group_tree(conn, ultimate_parent_id):
    """Build the complete corporate hierarchy tree starting from the ultimate parent."""
    parent_ent = entity(conn, ultimate_parent_id)
    if not parent_ent:
        return None
        
    def get_subsidiaries(pid, depth=1, parent_cash_flow=1.0, path=frozenset()):
        # F-3: `path` holds the companies above this one. A majority holding
        # back into it closes an ownership cycle (§15): the direct row is shown,
        # marked as a cycle, with no cash-flow % carried across it, and the walk
        # stops there.
        path = path | {pid}
        subs = repository.select_subsidiaries(conn, pid)

        associated = repository.select_associated(conn, pid)

        tree_subs = []
        for s in subs:
            pct_val = float(s["parsed_percentage"]) if s["parsed_percentage"] is not None else 100.0
            effective_cash_flow = parent_cash_flow * (pct_val / 100.0)
            cycle = s["owned_id"] in path
            tree_subs.append({
                "entity": dict(s),
                "type": "SUBSIDIARY",
                "percentage": s["parsed_percentage"],
                "control_pct": pct_val,
                "cash_flow_pct": None if cycle else round(effective_cash_flow * 100.0, 2),
                "cycle": cycle,
                "depth": depth,
                "children": [] if cycle else get_subsidiaries(
                    s["owned_id"], depth + 1, effective_cash_flow, path)
            })
        for a in associated:
            pct_val = float(a["parsed_percentage"]) if a["parsed_percentage"] is not None else 20.0
            effective_cash_flow = parent_cash_flow * (pct_val / 100.0)
            cycle = a["owned_id"] in path
            tree_subs.append({
                "entity": dict(a),
                "type": "ASSOCIATED",
                "percentage": a["parsed_percentage"],
                "control_pct": pct_val,
                "cash_flow_pct": None if cycle else round(effective_cash_flow * 100.0, 2),
                "cycle": cycle,
                "depth": depth,
                "children": []
            })
        return tree_subs

    children = get_subsidiaries(ultimate_parent_id, depth=1, parent_cash_flow=1.0)
    total_subs = sum(1 for c in children if c["type"] == "SUBSIDIARY")
    return {
        "parent": parent_ent,
        "tree": children,
        "subsidiary_count": total_subs
    }


def calculate_group_control_metrics(conn, ultimate_parent_id):
    """Flatten and summarize cash flow rights vs control rights across a group hierarchy."""
    tree = corporate_group_tree(conn, ultimate_parent_id)
    if not tree:
        return {"subsidiaries": [], "total_subsidiaries": 0}
        
    flat_subs = []
    def flatten(nodes):
        for n in nodes:
            flat_subs.append({
                "owned_id": n["entity"]["owned_id"],
                "owned_name": n["entity"]["owned_name"],
                "type": n["type"],
                "control_pct": n.get("control_pct", float(n["percentage"] or 0)),
                "cash_flow_pct": n.get("cash_flow_pct", float(n["percentage"] or 0)),
                "depth": n["depth"]
            })
            if n.get("children"):
                flatten(n["children"])
                
    flatten(tree["tree"])
    return {
        "parent": tree["parent"],
        "subsidiaries": flat_subs,
        "total_subsidiaries": len(flat_subs)
    }


def corporate_group_flow(conn, ultimate_parent_id):
    """Transform corporate group tree into Sankey nodes and links."""
    tree = corporate_group_tree(conn, ultimate_parent_id)
    if not tree:
        return None
    
    nodes = {}
    links = []
    
    parent = tree["parent"]
    nodes[parent["canonical_entity_id"]] = {
        "id": parent["canonical_entity_id"],
        "name": parent["display_name"],
        "kind": parent["entity_kind"],
        "depth": 0
    }
    
    def traverse(current_id, children, current_depth):
        for child in children:
            cid = child["entity"]["owned_id"]
            if cid not in nodes:
                nodes[cid] = {
                    "id": cid,
                    "name": child["entity"]["owned_name"],
                    "kind": child["entity"]["owned_kind"],
                    "depth": current_depth
                }
            links.append({
                "source": current_id,
                "target": cid,
                "value": child["control_pct"],
                "control_pct": child["control_pct"],
                "cash_flow_pct": child["cash_flow_pct"],
                "cycle": child["cycle"],  # F-3: closes an ownership cycle, no cash-flow %
                "type": child["type"]
            })
            if child.get("children"):
                traverse(cid, child["children"], current_depth + 1)
                
    traverse(parent["canonical_entity_id"], tree["tree"], 1)
    return {
        "parent": dict(parent),
        "nodes": list(nodes.values()),
        "links": links
    }


def all_corporate_groups(conn, limit=50, offset=0, q=None):
    """List all detected corporate groups in the registry, sorted by subsidiary count.
    T5.3 (F-16): optional q narrows on parent name/registration number."""
    rows = repository.select_all_corporate_groups(conn, limit, offset, q=q)
    return [dict(r) for r in rows]
