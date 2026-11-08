#!/usr/bin/env python3
"""HEIMDAL Phase 3 — pair co-participation + supported 2..N recurring groups.

Binding contract: plan v1.3.1 §5.4-5.7, §11 (truth table), §13 (pair predicates),
§14 (episodes/support/core), §15 (provenance + completeness gate).

Reuses the Phase 2 builder (phase2_build.collect) without mutating its semantics.
A Phase 3 build is COMPLETE before ACTIVE:
observations -> periods -> pairs -> groups -> gates -> full content hash -> ACTIVE.

Pairs/groups derive ONLY from registry_analytics person_company_observation /
person_company_period of the SAME build_run_id (in-memory copies produced by the
same collect()). No CHE/CPR/legacy/entity_change_event access in this module.
"""
import hashlib
import itertools
import json
import os
import re
import sys
import time
import resource
from collections import Counter, defaultdict
from datetime import date
os.environ["PGTZ"] = "UTC"  # B-8: hashes serialise timestamptz::text, so pin the session TZ

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from phase2_build import (  # noqa: E402
    PHASE0_FILE, TEMPORAL, build_phase0_pin, collect, content_hash, env_dsn)

PAIR_CLASSES = ("SAME_COMPANY_EVER", "SAME_OBSERVATION", "SAME_SNAPSHOT",
                "KNOWN_TEMPORAL_OVERLAP", "UNKNOWN_TEMPORAL_RELATION")
GROUP_OBS_KINDS = ("SAME_EVENT", "SAME_SNAPSHOT", "SAFE_PERIOD_OVERLAP")
POINT_KINDS = ("CURRENT_STATE_SNAPSHOT", "OWNERSHIP_SNAPSHOT_POINT")
PHASE3_FAMILIES = ("PDF_HISTORY", "OWNERSHIP_GRAPH")  # web-supplementary excluded (task §11)


class BudgetExceeded(RuntimeError):
    pass


# ============================== pure functions ==============================

def member_set_hash(ids):
    """Canonical group identity: sorted canonical ids -> deterministic hash."""
    return hashlib.sha256(",".join(str(i) for i in sorted(ids)).encode()).hexdigest()


def intersect_days(s1, e1, s2, e2):
    """Half-open [start,end) DAY-precision intersection length; 0 when empty."""
    lo, hi = max(s1, s2), min(e1, e2)
    return (hi - lo).days if lo < hi else 0


def classify_pair_evidence(obs_a, obs_b, per_a, per_b, attempt_hash):
    """Central pure pair classifier for ONE pair inside ONE (company, role_context).

    obs_a / obs_b: person a's and person b's observations in this (company, context),
                   both with canonical_person_id NOT NULL (web-supplementary family
                   excluded upstream; PHASE3_FAMILIES only).
    per_a / per_b: {"bounded": [(start, end, open_key, close_key), ...],
                    "left_censored": bool}  (plan §12.1 safe periods only)
    attempt_hash:  {source_attempt_id: response_sha256} for OWNERSHIP_GRAPH.

    Returns a list of grain rows, one per applicable disjunct
    co_participation_class (plan §11 truth table / §13 shared-source predicates):

      SAME_SNAPSHOT            Felag current snapshot shared identity (case 4)
      SAME_OBSERVATION         same Lysing source_event_id (case 5/9) OR same
                               ownership source_attempt_id + resolved response hash
      KNOWN_TEMPORAL_OVERLAP   bounded×bounded DAY intersection (case 1);
                               point-in-bounded (cases 2/3, days stay NULL)
      UNKNOWN_TEMPORAL_RELATION left-censored evidence (cases 7/8); point×point
                               different identity (case 6)
      SAME_COMPANY_EVER        safely observed same company+context, no temporal
                               knowledge (cases 6 '+', 9, default)

    known_overlap_days is set ONLY for truth-table case 1 (bounded×bounded);
    point semantics stay 0/NULL (never a fabricated 1 day).
    """
    all_obs = obs_a + obs_b
    known_dates = sorted(o["observed_at"] for o in all_obs if o["observed_at"] is not None)
    rows, skipped_fallback = [], 0

    # ---- direct shared-source identities (plan §13)
    snap_ids, evt_ids, own_ids = set(), set(), set()
    b_snaps = {q["source_document_id"] for q in obs_b
               if q["observation_kind"] == "CURRENT_STATE_SNAPSHOT" and q["source_document_id"]}
    b_events = {q["source_event_id"] for q in obs_b if q["source_event_id"]}
    b_own = {q["source_attempt_id"] for q in obs_b
             if q["source_family"] == "OWNERSHIP_GRAPH" and q["source_attempt_id"]}
    for o in obs_a:
        if o["observation_kind"] == "CURRENT_STATE_SNAPSHOT" and o["source_document_id"] in b_snaps:
            snap_ids.add(o["source_document_id"])
        if o["source_event_id"] and o["source_event_id"] in b_events:
            evt_ids.add(o["source_event_id"])
        if o["source_family"] == "OWNERSHIP_GRAPH" and o["source_attempt_id"] in b_own \
                and attempt_hash.get(o["source_attempt_id"]):
            own_ids.add((o["source_attempt_id"], attempt_hash[o["source_attempt_id"]]))

    ev_dates = sorted({o["observed_at"] for o in all_obs
                       if o["source_event_id"] and o["observed_at"] is not None})
    if snap_ids:
        rows.append({
            "co_participation_class": "SAME_SNAPSHOT",
            "observation_count": 2 * len(snap_ids),
            "episode_count": len(snap_ids),
            "known_overlap_days": None,
            "first_observed": known_dates[0] if known_dates else None,
            "last_observed": known_dates[-1] if known_dates else None,
            "same_source_event_count": 0,
            "same_snapshot_count": len(snap_ids),
            "source_family": "PDF_HISTORY",
            "authority_class": "AUTHORITATIVE_CURRENT_GOVERNANCE",
            "evidence_keys": sorted({o["source_observation_key"] for o in all_obs
                                     if o["observation_kind"] == "CURRENT_STATE_SNAPSHOT"
                                     and o["source_document_id"] in snap_ids}),
        })
    if evt_ids:
        rows.append({
            "co_participation_class": "SAME_OBSERVATION",
            "observation_count": 2 * len(evt_ids),
            "episode_count": len(evt_ids),
            "known_overlap_days": None,
            "first_observed": ev_dates[0] if ev_dates else None,
            "last_observed": ev_dates[-1] if ev_dates else None,
            "same_source_event_count": len(evt_ids),
            "same_snapshot_count": 0,
            "source_family": "PDF_HISTORY",
            "authority_class": "HISTORICAL_PDF_OBSERVATION",
            "evidence_keys": sorted({o["source_observation_key"] for o in all_obs
                                     if o["source_event_id"] in evt_ids}),
        })
    if own_ids:
        own_attempt_ids = {a for a, _h in own_ids}
        okeys = sorted({o["source_observation_key"] for o in all_obs
                        if o["source_attempt_id"] in own_attempt_ids})
        odates = sorted({o["observed_at"] for o in all_obs if o["source_observation_key"] in okeys
                         and o["observed_at"] is not None})
        rows.append({
            "co_participation_class": "SAME_OBSERVATION",
            "observation_count": 2 * len(own_ids),
            "episode_count": len(own_ids),
            "known_overlap_days": None,
            "first_observed": odates[0] if odates else None,
            "last_observed": odates[-1] if odates else None,
            "same_source_event_count": 0,
            "same_snapshot_count": 0,
            "source_family": "OWNERSHIP_GRAPH",
            "authority_class": "OWNERSHIP_POINT_OBSERVATION",
            "evidence_keys": okeys,
        })

    # ---- safe temporal evidence (bounded x bounded, point x bounded)
    inter, point_hits, tkeys = [], [], set()
    for (s1, e1, k1o, k1c) in per_a["bounded"]:
        for (s2, e2, k2o, k2c) in per_b["bounded"]:
            d = intersect_days(s1, e1, s2, e2)
            if d > 0:
                inter.append(d)
                tkeys.update((k1o, k1c, k2o, k2c))
    for pts, other_per in ((obs_a, per_b), (obs_b, per_a)):
        for o in pts:
            if o["observation_kind"] in POINT_KINDS and o["observed_at"] is not None:
                for (s, e, ko, kc) in other_per["bounded"]:
                    if s <= o["observed_at"] < e:
                        point_hits.append(o["observed_at"])
                        tkeys.update((o["source_observation_key"], ko, kc))
    if inter or point_hits:
        tdates = sorted(tdateset) if (tdateset := sorted(
            {x for (s, e, *_r) in per_a["bounded"] + per_b["bounded"] for x in (s, e)}
            | set(point_hits))) else []
        rows.append({
            "co_participation_class": "KNOWN_TEMPORAL_OVERLAP",
            "observation_count": len(tkeys),
            "episode_count": len(inter) + len(point_hits),
            "known_overlap_days": sum(inter) if inter else None,  # point hits add no days
            "first_observed": tdates[0] if tdates else None,
            "last_observed": tdates[-1] if tdates else None,
            "same_source_event_count": 0,
            "same_snapshot_count": 0,
            "source_family": "PDF_HISTORY",
            "authority_class": "HISTORICAL_PDF_OBSERVATION",  # period-anchored relation
            "evidence_keys": sorted(tkeys),
        })

    # ---- fallback classes (only when no direct row and no KNOWN overlap exists)
    if not any(r["co_participation_class"] in ("SAME_SNAPSHOT", "SAME_OBSERVATION",
                                               "KNOWN_TEMPORAL_OVERLAP") for r in rows):
        fams = {o["source_family"] for o in all_obs}
        auths = {o["authority_class"] for o in all_obs}
        lc = per_a["left_censored"] or per_b["left_censored"]
        # case 6 applies only when BOTH persons carry pure point evidence
        # (current snapshots / ownership points); historical event evidence is
        # same-company-ever (case 9 / default), left-censored stays UNKNOWN (7/8)
        point_only = all(o["observation_kind"] in POINT_KINDS for o in all_obs)
        candidates = []
        if lc:  # cases 7/8
            candidates.append(("UNKNOWN_TEMPORAL_RELATION",))
        elif point_only:  # case 6: UNKNOWN (+ SAME_COMPANY_EVER)
            candidates.append(("UNKNOWN_TEMPORAL_RELATION",))
            candidates.append(("SAME_COMPANY_EVER",))
        else:  # cases 9 / default
            candidates.append(("SAME_COMPANY_EVER",))
        for (cls, ) in candidates:
            # M-01 closure: a valid fallback pair fact is never dropped because its
            # contributing evidence spans multiple families/authorities. The pair
            # FACT (co_participation_class) is independent of the source AUTHORITY
            # LABEL: single-valued contributing metadata is stored verbatim;
            # heterogeneous contributing metadata is stored as NULL — metadata
            # incompleteness-by-design, mechanically reconstructible from the
            # same-build observations/periods via the grain. Never an invented
            # 'MIXED' class; direct evidence rows keep precise metadata.
            rows.append({
                "co_participation_class": cls,
                "observation_count": len(all_obs),
                "episode_count": 1,
                "known_overlap_days": None,
                "first_observed": known_dates[0] if known_dates else None,
                "last_observed": known_dates[-1] if known_dates else None,
                "same_source_event_count": 0,
                "same_snapshot_count": 0,
                "source_family": next(iter(sorted(fams))) if len(fams) == 1 else None,
                "authority_class": next(iter(sorted(auths))) if len(auths) == 1 else None,
                "evidence_keys": sorted({o["source_observation_key"] for o in all_obs}),
            })
    return rows, skipped_fallback

# ============================ pair derivation (live) ============================

def _period_view(periods):
    """per (company, context, person) -> {"bounded": [...], "left_censored": bool}"""
    out = defaultdict(lambda: {"bounded": [], "left_censored": False})
    for p in periods:
        k = (p["canonical_company_id"], p["role_context"], p["canonical_person_id"])
        if p["period_status"] == "BOUNDED":
            out[k]["bounded"].append((p["period_start"], p["period_end"],
                                      p["opening_observation_key"], p["closing_observation_key"]))
        else:  # OPEN_LEFT_CENSORED / CLOSED_ONLY: never duration-safe
            out[k]["left_censored"] = True
    return out


def derive_pairs(observations, periods, attempt_hash):
    """Pairs ONLY from this build's observations/periods (task §12). Returns
    (pair_rows, stats). Grain: (person_a<person_b, company, context, class)."""
    ctx_obs = defaultdict(lambda: defaultdict(list))
    for o in observations:
        if o["canonical_person_id"] is None or o["source_family"] not in PHASE3_FAMILIES:
            continue
        ctx_obs[(o["canonical_company_id"], o["role_context"])][o["canonical_person_id"]].append(o)
    per_view = _period_view(periods)
    rows, stats = [], {"pairs_by_context": defaultdict(int), "pairs_by_class": defaultdict(int),
                       "mixed_authority_fallback_skipped": 0, "known_overlap_rows": 0,
                       "same_snapshot_pairs": 0, "same_event_pairs": 0,
                       "ownership_same_observation_pairs": 0, "known_overlap_days_hist": defaultdict(int)}
    for (cid, ctx), by_person in sorted(ctx_obs.items()):
        persons = sorted(by_person)
        for a, b in itertools.combinations(persons, 2):
            per_a = per_view.get((cid, ctx, a), {"bounded": [], "left_censored": False})
            per_b = per_view.get((cid, ctx, b), {"bounded": [], "left_censored": False})
            evidence_rows, skipped = classify_pair_evidence(
                by_person[a], by_person[b], per_a, per_b, attempt_hash)
            if skipped:
                stats["mixed_authority_fallback_skipped"] += skipped
            for r in evidence_rows:
                row = {"person_a_id": a, "person_b_id": b,
                       "canonical_company_id": cid, "role_context": ctx}
                row.update({k: v for k, v in r.items() if k != "evidence_keys"})
                rows.append(row)
                cls = r["co_participation_class"]
                stats["pairs_by_class"][cls] += 1
                stats["pairs_by_context"][ctx] += 1
                if cls == "SAME_SNAPSHOT":
                    stats["same_snapshot_pairs"] += 1
                elif cls == "KNOWN_TEMPORAL_OVERLAP":
                    stats["known_overlap_rows"] += 1
                    if r["known_overlap_days"] is not None:
                        stats["known_overlap_days_hist"][min(r["known_overlap_days"], 9999)] += 1
                elif cls == "SAME_OBSERVATION":
                    if r["source_family"] == "OWNERSHIP_GRAPH":
                        stats["ownership_same_observation_pairs"] += 1
                    elif r["same_source_event_count"]:
                        stats["same_event_pairs"] += 1
    return rows, stats


# ========================= group episode derivation =========================

def derive_group_episodes(observations, periods, attempt_hash):
    """Direct/safe co-participation episodes (plan §14.1/§14.2, task §28-§30).
    NEVER replays individual APPOINTED/REMOVED into rosters (§27)."""
    episodes = []
    ctx_obs = defaultdict(lambda: defaultdict(list))
    for o in observations:
        if o["canonical_person_id"] is None or o["source_family"] not in PHASE3_FAMILIES:
            continue
        ctx_obs[(o["canonical_company_id"], o["role_context"])][o["canonical_person_id"]].append(o)

    def add_ep(cid, ctx, kind, family, members, start, end, member_prov):
        if len(set(members)) < 2:
            return
        episodes.append({"canonical_company_id": cid, "role_context": ctx,
                         "co_observation_kind": kind, "source_family": family,
                         "members": frozenset(members), "start": start, "end": end,
                         "member_prov": member_prov})

    ctx_keys = set(ctx_obs.keys()) | {
        (p["canonical_company_id"], p["role_context"]) for p in periods
        if p["period_status"] == "BOUNDED"}
    for (cid, ctx) in sorted(ctx_keys):
        by_person = ctx_obs.get((cid, ctx), {})
        # ---- SAME_SNAPSHOT (task §28): same Felag snapshot identity
        by_doc = defaultdict(dict)   # doc_id -> {pid: obs}
        by_evt = defaultdict(dict)   # event_id -> {pid: obs}
        by_own = defaultdict(dict)   # (attempt, hash) -> {pid: obs}
        for pid, obs in sorted(by_person.items()):
            for o in obs:
                if o["observation_kind"] == "CURRENT_STATE_SNAPSHOT" and o["source_document_id"]:
                    by_doc[o["source_document_id"]][pid] = o
                if o["source_event_id"]:
                    by_evt[o["source_event_id"]][pid] = o
                if o["source_family"] == "OWNERSHIP_GRAPH" and o["source_attempt_id"] \
                        and attempt_hash.get(o["source_attempt_id"]):
                    by_own[(o["source_attempt_id"], attempt_hash[o["source_attempt_id"]])][pid] = o
        for doc, members in sorted(by_doc.items(), key=lambda kv: str(kv[0])):
            dates = [o["observed_at"] for o in members.values() if o["observed_at"]]
            add_ep(cid, ctx, "SAME_SNAPSHOT", "PDF_HISTORY", members.keys(),
                   dates[0] if dates else None, dates[0] if dates else None,
                   {pid: [(o["source_observation_key"], "MEMBER")] for pid, o in members.items()})
        for evt, members in sorted(by_evt.items(), key=lambda kv: str(kv[0])):
            dates = [o["observed_at"] for o in members.values() if o["observed_at"]]
            add_ep(cid, ctx, "SAME_EVENT", "PDF_HISTORY", members.keys(),
                   min(dates) if dates else None, max(dates) if dates else None,
                   {pid: [(o["source_observation_key"], "MEMBER")] for pid, o in members.items()})
        for (att, hsh), members in sorted(by_own.items(), key=lambda kv: str(kv[0])):
            dates = [o["observed_at"] for o in members.values() if o["observed_at"]]
            add_ep(cid, ctx, "SAME_SNAPSHOT", "OWNERSHIP_GRAPH", members.keys(),
                   min(dates) if dates else None, max(dates) if dates else None,
                   {pid: [(o["source_observation_key"], "MEMBER")] for pid, o in members.items()})
        # ---- SAFE_PERIOD_OVERLAP (task §30): common non-empty intersection of
        # ALL members' safe BOUNDED DAY periods. Atomic-interval sweep.
        bnd = {}
        for p in periods:
            if p["canonical_company_id"] != cid or p["role_context"] != ctx \
                    or p["period_status"] != "BOUNDED":
                continue
            plist = bnd.setdefault(p["canonical_person_id"], [])
            plist.append((p["period_start"], p["period_end"],
                          p["opening_observation_key"], p["closing_observation_key"]))
        for pid in bnd:
            bnd[pid].sort(key=lambda x: (x[0], x[1]))
        if len(bnd) >= 2:
            pts = sorted({x for pl in bnd.values() for (s, e, *_r) in pl for x in (s, e)})
            runs, cur_set, cur_start, cur_prov = [], None, None, None
            for i in range(len(pts) - 1):
                s, e = pts[i], pts[i + 1]
                active = {}
                for pid, pl in bnd.items():
                    for (ps, pe, ko, kc) in pl:
                        if ps <= s and pe >= e:
                            active[pid] = [(ko, "PERIOD_OPENING"), (kc, "PERIOD_CLOSING")]
                            break
                fs = frozenset(active)
                if len(fs) >= 2:
                    if cur_set == fs:
                        cur_end = e
                        cur_prov = active
                    else:
                        if cur_set is not None:
                            runs.append((cur_start, cur_end, cur_set, cur_prov))
                        cur_set, cur_start, cur_end, cur_prov = fs, s, e, active
                else:
                    if cur_set is not None:
                        runs.append((cur_start, cur_end, cur_set, cur_prov))
                    cur_set = None
            if cur_set is not None:
                runs.append((cur_start, cur_end, cur_set, cur_prov))
            for (s, e, fs, prov) in runs:
                add_ep(cid, ctx, "SAFE_PERIOD_OVERLAP", "PDF_HISTORY", fs, s, e, prov)
    # exact-duplicate removal (same-fact representations count once)
    seen, dedup = set(), []
    for ep in sorted(episodes, key=lambda e: (e["canonical_company_id"], e["role_context"],
                                              e["co_observation_kind"], str(e["start"]),
                                              str(e["end"]), member_set_hash(e["members"]))):
        sig = (ep["canonical_company_id"], ep["role_context"], ep["co_observation_kind"],
               ep["start"], ep["end"], tuple(sorted(ep["members"])))
        if sig not in seen:
            seen.add(sig)
            dedup.append(ep)
    return dedup


def _periods_for(periods, cid, ctx, pid):
    return [p for p in periods if p["canonical_company_id"] == cid and p["role_context"] == ctx
            and p["canonical_person_id"] == pid and p["period_status"] == "BOUNDED"]


# ===================== candidate generation (Apriori) =====================

def apriori_groups(episodes, t_company, t_episode, cap, max_candidates, context):
    """Deterministic level-wise Apriori over episode member-sets for ONE context.
    Survivor rule: support_company_count >= t_company OR support_episode_count >= t_episode
    (both counters monotone -> downward-closure pruning valid). Returns
    ({itemset tuple: {"companies", "episodes"}}, stats). Raises BudgetExceeded."""
    stats = {"context": context, "candidates_generated": 0, "candidates_pruned": 0,
             "levels": {}, "budget": max_candidates}
    person_eps = defaultdict(set)
    for i, ep in enumerate(episodes):
        for p in ep["members"]:
            person_eps[p].add(i)

    def support(item):
        eps = set.intersection(*(person_eps[p] for p in item))
        comps = {episodes[i]["canonical_company_id"] for i in eps}
        return sorted(eps), comps

    def check_budget(n_new):
        stats["candidates_generated"] += n_new
        if stats["candidates_generated"] > max_candidates:
            raise BudgetExceeded(
                f"MAX_CANDIDATE_COUNT exceeded in context {context}: "
                f"{stats['candidates_generated']} > {max_candidates}")

    supported, prev_items = {}, set()
    # L2 from co-occurrence inside episodes
    cands = set()
    for ep in episodes:
        ms = sorted(ep["members"])
        if len(ms) >= 2:
            cands.update(itertools.combinations(ms, 2))
    check_budget(len(cands))
    for item in sorted(cands):
        eps, comps = support(item)
        if len(comps) >= t_company or len(eps) >= t_episode:
            supported[item] = {"companies": comps, "episodes": eps}
            prev_items.add(item)
        else:
            stats["candidates_pruned"] += 1
    stats["levels"][2] = len(supported)
    # Lk: join survivors on shared (k-2)-prefix, subset-prune, count
    k = 3
    prev_list = sorted(prev_items)
    while prev_list and k <= cap:
        by_prefix = defaultdict(list)
        for item in prev_list:
            by_prefix[item[:-1]].append(item)
        cands = set()
        for prefix, items in sorted(by_prefix.items()):
            for i1, i2 in itertools.combinations(items, 2):
                cand = tuple(sorted(set(i1) | set(i2)))
                if len(cand) == k:
                    cands.add(cand)
        check_budget(len(cands))
        new_prev = set()
        for item in sorted(cands):
            if any(tuple(sorted(item[:i] + item[i + 1:])) not in prev_items
                   for i in range(len(item))):
                stats["candidates_pruned"] += 1
                continue
            eps, comps = support(item)
            if len(comps) >= t_company or len(eps) >= t_episode:
                supported[item] = {"companies": comps, "episodes": eps}
                new_prev.add(item)
            else:
                stats["candidates_pruned"] += 1
        stats["levels"][k] = len(new_prev)
        prev_items = new_prev
        prev_list = sorted(new_prev)
        k += 1
    return supported, stats


def derive_core_groups(supported, episodes, t_context_episodes, core_threshold, cap):
    """CORE_GROUP only from already-persisted (supported) groups (task §47/§48).
    Minimum-cardinality guard: episodes >= 3 OR (companies >= 2 AND episodes >= 2)
    BEFORE the 75% core threshold applies."""
    cores = []
    for item, sup in sorted(supported.items()):
        n_ep, n_co = len(sup["episodes"]), len(sup["companies"])
        if len(item) < 3 or not (n_ep >= 3 or (n_co >= 2 and n_ep >= 2)):
            continue
        parent_eps = sup["episodes"]
        for size in range(2, min(len(item), cap)):
            for sub in itertools.combinations(item, size):
                in_core = [i for i in parent_eps if set(sub) <= set(episodes[i]["members"])]
                ratio = len(in_core) / len(parent_eps)
                if ratio + 1e-9 >= core_threshold:
                    cores.append({"item": sub, "parent": item, "ratio": ratio,
                                  "core_episodes": in_core, "parent_episodes": len(parent_eps),
                                  "companies": sorted({episodes[i]["canonical_company_id"] for i in in_core})})
    return cores


# ====================== group/episode row assembly (live) ======================

DATE_MIN = date(1, 1, 1)
DATE_MAX = date(9999, 12, 31)


def _ep_sort_key(ep):
    return (ep["start"] or DATE_MIN, ep["end"] or DATE_MAX, ep["co_observation_kind"],
            ep["canonical_company_id"], member_set_hash(ep["members"]))


def assemble_group_rows(episodes, supported_by_ctx, cores_by_ctx, core_threshold, cap):
    """Build person_group / person_group_member / person_group_observation /
    person_group_observation_member row dicts (observation keys; physical ids are
    resolved at insert time). Deterministic ordering and episode indexing."""
    # NOTE: `episodes` must arrive pre-sorted by _ep_sort_key (run_build does this
    # once) so episode indices used by apriori/cores match assembly exactly.
    ctx_eps = defaultdict(list)
    for ep in episodes:
        ctx_eps[ep["role_context"]].append(ep)

    groups, members, gobs, junction = [], [], [], []
    for ctx in sorted(ctx_eps):
        eps = ctx_eps[ctx]
        planned = [("RECURRING_GROUP", item, sup, None)
                   for item, sup in sorted(supported_by_ctx.get(ctx, {}).items())]
        for c in sorted(cores_by_ctx.get(ctx, []),
                        key=lambda c: (member_set_hash(c["item"]), member_set_hash(c["parent"]))):
            planned.append(("CORE_GROUP", c["item"], c, None))
        seen = set()
        for kind, item, sup, _x in planned:
            msh = member_set_hash(item)
            if (msh, kind) in seen:
                continue  # first (deterministically sorted) wins
            seen.add((msh, kind))
            if kind == "CORE_GROUP":
                member_eps = [eps[i] for i in sorted(set(sup["core_episodes"]))]
                support_doc = {
                    "core_of_member_set_hashes": sorted({member_set_hash(sup["parent"])}),
                    "core_ratio": round(sup["ratio"], 4),
                    "core_episode_count": len(sup["core_episodes"]),
                    "parent_episode_count": sup["parent_episodes"],
                    "core_threshold": core_threshold,
                }
            else:
                member_eps = [e for e in eps if set(item) <= e["members"]]
                ekys = [f"{e['canonical_company_id']}:{e['co_observation_kind']}:"
                        f"{e['start']}..{e['end']}:{member_set_hash(e['members'])[:12]}"
                        for e in member_eps]
                # M-02 closure: support is context-level (role_context + member set);
                # episode kind is provenance only. kind_counts is a deterministic
                # breakdown of the combined support, NOT part of eligibility grain.
                kind_counts = Counter(e["co_observation_kind"] for e in member_eps)
                support_doc = {
                    "support_company_count": len(sup["companies"]),
                    "support_episode_count": len(member_eps),
                    "episode_kind_counts": {k: kind_counts[k] for k in sorted(kind_counts)},
                    "episode_keys": ekys[:50] + ([f"...truncated {len(ekys) - 50}"]
                                                 if len(ekys) > 50 else []),
                }
            starts = [e["start"] for e in member_eps if e["start"]]
            ends = [e["end"] for e in member_eps if e["end"]]
            groups.append({"member_set_hash": msh, "role_context": ctx, "group_kind": kind,
                           "group_size": len(item), "support": support_doc,
                           "first_observed": min(starts) if starts else None,
                           "last_observed": max(ends) if ends else None,
                           "members": list(item)})
            members.extend({"member_set_hash": msh, "role_context": ctx, "group_kind": kind,
                            "canonical_person_id": p} for p in item)
            counters = defaultdict(int)
            for e in member_eps:
                counters[e["canonical_company_id"]] += 1
                ekey = (f"EP:{e['canonical_company_id']}:{ctx}:{msh[:16]}:"
                        f"{counters[e['canonical_company_id']]:03d}")
                gobs.append({"member_set_hash": msh, "role_context": ctx, "group_kind": kind,
                             "episode_key": ekey, "canonical_company_id": e["canonical_company_id"],
                             "episode_start": e["start"], "episode_end": e["end"],
                             "source_family": e["source_family"],
                             "co_observation_kind": e["co_observation_kind"]})
                for pid in sorted(item):
                    for (okey, role) in e["member_prov"].get(pid, []):
                        junction.append({"member_set_hash": msh, "role_context": ctx,
                                         "group_kind": kind, "episode_key": ekey,
                                         "canonical_company_id": e["canonical_company_id"],
                                         "observation_key": okey, "role_in_episode": role})
    keys = [(g["member_set_hash"], g["role_context"], g["group_kind"], g["episode_key"])
            for g in gobs]
    assert len(keys) == len(set(keys)), "duplicate episode_key collision"
    return groups, members, gobs, junction


def content_hash3(obs_rows, period_rows, pair_rows, groups, gobs, junction):
    """Phase 3 full semantic hash: Phase 2 observations+periods PLUS pairs, groups,
    group members, group observations and junction provenance (task §51)."""
    base = content_hash(obs_rows, period_rows)
    ph = hashlib.md5("\n".join(sorted(
        "|".join("NULL" if r[k] is None else str(r[k]) for k in (
            "person_a_id", "person_b_id", "canonical_company_id", "role_context",
            "co_participation_class", "observation_count", "episode_count",
            "known_overlap_days", "first_observed", "last_observed",
            "same_source_event_count", "same_snapshot_count", "source_family",
            "authority_class")) for r in pair_rows)).encode()).hexdigest()
    gh = hashlib.md5("\n".join(sorted(
        "|".join("NULL" if g[k] is None else str(g[k]) for k in (
            "member_set_hash", "role_context", "group_kind", "group_size"))
        + "|" + json.dumps(g["support"], sort_keys=True, default=str)
        + "|" + str(g["first_observed"]) + "|" + str(g["last_observed"])
        + "|" + ",".join(str(m) for m in sorted(g["members"])) for g in groups)).encode()).hexdigest()
    goh = hashlib.md5("\n".join(sorted(
        "|".join("NULL" if g[k] is None else str(g[k]) for k in (
            "member_set_hash", "role_context", "group_kind", "episode_key",
            "canonical_company_id", "episode_start", "episode_end",
            "source_family", "co_observation_kind")) for g in gobs)).encode()).hexdigest()
    jh = hashlib.md5("\n".join(sorted(
        f"{j['member_set_hash']}|{j['role_context']}|{j['group_kind']}|{j['episode_key']}"
        f"|{j['canonical_company_id']}|{j['observation_key']}|{j['role_in_episode']}"
        for j in junction)).encode()).hexdigest()
    return hashlib.md5(f"{base}|{ph}|{gh}|{goh}|{jh}".encode()).hexdigest()


# =============================== live build ==================================

def load_phase0_params(path=PHASE0_FILE):
    a = json.load(open(path))
    rp, pb = a["recommended_parameters"], a["performance_budget"]
    return {"company_threshold": rp["recommended_company_threshold"],
            "episode_threshold": rp["recommended_episode_threshold"],
            "group_size_cap": rp["recommended_group_size_cap"],
            "max_build_time_s": pb["MAX_BUILD_TIME"]["recommended_limit_s"],
            "max_peak_memory_mb": pb["MAX_PEAK_MEMORY"]["recommended_limit_mb"],
            "max_candidate_count": pb["MAX_CANDIDATE_COUNT"]["recommended_limit"],
            "core_threshold": 0.75,  # plan §14.3 default (not in Phase 0 artifact)
            "artifact_measured_at": a["measured_at"]}


def fetch_attempt_hash(ch):
    """task §15: observation.source_attempt_id -> public.request_attempt -> response_sha256."""
    return {r["attempt_id"]: r["response_sha256"] for r in ch.execute(
        "SELECT attempt_id, response_sha256 FROM public.request_attempt "
        "WHERE response_sha256 IS NOT NULL").fetchall()}


def persist_phase3(ch, pd, B, P, derived):
    """Complete Phase 3 build lifecycle. Everything (observations, periods, pairs,
    groups, members, group observations, junction provenance) is inserted and
    validated in ONE build; the previous ACTIVE build is superseded ONLY after
    full validation + content hash. No partial ACTIVE (task §3/§52)."""
    pin = B.get("phase0_id", "")
    if not re.fullmatch(r"\S+#sha256:[0-9a-f]{64}", pin):
        raise RuntimeError(f"PHASE0 PIN REFUSED before insert: malformed pin: {pin!r}")
    live = build_phase0_pin(B.get("phase0_artifact_path", PHASE0_FILE))
    if live["pin"] != pin or live["measured_at"] != B.get("p0_at"):
        raise RuntimeError("PHASE0 PIN REFUSED before insert: pin does not match current artifact bytes")
    pair_rows, pair_stats, episodes, ap_stats_by_ctx, groups, members, gobs, junction = derived
    cov = dict(B["cov"])
    cov.update({
        "pair_rows_total": len(pair_rows),
        "unique_person_pairs": len({(r["person_a_id"], r["person_b_id"]) for r in pair_rows}),
        "group_episodes": len(episodes),
        "groups_persisted": len(groups),
        "group_observations": len(gobs),
        "junction_rows": len(junction),
        "phase3_parameters": P})
    t0 = time.perf_counter()
    cur = ch.cursor()
    cur.execute("SET default_transaction_read_only = off")
    cur.execute("""INSERT INTO registry_analytics.analytics_build
        (source_projection_run_id, pdf_link_code_version, ownership_source_meta, canonical_identity_pin,
         temporal_conventions, group_support_policy, group_size_cap, phase0_measurement_id,
         phase0_measured_at, build_warnings, coverage, content_hash, status)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,'FAILED') RETURNING build_run_id""",
                (B["pin_run"], B["link_ver"],
                 json.dumps({"build_meta_id": B["own"]["build_id"], "schema_version": B["own"]["schema_version"],
                             "source_relation_count": B["own"]["source_relation_count"],
                             "source_relation_max_id": B["own"]["source_relation_max_id"],
                             "content_hash": B["own"]["content_hash"], "generation_pin": B["own_gen_pin"]}),
                 json.dumps({"policy": "referenced canonical entity ids + kinds pinned per build",
                             "entity_count": len({o["canonical_person_id"] for o in B["observations"] if o["canonical_person_id"]})
                                             + len({o["canonical_company_id"] for o in B["observations"]})}),
                 json.dumps(TEMPORAL),
                 json.dumps({"company_threshold": P["company_threshold"], "episode_threshold": P["episode_threshold"],
                             "core_threshold": P["core_threshold"], "group_size_cap": P["group_size_cap"],
                             "source": "docs/phase0_scale_measurement.json#recommended_parameters"}),
                 P["group_size_cap"], B["phase0_id"], B["p0_at"],
                 json.dumps(B["warnings"], default=str), json.dumps(cov, default=str)))
    build_run_id = cur.fetchone()["build_run_id"]
    ch.commit()
    try:
        cur.execute("SET default_transaction_read_only = off")
        # ---- observations + periods (identical Phase 2 semantics, unchanged)
        obs_cols = ["build_run_id", "source_observation_key", "canonical_person_id", "canonical_company_id",
                    "role_context", "source_family", "authority_class", "identity_resolution",
                    "observation_kind", "observed_at", "observed_state_at", "action", "state",
                    "ownership_percentage", "source_projection_run_id", "source_person_role_id",
                    "source_history_event_id", "source_event_id", "source_document_id",
                    "source_observation_id", "source_relation_id", "source_attempt_id", "provenance"]
        rows = [[build_run_id] + [o[k] for k in obs_cols[1:22]] + [json.dumps(o["provenance"], default=str)]
                for o in sorted(B["observations"], key=lambda x: x["source_observation_key"])]
        cur.executemany(f"INSERT INTO registry_analytics.person_company_observation ({','.join(obs_cols)}) "
                        f"VALUES ({','.join(['%s'] * 23)})", rows)
        per_rows = [[build_run_id, p["canonical_person_id"], p["canonical_company_id"], p["role_context"],
                     p["period_start"], p["period_end"], p["start_status"], p["end_status"],
                     p["left_censored_start"], p["left_censored_end"], p["period_status"],
                     p["opening_observation_key"], p["closing_observation_key"], p["source_family"]]
                    for p in sorted(B["periods"], key=lambda p: (p["canonical_person_id"], p["canonical_company_id"],
                                                                 p["role_context"], str(p["period_start"]),
                                                                 p["opening_observation_key"] or ""))]
        cur.executemany("""INSERT INTO registry_analytics.person_company_period
            (build_run_id, canonical_person_id, canonical_company_id, role_context, period_start, period_end,
             start_status, end_status, left_censored_start, left_censored_end, period_status,
             opening_observation_key, closing_observation_key, source_family)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", per_rows)
        # ---- pairs
        cur.executemany("""INSERT INTO registry_analytics.person_person_overlap
            (build_run_id, person_a_id, person_b_id, canonical_company_id, role_context,
             co_participation_class, observation_count, episode_count, known_overlap_days,
             first_observed, last_observed, same_source_event_count, same_snapshot_count,
             source_family, authority_class) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            [[build_run_id, r["person_a_id"], r["person_b_id"], r["canonical_company_id"], r["role_context"],
              r["co_participation_class"], r["observation_count"], r["episode_count"],
              r["known_overlap_days"], r["first_observed"], r["last_observed"],
              r["same_source_event_count"], r["same_snapshot_count"], r["source_family"],
              r["authority_class"]] for r in pair_rows])
        # ---- groups + members
        gid = {}
        for g in groups:
            r = cur.execute("""INSERT INTO registry_analytics.person_group
                (build_run_id, member_set_hash, role_context, group_kind, group_size, support,
                 first_observed, last_observed) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING group_id""",
                            (build_run_id, g["member_set_hash"], g["role_context"], g["group_kind"],
                             g["group_size"], json.dumps(g["support"], default=str),
                             g["first_observed"], g["last_observed"])).fetchone()
            gid[(g["member_set_hash"], g["role_context"], g["group_kind"])] = r["group_id"]
        cur.executemany("""INSERT INTO registry_analytics.person_group_member (group_id, canonical_person_id)
            VALUES (%s,%s)""",
            [[gid[(m["member_set_hash"], m["role_context"], m["group_kind"])], m["canonical_person_id"]]
             for m in members])
        # ---- group observations + junction provenance
        cur.executemany("""INSERT INTO registry_analytics.person_group_observation
            (build_run_id, group_id, episode_key, canonical_company_id, role_context, episode_start,
             episode_end, source_family, co_observation_kind) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            [[build_run_id, gid[(g["member_set_hash"], g["role_context"], g["group_kind"])],
              g["episode_key"], g["canonical_company_id"], g["role_context"], g["episode_start"],
              g["episode_end"], g["source_family"], g["co_observation_kind"]] for g in gobs])
        key_map = {r["source_observation_key"]: r["observation_id"] for r in ch.execute(
            "SELECT observation_id, source_observation_key FROM registry_analytics.person_company_observation "
            "WHERE build_run_id=%s", (build_run_id,)).fetchall()}
        go_map = {(r["group_id"], r["episode_key"]): r["group_observation_id"] for r in ch.execute(
            """SELECT go.group_observation_id, go.group_id, go.episode_key
               FROM registry_analytics.person_group_observation go
               JOIN registry_analytics.person_group pg ON pg.group_id = go.group_id
               WHERE go.build_run_id=%s""", (build_run_id,)).fetchall()}
        jrows, missing = [], []
        for j in junction:
            gk = (j["member_set_hash"], j["role_context"], j["group_kind"])
            goid = go_map.get((gid[gk], j["episode_key"]))
            oid = key_map.get(j["observation_key"])
            if goid is None or oid is None:
                missing.append(j)
                continue
            jrows.append([goid, oid, j["role_in_episode"]])
        assert not missing, f"junction rows without resolved FK ids: {len(missing)}"
        cur.executemany("""INSERT INTO registry_analytics.person_group_observation_member
            (group_observation_id, observation_id, role_in_episode) VALUES (%s,%s,%s)""", jrows)
        ch.commit()
    except Exception:
        ch.rollback()
        raise
    return {"build_run_id": build_run_id, "t0": t0, "cur": cur, "pair_stats": pair_stats,
            "ap_stats_by_ctx": ap_stats_by_ctx, "cov": cov}


def validate_and_activate(ch, ctx):
    """Phase 2 gates (unchanged) + Phase 3 completeness gates (task §52), then
    full content hash -> ACTIVE; previous ACTIVE SUPERSEDED only here."""
    build_run_id = ctx["build_run_id"]
    cur = ctx["cur"]
    B = ctx["B"]
    P = ctx["P"]
    # ---------- Phase 2 gates (byte-identical semantics to phase2_build.persist)
    gates = ch.execute("""SELECT
        (SELECT count(*) FROM registry_analytics.person_company_observation
          WHERE build_run_id=%s AND canonical_person_id IS NULL
            AND observation_kind IN ('APPOINTED_EVENT','REMOVED_EVENT')) AS unresolved_rows,
        (SELECT count(*) FROM registry_analytics.person_company_observation o
          JOIN registry_analytics.person_company_period p ON p.build_run_id=o.build_run_id
           AND p.opening_observation_key=o.source_observation_key
          WHERE o.build_run_id=%s AND o.observation_kind NOT IN ('APPOINTED_EVENT','REMOVED_EVENT')) AS bad_period_openers,
        (SELECT count(*) FROM registry_analytics.person_company_period
          WHERE build_run_id=%s AND period_status='BOUNDED' AND period_start >= period_end) AS bad_bounded,
        (SELECT count(*) FROM registry_analytics.person_company_period
          WHERE build_run_id=%s AND source_family <> 'PDF_HISTORY') AS non_pdf_periods,
        (SELECT count(*) FROM registry_analytics.person_company_observation
          WHERE build_run_id=%s AND source_family='PDF_HISTORY' AND observation_kind <> 'CURRENT_STATE_SNAPSHOT'
            AND source_observation_key NOT LIKE 'PDF:%%:CHE:%%') AS cpr_keyed_historical,
        (SELECT count(*) FROM registry_analytics.person_company_observation o
          JOIN registry_ownership.relation r ON r.relation_id=o.source_relation_id
          WHERE o.build_run_id=%s AND o.role_context='CONTROL_ACTOR'
            AND o.ownership_percentage IS NOT NULL) AS control_with_pct,
        (SELECT count(*) FROM registry_analytics.person_company_observation
          WHERE build_run_id=%s AND canonical_person_id IS NULL AND canonical_company_id IS NULL) AS fully_unresolved,
        (SELECT count(*) FROM registry_analytics.person_company_observation
          WHERE build_run_id=%s AND identity_resolution IS NOT NULL
            AND observation_kind NOT IN ('APPOINTED_EVENT','REMOVED_EVENT')) AS identity_on_non_events""",
        (build_run_id,) * 8).fetchone()
    # ---------- Phase 3 completeness gates
    gates3 = ch.execute("""SELECT
        (SELECT count(*) FROM registry_analytics.person_person_overlap ppo
          JOIN public.canonical_entity e1 ON e1.canonical_entity_id=ppo.person_a_id
          JOIN public.canonical_entity e2 ON e2.canonical_entity_id=ppo.person_b_id
         WHERE ppo.build_run_id=%s AND (e1.entity_kind <> 'PERSON' OR e2.entity_kind <> 'PERSON')) AS pair_bad_person_kind,
        (SELECT count(*) FROM registry_analytics.person_person_overlap ppo
          JOIN public.canonical_entity ec ON ec.canonical_entity_id=ppo.canonical_company_id
         WHERE ppo.build_run_id=%s AND ec.entity_kind <> 'COMPANY') AS pair_bad_company_kind,
        (SELECT count(*) FROM registry_analytics.person_person_overlap
          WHERE build_run_id=%s AND person_a_id >= person_b_id) AS pair_order_violations,
        (SELECT count(*) FROM registry_analytics.person_person_overlap ppo
          LEFT JOIN registry_analytics.person_company_observation o ON o.build_run_id=ppo.build_run_id
           AND o.canonical_person_id=ppo.person_a_id AND o.canonical_company_id=ppo.canonical_company_id
           AND o.role_context=ppo.role_context
          WHERE ppo.build_run_id=%s AND o.observation_id IS NULL) AS pair_without_observations,
        (SELECT count(*) FROM registry_analytics.person_group g
          WHERE g.build_run_id=%s AND NOT EXISTS (
            SELECT 1 FROM registry_analytics.person_group_observation go
            WHERE go.group_id=g.group_id)) AS group_without_observations,
        (SELECT count(*) FROM registry_analytics.person_group WHERE build_run_id=%s AND group_size > %s) AS group_over_cap,
        (SELECT count(*) FROM registry_analytics.person_group g
          WHERE g.build_run_id=%s
            AND (SELECT count(*) FROM registry_analytics.person_group_member m
                 WHERE m.group_id=g.group_id) <> g.group_size) AS group_member_count_mismatch,
        (SELECT count(*) FROM registry_analytics.person_group_member m
          JOIN registry_analytics.person_group g ON g.group_id=m.group_id
          JOIN public.canonical_entity e ON e.canonical_entity_id=m.canonical_person_id
          WHERE g.build_run_id=%s AND e.entity_kind <> 'PERSON') AS group_member_not_person,
        (SELECT count(*) FROM registry_analytics.person_group_member m
          JOIN registry_analytics.person_group g ON g.group_id=m.group_id
          WHERE g.build_run_id=%s AND NOT EXISTS (
            SELECT 1 FROM registry_analytics.person_company_observation o
            WHERE o.build_run_id=g.build_run_id AND o.canonical_person_id=m.canonical_person_id)) AS group_member_without_observation,
        (SELECT count(*) FROM registry_analytics.person_group_observation go
          WHERE go.build_run_id=%s
            AND (SELECT count(*) FROM registry_analytics.person_group_observation_member jm
                 WHERE jm.group_observation_id=go.group_observation_id) = 0) AS group_obs_without_junction,
        (SELECT count(*) FROM registry_analytics.person_group_observation_member jm
          JOIN registry_analytics.person_group_observation go ON go.group_observation_id=jm.group_observation_id
          JOIN registry_analytics.person_company_observation o ON o.observation_id=jm.observation_id
          WHERE go.build_run_id=%s
            AND (o.build_run_id <> go.build_run_id
                 OR o.canonical_company_id <> go.canonical_company_id
                 OR NOT EXISTS (SELECT 1 FROM registry_analytics.person_group_member m
                                JOIN registry_analytics.person_group g ON g.group_id=go.group_id
                                WHERE g.build_run_id=go.build_run_id
                                  AND m.canonical_person_id=o.canonical_person_id))) AS junction_not_same_build_provenance,
        (SELECT count(*) FROM registry_analytics.person_group g
         WHERE g.build_run_id=%s AND g.group_kind='RECURRING_GROUP'
           AND NOT (SELECT count(DISTINCT go.canonical_company_id) >= %s
                    OR count(*) >= %s
                    FROM registry_analytics.person_group_observation go
                    WHERE go.group_id=g.group_id)) AS unsupported_group_persisted,
        (SELECT count(*) FROM registry_analytics.person_group g
          WHERE g.build_run_id=%s AND g.group_kind='CORE_GROUP'
            AND (SELECT count(*) FROM registry_analytics.person_group_observation go
                 WHERE go.group_id=g.group_id) < 2) AS core_group_without_episodes,
        (SELECT count(*) FROM registry_analytics.person_group_observation go
          WHERE go.build_run_id=%s AND go.co_observation_kind NOT IN
            ('SAME_EVENT','SAME_SNAPSHOT','SAFE_PERIOD_OVERLAP')) AS bad_group_obs_kind""",
        (build_run_id, build_run_id, build_run_id, build_run_id, build_run_id,
         build_run_id, P["group_size_cap"], build_run_id, build_run_id, build_run_id,
         build_run_id, build_run_id, build_run_id, P["company_threshold"], P["episode_threshold"],
         build_run_id, build_run_id)).fetchone()
    bad = {k: v for d in (gates, gates3) for k, v in d.items() if v}
    if bad:
        cur.execute("""UPDATE registry_analytics.analytics_build SET status='FAILED', content_hash=NULL,
            coverage=%s WHERE build_run_id=%s""",
                    (json.dumps({"gate_failures": bad, "warnings": B["warnings"]}, default=str), build_run_id))
        ch.commit()
        raise RuntimeError(f"phase3 build gate failure: {bad}")
    # ---------- performance gates (Phase 0 configured hard limits)
    elapsed = time.perf_counter() - ctx["t0"]
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    perf = {"elapsed_s": round(elapsed, 2), "peak_rss_mb": round(rss_mb, 1),
            "max_build_time_s": P["max_build_time_s"], "max_peak_memory_mb": P["max_peak_memory_mb"]}
    if elapsed > P["max_build_time_s"] or rss_mb > P["max_peak_memory_mb"]:
        cur.execute("""UPDATE registry_analytics.analytics_build SET status='FAILED', content_hash=NULL,
            coverage=%s WHERE build_run_id=%s""",
                    (json.dumps({"gate_failures": {"performance_budget_exceeded": perf},
                                 "warnings": B["warnings"]}, default=str), build_run_id))
        ch.commit()
        raise RuntimeError(f"performance budget exceeded: {perf}")
    # ---------- full content hash -> ACTIVE (previous ACTIVE superseded only now)
    hh = content_hash3(B["observations"], B["periods"], ctx["pair_rows"], ctx["groups"],
                       ctx["gobs"], ctx["junction"])
    cur.execute("UPDATE registry_analytics.analytics_build SET status='SUPERSEDED' WHERE status='ACTIVE'")
    cur.execute("""UPDATE registry_analytics.analytics_build SET status='ACTIVE', content_hash=%s,
        coverage=%s, group_support_policy=%s WHERE build_run_id=%s""",
                (hh, json.dumps(ctx["cov"], default=str),
                 json.dumps({"company_threshold": P["company_threshold"], "episode_threshold": P["episode_threshold"],
                             "core_threshold": P["core_threshold"], "group_size_cap": P["group_size_cap"],
                             "source": "docs/phase0_scale_measurement.json#recommended_parameters"}),
                 build_run_id))
    ch.commit()
    return {"content_hash": hh, "performance": perf}


def run_build(tag="", dry=False):
    t0 = time.perf_counter()
    ch = psycopg.connect(env_dsn("HEIMDAL_DB"), row_factory=dict_row)
    pd = psycopg.connect(env_dsn("HEIMDAL_PDFHIST_PROJECTION_DSN"), row_factory=dict_row)
    P = load_phase0_params()
    B = collect(ch, pd)
    print(f"pinned run {B['pin_run']} | obs {len(B['observations'])} | periods {len(B['periods'])} | cap {P['group_size_cap']}")
    attempt_hash = fetch_attempt_hash(ch)
    pair_rows, pair_stats = derive_pairs(B["observations"], B["periods"], attempt_hash)
    print(f"pair rows: {len(pair_rows)}")
    episodes = derive_group_episodes(B["observations"], B["periods"], attempt_hash)
    episodes = sorted(episodes, key=_ep_sort_key)  # canonical order for all indexing
    print(f"group episodes: {len(episodes)}")
    ap_stats_by_ctx, supported_by_ctx, cores_by_ctx = {}, {}, {}
    for ctx in sorted({e["role_context"] for e in episodes}):
        eps = [e for e in episodes if e["role_context"] == ctx]
        sup, st = apriori_groups(eps, P["company_threshold"], P["episode_threshold"],
                                 P["group_size_cap"], P["max_candidate_count"], ctx)
        ap_stats_by_ctx[ctx] = st
        supported_by_ctx[ctx] = sup
        cores_by_ctx[ctx] = derive_core_groups(sup, eps, P["episode_threshold"],
                                               P["core_threshold"], P["group_size_cap"])
        print(f"  {ctx}: episodes={len(eps)} supported={len(sup)} core={len(cores_by_ctx[ctx])} "
              f"cand={st['candidates_generated']} pruned={st['candidates_pruned']}")
    groups, members, gobs, junction = assemble_group_rows(
        episodes, supported_by_ctx, cores_by_ctx, P["core_threshold"], P["group_size_cap"])
    print(f"groups: {len(groups)} | group obs: {len(gobs)} | junction: {len(junction)}")
    derived = (pair_rows, pair_stats, episodes, ap_stats_by_ctx, groups, members, gobs, junction)
    if dry:
        hh = content_hash3(B["observations"], B["periods"], pair_rows, groups, gobs, junction)
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        print(f"[DRY] pairs={len(pair_rows)} episodes={len(episodes)} groups={len(groups)} "
              f"gobs={len(gobs)} junction={len(junction)} hash={hh} rss={rss:.0f}MB "
              f"wall={time.perf_counter() - t0:.1f}s")
        return {"dry": True, "pair_rows": pair_rows, "pair_stats": pair_stats,
                "episodes": episodes, "ap_stats_by_ctx": ap_stats_by_ctx,
                "groups": groups, "gobs": gobs, "junction": junction, "P": P,
                "content_hash": hh, "B": B}
    ctx = persist_phase3(ch, pd, B, P, derived)
    ctx.update({"B": B, "P": P, "pair_rows": pair_rows, "groups": groups, "gobs": gobs,
                "junction": junction, "episodes": episodes})
    res = validate_and_activate(ch, ctx)
    rss = res["performance"]["peak_rss_mb"]
    print(f"ACTIVE build_run_id={ctx['build_run_id']} hash={res['content_hash']} "
          f"runtime={res['performance']['elapsed_s']}s rss={rss}MB "
          f"(total wall {time.perf_counter() - t0:.1f}s)")
    state = {"build_run_id": ctx["build_run_id"], "content_hash": res["content_hash"],
             "performance": res["performance"],
             "pair_stats": {k: dict(v) if isinstance(v, defaultdict) else v for k, v in pair_stats.items()},
             "ap_stats_by_ctx": ap_stats_by_ctx,
             "counts": {"pair_rows": len(pair_rows), "episodes": len(episodes),
                        "groups": len(groups), "group_obs": len(gobs), "junction": len(junction)},
             "phase0_parameters": P,
             "pins": {"projection_run": B["pin_run"], "ownership_generation_pin": B["own_gen_pin"],
                      "phase0_measurement_id": B["phase0_id"], "phase0_measured_at": B["p0_at"],
                      "phase0_artifact_path": B["phase0_artifact_path"]}}
    fn = f"tools/phase3_build_state{tag}.json"
    json.dump(state, open(fn, "w"), indent=1, default=str)
    print(f"WROTE {fn}")
    return state


if __name__ == "__main__":
    args = sys.argv[1:]
    run_build(args[0] if args and not args[0].startswith("-") else "",
              dry="--dry" in args)
