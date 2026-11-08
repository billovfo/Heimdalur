"""C-4: auditor current-state replay over the full Lysing evidence stream.

Population: run 424245 VERIFIED Lysing observations (full-run population,
attribution gate already reconciled with 0 leaks in
full_run_attribution_reconciliation.json).

Semantics (binding, from the decision audit):
  * date precedence per event: effective_date -> registration_date ->
    notice_date -> (none)
  * APPOINTED opens entity; REMOVED closes entity (entity_normalized, NFC)
  * removal of an entity that was never opened -> history is left-censored:
    the auditor existed before the evidence window; current state MUST NOT be
    concluded as "none"
  * same-day APPOINTED+REMOVED for the SAME normalized entity -> no secure
    ordering -> AUDITOR_SAME_DAY_AMBIGUOUS (never silently ordered)
  * distinct-entity same-day opposite directions are order-independent and
    therefore not ambiguous
  * anchors never populate auditor (0/15152 Felag parses) -> companies with
    no AUDITOR_CHANGE at all are NO_AUDITOR_EVIDENCE, never "no auditor"

States: AUDITOR_CURRENT_VERIFIED / AUDITOR_CURRENT_NONE_VERIFIED /
AUDITOR_HISTORY_LEFT_CENSORED / AUDITOR_SAME_DAY_AMBIGUOUS /
AUDITOR_CONFLICT / NO_AUDITOR_EVIDENCE
"""
from __future__ import annotations

import os  # DSNs from tools/dev_env.sh, auth via PGPASSFILE (I-4)
import json
import unicodedata

import psycopg

DSN = os.environ["HEIMDAL_PDFHIST_DB"]
RUN = 424245


def norm(s):
    if not s:
        return None
    return unicodedata.normalize("NFC", str(s)).strip().lower()


def pick_date(ev: dict):
    """Binding date precedence: effective > registration > notice."""
    d = ev.get("effective_date") or ev.get("registration_date") \
        or ev.get("notice_date")
    src = ("effective" if ev.get("effective_date") else
           "registration" if ev.get("registration_date") else
           "notice" if ev.get("notice_date") else None)
    return d, src


def fetch_population(conn) -> tuple[dict, dict]:
    """companies: id -> {events}, population: every company with >=1 Lysing
    parse in the run (for the NO_AUDITOR_EVIDENCE denominator)."""
    events = {}
    rows = conn.execute("""
        SELECT DISTINCT e.company_id, ld.document_id, o.id, p.structured_data
        FROM pdf_source_event e
        JOIN pdf_source_event_document ld ON ld.source_event_id = e.id
        JOIN pdf_document_observation o
          ON o.document_id = ld.document_id AND o.collection_run_id = %s
        JOIN pdf_parse_result p ON p.document_observation_id = o.id
        WHERE p.structured_data::text LIKE '%%AUDITOR_CHANGE%%'
    """, (RUN,)).fetchall()
    for company_id, document_id, obs_id, sd in rows:
        if isinstance(sd, str):
            sd = json.loads(sd)
        for ch in sd.get("changes", []):
            if ch.get("type") != "AUDITOR_CHANGE":
                continue
            events.setdefault(company_id, []).append({
                "date": pick_date(ch)[0],
                "date_source": pick_date(ch)[1],
                "action": ch.get("action"),
                "entity": ch.get("entity"),
                "entity_normalized": norm(ch.get("entity_normalized")
                                          or ch.get("entity")),
                "document_id": document_id,
                "observation_id": obs_id,
                "raw_text": ch.get("raw_text"),
            })
    pop = conn.execute("""
        SELECT DISTINCT e.company_id
        FROM pdf_source_event e
        JOIN pdf_source_event_document ld ON ld.source_event_id = e.id
        JOIN pdf_document_observation o
          ON o.document_id = ld.document_id AND o.collection_run_id = %s
        JOIN pdf_parse_result p ON p.document_observation_id = o.id
        WHERE p.document_kind = 'REGISTRATION_NOTICE'
    """, (RUN,)).fetchall()
    return events, {r[0] for r in pop}


def replay_company(evs: list[dict]) -> dict:
    evs = sorted(evs, key=lambda e: (e["date"] or "9999-99-99",
                                     e["action"] == "APPOINTED"))
    open_set: dict[str, dict] = {}
    left_censored = []
    same_entity_same_day_conflict = []
    same_day_groups: dict[str, list] = {}
    for e in evs:
        if e["date"]:
            same_day_groups.setdefault(e["date"], []).append(e)
    # same-day same-entity conflict scan
    for day, group in same_day_groups.items():
        seen = {}
        for e in group:
            k = e["entity_normalized"]
            seen.setdefault(k, set()).add(e["action"])
        for k, actions in seen.items():
            if {"APPOINTED", "REMOVED"} <= actions:
                same_entity_same_day_conflict.append(
                    {"day": day, "entity": k})
    # ordered application
    first_event = evs[0] if evs else None
    for e in evs:
        k = e["entity_normalized"]
        if not k:
            continue
        if e["action"] == "APPOINTED":
            open_set[k] = e
        elif e["action"] == "REMOVED":
            if k in open_set:
                del open_set[k]
            else:
                left_censored.append(
                    {"day": e["date"], "entity": k,
                     "document_id": e["document_id"]})

    if not evs:
        state, reason = "NO_AUDITOR_EVIDENCE", "no AUDITOR_CHANGE events"
    elif same_entity_same_day_conflict:
        state = "AUDITOR_SAME_DAY_AMBIGUOUS"
        reason = "same-day opposite directions for same entity"
    elif first_event and first_event["action"] == "REMOVED":
        state = "AUDITOR_HISTORY_LEFT_CENSORED"
        reason = ("first evidence event is a REMOVAL; pre-window auditor"
                  " history absent; current state MUST NOT be concluded")
    elif open_set:
        state = "AUDITOR_CURRENT_VERIFIED"
        reason = "open appointment evidence; end state determined"
    elif not open_set and evs and evs[-1]["action"] == "REMOVED":
        state = "AUDITOR_CURRENT_NONE_VERIFIED"
        reason = "opens then closes; ends closed"
    else:
        # empty set, ends otherwise: conservative
        state, reason = "AUDITOR_CONFLICT", "unclassifiable end state"

    return {
        "n_events": len(evs),
        "first_event": {k: first_event[k] for k in
                        ("date", "action", "entity")} if first_event else None,
        "last_event": {k: evs[-1][k] for k in
                       ("date", "action", "entity")} if evs else None,
        "open_set": sorted(open_set),
        "mid_stream_never_opened_removals": left_censored,
        "history_incomplete": bool(left_censored),
        "same_day_same_entity_conflicts": same_entity_same_day_conflict,
        "state": state,
        "state_reason": reason,
    }


def main():
    conn = psycopg.connect(DSN)
    events, population = fetch_population(conn)
    results = {}
    for cid, evs in sorted(events.items()):
        results[str(cid)] = replay_company(evs)
    for cid in population:
        results.setdefault(str(cid), {
            "n_events": 0, "first_event": None, "last_event": None,
            "open_set": [], "left_censored_removals": [],
            "same_day_same_entity_conflicts": [],
            "state": "NO_AUDITOR_EVIDENCE",
            "state_reason": "no AUDITOR_CHANGE events in Lysing stream"})
    dist = {}
    for r in results.values():
        dist[r["state"]] = dist.get(r["state"], 0) + 1
    metrics = {
        "population_companies_with_lysing_parse": len(population),
        "companies_with_auditor_change_events": len(events),
        "total_auditor_change_events": sum(len(v) for v in events.values()),
        "state_distribution": dist,
        "felag_anchor_auditor_population": 0,
    }
    out = {"run": RUN, "methodology": __doc__.strip(), "metrics": metrics,
           "results": results}
    with open("auditor_current_state_replay.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    print(json.dumps(metrics, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()