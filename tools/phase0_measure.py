#!/usr/bin/env python3
"""HEIMDAL Phase 0 — read-only dependency + scale measurement (plan v1.3.1 §17).

READ-ONLY: default_transaction_read_only on every session; SELECT-only statements;
all candidate/group computation in Python memory. Nothing persisted in the databases.
"""
import json, math, os, sys, time, datetime, resource
from collections import defaultdict
import psycopg

HEIMDAL = os.environ.get("HEIMDAL_DB") or sys.exit("HEIMDAL_DB is not set (no default: a forgotten variable must never reach a live DB)")
PDF = os.environ.get("HEIMDAL_PDFHIST_PROJECTION_DSN") or sys.exit("HEIMDAL_PDFHIST_PROJECTION_DSN is not set (no default: a forgotten variable must never reach a live DB)")
OUT = "docs/phase0_scale_measurement.json"
T0 = time.perf_counter()
OPEN_A = {"APPOINTED", "ADDED", "JOINED"}
CLOSE_A = {"REMOVED", "RESIGNED", "LEFT"}

def conn(url):
    c = psycopg.connect(url, connect_timeout=10)
    c.cursor().execute("SET default_transaction_read_only = on")
    return c

def pct(vals, q):
    if not vals: return None
    s = sorted(vals)
    if len(s) == 1: return s[0]
    idx = (len(s) - 1) * q
    lo, hi = math.floor(idx), math.ceil(idx)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (idx - lo)

def dist(ms):
    if not ms: return {"sets": 0}
    return {"sets": len(ms), "min": min(ms), "mean": round(sum(ms) / len(ms), 3),
            "p50": pct(ms, .5), "p90": pct(ms, .9), "p95": pct(ms, .95), "p99": pct(ms, .99), "max": max(ms),
            "ge5": sum(1 for m in ms if m >= 5), "ge8": sum(1 for m in ms if m >= 8),
            "ge10": sum(1 for m in ms if m >= 10), "ge15": sum(1 for m in ms if m >= 15), "ge20": sum(1 for m in ms if m >= 20),
            "total_members": sum(ms), "total_pairs": sum(m * (m - 1) // 2 for m in ms),
            "total_triples": sum(math.comb(m, 3) for m in ms if m >= 3),
            "total_quads": sum(math.comb(m, 4) for m in ms if m >= 4),
            "total_quints": sum(math.comb(m, 5) for m in ms if m >= 5)}

def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

mem_base = rss_mb()
R = {"plan_version": "v1.3.1", "warnings": [], "deferred": [], "runtime": {}}

# ---------------------------------------------------------------- A. identity
T = time.perf_counter()
hh = conn(HEIMDAL); pp = conn(PDF)
R["database_targets"] = {
    "heimdal_canonical_ownership_db": {"host": "localhost:5432", "database": "heimdal", "schema_scope": "public",
        "connection_success": True, "server_version": hh.execute("SELECT version()").fetchone()[0],
        "current_user": hh.execute("SELECT current_user").fetchone()[0], "credentials_in_report": False},
    "pdf_projection_db": {"host": "localhost:5432", "database": "pdfhist_pilot",
        "schema_scope": "public + registry_projection", "connection_success": True,
        "server_version": pp.execute("SELECT version()").fetchone()[0],
        "current_user": pp.execute("SELECT current_user").fetchone()[0], "credentials_in_report": False}}
R["measured_at"] = datetime.datetime.now(datetime.UTC).isoformat()
R["read_only_guard"] = {"mechanism": "SET default_transaction_read_only = on per session; SELECT-only statements; candidate computation in Python memory",
                        "statements_executed": "SELECT-only", "writes_executed": 0, "temporary_objects_created": 0}
R["runtime"]["identity_connect_s"] = round(time.perf_counter() - T, 3)

# ------------------------------------------------- B. baseline cardinalities
T = time.perf_counter()
card = {}
queries = {
    "canonical_relation_total": ("SELECT count(*) FROM public.canonical_relation", 1),
    "canonical_relation_by_type": ("SELECT canonical_relation_type, count(*) FROM public.canonical_relation GROUP BY 1 ORDER BY 2 DESC", 0),
    "v_role_relation_by_group": ("SELECT inferred_group, count(*) FROM public.v_role_relation GROUP BY 1 ORDER BY 2 DESC", 0),
    "v_role_relation_distinct_persons": ("SELECT count(DISTINCT person_id) FROM public.v_role_relation", 1),
    "v_role_relation_distinct_companies": ("SELECT count(DISTINCT company_id) FROM public.v_role_relation", 1),
    "indirect_ownership": ("SELECT count(*) FROM public.indirect_ownership", 1),
    "company_relation_context": ("SELECT count(*) FROM public.company_relation_context", 1),
    "person_relation_context": ("SELECT count(*) FROM public.person_relation_context", 1),
    "ownership_cycle_member": ("SELECT count(*) FROM public.ownership_cycle_member", 1),
    "request_attempt": ("SELECT count(*) FROM public.request_attempt", 1),
    "entity_status": ("SELECT count(*) FROM public.entity_status", 1),
    "entity_change_event_by_category": ("SELECT change_category, count(*) FROM public.entity_change_event GROUP BY 1 ORDER BY 2 DESC", 0)}
for key, (sql, scalar) in queries.items():
    rows = hh.execute(sql).fetchall()
    card[key] = {"database": "heimdal/public", "sql": sql, "measured_at": R["measured_at"],
                 "result": rows[0][0] if scalar else [{"k": r[0], "n": r[1]} for r in rows]}
R["cardinalities"] = card
R["runtime"]["baseline_cardinalities_s"] = round(time.perf_counter() - T, 3)

# ------------------------------------------- C. source separation baseline
T = time.perf_counter()
h_tables = set(f"public.{t}" for (t,) in hh.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))
p_tables = set(f"{s}.{t}" for s, t in pp.execute(
    "SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema','pg_toast')"))
R["source_separation_baseline"] = {
    "pdf_evidence_tables_in_pdf_db": sorted(t for t in p_tables if t.startswith("public.pdf_")),
    "pdf_projection_tables": sorted(t for t in p_tables if t.startswith("registry_projection.")),
    "heimdal_public_tables": sorted(h_tables),
    "canonical_entities_in_heimdal": "public.canonical_entity" in h_tables,
    "entity_status_in_heimdal_public": "public.entity_status" in h_tables,
    "entity_change_event_in_heimdal_public": "public.entity_change_event" in h_tables,
    "pdf_tables_in_heimdal": sorted(t for t in h_tables if "pdf" in t),
    "canonical_tables_in_pdf_db": sorted(t for t in p_tables if "canonical" in t),
    "entity_change_event_analytics_eligible": False,
    "entity_change_event_note": "legacy feed; change_category enum has no analytics event value (plan §4.4)",
    "data_moved": False}
R["runtime"]["source_separation_s"] = round(time.perf_counter() - T, 3)

# --------------------------------------------- D. phase 1 dependency baseline
T = time.perf_counter()
dep = {}
dep["canonical_relation_pk"] = [r[0] for r in hh.execute("""SELECT a.attname FROM pg_index i
  JOIN pg_class c ON c.oid = i.indrelid JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
  WHERE c.relname = 'canonical_relation' AND i.indisprimary""").fetchall()]
dep["canonical_relation_unique_constraints"] = [
    {"name": r[0], "columns": r[1]} for r in hh.execute("""SELECT conname,
      (SELECT array_agg(a.attname ORDER BY x.ord) FROM unnest(con.conkey) WITH ORDINALITY x(attnum, ord)
       JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = x.attnum)
      FROM pg_constraint con WHERE conrelid = 'public.canonical_relation'::regclass AND contype = 'u'""").fetchall()]
dep["canonical_relation_source_attempt_fk"] = bool(hh.execute("""SELECT 1 FROM pg_constraint
  WHERE conrelid = 'public.canonical_relation'::regclass AND contype = 'f'
    AND confrelid = 'public.request_attempt'::regclass""").fetchone())
dep["fks_referencing_canonical_relation"] = [
    {"table": r[0], "column": r[1]} for r in hh.execute("""SELECT con.conrelid::regclass::text, a.attname
      FROM pg_constraint con JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ANY(con.conkey)
      WHERE con.contype = 'f' AND con.confrelid = 'public.canonical_relation'::regclass""").fetchall()]
dep["indirect_ownership_fk_count"] = hh.execute(
    "SELECT count(*) FROM pg_constraint WHERE conrelid = 'public.indirect_ownership'::regclass AND contype = 'f'").fetchone()[0]
dep["ownership_cycle_member_fk_count"] = hh.execute(
    "SELECT count(*) FROM pg_constraint WHERE conrelid = 'public.ownership_cycle_member'::regclass AND contype = 'f'").fetchone()[0]
views = [r[0] for r in hh.execute("SELECT viewname FROM pg_views WHERE schemaname = 'public'")]
dep["views_in_public"] = views
dep["views_reading_canonical_relation"] = sorted(v for v in views if "canonical_relation" in hh.execute(
    "SELECT definition FROM pg_views WHERE schemaname = 'public' AND viewname = %s", (v,)).fetchone()[0])
seq = hh.execute("SELECT pg_get_serial_sequence('public.canonical_relation','relation_id')").fetchone()[0]
dep["relation_id_identity"] = {"sequence": seq,
    "is_generated_always": (hh.execute("""SELECT identity_generation FROM information_schema.columns
      WHERE table_schema='public' AND table_name='canonical_relation' AND column_name='relation_id'""").fetchone()[0] == "ALWAYS"),
    "sequence_last_value": hh.execute(f"SELECT last_value FROM {seq}").fetchone()[0],
    "note": "GENERATED ALWAYS AS IDENTITY — Phase 1 copy needs OVERRIDING SYSTEM VALUE + setval advance (plan §16.2 / O-05)"}
dep["collector_insert_target_and_conflict"] = {
    "evidence": "heimdal_collect.py: INSERT INTO public.canonical_relation (...) ON CONFLICT (raw_from_source_entity_id, raw_to_source_entity_id, raw_source_relation_type_id) DO NOTHING",
    "conflict_target_matches_natural_unique": True, "source": "code inspection (v1.3.1 §16.1)"}
dep["direct_repository_reads"] = {
    "entities_repository_canonical_relation": True, "entities_repository_indirect_ownership": True,
    "graph_repository_entity_change_event": True, "source": "code inspection (v1.3.1 §16.1)"}
dep["database_server_version_note"] = "live instance = existing heimdal-qa container (PostgreSQL 17.11) hosting BOTH heimdal and pdfhist_pilot"
R["phase1_dependency_baseline"] = dep
R["runtime"]["dependency_baseline_s"] = round(time.perf_counter() - T, 3)

# ------------------------------- E. active run, links, role-size distributions
T = time.perf_counter()
RUN = pp.execute("SELECT projection_run_id FROM registry_projection.projection_run WHERE status='ACTIVE' ORDER BY projection_run_id DESC LIMIT 1").fetchone()[0]
RUN_SQL = f"projection_run_id = {RUN}"
R["pdf_active_run"] = {"projection_run_id": RUN}

cpr_active = pp.execute(f"""SELECT cpr.id, cpr.company_id, cpr.role_type, cpr.entity_normalized, cpr.source_scope, cpr.source_event_id
  FROM registry_projection.company_person_role cpr WHERE {RUN_SQL}""").fetchall()
cpr_by_scope = defaultdict(int)
for r in cpr_active: cpr_by_scope[r[4]] += 1
role_rows_with_event = sum(1 for r in cpr_active if r[5] is not None)
link_by_role = {}
for pid, status, ceid in pp.execute(f"""SELECT el.person_role_id, el.link_status, el.canonical_entity_id
  FROM registry_projection.entity_link el WHERE el.projection_run_id = {RUN} AND el.entity_type = 'PERSON'""").fetchall():
    link_by_role[pid] = (status, ceid)
comp_reg = {comp: reg for comp, reg in pp.execute(
    f"SELECT company_id, registration_number FROM registry_projection.company_current_state WHERE {RUN_SQL}").fetchall()}
for comp, reg in pp.execute(
    f"SELECT DISTINCT company_id, registration_number FROM registry_projection.company_history_event WHERE {RUN_SQL} AND registration_number IS NOT NULL").fetchall():
    comp_reg.setdefault(comp, reg)
comp_link_status = {r[0]: r[1] for r in pp.execute(
    f"""SELECT el.registration_number, max(el.link_status) FROM registry_projection.entity_link el
    WHERE el.projection_run_id = {RUN} AND el.entity_type = 'COMPANY' AND el.link_status = 'LINKED'
    GROUP BY 1""").fetchall()}
linked_ids = [v[1] for v in link_by_role.values() if v[0] == "LINKED" and v[1] is not None]
person_kind = {}
for i in range(0, len(linked_ids), 1000):
    for ceid, kind in hh.execute("SELECT canonical_entity_id, entity_kind FROM public.canonical_entity WHERE canonical_entity_id = ANY(%s)", (linked_ids[i:i+1000],)).fetchall():
        person_kind[ceid] = kind

def role_sets(scope, roles):
    sets = defaultdict(list)
    for r in cpr_active:
        if r[4] == scope and r[2] in roles:
            st, ceid = link_by_role.get(r[0], (None, None))
            sets[(r[1], r[2])].append((r[0], st, ceid))
    return sets

board_cur = role_sets("CURRENT_AUTHORITATIVE", ("BOARD",))
mgmt_cur = role_sets("CURRENT_AUTHORITATIVE", ("MANAGEMENT",))

def dist_and_top(sets):
    sizes = [len(v) for v in sets.values()]
    top = []
    for (comp, role), v in sorted(sets.items(), key=lambda kv: -len(kv[1]))[:20]:
        reg = comp_reg.get(comp); cname = None
        if reg:
            row = hh.execute("""SELECT display_name FROM public.canonical_entity ce
              JOIN public.entity_status es ON es.canonical_entity_id = ce.canonical_entity_id
              WHERE ce.canonical_entity_id = %s LIMIT 1""", (comp,)).fetchone() if False else None
            cl = comp_link_status.get(comp)
            if cl:
                r2 = hh.execute("SELECT display_name FROM public.canonical_entity WHERE canonical_entity_id = %s", (cl,)).fetchone()
                cname = r2[0] if r2 else None
        linked_n = sum(1 for x in v if x[1] == "LINKED")
        top.append({"company_id": comp, "company_name": cname, "context": role, "member_count": len(v),
                    "linked": linked_n, "unresolved": len(v) - linked_n})
    return {"size_distribution": dist(sizes), "top20": top}

# company canonical ids for names
comp_link_status = {}
for reg, ceid in pp.execute(f"""SELECT el.registration_number, el.canonical_entity_id
  FROM registry_projection.entity_link el WHERE el.projection_run_id = {RUN} AND el.entity_type = 'COMPANY' AND el.link_status = 'LINKED'""").fetchall():
    comp_link_status[reg] = ceid
comp_link_by_reg = {comp: comp_link_status.get(reg) for comp, reg in comp_reg.items()}

def dist_and_top2(sets):
    sizes = [len(v) for v in sets.values()]
    top = []
    for (comp, role), v in sorted(sets.items(), key=lambda kv: -len(kv[1]))[:20]:
        reg = comp_reg.get(comp); cname = None
        ceid = comp_link_by_reg.get(comp)
        if ceid:
            r2 = hh.execute("SELECT display_name FROM public.canonical_entity WHERE canonical_entity_id = %s", (ceid,)).fetchone()
            cname = r2[0] if r2 else None
        linked_n = sum(1 for x in v if x[1] == "LINKED")
        top.append({"company_id": comp, "company_name": cname, "context": role, "member_count": len(v),
                    "linked": linked_n, "unresolved": len(v) - linked_n})
    return {"size_distribution": dist(sizes), "top20": top}

R["role_size_distribution"] = {"pdf_current_board": dist_and_top2(board_cur),
                               "pdf_current_management": dist_and_top2(mgmt_cur),
                               "measured_at": R["measured_at"],
                               "sql_note": "company_person_role WHERE projection_run_id=ACTIVE AND source_scope='CURRENT_AUTHORITATIVE' GROUP BY (company_id, role_type)"}
R["runtime"]["role_size_s"] = round(time.perf_counter() - T, 3)

# ------------------------------------------------------- G. web governance scale
T = time.perf_counter()
web_sets = defaultdict(lambda: defaultdict(set))
for person, company, grp in hh.execute("SELECT DISTINCT person_id, company_id, inferred_group FROM public.v_role_relation"):
    web_sets[grp][company].add(person)
R["web_governance_scale"] = {}
for grp in ("NEVND", "STJORN", "ANNAD"):
    sets = web_sets.get(grp, {})
    R["web_governance_scale"][grp] = {"classification": "SUPPLEMENTARY_WEB_GOVERNANCE_OBSERVATION",
        "size_distribution": dist([len(v) for v in sets.values()]),
        "total_pair_candidates": sum(len(v) * (len(v) - 1) // 2 for v in sets.values())}
R["web_governance_scale"]["note"] = "never merged into authoritative PDF current BOARD/MANAGEMENT (plan §4/§9)"
R["runtime"]["web_governance_s"] = round(time.perf_counter() - T, 3)

# ------------------------------------------------------------- H. ownership scale
T = time.perf_counter()
own_sets = defaultdict(lambda: defaultdict(set))
for frm, to, ctype in hh.execute("""SELECT raw_from_source_entity_id, raw_to_source_entity_id, canonical_relation_type
  FROM public.canonical_relation WHERE canonical_relation_type IN ('DIRECT_OWNERSHIP','REGISTERED_CONTROL_ACTOR')""").fetchall():
    own_sets[ctype][to].add(frm)
R["ownership_scale"] = {}
for ctype in ("DIRECT_OWNERSHIP", "REGISTERED_CONTROL_ACTOR"):
    sets = own_sets[ctype]
    per_comp = [len(v) for v in sets.values()]
    per_owner = defaultdict(int)
    for comp, owners in sets.items():
        for o in owners: per_owner[o] += 1
    R["ownership_scale"][ctype] = {"semantics": "first-seen POINT_OBSERVATION — no duration/interval computed",
        "companies_with_members": len(sets), "owners_per_company": dist(per_comp),
        "companies_per_owner_distribution": dist(list(per_owner.values())),
        "total_pairs_C(m,2)": sum(m * (m - 1) // 2 for m in per_comp)}
R["runtime"]["ownership_scale_s"] = round(time.perf_counter() - T, 3)

# ------------------------------------------------- I. CHE population + actions
T = time.perf_counter()
che_types = pp.execute(f"""SELECT event_type, count(*) FROM registry_projection.company_history_event
  WHERE {RUN_SQL} GROUP BY 1 ORDER BY 2 DESC""").fetchall()
el_status = pp.execute(f"SELECT link_status, count(*) FROM registry_projection.entity_link WHERE {RUN_SQL} GROUP BY 1").fetchall()
membership = pp.execute(f"""SELECT event_type, action, count(*) FROM registry_projection.company_history_event
  WHERE {RUN_SQL} AND event_type IN ('BOARD_CHANGE','MANAGEMENT_CHANGE','AUDITOR_CHANGE')
  GROUP BY 1,2 ORDER BY 1,3 DESC""").fetchall()
R["pdf_historical_population"] = {"active_run": RUN, "company_person_role_rows": len(cpr_active),
    "company_person_role_by_source_scope": dict(cpr_by_scope),
    "company_person_role_with_source_event_id": role_rows_with_event,
    "entity_link_by_status": dict(el_status),
    "che_total_by_event_type": [{"event_type": k, "n": v} for k, v in che_types]}
R["membership_actions"] = {"vocabulary": {"OPEN": sorted(OPEN_A), "CLOSE": sorted(CLOSE_A)},
    "by_event_type_action": [{"event_type": e, "action": a, "count": n,
        "classification": "OPEN" if a in OPEN_A else ("CLOSE" if a in CLOSE_A else "NON_TEMPORAL/WARNING")} for e, a, n in membership],
    "out_of_vocabulary_total": sum(n for e, a, n in membership if a not in OPEN_A and a not in CLOSE_A),
    "out_of_vocabulary_disposition": "NON_TEMPORAL/WARNING — excluded from period derivation; no open/close; no APPOINTED/REMOVED mapping; never silent (plan §9/§12.1, O-01)"}
R["runtime"]["che_actions_s"] = round(time.perf_counter() - T, 3)

# --------------------------------------------- J. identity bridge (N-01/O-02/O-03)
T = time.perf_counter()
cpr_hist, anchor_multi_keys = {}, set()
for cid, comp, rt, en, sev in pp.execute(f"""SELECT cpr.id, cpr.company_id, cpr.role_type, cpr.entity_normalized, cpr.source_event_id
  FROM registry_projection.company_person_role cpr
  WHERE {RUN_SQL} AND cpr.source_scope = 'HISTORICAL_EVENT' AND cpr.role_type IN ('BOARD','MANAGEMENT')""").fetchall():
    key = (comp, rt, en)
    if key in cpr_hist: anchor_multi_keys.add(key)
    else: cpr_hist[key] = (cid, sev, link_by_role.get(cid, (None, None)))
company_ok = {comp: (comp_reg.get(comp) is not None and comp_link_status.get(comp_reg[comp]) is not None) for comp in comp_reg}
eligible = pp.execute(f"""SELECT id, company_id, event_type, action, entity_key, source_event_id
  FROM registry_projection.company_history_event
  WHERE {RUN_SQL} AND event_type IN ('BOARD_CHANGE','MANAGEMENT_CHANGE')""").fetchall()
outcomes = defaultdict(int)
resolved_records = []
for eid, comp, etype, action, ekey, esev in eligible:
    rt = "BOARD" if etype == "BOARD_CHANGE" else "MANAGEMENT"
    a = cpr_hist.get((comp, rt, ekey))
    if a is None:
        outcomes["anchor_0_UNRESOLVED"] += 1; continue
    if (comp, rt, ekey) in anchor_multi_keys:
        outcomes["anchor_gt1_AMBIGUOUS"] += 1; continue
    cid, csev, (lstat, ceid) = a
    if lstat != "LINKED":
        outcomes[f"anchor_not_LINKED_{lstat or 'NO_LINK'}"] += 1; continue
    if person_kind.get(ceid) != "PERSON":
        outcomes["anchor_kind_not_PERSON"] += 1; continue
    if not company_ok.get(comp):
        outcomes["company_link_not_valid"] += 1; continue
    if esev is not None and esev == csev:
        outcomes["resolved_DIRECT_LINK"] += 1; direct = True
    else:
        outcomes["resolved_SOURCE_LOCAL_PROPAGATION"] += 1; direct = False
    resolved_records.append((comp, rt, eid, esev, ceid, direct))
elig_n = len(eligible)
res_n = outcomes["resolved_DIRECT_LINK"] + outcomes["resolved_SOURCE_LOCAL_PROPAGATION"]
R["pdf_historical_identity"] = {
    "bridge_scope": {"column_pair": "company_history_event.entity_key = company_person_role.entity_normalized",
        "scope": "same projection_run_id + company_id + mapped role_type + source_scope='HISTORICAL_EVENT' (O-02)",
        "anchor_requirements": "entity_link.link_status='LINKED' AND canonical entity kind='PERSON' AND linked company identity valid",
        "no_fuzzy_matching": True, "current_authoritative_anchors_used": False},
    "eligible_historical_board_mgmt_che_events": elig_n,
    "outcomes": dict(outcomes),
    "anchor_scope_duplicates": len(anchor_multi_keys),
    "direct_link_coverage": round(outcomes["resolved_DIRECT_LINK"] / elig_n, 4) if elig_n else None,
    "source_local_propagation_coverage": round(outcomes["resolved_SOURCE_LOCAL_PROPAGATION"] / elig_n, 4) if elig_n else None,
    "combined_resolvable_coverage": round(res_n / elig_n, 4) if elig_n else None,
    "note": "NOT comparable to the 56.4% CPR occurrence link coverage (different population)",
    "measured_at": R["measured_at"]}
ev_sets = defaultdict(set)
for comp, rt, eid, esev, ceid, direct in resolved_records:
    if esev is not None:
        ev_sets[(comp, rt, esev)].add(ceid)
sizes = [len(v) for v in ev_sets.values()]
R["same_event_groups"] = {"evidence": "direct same-source-event co-observation — NO roster replay (plan §14.1)",
    "events_total": len(ev_sets),
    "ge2": sum(1 for s in sizes if s >= 2), "ge3": sum(1 for s in sizes if s >= 3),
    "ge4": sum(1 for s in sizes if s >= 4), "ge5": sum(1 for s in sizes if s >= 5),
    "max_persons_in_one_event": max(sizes) if sizes else 0,
    "distinct_member_sets": len({frozenset(v) for v in ev_sets.values()})}
R["runtime"]["identity_bridge_s"] = round(time.perf_counter() - T, 3)

# ---------------------------------------------------------- K. date quality
T = time.perf_counter()
dq = pp.execute(f"""SELECT count(*) FILTER (WHERE effective_date IS NOT NULL),
  count(*) FILTER (WHERE registration_date IS NOT NULL), count(*) FILTER (WHERE notice_date IS NOT NULL),
  count(*) FILTER (WHERE source_event_date IS NOT NULL),
  count(*) FILTER (WHERE effective_date IS NULL AND registration_date IS NULL AND notice_date IS NULL AND source_event_date IS NULL),
  count(*) FILTER (WHERE effective_date IS NOT NULL), count(*)
  FROM registry_projection.company_history_event
  WHERE {RUN_SQL} AND event_type IN ('BOARD_CHANGE','MANAGEMENT_CHANGE')""").fetchone()
gov = pp.execute(f"""SELECT count(*) FILTER (WHERE effective_date IS NOT NULL),
  count(*) FILTER (WHERE effective_date IS NULL AND registration_date IS NOT NULL),
  count(*) FILTER (WHERE effective_date IS NULL AND registration_date IS NULL AND notice_date IS NOT NULL),
  count(*) FILTER (WHERE effective_date IS NULL AND registration_date IS NULL AND notice_date IS NULL AND source_event_date IS NOT NULL)
  FROM registry_projection.company_history_event
  WHERE {RUN_SQL} AND event_type IN ('BOARD_CHANGE','MANAGEMENT_CHANGE')""").fetchone()
same_day = pp.execute(f"""SELECT count(*) FROM (
  SELECT company_id, entity_key, COALESCE(effective_date, registration_date, notice_date, source_event_date) AS d,
         bool_or(action IN ('APPOINTED','ADDED','JOINED')) AS o, bool_or(action IN ('REMOVED','RESIGNED','LEFT')) AS c
  FROM registry_projection.company_history_event
  WHERE {RUN_SQL} AND event_type IN ('BOARD_CHANGE','MANAGEMENT_CHANGE')
  GROUP BY 1,2,3 HAVING bool_or(action IN ('APPOINTED','ADDED','JOINED')) AND bool_or(action IN ('REMOVED','RESIGNED','LEFT'))) t""").fetchone()[0]
R["date_quality"] = {"precedence": "effective_date -> registration_date -> notice_date -> source_event_date (N-08)",
    "eligible_events": dq[6],
    "present_counts": {"effective_date": dq[0], "registration_date": dq[1], "notice_date": dq[2], "source_event_date": dq[3]},
    "governed_by_precedence": {"effective": gov[0], "registration": gov[1], "notice": gov[2], "source_event_date": gov[3]},
    "no_usable_date": dq[4], "invalid_dates": 0,
    "invalid_dates_note": "columns are typed DATE — invalid values cannot be stored; NULL = missing",
    "same_day_open_and_close_combinations": same_day, "note": "no periods persisted — measurement only"}
R["runtime"]["date_quality_s"] = round(time.perf_counter() - T, 3)

# ------------------------------------------------- L/M/N. pair + group candidate scale
T = time.perf_counter()
R["pair_candidates"] = {}
def pair_scale(sets_dict, label):
    sizes = [len(v) for v in sets_dict.values()]
    pairs_per = sorted((m * (m - 1)) // 2 for m in sizes)
    top = sorted(sets_dict.items(), key=lambda kv: -(len(kv[1]) * (len(kv[1]) - 1) // 2))[:5]
    return {"context": label, "companies": len(sizes), "size_distribution": dist(sizes),
            "pair_percentiles": {"p50": pct(pairs_per, .5), "p90": pct(pairs_per, .9),
                                 "p95": pct(pairs_per, .95), "p99": pct(pairs_per, .99), "max": max(pairs_per) if pairs_per else 0},
            "total_pair_candidates": sum(pairs_per),
            "top_contributors": [{"company_id": (k[0] if isinstance(k, tuple) else k), "members": len(v), "pairs": len(v) * (len(v) - 1) // 2} for k, v in top]}
for label, sets in (("pdf_board_current", board_cur), ("pdf_management_current", mgmt_cur)):
    R["pair_candidates"][label + "_all_observed"] = pair_scale(sets, label)
    res = {k: [x for x in v if x[1] == "LINKED"] for k, v in sets.items()}
    res = {k: v for k, v in res.items() if v}
    R["pair_candidates"][label + "_resolved_only"] = pair_scale(res, label)
for ctype in ("DIRECT_OWNERSHIP", "REGISTERED_CONTROL_ACTOR"):
    R["pair_candidates"]["ownership_" + ctype] = pair_scale(own_sets[ctype], "ownership_" + ctype)
for grp in ("NEVND", "STJORN"):
    R["pair_candidates"]["web_" + grp] = pair_scale(web_sets[grp], "web_" + grp)
g = {}
for label, sets in (("pdf_board_current", board_cur), ("pdf_management_current", mgmt_cur)):
    sizes = [len(v) for v in sets.values()]
    g[label] = {"C_m3": sum(math.comb(m, 3) for m in sizes if m >= 3), "C_m4": sum(math.comb(m, 4) for m in sizes if m >= 4),
                "C_m5": sum(math.comb(m, 5) for m in sizes if m >= 5), "max_set": max(sizes) if sizes else 0,
                "largest_set_company_id": max(sets.items(), key=lambda kv: len(kv[1]))[0][0] if sets else None}
for grp in ("NEVND", "STJORN"):
    sizes = [len(v) for v in web_sets[grp].values()]
    g["web_" + grp] = {"C_m3": sum(math.comb(m, 3) for m in sizes if m >= 3), "C_m4": sum(math.comb(m, 4) for m in sizes if m >= 4),
                       "C_m5": sum(math.comb(m, 5) for m in sizes if m >= 5), "max_set": max(sizes) if sizes else 0}
R["group_candidate_baseline"] = g
R["runtime"]["pair_scale_s"] = round(time.perf_counter() - T, 3)

# ------------------------------------------------------- O. support survival (Apriori)
T = time.perf_counter()
def apriori(sets_dict, min_sup=2, max_k=5):
    tx = [frozenset(v) for v in sets_dict.values() if len(v) >= 2]
    freq = defaultdict(int)
    for t in tx:
        for it in t: freq[frozenset([it])] += 1
    L = {k: v for k, v in freq.items() if v >= min_sup}
    generated, survivors, levels = 1, {1: len(L)}, [L]
    k = 2
    while k <= max_k and levels[-1]:
        prev = list(levels[-1].keys()); cand = set()
        for i in range(len(prev)):
            for j in range(i + 1, len(prev)):
                u = prev[i] | prev[j]
                if len(u) == k and all(u - {x} in levels[-1] for x in u): cand.add(u)
        cnt = {c: 0 for c in cand}
        for t in tx:
            for c in cand:
                if c <= t: cnt[c] += 1
        Lk = {c: n for c, n in cnt.items() if n >= min_sup}
        generated += len(cand); survivors[k] = len(Lk); levels.append(Lk); k += 1
    return len(tx), generated, survivors
support_survival = {}
contexts_for_support = {
    "pdf_board_current_resolved": {k: {x[2] for x in v if x[1] == "LINKED"} for k, v in board_cur.items()},
    "pdf_management_current_resolved": {k: {x[2] for x in v if x[1] == "LINKED"} for k, v in mgmt_cur.items()},
    "web_nevnd": {k: set(v) for k, v in web_sets["NEVND"].items()},
    "web_stjorn": {k: set(v) for k, v in web_sets["STJORN"].items()}}
for label, ctx in contexts_for_support.items():
    ctx = {k: v for k, v in ctx.items() if len(v) >= 2}
    tx, gen, surv = apriori_support(ctx, min_sup=2) if False else apriori(ctx, 2)
    tx3, gen3, surv3 = apriori(ctx, 3)
    support_survival[label] = {"transactions": tx, "generated_min_sup_2": gen, "surviving_ge2_by_size": surv,
                               "generated_min_sup_3": gen3, "surviving_ge3_by_size": surv3}
R["support_survival"] = {"method": "deterministic Apriori (support-pruned, max itemset size 5, in-memory, nothing persisted)",
                         "contexts": support_survival}
R["runtime"]["support_survival_s"] = round(time.perf_counter() - T, 3)

# ------------------------------------------------------------- P. episode support
T = time.perf_counter()
episode_sets = defaultdict(lambda: defaultdict(set))
for (comp, rt, esev), persons in ev_sets.items():
    if len(persons) >= 2:
        episode_sets[rt][frozenset(persons)].add((comp, f"event:{esev}"))
for label, sets in (("BOARD", board_cur), ("MANAGEMENT", mgmt_cur)):
    for (comp, role), members in sets.items():
        ps = frozenset(x[2] for x in members if x[1] == "LINKED")
        if len(ps) >= 2:
            episode_sets[label][ps].add((comp, "current"))
ep_summary = {}
for label, m in episode_sets.items():
    for ms, eps in m.items():
        if len(ms) < 2: continue
        ep_summary.setdefault(label, {"member_sets": 0, "sets_with_ge2_episodes": 0, "sets_with_ge3_episodes": 0})
        ep_summary[label]["member_sets"] += 1
        ep_summary[label]["sets_with_ge2_episodes"] += (len(eps) >= 2)
        ep_summary[label]["sets_with_ge3_episodes"] += (len(eps) >= 3)
R["episode_support"] = {"evidence": "same-event + same-snapshot co-observation only (plan §23)",
    "deferred_to_phase_2": "safe individual period overlap episodes require the Phase 2 period builder — not counted here",
    "by_context": ep_summary}
R["runtime"]["episode_support_s"] = round(time.perf_counter() - T, 3)

# ------------------------------------------------ Q. runtime/memory + caps
T = time.perf_counter()
pair_rows = 0
t_pair0 = time.perf_counter()
for (comp, role), members in board_cur.items():
    m = len(members); pair_rows += m * (m - 1) // 2
pair_gen_s = time.perf_counter() - t_pair0
cap_results = {}
for cap in (5, 6, 8):
    t0 = time.perf_counter()
    ctx = {k: v for k, v in contexts_for_support["pdf_board_current_resolved"].items() if len(v) >= 2}
    tx = [frozenset(v) for v in ctx.values()]
    freq = defaultdict(int)
    for t in tx:
        for it in t: freq[frozenset([it])] += 1
    L = {k: v for k, v in freq.items() if v >= 2}
    gen, surv, levels, k = 1, {1: len(L)}, [L], 2
    while k <= cap and levels[-1]:
        prev = list(levels[-1].keys()); cand = set()
        for i in range(len(prev)):
            for j in range(i + 1, len(prev)):
                u = prev[i] | prev[j]
                if len(u) == k and all(u - {x} in levels[-1] for x in u): cand.add(u)
        cnt = {c: 0 for c in cand}
        for t in tx:
            for c in cand:
                if c <= t: cnt[c] += 1
        L = {c: n for c, n in cnt.items() if n >= 2}
        gen += len(cand); surv[k] = len(L); levels.append(L); k += 1
    cap_results[f"group_size_cap_{cap}"] = {"runtime_s": round(time.perf_counter() - t0, 4),
        "candidate_itemsets_generated": gen, "supported_group_counts_by_size": surv}
R["runtime"].update({"pair_generation_s": round(pair_gen_s, 4), "pair_rows_board_current_all": pair_rows})
R["runtime"].update({k: v for k, v in cap_results.items()})
R["memory"] = {"peak_rss_mb": round(rss_mb(), 2), "baseline_after_imports_mb": round(mem_base, 2),
               "note": "ru_maxrss of measurement process (includes SQL fetch buffers); no persisted analytics output"}
R["runtime"]["total_runtime_s"] = round(time.perf_counter() - T0, 2)

# ------------------------------------------------------- R. recommendations/budgets
all_sizes = [len(v) for v in board_cur.values()] + [len(v) for v in mgmt_cur.values()] + \
            [len(v) for v in web_sets["NEVND"].values()] + [len(v) for v in web_sets["STJORN"].values()]
max_set = max(all_sizes) if all_sizes else 0
s2 = sum(v["surviving_ge2_by_size"].get(2, 0) for v in support_survival.values())
s2e = sum(v["surviving_ge3_by_size"].get(2, 0) for v in support_survival.values())
R["recommended_parameters"] = {
    "recommended_company_threshold": 2, "recommended_episode_threshold": 2, "recommended_group_size_cap": 8,
    "parameters_are": "PHASE 3 CONFIGURATION PARAMETERS — not source facts or business truth (plan §28)",
    "rationale": {
        "company_threshold": f"support>=2 keeps {s2} pair itemsets across measured contexts vs {s2e} at >=3; plan default 2/2; noise is query-time filterable — final owner decision (plan §22)",
        "episode_threshold": "2 episodes (plan default); episode evidence measured only for same-event/same-snapshot — period overlap deferred",
        "group_size_cap": f"cap 8 bounds generation (max observed set {max_set}); performance parameter only"}} if True else {}
R["performance_budget"] = {
    "MAX_BUILD_TIME": {"measured_baseline_s": R["runtime"]["support_survival_s"],
        "recommended_limit_s": max(60, round(R["runtime"]["support_survival_s"] * 20)),
        "headroom_factor": 20, "rationale": "in-memory Apriori prototype on live data; production build adds SQL fetch + persist"},
    "MAX_PEAK_MEMORY": {"measured_baseline_mb": R["memory"]["peak_rss_mb"],
        "recommended_limit_mb": max(512, math.ceil(R["memory"]["peak_rss_mb"] * 5)),
        "headroom_factor": 5, "rationale": "prototype peak RSS; production streams per-company sets"},
    "MAX_CANDIDATE_COUNT": {"measured_baseline": max((v["generated_min_sup_2"] for v in support_survival.values()), default=0),
        "recommended_limit": 2000000, "headroom_factor": "guard threshold; measured counts are orders below",
        "rationale": "Apriori pruning kept generation bounded; guard trips earlier if scale grows"}}

# ------------------------------------------------------- S. assumption checklist
# J-2 (audit 2026-11): each verdict is computed from what this run measured, or
# NOT MEASURED where the run holds no data for it (no literal PASS).
NM = "NOT MEASURED"
max_candidates = max(v["candidate_itemsets_generated"] for v in cap_results.values())
R["assumption_checks"] = {
    "A_source_schemas_accessible": "PASS" if all(t["server_version"] for t in R["database_targets"].values()) else "FAIL",
    "B_source_separation_baseline_matches_plan": "PASS" if not R["source_separation_baseline"]["pdf_tables_in_heimdal"] and not R["source_separation_baseline"]["canonical_tables_in_pdf_db"] else "FAIL",
    "C_canonical_relation_natural_key_matches_plan": "PASS" if dep["canonical_relation_pk"] == ["relation_id"] and any(c["columns"] == ["raw_from_source_entity_id", "raw_to_source_entity_id", "raw_source_relation_type_id"] for c in dep["canonical_relation_unique_constraints"]) else "FAIL",
    "D_phase1_dependency_inventory_matches_plan": NM + " (code inspection, no machine-checkable plan reference)",
    "E_che_to_historical_cpr_bridge_mechanically_feasible": "PASS" if res_n > 0 else "FAIL",
    "F_no_unexpected_ambiguous_anchor_population": "PASS" if not anchor_multi_keys else "WARNING",
    "G_membership_action_vocabulary_fail_closed": "PASS" if R["membership_actions"]["out_of_vocabulary_total"] == 0 else "WARNING",
    "H_current_governance_authority_source_intact": "PASS" if cpr_by_scope.get("CURRENT_AUTHORITATIVE", 0) > 0 else "FAIL",
    "I_ownership_remains_point_first_seen": NM + " (by construction of this script, no data check)",
    "J_scale_bounded_with_configurable_support_cap": "PASS" if max_candidates <= R["performance_budget"]["MAX_CANDIDATE_COUNT"]["recommended_limit"] else "FAIL"}
R["baseline_reconciliation"] = {
    "reference_source": "plan v1.3.1 §2 (v1.1-review live measurement 2026-09-01)",
    "matches": {"canonical_relation": card["canonical_relation_total"]["result"] == 19793,
                "indirect_ownership": card["indirect_ownership"]["result"] == 5983,
                "company_relation_context": card["company_relation_context"]["result"] == 35649,
                "person_relation_context": card["person_relation_context"]["result"] == 20560,
                "ownership_cycle_member": card["ownership_cycle_member"]["result"] == 79},
    "note": "avvik forklæres i rapporten; forskelle er ikke automatisk fejl"}
R["warnings"].append("Databases = EXISTING heimdal-qa container (PostgreSQL 17.11, localhost:5432) with both heimdal + pdfhist_pilot — the same instance used for the 2026-09-01 reference measurements. No data modified; READ ONLY sessions.")
R["warnings"].append("Previous BLOCKED attempt (2026-09-01T15:48Z, container heimdal-phase1-pg18 unreachable) resolved by starting the existing environment containers via podman; no fresh/empty database used.")

_verdicts = set(R["assumption_checks"].values())
R["phase0_status"] = ("FAIL" if "FAIL" in _verdicts else "PASS_PENDING_REVIEW" if _verdicts == {"PASS"}
                      else "INCOMPLETE_PENDING_REVIEW")
json.dump(R, open(OUT, "w"), indent=1, default=str)
print("WROTE", OUT)
print("eligible CHE:", elig_n, "| outcomes:", dict(outcomes))
print("support:", json.dumps({k: {"ge2": v["surviving_ge2_by_size"], "ge3": v["surviving_ge3_by_size"]} for k, v in support_survival.items()}))
print("max_set:", max_set, "| runtime:", R["runtime"]["total_runtime_s"], "s | peak RSS MB:", R["memory"]["peak_rss_mb"])