"""Phase 4 analytics view models — versioned readers + Danish labels.

Reader contract (task B3/B4/B38): every public reader resolves ONE ACTIVE
build and pins (build_run_id, content_hash) into every response; queries in
one response always run against that single build. No ACTIVE build -> every
builder returns None and the portal renders exactly as before (B5, fail
closed).

Binding semantics implemented here (none of them invented):

* PAIR FACT != SOURCE AUTHORITY LABEL (Phase 3 closure): a pair row with
  source_family='PDF_HISTORY' + authority_class=NULL is rendered as a fact
  supported by several PDF authority classes — never "ukendt kilde", never a
  MIXED label (B16);
* co_observation_kind is provenance; role_context is the support boundary —
  group support is presented per (role_context, member_set) (B13);
* temporal honesty (B8/B18/B26/B27/B28): ownership is point evidence
  ("Observeret ejer pr. <dato>", never a period); BOUNDED periods may show a
  duration; OPEN_LEFT_CENSORED never invents an original start;
  CLOSED_ONLY shows no duration; snapshot/event episodes never render a
  duration;
* authority (B9): AUTHORITATIVE_CURRENT_GOVERNANCE (Felag) is the only
  current board/management authority; historical PDF evidence is labelled
  historical; web governance is not presented as current anything (B37);
* coverage (B10/B36/B37) comes from the pinned build's own coverage metrics —
  never hardcoded.
"""
from __future__ import annotations

import logging

import psycopg

from heimdal.modules.shared.db import read_savepoint

from . import repository as repo
from .flags import analytics_enabled

_logger = logging.getLogger(__name__)

# --- human labels (B7/B32) ----------------------------------------------------

ROLE_CONTEXT_LABELS = {
    "BOARD": "Bestyrelse",
    "MANAGEMENT": "Ledelse",
    "OWNERSHIP": "Ejerskab",
    "CONTROL_ACTOR": "Kontrolaktør",
}

# Collapsed-state constellation titles (owner UX correction UX-01): a human
# group identity derived from the context — presentation vocabulary only.
ROLE_CONTEXT_TITLE = {
    "BOARD": "Bestyrelseskonstellation",
    "MANAGEMENT": "Ledelseskonstellation",
    "OWNERSHIP": "Ejerskabskonstellation",
    "CONTROL_ACTOR": "Kontrolaktørkonstellation",
}

PARTICIPATION_CLASS_LABELS = {
    "SAME_SNAPSHOT": "Samme registrerede snapshot",
    "SAME_OBSERVATION": "Samme observation",
    "KNOWN_TEMPORAL_OVERLAP": "Observeret tidsmæssigt overlap",
    "UNKNOWN_TEMPORAL_RELATION": "Samme virksomhed — tidsforhold ukendt",
    "SAME_COMPANY_EVER": "Samme virksomhed — forskellige registreringer",
}

GROUP_KIND_LABELS = {
    "RECURRING_GROUP": "Tilbagevendende konstellation",
    "CORE_GROUP": "Kernegruppe",
}

OBSERVATION_KIND_LABELS = {
    "SNAPSHOT": "Registreret snapshot",
    "APPOINTED": "Tiltrædelsesregistrering",
    "REMOVED": "Fratrædelsesregistrering",
    "SPO": "Ejerskabsobservation",
}

SOURCE_FAMILY_LABELS = {
    "PDF_HISTORY": "Skráseting — Felag",
    "OWNERSHIP_GRAPH": "Ejerskabsobservation",
    "OWNERSHIP_WEB_GOVERNANCE": "Web governance (supplerende)",
}

AUTHORITY_CLASS_LABELS = {
    "AUTHORITATIVE_CURRENT_GOVERNANCE": "Aktuel autoritativ status (Felag)",
    "HISTORICAL_PDF_OBSERVATION": "Historisk registrering (Felag)",
    "OWNERSHIP_POINT": "Ejerskabspunkt",
}

# B16: heterogeneous fallback metadata is incompleteness-by-design, not an
# error and not an invented class.
FALLBACK_AUTHORITY_NOTE = ("Faktum er understøttet af flere PDF-authority-klasser "
                           "samtidig; den enkelte authority-klassificering kan derfor ikke "
                           "sættes på rækken — kilderne kan rekonstrueres pr. registrering.")
FALLBACK_FAMILY_NOTE = ("Faktum er understøttet af mere end én kildefamilie samtidig; "
                        "den enkelte kildefamilie kan derfor ikke sættes på rækken.")

PERIOD_STATUS_TEXT = {
    "BOUNDED": "Afgrænset periode",
    "OPEN_LEFT_CENSORED": "Observeret fra {start} — oprindelig start ukendt",
    "CLOSED_ONLY": "Tidligere registreret; start ukendt",
}

COVERAGE_NOTE = ("Dækning: Analytics bruger kun personer, som kan knyttes sikkert "
                 "til Heimdal.")


def _format_dk_int(n):
    """Danish thousands separator (7 126) — format-safe."""
    return f"{int(n):,}".replace(",", ".")


COVERAGE_UNRESOLVED_TEXT = "{unresolved} historiske personhændelser er ikke med " \
                           "i personbaseret analyse."
COVERAGE_WEB_TEXT = ("Ingen sikkert canonical-linkede web governance-observationer "
                     "indgår i dette build.")
ANALYTICS_DISCLAIMER = ("Analytics er en udledt oversigt bygget oven på kilde-"
                        "observationerne — den erstatter ikke kildeoplysningerne "
                        "og vises med kilde for hver række.")
MIXED_AUTHORITY_LABEL = "Flere PDF-authority-klasser (faktum bevaret)"

# B14 ordering: transparent and deterministic; documented in the report.
COLLABORATOR_SORT = ("shared_company_count DESC, direct evidence count DESC, "
                     "known overlap DESC, canonical id")


def _dk_date(value):
    if value is None:
        return None
    return value.strftime("%d.%m.%Y")


def _coverage_display(coverage):
    """Coverage view from the pinned build's own metrics (B36/B37)."""
    cov = coverage or {}
    return {
        "unresolved_historical": cov.get("unresolved"),
        "unresolved_text": COVERAGE_UNRESOLVED_TEXT.format(
            unresolved=_format_dk_int(cov.get("unresolved") or 0)),
        "resolved_historical": cov.get("historical_observations"),
        "ambiguous": cov.get("ambiguous"),
        "current_linked": cov.get("direct_cpr_linked_coverage"),
        "current_unresolved": cov.get("unresolved_current_person"),
        "ownership_observations": cov.get("ownership_observations"),
        "web_observations": cov.get("web_governance_observations"),
        "web_unresolved": cov.get("web_unresolved_canonical"),
        "web_note": COVERAGE_WEB_TEXT,
        "pair_rows": cov.get("pair_rows_total"),
        "unique_person_pairs": cov.get("unique_person_pairs"),
        "groups_persisted": cov.get("groups_persisted"),
        "group_observations": cov.get("group_observations"),
    }


# --- build pin ---------------------------------------------------------------

def get_active_analytics_build(conn):
    """dict | None: the ACTIVE build pin + coverage, or None fail-closed."""
    try:
        with read_savepoint(conn):  # C-4: a missing schema can't poison conn
            row = repo.active_build(conn)
    except psycopg.Error as exc:
        _logger.warning("analytics active build read degraded: %s", repr(exc))
        return None
    if row is None:
        return None
    return {"build_run_id": row["build_run_id"],
            "content_hash": row["content_hash"],
            "built_at": row["built_at"],
            "coverage": row.get("coverage") if isinstance(row, dict) else None,
            "coverage_display": _coverage_display(
                row["coverage"] if isinstance(row, dict) else None)}


# --- pairs: person collaborators (B12/B13/B14/B15) ---------------------------

def _pair_evidence_counts(row):
    return {
        "same_snapshot": row["same_snapshot_count"] or 0,
        "same_event": row["same_source_event_count"] or 0,
        "known_overlap_days": row["known_overlap_days"],
    }


def get_person_collaborators(conn, person_id, build=None, limit=25,
                             context_filter=None, class_filter=None):
    """Query-time pair-global metrics from the pinned ACTIVE build.

    Returns dict(build_run_id, content_hash, collaborators, total, ...)
    or None when no ACTIVE build exists (B5). Sort documented in
    COLLABORATOR_SORT — transparent, deterministic, no opaque score (B14).
    """
    if build is None:
        build = get_active_analytics_build(conn)
    if build is None:
        return None
    try:
        rows = repo.pair_rows_for_person(conn, build["build_run_id"], person_id)
        names = repo.names_for(conn, [r["person_a_id"] for r in rows] +
                               [r["person_b_id"] for r in rows])
    except psycopg.Error as exc:
        _logger.warning("person collaborators read degraded: %s", repr(exc))
        return {"build_run_id": build["build_run_id"],
                "content_hash": build["content_hash"],
                "error": True, "collaborators": [], "coverage_display":
                    build["coverage_display"]}
    by_peer = {}
    for r in rows:
        if r["person_a_id"] == person_id:
            peer = r["person_b_id"]
        elif r["person_b_id"] == person_id:
            peer = r["person_a_id"]
        else:  # cannot happen with the A<B CHECK; kept explicit
            continue
        entry = by_peer.setdefault(peer, {
            "canonical_person_id": peer,
            "display_name": (names.get(peer) or {}).get("display_name"),
            "companies": {}, "contexts": {}, "classes": {},
            "direct_evidence": 0, "known_overlap_days": 0,
            "first_observed": None, "last_observed": None,
        })
        if entry["first_observed"] is None or (
                r["first_observed"] and r["first_observed"] < entry["first_observed"]):
            entry["first_observed"] = r["first_observed"]
        if entry["last_observed"] is None or (
                r["last_observed"] and r["last_observed"] > entry["last_observed"]):
            entry["last_observed"] = r["last_observed"]
        entry["companies"].setdefault(r["canonical_company_id"], 0)
        entry["companies"][r["canonical_company_id"]] += 1
        entry["contexts"][r["role_context"]] = entry["contexts"].get(
            r["role_context"], 0) + 1
        entry["classes"][r["co_participation_class"]] = entry["classes"].get(
            r["co_participation_class"], 0) + 1
        entry["direct_evidence"] += (r["same_snapshot_count"] or 0) + \
                                    (r["same_source_event_count"] or 0)
        if r["known_overlap_days"]:
            entry["known_overlap_days"] += r["known_overlap_days"]
        # mixed-authority fallback metadata stays explainable (B16)
        if r["source_family"] == "PDF_HISTORY" and r["authority_class"] is None:
            entry["mixed_authority_fallback"] = True

    collaborators = []
    for peer, e in by_peer.items():
        context_breakdown = [
            {"context": c, "label": ROLE_CONTEXT_LABELS.get(c, c), "count": n}
            for c, n in sorted(e["contexts"].items(), key=lambda kv: -kv[1])]
        if context_filter and context_filter not in e["contexts"]:
            continue
        class_breakdown = [
            {"class": cl, "label": PARTICIPATION_CLASS_LABELS.get(cl, cl),
             "count": n}
            for cl, n in sorted(e["classes"].items(), key=lambda kv: -kv[1])]
        if class_filter and not any(cb["class"] == class_filter
                                    for cb in class_breakdown):
            continue
        collaborators.append({
            "canonical_person_id": peer,
            "display_name": e["display_name"],
            "shared_company_count": len(e["companies"]),
            "context_breakdown": context_breakdown,
            "class_breakdown": class_breakdown,
            "direct_evidence_count": e["direct_evidence"],
            "known_overlap_days": e["known_overlap_days"] or None,
            "first_observed": _dk_date(e["first_observed"]),
            "last_observed": _dk_date(e["last_observed"]),
            "mixed_authority_fallback": e.get("mixed_authority_fallback", False),
        })
    collaborators.sort(key=lambda c: (
        -c["shared_company_count"], -c["direct_evidence_count"],
        -(c["known_overlap_days"] or 0),
        c["canonical_person_id"]))
    return {"build_run_id": build["build_run_id"],
            "content_hash": build["content_hash"],
            "coverage_display": build["coverage_display"],
            "collaborators": collaborators[:limit],
            "total": len(collaborators),
            "shown": min(limit, len(collaborators)),
            "sort_documentation": COLLABORATOR_SORT,
            "disclaimer": ANALYTICS_DISCLAIMER}


def get_pair_evidence(conn, person_a_id, person_b_id, build=None):
    """Pair drilldown: rows + the contributing same-build observations (B33).

    Provenance is the Phase 3 model, not new inference: for every pair row,
    both persons' observations in (company, context) reconstruct the fact.
    """
    if build is None:
        build = get_active_analytics_build(conn)
    if build is None:
        return None
    a, b = min(int(person_a_id), int(person_b_id)), \
           max(int(person_a_id), int(person_b_id))
    try:
        pair_rows = repo.pair_rows_between(conn, build["build_run_id"], a, b)
        obs = repo.observations_for_pairs(
            conn, build["build_run_id"],
            [(a, r["canonical_company_id"]) for r in pair_rows] +
            [(b, r["canonical_company_id"]) for r in pair_rows])
        names = repo.names_for(conn, [a, b] +
                               [r["canonical_company_id"] for r in pair_rows])
    except psycopg.Error as exc:
        _logger.warning("pair evidence read degraded: %s", repr(exc))
        return {"build_run_id": build["build_run_id"],
                "content_hash": build["content_hash"], "error": True,
                "rows": [], "persons": {}}
    person_names = {a: (names.get(a) or {}).get("display_name"),
                    b: (names.get(b) or {}).get("display_name")}
    company_names = {i: (names.get(i) or {}).get("display_name")
                     for i in {r["canonical_company_id"] for r in pair_rows}}
    obs_by_pc = {}
    for o in obs:
        obs_by_pc.setdefault((o["canonical_person_id"],
                              o["canonical_company_id"]), []).append(o)
    rows_out = []
    for r in pair_rows:
        contributing = obs_by_pc.get((a, r["canonical_company_id"]), []) + \
                       obs_by_pc.get((b, r["canonical_company_id"]), [])
        fams = sorted({o["source_family"] for o in contributing if o["source_family"]})
        auths = sorted({o["authority_class"] for o in contributing if o["authority_class"]})
        rows_out.append({
            "pair_id": r["pair_id"],
            "company": {"canonical_company_id": r["canonical_company_id"],
                        "name": company_names.get(r["canonical_company_id"])},
            "context": r["role_context"],
            "context_label": ROLE_CONTEXT_LABELS.get(r["role_context"],
                                                     r["role_context"]),
            "class": r["co_participation_class"],
            "class_label": PARTICIPATION_CLASS_LABELS.get(
                r["co_participation_class"], r["co_participation_class"]),
            "observation_count": r["observation_count"],
            "episode_count": r["episode_count"],
            "known_overlap_days": r["known_overlap_days"],
            "first_observed": _dk_date(r["first_observed"]),
            "last_observed": _dk_date(r["last_observed"]),
            "stored_source_family": r["source_family"],
            "stored_source_family_label": SOURCE_FAMILY_LABELS.get(
                r["source_family"], r["source_family"]),
            "stored_authority_class": r["authority_class"],
            "stored_authority_label": AUTHORITY_CLASS_LABELS.get(
                r["authority_class"], r["authority_class"]),
            "contributing_source_families": fams,
            "contributing_authorities": auths,
            "metadata_incomplete": (len(fams) > 1 or len(auths) > 1),
            "fallback_note": (FALLBACK_AUTHORITY_NOTE
                              if r["source_family"] == "PDF_HISTORY"
                              and r["authority_class"] is None else
                              (FALLBACK_FAMILY_NOTE if r["source_family"] is None
                               else None)),
            "evidence": [{
                "person": person_names.get(o["canonical_person_id"]),
                "kind": o["observation_kind"],
                "kind_label": OBSERVATION_KIND_LABELS.get(
                    o["observation_kind"], o["observation_kind"]),
                "observed_at": _dk_date(o["observed_at"]),
                "action": o["action"],
                "source_family": o["source_family"],
                "source_family_label": SOURCE_FAMILY_LABELS.get(
                    o["source_family"], o["source_family"]),
                "authority_class": o["authority_class"],
                "authority_label": AUTHORITY_CLASS_LABELS.get(
                    o["authority_class"], o["authority_class"]),
                "source_document_id": o["source_document_id"],
                "source_event_id": o["source_event_id"],
                "source_relation_id": o["source_relation_id"],
                "source_attempt_id": o["source_attempt_id"],
            } for o in sorted(contributing,
                              key=lambda x: (x["observed_at"] is None,
                                             x["observed_at"], x["observation_id"]))],
        })
    return {"build_run_id": build["build_run_id"],
            "content_hash": build["content_hash"],
            "persons": person_names, "rows": rows_out,
            "disclaimer": ANALYTICS_DISCLAIMER}


# --- periods / role history (B17/B18) ----------------------------------------

def _period_view(p):
    """Render metadata that preserves the period semantics exactly (B18)."""
    status = p["period_status"]
    start = _dk_date(p["period_start"])
    end = _dk_date(p["period_end"])
    if status == "BOUNDED" and start and end:
        text = f"Observeret funktionstid {start} – {end}"
        show_interval = True
    elif status == "OPEN_LEFT_CENSORED":
        text = (f"Observeret fra {start}; oprindelig start ukendt"
                if start else "Observeret; oprindelig start ukendt")
        show_interval = False
    elif status == "CLOSED_ONLY":
        text = "Tidligere registreret; start ukendt"
        show_interval = False
    else:
        text = status or "Ukendt periodesemantik"
        show_interval = False
    return {"period_id": p["period_id"],
            "company_id": p["canonical_company_id"],
            "context": p["role_context"],
            "context_label": ROLE_CONTEXT_LABELS.get(p["role_context"],
                                                     p["role_context"]),
            "period_start": start, "period_end": end,
            "start_status": p["start_status"], "end_status": p["end_status"],
            "period_status": status, "source_family": p["source_family"],
            "source_family_label": SOURCE_FAMILY_LABELS.get(
                p["source_family"], p["source_family"]),
            "duration_known": show_interval, "text": text}


def get_person_role_history(conn, person_id, build=None, limit=40):
    """Observed role history: periods (with their exact semantics) + the
    AUTHORITATIVE_CURRENT_GOVERNANCE observations as current roles (B17/B21)."""
    if build is None:
        build = get_active_analytics_build(conn)
    if build is None:
        return None
    try:
        periods = repo.periods_for_person(conn, build["build_run_id"], person_id)
        current = repo.current_authoritative_observations(
            conn, build["build_run_id"], [person_id])
        names = repo.names_for(conn, [p["canonical_company_id"] for p in periods] +
                               [o["canonical_company_id"] for o in current])
    except psycopg.Error as exc:
        _logger.warning("person role history read degraded: %s", repr(exc))
        return {"build_run_id": build["build_run_id"],
                "content_hash": build["content_hash"], "error": True,
                "periods": [], "current_roles": []}
    current_roles = {}
    for o in current:
        cid = o["canonical_company_id"]
        current_roles.setdefault(cid, {
            "company_id": cid,
            "company_name": (names.get(cid) or {}).get("display_name"),
            "contexts": set(), "observed_at": None})
        current_roles[cid]["contexts"].add(o["role_context"])
        if o["observed_at"] and (current_roles[cid]["observed_at"] is None
                                 or o["observed_at"] > current_roles[cid]["observed_at"]):
            current_roles[cid]["observed_at"] = o["observed_at"]
    current_out = [{
        "company_id": c["company_id"],
        "company_name": c["company_name"],
        "contexts": [ROLE_CONTEXT_LABELS.get(x, x) for x in sorted(c["contexts"])],
        "observed": _dk_date(c["observed_at"]),
        "authority_label": AUTHORITY_CLASS_LABELS[
            "AUTHORITATIVE_CURRENT_GOVERNANCE"],
    } for c in current_roles.values()]
    periods_out = []
    for p in periods:
        pv = _period_view(p)
        pv["company_name"] = (names.get(p["canonical_company_id"]) or {}).get(
            "display_name")
        periods_out.append(pv)
    return {"build_run_id": build["build_run_id"],
            "content_hash": build["content_hash"],
            "periods": periods_out[:limit], "period_total": len(periods_out),
            "current_roles": current_out,
            "period_semantics": {"BOUNDED": PERIOD_STATUS_TEXT["BOUNDED"],
                                 "OPEN_LEFT_CENSORED": "observeret fra første registrering — start kan være left-censored",
                                 "CLOSED_ONLY": "afsluttet uden kendt start"},
            "disclaimer": ANALYTICS_DISCLAIMER}


# --- groups (B20/B22/B23/B24/B25) --------------------------------------------

def _support_breakdown(support):
    if not isinstance(support, dict):
        return {}
    return {"support_company_count": support.get("support_company_count"),
            "support_episode_count": support.get("support_episode_count"),
            "episode_kind_counts": support.get("episode_kind_counts") or {}}


def _group_view(g, members):
    kind = g["group_kind"]
    return {"group_id": g["group_id"],
            "member_set_hash": g["member_set_hash"],
            "context": g["role_context"],
            "context_label": ROLE_CONTEXT_LABELS.get(g["role_context"],
                                                     g["role_context"]),
            "context_title": ROLE_CONTEXT_TITLE.get(
                g["role_context"],
                f'{ROLE_CONTEXT_LABELS.get(g["role_context"], g["role_context"])}-konstellation'),
            "group_kind": kind,
            "group_kind_label": GROUP_KIND_LABELS.get(kind, kind),
            "group_size": g["group_size"],
            "support": _support_breakdown(g["support"]),
            "first_observed": _dk_date(g["first_observed"]),
            "last_observed": _dk_date(g["last_observed"]),
            "members": members}


def _observation_wording(first_raw, last_raw, episode_kinds):
    """Honest observation wording for one constellation (owner UX correction
    UX-02): a point observation is never rendered as a duration. A dash
    interval is allowed ONLY for bounded SAFE_PERIOD_OVERLAP evidence; mixed
    or point kinds get explicit observation labels instead."""
    first, last = _dk_date(first_raw), _dk_date(last_raw)
    if not first:
        return ""
    kinds = set(episode_kinds or ())
    if first == last:
        if kinds == {"SAME_EVENT"}:
            return f"Registreringshændelse {first}"
        return f"Observeret {first}"
    if kinds == {"SAFE_PERIOD_OVERLAP"} and last:
        return f"Observeret overlap {first}–{last}"
    if not last:
        return f"Observeret {first}"
    return (f"Første observeret {first} · Senest observeret {last}")


def _member_preview(members, preview=3):
    """Collapsed-state member identity (owner UX correction UX-07): the first
    few canonical names + how many more; every name stays canonical-only."""
    members = members or []
    shown = [{"canonical_person_id": m.get("canonical_person_id"),
              "display_name": m.get("display_name")} for m in members[:preview]]
    return {"shown": shown, "rest": max(len(members) - preview, 0)}


def _constellation_order_key(entry):
    """§6 deterministic constellation ordering — transparent, no score:
    support_company_count DESC, support_episode_count DESC, group_size DESC,
    last_observed DESC (unknown last), then member_set_hash / context /
    group_id as the stable tiebreak."""
    sup = entry.get("support") or {}
    last = entry.get("last_observed_raw")
    try:
        last_sort = (-last.toordinal()) if last is not None else 0
    except AttributeError:
        last_sort = 0  # non-date raw value: fall through to the stable tail
    return (-(sup.get("support_company_count") or 0),
            -(sup.get("support_episode_count") or 0),
            -(entry.get("group_size") or 0),
            0 if last is not None else 1,
            last_sort,
            entry.get("member_set_hash") or "",
            entry.get("context") or "",
            entry.get("group_id") or 0)


CONSTELLATION_ORDER = ("support_company_count DESC, support_episode_count "
                       "DESC, group_size DESC, last_observed DESC (unknown "
                       "last), member_set_hash, role_context, group_id")

# Product UX consolidation (§27): the deterministic order key stays internal
# (CONSTELLATION_ORDER, tests/debug only) — normal UI gets human copy only.
CONSTELLATION_ORDER_TEXT = ("Visningen prioriterer grupper, der går igen i "
                            "flest virksomheder og observationer. Rækkefølgen "
                            "er deterministisk og bruger ingen skjult score.")

# §31/§32: family presentation is a READ-ONLY display grouping on top of the
# persisted (member_set_hash, role_context) groups. Family selection is
# deterministic and score-free: strict member-set subset within the SAME
# role_context folds under the maximal member set; ordering below.
FAMILY_HEADLINE_CAP = 8


def _member_set(gv):
    """frozenset of canonical person ids for one merged group view."""
    return frozenset(m.get("canonical_person_id") for m in gv.get("members") or [])


def _family_order_key(gv, member_sets):
    """§32 deterministic family order: maximal member sets first, then
    support_company_count DESC, support_episode_count DESC, then the stable
    (member_set_hash, context, group_id) tiebreak. No score anywhere."""
    sup = gv.get("support") or {}
    return (-(len(member_sets[id(gv)])),
            -(sup.get("support_company_count") or 0),
            -(sup.get("support_episode_count") or 0),
            gv.get("member_set_hash") or "",
            gv.get("context") or "",
            gv.get("group_id") or 0)


def _build_constellation_families(merged_views):
    """§31-§35 read-only family view-model.

    Within one role_context, a group whose member set is a strict SUBSET of
    another group's member set becomes a family subgroup of the maximal set;
    the maximal group is the family headline ("Vis den mest informative
    gruppe"). Subgroups stay fully rendered (members, support, evidence) in a
    disclosure — nothing is deleted or merged (§33). Groups that are not
    subset-related stay distinct (§61). No persisted family table, no score
    (§32): selection and ordering are deterministic on ids/counts only.
    A group attaches to exactly ONE headline (the first in deterministic
    headline order), so the family list never duplicates a group.
    """
    by_context: dict[str, list[dict]] = {}
    for gv in merged_views:
        by_context.setdefault(gv.get("context"), []).append(gv)
    member_sets = {id(gv): _member_set(gv) for gv in merged_views}
    families = []
    for _ctx, views in by_context.items():
        maximal = []
        for gv in views:
            s = member_sets[id(gv)]
            if not s:
                maximal.append(gv)  # empty set: never a subset, never a headline of others
                continue
            if not any(o is not gv and member_sets[id(o)] > s for o in views):
                maximal.append(gv)
        maximal.sort(key=lambda gv: _family_order_key(gv, member_sets))
        attached: set[int] = set()
        for headline in maximal:
            s = member_sets[id(headline)]
            subs = []
            if s:
                for gv in views:
                    if gv is headline or id(gv) in attached:
                        continue
                    so = member_sets[id(gv)]
                    if so and so < s:
                        subs.append(gv)
                        attached.add(id(gv))
            subs.sort(key=lambda gv: _family_order_key(gv, member_sets))
            families.append({"headline": headline,
                             "subgroups": subs,
                             "subgroup_count": len(subs),
                             "context": headline.get("context"),
                             "context_title": headline.get("context_title"),
                             "context_label": headline.get("context_label")})
    families.sort(key=lambda f: _family_order_key(f["headline"], member_sets))
    return families


def _merge_core_recurring(group_views):
    """B23: one member-set presentation when CORE and RECURRING share
    (member_set_hash, context); the underlying distinct rows stay intact."""
    by_key = {}
    for gv in group_views:
        key = (gv["member_set_hash"], gv["context"])
        cur = by_key.get(key)
        if cur is None:
            entry = dict(gv)
            entry["kinds"] = [gv["group_kind"]]
            entry["kinds_labels"] = [gv["group_kind_label"]]
            by_key[key] = entry
        else:
            if gv["group_kind"] not in cur["kinds"]:
                cur["kinds"].append(gv["group_kind"])
                cur["kinds_labels"].append(gv["group_kind_label"])
            # RECURRING carries the shared member-set view; keep the higher
            # support counts visible as provenance, not as extra cards
            if gv["group_kind"] == "RECURRING_GROUP":
                cur["support"] = gv["support"]
    return list(by_key.values())


def _decorate_groups(conn, build, groups):
    group_ids = [g["group_id"] for g in groups]
    members = repo.group_members_bulk(conn, build["build_run_id"], group_ids)
    observations = repo.group_observations(conn, build["build_run_id"], group_ids)
    obs_member_map = repo.group_observation_members(
        conn, build["build_run_id"], [o["group_observation_id"]
                                      for o in observations])
    company_ids = {o["canonical_company_id"] for o in observations}
    names = repo.names_for(conn, company_ids)
    views = []
    for g in groups:
        gv = _group_view(g, members.get(g["group_id"], []))
        raw_kinds = sorted({o["co_observation_kind"] for o in observations
                            if o["group_id"] == g["group_id"]})
        gv["episode_kinds"] = raw_kinds
        gv["episode_kinds_labels"] = [EPISODE_KIND_LABELS.get(k, k)
                                      for k in raw_kinds]
        companies = {o["canonical_company_id"]:
                     (names.get(o["canonical_company_id"]) or {}).get("display_name")
                     for o in observations if o["group_id"] == g["group_id"]}
        gv["companies"] = [{"canonical_company_id": cid, "name": nm}
                           for cid, nm in sorted(companies.items())]
        gv["first_observed_raw"] = g["first_observed"]
        gv["last_observed_raw"] = g["last_observed"]
        gv["observation_wording"] = _observation_wording(
            g["first_observed"], g["last_observed"], gv["episode_kinds"])
        gv["member_preview"] = _member_preview(members.get(g["group_id"]))
        views.append(gv)
    return views


def _groups_result(build, merged, limit):
    """Shared groups response (§31): deterministic order + family view-model.
    `groups` (flat) stays for evidence drilldown/tests; `families` is the
    presentation layer; nothing is lost between the two (§33).

    `families` is UNCAP — every family is delivered so the template's
    "Vis flere"-disclosure can render the rest beyond the 8-family headline
    cap (reviewer finding: capped payload + `[8:]` slice rendered an empty
    disclosure and made family 9+ unreachable). `shown_families` states how
    many render as headlines before folding."""
    families = _build_constellation_families(merged)
    return {"build_run_id": build["build_run_id"],
            "content_hash": build["content_hash"],
            "groups": merged,
            "total": len(merged),
            "families": families,
            "family_total": len(families),
            "shown_families": min(FAMILY_HEADLINE_CAP, len(families)),
            "group_total": len(merged),
            "disclaimer": ANALYTICS_DISCLAIMER,
            "order": CONSTELLATION_ORDER,
            "order_text": CONSTELLATION_ORDER_TEXT}


def get_person_groups(conn, person_id, build=None, limit=12,
                      group_kind_filter=None):
    if build is None:
        build = get_active_analytics_build(conn)
    if build is None:
        return None
    try:
        rows = repo.groups_for_person(conn, build["build_run_id"], person_id)
        if group_kind_filter:
            want = {"RECURRING": "RECURRING_GROUP",
                    "CORE": "CORE_GROUP"}.get(group_kind_filter,
                                              group_kind_filter)
            rows = [r for r in rows if r["group_kind"] == want]
        views = _decorate_groups(conn, build, rows)
    except psycopg.Error as exc:
        _logger.warning("person groups read degraded: %s", repr(exc))
        return {"build_run_id": build["build_run_id"],
                "content_hash": build["content_hash"], "error": True,
                "groups": []}
    merged = _merge_core_recurring(views)
    merged.sort(key=_constellation_order_key)
    return _groups_result(build, merged, limit)


def get_company_groups(conn, company_id, build=None, limit=15,
                       group_kind_filter=None):
    if build is None:
        build = get_active_analytics_build(conn)
    if build is None:
        return None
    try:
        rows = repo.groups_for_company(conn, build["build_run_id"], company_id)
        if group_kind_filter:
            want = {"RECURRING": "RECURRING_GROUP",
                    "CORE": "CORE_GROUP"}.get(group_kind_filter,
                                              group_kind_filter)
            rows = [r for r in rows if r["group_kind"] == want]
        views = _decorate_groups(conn, build, rows)
    except psycopg.Error as exc:
        _logger.warning("company groups read degraded: %s", repr(exc))
        return {"build_run_id": build["build_run_id"],
                "content_hash": build["content_hash"], "error": True,
                "groups": []}
    merged = _merge_core_recurring(views)
    merged.sort(key=_constellation_order_key)
    return _groups_result(build, merged, limit)


def get_group_evidence(conn, group_id, build=None):
    """Group drilldown: episode -> member junction observations -> source."""
    if build is None:
        build = get_active_analytics_build(conn)
    if build is None:
        return None
    try:
        g = repo.group_by_id(conn, build["build_run_id"], group_id)
        if g is None:
            return None
        members = repo.group_members_bulk(conn, build["build_run_id"], [g["group_id"]])
        observations = repo.group_observations(conn, build["build_run_id"], [g["group_id"]])
        obs_members = repo.group_observation_members(
            conn, build["build_run_id"], [o["group_observation_id"] for o in observations])
        sources = repo.group_observation_sources(
            conn, build["build_run_id"], [m["observation_id"] for ms in obs_members.values()
                                          for m in ms])
        names = repo.names_for(conn, [o["canonical_company_id"] for o in observations])
    except psycopg.Error as exc:
        _logger.warning("group evidence read degraded: %s", repr(exc))
        return {"build_run_id": build["build_run_id"],
                "content_hash": build["content_hash"], "error": True}
    episodes = []
    for o in observations:
        junction = obs_members.get(o["group_observation_id"], [])
        ep = {"group_observation_id": o["group_observation_id"],
              "company": {"canonical_company_id": o["canonical_company_id"],
                          "name": (names.get(o["canonical_company_id"]) or {}).get(
                              "display_name")},
              "context": o["role_context"],
              "context_label": ROLE_CONTEXT_LABELS.get(o["role_context"],
                                                       o["role_context"]),
              "episode_start": _dk_date(o["episode_start"]),
              "episode_end": _dk_date(o["episode_end"]),
              "episode_key": o["episode_key"],
              "co_observation_kind": o["co_observation_kind"],
              "source_family": o["source_family"],
              "source_family_label": SOURCE_FAMILY_LABELS.get(
                  o["source_family"], o["source_family"]),
              "members": [{"canonical_person_id": m["canonical_person_id"],
                           "name": m["display_name"],
                           "role_in_episode": m["role_in_episode"]} for m in junction],
              "sources": []}
        seen = set()
        for m in junction:
            src = sources.get(m["observation_id"])
            if src is None:
                continue
            key = (src["source_document_id"], src["source_event_id"],
                   src["source_relation_id"], src["source_attempt_id"])
            if key in seen:
                continue
            seen.add(key)
            ep["sources"].append({
                "source_family": src["source_family"],
                "source_family_label": SOURCE_FAMILY_LABELS.get(
                    src["source_family"], src["source_family"]),
                "authority_class": src["authority_class"],
                "authority_label": AUTHORITY_CLASS_LABELS.get(
                    src["authority_class"], src["authority_class"]),
                "observed_at": _dk_date(src["observed_at"]),
                "source_document_id": src["source_document_id"],
                "source_event_id": src["source_event_id"],
                "source_history_event_id": src["source_history_event_id"],
                "source_relation_id": src["source_relation_id"],
                "source_attempt_id": src["source_attempt_id"],
                "source_projection_run_id": src["source_projection_run_id"]})
        episodes.append(ep)
    gv = _group_view(g, members.get(g["group_id"], []))
    gv["episodes"] = episodes
    return {"build_run_id": build["build_run_id"],
            "content_hash": build["content_hash"], "group": gv,
            "disclaimer": ANALYTICS_DISCLAIMER}


# --- company people (B20/B21) -------------------------------------------------

def get_company_people_analytics(conn, company_id, build=None, limit=60):
    """Analytics supplement for the company's Personer tab. Current Felag
    governance is surfaced as its own authoritative bucket (B21); historical
    PDF evidence and ownership points stay separate buckets."""
    if build is None:
        build = get_active_analytics_build(conn)
    if build is None:
        return None
    try:
        obs = repo.observations_for_company(conn, build["build_run_id"], company_id)
        period_counts = repo.period_counts_for_company(
            conn, build["build_run_id"], [company_id])
        names = repo.names_for(
            conn, [o["canonical_person_id"] for o in obs] +
                  [p["canonical_person_id"] for p in period_counts])
    except psycopg.Error as exc:
        _logger.warning("company people analytics read degraded: %s", repr(exc))
        return {"build_run_id": build["build_run_id"],
                "content_hash": build["content_hash"], "error": True,
                "people": []}
    people = {}
    for o in obs:
        pid = o["canonical_person_id"]
        entry = people.setdefault(pid, {
            "canonical_person_id": pid,
            "display_name": (names.get(pid) or {}).get("display_name"),
            "current_authoritative": False,
            "observation_count": 0,
            "first_observed": None, "last_observed": None,
            "ownership_observed": None})
        entry["observation_count"] += 1
        if o["authority_class"] == "AUTHORITATIVE_CURRENT_GOVERNANCE" and \
                o["role_context"] in ("BOARD", "MANAGEMENT"):
            entry["current_authoritative"] = True
        if o["observed_at"]:
            if entry["first_observed"] is None or o["observed_at"] < entry["first_observed"]:
                entry["first_observed"] = o["observed_at"]
            if entry["last_observed"] is None or o["observed_at"] > entry["last_observed"]:
                entry["last_observed"] = o["observed_at"]
        if o["source_family"] == "OWNERSHIP_GRAPH" and o["observed_at"] and \
                (entry["ownership_observed"] is None
                 or o["observed_at"] > entry["ownership_observed"]):
            entry["ownership_observed"] = o["observed_at"]
    # periods add observed-function-time counts per (person, context)
    for p in period_counts:
        pid = p["canonical_person_id"]
        people.setdefault(pid, {
            "canonical_person_id": pid,
            "display_name": (names.get(pid) or {}).get("display_name"),
            "current_authoritative": False, "observation_count": 0,
            "first_observed": None, "last_observed": None,
            "ownership_observed": None})
        people[pid].setdefault("period_count", 0)
        people[pid]["period_count"] += p["period_count"]
    # per-person context list from the same batched period counts
    contexts_by_person = {}
    for p in period_counts:
        contexts_by_person.setdefault(p["canonical_person_id"], []).append(
            (p["role_context"], p["period_count"]))
    out = []
    for pid, e in people.items():
        e["contexts"] = [
            {"context": ctx, "label": ROLE_CONTEXT_LABELS.get(ctx, ctx),
             "period_count": n}
            for ctx, n in sorted(contexts_by_person.get(pid, []),
                                 key=lambda kv: -kv[1])]
        e["first_observed"] = _dk_date(e["first_observed"])
        e["last_observed"] = _dk_date(e["last_observed"])
        e["ownership_observed"] = _dk_date(e["ownership_observed"])
        out.append(e)
    out.sort(key=lambda e: (not e["current_authoritative"],
                            -(e.get("observation_count") or 0),
                            e["display_name"] or "",
                            e["canonical_person_id"]))
    return {"build_run_id": build["build_run_id"],
            "content_hash": build["content_hash"],
            "people": out[:limit], "total": len(out),
            "disclaimer": ANALYTICS_DISCLAIMER}


def get_coverage(conn, build=None):
    if build is None:
        build = get_active_analytics_build(conn)
    if build is None:
        return None
    return {"build_run_id": build["build_run_id"],
            "content_hash": build["content_hash"],
            "coverage": build["coverage_display"]}

# --- episode rendering (B26/B27/B28) ------------------------------------------

EPISODE_KIND_LABELS = {
    "SAME_SNAPSHOT": "Samme registrerede snapshot",
    "SAME_EVENT": "Samme registreringshændelse",
    "SAFE_PERIOD_OVERLAP": "Observeret overlap",
}


EPISODE_KIND_RENDER = {
    "SAME_SNAPSHOT": {"label": "Samme registrerede snapshot",
                      "template": "Observeret sammen i samme registrerede snapshot",
                      "duration_allowed": False},
    "SAME_EVENT": {"label": "Samme registreringshændelse",
                   "template": "Observeret sammen i samme registreringshændelse",
                   "duration_allowed": False},
    "SAFE_PERIOD_OVERLAP": {"label": "Observeret overlap",
                            "template": "Observeret overlap",
                            "duration_allowed": True},
}


def episode_render_text(episode):
    """Temporal wording per episode kind (B26/B27/B28): an interval is shown
    only when the kind's semantics allow it AND the episode is bounded."""
    kind = episode.get("co_observation_kind")
    spec = EPISODE_KIND_RENDER.get(kind) or {
        "label": kind, "template": kind, "duration_allowed": False}
    text = spec["template"]
    if spec["duration_allowed"] and episode.get("episode_start") \
            and episode.get("episode_end"):
        text += f" {episode['episode_start']} – {episode['episode_end']}"
    elif spec["duration_allowed"] and episode.get("episode_start"):
        text += f" fra {episode['episode_start']}"
    return text


# --- composed section builders (used by app.py template globals) --------------

def _section_error(conn, exc, sections=None):
    _logger.warning("analytics section degraded: %s", repr(exc))
    base = {"error": True, "build": None, "sections": sections or {}}
    return base


def person_analytics_section(conn, person_id):
    """Full person-page section, or None (fail closed / no ACTIVE build).

    Sections: collaborators (query-time), groups, role history, coverage.
    Any read failure degrades this section only — the person page itself is
    never affected (B51).
    """
    if not analytics_enabled():
        return None
    try:
        build = get_active_analytics_build(conn)
        if build is None:
            return None
        section = {"allowed": True, "build": build,
                   "collaborators": get_person_collaborators(
                       conn, person_id, build=build),
                   "groups": get_person_groups(conn, person_id, build=build),
                   "role_history": get_person_role_history(
                       conn, person_id, build=build),
                   "labels": {"heading": "Observeret samarbejde (analyse)",
                              "groups_heading": "Tilbagevendende konstellationer",
                              "history_heading": "Observeret funktionstid",
                              "coverage_heading": "Dækning",
                              "disclaimer": ANALYTICS_DISCLAIMER,
                              "empty_collaborators": "Ingen sikkert observerede samarbejder i dette build",
                              "empty_groups": "Ingen understøttede tilbagevendende konstellationer",
                              "empty_overlap": "Ingen sikkert afgrænset overlap"}}
        return section
    except Exception as exc:  # noqa: BLE001 — B51: the page must never break
        return _section_error(conn, exc)


def relation_analytics_supplement(conn, person_id, peer_ids):
    """B29: pair analytics as an explaining supplement next to the existing
    canonical/source relation facts — never a replacement. Returns
    {peer_id: summary} for the peers actually rendered."""
    if not analytics_enabled():
        return None
    try:
        build = get_active_analytics_build(conn)
        if build is None:
            return None
        data = get_person_collaborators(conn, person_id, build=build,
                                        limit=1000)
        if data is None or data.get("error"):
            return None
        wanted = {int(p) for p in peer_ids}
        supplement = {}
        for c in data["collaborators"]:
            if c["canonical_person_id"] in wanted:
                supplement[c["canonical_person_id"]] = {
                    "shared_company_count": c["shared_company_count"],
                    "context_breakdown": c["context_breakdown"],
                    "class_breakdown": c["class_breakdown"],
                    "known_overlap_days": c["known_overlap_days"],
                }
        supplement["_build_run_id"] = data["build_run_id"]
        return supplement
    except Exception as exc:  # noqa: BLE001
        return _section_error(conn, exc)


def company_analytics_section(conn, company_id):
    """Company-page ANALYSE supplement (product UX consolidation §29/§14):
    derived constellations (family view-model) + historical person
    associations, kept clearly separate from the current Felag roles in the
    Personer panel. Fail closed like the person section."""
    if not analytics_enabled():
        return None
    try:
        build = get_active_analytics_build(conn)
        if build is None:
            return None
        people_data = get_company_people_analytics(conn, company_id, build=build)
        historical = []
        if people_data and not people_data.get("error"):
            for e in people_data.get("people") or []:
                if e.get("current_authoritative"):
                    continue  # current governance renders in Personer (Felag)
                contexts = e.get("contexts") or []
                historical.append({
                    "person_id": e["canonical_person_id"],
                    "name": e.get("display_name"),
                    "context_label": ", ".join(c["label"] for c in contexts[:2]) or None,
                    "period_text": (("Første observeret " + e["first_observed"])
                                    if e.get("first_observed") else None),
                    "observation_count": e.get("observation_count") or 0,
                })
        section = {"allowed": True, "build": build,
                   "groups": get_company_groups(conn, company_id, build=build,
                                                limit=60),
                   "coverage": build["coverage_display"],
                   "people": {"historical": historical},
                   "labels": {"heading": "Konstellationer i analyse-buildet",
                              "coverage_note": COVERAGE_NOTE,
                              "disclaimer": ANALYTICS_DISCLAIMER,
                              "empty_groups": "Ingen understøttede tilbagevendende konstellationer"}}
        return section
    except Exception as exc:  # noqa: BLE001
        return _section_error(conn, exc)


def network_analytics_summary(conn, person_id, limit=5):
    """B30 minimal network integration: a small read-only summary with
    click-through to the existing person pages. No graph model changes."""
    if not analytics_enabled():
        return None
    try:
        build = get_active_analytics_build(conn)
        if build is None:
            return None
        data = get_person_collaborators(conn, person_id, build=build, limit=limit)
        if data is None or data.get("error") or not data["collaborators"]:
            return None
        return {"build": {"build_run_id": build["build_run_id"],
                          "content_hash": build["content_hash"]},
                "top": data["collaborators"][:limit],
                "note": "Observeret samarbejde (analyse) — se personens Analyse-fane"}
    except Exception as exc:  # noqa: BLE001
        return _section_error(conn, exc)


def network_preview_summary(conn, entity_id, entity_kind):
    """§37 compact network summary numbers for the detail-page preview.
    Read-only aggregation of the SAME ego-graph query the full /network page
    uses (ego_graph + ego_layout) — no second graph model. Any read failure
    returns None and the preview renders without counts (fail closed)."""
    try:
        from heimdal.modules.shared.entity import entity as load_entity
        from heimdal.modules.entities.service import ego_graph, ego_layout
        ent = load_entity(conn, entity_id)
        if ent is None or ent["entity_kind"] not in ("PERSON", "COMPANY"):
            return None
        layout = ego_layout(ego_graph(conn, entity_id, ent["source_entity_id"]))
        nodes = layout.get("nodes") or []
        return {"companies": sum(1 for n in nodes
                                 if n.get("kind") == "COMPANY"),
                "persons": sum(1 for n in nodes if n.get("kind") == "PERSON"),
                "relations": len(layout.get("edges") or []),
                "groups": 0,
                "truncated": layout.get("truncated", False)}
    except Exception as exc:  # noqa: BLE001 — preview must never break the page
        _logger.warning("network preview summary degraded: %s", repr(exc))
        return None
