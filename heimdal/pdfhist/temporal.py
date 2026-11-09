"""Change-validity model per plan v3.1.2 FASE 17.

* SCALAR_REPLACEMENT: one value replaces the previous; intervals are
  [t_i, t_{i+1}) over the values of one family, last interval open.
* SET_MEMBERSHIP: intervals pair an entity with an action; APPOINTED/ADDED/
  JOINED opens, REMOVED/RESIGNED/LEFT closes the OPEN interval whose
  entity_normalized matches. Nothing fuzzy in between.
* EVENT_ONLY: change rows without interval semantics, passed through.

Event-date precedence (D-13): the ONE rule for every PDF-history surface -
projection build, consumer SQL and portal routes - is

    effective_date -> registration_date -> notice_date -> source_event_date

(docs/HEIMDAL-PDF-HISTORIK-FINAL-CLOSURE-AUDIT-v1.md). governing_date() and
governing_date_sql() below are its only implementations. No date at all =>
review entry CHANGE_DATE_MISSING. Verbatim raw_text is always carried through.
"""
from __future__ import annotations

from datetime import date

SCALAR_FAMILY_MAP = {
    "NAME_CHANGE": "NAME", "CAPITAL_CHANGE": "CAPITAL",
    "PURPOSE_CHANGE": "PURPOSE", "SIGNING_RULE_CHANGE": "SIGNING_RULES",
    "COMPANY_FORM_CHANGE": "COMPANY_FORM", "ADDRESS_CHANGE": "ADDRESS",
}
MEMBERSHIP_FAMILY_MAP = {
    "BOARD_CHANGE": "BOARD", "MANAGEMENT_CHANGE": "MANAGEMENT",
    "AUDITOR_CHANGE": "AUDITOR",
}
MEMBERSHIP_OPEN_ACTIONS = {"APPOINTED", "ADDED", "JOINED"}
MEMBERSHIP_CLOSE_ACTIONS = {"REMOVED", "RESIGNED", "LEFT"}
EVENT_ONLY_TYPES = {"ARTICLES_CHANGE", "OTHER_CHANGE", "MERGER"}


DATE_PRECEDENCE = ("effective_date", "registration_date", "notice_date",
                   "source_event_date")


def governing_date(change: dict, source_event_date=None):
    """First set date of DATE_PRECEDENCE in change, else source_event_date."""
    return next((change[k] for k in DATE_PRECEDENCE if change.get(k)),
                source_event_date)


def governing_date_sql(alias: str = "") -> str:
    """governing_date() as a SQL expression over a company_history_event row."""
    prefix = f"{alias}." if alias else ""
    return "COALESCE(" + ", ".join(prefix + k for k in DATE_PRECEDENCE) + ")"


def build_change_history(
    changes: list[dict],
    source_event_date: str | None = None,
):
    """Return {'entries': [...], 'review': [...]}.

    entries: [{'family', 'entity', 'entity_normalized', 'action', 'from_date',
    'to_date', 'raw_text', 'index'}] - scalar families become intervals
    [from, next-from); memberships close on matching entity; event-only rows
    pass through with to_date=None.
    review: [{'reason', 'raw_text'}] for CHANGE_DATE_MISSING,
    MEMBERSHIP_CLOSE_WITHOUT_OPEN, DUPLICATE_OPEN_APPOINTMENT.
    """
    entries: list[dict] = []
    review: list[dict] = []

    dated: list[tuple[str | None, int, dict]] = []
    for index, change in enumerate(changes):
        dated.append((governing_date(change, source_event_date), index, change))

    # -- scalars ------------------------------------------------------------
    scalar_intervals: dict[str, list] = {}
    for entry_date, index, change in dated:
        family = SCALAR_FAMILY_MAP.get(change.get("type", ""))
        if family is None:
            continue
        if not entry_date:
            review.append({"reason": "CHANGE_DATE_MISSING",
                           "raw_text": change.get("raw_text", "")})
            continue
        scalar_intervals.setdefault(family, []).append(
            (entry_date, index, change))

    for family, items in scalar_intervals.items():
        ordered = sorted(items, key=lambda item: (item[0], item[1]))
        for position, (entry_date, index, change) in enumerate(ordered):
            next_date = (ordered[position + 1][0]
                         if position + 1 < len(ordered) else None)
            entries.append({
                "family": family,
                "entity": change.get("entity"),
                "entity_normalized": change.get("entity_normalized"),
                "action": change.get("action"),
                "from_date": entry_date,
                "to_date": next_date,
                "raw_text": change.get("raw_text", ""),
            })

    # -- memberships ---------------------------------------------------------
    open_intervals: dict[str, list[dict]] = {}
    ordered_membership = []
    for entry_date, index, change in dated:
        family = MEMBERSHIP_FAMILY_MAP.get(change.get("type", ""))
        if family is None:
            continue
        ordered_membership.append((entry_date, index, family, change))

    ordered_membership.sort(key=lambda item: (item[0] or "9999-12-31", item[1]))
    for entry_date, index, family, change in ordered_membership:
        action = change.get("action")
        entity = change.get("entity")
        entity_key = change.get("entity_normalized") or (entity or None)
        raw_text = change.get("raw_text", "")
        if action in MEMBERSHIP_OPEN_ACTIONS:
            opened = [e for e in open_intervals.get(family, [])
                      if e["entity_normalized"] == entity_key
                      and e["to_date"] is None]
            if opened:
                review.append({"reason": "DUPLICATE_OPEN_APPOINTMENT",
                               "raw_text": raw_text})
                continue
            entry = {"family": family, "entity": entity,
                     "entity_normalized": entity_key, "action": action,
                     "from_date": entry_date, "to_date": None,
                     "raw_text": raw_text}
            entries.append(entry)
            if not entry_date:
                review.append({"reason": "CHANGE_DATE_MISSING",
                               "raw_text": raw_text})
            open_intervals.setdefault(family, []).append(entry)
        elif action in MEMBERSHIP_CLOSE_ACTIONS:
            open_list = [e for e in open_intervals.get(family, [])
                         if e["entity_normalized"] == entity_key
                         and e["to_date"] is None]
            if not open_list:
                review.append({"reason": "MEMBERSHIP_CLOSE_WITHOUT_OPEN",
                               "raw_text": raw_text})
                continue
            closable = max(open_list,
                           key=lambda e: (e["from_date"] or ""))
            if entry_date:
                closable["to_date"] = entry_date
            else:
                review.append({"reason": "CHANGE_DATE_MISSING",
                               "raw_text": raw_text})
        else:
            review.append({"reason": "UNKNOWN_MEMBERSHIP_ACTION",
                           "raw_text": raw_text})

    # -- event-only passthrough ----------------------------------------------
    for entry_date, index, change in dated:
        if change.get("type", "") not in EVENT_ONLY_TYPES:
            continue
        if not entry_date:
            review.append({"reason": "CHANGE_DATE_MISSING",
                           "raw_text": change.get("raw_text", "")})
            continue
        entries.append({
            "family": "EVENT_ONLY", "entity": None,
            "entity_normalized": None, "action": change.get("action"),
            "from_date": entry_date, "to_date": None,
            "raw_text": change.get("raw_text", ""),
        })

    entries.sort(key=lambda e: (e["from_date"] or "9999-12-31",
                                e["family"], e["action"] or ""))
    return {"entries": entries, "review": review}