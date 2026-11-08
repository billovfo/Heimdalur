# heimdal/modules/entities/service.py
"""Entities module: domain logic for entity browsing, ownership, control, roles."""
import math
from decimal import Decimal

from heimdal.config import (
    HUNDRED, INCOMING, OUTGOING,
    PERSON_OWNERS_CAP, PAGE_SIZE, NETWORK_NODE_CAP,
)
from heimdal.modules.shared.entity import entity
from heimdal.modules.shared.templates import pct
from heimdal.modules.shared.labels import ownership_shape_label
from heimdal.modules.entities import repository

# --- entity kinds and sort keys (from heimdal_portal.py:1193, 1267) ---
COMPANY_KINDS = ("COMPANY", "ORGANIZATION", "PUBLIC_AUTHORITY", "UNKNOWN_ENTITY")
PERSON_KINDS = ("PERSON",)
PERSON_SORT_KEYS = ("name", "id", "direct", "indirect", "combined", "nevn", "stjorn")

# --- population state (from heimdal_portal.py:1148-1151) ---
PARTIAL_POPULATION = "PARTIAL_POPULATION"
ALL_TERMINAL = "ALL_TERMINAL"
_UNRESOLVED = ("DISCOVERED", "QUEUED", "RETRYABLE_ERROR")

RELATION_BASIS_LABELS = {
    "DIRECT_OWNERSHIP": "Direkte ejerskab",
    "INDIRECT_OWNERSHIP": "Indirekte ejerskab",
    "SHARED_OWNER": "Fælles ejer",
    "SHARED_BENEFICIAL_OWNER": "Fælles registreret kontrolaktør",
    "SHARED_BOARD_PERSON": "Fælles bestyrelsesperson",
    "SHARED_MANAGEMENT_PERSON": "Fælles ledelsesperson",
    # Product UX consolidation (§28): "Cross-role-person" was implementation
    # vocabulary. The source semantics are unchanged — a person observed in
    # more than one role family at the same counterparty.
    "CROSS_ROLE_PERSON": "Fælles person på tværs af roller",
    "SHARED_OTHER_RELATION": "Fælles relation (Annað)",
    "DIRECT_CONTROL_ACTOR": "Direkte kontrolaktør",
}

PERSON_RELATION_BASIS_LABELS = {
    "SHARED_OWNER": "Fælles ejer",
    "SHARED_BENEFICIAL_OWNER": "Fælles registreret kontrolaktør",
    "SHARED_BOARD_PERSON": "Fælles bestyrelsesperson (Bestyrelse)",
    "SHARED_MANAGEMENT_PERSON": "Fælles ledelsesperson (Ledelse)",
    "CROSS_ROLE_PERSON": "Fælles person på tværs af roller",
    "SHARED_OTHER_RELATION": "Fælles relation (Annað)",
}

# --- product role vocabulary (§13): Danish product labels for normal UI;
# the source's own Faroese title stays in the evidence (Vis kilde renders
# raw_source_relation_label). Deterministic translation of the OBSERVED
# source titles — no inference: any label not in the map keeps its source
# spelling as the product label rather than inventing a translation.
# Remediation plan v1 T3.1: the canonical vocabulary lives in shared/labels.py
# (ROLE_TOKEN_LABELS — binding tokens + preserved correct mappings).
# Unknown titles fall back to a neutral product phrase there ("Registreret
# rolle"), never an invented translation; the source spelling remains visible
# in source/evidence disclosures.
from heimdal.modules.shared.labels import product_role_label as _shared_role_label


def product_role_label(canonical_role_label, raw_label=None):
    """Danish product label for a source role title (§13 / plan v1 T3.1).
    Unknown titles → neutral fallback; never a guessed translation."""
    return _shared_role_label(canonical_role_label, raw_label)

# §12 semantic person split: which source role families are GOVERNANCE
# (board/management) and in which display order they render.
GOVERNANCE_RANK = {
    "Bestyrelsesformand": 0,
    "Næstformand": 1,
    "Bestyrelsesmedlem": 2,
    "Direktør": 3,
    "Medarbejderrepræsentant": 4,
}


def governance_summary(company_id, roles):
    """§9 compact current-governance summary from the Felag authoritative
    current snapshot (the same rows §16.3 renders — no second authority).

    `roles` is the grouped roles_at_company() dict (NEVND/STJORN/ANNAD).
    Returns {'board': [{'person_id','name','role','source_role',
    'raw_source_relation_label'}...], 'management': [...], 'other': [...],
    'total': n} or None when the company has no current Felag roles at all.
    Order inside each bucket follows GOVERNANCE_RANK, then name —
    deterministic, no invented seniority.
    """
    board, management, other = [], [], []
    total = 0
    for group_name, bucket in (("NEVND", board), ("STJORN", management)):
        for r in roles.get(group_name) or []:
            label = product_role_label(r["canonical_role_label"])
            raw_label = (r["raw_source_relation_label"]
                         if "raw_source_relation_label" in r.keys() else
                         r.get("raw_source_relation_label"))
            entry = {"person_id": r["person_id"], "name": r["person_name"],
                     "role": label, "source_role": r["canonical_role_label"],
                     "raw_source_relation_label": raw_label}
            # NEVND governance roles form the Bestyrelse list; everything else
            # (Ledelse-stillinger, medarbejderrepræsentanter, øvrige) følger
            # sin egen kildegruppe — ingen omklassificering.
            if group_name == "STJORN":
                management.append((GOVERNANCE_RANK.get(label, 9), entry))
            elif group_name == "NEVND" and label in ("Bestyrelsesformand",
                                                     "Næstformand",
                                                     "Bestyrelsesmedlem"):
                bucket.append((GOVERNANCE_RANK[label], entry))
            else:
                other.append((GOVERNANCE_RANK.get(label, 9), entry))
            total += 1
    board.sort(key=lambda t: (t[0], t[1]["name"]))
    management.sort(key=lambda t: (t[0], t[1]["name"]))
    other.sort(key=lambda t: (t[0], t[1]["name"]))
    if total == 0:
        return None
    return {"board": [e for _, e in board], "management": [e for _, e in management],
            "other": [e for _, e in other], "total": total}

# --- ownership (§16.1/§16.2, from heimdal_portal.py:53-98) -------------------

def owners_of(conn, canonical_entity_id):
    """§16.1: rows where this entity is the OWNED side. Incoming only."""
    rows = repository.select_owners_of(conn, canonical_entity_id)
    for r in rows:
        r["direction"] = INCOMING
    return rows


def holdings_of(conn, canonical_entity_id):
    """§16.2: rows where this entity is the OWNER side. Outgoing only."""
    rows = repository.select_holdings_of(conn, canonical_entity_id)
    for r in rows:
        r["direction"] = OUTGOING
    return rows


# --- sum footer (§16.1 / §16.2, always rendered) ------------------------------
SUM_UNDER = "UNDER_100"
SUM_EXACT = "EXACTLY_100"
SUM_OVER = "OVER_100"


def ownership_sum(rows):
    """Total of the source's own percentages. Heimdal never normalizes them.

    A FAILED parse contributes nothing to the total and is counted separately —
    it must never be silently read as 0 (SoT §14).
    """
    parsed = [r["parsed_percentage"] for r in rows
              if r["parsed_percentage"] is not None]
    total = sum(parsed, Decimal(0))
    unreadable = sum(1 for r in rows if r["percentage_parse_status"] == "FAILED")
    if total == HUNDRED:
        state = SUM_EXACT
    elif total > HUNDRED:
        state = SUM_OVER
    else:
        state = SUM_UNDER
    return {"total": total, "state": state, "unreadable": unreadable,
            "remainder": HUNDRED - total, "excess": total - HUNDRED,
            "row_count": len(rows), "largest": max(parsed, default=None)}


# --- kontrolregistrering (§16.1a / §17.1a, from heimdal_portal.py:101-163) ---
CONTROL_REGISTERED = "REGISTERED_ACTORS"        # §14a.1
CONTROL_NONE_REGISTERED = "NO_REGISTERED_CONTROL_ACTOR"   # §14a.2 — a source claim
CONTROL_NO_DATA = "NO_CONTROL_DATA"             # absence of data — NOT the same claim

# Which query this dict came from — never conflate the two questions
# ("who is registered as OUR control actor" vs "what are WE registered as
# control actor of") behind one shared shape.
CONTROL_SIDE_REGISTERED = "REGISTERED"          # §16.1a: this entity is registered_id
CONTROL_SIDE_ACTOR = "ACTOR"                    # §17.1a: this entity is actor_id


def control_state(actor_rows, has_no_actor_status):
    """Three states, never two. §14a.2's positive statement and the absence of
    any kontrolregistrering at all are different claims about the world."""
    if actor_rows:
        return CONTROL_REGISTERED
    if has_no_actor_status:
        return CONTROL_NONE_REGISTERED
    return CONTROL_NO_DATA


def _no_actor_status(conn, source_entity_id):
    return repository.select_no_actor_status(conn, source_entity_id)


def control_registration(conn, canonical_entity_id, source_entity_id):
    """§16.1a: registered_id literal — who is registered as an actor FOR this
    entity. Never merged with the actor side below: the two sides answer
    opposite questions and a shared query is exactly the shape §16.2's
    865-false-positive incident came in."""
    actors = repository.select_control_registration(
        conn, canonical_entity_id, source_entity_id)
    status = _no_actor_status(conn, source_entity_id)
    return {"side": CONTROL_SIDE_REGISTERED,
            "state": control_state(actors, status is not None),
            "actors": actors, "registrations": [],
            "raw_status_label": status["raw_source_label"] if status else None}


def control_registrations_of_actor(conn, canonical_entity_id, source_entity_id):
    """§17.1a: actor_id literal — which entities this entity is registered as a
    control actor for. Same three states, same resolver, opposite side."""
    registrations = repository.select_control_registrations_of_actor(
        conn, canonical_entity_id, source_entity_id)
    # §14a.2's status belongs to the entity it was registered on. For a person
    # the source has never recorded one (0 rows, measured 2026-08-03) — the
    # lookup stays anyway so state 2 and state 3 can never silently merge into
    # one "nothing here" branch on this page either.
    status = _no_actor_status(conn, source_entity_id)
    return {"side": CONTROL_SIDE_ACTOR,
            "state": control_state(registrations, status is not None),
            "actors": [], "registrations": registrations,
            "raw_status_label": status["raw_source_label"] if status else None}


# --- roles (§16.3 / §17.3, from heimdal_portal.py:166-229) -------------------
ROLE_GROUPS = ("NEVND", "STJORN", "ANNAD")


def roles_at_company(conn, canonical_entity_id):
    """§16.3: Nevnd/Stjórn/Annað relations for this company, grouped.
    Eigarar (owners) are never duplicated here — see §16.1 above. EIGARAR is
    also excluded from this query outright: inferred_group's fourth value is
    not ownership at all, it's Atkvøðurættur (voting rights, cbTypeId 101) —
    see voting_rights_at_company below, its own concept and its own section."""
    rows = repository.select_roles_at_company(conn, canonical_entity_id)
    groups = {g: [] for g in ROLE_GROUPS}
    for r in rows:
        groups[r["inferred_group"]].append(r)
    return groups


def voting_rights_at_company(conn, canonical_entity_id):
    """Atkvøðurættur (voting rights, cbTypeId 101) — a distinct concept from
    ownership (§14) and from Nevnd/Stjórn/Annað roles. Shown in its own
    small section, never hidden, never guessed into the wrong category."""
    return repository.select_voting_rights_at_company(conn, canonical_entity_id)


def roles_held_by_person(conn, canonical_entity_id):
    """§17.3: Nevnd/Stjórn/Annað relations this person holds, grouped.
    Mirrors roles_at_company's fix (Task 1): inferred_group's fourth value,
    EIGARAR, is Atkvøðurættur (voting rights, cbTypeId 101) — not a role — and
    is excluded here outright, shown separately by voting_rights_held_by_person
    instead. Applied from the start: this is the identical live-reachable
    crash roles_at_company had before it was fixed."""
    rows = repository.select_roles_held_by_person(conn, canonical_entity_id)
    groups = {g: [] for g in ROLE_GROUPS}
    for r in rows:
        groups[r["inferred_group"]].append(r)
    return groups


def voting_rights_held_by_person(conn, canonical_entity_id):
    """Atkvøðurættur (voting rights, cbTypeId 101) this person holds — the
    person-side mirror of voting_rights_at_company. Its own small section,
    never hidden, never guessed into NEVND/STJORN/ANNAD."""
    return repository.select_voting_rights_held_by_person(
        conn, canonical_entity_id)


# --- indirect ownership (§15 / §16.1 / §16.2, from heimdal_portal.py:761-845) --

def cycle_info(conn, source_entity_id):
    """None, or the cycle this entity sits in. SoT §15: a cycle member never
    gets a computed indirect percentage — the cycle is explained instead."""
    return repository.select_cycle_info(conn, source_entity_id)


def indirect_owners_of(conn, source_entity_id):
    """§16.1 indirect side. owned_entity_id literal — incoming only."""
    return repository.select_indirect_owners_of(conn, source_entity_id)


def indirect_holdings_of(conn, source_entity_id):
    """§16.2 indirect side. owner_entity_id literal — outgoing only."""
    return repository.select_indirect_holdings_of(conn, source_entity_id)


def indirect_view(conn, source_entity_id, direction):
    """§16.1/§16.2 indirect side for the company and person pages, with the
    owner D8 2026-11-06 marker (C-3): a chain that runs through an ownership
    cycle never gets an indirect percentage, so it is named instead of left
    out. `indirect` rows whose counterparty has such a chain as well get
    `withheld_cycles`; counterparties reached only that way are `withheld`."""
    incoming = direction == INCOMING
    rows = hydrate_paths(conn, (indirect_owners_of if incoming else indirect_holdings_of)(
        conn, source_entity_id))
    passing = {m["counterparty_source_id"]: m for m in
               repository.select_cycle_passing(conn, source_entity_id, incoming)}
    for r in rows:
        m = passing.pop(r["counterparty_source_id"], None)
        r["withheld_cycles"] = m["cycle_group_ids"] if m else None
    return {"indirect": rows, "withheld": list(passing.values())}


def hydrate_paths(conn, rows):
    """contributing_paths stores source entity IDs and a percentage product only.

    Turn each into a renderable chain: named nodes plus the per-hop edge that
    connects them, so "A —100%→ B —51%→ C" can be shown instead of a bare
    number. Two queries total, regardless of how many paths there are.
    """
    source_ids = {sid for r in rows for p in r["contributing_paths"] for sid in p["path"]}
    if not source_ids:
        for r in rows:
            r["paths"] = []
            r["counterparty"] = None
        return rows
    nodes = repository.select_hydrate_paths_nodes(conn, source_ids)
    canonical_ids = [n["canonical_entity_id"] for n in nodes.values()]
    edges = repository.select_hydrate_paths_edges(conn, canonical_ids)

    for r in rows:
        r["counterparty"] = nodes.get(r["counterparty_source_id"])
        r["paths"] = []
        for p in r["contributing_paths"]:
            chain = [nodes.get(sid) for sid in p["path"]]
            hops = [edges.get((a["canonical_entity_id"], b["canonical_entity_id"]))
                    if a and b else None
                    for a, b in zip(chain, chain[1:])]
            r["paths"].append({"nodes": chain, "hops": hops,
                               "percentage": p["percentage"]})
        r["shown_path_count"] = len(r["paths"])
    return rows


def top_persons(persons, cap=PERSON_OWNERS_CAP):
    """Ren funktion: cap'et er kun vist-niveau — dataene bag bevares intakte."""
    return persons[:cap]

# --- ejertræ + ejerayvirlit (§21a, from heimdal_portal.py:764-890) -----------

TREE_ROW_SPACING = 110
TREE_COL_SPACING = 150
TREE_MARGIN = 60


def ownership_tree_layout(ent, rows, direct_rows):
    """Lagdelt ejertræ til §21a-grafen: virksomheden i bunden (dybde 0),
    ejerne ovenover. ALLE grene med — hver række i indirect_ownership
    bidrager med sine stier, både grene der ender i personer og grene der
    ender i døde ender (virksomheder uden registrerede ejere — de er selv
    det "seneste" lag). Ingen cap: "det hele" skal med (ejerbeslutning
    2026-08-16). Noder dedupes pr. entity ved mindste dybde; blade får
    fortløbende kolonner, forældre centreres over deres børn. Træ-tabellen
    (persons-listen) forbliver den primære tilgængelige visning."""
    nodes = {}
    edges = {}

    def add_node(nid, name, kind, depth):
        cur = nodes.get(nid)
        if cur is None or depth < cur["depth"]:
            nodes[nid] = {"id": nid, "name": name, "kind": kind, "depth": depth}

    add_node(ent["canonical_entity_id"], ent["display_name"], ent["entity_kind"], 0)
    for r in rows:
        cp = r["counterparty"]
        if cp is None:
            continue
        for path in r["paths"]:
            chain = [n for n in path["nodes"] if n is not None]
            if not chain:
                continue
            for depth, node in enumerate(reversed(chain)):
                add_node(node["canonical_entity_id"], node["display_name"],
                         node["entity_kind"], depth)
            for a, b, hop in zip(chain, chain[1:], path["hops"]):
                if a is None or b is None or hop is None or \
                        hop.get("parsed_percentage") is None:
                    continue
                key = (b["canonical_entity_id"], a["canonical_entity_id"])
                if key[0] == key[1]:
                    continue  # selv-ejerskab tegnes aldrig som løkke (§15)
                edges.setdefault(key, hop["parsed_percentage"])
    for r in direct_rows:
        if r["parsed_percentage"] is None or r["owner_id"] == ent["canonical_entity_id"]:
            continue  # selv-ejerskab: cyklus-forklaringen dækker det
        add_node(r["owner_id"], r["owner_name"], r["owner_kind"], 1)
        edges.setdefault((ent["canonical_entity_id"], r["owner_id"]),
                         r["parsed_percentage"])

    children = {}
    for parent, child in edges:
        children.setdefault(parent, []).append(child)
    column = {"n": 0}
    x_by_id = {}

    def assign(nid):
        kids = sorted({k for k in children.get(nid, []) if k in nodes},
                      key=lambda k: nodes[k]["name"])
        if not kids:
            x_by_id[nid] = column["n"]
            column["n"] += 1
            return
        for k in kids:
            assign(k)
        x_by_id[nid] = sum(x_by_id[k] for k in kids) / len(kids)

    assign(ent["canonical_entity_id"])
    for nid in nodes:
        if nid not in x_by_id:
            x_by_id[nid] = column["n"]
            column["n"] += 1

    max_depth = max(n["depth"] for n in nodes.values())
    leaves = column["n"]
    width = max(640, TREE_MARGIN * 2 + (leaves - 1) * TREE_COL_SPACING)
    height = TREE_MARGIN * 2 + max_depth * TREE_ROW_SPACING
    pos = {}
    out_nodes = []
    for nid, n in nodes.items():
        x = TREE_MARGIN + x_by_id[nid] * TREE_COL_SPACING
        y = height - TREE_MARGIN - n["depth"] * TREE_ROW_SPACING
        pos[nid] = {"x": x, "y": y}
        out_nodes.append({"id": nid, "name": n["name"], "kind": n["kind"],
                          "x": x, "y": y, "is_center": nid == ent["canonical_entity_id"]})
    out_edges = [{"from_id": a, "to_id": b, "pct": pct} for (a, b), pct in edges.items()]
    return {"nodes": out_nodes, "edges": out_edges, "pos": pos,
            "width": width, "height": height}


def person_owners_tree(conn, canonical_entity_id, source_entity_id):
    """Ejerayvirlit (§21a): alle personer bag virksomheden — direkte (1 hop)
    og via ejerkæder af vilkårlig længde. Kun DIRECT_OWNERSHIP-kanter indgår
    (indirect_ownership er bygget over dem). Hver person optræder én gang;
    best_pct er den stærkeste sti (maks. produkt) — aldrig en sum, jf.
    ejerbeslutning 2026-08-15. Cyklus-kanter er allerede fraværende i
    indirect_ownership (§15)."""
    return persons_behind(owners_of(conn, canonical_entity_id),
                          hydrate_paths(conn, indirect_owners_of(conn, source_entity_id)))


def persons_behind(owners, rows):
    """Pure core of person_owners_tree over owners_of rows and hydrated
    indirect paths, so a page that also draws the tree reads them once (F-13)."""
    by_person = {}

    def entry(pid, name, kind):
        return by_person.setdefault(pid, {
            "canonical_entity_id": pid, "display_name": name, "entity_kind": kind,
            "best_pct": None, "direct": False, "paths": []})

    for r in owners:
        if r["owner_kind"] != "PERSON":
            continue
        e = entry(r["owner_id"], r["owner_name"], r["owner_kind"])
        e["direct"] = True
        if r["parsed_percentage"] is not None and (
                e["best_pct"] is None or r["parsed_percentage"] > e["best_pct"]):
            e["best_pct"] = r["parsed_percentage"]

    for r in rows:
        cp = r["counterparty"]
        if cp is None or cp["entity_kind"] != "PERSON":
            continue
        e = entry(cp["canonical_entity_id"], cp["display_name"], cp["entity_kind"])
        e["paths"].extend(r["paths"])
        best = max((path["percentage"] for path in r["paths"]), default=None)
        if best is not None and (e["best_pct"] is None or best > e["best_pct"]):
            e["best_pct"] = best

    persons = list(by_person.values())
    persons.sort(key=lambda e: (e["best_pct"] is None, -(e["best_pct"] or 0),
                                e["display_name"]))
    return persons


def owner_chain_rows(tree):
    """F-2: the flat view of person_owners_tree for the KYC table, the company
    dossier and ejarayvirlit.csv — the one place its keys are mapped.
    person_name, path_type ('Ejerform') and path_desc ('Ejersti & Mellemled')."""
    def chain(path):
        names = " → ".join(n["display_name"] if n else "ukendt" for n in path["nodes"])
        return f"{names} ({pct(path['percentage'])} %)"
    return [{**p, "person_name": p["display_name"],
             "path_type": ownership_shape_label(p),
             "path_desc": "; ".join((["Direkte ejer"] if p["direct"] else [])
                                    + [chain(path) for path in p["paths"]])}
            for p in tree]


# --- oversigt + lister (§16 forside / §16 / §17, from heimdal_portal.py:771-965) ---

def population_state(unresolved):
    """Kun det, porten selv kan se. FULL_POPULATION kræver desuden
    indsamlerens nøglerums- og hultjek og kan derfor aldrig hævdes herfra."""
    return PARTIAL_POPULATION if unresolved else ALL_TERMINAL


def overview(conn):
    row = repository.select_overview(conn, list(_UNRESOLVED))
    row["state"] = population_state(row["unresolved"])
    return row


def normalize_faroese_query(text):
    """Normalize Faroese special characters for tolerant search matching."""
    if not text:
        return ""
    trans = str.maketrans({
        'á': 'a', 'Á': 'A',
        'ð': 'd', 'Ð': 'D',
        'í': 'i', 'Í': 'I',
        'ó': 'o', 'Ó': 'O',
        'ú': 'u', 'Ú': 'U',
        'ý': 'y', 'Ý': 'Y',
        'æ': 'ae', 'Æ': 'Ae',
        'ø': 'o', 'Ø': 'O',
        'ö': 'o', 'Ö': 'O'
    })
    return text.translate(trans).lower()


def entity_list(conn, kinds, query, page, sort="name", per_page=PAGE_SIZE, ownership_band=None, kind=None):
    """§22: query matches either display_name or registration_number with Faroese normalization."""
    page = max(1, page)
    norm_like = f"%{normalize_faroese_query(query)}%" if query else "%"
    result = repository.select_entity_list(
        conn, kinds, query, (page - 1) * per_page, per_page, sort,
        ownership_band, kind, norm_like)
    return {"rows": result["rows"], "total": result["total"], "page": result["page"],
            "per_page": per_page, "pages": result["pages"], "query": query or "",
            "sort": "-name" if sort == "-name" else "name",
            "ownership_band": ownership_band,
            "kind": kind}


def person_list(conn, query, page, sort="name", per_page=PAGE_SIZE):
    """§17: person registry with Faroese fuzzy search."""
    page = max(1, page)
    desc = sort.startswith("-")
    key = sort.lstrip("-")
    if key not in PERSON_SORT_KEYS:
        key, desc = "name", False
    norm_like = f"%{normalize_faroese_query(query)}%" if query else "%"
    result = repository.select_person_list(
        conn, query, (page - 1) * per_page, per_page, key, desc, norm_like)
    return {"rows": result["rows"], "total": result["total"], "page": result["page"],
            "per_page": per_page, "pages": result["pages"], "query": query or "",
            "sort": ("-" if desc else "") + key}


# --- §21 netværk (ego graph, depth 1, from heimdal_portal.py:968-1063) -------

def ego_graph(conn, canonical_entity_id, source_entity_id):
    """§21: depth-1 ego graph. Ownership edges from v_direct_ownership,
    control-registration edges from v_control_registration, role edges from
    v_role_relation — each converted to the same {from_id, to_id, kind,
    label} shape so the template renders one edge list, not three.

    v_control_registration does not expose the registered-entity's
    entity_kind (only actor_kind). When this entity is the actor_id side,
    the OTHER entity (registered_id) has no kind available from this view —
    schema_read.sql is deliberately not extended for this one field (out of
    scope for this task), so that side defaults to "UNKNOWN_ENTITY". The
    node still renders and links correctly (entity_url routes every kind to
    its right surface — /persons/, /companies/, or the /entities/ fallback —
    which is always right here); only its glyph/color
    is generic instead of kind-specific. Flagged here, not silent."""
    center = entity(conn, canonical_entity_id)
    nodes = {canonical_entity_id: {"id": canonical_entity_id,
                                    "name": center["display_name"], "kind": center["entity_kind"]}}
    edges = []

    rows = repository.select_ego_graph(conn, canonical_entity_id, source_entity_id)

    for r in rows["direct"]:
        other_id, other_name, other_kind = (
            (r["owned_id"], r["owned_name"], r["owned_kind"]) if r["owner_id"] == canonical_entity_id
            else (r["owner_id"], r["owner_name"], r["owner_kind"]))
        nodes.setdefault(other_id, {"id": other_id, "name": other_name, "kind": other_kind})
        edges.append({"from_id": r["owner_id"], "to_id": r["owned_id"], "kind": "OWNERSHIP",
                      "pct": r["parsed_percentage"],
                      "label": pct(r["parsed_percentage"]) + " %" if r["parsed_percentage"] is not None
                               else "ejerandel ikke læst"})

    for r in rows["control"]:
        other_id, other_name = (
            (r["registered_id"], r["registered_name"]) if r["actor_id"] == canonical_entity_id
            else (r["actor_id"], r["actor_name"]))
        other_kind = r["actor_kind"] if r["actor_id"] != canonical_entity_id else None
        nodes.setdefault(other_id, {"id": other_id, "name": other_name, "kind": other_kind or "UNKNOWN_ENTITY"})
        edges.append({"from_id": r["actor_id"], "to_id": r["registered_id"], "kind": "CONTROL",
                      "label": "registreret kontrolaktør"})

    for r in rows["roles"]:
        if r["person_id"] == canonical_entity_id:
            other_id, other_name, other_kind = r["company_id"], r["company_name"], "COMPANY"
        else:
            other_id, other_name, other_kind = r["person_id"], r["person_name"], "PERSON"
        nodes.setdefault(other_id, {"id": other_id, "name": other_name, "kind": other_kind})
        edges.append({"from_id": r["person_id"], "to_id": r["company_id"], "kind": "ROLE",
                      "label": r["canonical_role_label"]})

    center_node = nodes.pop(canonical_entity_id)
    return {"center": center_node, "nodes": list(nodes.values()), "edges": edges}


def ego_layout(graph, cap=NETWORK_NODE_CAP):
    """Trig belongs in Python, not Jinja. Places the center node in the
    middle of an 800x800 viewBox and its neighbors evenly around a circle by
    angle — no physics, no library. Above `cap` neighbors (this dataset's
    measured max degree is ~45, well under it) the display is capped with
    the true total stated, never a silent truncation — edges are filtered
    to the same shown-node set so the SVG and the plain-HTML fallback below
    it always describe the identical edge list."""
    all_nodes = graph["nodes"]
    total = len(all_nodes)
    truncated = total > cap
    shown = all_nodes[:cap] if truncated else all_nodes
    cx = cy = 400
    radius = 300
    n = len(shown)
    for i, node in enumerate(shown):
        angle = 2 * math.pi * i / n if n else 0.0
        node["x"] = cx + radius * math.cos(angle)
        node["y"] = cy + radius * math.sin(angle)
        node["anchor"] = "start" if math.cos(angle) >= 0 else "end"

    center = dict(graph["center"])
    center["x"], center["y"], center["anchor"] = cx, cy, "middle"

    positions = {node["id"]: node for node in shown}
    positions[center["id"]] = center
    edges = [e for e in graph["edges"]
             if e["from_id"] in positions and e["to_id"] in positions]

    return {"center": center, "nodes": shown, "edges": edges, "positions": positions,
            "total_nodes": total, "shown_count": len(shown), "truncated": truncated}


def network_layout_with_constellations(graph, constellations,
                                       cap=NETWORK_NODE_CAP):
    """Phase 5 (additive): place constellation nodes on an inner ring of
    the existing ego layout. Pure Python trig, no new engine; the base
    ego_layout result is left untouched so the flag-off render path is
    byte-identical. Constellation nodes (max group size 7 in the measured
    dataset) render on radius 170; their member/episode edges are only
    kept when both endpoints are in the shown node set — same honesty
    rule as ego_layout (SVG and fallback list describe identical edges).
    """
    base = ego_layout(graph, cap=cap)
    if not constellations or constellations.get("error") \
            or not constellations.get("nodes"):
        return base
    cx = cy = 400
    radius = 170
    shown = []
    positions = dict(base["positions"])
    edges = list(base["edges"])
    n = len(constellations["nodes"])
    for i, node in enumerate(constellations["nodes"]):
        angle = 2 * math.pi * (i + 0.5) / n
        c = dict(node)
        c["x"] = cx + radius * math.cos(angle)
        c["y"] = cy + radius * math.sin(angle)
        c["anchor"] = "middle"
        shown.append(c)
        positions[c["node_id"]] = c
    c_edges = [e for e in constellations["edges"]
               if e["from_id"] in positions and e["to_id"] in positions]
    return {"center": base["center"], "nodes": base["nodes"],
            "constellations": shown, "constellation_edges": c_edges,
            "positions": positions,
            "constellation_total": constellations.get("total"),
            "constellation_shown": constellations.get("shown"),
            "constellation_cap": constellations.get("cap"),
            "total_nodes": base["total_nodes"],
            "shown_count": base["shown_count"],
            "truncated": base["truncated"],
            "edges": edges}


# --- relationer (§16.4/§17.4/§18/§19) + PEP (from heimdal_portal.py:602-754, 1028) ---

_DIRECTIONAL_BASES = {"DIRECT_OWNERSHIP", "INDIRECT_OWNERSHIP", "DIRECT_CONTROL_ACTOR"}


def company_relations_of(conn, source_entity_id):
    """§16.4/§19: every relation_basis context this company appears in,
    grouped by counterparty, each with a visible per-category breakdown.
    company_a/b_entity_id and related_entity_id on the source table are raw
    source_entity_id values — resolved to canonical_entity_id/display_name
    here so nothing downstream ever sees a raw id."""
    rows = repository.select_company_relations_of(conn, source_entity_id)

    by_peer = {}
    for r in rows:
        this_is_a = r["company_a_entity_id"] == source_entity_id
        peer_id = r["b_id"] if this_is_a else r["a_id"]
        entry = by_peer.setdefault(peer_id, {
            "peer_id": peer_id,
            "peer_name": r["b_name"] if this_is_a else r["a_name"],
            "peer_kind": r["b_kind"] if this_is_a else r["a_kind"],
            "contexts": [], "counts": {},
        })
        directional = r["relation_basis"] in _DIRECTIONAL_BASES
        entry["contexts"].append({
            "relation_basis": r["relation_basis"],
            "related_entity_id": r["related_id"],
            "related_entity_name": r["related_name"],
            "related_entity_kind": "PERSON",  # the shared entity between two companies is always a person
            "this_relation": r["company_a_relation"] if this_is_a else r["company_b_relation"],
            "peer_relation": r["company_b_relation"] if this_is_a else r["company_a_relation"],
            "directional": directional,
            "subject_is_this": this_is_a if directional else None,
        })
        entry["counts"][r["relation_basis"]] = entry["counts"].get(r["relation_basis"], 0) + 1

    return sorted(by_peer.values(), key=lambda e: -sum(e["counts"].values()))


def _person_relation_basis(rel_type_a, group_a, rel_type_b, group_b):
    """Mirrors heimdal_collect.relation_basis's dispatch order exactly:
    same-category-of-relation-type wins before falling back to group match,
    so DIRECT_OWNERSHIP and REGISTERED_CONTROL_ACTOR (both EIGARAR) never
    collapse into one bucket."""
    if rel_type_a == rel_type_b == "DIRECT_OWNERSHIP":
        return "SHARED_OWNER"
    if rel_type_a == rel_type_b == "REGISTERED_CONTROL_ACTOR":
        return "SHARED_BENEFICIAL_OWNER"
    if group_a == group_b == "NEVND":
        return "SHARED_BOARD_PERSON"
    if group_a == group_b == "STJORN":
        return "SHARED_MANAGEMENT_PERSON"
    if group_a == group_b == "ANNAD":
        return "SHARED_OTHER_RELATION"
    return "CROSS_ROLE_PERSON"


def _collapse_pair_contexts(contexts):
    """Owner ruling 2026-08-15: two persons may hold at most ONE relation per
    company — except the ownership-share relation (ejerandel), which stands
    alongside it. Per company keep at most one non-ownership context (the
    most specific basis wins) plus at most one context per ownership basis.
    This subsumes the earlier cross-role-once-per-company rule."""
    best_other = {}   # company -> context
    best_own = {}     # (company, basis) -> context
    for ctx in contexts:
        if ctx["relation_basis"] in ("SHARED_OWNER", "SHARED_BENEFICIAL_OWNER"):
            best_own[(ctx["related_entity_id"], ctx["relation_basis"])] = ctx
            continue
        company = ctx["related_entity_id"]
        cur = best_other.get(company)
        if cur is None or _NON_OWNERSHIP_ORDER.index(ctx["relation_basis"]) \
                < _NON_OWNERSHIP_ORDER.index(cur["relation_basis"]):
            best_other[company] = ctx
    return list(best_own.values()) + list(best_other.values())


_NON_OWNERSHIP_ORDER = ("SHARED_BOARD_PERSON", "SHARED_MANAGEMENT_PERSON",
                        "SHARED_OTHER_RELATION", "CROSS_ROLE_PERSON")


def person_relations_of(conn, source_entity_id):
    """§17.4/§18: every shared-company context this person appears in with
    another person, grouped by counterparty, breakdown always visible.
    person_a/b_entity_id are raw source_entity_id — resolved here.
    Per (pair, company) at most one non-ownership relation plus the
    ownership-share relation — see _collapse_pair_contexts."""
    rows = repository.select_person_relations_of(conn, source_entity_id)

    by_peer = {}
    for r in rows:
        this_is_a = r["person_a_entity_id"] == source_entity_id
        peer_id = r["b_id"] if this_is_a else r["a_id"]
        basis = _person_relation_basis(r["type_a"], r["person_a_group"],
                                       r["type_b"], r["person_b_group"])
        entry = by_peer.setdefault(peer_id, {
            "peer_id": peer_id,
            "peer_name": r["b_name"] if this_is_a else r["a_name"],
            "peer_kind": "PERSON",
            "contexts": [], "counts": {},
        })
        entry["contexts"].append({
            "relation_basis": basis,
            "related_entity_id": r["company_id"],
            "related_entity_name": r["company_name"],
            "related_entity_kind": "COMPANY",  # the shared entity between two persons is always a company
            "this_relation": r["person_a_role_label"] if this_is_a else r["person_b_role_label"],
            "peer_relation": r["person_b_role_label"] if this_is_a else r["person_a_role_label"],
            "directional": False,
            "subject_is_this": None,
        })

    for entry in by_peer.values():
        entry["contexts"] = _collapse_pair_contexts(entry["contexts"])
        counts = {}
        for ctx in entry["contexts"]:
            b = ctx["relation_basis"]
            counts[b] = counts.get(b, 0) + 1
        entry["counts"] = counts
        # udadtil tæller relationen pr. virksomhed, ikke pr. rolle-kombination
        entry["companies"] = sorted({c["related_entity_id"] for c in entry["contexts"]})
    return sorted(by_peer.values(), key=lambda e: -len(e["companies"]))


def person_pep_info(conn, canonical_entity_id):
    """Retrieve PEP details for a person if applicable."""
    return repository.select_person_pep_info(conn, canonical_entity_id)
