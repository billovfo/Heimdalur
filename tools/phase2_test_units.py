#!/usr/bin/env python3
"""Phase 2 unit tests — pure builder logic (E1-E3, precedence, identity, temporal).
Run: python3 tools/phase2_test_units.py"""
import sys, datetime
sys.path.insert(0, "tools")
from phase2_build import (governed_date, action_direction, resolve_identity,
                          derive_periods, content_hash)

D = datetime.date
fails = []
def check(name, cond):
    print(("  ok  " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)

d1, d2, d3 = D(2010, 4, 1), D(2015, 6, 1), D(2020, 1, 1)
def E(key, date, dirn):
    return {"key": key, "date": date, "dir": dirn}

# ---- B21 date precedence (4 cases)
check("prec-effective", governed_date(d1, d2, d2, d2) == d1)
check("prec-registration", governed_date(None, d2, d2, d2) == d2)
check("prec-notice", governed_date(None, None, d2, d2) == d2)
check("prec-source_event_date", governed_date(None, None, None, d2) == d2)
check("prec-none", governed_date(None, None, None, None) is None)

# ---- B17 action vocabulary
check("open-vocab", all(action_direction("BOARD_CHANGE", a) == "OPEN" for a in ("APPOINTED", "ADDED", "JOINED")))
check("close-vocab", all(action_direction("MANAGEMENT_CHANGE", a) == "CLOSE" for a in ("REMOVED", "RESIGNED", "LEFT")))
check("unknown-action", action_direction("BOARD_CHANGE", "CHANGED") is None)
check("non-membership", action_direction("AUDITOR_CHANGE", "APPOINTED") is None)

# ---- B12/B13 identity classification
check("id-0-anchors", resolve_identity({"source_event_id": 1}, []) == (None, "UNRESOLVED"))
check("id-1-direct", resolve_identity({"source_event_id": 5}, [{"canonical_entity_id": 7, "source_event_id": 5}]) == (7, "DIRECT_LINK"))
check("id-1-propagation", resolve_identity({"source_event_id": 9}, [{"canonical_entity_id": 7, "source_event_id": 5}]) == (7, "SOURCE_LOCAL_PROPAGATION"))
check("id-2-distinct-ambiguous", resolve_identity({"source_event_id": 1}, [
    {"canonical_entity_id": 7, "source_event_id": 1}, {"canonical_entity_id": 9, "source_event_id": 1}]) == (None, "AMBIGUOUS"))
check("id-2-same-canonical-resolves", resolve_identity({"source_event_id": 9}, [
    {"canonical_entity_id": 7, "source_event_id": 1}, {"canonical_entity_id": 7, "source_event_id": 5}]) == (7, "SOURCE_LOCAL_PROPAGATION"))

# ---- E1/E2/E3 (plan §7.3): A(d1)->R(d2)->A(d3) => 2 periods, FIFO
ps, sd, nd = derive_periods([E("E1", d1, "OPEN"), E("E2", d2, "CLOSE"), E("E3", d3, "OPEN")])
check("E1E2E3: 2 periods", len(ps) == 2)
check("E1E2E3: bounded first", ps[0]["period_status"] == "BOUNDED" and ps[0]["period_start"] == d1 and ps[0]["period_end"] == d2)
check("E1E2E3: open-left-censored second", ps[1]["period_status"] == "OPEN_LEFT_CENSORED" and ps[0]["opening_observation_key"] == "E1" and ps[1]["opening_observation_key"] == "E3")

# ---- B26 same-day unordered open+close: no duration, no period, counted
ps, sd, nd = derive_periods([E("O", d1, "OPEN"), E("C", d1, "CLOSE")])
check("same-day suppressed", len(ps) == 0 and sd == 1)

# ---- unknown-action event never opens/closes (B17)
ps, sd, nd = derive_periods([E("X", d1, "OPEN"), E("Y", d2, None), E("C", d3, "CLOSE")])
check("unknown-action no period effect", len(ps) == 1 and ps[0]["period_status"] == "BOUNDED"
      and ps[0]["period_start"] == d1 and ps[0]["period_end"] == d3)

# ---- B23: CLOSED_ONLY with unknown start
ps, _, _ = derive_periods([E("C", d1, "CLOSE")])
check("closed-only shape", ps[0]["period_status"] == "CLOSED_ONLY" and ps[0]["period_start"] is None and ps[0]["start_status"] == "UNKNOWN")

# ---- no usable date: event kept as evidence, no period, counted
ps, sd, nd = derive_periods([E("O", None, "OPEN"), E("O2", d1, "OPEN")])
check("no-date event not paired", len(ps) == 1 and ps[0]["period_status"] == "OPEN_LEFT_CENSORED")

# ---- FIFO oldest-open matching
ps, _, _ = derive_periods([E("O1", d1, "OPEN"), E("O2", d2, "OPEN"), E("C", d3, "CLOSE")])
b = [p for p in ps if p["period_status"] == "BOUNDED"][0]
check("fifo-oldest", b["opening_observation_key"] == "O1")
left = [p for p in ps if p["period_status"] == "OPEN_LEFT_CENSORED"]
check("fifo-leftover", len(left) == 1 and left[0]["opening_observation_key"] == "O2")

# ---- content hash deterministic
o = {"source_observation_key": "PDF:4:CHE:1", "canonical_person_id": 7, "canonical_company_id": 2518,
     "role_context": "BOARD", "source_family": "PDF_HISTORY", "authority_class": "HISTORICAL_PDF_OBSERVATION",
     "identity_resolution": "DIRECT_LINK", "observation_kind": "APPOINTED_EVENT", "observed_at": d1,
     "observed_state_at": None, "action": "APPOINTED", "state": None, "ownership_percentage": None,
     "source_projection_run_id": 4, "source_person_role_id": 1, "source_history_event_id": 1,
     "source_event_id": 1, "source_document_id": 1, "source_observation_id": 1,
     "source_relation_id": None, "source_attempt_id": None}
check("hash-deterministic", content_hash([o], []) == content_hash([o], []))

print("FAILS:", fails or "none")
sys.exit(1 if fails else 0)