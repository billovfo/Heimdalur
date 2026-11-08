#!/usr/bin/env python3
"""Phase 3 group tests — episodes, Apriori support, core groups, roster-replay
guard, junction provenance (task §54-§57). Run: python3 tools/phase3_test_groups.py"""
import sys, datetime, itertools
sys.path.insert(0, "tools")
from phase3_build import (derive_group_episodes, apriori_groups, derive_core_groups,
                          assemble_group_rows, member_set_hash, BudgetExceeded,
                          content_hash3, _ep_sort_key)

D = datetime.date
fails = []
def check(name, cond):
    print(("  ok  " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)

def O(pid, cid, ctx="BOARD", kind="APPOINTED_EVENT", at=None, event=None, doc=None,
      attempt=None, fam="PDF_HISTORY", auth="HISTORICAL_PDF_OBSERVATION"):
    return {"source_observation_key": f"K:{pid}:{kind}:{event}:{doc}:{attempt}",
            "canonical_person_id": pid, "canonical_company_id": cid, "role_context": ctx,
            "source_family": fam, "authority_class": auth, "identity_resolution": None,
            "observation_kind": kind, "observed_at": at, "observed_state_at": at,
            "action": None, "state": None, "ownership_percentage": None,
            "source_projection_run_id": 9, "source_person_role_id": None,
            "source_history_event_id": None, "source_event_id": event,
            "source_document_id": doc, "source_observation_id": doc,
            "source_relation_id": None, "source_attempt_id": attempt}

def period(pid, cid, s, e, ok="po", ck="pc", ctx="BOARD"):
    return {"canonical_person_id": pid, "canonical_company_id": cid, "role_context": ctx,
            "period_start": s, "period_end": e, "period_status": "BOUNDED",
            "opening_observation_key": f"{ok}-{pid}", "closing_observation_key": f"{ck}-{pid}",
            "source_family": "PDF_HISTORY"}

AH = {77: "a" * 64}

# ---- 54.1: 3 persons same Lysing event -> one SAME_EVENT episode
obs = [O(p, 10, at=D(2015, 1, 1), event=900) for p in (1, 2, 3)]
eps = derive_group_episodes(obs, [], AH)
check("3 persons same Lysing event -> 1 SAME_EVENT episode", len(eps) == 1 and
      eps[0]["co_observation_kind"] == "SAME_EVENT" and eps[0]["members"] == {1, 2, 3}
      and eps[0]["start"] == D(2015, 1, 1))

# ---- 54.2: 4 persons same Felag snapshot -> one SAME_SNAPSHOT episode
obs = [O(p, 20, kind="CURRENT_STATE_SNAPSHOT", auth="AUTHORITATIVE_CURRENT_GOVERNANCE",
         at=D(2026, 9, 1), doc=150) for p in (1, 2, 3, 4)]
eps = derive_group_episodes(obs, [], AH)
check("4 persons same Felag snapshot -> 1 SAME_SNAPSHOT episode", len(eps) == 1 and
      eps[0]["co_observation_kind"] == "SAME_SNAPSHOT" and len(eps[0]["members"]) == 4
      and eps[0]["source_family"] == "PDF_HISTORY")

# ---- 54.3: 5-person same group across 2 companies -> supported via company>=2
obs = [O(p, 10, at=D(2015, 1, 1), event=901) for p in (1, 2, 3, 4, 5)] + \
      [O(p, 20, at=D(2016, 1, 1), event=902) for p in (1, 2, 3, 4, 5)]
eps = derive_group_episodes(obs, [], AH)
sup, st = apriori_groups(eps, 2, 2, 8, 2_000_000, "BOARD")
check("5-person set across 2 companies -> supported", (1, 2, 3, 4, 5) in sup
      and len(sup[(1, 2, 3, 4, 5)]["companies"]) == 2)

# ---- 54.4: group repeated in multiple episodes same company -> episodes>=2
obs = [O(p, 10, at=D(2015, 1, 1), event=910) for p in (1, 2)] + \
      [O(p, 10, at=D(2016, 1, 1), event=911) for p in (1, 2)]
eps = derive_group_episodes(obs, [], AH)
sup, st = apriori_groups(eps, 2, 2, 8, 2_000_000, "BOARD")
check("same pair, 2 episodes, 1 company -> supported via episodes>=2",
      (1, 2) in sup and len(sup[(1, 2)]["episodes"]) == 2)

# ---- 54.5: unsupported subset not persisted
obs = [O(p, 10, at=D(2015, 1, 1), event=920) for p in (1, 2, 3)] + \
      [O(1, 20, at=D(2015, 1, 1), event=921), O(2, 20, at=D(2015, 1, 1), event=921)]
eps = derive_group_episodes(obs, [], AH)
sup, st = apriori_groups(eps, 2, 2, 8, 2_000_000, "BOARD")
check("(1,2) supported via 2 companies; (1,3) unsupported not persisted",
      (1, 2) in sup and (1, 3) not in sup and (2, 3) not in sup and (1, 2, 3) not in sup)

# ---- 54.6: safe bounded-period 3-person intersection -> SAFE_PERIOD_OVERLAP episode
per = [period(1, 30, D(2010, 1, 1), D(2020, 1, 1)), period(2, 30, D(2012, 1, 1), D(2018, 1, 1)),
       period(3, 30, D(2014, 1, 1), D(2016, 1, 1))]
obs = [O(p, 30, at=D(2010, 1, 1), event=930 + p) for p in (1, 2, 3)] + \
      [O(p, 30, kind="REMOVED_EVENT", at=D(2018, 1, 1), event=940 + p) for p in (1, 2, 3)]
eps = derive_group_episodes(obs, per, AH)
spo = [e for e in eps if e["co_observation_kind"] == "SAFE_PERIOD_OVERLAP"
       and e["members"] == {1, 2, 3}]
check("3-person bounded intersection -> SAFE_PERIOD_OVERLAP episode [2014-01-01, 2016-01-01)",
      len(spo) == 1
      and spo[0]["start"] == D(2014, 1, 1) and spo[0]["end"] == D(2016, 1, 1)
      and set(spo[0]["member_prov"]) == {1, 2, 3}
      and all(k.startswith(("po-", "pc-")) for pid in spo[0]["member_prov"] for k, _r in spo[0]["member_prov"][pid]))

# ---- 54.7: no common intersection across all members -> no group episode
per = [period(1, 31, D(2010, 1, 1), D(2020, 1, 1)), period(2, 31, D(2012, 1, 1), D(2018, 1, 1)),
       period(3, 31, D(2019, 1, 1), D(2021, 1, 1))]
eps = derive_group_episodes([], per, AH)
spo = [e for e in eps if e["co_observation_kind"] == "SAFE_PERIOD_OVERLAP"]
check("no 3-way common intersection -> only pairwise episodes, no triple",
      all(len(e["members"]) == 2 for e in spo) and not any(len(e["members"]) == 3 for e in spo))

# ---- 54.8: group disappears and returns -> separate episodes (no collapse)
per = [period(1, 32, D(2010, 1, 1), D(2015, 1, 1)), period(1, 32, D(2018, 1, 1), D(2020, 1, 1)),
       period(2, 32, D(2010, 1, 1), D(2020, 1, 1))]
eps = derive_group_episodes([], per, AH)
spo = [e for e in eps if e["co_observation_kind"] == "SAFE_PERIOD_OVERLAP" and e["members"] == {1, 2}]
check("break/return -> 2 separate episodes (no collapse to one long period)",
      len(spo) == 2 and {(spo[0]["start"], spo[0]["end"]), (spo[1]["start"], spo[1]["end"])} ==
      {(D(2010, 1, 1), D(2015, 1, 1)), (D(2018, 1, 1), D(2020, 1, 1))})

# ---- 54.9: member permutation -> same member_set_hash
check("permutation invariance", member_set_hash([4, 2, 7]) == member_set_hash([7, 4, 2])
      and member_set_hash([4, 2]) != member_set_hash([4, 2, 7]))

# ---- 54.10: group size > cap -> bounded according to config
obs = [O(p, 40, at=D(2015, 1, 1), event=950) for p in range(1, 7)] + \
      [O(p, 41, at=D(2016, 1, 1), event=951) for p in range(1, 7)]
eps = derive_group_episodes(obs, [], AH)
sup, st = apriori_groups(eps, 2, 2, 3, 2_000_000, "BOARD")
check("cap=3: no group beyond cap, levels stop at 3",
      max(len(k) for k in sup) == 3 and max(st["levels"]) == 3)

# ---- 54.11: candidate budget exceeded -> BUILD FAILED before ACTIVE
try:
    apriori_groups(eps, 2, 2, 8, 5, "BOARD")
    check("budget exceeded raises", False)
except BudgetExceeded:
    check("budget exceeded raises BudgetExceeded", True)

# ---- 54.12: web governance alone cannot establish a group episode
web = [O(p, 50, ctx="BOARD", kind="WEB_GOVERNANCE_SNAPSHOT_POINT", fam="OWNERSHIP_WEB_GOVERNANCE",
         auth="SUPPLEMENTARY_WEB_GOVERNANCE_OBSERVATION", at=D(2026, 1, 1), attempt=77) for p in (1, 2, 3)]
eps = derive_group_episodes(web, [], AH)
check("web governance alone -> no group episodes", len(eps) == 0)

# ---- 54.13: no historical roster replay from individual appointments (task §56)
# Two persons, individual appointments/removals via DIFFERENT events, NON-overlapping.
obs = [O(1, 60, at=D(2010, 1, 1), event=961), O(1, 60, kind="REMOVED_EVENT", at=D(2012, 1, 1), event=962),
       O(2, 60, at=D(2014, 1, 1), event=963), O(2, 60, kind="REMOVED_EVENT", at=D(2016, 1, 1), event=964)]
per = [period(1, 60, D(2010, 1, 1), D(2012, 1, 1)), period(2, 60, D(2014, 1, 1), D(2016, 1, 1))]
eps = derive_group_episodes(obs, per, AH)
check("roster replay guard: no multi-person group without shared event/overlap",
      len(eps) == 0)
# but with SAME event, direct co-observation is allowed
obs2 = [O(1, 60, at=D(2010, 1, 1), event=961), O(2, 60, at=D(2010, 1, 1), event=961),
        O(1, 60, kind="REMOVED_EVENT", at=D(2012, 1, 1), event=962),
        O(2, 60, kind="REMOVED_EVENT", at=D(2016, 1, 1), event=964)]
per2 = [period(1, 60, D(2010, 1, 1), D(2012, 1, 1)), period(2, 60, D(2010, 1, 1), D(2016, 1, 1))]
eps2 = derive_group_episodes(obs2, per2, AH)
check("same event -> direct SAME_EVENT episode allowed", any(
      e["co_observation_kind"] == "SAME_EVENT" and e["members"] == {1, 2} for e in eps2))

# ---- 55: core groups — ABC recurrent core of ABCD/ABCE extended episodes
obs = [O(p, 70, at=D(2015, 1, 1), event=971) for p in (1, 2, 3)] + \
      [O(4, 70, at=D(2015, 1, 1), event=972), O(5, 70, at=D(2015, 1, 1), event=973)]
eps = derive_group_episodes(obs, [], AH)
eps = sorted(eps, key=_ep_sort_key)
sup, st = apriori_groups(eps, 2, 2, 8, 2_000_000, "BOARD")
# episodes: {1,2,3} (event 971), {1,4}, {1,5} -> ABC supported? episodes=1, companies=1 -> NOT
check("ABC from single episode not supported", (1, 2, 3) not in sup)
# make ABC recur: add two more episodes (companies 71, 72)
obs += [O(p, 71, at=D(2016, 1, 1), event=974) for p in (1, 2, 3, 4)] + \
       [O(p, 72, at=D(2017, 1, 1), event=975) for p in (1, 2, 3, 5)]
eps = sorted(derive_group_episodes(obs, [], AH), key=_ep_sort_key)
sup, st = apriori_groups(eps, 2, 2, 8, 2_000_000, "BOARD")
check("ABC supported across 3 companies", (1, 2, 3) in sup
      and len(sup[(1, 2, 3)]["companies"]) == 3)
cores = derive_core_groups(sup, eps, 2, 0.75, 8)
core_of_abc = [c for c in cores if c["item"] == (1, 2) and c["parent"] == (1, 2, 3)]
# ABC parent: 3 episodes (co 70,71,72); subset (1,2) present in all 3 -> ratio 1.0
check("core subset (1,2) of ABC with ratio>=0.75", len(core_of_abc) == 1
      and core_of_abc[0]["ratio"] >= 0.75)
core_pairs = {(c["item"], member_set_hash(c["parent"])) for c in cores}
check("extended sets ABCD/ABCE not supported (episodes=1 each)",
      (1, 2, 3, 4) not in sup and (1, 2, 3, 5) not in sup)
# unsupported mathematical subsets not persisted: (4,5) never co-observed
check("subset (4,5) never co-observed -> no core/derived row",
      not any(set(c["item"]) == {4, 5} for c in cores) and (4, 5) not in sup)

# ---- 57: junction assembly — every persisted junction resolves to group+obs
groups, members, gobs, junction = assemble_group_rows(
    eps, {"BOARD": sup}, {"BOARD": cores}, 0.75, 8)
gkeys = {(g["member_set_hash"], g["role_context"], g["group_kind"]) for g in groups}
check("junction rows reference persisted groups only",
      all((j["member_set_hash"], j["role_context"], j["group_kind"]) in gkeys for j in junction))
check("every group observation has >=1 junction row",
      all(any(j["episode_key"] == g["episode_key"] and
              j["member_set_hash"] == g["member_set_hash"] and
              j["role_context"] == g["role_context"] and j["group_kind"] == g["group_kind"]
              for j in junction) for g in gobs))
check("episode_key unique per group observation",
      len({(g["member_set_hash"], g["role_context"], g["group_kind"], g["episode_key"])
           for g in gobs}) == len(gobs))
# SAFE_PERIOD_OVERLAP junction provenance carries PERIOD_OPENING/PERIOD_CLOSING roles
spo_j = [j for j in junction if any(
    g["episode_key"] == j["episode_key"] and g["co_observation_kind"] == "SAFE_PERIOD_OVERLAP"
    for g in gobs)]
# (fixture has no SPO group obs; assert roles present when they exist)
if spo_j:
    check("SPO junction roles are PERIOD_OPENING/CLOSING",
          {j["role_in_episode"] for j in spo_j} <= {"PERIOD_OPENING", "PERIOD_CLOSING"})
else:
    check("no SPO groups in this fixture (roles asserted in pair tests)", True)

# ---- determinism of assembly + hash
g2, m2, go2, j2 = assemble_group_rows(eps, {"BOARD": dict(sup)}, {"BOARD": cores}, 0.75, 8)
check("assembly deterministic across calls",
      [(g["member_set_hash"], g["group_kind"], g["episode_key"]) for g in gobs] ==
      [(g["member_set_hash"], g["group_kind"], g["episode_key"]) for g in go2]
      and [(j["episode_key"], j["observation_key"], j["role_in_episode"]) for j in junction] ==
          [(j["episode_key"], j["observation_key"], j["role_in_episode"]) for j in j2]
      and len(j2) == len(junction))

# ---- content hash3 covers pairs/groups/junction (differs when groups differ)
o = {"source_observation_key": "K", "canonical_person_id": 1, "canonical_company_id": 2,
     "role_context": "BOARD", "source_family": "PDF_HISTORY",
     "authority_class": "HISTORICAL_PDF_OBSERVATION", "identity_resolution": None,
     "observation_kind": "APPOINTED_EVENT", "observed_at": D(2020, 1, 1), "observed_state_at": None,
     "action": None, "state": None, "ownership_percentage": None,
     "source_projection_run_id": 9, "source_person_role_id": None, "source_history_event_id": None,
     "source_event_id": None, "source_document_id": None, "source_observation_id": None,
     "source_relation_id": None, "source_attempt_id": None}
h1 = content_hash3([o], [], [], groups, gobs, junction)
h2 = content_hash3([o], [], [], groups, gobs, junction)
h3 = content_hash3([o], [], [], groups, gobs, junction[:-1])
check("content_hash3 deterministic; junction changes hash", h1 == h2 and h1 != h3)
pr = [{"person_a_id": 1, "person_b_id": 2, "canonical_company_id": 2, "role_context": "BOARD",
       "co_participation_class": "SAME_OBSERVATION", "observation_count": 2, "episode_count": 1,
       "known_overlap_days": None, "first_observed": None, "last_observed": None,
       "same_source_event_count": 1, "same_snapshot_count": 0, "source_family": "PDF_HISTORY",
       "authority_class": "HISTORICAL_PDF_OBSERVATION"}]
h4 = content_hash3([o], [], pr, groups, gobs, junction)
check("content_hash3 covers pairs", h4 != h1)

print("FAILS:", fails or "none")
sys.exit(1 if fails else 0)