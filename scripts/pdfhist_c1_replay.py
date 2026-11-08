"""C-1: historical current-state replay engine + fixtures.

Semantics (binding, from the decision audit §6):
  * scalars (NAME/ADDRESS/CAPITAL/PURPOSE/SIGNING_RULE/COMPANY_FORM):
    last-wins replacement by APPLIED change event
  * membership (BOARD/MANAGEMENT/AUDITOR): per-entity set semantics;
    APPOINTED/ADDED/JOINED opens; REMOVED/RESIGNED/LEFT closes the SAME
    normalized entity; appointment of B never closes A
  * event-only types (ARTICLES_CHANGE/OTHER_CHANGE) never mutate state
  * date precedence: effective_date > registration_date > notice_date
  * same-day: no secure ordering evidence -> SAME_DAY_UNORDERED flag; the
    engine must flag, never silently claim an order

Error classes (§10): EXACT_MATCH, NORMALIZATION_ONLY,
EXPECTED_SOURCE_LIMITATION, SAME_DAY_UNORDERED, MISSING_CHANGE_EVIDENCE,
PROJECTION_ERROR.
"""
from __future__ import annotations

import re
import unicodedata

MEMBERSHIP_TYPES = {"BOARD_CHANGE", "MANAGEMENT_CHANGE", "AUDITOR_CHANGE"}
MEMBERSHIP_FIELD = {"BOARD_CHANGE": "board", "MANAGEMENT_CHANGE": "management",
                    "AUDITOR_CHANGE": "auditor"}
OPEN_ACTIONS = {"APPOINTED", "ADDED", "JOINED"}
CLOSE_ACTIONS = {"REMOVED", "RESIGNED", "LEFT"}
SCALAR_MAP = {
    "NAME_CHANGE": "name", "ADDRESS_CHANGE": "address",
    "CAPITAL_CHANGE": "capital", "PURPOSE_CHANGE": "purpose",
    "SIGNING_RULE_CHANGE": "signing_rules", "COMPANY_FORM_CHANGE": "company_form",
}
EVENT_ONLY_TYPES = {"ARTICLES_CHANGE", "OTHER_CHANGE"}


def norm_entity(s):
    if not s:
        return None
    return unicodedata.normalize("NFC", str(s)).strip().lower()


def event_date(ev: dict):
    """Binding precedence: effective > registration > notice."""
    return ev.get("effective_date") or ev.get("registration_date") \
        or ev.get("notice_date")


def project(snapshot: dict, events: list[dict], a_date: str):
    """Replay VERIFIED events dated in (a_date, horizon] onto snapshot A.

    Returns (projected_state, uncertainties, per_event_log). Mutates a copy.
    """
    state = json_copy(snapshot)
    sets = {"board": {}, "management": {}, "auditor": {}}
    for field, members in sets.items():
        for m in state.get(field) or []:
            if isinstance(m, dict):
                entry = dict(m)
            else:
                entry = {"name": str(m)}
            sets[field][norm_entity(entry["name"])] = entry
        state[field] = [dict(m) for m in (state.get(field) or [])]
    same_day_flags = _flag_same_day_conflicts(events)

    ordered = sorted([e for e in events if event_date(e)],
                     key=lambda e: (event_date(e),))
    # events without any date are not applicable in the window
    for ev in ordered:
        d = event_date(ev)
        if d <= a_date:
            continue
        t = ev.get("type")
        if t in EVENT_ONLY_TYPES:
            continue
        act = ev.get("action")
        k = norm_entity(ev.get("entity_normalized") or ev.get("entity"))
        if not k:
            continue
        if t in MEMBERSHIP_TYPES:
            field = MEMBERSHIP_FIELD[t]
            if act in OPEN_ACTIONS:
                nm = ev.get("entity") or k
                if k in sets[field]:
                    sets[field][k] = dict(sets[field][k])  # keep role/entry
                else:
                    role = ev.get("role") or ev.get("raw_text") or nm
                    sets[field][k] = {"name": nm}
            elif act in CLOSE_ACTIONS:
                sets[field].pop(k, None)
            else:
                raise ValueError("unknown action %r" % act)
        elif t in SCALAR_MAP:
            state[SCALAR_MAP[t]] = ev.get("new_value") or ev.get("entity")
        else:
            raise ValueError("unknown type %r" % t)
    for field in sets:
        state[field] = list(sets[field].values())
    return state, same_day_flags, [e for e in events if not event_date(e)]


def _flag_same_day_conflicts(events: list[dict]) -> list[dict]:
    same_day = {}
    for ev in events:
        d = event_date(ev)
        if d:
            same_day.setdefault(d, []).append(ev)
    flags = []
    for d, group in sorted(same_day.items()):
        seen = {}
        for ev in group:
            k = norm_entity(ev.get("entity_normalized") or ev.get("entity"))
            if not k or not ev.get("action"):
                continue
            seen.setdefault(k, set()).add(ev["action"])
        for k, acts in sorted(seen.items()):
            if acts & OPEN_ACTIONS and acts & CLOSE_ACTIONS:
                flags.append({"class": "SAME_DAY_UNORDERED",
                              "date": d, "entity": k,
                              "directions": sorted(acts)})
    return flags


def compare(projected: dict, expected: dict) -> dict:
    """Field-by-field comparison with normalization tolerance only."""
    out = {}
    for f in ("name", "aliases", "address", "capital", "purpose",
              "signing_rules", "company_form", "board", "management",
              "auditor", "accounts_dates"):
        a, b = projected.get(f), expected.get(f)
        if a == b:
            cls = "EXACT_MATCH"
        elif _norm_value(a) == _norm_value(b):
            cls = "NORMALIZATION_ONLY"
        else:
            cls = "PROJECTION_ERROR"
        out[f] = {"projected": a, "expected": b, "class": cls}
    return out


def _norm_value(v):
    import json as _json
    if isinstance(v, list):
        return sorted((_json.dumps(_norm_value(x), sort_keys=True,
                                   ensure_ascii=False)) for x in v)
    if isinstance(v, dict):
        return {k: _norm_value(x) for k, x in sorted(v.items())}
    if isinstance(v, str):
        return re.sub(r"\s+", " ",
                      unicodedata.normalize("NFC", v)).strip().lower()
    if v is None:
        return []
    return v


def json_copy(o):
    import copy
    return copy.deepcopy(o)