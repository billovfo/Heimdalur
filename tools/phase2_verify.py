#!/usr/bin/env python3
"""Phase 2 live verification: identity reconciliation, authority gates, CHE/CPR
dedupe, E1/E2/E3 live instance, provenance samples, period audits, no-legacy.
Emits tools/phase2_verify_state.json."""
import json, os, sys
import psycopg
from psycopg.rows import dict_row
os.environ["PGTZ"] = "UTC"  # B-8: hashes serialise timestamptz::text, so pin the session TZ

fails, checks = [], []
def check(name, ok, detail=""):
    (checks if ok else fails).append({"check": name, "ok": bool(ok), "detail": detail})
    print(("  ok  " if ok else "  FAIL ") + name + (f" :: {detail}" if detail else ""))

ch = psycopg.connect(os.environ.get("HEIMDAL_DB") or sys.exit("HEIMDAL_DB is not set (no default: a forgotten variable must never reach a live DB)"), row_factory=dict_row)
pd = psycopg.connect(os.environ.get("HEIMDAL_PDFHIST_PROJECTION_DSN")
                     or sys.exit("HEIMDAL_PDFHIST_PROJECTION_DSN is not set (no default: a forgotten variable must never reach a live DB)"), row_factory=dict_row)
pd.execute("SET search_path = registry_projection")
B = ch.execute("""SELECT * FROM registry_analytics.analytics_build WHERE status='ACTIVE'
    ORDER BY build_run_id DESC LIMIT 1""").fetchone()
bid = B["build_run_id"]
pin = str(B["source_projection_run_id"])
print(f"verifying ACTIVE build {bid} (pinned run {pin}, hash {B['content_hash']})")

# ---- identity reconciliation vs the measured baseline of the pinned projection run
# (H-6: the constants were pinned to run 4 only; each run gets its own source)
#   run 4  = Phase 0, docs/phase0_scale_measurement.json (2026-09-01T16:21Z): 10,609
#            eligible CHE events, direct 0.2685 / propagation 0.0598 of them (builds 8-13)
#   run 33 = the recovered pilot (pdfhist_pilot_recovered_20260904, ACTIVE f80d8f36...),
#            measured by tools/phase2_build.py on a heimdal_recovered_20260904 clone
#            (lane W6a, 2026-11-06): same CHE total, more anchors resolve
IDENTITY_BASELINE = {4: {"che_total": 10609, "direct": 2849, "propagation": 634},
                     33: {"che_total": 10609, "direct": 3291, "propagation": 931}}
cov = B["coverage"]
exp = IDENTITY_BASELINE.get(int(pin))
check(f"identity baseline recorded for pinned run {pin}", exp is not None, "add it to IDENTITY_BASELINE")
if exp:
    check(f"historical CHE total == run {pin} baseline ({exp['che_total']})",
          cov["historical_person_events_total"] == exp["che_total"], str(cov["historical_person_events_total"]))
    check(f"DIRECT_LINK == run {pin} baseline ({exp['direct']})",
          cov["resolved_via_direct_link"] == exp["direct"], str(cov["resolved_via_direct_link"]))
    check(f"PROPAGATION == run {pin} baseline ({exp['propagation']})",
          cov["resolved_via_source_local_propagation"] == exp["propagation"],
          str(cov["resolved_via_source_local_propagation"]))
combined = cov["resolved_via_direct_link"] + cov["resolved_via_source_local_propagation"]
check("direct + propagation + unresolved + ambiguous == historical CHE total",
      combined + cov["unresolved"] + cov["ambiguous"] == cov["historical_person_events_total"],
      str({k: cov[k] for k in ("historical_person_events_total", "unresolved", "ambiguous")}))
check("ambiguous == 0 (fail-closed)", cov["ambiguous"] == 0)

# ---- authority / kind gates
r = ch.execute("""SELECT
  count(*) FILTER (WHERE authority_class='AUTHORITATIVE_CURRENT_GOVERNANCE' AND (source_family<>'PDF_HISTORY' OR observation_kind<>'CURRENT_STATE_SNAPSHOT')) AS a1,
  count(*) FILTER (WHERE authority_class='OWNERSHIP_POINT_OBSERVATION' AND source_family<>'OWNERSHIP_GRAPH') AS a2,
  count(*) FILTER (WHERE authority_class='SUPPLEMENTARY_WEB_GOVERNANCE_OBSERVATION' AND source_family<>'OWNERSHIP_WEB_GOVERNANCE') AS a3,
  count(*) FILTER (WHERE role_context='CONTROL_ACTOR' AND ownership_percentage IS NOT NULL) AS a4,
  count(*) FILTER (WHERE identity_resolution='SOURCE_LOCAL_PROPAGATION' AND observation_kind NOT IN ('APPOINTED_EVENT','REMOVED_EVENT')) AS a5
  FROM registry_analytics.person_company_observation WHERE build_run_id=%s""", (bid,)).fetchone()
check("authority/kind gates hold", all(v == 0 for v in r.values()), str(dict(r)))

# ---- CHE-only counted events (B9/N-02)
r = ch.execute("""SELECT count(*) FROM registry_analytics.person_company_observation
  WHERE build_run_id=%s AND observation_kind IN ('APPOINTED_EVENT','REMOVED_EVENT')
  AND (source_observation_key NOT LIKE 'PDF:' || %s || ':CHE:%%' OR source_history_event_id IS NULL)""",
  (bid, pin)).fetchone()
check("historical observations CHE-keyed only", r["count"] == 0)
n_hist = ch.execute("""SELECT count(*) n FROM registry_analytics.person_company_observation
  WHERE build_run_id=%s AND observation_kind IN ('APPOINTED_EVENT','REMOVED_EVENT')""", (bid,)).fetchone()
check(f"resolved historical observations == direct + propagation ({combined})",
      n_hist["n"] == combined == cov["historical_observations"], str(n_hist["n"]))

# ---- period status shapes
stat = ch.execute("""SELECT period_status, count(*) n FROM registry_analytics.person_company_period
  WHERE build_run_id=%s GROUP BY 1 ORDER BY 1""", (bid,)).fetchall()
smap = {x["period_status"]: x["n"] for x in stat}
check("period status vocabulary", set(smap) <= {"BOUNDED", "OPEN_LEFT_CENSORED", "CLOSED_ONLY"}, str(smap))
r = ch.execute("""SELECT
  count(*) FILTER (WHERE period_status='BOUNDED' AND (period_start IS NULL OR period_end IS NULL OR period_start >= period_end OR left_censored_start OR left_censored_end)) AS bad_bounded,
  count(*) FILTER (WHERE period_status='OPEN_LEFT_CENSORED' AND (period_start IS NULL OR period_end IS NOT NULL OR NOT left_censored_end)) AS bad_olc,
  count(*) FILTER (WHERE period_status='CLOSED_ONLY' AND (period_start IS NOT NULL OR period_end IS NULL OR NOT left_censored_start)) AS bad_co
  FROM registry_analytics.person_company_period WHERE build_run_id=%s""", (bid,)).fetchone()
check("period status shapes", all(v == 0 for v in r.values()), str(dict(r)))

# ---- E1/E2/E3 live discovery
seq = ch.execute("""SELECT canonical_person_id, canonical_company_id, role_context,
  count(*) FILTER (WHERE period_status='BOUNDED') AS bounded,
  count(*) FILTER (WHERE period_status='OPEN_LEFT_CENSORED') AS open_left
  FROM registry_analytics.person_company_period WHERE build_run_id=%s
  GROUP BY 1,2,3 HAVING count(*) FILTER (WHERE period_status='BOUNDED') >= 2
   AND count(*) FILTER (WHERE period_status='OPEN_LEFT_CENSORED') >= 1 LIMIT 1""", (bid,)).fetchone()
check("E1/E2/E3 live instance exists (re-appointed person)", seq is not None)
if seq is not None:
    obs = ch.execute("""SELECT observation_kind FROM registry_analytics.person_company_observation
      WHERE build_run_id=%s AND canonical_person_id=%s AND canonical_company_id=%s AND role_context=%s
        AND observation_kind IN ('APPOINTED_EVENT','REMOVED_EVENT')
      ORDER BY observed_at, source_observation_key""",
      (bid, seq["canonical_person_id"], seq["canonical_company_id"], seq["role_context"])).fetchall()
    kinds = [o["observation_kind"] for o in obs]
    opens = kinds.count("APPOINTED_EVENT")
    closes = kinds.count("REMOVED_EVENT")
    check("A/R/A pattern present in live sequence", kinds[0] == "APPOINTED_EVENT" and closes >= 1 and opens >= 2,
          f"person={seq['canonical_person_id']} company={seq['canonical_company_id']} n={len(kinds)}")
    row = ch.execute("""SELECT count(*) n FROM registry_analytics.person_company_period
      WHERE build_run_id=%s AND canonical_person_id=%s AND canonical_company_id=%s AND role_context=%s""",
      (bid, seq["canonical_person_id"], seq["canonical_company_id"], seq["role_context"])).fetchone()
    expected = opens - 1 + (1 if opens > closes else 0)
    check("E3 periods == opens-1 (last appointment open) for this live group",
          row["n"] == expected, f"periods={row['n']} expected={expected} opens={opens} closes={closes}")

# ---- provenance samples (10 per family)
samples = ch.execute("""SELECT * FROM registry_analytics.person_company_observation
  WHERE build_run_id=%s AND observation_kind IN ('APPOINTED_EVENT','REMOVED_EVENT')
  ORDER BY observation_id LIMIT 10""", (bid,)).fetchall()
bad = sum(1 for s in samples if pd.execute(
    "SELECT source_document_id FROM company_history_event WHERE id=%s AND projection_run_id=%s",
    (s["source_history_event_id"], int(pin))).fetchone() is None)
check("PDF historical provenance 10-sample -> pinned CHE", bad == 0)
samples = ch.execute("""SELECT * FROM registry_analytics.person_company_observation
  WHERE build_run_id=%s AND observation_kind='CURRENT_STATE_SNAPSHOT'
  ORDER BY observation_id LIMIT 10""", (bid,)).fetchall()
bad = sum(1 for s in samples if pd.execute(
    """SELECT id FROM company_person_role WHERE id=%s AND projection_run_id=%s AND source_scope='CURRENT_AUTHORITATIVE'""",
    (s["source_person_role_id"], int(pin))).fetchone() is None)
check("PDF current provenance 10-sample -> pinned CPR", bad == 0)
own_s = ch.execute("""SELECT * FROM registry_analytics.person_company_observation
  WHERE build_run_id=%s AND source_family='OWNERSHIP_GRAPH' ORDER BY observation_id LIMIT 10""",
  (bid,)).fetchall()
bad = 0
for s in own_s:
    ra = ch.execute("SELECT 1 FROM public.request_attempt WHERE attempt_id=%s", (s["source_attempt_id"],)).fetchone()
    rel = ch.execute("SELECT 1 FROM registry_ownership.relation WHERE relation_id=%s", (s["source_relation_id"],)).fetchone()
    if ra is None or rel is None:
        bad += 1
check("ownership provenance 10-sample (relation + request_attempt)", bad == 0)
# C-1 (audit 2026-11): every ownership observation names its relation's own endpoints,
# as the portal views resolve them. Build 8 failed this for 817/817 rows.
r = ch.execute("""SELECT count(*) n FROM registry_analytics.person_company_observation o
  LEFT JOIN v_direct_ownership d ON d.relation_id = o.source_relation_id
  LEFT JOIN v_control_registration k ON k.relation_id = o.source_relation_id
  WHERE o.build_run_id=%s AND o.source_family='OWNERSHIP_GRAPH' AND NOT (
    (o.canonical_person_id, o.canonical_company_id) IS NOT DISTINCT FROM (d.owner_id, d.owned_id)
    OR (o.canonical_person_id, o.canonical_company_id) IS NOT DISTINCT FROM (k.actor_id, k.registered_id))""",
  (bid,)).fetchone()
check("C-1 ownership observations name their relation's endpoints (v_direct_ownership/v_control_registration)",
      r["n"] == 0, f"{r['n']} wrong")
per_samples = ch.execute("""SELECT * FROM registry_analytics.person_company_period
  WHERE build_run_id=%s AND period_status='BOUNDED' ORDER BY period_id LIMIT 10""", (bid,)).fetchall()
bad = 0
for p in per_samples:
    o1 = ch.execute("""SELECT observed_at, observation_kind FROM registry_analytics.person_company_observation
      WHERE build_run_id=%s AND source_observation_key=%s""", (bid, p["opening_observation_key"])).fetchone()
    o2 = ch.execute("""SELECT observed_at, observation_kind FROM registry_analytics.person_company_observation
      WHERE build_run_id=%s AND source_observation_key=%s""", (bid, p["closing_observation_key"])).fetchone()
    if (o1 is None or o2 is None or o1["observed_at"] != p["period_start"] or o2["observed_at"] != p["period_end"]
            or o1["observation_kind"] != "APPOINTED_EVENT" or o2["observation_kind"] != "REMOVED_EVENT"
            or not p["period_start"] < p["period_end"]):
        bad += 1
check("BOUNDED period 10-sample (keys resolve, APPOINTED->REMOVED, start<end)", bad == 0)

# ---- authority isolation: ownership/web points never open or close periods
r = ch.execute("""SELECT count(*) n FROM registry_analytics.person_company_period
  WHERE build_run_id=%s AND (opening_observation_key LIKE 'OWN:%%' OR opening_observation_key LIKE 'WEBGOV:%%'
    OR closing_observation_key LIKE 'OWN:%%' OR closing_observation_key LIKE 'WEBGOV:%%')""", (bid,)).fetchone()
check("ownership/web points never open or close periods", r["n"] == 0)

# ---- no legacy leak
r = ch.execute("""SELECT count(*) n FROM registry_analytics.person_company_observation
  WHERE build_run_id=%s AND provenance::text ILIKE '%%entity_change_event%%'""", (bid,)).fetchone()
check("no entity_change_event provenance", r["n"] == 0)

json.dump({"build_run_id": bid, "n_checks": len(checks), "failures": fails},
          open("tools/phase2_verify_state.json", "w"), indent=1)
print(f"FAILS: {fails or 'none'} ({len(checks)} checks passed)")
sys.exit(1 if fails else 0)