"""Review reader service (P1 queue/packets, P2 ordering). Read-only builders.

Queue summaries are lightweight and derived per request (no packet
pre-generation). Packets are sealed via repository.materialize_packet on
first open. Candidate discovery reuses the closed deterministic readers
(role_relations + display names); it never mutates identity.
"""
from __future__ import annotations

from heimdal.pdfhist.human_links import resolve_keys_in_run  # noqa: F401  (one resolver)
from heimdal.pdfhist.normalize import normalize_registration_number

from . import cases as case_keys
from . import repository as repo


def _doc_shas(conn, observation_ids) -> dict:
    """observation id -> document sha256, in one query (E-13: was one per row)."""
    ids = sorted({i for i in observation_ids if i is not None})
    if not ids:
        return {}
    rows = conn.execute("SELECT id, sha256 FROM pdf_document_observation WHERE id = ANY(%s)",
                        (ids,)).fetchall()
    return {(r["id"] if isinstance(r, dict) else r[0]):
            (r["sha256"] if isinstance(r, dict) else r[1]) for r in rows}


def _doc_sha(conn, observation_id):
    return _doc_shas(conn, [observation_id]).get(observation_id)


def unresolved_rows(conn, run_id: int, limit: int = 10000, like_role=None):
    """Read UNRESOLVED person occurrences with persisted provenance.

    like_role: only the rows classify_rows compares with that role (same
    source event and normalized name), so one role's verdict matches the queue.
    """
    cols = ("person_role_id", "match_metadata", "person_name",
            "entity_normalized", "role_type", "source_scope",
            "source_document_id", "source_event_id", "source_observation_id",
            "source_parse_result_id", "company_id")
    peers, params = "", [run_id]
    if like_role is not None:
        peers = ("JOIN registry_projection.company_person_role t "
                 "ON t.projection_run_id = pr.projection_run_id AND t.id = %s "
                 "AND t.source_event_id IS NOT DISTINCT FROM pr.source_event_id "
                 "AND t.entity_normalized = pr.entity_normalized")
        params = [like_role, run_id]
    rows = conn.execute(
        """SELECT l.person_role_id, l.match_metadata,
                  pr.person_name, pr.entity_normalized, pr.role_type,
                  pr.source_scope, pr.source_document_id, pr.source_event_id,
                  pr.source_observation_id, pr.source_parse_result_id,
                  pr.company_id
           FROM registry_projection.entity_link l
           JOIN registry_projection.company_person_role pr
             ON pr.id = l.person_role_id AND pr.projection_run_id = l.projection_run_id
           %s
           WHERE l.projection_run_id = %%s AND l.entity_type = 'PERSON'
             AND l.link_status = 'UNRESOLVED'
           ORDER BY l.person_role_id LIMIT %%s""" % peers,
        (*params, limit)).fetchall()
    out = []
    for r in rows:
        out.append(dict(r) if isinstance(r, dict) else dict(zip(cols, r)))
    return out


def classify_rows(conn, rows: list[dict]) -> tuple[list[dict], dict]:
    """Attach eligibility + stable-key inputs. Returns (eligible, reason_counts)."""
    from collections import Counter
    eligible, counts = [], Counter()
    seen_event_names: set[tuple] = set()
    dup_keys: set[tuple] = set()
    for r in rows:
        key = (r.get("source_event_id"), (r.get("entity_normalized") or "").strip().lower())
        if key in seen_event_names:
            dup_keys.add(key)
        seen_event_names.add(key)
    shas = _doc_shas(conn, [r.get("source_observation_id") for r in rows])
    for r in rows:
        norm = (r.get("entity_normalized") or r.get("person_name") or "").strip().lower()
        info = {"source_scope": r.get("source_scope"),
                "doc_sha256": shas.get(r.get("source_observation_id")),
                "source_event_id": r.get("source_event_id"),
                "source_observation_id": r.get("source_observation_id"),
                "source_parse_result_id": r.get("source_parse_result_id"),
                "norm_name": norm,
                "role_type": r.get("role_type"),
                "duplicate_in_event": (
                    r.get("source_event_id"),
                    norm) in dup_keys}
        verdict = case_keys.classify_occurrence(info)
        if verdict["eligible"]:
            eligible.append({**r, "eligibility_kind": verdict["kind"], **info})
        else:
            counts[verdict["reason"]] += 1
    return eligible, dict(counts)


def reviewable(conn, run_id: int, person_role_id: int) -> bool:
    """E-10: only an UNRESOLVED, eligible occurrence (a queue row) is decidable."""
    rows = unresolved_rows(conn, run_id, like_role=person_role_id)
    eligible, _ = classify_rows(conn, rows)
    return any(r["person_role_id"] == person_role_id for r in eligible)


def next_queue_role(conn, run_id: int, after_role: int):
    """E-9: the queue item after `after_role` (wraps to the first), or None."""
    rows = conn.execute(
        "SELECT person_role_id FROM registry_projection.entity_link "
        "WHERE projection_run_id = %s AND entity_type = 'PERSON' "
        "AND link_status = 'UNRESOLVED' AND person_role_id <> %s "
        "ORDER BY person_role_id <= %s, person_role_id",
        (run_id, after_role, after_role)).fetchall()
    for r in rows:
        rid = r["person_role_id"] if isinstance(r, dict) else r[0]
        if reviewable(conn, run_id, rid):
            return rid
    return None


def coverage_health(conn, run_id: int) -> dict:
    """Compact operational coverage summary (E1, read-only).

    NOT ELIGIBLE != NOT YET REVIEWED: the former can never enter the queue
    under the current stable-identity contract.
    """
    from . import repository as repo
    rows = unresolved_rows(conn, run_id, limit=100000)
    eligible, reason_counts = classify_rows(conn, rows)
    not_eligible_total = sum(reason_counts.values())
    decided = conn.execute(
        "SELECT decision, application_status, count(*) AS n FROM %s "
        "GROUP BY decision, application_status" % repo.DECISION_TABLE).fetchall()
    by_decision: dict = {}
    by_status: dict = {}
    for r in decided:
        d = dict(r) if isinstance(r, dict) else dict(
            zip(("decision", "application_status", "n"), r))
        by_decision[d["decision"]] = by_decision.get(d["decision"], 0) + d["n"]
        by_status[d["application_status"]] = \
            by_status.get(d["application_status"], 0) + d["n"]
    total = len(rows)
    assert len(eligible) + not_eligible_total == total  # reconcile
    return {
        "run_id": run_id,
        "unresolved_total": total,
        "reviewable_now": len(eligible),
        "not_eligible_total": not_eligible_total,
        "not_eligible_breakdown": dict(reason_counts),
        "decided_by_decision": by_decision,
        "decided_by_status": by_status,
    }


def batch_state_pin(conn, run_id: int, decisions: list[dict]) -> str:
    """State pin for a frozen batch (E3). Covers decision targets AND current
    projection-side link state, so source drift / new automatic links /
    target conflicts after preview invalidate the pin. Score never included.
    """
    import hashlib
    import json as _json
    content = conn.execute(
        "SELECT content_hash FROM registry_projection.projection_run "
        "WHERE projection_run_id = %s", (run_id,)).fetchone()
    chash = (content["content_hash"] if isinstance(content, dict)
             else (content[0] if content else None))
    parts = ["run:%s" % run_id, "content:%s" % chash]
    for d in sorted(decisions, key=lambda x: x["decision_id"]):
        keys = resolve_keys_in_run(conn, run_id, d["case_key"])
        skey = keys[0] if len(keys) == 1 else "UNRESOLVABLE"
        link = conn.execute(
            "SELECT link_status, canonical_entity_id "
            "FROM registry_projection.entity_link "
            "WHERE projection_run_id = %s AND entity_type = 'PERSON' "
            "AND source_entity_key = %s", (run_id, skey)).fetchone()
        if link is None:
            lst = ("NO-ROW", None)
        else:
            lst = (link["link_status"] if isinstance(link, dict) else link[0],
                   link["canonical_entity_id"] if isinstance(link, dict) else link[1])
        parts.append(_json.dumps([d["decision_id"], d["case_key"],
                                  d["chosen_canonical_id"],
                                  d.get("shown_packet_hash"), skey,
                                  lst[0], lst[1]], sort_keys=True, default=str))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def dry_run_preview(conn, canonical_conn, run_id: int, batch_id: str) -> dict:
    """Read-only preview of what a frozen batch would do (E3). Never applies.
    No score read, no score branching; eligibility mirrors application rules.
    """
    from . import repository as repo
    rows = conn.execute(
        "SELECT decision_id, case_key, chosen_canonical_id, shown_packet_hash "
        "FROM %s WHERE frozen_batch_id = %%s AND application_status = 'FROZEN'"
        % repo.DECISION_TABLE, (batch_id,)).fetchall()
    summary = {"approved_decisions": len(rows), "clean_applicable": 0,
               "already_same_target": 0, "stale": 0, "target_conflict": 0,
               "missing_canonical": 0, "non_person_target": 0,
               "source_no_longer_unresolved": 0, "expected_net_new_links": 0,
               "expected_unresolved_reduction": 0, "approved_review_cases": 0,
               "resolved_occurrences": 0}
    case_rows = []
    seen_cases: set = set()
    for r in rows:
        d = dict(r) if isinstance(r, dict) else dict(zip(
            ("decision_id", "case_key", "chosen_canonical_id",
             "shown_packet_hash"), r))
        status, reason = _preview_class(conn, canonical_conn, run_id, d)
        seen_cases.add(d["case_key"])
        if status == "CLEAN":
            summary["clean_applicable"] += 1
            occ = _case_occurrences(conn, d["case_key"], run_id)
            summary["resolved_occurrences"] += max(1, len(occ))
        elif status == "ALREADY_SAME_TARGET":
            summary["already_same_target"] += 1
        elif status == "TARGET_CONFLICT":
            summary["target_conflict"] += 1
        elif status == "MISSING_CANONICAL":
            summary["missing_canonical"] += 1
        elif status == "NON_PERSON_TARGET":
            summary["non_person_target"] += 1
        elif status == "SOURCE_RESOLVED":
            summary["source_no_longer_unresolved"] += 1
        else:
            summary["stale"] += 1
        if status != "CLEAN":
            case_rows.append({"decision_id": d["decision_id"],
                              "case_key": d["case_key"],
                              "chosen_canonical": d["chosen_canonical_id"],
                              "status": status, "reason": reason})
    summary["approved_review_cases"] = len(seen_cases)
    summary["expected_net_new_links"] = summary["clean_applicable"]
    summary["expected_unresolved_reduction"] = summary["resolved_occurrences"]
    decisions = [dict(r) if isinstance(r, dict) else dict(zip(
        ("decision_id", "case_key", "chosen_canonical_id",
         "shown_packet_hash"), r)) for r in rows]
    summary["state_pin"] = batch_state_pin(conn, run_id, decisions)
    return {"summary": summary, "non_clean_cases": case_rows}


def _preview_class(conn, canonical_conn, run_id: int, d: dict):
    keys = resolve_keys_in_run(conn, run_id, d["case_key"])
    if len(keys) != 1:
        return "STALE", "source identity unresolvable in target run"
    skey = keys[0]
    crow = canonical_conn.execute(
        "SELECT entity_kind FROM canonical_entity "
        "WHERE canonical_entity_id = %s", (d["chosen_canonical_id"],)).fetchone()
    kind = crow["entity_kind"] if isinstance(crow, dict) \
        else (crow[0] if crow else None)
    if kind is None:
        return "MISSING_CANONICAL", "chosen canonical does not exist"
    if kind != "PERSON":
        return "NON_PERSON_TARGET", "chosen canonical is %s" % kind
    lk = conn.execute(
        "SELECT link_status, canonical_entity_id "
        "FROM registry_projection.entity_link "
        "WHERE projection_run_id = %s AND entity_type = 'PERSON' "
        "AND source_entity_key = %s", (run_id, skey)).fetchone()
    if lk is None:
        return "STALE", "source link row absent in target run"
    st = lk["link_status"] if isinstance(lk, dict) else lk[0]
    cid = lk["canonical_entity_id"] if isinstance(lk, dict) else lk[1]
    if st == "LINKED" and cid == d["chosen_canonical_id"]:
        return "ALREADY_SAME_TARGET", "automatic link already agrees"
    if st == "LINKED":
        if cid != d["chosen_canonical_id"]:
            return "TARGET_CONFLICT", "automatic deterministic link wins"
        return "SOURCE_RESOLVED", "source already linked"
    return "CLEAN", "applicable"


def _case_occurrences(conn, case_key: str, run_id: int) -> list:
    from . import repository as repo
    case = repo.get_case(conn, case_key)
    if not case:
        return []
    refs = case.get("occurrence_refs") or []
    if isinstance(refs, str):
        import json as _json
        refs = _json.loads(refs)
    return [r for r in refs if r.get("run_id") == run_id]


def score_signals(packet: dict) -> dict:
    """Derive score input signals from a packet (deterministic, read-only)."""
    cands = packet.get("candidates", [])
    anchor = (packet.get("source") or {}).get("company_anchor")
    return {
        "packet_format": packet.get("packet_format"),
        "h_class": packet.get("h_class", "H2"),
        "alternative_count": max(0, len(cands) - 1),
        "evidence": packet.get("evidence", {"supporting": [], "contradictory": [],
                                            "missing": []}),
        "evidence_signals": {
            "normalized_name": bool((packet.get("source") or {}).get("normalized")),
            "exact_raw_name": bool(packet.get("exact_name_match", False)),
            "variant_quality": packet.get("variant_quality"),
            "independent_company": ("partial" if (anchor and cands) else None),
            "role_agreement": bool(packet.get("role_agreement", False)),
            "temporal_agreement": False,
            "official_external": False,
            "raddr_strict": False,
        },
    }


def ranked_queue(items: list[dict]) -> list[dict]:
    """Highest-value ordering: band → single-candidate → score desc → stable key.

    Band: has independent company signal first. H2/H1/H0 (no anchor/candidates)
    never interleave: they sort after all anchored cases (low-value view).
    """
    def key(it):
        sig = it.get("signals", {})
        ev = sig.get("evidence_signals", {})
        high = 0 if ev.get("independent_company") else 1
        single = 0 if sig.get("alternative_count", 99) == 0 else 1
        return (high, single, -it.get("score", 0), it.get("case_ref", ""))
    return sorted(items, key=key)


def queue_summaries(conn, run_id: int, limit: int = 10000):
    """Lightweight derived queue summaries (no packets)."""
    rows = unresolved_rows(conn, run_id, limit)
    eligible, reason_counts = classify_rows(conn, rows)
    summaries = [{
        "person_role_id": r["person_role_id"],
        "source_name": r["person_name"],
        "norm_name": r["norm_name"],
        "role": r["role_type"],
        "source_scope": r["source_scope"],
        "eligibility_kind": r["eligibility_kind"],
    } for r in eligible]
    return {"run_id": run_id, "reviewable": len(summaries),
            "unresolved_total": len(rows),
            "not_eligible": reason_counts, "cases": summaries}


def build_packet(conn, canonical_conn, run_id: int, person_role_id: int) -> dict:
    """Build (not persist) a review packet for one occurrence."""
    from heimdal.pdfhist import entity_links as el
    rows = conn.execute(
        """SELECT pr.id, pr.company_id, pr.role_type, pr.person_name,
                  pr.entity_normalized, pr.role_title, pr.source_scope,
                  pr.source_document_id, pr.source_event_id,
                  pr.source_observation_id, pr.source_parse_result_id,
                  cs.registration_number AS company_regnr
           FROM registry_projection.company_person_role pr
           LEFT JOIN registry_projection.company_current_state cs
             ON cs.company_id = pr.company_id AND cs.projection_run_id = pr.projection_run_id
           WHERE pr.projection_run_id = %s AND pr.id = %s""",
        (run_id, person_role_id)).fetchall()
    if not rows:
        raise KeyError("occurrence not in run")
    r0 = rows[0]
    r = dict(r0) if isinstance(r0, dict) else dict(zip(
        ("id", "company_id", "role_type", "person_name", "entity_normalized",
         "role_title", "source_scope", "source_document_id", "source_event_id",
         "source_observation_id", "source_parse_result_id", "company_regnr"), r0))
    normalized = el.normalize_person_name(r.get("entity_normalized") or r.get("person_name"))
    # company anchor: resolve via registration number identity map
    company_canonical = None
    if r.get("company_regnr"):
        identity_map = el.load_company_identity_map(canonical_conn)
        _s, _cid, _c, _m = el.match_company(
            normalize_registration_number(r["company_regnr"]), identity_map)
        company_canonical = _cid if _s == "LINKED" else None
    relations = el.load_person_role_relations(canonical_conn)
    names = el.load_person_display_names(canonical_conn)
    _st, _cid, cands, _m = el.match_person(
        normalized, r.get("role_type"), company_canonical, relations, names)
    return {
        "packet_format": "REVIEW-PACKET-v1",
        "run_id": run_id,
        "person_role_id": person_role_id,
        "source": {"name_exact": r.get("person_name"), "normalized": normalized,
                   "role": r.get("role_type"), "scope": r.get("source_scope"),
                   "document_id": r.get("source_document_id"),
                   "event_id": r.get("source_event_id"),
                   "observation_id": r.get("source_observation_id"),
                   "parse_id": r.get("source_parse_result_id"),
                   "company_anchor": company_canonical},
        "candidates": [{"canonical_entity_id": c["canonical_entity_id"],
                        "display_name": c.get("display_name")} for c in cands],
        "evidence": {"supporting": [], "contradictory": [], "missing": []},
    }
