#!/usr/bin/env python3
"""Phase 3 targeted closure tests (M-01/M-02/m-01 closure task §11, §21-§24).

Cross-kind support semantics, heterogeneous fallback metadata representation,
episode dedupe identity. Run: python3 tools/phase3_test_closure.py"""
import sys, datetime
from collections import Counter
sys.path.insert(0, "tools")
from phase3_build import (derive_group_episodes, apriori_groups, derive_core_groups,
                          assemble_group_rows, derive_pairs, classify_pair_evidence,
                          member_set_hash, _ep_sort_key)

D = datetime.date
fails = []
def check(name, cond, detail=""):
    print(("  ok  " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
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


def survive(eps, item, t_company=2, t_episode=2, cap=8):
    """Support of `item` over ALL episodes (cross-kind, per role_context) with the
    binding persistence rule companies>=t_company OR episodes>=t_episode."""
    hit = [e for e in eps if set(item) <= set(e["members"])]
    comps = {e["canonical_company_id"] for e in hit}
    return hit, comps, len(comps) >= t_company or len(hit) >= t_episode


# ==== §21 — ABC: 1 SAME_SNAPSHOT @X + 1 SAME_EVENT @X; no kind reaches 2 alone ====
snap_obs = [O(p, 20, kind="CURRENT_STATE_SNAPSHOT", auth="AUTHORITATIVE_CURRENT_GOVERNANCE",
              at=D(2026, 9, 1), doc=500) for p in (1, 2, 3)]
evt_obs = [O(p, 20, at=D(2015, 6, 1), event=921) for p in (1, 2, 3)]
eps = derive_group_episodes(snap_obs + evt_obs, [], AH)
kinds = Counter(e["co_observation_kind"] for e in eps)
check("§21 fixture produces 1 SAME_SNAPSHOT + 1 SAME_EVENT episode for ABC",
      len(eps) == 2 and kinds == Counter({"SAME_SNAPSHOT": 1, "SAME_EVENT": 1}))
hit, comps, ok = survive(eps, (1, 2, 3), t_episode=2)
per_kind = Counter(e["co_observation_kind"] for e in hit)
check("§21 combined support_episode_count=2 -> ABC survives (episode_threshold=2)",
      ok and len(hit) == 2)
check("§21 no individual episode-kind reaches support 2 (only combined aggregation saves it)",
      all(c < 2 for c in per_kind.values()) and sum(per_kind.values()) == 2)

# ==== §22 — ABCD: 1 SAME_EVENT + 1 SAFE_PERIOD_OVERLAP ====
abcd_evt = [O(p, 30, at=D(2015, 6, 1), event=930) for p in (1, 2, 3, 4)]
abcd_per = [period(p, 30, D(2018, 1, 1), D(2020, 1, 1), ok=f"o{p}", ck=f"c{p}")
            for p in (1, 2, 3, 4)]
abcd_open = [O(p, 30, at=D(2018, 1, 1), event=940 + p) for p in (1, 2, 3, 4)]
abcd_close = [O(p, 30, at=D(2020, 1, 1), event=950 + p) for p in (1, 2, 3, 4)]
eps = derive_group_episodes(abcd_evt + abcd_open + abcd_close, abcd_per, AH)
kinds = Counter(e["co_observation_kind"] for e in eps if {1, 2, 3, 4} <= set(e["members"]))
check("§22 fixture: ABCD has 1 SAME_EVENT + 1 SAFE_PERIOD_OVERLAP episode",
      kinds == Counter({"SAME_EVENT": 1, "SAFE_PERIOD_OVERLAP": 1}), str(kinds))
hit, comps, ok = survive(eps, (1, 2, 3, 4), t_episode=2)
check("§22 combined support_episode_count=2 -> ABCD not pruned", ok and len(hit) == 2)

# ==== §23 — company support across kinds: SNAPSHOT @company1 + EVENT @company2 ====
s_obs = [O(p, 40, kind="CURRENT_STATE_SNAPSHOT", auth="AUTHORITATIVE_CURRENT_GOVERNANCE",
           at=D(2026, 9, 1), doc=600) for p in (1, 2, 3)]
e_obs = [O(p, 41, at=D(2016, 3, 1), event=960) for p in (1, 2, 3)]
eps = derive_group_episodes(s_obs + e_obs, [], AH)
hit, comps, ok = survive(eps, (1, 2, 3), t_company=2)
check("§23 cross-kind company support: support_company_count=2 (kinds differ)",
      ok and comps == {40, 41} and len(hit) == 2)

# ==== §24 — NO false cross-context support ====
b_obs = [O(p, 50, ctx="BOARD", at=D(2015, 6, 1), event=970) for p in (1, 2, 3)]
m_obs = [O(p, 50, ctx="MANAGEMENT", at=D(2015, 6, 1), event=971) for p in (1, 2, 3)]
eps = derive_group_episodes(b_obs + m_obs, [], AH)
board_eps = [e for e in eps if e["role_context"] == "BOARD"]
mgmt_eps = [e for e in eps if e["role_context"] == "MANAGEMENT"]
_, _, ok_board = survive(board_eps, (1, 2, 3), t_episode=2)
_, _, ok_mgmt = survive(mgmt_eps, (1, 2, 3), t_episode=2)
check("§24 contexts separate: BOARD=1, MANAGEMENT=1, no combined support 2",
      len(board_eps) == 1 and len(mgmt_eps) == 1 and not ok_board and not ok_mgmt)
sup_b, st_b = apriori_groups(board_eps, 2, 2, 8, 100000, "BOARD")
sup_m, st_m = apriori_groups(mgmt_eps, 2, 2, 8, 100000, "MANAGEMENT")
check("§24 Apriori per context: ABC unsupported in both (no cross-context leak)",
      (1, 2, 3) not in sup_b and (1, 2, 3) not in sup_m)
g, m, go, j = assemble_group_rows(sorted(eps, key=_ep_sort_key),
                                  {"BOARD": sup_b, "MANAGEMENT": sup_m}, {}, 0.75, 8)
check("§24 no group rows persisted for cross-context candidate",
      len(g) == 0 and len(go) == 0)

# ==== §15 — deterministic episode dedupe key: exact duplicates collapse once,
# distinct evidence identities stay separate episodes ====
dup = [O(p, 60, at=D(2015, 6, 1), event=980) for p in (1, 2)]
eps = derive_group_episodes(dup + dup, [], AH)  # same facts twice
check("§15 exact-duplicate episodes collapse to one (dedupe key: company+ctx+kind+"
      "interval+member set)", len(eps) == 1)
snap2 = [O(p, 60, kind="CURRENT_STATE_SNAPSHOT", auth="AUTHORITATIVE_CURRENT_GOVERNANCE",
           at=D(2026, 9, 1), doc=700) for p in (1, 2)]
eps = derive_group_episodes(dup + snap2, [], AH)
check("§15 distinct mechanisms stay distinct episode identities (kind in key)",
      len(eps) == 2 and {e["co_observation_kind"] for e in eps} == {"SAME_EVENT", "SAME_SNAPSHOT"})

# ==== §9/§7 — heterogeneous fallback metadata representation (post-fix contract) ====
# PDF current snapshot (AUTHORITATIVE_CURRENT_GOVERNANCE) + historical event
# (HISTORICAL_PDF_OBSERVATION): single family, two authorities.
m1 = O(1, 6, at=D(2020, 1, 1), event=41)
m2 = O(2, 6, kind="CURRENT_STATE_SNAPSHOT", auth="AUTHORITATIVE_CURRENT_GOVERNANCE",
       at=D(2026, 9, 1), doc=42)
rows, stats = derive_pairs([m1, m2], [], AH)
fb = [r for r in rows if r["co_participation_class"] == "SAME_COMPANY_EVER"]
check("mixed-authority fallback fact preserved (no skip, no MIXED enum)",
      len(fb) == 1 and stats["mixed_authority_fallback_skipped"] == 0
      and not any(r["co_participation_class"] == "MIXED" for r in rows))
check("heterogeneous authority -> authority_class NULL, single family kept",
      fb and fb[0]["authority_class"] is None and fb[0]["source_family"] == "PDF_HISTORY")
# heterogeneous FAMILY -> source_family NULL too
o1 = O(1, 7, at=D(2020, 1, 1), event=43)
o2 = O(2, 7, kind="OWNERSHIP_SNAPSHOT_POINT", fam="OWNERSHIP_GRAPH",
       auth="OWNERSHIP_POINT_OBSERVATION", ctx="BOARD", at=D(2026, 1, 1), attempt=77)
rows, stats = derive_pairs([o1, o2], [], AH)
fb = [r for r in rows if r["co_participation_class"] in ("SAME_COMPANY_EVER",
                                                          "UNKNOWN_TEMPORAL_RELATION")]
check("heterogeneous family AND authority -> both metadata NULL, class unchanged",
      fb and all(r["source_family"] is None and r["authority_class"] is None for r in fb)
      and stats["mixed_authority_fallback_skipped"] == 0)
# direct evidence supersedes fallback (truth table) and keeps precise metadata
rows, _sk = derive_pairs([o1, o2, O(2, 7, at=D(2020, 1, 1), event=43)], [], AH)
direct = [r for r in rows if r["co_participation_class"] == "SAME_OBSERVATION"]
fb2 = [r for r in rows if r["co_participation_class"] in ("SAME_COMPANY_EVER",
                                                           "UNKNOWN_TEMPORAL_RELATION")]
check("direct SAME_OBSERVATION row retains precise family/authority (supersedes fallback)",
      len(direct) == 1 and direct[0]["source_family"] == "PDF_HISTORY"
      and direct[0]["authority_class"] == "HISTORICAL_PDF_OBSERVATION" and not fb2)

# ==== §11 — heterogeneous fallback provenance mechanically reconstructible ====
# (person 1: event 901 + snapshot doc 902; person 2: event 903 + snapshot doc 904 —
#  no shared identity -> fallback row; heterogeneous authority -> NULL authority_class)
obs_a = [O(1, 90, at=D(2019, 1, 1), event=901), O(1, 90, kind="CURRENT_STATE_SNAPSHOT",
         auth="AUTHORITATIVE_CURRENT_GOVERNANCE", at=D(2026, 9, 1), doc=902)]
obs_b = [O(2, 90, at=D(2019, 1, 1), event=903), O(2, 90, kind="CURRENT_STATE_SNAPSHOT",
         auth="AUTHORITATIVE_CURRENT_GOVERNANCE", at=D(2026, 9, 1), doc=904)]
rows_raw, _ = classify_pair_evidence(obs_a, obs_b, {"bounded": [], "left_censored": False},
                                     {"bounded": [], "left_censored": False}, AH)
fb = [r for r in rows_raw if r["co_participation_class"] in ("UNKNOWN_TEMPORAL_RELATION",
                                                              "SAME_COMPANY_EVER")]
check("§11 fallback evidence_keys = same-build observation keys (reconstructible)",
      len(fb) == 1 and fb[0]["evidence_keys"] ==
      sorted(o["source_observation_key"] for o in obs_a + obs_b)
      and fb[0]["source_family"] == "PDF_HISTORY" and fb[0]["authority_class"] is None)
rows, stats2 = derive_pairs(obs_a + obs_b, [], AH)
prow = [r for r in rows if r["co_participation_class"] in ("UNKNOWN_TEMPORAL_RELATION",
                                                            "SAME_COMPANY_EVER")]
check("§11 persisted fallback row reproduces classifier facts without stored arrays",
      len(prow) == 1 and prow[0]["observation_count"] == 4
      and prow[0]["source_family"] == "PDF_HISTORY" and prow[0]["authority_class"] is None
      and not any(str(k).startswith("shared_") for k in prow[0]))

# ==== §20 — cores from cross-kind support ====
evt2 = [O(p, 21, at=D(2016, 6, 1), event=922) for p in (1, 2, 3)]
eps = derive_group_episodes(snap_obs + evt_obs + evt2, [], AH)
eps_sorted = sorted(eps, key=_ep_sort_key)
sup, st = apriori_groups(eps_sorted, 2, 2, 8, 100000, "BOARD")
check("§20 Apriori survivor uses combined support (ABC: 3 episodes, 2 companies)",
      (1, 2, 3) in sup and len(sup[(1, 2, 3)]["episodes"]) == 3
      and len(sup[(1, 2, 3)]["companies"]) == 2)
cores = derive_core_groups(sup, eps_sorted, 2, 0.75, 8)
check("§20 core from cross-kind parent: all 2-subsets ratio 1.0 over 3 episodes",
      len(cores) == 3 and all(c["ratio"] == 1.0 and len(c["core_episodes"]) == 3 for c in cores))
g, m, go, j = assemble_group_rows(eps_sorted, {"BOARD": sup}, {"BOARD": cores}, 0.75, 8)
core_rows = [x for x in g if x["group_kind"] == "CORE_GROUP"]
check("§20 persisted CORE groups carry cross-kind episode observations",
      len(core_rows) == 3 and all(x["support"]["core_episode_count"] == 3 for x in core_rows)
      and all(set(x["support"]["core_of_member_set_hashes"]) ==
              {member_set_hash([1, 2, 3])} for x in core_rows))

print("FAILS:", fails or "none")
sys.exit(1 if fails else 0)