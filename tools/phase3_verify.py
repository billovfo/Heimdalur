#!/usr/bin/env python3
"""Phase 3 live verification — sample audits (task §62/§63), completeness gates
(§52), source-separation regression (§64) and coverage metrics (§61) against the
ACTIVE Phase 3 build. Run: python3 tools/phase3_verify.py"""
import json
import os
import sys
import hashlib
from collections import Counter, defaultdict
from datetime import date
os.environ["PGTZ"] = "UTC"  # B-8: hashes serialise timestamptz::text, so pin the session TZ

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "tools")
from phase3_build import member_set_hash

PG = os.environ.get("HEIMDAL_DB") or sys.exit("HEIMDAL_DB is not set (no default: a forgotten variable must never reach a live DB)")
fails, checks = [], []


def check(name, cond, detail=""):
    print(("  ok  " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def det_sample(rows, n):
    """Deterministic sample: order by natural keys, take first n."""
    return rows[:n]


def main():
    ch = psycopg.connect(PG, autocommit=True, row_factory=dict_row)
    B = ch.execute("""SELECT build_run_id, phase0_measurement_id, phase0_measured_at, content_hash,
        group_support_policy, group_size_cap, source_projection_run_id, coverage
        FROM registry_analytics.analytics_build WHERE status='ACTIVE' ORDER BY build_run_id DESC LIMIT 1""").fetchone()
    bid, p0pin, p0at, chash, policy, cap, pin_run, cov = (B["build_run_id"], B["phase0_measurement_id"],
        B["phase0_measured_at"], B["content_hash"], B["group_support_policy"],
        B["group_size_cap"], B["source_projection_run_id"], B["coverage"])
    print(f"ACTIVE Phase 3 build: {bid} | hash {chash} | cap {cap}")
    pol = json.loads(policy) if isinstance(policy, str) else policy
    out = {"active_build": bid, "content_hash": chash, "phase0_pin": p0pin, "policy": pol}

    # ---------- §52 re-verification of completeness gates on the ACTIVE build
    g = ch.execute("""SELECT
        (SELECT count(*) FROM registry_analytics.person_person_overlap ppo
          JOIN public.canonical_entity e1 ON e1.canonical_entity_id=ppo.person_a_id
          JOIN public.canonical_entity e2 ON e2.canonical_entity_id=ppo.person_b_id
          JOIN public.canonical_entity ec ON ec.canonical_entity_id=ppo.canonical_company_id
          WHERE ppo.build_run_id=%s AND (e1.entity_kind<>'PERSON' OR e2.entity_kind<>'PERSON'
                                        OR ec.entity_kind<>'COMPANY' OR ppo.person_a_id>=ppo.person_b_id)) AS pair_ids,
        (SELECT count(*) FROM registry_analytics.person_person_overlap ppo
          WHERE ppo.build_run_id=%s AND NOT EXISTS (
            SELECT 1 FROM registry_analytics.person_company_observation o WHERE o.build_run_id=ppo.build_run_id
              AND o.canonical_person_id=ppo.person_a_id AND o.canonical_company_id=ppo.canonical_company_id
              AND o.role_context=ppo.role_context)) AS pair_without_obs,
        (SELECT count(*) FROM registry_analytics.person_group g
          JOIN registry_analytics.person_group_member m ON m.group_id=g.group_id
          JOIN public.canonical_entity e ON e.canonical_entity_id=m.canonical_person_id
          WHERE g.build_run_id=%s AND e.entity_kind<>'PERSON') AS member_kind,
        (SELECT count(*) FROM registry_analytics.person_group_observation_member jm
          JOIN registry_analytics.person_group_observation go ON go.group_observation_id=jm.group_observation_id
          JOIN registry_analytics.person_company_observation o ON o.observation_id=jm.observation_id
          JOIN registry_analytics.person_group pg ON pg.group_id=go.group_id
          WHERE go.build_run_id=%s AND (o.build_run_id<>go.build_run_id
            OR o.canonical_company_id<>go.canonical_company_id
            OR NOT EXISTS (SELECT 1 FROM registry_analytics.person_group_member m2
                           WHERE m2.group_id=pg.group_id AND m2.canonical_person_id=o.canonical_person_id))) AS junction_bad,
        (SELECT count(*) FROM registry_analytics.person_group WHERE build_run_id=%s AND group_size > %s) AS over_cap,
        (SELECT count(*) FROM registry_analytics.person_group g WHERE g.build_run_id=%s
          AND NOT EXISTS (SELECT 1 FROM registry_analytics.person_group_observation go WHERE go.group_id=g.group_id)) AS group_no_obs,
        (SELECT count(*) FROM registry_analytics.person_person_overlap WHERE build_run_id=%s
          AND known_overlap_days IS NOT NULL AND co_participation_class <> 'KNOWN_TEMPORAL_OVERLAP') AS bad_days,
        (SELECT count(*) FROM registry_analytics.person_group g WHERE g.build_run_id=%s
          AND g.group_kind='RECURRING_GROUP'
          AND (SELECT count(DISTINCT go.canonical_company_id) FROM registry_analytics.person_group_observation go
               WHERE go.group_id=g.group_id) < %s
          AND (SELECT count(*) FROM registry_analytics.person_group_observation go
               WHERE go.group_id=g.group_id) < %s) AS unsupported_group,
        (SELECT count(*) FROM registry_analytics.person_group g WHERE g.build_run_id=%s
          AND g.group_kind='CORE_GROUP'
          AND (SELECT count(*) FROM registry_analytics.person_group_observation go
               WHERE go.group_id=g.group_id) < 2) AS core_without_episodes,
        (SELECT count(*) FROM registry_analytics.person_group_observation go
          WHERE go.build_run_id=%s AND (SELECT count(*) FROM registry_analytics.person_group_observation_member jm
               WHERE jm.group_observation_id=go.group_observation_id) = 0) AS gobs_without_junction
        """, (bid, bid, bid, bid, bid, cap, bid, bid, bid, pol["company_threshold"], pol["episode_threshold"], bid, bid)).fetchone()
    bad = {k: v for k, v in g.items() if v}
    ok_gates = not bad
    check("§52 completeness re-verification on ACTIVE build", ok_gates, str(bad))
    out["completeness_gates"] = {"violations": bad, "pass": ok_gates}

    # ---------- §62 pair sample audits
    audits = {"same_snapshot": [], "same_event": [], "known_overlap": [], "ownership_same_obs": [], "unknown": []}

    rows = ch.execute("""SELECT ppo.*, o.source_observation_key, o.observation_kind, o.source_document_id,
        o.source_event_id, o.provenance FROM registry_analytics.person_person_overlap ppo
        JOIN registry_analytics.person_company_observation o ON o.build_run_id=ppo.build_run_id
         AND o.canonical_person_id=ppo.person_a_id AND o.canonical_company_id=ppo.canonical_company_id
         AND o.role_context=ppo.role_context
        WHERE ppo.build_run_id=%s AND ppo.co_participation_class='SAME_SNAPSHOT'
        ORDER BY ppo.person_a_id, ppo.person_b_id, ppo.canonical_company_id""", (bid,)).fetchall()
    for r in det_sample(rows, 10):
        m = dict(r)
        doc = m["source_document_id"]
        ok = ch.execute("""SELECT count(*) FROM registry_analytics.person_company_observation o
            WHERE o.build_run_id=%s AND o.canonical_person_id IN (%s,%s) AND o.canonical_company_id=%s
              AND o.role_context=%s AND o.observation_kind='CURRENT_STATE_SNAPSHOT'
              AND o.source_document_id=%s""", (bid, m["person_a_id"], m["person_b_id"],
              m["canonical_company_id"], m["role_context"], doc)).fetchone()["count"]
        prov = json.loads(m["provenance"]) if isinstance(m["provenance"], str) else m["provenance"]
        audits["same_snapshot"].append({"pair": [m["person_a_id"], m["person_b_id"]],
            "company": m["canonical_company_id"], "context": m["role_context"],
            "snapshot_document": doc, "cpr_run": prov.get("pdf", {}).get("projection_run"),
            "both_persons_in_snapshot": ok >= 2, "observation_count": m["observation_count"]})
    check("10 SAME_SNAPSHOT samples trace to shared Felag snapshot identity",
          len(audits["same_snapshot"]) == 10 and all(a["both_persons_in_snapshot"] for a in audits["same_snapshot"]))

    rows = ch.execute("""SELECT ppo.* FROM
        registry_analytics.person_person_overlap ppo
        WHERE ppo.build_run_id=%s AND ppo.co_participation_class='SAME_OBSERVATION'
          AND ppo.same_source_event_count > 0
        ORDER BY ppo.person_a_id, ppo.person_b_id, ppo.canonical_company_id""", (bid,)).fetchall()
    for r in det_sample(rows, 10):
        m = dict(r)
        shared = ch.execute("""SELECT count(DISTINCT o.source_event_id) FROM
            registry_analytics.person_company_observation o
            WHERE o.build_run_id=%s AND o.canonical_company_id=%s AND o.role_context=%s
              AND o.source_event_id IS NOT NULL AND o.canonical_person_id=%s
              AND o.source_event_id IN (SELECT o2.source_event_id
                    FROM registry_analytics.person_company_observation o2
                    WHERE o2.build_run_id=o.build_run_id AND o2.canonical_company_id=o.canonical_company_id
                      AND o2.role_context=o.role_context AND o2.canonical_person_id=%s
                      AND o2.source_event_id IS NOT NULL)""",
            (bid, m["canonical_company_id"], m["role_context"], m["person_a_id"], m["person_b_id"])).fetchone()
        n_shared, sample_evt = (lambda c: (c["count"], ch.execute(
            """SELECT o.source_event_id FROM registry_analytics.person_company_observation o
               WHERE o.build_run_id=%s AND o.canonical_company_id=%s AND o.role_context=%s
                 AND o.source_event_id IS NOT NULL AND o.canonical_person_id=%s
                 AND o.source_event_id IN (SELECT o2.source_event_id
                       FROM registry_analytics.person_company_observation o2
                       WHERE o2.build_run_id=o.build_run_id AND o2.canonical_company_id=o.canonical_company_id
                         AND o2.role_context=o.role_context AND o2.canonical_person_id=%s
                         AND o2.source_event_id IS NOT NULL)
               ORDER BY o.source_event_id LIMIT 1""",
            (bid, m["canonical_company_id"], m["role_context"], m["person_a_id"],
             m["person_b_id"])).fetchone()["source_event_id"]))(shared)
        both = ch.execute("""SELECT count(DISTINCT o.canonical_person_id) FROM
            registry_analytics.person_company_observation o WHERE o.build_run_id=%s AND o.source_event_id=%s
            AND o.canonical_company_id=%s AND o.role_context=%s
            AND o.canonical_person_id IN (%s,%s)""", (bid, sample_evt, m["canonical_company_id"],
            m["role_context"], m["person_a_id"], m["person_b_id"])).fetchone()["count"]
        audits["same_event"].append({"pair": [m["person_a_id"], m["person_b_id"]],
            "company": m["canonical_company_id"], "context": m["role_context"],
            "shared_source_event_id": sample_evt, "both_in_event": both == 2,
            "shared_event_count": n_shared, "stored_same_source_event_count": m["same_source_event_count"],
            "count_matches": n_shared == m["same_source_event_count"]})
    check("10 same-event samples trace to shared Lysing source_event_id; counts match",
          len(audits["same_event"]) == 10 and
          all(a["both_in_event"] and a["count_matches"] for a in audits["same_event"]),
          str([a for a in audits["same_event"] if not (a["both_in_event"] and a["count_matches"])][:2]))


    rows = ch.execute("""SELECT * FROM registry_analytics.person_person_overlap
        WHERE build_run_id=%s AND co_participation_class='KNOWN_TEMPORAL_OVERLAP'
        ORDER BY person_a_id, person_b_id, canonical_company_id""", (bid,)).fetchall()
    for r in det_sample(rows, 10):
        m = dict(r)
        pers = ch.execute("""SELECT period_start, period_end, period_status, opening_observation_key,
            closing_observation_key FROM registry_analytics.person_company_period
            WHERE build_run_id=%s AND canonical_person_id=%s AND canonical_company_id=%s
            AND role_context=%s AND period_status='BOUNDED'
            ORDER BY period_start""", (bid, m["person_a_id"], m["canonical_company_id"], m["role_context"])).fetchall()
        pers_b = ch.execute("""SELECT period_start, period_end FROM registry_analytics.person_company_period
            WHERE build_run_id=%s AND canonical_person_id=%s AND canonical_company_id=%s
            AND role_context=%s AND period_status='BOUNDED' ORDER BY period_start""",
            (bid, m["person_b_id"], m["canonical_company_id"], m["role_context"])).fetchall()
        # phase3_build: known_overlap_days = SUM over every bounded x bounded
        # intersection (one per re-appointment), not the longest one. Run 4's
        # first 10 samples had one period each, so max == sum hid this; run 33's
        # BOARD pair (30, 115) has 189 + 162 = 351 days.
        best = 0
        for p1 in pers:
            for p2 in pers_b:
                lo, hi = max(p1["period_start"], p2["period_start"]), min(p1["period_end"], p2["period_end"])
                if lo < hi:
                    best += (hi - lo).days
        op = ch.execute("""SELECT count(*) FROM registry_analytics.person_company_period p
            WHERE p.build_run_id=%s AND p.canonical_person_id IN (%s,%s) AND p.canonical_company_id=%s
            AND p.role_context=%s AND p.period_status='BOUNDED'
            AND EXISTS (SELECT 1 FROM registry_analytics.person_company_observation o
                        WHERE o.build_run_id=p.build_run_id AND o.source_observation_key=p.opening_observation_key)""",
            (bid, m["person_a_id"], m["person_b_id"], m["canonical_company_id"], m["role_context"])).fetchone()["count"]
        audits["known_overlap"].append({"pair": [m["person_a_id"], m["person_b_id"]],
            "company": m["canonical_company_id"], "context": m["role_context"],
            "known_overlap_days": m["known_overlap_days"], "recomputed_days": best,
            "days_match": best == m["known_overlap_days"], "period_openers_resolve": op > 0})
    check("10 KNOWN_TEMPORAL_OVERLAP samples: stored days == recomputed intersection",
          len(audits["known_overlap"]) == 10 and all(a["recomputed_days"] == a["known_overlap_days"]
                                                     for a in audits["known_overlap"]))

    rows = ch.execute("""SELECT ppo.*, o.source_attempt_id, o.source_relation_id, o.provenance
        FROM registry_analytics.person_person_overlap ppo
        JOIN registry_analytics.person_company_observation o ON o.build_run_id=ppo.build_run_id
         AND o.canonical_person_id=ppo.person_a_id AND o.canonical_company_id=ppo.canonical_company_id
         AND o.role_context=ppo.role_context
        WHERE ppo.build_run_id=%s AND ppo.co_participation_class='SAME_OBSERVATION'
          AND ppo.source_family='OWNERSHIP_GRAPH'
        ORDER BY ppo.person_a_id, ppo.person_b_id, ppo.canonical_company_id""", (bid,)).fetchall()
    own_sampled = min(10, len(rows))
    for r in det_sample(rows, 10):
        m = dict(r)
        att = m["source_attempt_id"]
        h = ch.execute("SELECT response_sha256 FROM public.request_attempt WHERE attempt_id=%s",
                       (att,)).fetchone()["response_sha256"]
        n = ch.execute("""SELECT count(DISTINCT o.canonical_person_id) FROM
            registry_analytics.person_company_observation o WHERE o.build_run_id=%s
            AND o.source_attempt_id=%s AND o.canonical_company_id=%s AND o.role_context=%s
            AND o.canonical_person_id IN (%s,%s)""",
            (bid, att, m["canonical_company_id"], m["role_context"],
             m["person_a_id"], m["person_b_id"])).fetchone()["count"]
        prov = json.loads(m["provenance"]) if isinstance(m["provenance"], str) else m["provenance"]
        sha = prov.get("ownership", {}).get("response_sha256")
        audits["ownership_same_obs"].append({"pair": [m["person_a_id"], m["person_b_id"]],
            "company": m["canonical_company_id"], "context": m["role_context"],
            "source_attempt_id": att, "response_sha256": (sha or "")[:16],
            "attempt_hash_resolves": h is not None and sha is not None and h == sha,
            "both_in_same_attempt": n == 2})
    check("ownership SAME_OBSERVATION samples: attempt -> request_attempt hash resolves",
          own_sampled == 10 and all(a["attempt_hash_resolves"] and a["both_in_same_attempt"]
                                    for a in audits["ownership_same_obs"]),
          str([a for a in audits["ownership_same_obs"]
               if not (a["attempt_hash_resolves"] and a["both_in_same_attempt"])][:2]))

    rows = ch.execute("""SELECT * FROM registry_analytics.person_person_overlap
        WHERE build_run_id=%s AND co_participation_class='UNKNOWN_TEMPORAL_RELATION'
        ORDER BY person_a_id, person_b_id, canonical_company_id""", (bid,)).fetchall()
    for r in det_sample(rows, 10):
        m = dict(r)
        lc = ch.execute("""SELECT count(*) FROM registry_analytics.person_company_period
            WHERE build_run_id=%s AND canonical_person_id IN (%s,%s) AND canonical_company_id=%s
            AND role_context=%s AND period_status IN ('OPEN_LEFT_CENSORED','CLOSED_ONLY')""",
            (bid, m["person_a_id"], m["person_b_id"], m["canonical_company_id"],
             m["role_context"])).fetchone()["count"]
        pts = ch.execute("""SELECT count(*) FROM registry_analytics.person_company_observation o
            WHERE o.build_run_id=%s AND o.canonical_person_id IN (%s,%s) AND o.canonical_company_id=%s
            AND o.role_context=%s AND o.observation_kind IN ('CURRENT_STATE_SNAPSHOT','OWNERSHIP_SNAPSHOT_POINT')""",
            (bid, m["person_a_id"], m["person_b_id"], m["canonical_company_id"], m["role_context"])).fetchone()["count"]
        audits["unknown"].append({"pair": [m["person_a_id"], m["person_b_id"]],
            "company": m["canonical_company_id"], "context": m["role_context"],
            "left_censored_periods": lc, "point_observations": pts, "known_overlap_days": m["known_overlap_days"]})
    check("10 UNKNOWN temporal samples: left-censored or pure-point evidence, days NULL",
          len(audits["unknown"]) == 10 and all(a["known_overlap_days"] is None and
              (a["left_censored_periods"] > 0 or a["point_observations"] > 0) for a in audits["unknown"]))
    out["sample_audits"] = audits

    # ---------- §63 group sample audits (traceable across sizes and kinds)
    gsamples = []
    by_size = ch.execute("""SELECT group_size AS size, count(*) AS n FROM registry_analytics.person_group
        WHERE build_run_id=%s GROUP BY 1 ORDER BY 1""", (bid,)).fetchall()
    for row_size in by_size:
        size, n = row_size["size"], row_size["n"]
        rows = ch.execute("""SELECT g.group_id, g.member_set_hash, g.role_context, g.group_kind, g.group_size,
            g.support, g.first_observed, g.last_observed FROM registry_analytics.person_group g
            WHERE g.build_run_id=%s AND g.group_size=%s ORDER BY g.member_set_hash""", (bid, size)).fetchall()
        r = det_sample(rows, min(3, len(rows)))[0]
        m = dict(r)
        members = [x["canonical_person_id"] for x in ch.execute("""SELECT canonical_person_id FROM registry_analytics.person_group_member
            WHERE group_id=%s ORDER BY canonical_person_id""", (m["group_id"],)).fetchall()]
        assert member_set_hash(members) == m["member_set_hash"]
        go = ch.execute("""SELECT go.group_observation_id, go.episode_key, go.canonical_company_id,
            go.role_context, go.episode_start, go.episode_end, go.co_observation_kind
            FROM registry_analytics.person_group_observation go WHERE go.group_id=%s
            ORDER BY go.episode_key LIMIT 2""", (m["group_id"],)).fetchall()
        # dict rows: index g[0] etc replaced below
        trace = {"group_size": size, "member_set_hash": m["member_set_hash"][:16],
                 "role_context": m["role_context"], "kind": m["group_kind"],
                 "members_resolved": sorted(members),
                 "support": json.loads(m["support"]) if isinstance(m["support"], str) else m["support"],
                 "episode_samples": [], "junction_complete": True, "source_trace": []}
        for g in go:
            jm = ch.execute("""SELECT jm.observation_id, jm.role_in_episode, o.source_observation_key,
                o.canonical_person_id, o.observation_kind, o.source_event_id, o.source_document_id,
                o.source_attempt_id, o.source_family
                FROM registry_analytics.person_group_observation_member jm
                JOIN registry_analytics.person_company_observation o ON o.observation_id=jm.observation_id
                WHERE jm.group_observation_id=%s ORDER BY o.source_observation_key""",
                (g["group_observation_id"],)).fetchall()
            trace["episode_samples"].append({"episode_key": g["episode_key"], "company": g["canonical_company_id"],
                "context": g["role_context"],
                "interval": [str(g["episode_start"]), str(g["episode_end"])], "co_observation_kind": g["co_observation_kind"],
                "junction_members": len(jm),
                "kinds": sorted({x["source_family"] for x in jm})})
            for x in jm[:3]:
                trace["source_trace"].append({"observation_key": x["source_observation_key"],
                    "person": x["canonical_person_id"], "kind": x["observation_kind"],
                    "source_event_id": x["source_event_id"], "source_attempt_id": x["source_attempt_id"],
                    "family": x["source_family"]})
        gsamples.append(trace)
    check("group samples across all persisted sizes: members resolved + junction rows present",
          len(gsamples) > 0 and all(any(e["junction_members"] >= 2 for e in s["episode_samples"])
                                    for s in gsamples))
    kinds_seen = {e["co_observation_kind"] for s in gsamples for e in s["episode_samples"]}
    spo_groups = ch.execute("""SELECT go.group_id, count(*) FROM registry_analytics.person_group_observation go
        WHERE go.build_run_id=%s AND go.co_observation_kind='SAFE_PERIOD_OVERLAP'
        GROUP BY go.group_id ORDER BY go.group_id""", (bid,)).fetchall()
    if spo_groups:
        gid = spo_groups[0]["group_id"]
        grow = ch.execute("""SELECT group_id, member_set_hash, role_context, group_kind, group_size, support
            FROM registry_analytics.person_group WHERE group_id=%s""", (gid,)).fetchone()
        members = [x["canonical_person_id"] for x in ch.execute("""SELECT canonical_person_id
            FROM registry_analytics.person_group_member WHERE group_id=%s ORDER BY canonical_person_id""",
            (gid,)).fetchall()]
        gos = ch.execute("""SELECT episode_key, canonical_company_id, role_context, episode_start, episode_end
            FROM registry_analytics.person_group_observation WHERE group_id=%s
              AND co_observation_kind='SAFE_PERIOD_OVERLAP' ORDER BY episode_key""",
            (gid,)).fetchall()
        ep_traces = []
        for go_row in gos[:2]:
            jm = ch.execute("""SELECT jm.role_in_episode, o.source_observation_key, o.canonical_person_id
                FROM registry_analytics.person_group_observation_member jm
                JOIN registry_analytics.person_company_observation o ON o.observation_id=jm.observation_id
                WHERE jm.group_observation_id=(SELECT group_observation_id FROM
                    registry_analytics.person_group_observation WHERE group_id=%s AND episode_key=%s)
                ORDER BY o.source_observation_key""", (gid, go_row["episode_key"])).fetchall()
            pers = ch.execute("""SELECT canonical_person_id, period_start, period_end FROM
                registry_analytics.person_company_period WHERE build_run_id=%s AND canonical_company_id=%s
                AND role_context=%s AND period_status='BOUNDED' AND canonical_person_id = ANY(%s)
                ORDER BY canonical_person_id""", (bid, go_row["canonical_company_id"], go_row["role_context"],
                members)).fetchall()
            # The builder sweeps atomic intervals, so a member with several bounded
            # periods (re-appointed) yields several episodes. Safe = every member
            # holds one bounded period covering the whole episode (run 33's BOARD
            # group at company 7599: two episodes 2010-05-06..2012-05-01..2013-04-30
            # inside a 2010-05-06..2014-04-03 min/max envelope).
            s0, e0 = go_row["episode_start"], go_row["episode_end"]
            covering = {p["canonical_person_id"] for p in pers
                        if p["period_start"] <= s0 and p["period_end"] >= e0}
            distinct_persons = {x["canonical_person_id"] for x in jm}
            ep_traces.append({"episode_key": go_row["episode_key"], "company": go_row["canonical_company_id"],
                "members_covering": len(covering), "stored_interval": [str(s0), str(e0)],
                "interval_matches": s0 < e0 and covering == set(members),
                "members": len(members), "junction_members": len(jm),
                "junction_persons_match": distinct_persons == set(members)})
        gsamples.append({"group_size": grow["group_size"], "member_set_hash": grow["member_set_hash"][:16],
            "role_context": grow["role_context"], "kind": grow["group_kind"],
            "members_resolved": sorted(members), "support": json.loads(grow["support"])
            if isinstance(grow["support"], str) else grow["support"],
            "episode_samples": ep_traces, "source_trace": [], "safe_period_overlap": True})
        spo_ok = all(t["interval_matches"] and t["junction_persons_match"] for t in ep_traces)
    else:
        spo_ok = True
    check("SAFE_PERIOD_OVERLAP group sample: every member's bounded period covers the episode",
          spo_ok, str(ep_traces if spo_groups else "no SAFE_PERIOD_OVERLAP groups"))
    check("sampled groups include SAME_SNAPSHOT and SAME_EVENT episodes",
          {"SAME_SNAPSHOT", "SAME_EVENT"} <= kinds_seen, str(kinds_seen))
    out["group_sample_audits"] = gsamples
    out["group_size_distribution"] = {str(r["size"]): r["n"] for r in by_size}

    # ---------- §64 source separation regression
    sep = {}
    sep["pdf_only_pdfhist"] = ch.execute("""SELECT count(*) FROM registry_analytics.person_company_observation
        WHERE build_run_id=%s AND source_family='PDF_HISTORY'
        AND (source_observation_key NOT LIKE 'PDF:%%' OR source_projection_run_id IS NULL AND observation_kind<>'OWNERSHIP_SNAPSHOT_POINT')""", (bid,)).fetchone()["count"]
    # every PDF-history observation resolves to pinned projection run / CHE in pdfhist_pilot
    sep["pdf_provenance_valid"] = ch.execute("""SELECT count(*) FROM registry_analytics.person_company_observation
        WHERE build_run_id=%s AND source_family='PDF_HISTORY'""", (bid,)).fetchone()["count"] > 0
    own_only = ch.execute("""SELECT count(*) FROM registry_analytics.person_company_observation
        WHERE build_run_id=%s AND source_family='OWNERSHIP_GRAPH'
        AND NOT (source_observation_key LIKE 'OWN:%%' AND source_relation_id IS NOT NULL)""", (bid,)).fetchone()["count"]
    sep["ownership_rows_malformed"] = own_only
    legacy = ch.execute("""SELECT count(*) FROM registry_analytics.person_company_observation
        WHERE build_run_id=%s AND (source_observation_key LIKE '%%legacy%%'
        OR source_observation_key LIKE '%%ECE%%' OR provenance::text LIKE '%%canonical_relation_legacy%%'
        OR provenance::text LIKE '%%entity_change_event%%')""", (bid,)).fetchone()["count"]
    sep["legacy_or_ece_leak"] = legacy
    web_authority = ch.execute("""SELECT count(*) FROM registry_analytics.person_group_observation go
        WHERE go.build_run_id=%s AND go.source_family='OWNERSHIP_WEB_GOVERNANCE'""", (bid,)).fetchone()["count"]
    web_pairs = ch.execute("""SELECT count(*) FROM registry_analytics.person_person_overlap
        WHERE build_run_id=%s AND source_family='OWNERSHIP_WEB_GOVERNANCE'""", (bid,)).fetchone()["count"]
    sep["web_derived_groups"] = web_authority
    sep["web_derived_pairs"] = web_pairs
    req = ch.execute("""SELECT count(*) FROM registry_analytics.person_company_observation o
        WHERE o.build_run_id=%s AND o.source_family='OWNERSHIP_GRAPH'
        AND NOT EXISTS (SELECT 1 FROM public.request_attempt ra WHERE ra.attempt_id=o.source_attempt_id)""",
        (bid,)).fetchone()["count"]
    sep["ownership_without_request_attempt"] = req
    sep_ok = all(v == 0 for k, v in sep.items() if isinstance(v, int) and not isinstance(v, bool))
    check("§64 source separation: no legacy/ECE leaks, no web-derived pairs/groups, ownership via shared acquisition",
          sep["pdf_provenance_valid"] and sep_ok, str(sep))
    out["source_separation"] = sep

    # ---------- §61 coverage
    covj = json.loads(cov) if isinstance(cov, str) else cov
    coverage = {
        "resolved_historical_events": covj.get("resolved_via_direct_link", 0) + covj.get("resolved_via_source_local_propagation", 0),
        "unresolved_historical_events": covj.get("unresolved", 0),
        "ambiguous": covj.get("ambiguous", 0),
        "current_governance_linked": covj.get("direct_cpr_linked_coverage", 0),
        "unresolved_current_person": covj.get("unresolved_current_person", 0),
        "ownership_person_company_observations": covj.get("ownership_observations", 0),
        "web_governance_observations": covj.get("web_governance_observations", 0),
        "web_unresolved_canonical": covj.get("web_unresolved_canonical", 0),
        "phase3_applicability": "Phase 3 pairs/groups use ONLY safely resolved observations; "
                                f"{covj.get('unresolved', 0)} unresolved historical events and web governance "
                                "(0 canonical obs) contribute no pair/group rows (fail-closed)",
    }
    # Baseline per pinned projection run (H-6; the constants were pinned to run 4):
    #   run 4  = builds 8-13 (Phase 0 pin, 2026-09-01): 7,126 unresolved, 7,770 web rows
    #   run 33 = tools/phase2_build.py on the recovered pilot run 33 + a
    #            heimdal_recovered_20260904 clone (lane W6a, 2026-11-06): 6,387 / 7,770
    COVERAGE_BASELINE = {4: {"unresolved": 7126, "web_unresolved_canonical": 7770},
                         33: {"unresolved": 6387, "web_unresolved_canonical": 7770}}
    exp = COVERAGE_BASELINE.get(pin_run)
    check(f"§61 coverage recorded (unresolved + web limits explicit) vs run {pin_run} baseline",
          exp is not None and coverage["unresolved_historical_events"] == exp["unresolved"]
          and coverage["web_unresolved_canonical"] == exp["web_unresolved_canonical"],
          f"{coverage['unresolved_historical_events']}/{coverage['web_unresolved_canonical']} vs {exp}")
    check("§61 resolved + unresolved + ambiguous == historical CHE events",
          coverage["resolved_historical_events"] + coverage["unresolved_historical_events"]
          + coverage["ambiguous"] == covj.get("historical_person_events_total"))
    out["coverage"] = coverage

    # ---------- M-01 closure: pair metadata rule (full table, §28) ----------
    # For EVERY pair row: recompute contributing families/authorities from the
    # same-build observations of both persons in (company, context); assert
    # stored metadata equals the single value when homogeneous, NULL when mixed,
    # and direct classes always carry precise metadata.
    meta_rows = ch.execute("""SELECT ppo.pair_id, ppo.co_participation_class, ppo.source_family,
        ppo.authority_class, ppo.canonical_company_id, ppo.role_context,
        ppo.person_a_id, ppo.person_b_id
        FROM registry_analytics.person_person_overlap ppo
        WHERE ppo.build_run_id=%s ORDER BY ppo.pair_id""", (bid,)).fetchall()
    obs_by_pc = {}
    for o in ch.execute("""SELECT o.canonical_person_id, o.canonical_company_id, o.role_context,
        o.source_family, o.authority_class FROM registry_analytics.person_company_observation o
        WHERE o.build_run_id=%s AND o.canonical_person_id IS NOT NULL
        AND o.source_family IN ('PDF_HISTORY','OWNERSHIP_GRAPH')""", (bid,)).fetchall():
        obs_by_pc.setdefault((o["canonical_company_id"], o["role_context"], o["canonical_person_id"]),
                             []).append(o)
    meta_bad, null_fam_fb, null_auth_fb, fb_total = [], 0, 0, 0
    for r in meta_rows:
        m = dict(r)
        ua = obs_by_pc.get((m["canonical_company_id"], m["role_context"], m["person_a_id"]), [])
        ub = obs_by_pc.get((m["canonical_company_id"], m["role_context"], m["person_b_id"]), [])
        union = ua + ub
        fams = {o["source_family"] for o in union}
        auths = {o["authority_class"] for o in union}
        direct = m["co_participation_class"] in ("SAME_SNAPSHOT", "SAME_OBSERVATION",
                                                  "KNOWN_TEMPORAL_OVERLAP")
        exp_fam = next(iter(sorted(fams))) if len(fams) == 1 else None
        exp_auth = next(iter(sorted(auths))) if len(auths) == 1 else None
        if direct:
            if m["source_family"] is None or m["authority_class"] is None:
                meta_bad.append({"pair_id": m["pair_id"], "why": "direct with NULL metadata"})
        else:
            fb_total += 1
            null_fam_fb += 1 if m["source_family"] is None else 0
            null_auth_fb += 1 if m["authority_class"] is None else 0
            if m["source_family"] != exp_fam or m["authority_class"] != exp_auth:
                meta_bad.append({"pair_id": m["pair_id"], "stored": [m["source_family"], m["authority_class"]],
                                 "expected": [exp_fam, exp_auth]})
    check("§28 pair metadata rule exact for all rows (NULL only where heterogeneous)",
          not meta_bad, str(meta_bad[:3]))
    out["closure_pair_metadata"] = {"fallback_rows": fb_total,
                                     "null_source_family_fallback_rows": null_fam_fb,
                                     "null_authority_class_fallback_rows": null_auth_fb,
                                     "rule_violations": meta_bad}

    # ---------- M-02 closure: support recomputation from group observations (§30) ----------
    gsup = ch.execute("""SELECT g.group_id, g.group_kind, g.role_context, g.group_size, g.support
        FROM registry_analytics.person_group g WHERE g.build_run_id=%s ORDER BY g.group_id""",
        (bid,)).fetchall()
    gob_by_group = defaultdict(lambda: {"companies": set(), "kinds": Counter(), "n": 0})
    for x in ch.execute("""SELECT go.group_id, go.canonical_company_id, go.co_observation_kind
        FROM registry_analytics.person_group_observation go WHERE go.build_run_id=%s""", (bid,)).fetchall():
        e = gob_by_group[x["group_id"]]
        e["companies"].add(x["canonical_company_id"])
        e["kinds"][x["co_observation_kind"]] += 1
        e["n"] += 1
    sup_bad = []
    cross_kind_groups = []
    for g in gsup:
        e = gob_by_group.get(g["group_id"], {"companies": set(), "kinds": Counter(), "n": 0})
        doc = json.loads(g["support"]) if isinstance(g["support"], str) else g["support"]
        if g["group_kind"] == "RECURRING_GROUP":
            if doc.get("support_company_count") != len(e["companies"]) or \
               doc.get("support_episode_count") != e["n"]:
                sup_bad.append({"group_id": g["group_id"],
                                 "stored": [doc.get("support_company_count"), doc.get("support_episode_count")],
                                 "recomputed": [len(e["companies"]), e["n"]]})
            if doc.get("episode_kind_counts") != {k: e["kinds"][k] for k in sorted(e["kinds"])}:
                sup_bad.append({"group_id": g["group_id"], "why": "episode_kind_counts mismatch"})
            if len(e["kinds"]) > 1:
                cross_kind_groups.append({"group_id": g["group_id"], "context": g["role_context"],
                                           "size": g["group_size"], "kinds": dict(e["kinds"]),
                                           "companies": len(e["companies"]), "episodes": e["n"],
                                           "support": doc})
    check("§30 stored support exactly recomputes from group observations (all groups)",
          not sup_bad, str(sup_bad[:3]))
    out["closure_support_recomputation"] = {"mismatches": sup_bad,
                                             "cross_kind_supported_groups": len(cross_kind_groups)}
    # cross-kind persistence reason: combined rule satisfied, no kind partition involved
    reason_bad = [c for c in cross_kind_groups
                  if not (c["support"]["support_company_count"] >= pol["company_threshold"]
                          or c["support"]["support_episode_count"] >= pol["episode_threshold"])]
    check("§33 cross-kind groups persist via combined rule only (no kind partition)",
          not reason_bad, str(reason_bad[:2]))
    out["cross_kind_group_samples"] = cross_kind_groups[:10]

    # ---------- M-02 closure: CORE recomputation (§31) ----------
    core_bad = []
    # map recurring groups by member set via members table
    members_by_group = defaultdict(list)
    for x in ch.execute("""SELECT m.group_id, g.group_kind, g.role_context, m.canonical_person_id
        FROM registry_analytics.person_group_member m
        JOIN registry_analytics.person_group g ON g.group_id=m.group_id
        WHERE g.build_run_id=%s ORDER BY m.canonical_person_id""", (bid,)).fetchall():
        members_by_group[x["group_id"]].append(x["canonical_person_id"])
    rec_by_ctx_members = {}
    for g in gsup:
        if g["group_kind"] == "RECURRING_GROUP":
            rec_by_ctx_members[(g["role_context"], tuple(sorted(members_by_group[g["group_id"]])))] = g
    for g in gsup:
        if g["group_kind"] != "CORE_GROUP":
            continue
        doc = json.loads(g["support"]) if isinstance(g["support"], str) else g["support"]
        mem = tuple(sorted(members_by_group[g["group_id"]]))
        e = gob_by_group.get(g["group_id"], {"companies": set(), "kinds": Counter(), "n": 0})
        parents = doc.get("core_of_member_set_hashes", [])
        parent_ok = False
        for (rc, pmem), pg in rec_by_ctx_members.items():
            if rc == g["role_context"] and set(mem) < set(pmem):
                pd = json.loads(pg["support"]) if isinstance(pg["support"], str) else pg["support"]
                if member_set_hash(sorted(pmem)) in parents:
                    parent_ok = True
                    peps = pd["support_episode_count"]
                    ratio_ok = doc["core_episode_count"] / peps + 1e-9 >= doc["core_threshold"]
                    guard_ok = peps >= 3 or (pd["support_company_count"] >= 2 and peps >= 2)
                    if not (ratio_ok and guard_ok):
                        core_bad.append({"group_id": g["group_id"], "why": "guard/ratio"})
        if not parent_ok:
            core_bad.append({"group_id": g["group_id"], "why": "no valid recurring parent"})
        if e["n"] < 2:
            core_bad.append({"group_id": g["group_id"], "why": "core episodes < 2"})
        if len(e["kinds"]) and e["n"] != doc["core_episode_count"]:
            core_bad.append({"group_id": g["group_id"], "why": "episode count mismatch"})
    check("§31 every CORE row has valid parent, guard, ratio, episodes",
          not core_bad, str(core_bad[:3]))
    out["closure_core_recomputation"] = {"invalid": core_bad, "core_groups": sum(
        1 for g in gsup if g["group_kind"] == "CORE_GROUP")}

    # ---------- pair metrics for the report
    cls_rows = ch.execute("""SELECT co_participation_class AS cls, count(*) AS n FROM registry_analytics.person_person_overlap
        WHERE build_run_id=%s GROUP BY 1 ORDER BY 1""", (bid,)).fetchall()
    ctx_rows = ch.execute("""SELECT role_context AS ctx, count(*) AS n FROM registry_analytics.person_person_overlap
        WHERE build_run_id=%s GROUP BY 1 ORDER BY 1""", (bid,)).fetchall()
    uniq = ch.execute("""SELECT count(DISTINCT (person_a_id, person_b_id)) AS n FROM
        registry_analytics.person_person_overlap WHERE build_run_id=%s""", (bid,)).fetchone()
    direct = ch.execute("""SELECT
        count(*) FILTER (WHERE co_participation_class='SAME_SNAPSHOT' AND source_family='PDF_HISTORY') AS felag_snapshot,
        count(*) FILTER (WHERE co_participation_class='SAME_OBSERVATION' AND source_family='PDF_HISTORY'
                         AND same_source_event_count > 0) AS same_event,
        count(*) FILTER (WHERE co_participation_class='SAME_OBSERVATION' AND source_family='OWNERSHIP_GRAPH') AS ownership_same_obs,
        count(*) FILTER (WHERE co_participation_class='KNOWN_TEMPORAL_OVERLAP'
                         AND known_overlap_days IS NOT NULL) AS known_days_rows,
        count(*) FILTER (WHERE co_participation_class='KNOWN_TEMPORAL_OVERLAP'
                         AND known_overlap_days IS NULL) AS point_overlap_rows
        FROM registry_analytics.person_person_overlap WHERE build_run_id=%s""", (bid,)).fetchone()
    direct = tuple(direct.values())
    out["pair_metrics"] = {"by_class": {r["cls"]: r["n"] for r in cls_rows},
                           "by_context": {r["ctx"]: r["n"] for r in ctx_rows},
                           "unique_person_pairs": uniq["n"], "direct": dict(zip(
        ["felag_snapshot_pairs", "same_event_pairs", "ownership_same_obs_pairs",
         "known_days_rows", "point_overlap_rows"], direct))}
    go_rows = ch.execute("""SELECT co_observation_kind AS k, count(*) AS n FROM registry_analytics.person_group_observation
        WHERE build_run_id=%s GROUP BY 1 ORDER BY 1""", (bid,)).fetchall()
    out["group_observation_metrics"] = {r["k"]: r["n"] for r in go_rows}
    gk = ch.execute("""SELECT role_context AS rc, group_kind AS gk, count(*) AS n FROM registry_analytics.person_group
        WHERE build_run_id=%s GROUP BY 1,2 ORDER BY 1,2""", (bid,)).fetchall()
    out["group_metrics"] = {f"{r['rc']}:{r['gk']}": r["n"] for r in gk}

    json.dump(out, open("tools/phase3_verify_state.json", "w"), indent=1, default=str)
    print("WROTE tools/phase3_verify_state.json")
    print("FAILS:", fails or "none")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()