#!/usr/bin/env python3
"""HEIMDAL Phase 2 — atomic observations + safe periods (plan v1.3.1 §5-§11).

Reads ONLY: pdfhist_pilot.registry_projection (pinned ACTIVE run),
heimdal.registry_ownership, heimdal.public.canonical_entity (+ public.request_attempt
for shared provenance). Hard excludes: public.canonical_relation_legacy,
public.entity_change_event (B2/B20).

Pure functions (unit-tested) + deterministic live build. Nothing outside
registry_analytics is written.
"""
import json, os, time, hashlib, datetime, sys, resource
import psycopg
from psycopg.rows import dict_row
os.environ["PGTZ"] = "UTC"  # B-8: hashes serialise timestamptz::text, so pin the session TZ

PHASE0_FILE = "docs/phase0_scale_measurement.json"
EVENT_TO_CONTEXT = {"BOARD_CHANGE": "BOARD", "MANAGEMENT_CHANGE": "MANAGEMENT"}
ROLE_TO_CONTEXT = {"BOARD": "BOARD", "MANAGEMENT": "MANAGEMENT"}
WEB_GROUP_TO_CONTEXT = {"NEVND": "BOARD", "STJORN": "MANAGEMENT"}  # B19: supplementary only
OPEN_ACTIONS = {"APPOINTED", "ADDED", "JOINED"}
CLOSE_ACTIONS = {"REMOVED", "RESIGNED", "LEFT"}
GROUP_CAP = 8  # Phase 0 decision "GROUP SIZE CAP 8: CONFIRMED"
TEMPORAL = {"period_convention": "half-open [start, end)",
            "calendar": "source-local Atlantic/Faroe", "precision": "DATE",
            "date_precedence": ["effective_date", "registration_date", "notice_date", "source_event_date"]}

# ================================ pure functions ================================
def build_phase0_pin(path=PHASE0_FILE):
    """Deterministic Phase 0 artifact pin (closure gate): SHA-256 over the EXACT
    file bytes + measured_at from the artifact JSON. Hard-fails (raises) on:
    missing/unreadable file, unparseable JSON, missing/empty measured_at,
    or any format deviation. No silent '#sha256:' is constructible."""
    import hashlib as _h, json as _json, re as _re
    try:
        blob = open(path, "rb").read()
    except OSError as e:
        raise RuntimeError(f"PHASE0 PIN REFUSED: artifact unreadable: {path}: {e}") from e
    sha = _h.sha256(blob).hexdigest()
    if not (len(sha) == 64 and _re.fullmatch(r"[0-9a-f]{64}", sha)):
        raise RuntimeError("PHASE0 PIN REFUSED: computed hash is not 64 lowercase hex chars")
    try:
        doc = _json.loads(blob)
    except ValueError as e:
        raise RuntimeError(f"PHASE0 PIN REFUSED: artifact is not valid JSON: {e}") from e
    measured_at = doc.get("measured_at") if isinstance(doc, dict) else None
    if not measured_at or not isinstance(measured_at, str) or not measured_at.strip():
        raise RuntimeError("PHASE0 PIN REFUSED: artifact has no usable measured_at")
    pin = f"{path}#sha256:{sha}"
    if not pin.endswith(sha) or not _re.fullmatch(r"\S+#sha256:[0-9a-f]{64}", pin):
        raise RuntimeError("PHASE0 PIN REFUSED: constructed pin failed format validation")
    return {"artifact_path": path, "sha256": sha, "measured_at": measured_at, "pin": pin}

def governed_date(effective, registration, notice, source_event_date):
    """B21 binding precedence: effective -> registration -> notice -> source_event_date."""
    for v in (effective, registration, notice, source_event_date):
        if v is not None:
            return v
    return None

def action_direction(event_type, action):
    """B17: OPEN/CLOSE vocabulary; anything else -> None (no coercion, warning-counted)."""
    if event_type not in EVENT_TO_CONTEXT:
        return None
    if action in OPEN_ACTIONS:
        return "OPEN"
    if action in CLOSE_ACTIONS:
        return "CLOSE"
    return None

def resolve_identity(che, anchors):
    """B12/B13. anchors = candidate historical CPR anchors already resolved to ONE
    canonical person each. 0 -> (None, 'UNRESOLVED'); >1 distinct canonical persons
    -> (None, 'AMBIGUOUS'); else the single canonical person with
    DIRECT_LINK (CHE.source_event_id == anchor.source_event_id) or
    SOURCE_LOCAL_PROPAGATION (later occurrence reusing the same LINKED anchor)."""
    if not anchors:
        return None, "UNRESOLVED"
    persons = {a["canonical_entity_id"] for a in anchors}
    if len(persons) > 1:
        return None, "AMBIGUOUS"
    direct = [a for a in anchors if a["source_event_id"] == che["source_event_id"]]
    cls = "DIRECT_LINK" if direct else "SOURCE_LOCAL_PROPAGATION"
    return persons.pop(), cls

def derive_periods(events):
    """B23-B26. events: [{date: date|None, dir: 'OPEN'|'CLOSE', key: str}] for one
    (person, company, role_context). FIFO matching; BOUNDED [start,end) for
    OPEN(Y)->CLOSE(Z) with Y<Z; CLOSE with no open and no same-day open -> CLOSED_ONLY;
    CLOSE with same-day OPEN only -> suppressed (no duration) and counted;
    leftover OPEN -> OPEN_LEFT_CENSORED. Returns (periods, same_day_suppressed, no_date)."""
    periods = []
    same_day = 0
    dated = [e for e in events if e["date"] is not None]
    open_q = []
    for ev in sorted(dated, key=lambda e: (e["date"], 0 if e["dir"] == "OPEN" else 1, e["key"])):
        if ev["dir"] not in ("OPEN", "CLOSE"):
            continue  # unknown-action events are non-temporal evidence only (B17)
        if ev["dir"] == "OPEN":
            open_q.append(ev)
        else:
            match = next((o for o in open_q if o["date"] < ev["date"]), None)
            if match is not None:
                open_q.remove(match)
                periods.append({"period_status": "BOUNDED", "period_start": match["date"],
                                "period_end": ev["date"], "start_status": "KNOWN", "end_status": "KNOWN",
                                "left_censored_start": False, "left_censored_end": False,
                                "opening_observation_key": match["key"], "closing_observation_key": ev["key"]})
            else:
                if any(o["date"] == ev["date"] for o in open_q):
                    same_day += 1
                    open_q.remove(next(o for o in open_q if o["date"] == ev["date"]))
                else:
                    periods.append({"period_status": "CLOSED_ONLY", "period_start": None,
                                    "period_end": ev["date"], "start_status": "UNKNOWN",
                                    "end_status": "KNOWN", "left_censored_start": True,
                                    "left_censored_end": False, "opening_observation_key": None,
                                    "closing_observation_key": ev["key"]})
    for o in open_q:
        periods.append({"period_status": "OPEN_LEFT_CENSORED", "period_start": o["date"],
                        "period_end": None, "start_status": "KNOWN", "end_status": "LEFT_CENSORED",
                        "left_censored_start": False, "left_censored_end": True,
                        "opening_observation_key": o["key"], "closing_observation_key": None})
    return periods, same_day, len(events) - len(dated)

def relation_endpoints(rel, canon_of):
    """C-1: registry_ownership.relation.canonical_subject/object_entity_id hold raw
    SOURCE entity ids (heimdal_collect.classify_edge), not canonical_entity_id. Map
    them like v_direct_ownership/v_control_registration do (JOIN canonical_entity ON
    source_entity_id). canon_of = {source_entity_id: canonical_entity_id}; unknown -> None."""
    return (canon_of.get(rel["canonical_subject_entity_id"]),
            canon_of.get(rel["canonical_object_entity_id"]))

def h_obs(o):
    return "|".join("NULL" if x is None else str(x) for x in (
        o["source_observation_key"], o["canonical_person_id"], o["canonical_company_id"],
        o["role_context"], o["source_family"], o["authority_class"], o["identity_resolution"],
        o["observation_kind"], o["observed_at"], o["observed_state_at"], o["action"], o["state"],
        o["ownership_percentage"], o["source_projection_run_id"], o["source_person_role_id"],
        o["source_history_event_id"], o["source_event_id"], o["source_document_id"],
        o["source_observation_id"], o["source_relation_id"], o["source_attempt_id"]))

def h_period(p):
    return "|".join("NULL" if x is None else str(x) for x in (
        p["canonical_person_id"], p["canonical_company_id"], p["role_context"],
        p["period_status"], p["period_start"], p["period_end"],
        p["opening_observation_key"], p["closing_observation_key"], p["source_family"]))

def content_hash(obs_rows, period_rows):
    """Deterministic over stable semantic columns; independent of physical order."""
    oh = hashlib.md5("\n".join(sorted(h_obs(o) for o in obs_rows)).encode()).hexdigest()
    ph = hashlib.md5("\n".join(sorted(h_period(p) for p in period_rows)).encode()).hexdigest()
    return hashlib.md5((oh + "|" + ph).encode()).hexdigest()

# ================================ live build ================================
def collect(ch, pd):
    """Collect all observations/periods/metrics from live sources (no writes)."""
    pd.execute("SET search_path = registry_projection")
    cov = {"historical_person_events_total": 0, "resolved_via_direct_link": 0,
           "resolved_via_source_local_propagation": 0, "unresolved": 0, "ambiguous": 0,
           "excluded_auditor": 0, "excluded_other_roles": 0, "unknown_membership_action_count": 0,
           "invalid_date_count": 0, "source_date_artifact_count": 0, "unresolved_current_person": 0,
           "excluded_wrong_kind": 0, "ownership_unresolved_endpoints": 0, "direct_cpr_linked_coverage": 0,
           "ownership_observations": 0, "web_observations": 0, "current_governance_observations": 0,
           "historical_observations": 0, "web_unresolved_canonical": 0}
    warnings = []
    # ---- pins (live, never hardcoded assumptions)
    run = pd.execute("""SELECT projection_run_id, projection_version, code_version, content_hash,
        status, completed_at FROM projection_run WHERE status='ACTIVE'
        ORDER BY projection_run_id DESC LIMIT 1""").fetchone()
    pin_run = run["projection_run_id"]
    own = ch.execute("""SELECT build_id, schema_version, source_relation_count, source_relation_max_id,
        max_source_attempt_id, max_collection_run_id, content_hash
        FROM registry_ownership.build_meta WHERE status='ACTIVE' ORDER BY build_id DESC LIMIT 1""").fetchone()
    assert own is not None, "no ACTIVE registry_ownership build_meta"
    own_gen_pin = f"own-build:{own['build_id']}"
    p0pin = build_phase0_pin(PHASE0_FILE)  # hard gate: raises before any write if invalid
    p0_hash = p0pin["sha256"]
    phase0_id = p0pin["pin"]
    p0_at = p0pin["measured_at"]
    link_ver = pd.execute("SELECT DISTINCT link_code_version FROM entity_link WHERE projection_run_id=%s",
                          (pin_run,)).fetchone()["link_code_version"]
    ents = ch.execute("SELECT canonical_entity_id, source_entity_id, entity_kind"
                      " FROM public.canonical_entity").fetchall()
    kind_of = {r["canonical_entity_id"]: r["entity_kind"] for r in ents}
    canon_of = {r["source_entity_id"]: r["canonical_entity_id"] for r in ents}
    assert len(canon_of) == len(ents), "source_entity_id is not unique in canonical_entity (C-1 mapping ambiguous)"
    comp_by_key = {r["source_entity_key"]: r["canonical_entity_id"] for r in pd.execute(
        """SELECT source_entity_key, canonical_entity_id FROM entity_link
           WHERE projection_run_id=%s AND entity_type='COMPANY' AND link_status='LINKED'""", (pin_run,)).fetchall()}
    person_by_cpr = {r["person_role_id"]: r["canonical_entity_id"] for r in pd.execute(
        """SELECT person_role_id, canonical_entity_id FROM entity_link
           WHERE projection_run_id=%s AND entity_type='PERSON' AND link_status='LINKED'""", (pin_run,)).fetchall()}
    date_artifact_companies = {r["company_id"] for r in pd.execute(
        """SELECT DISTINCT company_id FROM projection_warning WHERE projection_run_id=%s
           AND warning_code = 'SOURCE_DATE_ARTIFACT'""", (pin_run,)).fetchall()}
    # historical anchors: (company_id, entity_normalized, role_type) -> HISTORICAL CPR rows
    anchor_idx = {}
    for r in pd.execute("""SELECT id, company_id, role_type, entity_normalized, source_event_id
        FROM company_person_role WHERE projection_run_id=%s AND source_scope='HISTORICAL_EVENT'
        AND role_type IN ('BOARD','MANAGEMENT')""", (pin_run,)).fetchall():
        anchor_idx.setdefault((r["company_id"], r["entity_normalized"], r["role_type"]), []).append(r)
    observations = []

    # ---------- (1) PDF CURRENT governance snapshots (B7/B8): never opens periods
    snap_date = run["completed_at"].date()
    cur = pd.execute("""SELECT cpr.id, cpr.company_id, cpr.role_type, cpr.entity_normalized,
        cpr.source_document_id, cpr.source_observation_id
        FROM company_person_role cpr WHERE cpr.projection_run_id=%s
        AND cpr.source_scope='CURRENT_AUTHORITATIVE' AND cpr.role_type IN ('BOARD','MANAGEMENT')
        ORDER BY cpr.id""", (pin_run,)).fetchall()
    for r in cur:
        comp = comp_by_key.get(str(r["company_id"]))
        if comp is None or kind_of.get(comp) != "COMPANY":
            cov["excluded_wrong_kind"] += 1
            continue
        pid = person_by_cpr.get(r["id"])
        if pid is not None and kind_of.get(pid) == "PERSON":
            cov["direct_cpr_linked_coverage"] += 1
        else:
            pid = None
            cov["unresolved_current_person"] += 1
        observations.append({
            "source_observation_key": f"PDF:{pin_run}:CPR:{r['id']}",
            "canonical_person_id": pid, "canonical_company_id": comp,
            "role_context": ROLE_TO_CONTEXT[r["role_type"]], "source_family": "PDF_HISTORY",
            "authority_class": "AUTHORITATIVE_CURRENT_GOVERNANCE", "identity_resolution": None,
            "observation_kind": "CURRENT_STATE_SNAPSHOT", "observed_at": snap_date,
            "observed_state_at": None, "action": None, "state": r["role_type"],
            "ownership_percentage": None, "source_projection_run_id": pin_run,
            "source_person_role_id": r["id"], "source_history_event_id": None,
            "source_event_id": None, "source_document_id": r["source_document_id"],
            "source_observation_id": r["source_observation_id"], "source_relation_id": None,
            "source_attempt_id": None,
            "provenance": {"pdf": {"projection_run": pin_run, "cpr_id": r["id"],
                                   "source_document_id": r["source_document_id"],
                                   "source_observation_id": r["source_observation_id"],
                                   "note": "Felag current snapshot — identity anchor only; never opens a period (B8)"}}})
    current_count = len([o for o in observations if o["observation_kind"] == "CURRENT_STATE_SNAPSHOT"])

    # ---------- (2) PDF HISTORICAL CHE events (B9-B13): CHE = only counted observation
    ches = pd.execute("""SELECT id, company_id, event_type, action, entity_key, effective_date,
        registration_date, notice_date, source_event_date, source_event_id, source_document_id,
        source_observation_id FROM company_history_event
        WHERE projection_run_id=%s AND event_type IN ('BOARD_CHANGE','MANAGEMENT_CHANGE')
        ORDER BY id""", (pin_run,)).fetchall()
    cov["excluded_auditor"] = pd.execute(
        "SELECT count(*) n FROM company_history_event WHERE projection_run_id=%s AND event_type='AUDITOR_CHANGE'",
        (pin_run,)).fetchone()["n"]  # B16: auditor excluded from person analytics, warning-counted
    for c in ches:
        comp = comp_by_key.get(str(c["company_id"]))
        if comp is None or kind_of.get(comp) != "COMPANY":
            continue
        direction = action_direction(c["event_type"], c["action"])
        gdate = governed_date(c["effective_date"], c["registration_date"], c["notice_date"], c["source_event_date"])
        if direction is None:
            # unknown membership action (B17): remains non-temporal evidence in CHE source
            cov["unknown_membership_action_count"] += 1
            continue
        cov["historical_person_events_total"] += 1
        if gdate is None:
            cov["invalid_date_count"] += 1
        if c["company_id"] in date_artifact_companies:
            cov["source_date_artifact_count"] += 1
        cands = [{"id": a["id"], "source_event_id": a["source_event_id"],
                  "canonical_entity_id": person_by_cpr[a["id"]]}
                 for a in anchor_idx.get((c["company_id"], c["entity_key"], EVENT_TO_CONTEXT[c["event_type"]]), [])
                 if a["id"] in person_by_cpr and kind_of.get(person_by_cpr[a["id"]]) == "PERSON"]
        pid, cls = resolve_identity(c, cands)
        if pid is None:
            if cls == "AMBIGUOUS":
                cov["ambiguous"] += 1
                warnings.append({"code": "AMBIGUOUS_IDENTITY", "che_id": c["id"],
                                 "company_id": c["company_id"], "entity_key": c["entity_key"]})
            else:
                cov["unresolved"] += 1
            continue
        anchor = next(a for a in cands if a["canonical_entity_id"] == pid)
        obs = {
            "source_observation_key": f"PDF:{pin_run}:CHE:{c['id']}",
            "canonical_person_id": pid, "canonical_company_id": comp,
            "role_context": EVENT_TO_CONTEXT[c["event_type"]], "source_family": "PDF_HISTORY",
            "authority_class": "HISTORICAL_PDF_OBSERVATION", "identity_resolution": cls,
            "observation_kind": "APPOINTED_EVENT" if direction == "OPEN" else "REMOVED_EVENT",
            "observed_at": gdate, "observed_state_at": None,
            "action": c["action"], "state": None, "ownership_percentage": None,
            "source_projection_run_id": pin_run, "source_person_role_id": anchor["id"],
            "source_history_event_id": c["id"], "source_event_id": c["source_event_id"],
            "source_document_id": c["source_document_id"], "source_observation_id": c["source_observation_id"],
            "source_relation_id": None, "source_attempt_id": None,
            "provenance": {"pdf": {"projection_run": pin_run, "che_id": c["id"],
                                   "source_event_id": c["source_event_id"],
                                   "source_document_id": c["source_document_id"],
                                   "source_observation_id": c["source_observation_id"],
                                   "anchor_cpr_id": anchor["id"],
                                   "anchor_source_event_id": anchor["source_event_id"],
                                   "governed_date_source": "precedence helper"}}}
        observations.append(obs)
        cov["resolved_via_direct_link" if cls == "DIRECT_LINK" else "resolved_via_source_local_propagation"] += 1
    hist_count = len([o for o in observations if o["source_family"] == "PDF_HISTORY"
                      and o["observation_kind"] in ("APPOINTED_EVENT", "REMOVED_EVENT")])
    cov["historical_observations"] = hist_count
    cov["current_governance_observations"] = current_count

    # ---------- (3) OWNERSHIP + WEB GOVERNANCE point observations (B18/B19)
    rel_rows = ch.execute("""SELECT r.relation_id, r.raw_from_source_entity_id, r.raw_to_source_entity_id,
        r.canonical_subject_entity_id, r.canonical_object_entity_id, r.canonical_relation_type,
        r.inferred_group, r.parsed_percentage, r.source_attempt_id, r.first_stored_at
        FROM registry_ownership.relation r""").fetchall()
    ev = {r["relation_id"]: r for r in ch.execute(
        """SELECT relation_id, response_sha256, collection_run_id
           FROM registry_ownership.relation_evidence_pointer""").fetchall()}
    for r in rel_rows:
        subj, obj = relation_endpoints(r, canon_of)
        prov = {"ownership": {"relation_id": r["relation_id"], "source_attempt_id": r["source_attempt_id"],
                              "response_sha256": (ev[r["relation_id"]]["response_sha256"] if r["relation_id"] in ev else None),
                              "collection_run_id": (ev[r["relation_id"]]["collection_run_id"] if r["relation_id"] in ev else None)},
                "shared_acquisition": {"request_attempt": "public.request_attempt"}}
        obs_date = r["first_stored_at"].date() if r["first_stored_at"] else None
        if r["inferred_group"] in ("NEVND", "STJORN") and (subj is None or obj is None):
            # web governance rows with unresolved canonical endpoints: fail-closed (B19)
            cov["web_unresolved_canonical"] += 1
            continue
        if r["canonical_relation_type"] in ("DIRECT_OWNERSHIP", "REGISTERED_CONTROL_ACTOR"):
            if subj is None or obj is None:
                cov["ownership_unresolved_endpoints"] = cov.get("ownership_unresolved_endpoints", 0) + 1
                continue
            if kind_of.get(subj) != "PERSON" or kind_of.get(obj) != "COMPANY":
                cov["excluded_wrong_kind"] += 1
                if len([w for w in warnings if w["code"] == "KIND_GATE_EXCLUDED"]) < 5:
                    warnings.append({"code": "KIND_GATE_EXCLUDED",
                                     "relation_id": r["relation_id"],
                                     "subject_kind": kind_of.get(subj), "object_kind": kind_of.get(obj)})
                continue
            ctx = "OWNERSHIP" if r["canonical_relation_type"] == "DIRECT_OWNERSHIP" else "CONTROL_ACTOR"
            observations.append({
                "source_observation_key": f"OWN:{own_gen_pin}:{r['relation_id']}:{r['source_attempt_id']}",
                "canonical_person_id": subj, "canonical_company_id": obj, "role_context": ctx,
                "source_family": "OWNERSHIP_GRAPH", "authority_class": "OWNERSHIP_POINT_OBSERVATION",
                "identity_resolution": None,
                "observation_kind": "OWNERSHIP_SNAPSHOT_POINT",
                "observed_at": obs_date, "observed_state_at": obs_date,
                "action": None, "state": r["canonical_relation_type"],
                "ownership_percentage": (r["parsed_percentage"] if ctx == "OWNERSHIP" else None),
                "source_projection_run_id": None, "source_person_role_id": None,
                "source_history_event_id": None, "source_event_id": None,
                "source_document_id": None, "source_observation_id": None,
                "source_relation_id": r["relation_id"], "source_attempt_id": r["source_attempt_id"],
                "provenance": prov})
        elif r["inferred_group"] in WEB_GROUP_TO_CONTEXT and kind_of.get(subj) == "PERSON" \
                and obj is not None and kind_of.get(obj) == "COMPANY":
            observations.append({
                "source_observation_key": f"WEBGOV:{own_gen_pin}:{r['relation_id']}:{r['source_attempt_id']}",
                "canonical_person_id": subj, "canonical_company_id": obj,
                "role_context": WEB_GROUP_TO_CONTEXT[r["inferred_group"]],
                "source_family": "OWNERSHIP_WEB_GOVERNANCE",
                "authority_class": "SUPPLEMENTARY_WEB_GOVERNANCE_OBSERVATION",
                "identity_resolution": None, "observation_kind": "WEB_GOVERNANCE_SNAPSHOT_POINT",
                "observed_at": obs_date, "observed_state_at": obs_date,
                "action": None, "state": r["inferred_group"], "ownership_percentage": None,
                "source_projection_run_id": None, "source_person_role_id": None,
                "source_history_event_id": None, "source_event_id": None,
                "source_document_id": None, "source_observation_id": None,
                "source_relation_id": r["relation_id"], "source_attempt_id": r["source_attempt_id"],
                "provenance": prov})
        elif r["inferred_group"] in ("EIGARAR", "ANNAD"):
            cov["excluded_other_roles"] += 1
    cov["ownership_observations"] = len([o for o in observations if o["source_family"] == "OWNERSHIP_GRAPH"])
    cov["web_governance_observations"] = len([o for o in observations if o["source_family"] == "OWNERSHIP_WEB_GOVERNANCE"])

    # ---------- (4) provenance validation (B32): every PDF obs resolves to pinned source
    pdf_chez = {c["id"] for c in ches}
    pdf_cpr_ids = set()
    for v in anchor_idx.values():
        pdf_cpr_ids.update(a["id"] for a in v)
    prov_ok = True
    for o in observations:
        if o["source_family"] == "PDF_HISTORY":
            che = o["source_history_event_id"]
            if (o["observation_kind"] in ("APPOINTED_EVENT", "REMOVED_EVENT") and che not in pdf_chez) \
                    or (o["observation_kind"] == "CURRENT_STATE_SNAPSHOT" and o["source_projection_run_id"] != pin_run):
                prov_ok = False
    cov["provenance_pdf_validated"] = prov_ok

    # ---------- (5) period derivation (B23-B27): resolved PDF_HISTORY events ONLY.
    # Felag current snapshots, ownership and web points NEVER open periods (B8/B18/B19).
    per_groups = {}
    for o in observations:
        if o["source_family"] != "PDF_HISTORY" or o["canonical_person_id"] is None:
            continue
        d = ("OPEN" if o["observation_kind"] == "APPOINTED_EVENT"
             else "CLOSE" if o["observation_kind"] == "REMOVED_EVENT" else None)
        if d is None:
            continue
        key = (o["canonical_person_id"], o["canonical_company_id"], o["role_context"])
        per_groups.setdefault(key, []).append(
            {"date": o["observed_at"], "dir": d, "key": o["source_observation_key"]})
    periods = []
    same_day_total = 0
    for (pid, cid, ctx), evs in sorted(per_groups.items()):
        derived, sd, nd = derive_periods(evs)
        same_day_total += sd
        for p in derived:
            p.update({"canonical_person_id": pid, "canonical_company_id": cid,
                      "role_context": ctx, "source_family": "PDF_HISTORY"})
            periods.append(p)
        if sd:
            warnings.append({"code": "SAME_DAY_UNORDERED_NO_DURATION",
                             "canonical_person_id": pid, "canonical_company_id": cid,
                             "role_context": ctx, "pairs": sd})
    cov["same_day_suppressed"] = same_day_total
    return {"pin_run": pin_run, "run": run, "own": own, "own_gen_pin": own_gen_pin,
            "link_ver": link_ver, "phase0_id": phase0_id, "p0_hash": p0_hash, "p0_at": p0_at,
            "phase0_artifact_path": p0pin["artifact_path"],
            "kind_of": kind_of, "cov": cov, "warnings": warnings, "observations": observations,
            "periods": periods}

def persist(ch, pd, B, tag=""):
    """Single deterministic build pass with safe lifecycle:
    insert analytics_build (FAILED) -> observations -> periods -> validate ->
    content hash -> set ACTIVE, supersede previous ACTIVE. Commit only on success."""
    import re as _re
    # ---- hard application gate (closure): pin must be byte-derived and well-formed
    # BEFORE any DB write; an empty/truncated '#sha256:' can never become ACTIVE.
    pin = B.get("phase0_id", "")
    if not _re.fullmatch(r"\S+#sha256:[0-9a-f]{64}", pin):
        raise RuntimeError(f"PHASE0 PIN REFUSED before insert: malformed pin: {pin!r}")
    live = build_phase0_pin(B.get("phase0_artifact_path", PHASE0_FILE))
    if live["pin"] != pin or live["measured_at"] != B.get("p0_at"):
        raise RuntimeError("PHASE0 PIN REFUSED before insert: pin does not match current artifact bytes")
    t0 = time.perf_counter()
    cur = ch.cursor()
    cur.execute("SET default_transaction_read_only = off")
    try:
        cur.execute("""INSERT INTO registry_analytics.analytics_build
            (source_projection_run_id, pdf_link_code_version, ownership_source_meta, canonical_identity_pin,
             temporal_conventions, group_support_policy, group_size_cap, phase0_measurement_id,
             phase0_measured_at, build_warnings, coverage, content_hash, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,'FAILED') RETURNING build_run_id""",
            (B["pin_run"], B["link_ver"],
             json.dumps({"build_meta_id": B["own"]["build_id"], "schema_version": B["own"]["schema_version"],
                         "source_relation_count": B["own"]["source_relation_count"],
                         "source_relation_max_id": B["own"]["source_relation_max_id"],
                         "content_hash": B["own"]["content_hash"],
                         "generation_pin": B["own_gen_pin"]}),
             json.dumps({"policy": "referenced canonical entity ids + kinds pinned per build",
                         "entity_count": len({o["canonical_person_id"] for o in B["observations"] if o["canonical_person_id"]})
                                         + len({o["canonical_company_id"] for o in B["observations"]})}),
             json.dumps(TEMPORAL), json.dumps({"supported_pairs_min": 2, "supported_episodes_min": 2}),
             GROUP_CAP, B["phase0_id"], B["p0_at"], json.dumps(B["warnings"], default=str), json.dumps(B["cov"])))
        build_run_id = cur.fetchone()["build_run_id"]
        ch.commit()
    except Exception as e:
        ch.rollback()
        raise
    # ---------- insert observations (batched)
    obs_cols = ["build_run_id", "source_observation_key", "canonical_person_id", "canonical_company_id",
                "role_context", "source_family", "authority_class", "identity_resolution",
                "observation_kind", "observed_at", "observed_state_at", "action", "state",
                "ownership_percentage", "source_projection_run_id", "source_person_role_id",
                "source_history_event_id", "source_event_id", "source_document_id",
                "source_observation_id", "source_relation_id", "source_attempt_id", "provenance"]
    try:
        cur.execute("SET default_transaction_read_only = off")
        rows = []
        for o in sorted(B["observations"], key=lambda x: x["source_observation_key"]):
            rows.append([build_run_id] + [o[k] for k in obs_cols[1:22]] + [json.dumps(o["provenance"], default=str)])
        ph = ",".join(["%s"] * 23)
        cur.executemany(f"""INSERT INTO registry_analytics.person_company_observation
            ({','.join(obs_cols)}) VALUES ({ph})""", rows)
        # ---------- periods
        per_rows = []
        for p in sorted(B["periods"], key=lambda p: (p["canonical_person_id"], p["canonical_company_id"],
                                                     p["role_context"], str(p["period_start"]), p["opening_observation_key"] or "")):
            per_rows.append([build_run_id, p["canonical_person_id"], p["canonical_company_id"],
                             p["role_context"], p["period_start"], p["period_end"],
                             p["start_status"], p["end_status"], p["left_censored_start"],
                             p["left_censored_end"], p["period_status"], p["opening_observation_key"],
                             p["closing_observation_key"], p["source_family"]])
        cur.executemany("""INSERT INTO registry_analytics.person_company_period
            (build_run_id, canonical_person_id, canonical_company_id, role_context, period_start,
             period_end, start_status, end_status, left_censored_start, left_censored_end,
             period_status, opening_observation_key, closing_observation_key, source_family)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", per_rows)
        ch.commit()
    except Exception:
        ch.rollback()
        raise
    # ---------- gates (post-insert validation, B32/B33)
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
    bad = {k: v for k, v in gates.items() if k != "n_obs" and (v or 0) != 0}
    if bad:
        cur.execute("""UPDATE registry_analytics.analytics_build SET status='FAILED', content_hash=NULL,
            coverage=%s WHERE build_run_id=%s""",
                    (json.dumps({"gate_failures": bad, "warnings": B["warnings"]}, default=str), build_run_id))
        ch.commit()
        raise RuntimeError(f"phase2 build gate failure: {bad}")
    hh = content_hash(B["observations"], B["periods"])
    cur.execute("UPDATE registry_analytics.analytics_build SET status='SUPERSEDED' WHERE status='ACTIVE'")
    cur.execute("""UPDATE registry_analytics.analytics_build SET status='ACTIVE', content_hash=%s,
        coverage=%s WHERE build_run_id=%s""", (hh, json.dumps(B["cov"]), build_run_id))
    ch.commit()
    return {"build_run_id": build_run_id, "n_obs": len(B["observations"]),
            "n_periods": len(B["periods"]), "content_hash": hh, "cov": B["cov"], "gates": bad}

def env_dsn(var):
    """DSN from the environment, required (A-15). Resolved at connect time, not
    import time: the phase2/phase3 unit tests import this module offline."""
    return os.environ.get(var) or sys.exit(f"{var} is not set (no default: a forgotten variable must never reach a live DB)")


def main():
    t0 = time.perf_counter()
    ch = psycopg.connect(env_dsn("HEIMDAL_DB"), row_factory=dict_row)
    pd = psycopg.connect(env_dsn("HEIMDAL_PDFHIST_PROJECTION_DSN"), row_factory=dict_row)
    B = collect(ch, pd)
    print(f"pinned projection run {B['pin_run']} (code {B['run']['code_version']})")
    print(f"observations: {len(B['observations'])} | periods: {len(B['periods'])}")
    cov = B["cov"]
    print(f"identity: DIRECT={cov['resolved_via_direct_link']} PROP={cov['resolved_via_source_local_propagation']} "
          f"UNRESOLVED={cov['unresolved']} AMBIGUOUS={cov['ambiguous']}")
    res = persist(ch, pd, B)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    out = {"build_run_id": res["build_run_id"], "n_obs": res["n_obs"], "n_periods": res["n_periods"],
           "content_hash": res["content_hash"], "cov": cov, "pins": {
               "projection_run": B["pin_run"], "projection_code_version": B["run"]["code_version"],
               "projection_content_hash": B["run"]["content_hash"],
               "ownership_generation_pin": B["own_gen_pin"], "pdf_link_code_version": B["link_ver"],
               "phase0_measurement_id": B["phase0_id"], "phase0_measured_at": B["p0_at"]}}
    json.dump(out, open("tools/phase2_build_state.json", "w"), indent=1, default=str)
    print(f"ACTIVE build_run_id={res['build_run_id']} content_hash={res['content_hash']} "
          f"rss={rss:.0f}MB elapsed={time.perf_counter()-t0:.1f}s")
    print("WROTE tools/phase2_build_state.json")

if __name__ == "__main__":
    main()