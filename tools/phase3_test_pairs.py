#!/usr/bin/env python3
"""Phase 3 pair tests — central classifier truth-table conformance (task §53).
Run: python3 tools/phase3_test_pairs.py"""
import sys, datetime
sys.path.insert(0, "tools")
from phase3_build import (classify_pair_evidence, derive_pairs, member_set_hash,
                          intersect_days, _period_view)

D = datetime.date
fails = []
def check(name, cond):
    print(("  ok  " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)

def O(pid, cid, ctx="BOARD", kind="APPOINTED_EVENT", fam="PDF_HISTORY",
      auth="HISTORICAL_PDF_OBSERVATION", at=None, event=None, doc=None, attempt=None,
      rel=None):
    return {"source_observation_key": key(pid, kind, event, doc, attempt),
            "canonical_person_id": pid, "canonical_company_id": cid, "role_context": ctx,
            "source_family": fam, "authority_class": auth, "identity_resolution": None,
            "observation_kind": kind, "observed_at": at, "observed_state_at": at,
            "action": None, "state": None, "ownership_percentage": None,
            "source_projection_run_id": 9 if fam == "PDF_HISTORY" else None,
            "source_person_role_id": None, "source_history_event_id": None,
            "source_event_id": event, "source_document_id": doc, "source_observation_id": doc,
            "source_relation_id": rel, "source_attempt_id": attempt}

def key(pid, kind, event, doc, attempt):
    return f"K:{pid}:{kind}:{event}:{doc}:{attempt}"

def PER(s, e, ok="k-o", ck="k-c"):
    return (s, e, ok, ck)

EMPTY = {"bounded": [], "left_censored": False}
def per_bounded(pairs):
    return {"bounded": list(pairs), "left_censored": False}
def per_lc():
    return {"bounded": [], "left_censored": True}

AH = {77: "a" * 64, 88: "b" * 64, 78: "c" * 64}

def classes(rows):
    return {r["co_participation_class"] for r in rows}

# 1 ---- A/B ordering always min/max
o1, o2 = O(10, 1, at=D(2020, 1, 1), event=1), O(5, 1, at=D(2020, 1, 1), event=1)
rows, _sk = derive_pairs([o1, o2], [], AH)
check("A/B ordering min/max", rows and all(r["person_a_id"] < r["person_b_id"] for r in rows)
      and rows[0]["person_a_id"] == 5 and rows[0]["person_b_id"] == 10)
check("ordering gives one class row (same event)", len(rows) == 1
      and rows[0]["co_participation_class"] == "SAME_OBSERVATION")

# 2 ---- same Felag snapshot -> SAME_SNAPSHOT
s1 = O(1, 2, kind="CURRENT_STATE_SNAPSHOT", auth="AUTHORITATIVE_CURRENT_GOVERNANCE",
       at=D(2026, 9, 1), doc=150)
s2 = O(2, 2, kind="CURRENT_STATE_SNAPSHOT", auth="AUTHORITATIVE_CURRENT_GOVERNANCE",
       at=D(2026, 9, 1), doc=150)
rows, _sk = derive_pairs([s1, s2], [], AH)
check("same Felag snapshot -> SAME_SNAPSHOT", len(rows) == 1 and
      rows[0]["co_participation_class"] == "SAME_SNAPSHOT" and rows[0]["same_snapshot_count"] == 1
      and rows[0]["known_overlap_days"] is None)
# different snapshot identity -> no SAME_SNAPSHOT
s3 = O(3, 2, kind="CURRENT_STATE_SNAPSHOT", auth="AUTHORITATIVE_CURRENT_GOVERNANCE",
       at=D(2026, 9, 1), doc=999)
rows, _sk = derive_pairs([s1, s3], [], AH)
check("different snapshot identity -> no SAME_SNAPSHOT (case 6: UNKNOWN + SAME_COMPANY_EVER)",
      {r["co_participation_class"] for r in rows} ==
      {"UNKNOWN_TEMPORAL_RELATION", "SAME_COMPANY_EVER"})

# 3 ---- same Lysing event -> direct same-event co-observation
rows, _sk = derive_pairs([o1, o2], [], AH)
check("same Lysing event -> SAME_OBSERVATION", rows[0]["co_participation_class"] == "SAME_OBSERVATION"
      and rows[0]["same_source_event_count"] == 1 and rows[0]["episode_count"] == 1)

# 4 ---- same ownership attempt + resolved response hash -> SAME_OBSERVATION
w1 = O(1, 3, ctx="OWNERSHIP", kind="OWNERSHIP_SNAPSHOT_POINT", fam="OWNERSHIP_GRAPH",
       auth="OWNERSHIP_POINT_OBSERVATION", at=D(2025, 5, 1), attempt=77, rel=1)
w2 = O(2, 3, ctx="OWNERSHIP", kind="OWNERSHIP_SNAPSHOT_POINT", fam="OWNERSHIP_GRAPH",
       auth="OWNERSHIP_POINT_OBSERVATION", at=D(2025, 5, 1), attempt=77, rel=2)
rows, _sk = derive_pairs([w1, w2], [], AH)
check("same ownership attempt+hash -> SAME_OBSERVATION (single direct row, no fallback)",
      len(rows) == 1 and rows[0]["co_participation_class"] == "SAME_OBSERVATION"
      and rows[0]["source_family"] == "OWNERSHIP_GRAPH"
      and rows[0]["known_overlap_days"] is None)
w3b = O(3, 3, ctx="OWNERSHIP", kind="OWNERSHIP_SNAPSHOT_POINT", fam="OWNERSHIP_GRAPH",
        auth="OWNERSHIP_POINT_OBSERVATION", at=D(2026, 2, 1), attempt=88, rel=3)
rows_c6, _sk = derive_pairs([w1, w3b], [], AH)
check("ownership point-pair different attempts -> UNKNOWN + SAME_COMPANY_EVER (case 6)",
      {r["co_participation_class"] for r in rows_c6} ==
      {"UNKNOWN_TEMPORAL_RELATION", "SAME_COMPANY_EVER"})

# 5 ---- different ownership responses -> no false same-observation
w3 = O(3, 3, ctx="OWNERSHIP", kind="OWNERSHIP_SNAPSHOT_POINT", fam="OWNERSHIP_GRAPH",
       auth="OWNERSHIP_POINT_OBSERVATION", at=D(2026, 2, 1), attempt=88, rel=3)
rows, _sk = derive_pairs([w1, w3], [], AH)
check("different ownership responses -> no SAME_OBSERVATION",
      all(r["co_participation_class"] != "SAME_OBSERVATION" for r in rows))
# unresolved response hash (attempt without hash) -> cannot assert co-observation
rows, _sk = derive_pairs([w1, O(4, 3, ctx="OWNERSHIP", kind="OWNERSHIP_SNAPSHOT_POINT",
      fam="OWNERSHIP_GRAPH", auth="OWNERSHIP_POINT_OBSERVATION", at=D(2025, 5, 1), attempt=99)], [], AH)
check("attempt without resolved hash -> no SAME_OBSERVATION",
      all(r["co_participation_class"] != "SAME_OBSERVATION" for r in rows))

# 6 ---- bounded DAY overlap -> KNOWN_TEMPORAL_OVERLAP with correct days
p_a = O(1, 4, at=D(2010, 1, 1), event=11)
p_b = O(2, 4, at=D(2012, 1, 1), event=12)
c_a = O(1, 4, kind="REMOVED_EVENT", at=D(2020, 1, 1), event=13)
c_b = O(2, 4, kind="REMOVED_EVENT", at=D(2018, 6, 1), event=14)
periods = [
    {"canonical_person_id": 1, "canonical_company_id": 4, "role_context": "BOARD",
     "period_start": D(2010, 1, 1), "period_end": D(2020, 1, 1), "period_status": "BOUNDED",
     "opening_observation_key": key(1, "APPOINTED_EVENT", 11, None, None),
     "closing_observation_key": key(1, "REMOVED_EVENT", 13, None, None), "source_family": "PDF_HISTORY"},
    {"canonical_person_id": 2, "canonical_company_id": 4, "role_context": "BOARD",
     "period_start": D(2012, 1, 1), "period_end": D(2018, 6, 1), "period_status": "BOUNDED",
     "opening_observation_key": key(2, "APPOINTED_EVENT", 12, None, None),
     "closing_observation_key": key(2, "REMOVED_EVENT", 14, None, None), "source_family": "PDF_HISTORY"},
]
rows, _sk = derive_pairs([p_a, p_b, c_a, c_b], periods, AH)
k = [r for r in rows if r["co_participation_class"] == "KNOWN_TEMPORAL_OVERLAP"]
check("bounded DAY overlap -> KNOWN with days 2012-01-01..2018-06-01 = 3074",
      len(k) == 1 and k[0]["known_overlap_days"] == (D(2018, 6, 1) - D(2012, 1, 1)).days
      and k[0]["episode_count"] == 1 and k[0]["first_observed"] == D(2010, 1, 1)
      and k[0]["last_observed"] == D(2020, 1, 1))

# 7 ---- bounded no-overlap -> no KNOWN row (fallback SAME_COMPANY_EVER)
periods2 = [dict(periods[0]),
            {**periods[1], "period_start": D(2021, 1, 1), "period_end": D(2023, 1, 1)}]
rows, _sk = derive_pairs([p_a, p_b, c_a, c_b], periods2, AH)
check("bounded no-overlap -> no KNOWN row", all(
      r["co_participation_class"] != "KNOWN_TEMPORAL_OVERLAP" for r in rows)
      and any(r["co_participation_class"] == "SAME_COMPANY_EVER" for r in rows))

# 8/9 ---- point inside bounded -> KNOWN with days NULL; outside -> no KNOWN
snap = O(3, 4, kind="CURRENT_STATE_SNAPSHOT", auth="AUTHORITATIVE_CURRENT_GOVERNANCE",
         at=D(2015, 1, 1), doc=150)
snap2 = O(3, 4, kind="CURRENT_STATE_SNAPSHOT", auth="AUTHORITATIVE_CURRENT_GOVERNANCE",
          at=D(2026, 9, 1), doc=150)
rows, _sk = derive_pairs([p_a, c_a, snap], [periods[0]], AH)
kk = [r for r in rows if r["co_participation_class"] == "KNOWN_TEMPORAL_OVERLAP"]
check("point inside bounded -> KNOWN, days NULL (point semantics)",
      len(kk) == 1 and kk[0]["known_overlap_days"] is None)
rows, _sk = derive_pairs([p_a, c_a, snap2], [periods[0]], AH)
fb = [r for r in rows if r["co_participation_class"] == "SAME_COMPANY_EVER"]
check("point outside bounded -> no KNOWN row; mixed-authority fallback preserved "
      "with NULL authority (M-01)",
      all(r["co_participation_class"] != "KNOWN_TEMPORAL_OVERLAP" for r in rows)
      and len(fb) == 1 and fb[0]["authority_class"] is None
      and fb[0]["source_family"] == "PDF_HISTORY")

# 10/11 ---- left-censored x bounded, left x left -> UNKNOWN, NULL days
lc_open = {"canonical_person_id": 2, "canonical_company_id": 4, "role_context": "BOARD",
           "period_start": D(2012, 1, 1), "period_end": None,
           "period_status": "OPEN_LEFT_CENSORED", "opening_observation_key": "k-o",
           "closing_observation_key": None, "source_family": "PDF_HISTORY"}
rows, _sk = derive_pairs([p_a, c_a, p_b], [periods[0], lc_open], AH)
u = [r for r in rows if r["co_participation_class"] == "UNKNOWN_TEMPORAL_RELATION"]
check("left-censored x bounded -> UNKNOWN, no duration",
      len(u) == 1 and u[0]["known_overlap_days"] is None
      and all(r["co_participation_class"] != "KNOWN_TEMPORAL_OVERLAP" for r in rows))
lc2 = dict(lc_open, canonical_person_id=1)
rows, _sk = derive_pairs([p_a, p_b], [lc_open, lc2], AH)
check("left-censored x left-censored -> UNKNOWN",
      len(rows) == 1 and rows[0]["co_participation_class"] == "UNKNOWN_TEMPORAL_RELATION"
      and rows[0]["known_overlap_days"] is None)

# 12 ---- same-day unordered independent events -> no fabricated duration
e_a = O(1, 5, at=D(2020, 3, 1), event=21)
e_b = O(2, 5, at=D(2020, 3, 1), event=22)
rows, _sk = derive_pairs([e_a, e_b], [], AH)
check("same-day unordered -> no KNOWN duration, SAME_COMPANY_EVER (case 9)",
      len(rows) == 1 and rows[0]["co_participation_class"] == "SAME_COMPANY_EVER"
      and rows[0]["known_overlap_days"] is None)
# same-day via SAME event -> direct co-observation (case 9 second clause)
rows, _sk = derive_pairs([e_a, O(2, 5, at=D(2020, 3, 1), event=21)], [], AH)
check("same-day SAME event -> SAME_OBSERVATION", any(
      r["co_participation_class"] == "SAME_OBSERVATION" for r in rows))

# 13 ---- same pair across two companies -> two company-grain rows
x1 = O(1, 10, at=D(2020, 1, 1), event=31)
x2 = O(2, 10, at=D(2020, 1, 1), event=31)
y1 = O(1, 11, at=D(2021, 1, 1), event=32)
y2 = O(2, 11, at=D(2021, 1, 1), event=32)
rows, _sk = derive_pairs([x1, x2, y1, y2], [], AH)
check("same pair across two companies -> 2 rows, distinct company grain",
      len(rows) == 2 and {r["canonical_company_id"] for r in rows} == {10, 11}
      and all(r["co_participation_class"] == "SAME_OBSERVATION" for r in rows))

# 14 ---- pair global metrics are query-time only: classifier/derive emits no
# shared_* aggregate keys and no pair-global row
rows, _sk = derive_pairs([x1, x2, y1, y2], [], AH)
check("no pair-global aggregate columns on grain rows", all(
      not any(str(k).startswith("shared_") for k in r) for r in rows)
      and len(rows) == 2)

# 15 ---- mixed-authority fallback evidence -> fact PRESERVED with nullable
# metadata (M-01 closure); never skipped, never a 'MIXED' enum
m1 = O(1, 6, at=D(2020, 1, 1), event=41)
m2 = O(2, 6, kind="OWNERSHIP_SNAPSHOT_POINT", fam="OWNERSHIP_GRAPH",
       auth="OWNERSHIP_POINT_OBSERVATION", ctx="BOARD", at=D(2026, 1, 1), attempt=77)
rows, stats = derive_pairs([m1, m2], [], AH)
fb = [r for r in rows if r["co_participation_class"] in ("SAME_COMPANY_EVER",
                                                          "UNKNOWN_TEMPORAL_RELATION")]
check("mixed-authority fallback fact preserved; no skip; no MIXED class",
      len(fb) >= 1 and stats["mixed_authority_fallback_skipped"] == 0
      and not any(r["co_participation_class"] == "MIXED" for r in rows))
check("heterogeneous family+authority -> both metadata NULL, class unchanged",
      all(r["source_family"] is None and r["authority_class"] is None for r in fb))
# direct evidence is never dropped by the authority rule
rows, _sk = derive_pairs([m1, m2, O(2, 6, at=D(2020, 1, 1), event=41)], [], AH)
check("mixed authority does NOT drop direct shared-source rows", any(
      r["co_participation_class"] == "SAME_OBSERVATION" for r in rows))

# 16 ---- member_set_hash permutation invariance / distinctness
check("member_set_hash permutation-invariant", member_set_hash([3, 1, 2]) == member_set_hash([2, 3, 1])
      and member_set_hash([1, 2]) != member_set_hash([1, 3]))

# 17 ---- intersect_days half-open semantics
check("intersect_days half-open [s,e)", intersect_days(D(2020, 1, 1), D(2020, 1, 11),
      D(2020, 1, 5), D(2020, 1, 6)) == 1 and intersect_days(D(2020, 1, 1), D(2020, 1, 5),
      D(2020, 1, 5), D(2020, 1, 9)) == 0 and intersect_days(D(2020, 1, 1), D(2020, 1, 5),
      D(2020, 2, 1), D(2020, 2, 5)) == 0)

# 18 ---- multi-period overlap sums per period combination (no fabrication)
pa = per_bounded([(D(2010, 1, 1), D(2020, 1, 1), "a-o", "a-c"), (D(2022, 1, 1), D(2024, 1, 1), "a2-o", "a2-c")])
pb = per_bounded([(D(2019, 1, 1), D(2023, 1, 1), "b-o", "b-c")])
o_a = [O(1, 7, at=D(2010, 1, 1), event=51), O(1, 7, kind="REMOVED_EVENT", at=D(2020, 1, 1), event=52),
       O(1, 7, at=D(2022, 1, 1), event=53), O(1, 7, kind="REMOVED_EVENT", at=D(2024, 1, 1), event=54)]
o_b = [O(2, 7, at=D(2019, 1, 1), event=55), O(2, 7, kind="REMOVED_EVENT", at=D(2023, 1, 1), event=56)]
rows, _sk = derive_pairs(o_a + o_b, [
    {"canonical_person_id": 1, "canonical_company_id": 7, "role_context": "BOARD",
     "period_start": D(2010, 1, 1), "period_end": D(2020, 1, 1), "period_status": "BOUNDED",
     "opening_observation_key": "a-o", "closing_observation_key": "a-c", "source_family": "PDF_HISTORY"},
    {"canonical_person_id": 1, "canonical_company_id": 7, "role_context": "BOARD",
     "period_start": D(2022, 1, 1), "period_end": D(2024, 1, 1), "period_status": "BOUNDED",
     "opening_observation_key": "a2-o", "closing_observation_key": "a2-c", "source_family": "PDF_HISTORY"},
    {"canonical_person_id": 2, "canonical_company_id": 7, "role_context": "BOARD",
     "period_start": D(2019, 1, 1), "period_end": D(2023, 1, 1), "period_status": "BOUNDED",
     "opening_observation_key": "b-o", "closing_observation_key": "b-c", "source_family": "PDF_HISTORY"},
], AH)
kk = [r for r in rows if r["co_participation_class"] == "KNOWN_TEMPORAL_OVERLAP"]
exp = (D(2020, 1, 1) - D(2019, 1, 1)).days + (D(2023, 1, 1) - D(2022, 1, 1)).days
check("break/return: two intersecting episodes, days summed, episode_count=2",
      len(kk) == 1 and kk[0]["known_overlap_days"] == exp and kk[0]["episode_count"] == 2)

# 19 ---- same snapshot + overlapping periods -> BOTH class rows (disjunct classes)
rows, _sk = derive_pairs([s1, s2], [
    {"canonical_person_id": 1, "canonical_company_id": 2, "role_context": "BOARD",
     "period_start": D(2010, 1, 1), "period_end": D(2020, 1, 1), "period_status": "BOUNDED",
     "opening_observation_key": "a-o", "closing_observation_key": "a-c", "source_family": "PDF_HISTORY"},
    {"canonical_person_id": 2, "canonical_company_id": 2, "role_context": "BOARD",
     "period_start": D(2012, 1, 1), "period_end": D(2018, 1, 1), "period_status": "BOUNDED",
     "opening_observation_key": "b-o", "closing_observation_key": "b-c", "source_family": "PDF_HISTORY"},
], AH)
check("same snapshot AND period overlap -> two disjunct class rows",
      {r["co_participation_class"] for r in rows} == {"SAME_SNAPSHOT", "KNOWN_TEMPORAL_OVERLAP"}
      and len(rows) == 2)

print("FAILS:", fails or "none")
sys.exit(1 if fails else 0)