#!/usr/bin/env python3
"""Audit lane C check (2026-11): ownership-derived data is correct and fresh.

Read-only (session default_transaction_read_only=on). Asserts:
  C-1  tools/phase2_build.py does not use registry_ownership.relation's
       canonical_subject/object_entity_id (raw SOURCE entity ids) directly as
       canonical_entity_id. It did, so 0 of the 817 OWNERSHIP_GRAPH observations
       of analytics build 8 named the right person/company. Also, every
       OWNERSHIP_GRAPH observation of the ACTIVE analytics build must match
       v_direct_ownership/v_control_registration for its source_relation_id
       (wrong rows in older builds are reported, not failed).
  C-2  registry_ownership.indirect_ownership and ownership_cycle_member are
       exactly what the current heimdal_collect code computes from
       registry_ownership.relation (same rows, %, path counts, path order,
       cycle ids). Every backup holds pre-2026-08-14 derived data that fails
       this until `compute-ownership` is re-run.
  C-3  every owner-company chain through an edge inside a cycle group (its
       share withheld by §15) is named by the read-time marker query
       (entities.repository.select_cycle_passing), checked against a recompute.
  C-8  no self-edge company is its own subsidiary in its group tree.
  C-5  PEP reaches the person page and screening only through the identity link:
       none of the 6 persons the name-matched seed flagged is PEP, screening code
       has no ILIKE / LIMIT 1 pick, and every shared person name screens as
       ambiguous (no homonym is chosen). Prints counts only, never names.
Run: python3 tools/audit/check_c.py [DSN]   (default: $HEIMDAL_READ_DB)
Exit 1 while a finding is present.
"""
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HEIMDAL_DB", "postgresql://unused@127.0.0.1:1/none")  # import-time default only
import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from heimdal_collect import compute_indirect_ownership, find_cycle_components  # noqa: E402

fails = []

# C-1 static: the id-space confusion in the ownership-observation section.
src = (ROOT / "tools/phase2_build.py").read_text()
if re.search(r'subj,\s*obj\s*=\s*r\["canonical_subject_entity_id"\],\s*r\["canonical_object_entity_id"\]', src):
    fails.append("C-1 phase2_build.py treats relation source entity ids as canonical_entity_id")

dsn = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("HEIMDAL_READ_DB", "")
assert dsn, "pass a DSN or set HEIMDAL_READ_DB"
c = psycopg.connect(dsn, options="-c default_transaction_read_only=on")

# C-1 data: stored OWNERSHIP_GRAPH observations must name the relation's own endpoints.
# Only the ACTIVE build fails the check: every reader and /api/analytics/graph read
# the ACTIVE build only (no SUPERSEDED fallback). Wrong rows in older builds are
# printed, so nobody re-activates one of them.
bad = dict(c.execute(
    "SELECT b.status = 'ACTIVE', count(*) FROM registry_analytics.person_company_observation o"
    " JOIN registry_analytics.analytics_build b ON b.build_run_id = o.build_run_id"
    " LEFT JOIN v_direct_ownership d ON d.relation_id = o.source_relation_id"
    " LEFT JOIN v_control_registration k ON k.relation_id = o.source_relation_id"
    " WHERE o.source_family = 'OWNERSHIP_GRAPH' AND NOT ("
    "  (o.canonical_person_id, o.canonical_company_id) IS NOT DISTINCT FROM (d.owner_id, d.owned_id)"
    "  OR (o.canonical_person_id, o.canonical_company_id) IS NOT DISTINCT FROM (k.actor_id, k.registered_id))"
    " GROUP BY 1").fetchall())
if bad.get(True):
    fails.append(f"C-1 {bad[True]} OWNERSHIP_GRAPH observations of the ACTIVE build name the wrong person/company")
if bad.get(False):
    print(f"info C-1: {bad[False]} wrong OWNERSHIP_GRAPH rows in non-ACTIVE (pre-fix) builds; never re-activate them")

# C-2: recompute exactly as cmd_compute_ownership does and compare.
edges = [(o, w, float(p)) for o, w, p in c.execute(
    "SELECT canonical_subject_entity_id, canonical_object_entity_id, parsed_percentage"
    " FROM registry_ownership.relation WHERE canonical_relation_type='DIRECT_OWNERSHIP'"
    " ORDER BY canonical_subject_entity_id, canonical_object_entity_id")]
comps = find_cycle_components((o, w) for o, w, _ in edges)
want_cyc = {(i, n) for i, comp in enumerate(comps) for n in comp}
have_cyc = set(c.execute("SELECT cycle_group_id, entity_id FROM registry_ownership.ownership_cycle_member"))
if want_cyc != have_cyc:
    fails.append(f"C-2 ownership_cycle_member differs from recompute ({len(want_cyc ^ have_cyc)} rows)")
want = {(r["owner_entity_id"], r["owned_entity_id"]): r
        for r in compute_indirect_ownership(edges, {n: i for i, n in want_cyc})}
have = {(o, w): (p, n, paths, t) for o, w, p, n, paths, t in c.execute(
    "SELECT owner_entity_id, owned_entity_id, computed_percentage, path_count,"
    " contributing_paths, paths_truncated FROM registry_ownership.indirect_ownership")}
diff = set(want) ^ set(have)
for k in set(want) & set(have):
    r, (p, n, paths, t) = want[k], have[k]
    # tolerance = one numeric(9,4) storage unit (PG rounds float8 via 15 digits)
    if (abs(Decimal(repr(r["computed_percentage"])) - p) > Decimal("0.0001") or n != r["path_count"]
            or t != r["paths_truncated"] or paths != r["contributing_paths"]):
        diff.add(k)
if diff:
    fails.append(f"C-2 indirect_ownership differs from recompute ({len(diff)} rows)")

# C-3 (owner D8 2026-11-06): every owner-company chain that runs over an edge
# inside one cycle group (so compute_indirect_ownership withheld its share)
# must be named by the read-time marker query, from both ends.
dc = psycopg.connect(dsn, options="-c default_transaction_read_only=on", row_factory=dict_row)
cyc = {n: i for i, n in have_cyc}
out = {}
for o, w, _ in edges:
    if o != w:
        out.setdefault(o, set()).add(w)
same = lambda a, b: a in cyc and cyc.get(a) == cyc.get(b)  # noqa: E731
want3 = {}
for a in out:  # simple paths of >= 2 hops
    stack = [(a, (a,), frozenset())]
    while stack:
        n, path, groups = stack.pop()
        for m in out.get(n, ()):
            if m not in path:
                g = groups | ({cyc[n]} if same(n, m) else set())
                if g and len(path) >= 2 and not same(a, m):
                    want3.setdefault((a, m), set()).update(g)
                stack.append((m, path + (m,), g))
try:
    from heimdal.modules.entities.repository import select_cycle_passing
except ImportError:
    fails.append(f"C-3 {len(want3)} chains through a cycle have no marker (no select_cycle_passing)")
else:
    ids = {n for o, w, _ in edges for n in (o, w)}
    for incoming in (True, False):
        have3 = {}
        for x in ids:
            for m in select_cycle_passing(dc, x, incoming):
                k = (m["counterparty_source_id"], x) if incoming else (x, m["counterparty_source_id"])
                have3[k] = set(m["cycle_group_ids"])
        if have3 != want3:
            fails.append(f"C-3 cycle markers ({'incoming' if incoming else 'outgoing'}) differ from"
                         f" recompute ({len(set(have3.items()) ^ set(want3.items()))} pairs)")

# C-8 (owner D8 2026-11-06): a self-edge is a §15 cycle of one; the group tree
# must never list a self-owning company as its own subsidiary.
from heimdal.modules.groups.service import corporate_group_tree  # noqa: E402
selfs = [r["owner_id"] for r in dc.execute(
    "SELECT owner_id FROM v_direct_ownership WHERE owner_id = owned_id ORDER BY 1")]
own_sub = []
for e in selfs:
    try:
        if any(n["entity"]["owned_id"] == e for n in corporate_group_tree(dc, e)["tree"]):
            own_sub.append(e)
    except RecursionError:
        own_sub.append(e)
if own_sub:
    fails.append(f"C-8 {len(own_sub)} of {len(selfs)} self-edge companies are their own subsidiary")

# C-5 (owner D14): no PEP by name, no namesake picked by screening.
from heimdal.modules.entities.service import person_pep_info  # noqa: E402
from heimdal.modules.screening import repository as screening_repo  # noqa: E402
from heimdal.modules.screening.service import batch_screen_entities  # noqa: E402
SEED_FLAGGED = (1, 4, 21, 5978, 6006, 9095)  # canonical ids on the 2026-09-04 recovered DB
pep_titles = screening_repo.first_pep_titles(dc, list(SEED_FLAGGED)) or {}
still = [i for i in SEED_FLAGGED if person_pep_info(dc, i) or i in pep_titles]
if still:
    fails.append(f"C-5 {len(still)} of {len(SEED_FLAGGED)} name-matched seed persons are still PEP")
scr = "\n".join(f.read_text() for f in sorted((ROOT / "heimdal/modules/screening").glob("*.py")))
if re.search(r"ILIKE|LIMIT 1\b", scr):
    fails.append("C-5 screening code still has an ILIKE / LIMIT 1 entity pick")
homonyms = [r["display_name"] for r in dc.execute(
    "SELECT display_name FROM v_entity WHERE entity_kind = 'PERSON'"
    " GROUP BY display_name HAVING count(*) > 1 ORDER BY 1")]
picked = sum(r["id"] is not None for r in batch_screen_entities(dc, homonyms))
print(f"C-5 seed persons still PEP: {len(still)} | shared person names: {len(homonyms)},"
      f" screened to one person: {picked}")
if picked:
    fails.append(f"C-5 screening picks one person for {picked} shared names")

for f in fails:
    print("FAIL", f)
print("OK" if not fails else f"{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
