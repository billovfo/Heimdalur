# heimdal/modules/entities/routes.py
"""Entity browsing routes: /bestand, /companies, /persons, /entities/<id>,
/kildeinformation/<id>, /persons/relationer, /persons/<id>, /companies/<id>,
/companies/<id>/eigarayvirlit, /api/inspect/<id> (10 routes)."""
from flask import Blueprint, abort, jsonify, redirect, render_template, request
from heimdal.config import INCOMING, OUTGOING, PAGE_SIZE, _DIRECT_COLS
from heimdal.modules.compliance.repository import registration_patterns
from heimdal.modules.compliance.service import entity_structure_status
from heimdal.modules.shared.db import conn, either_side
from heimdal.modules.shared import labels as _ui_labels
from heimdal.modules.shared.entity import (
    entity, provenance, fetch_state, entity_url, entity_relations,
)
from heimdal.modules.shared.templates import pct
from heimdal.modules.entities.service import (
    COMPANY_KINDS, PERSON_SORT_KEYS,
    RELATION_BASIS_LABELS, PERSON_RELATION_BASIS_LABELS,
    owners_of, holdings_of, ownership_sum,
    control_registration, control_registrations_of_actor,
    roles_at_company, voting_rights_at_company,
    roles_held_by_person, voting_rights_held_by_person,
    cycle_info, indirect_owners_of, indirect_view,
    hydrate_paths, top_persons, ownership_tree_layout,
    persons_behind, overview, entity_list, person_list,
    person_pep_info,
    company_relations_of, person_relations_of,
    governance_summary, product_role_label,
)
from heimdal.modules.groups.service import ultimate_parent_of
from heimdal.modules.snapshots.service import entity_timeline
from heimdal.modules.skraseting import views as skraseting_views
from ...pdfhist import consumer
from ...pdfhist.normalize import normalize_registration_number

entities_bp = Blueprint("entities", __name__)

# --- Entity history (owner defect task: ENTITY-SCOPED HISTORIK) -----------
# Skráseting event vocabulary, kept verbatim from the Skráseting view layer -
# sources stay distinguishable, no collapse of provenance.
SKRASETING_SOURCE_LABEL = "Skráseting Føroya"
SKRASETING_EVENT_LABELS = {
    "BOARD_CHANGE": "Bestyrelse ændret",
    "MANAGEMENT_CHANGE": "Ledelse ændret",
    "AUDITOR_CHANGE": "Revisor ændret",
    "NAME_CHANGE": "Navn ændret",
    "CAPITAL_CHANGE": "Kapital ændret",
    "SIGNING_RULE_CHANGE": "Signeringsregler ændret",
    "ARTICLES_CHANGE": "Vedtægter ændret",
    "PURPOSE_CHANGE": "Formål ændret",
    "ADDRESS_CHANGE": "Adresse ændret",
    "OTHER_CHANGE": "Registrering ændret",
}
SKRASETING_ACTION_LABELS = {
    "APPOINTED": "Tiltrådt",
    "REMOVED": "Fratrådt",
    "CHANGED": "Ændret",
}
# Heimdal snapshot change-feed presentation (existing global vocabulary,
# reused - the global log keeps its own semantics).
HEIMDAL_SOURCE_LABEL = "Heimdal snapshot"


@entities_bp.get("/bestand")
def bestand():
    """Det gamle dashboards indhold flyttet hertil (ejerbeslutning
    2026-08-15): enhedsbestand, relationer, indirekte ejerskab og seneste
    kørsler. Dashboardet selv viser noget andet."""
    return render_template("dashboard.html", o=overview(conn()))


def _listing_sort():
    s = request.args.get("sort", "name")
    return s if s.lstrip("-") in PERSON_SORT_KEYS else "name"


@entities_bp.get("/companies")
def companies():
    return render_template(
        "entities.html", clause="§16", title="Virksomheder", path="/companies",
        lede="Alle registrerede enheder, der ikke er personer.", regnr=True,
        listing=entity_list(conn(), COMPANY_KINDS,
                            request.args.get("q", "").strip(),
                            request.args.get("page", 1, type=int), 
                            _listing_sort(),
                            ownership_band=request.args.get("ownership_band"),
                            kind=request.args.get("kind")))


@entities_bp.get("/persons")
def persons():
    return render_template(
        "entities.html", clause="§17", title="Personer", path="/persons",
        lede="Kun kildens parsede navn. Antal-tallene tæller virksomheder, ikke ejerandele.",
        regnr=False,
        listing=person_list(conn(),
                            request.args.get("q", "").strip(),
                            request.args.get("page", 1, type=int), _listing_sort()))


@entities_bp.get("/entities/<int:canonical_entity_id>")
def entity_page(canonical_entity_id):
    """§3.4 minimal fallback for ORGANIZATION/PUBLIC_AUTHORITY/UNKNOWN_ENTITY:
    name, entity type, source-ID and a clickable list of the relations the
    entity appears in — nothing more. No §16/§17 sections. /entities/:id must
    never 404 for an existing canonical entity: PERSON/COMPANY redirect to
    their full portals; the three fallback kinds render the minimal page."""
    c = conn()
    ent = entity(c, canonical_entity_id)
    if ent is None:
        abort(404)
    if ent["entity_kind"] == "PERSON":
        return redirect(entity_url("PERSON", canonical_entity_id))
    if ent["entity_kind"] == "COMPANY":
        return redirect(entity_url("COMPANY", canonical_entity_id))
    prov = provenance(c, canonical_entity_id)
    return render_template(
        "entity.html", entity=ent, provenance=prov,
        fetch_state=fetch_state(prov),
        relations=entity_relations(c, canonical_entity_id))


@entities_bp.get("/kildeinformation/<int:canonical_entity_id>")
def sourceinfo(canonical_entity_id):
    """Separate source-information page (owner ruling 2026-08-15): the
    meta-facts and provenance details that used to clutter every entity page
    header live here — registration number, source type token, inventory
    status, both IDs, and the full fetch/provenance record. No raw person
    labels are ever rendered here (§5)."""
    c = conn()
    ent = entity(c, canonical_entity_id)
    if ent is None:
        abort(404)
    prov = provenance(c, canonical_entity_id)
    # the per-row direction confidence that used to clutter every ownership
    # table now lives here, on the source page (owner ruling 2026-08-15)
    rel_rows = c.execute(
        either_side(_DIRECT_COLS, "v_direct_ownership", "owner_id", "owned_id")
        + " ORDER BY owner_id, owned_id", {"id": canonical_entity_id}).fetchall()
    return render_template(
        "sourceinfo.html", entity=ent, provenance=prov,
        fetch_state=fetch_state(prov), rel_rows=rel_rows)


@entities_bp.get("/persons/relationer")
def person_relation_pairs():
    """Underside til /persons: alle personpar med fælles virksomheder,
    sorterbart og pagineret — dashboardets top 5 i fuld liste."""
    page = max(1, request.args.get("page", 1, type=int))
    sort = request.args.get("sort", "-n")
    key, desc = sort.lstrip("-"), sort.startswith("-")
    if key not in ("n", "a", "b"):
        key, desc = "n", True
    direction = "DESC" if desc else "ASC"
    order = {
        "n": f"s.n {direction}, a_name ASC, b_name ASC",
        "a": f"a_name {direction}, b_name ASC",
        "b": f"b_name {direction}, a_name ASC",
    }[key]
    c = conn()
    total = c.execute(
        "SELECT count(*) AS n FROM (SELECT person_a_entity_id, person_b_entity_id"
        " FROM registry_ownership.person_relation_context GROUP BY 1, 2) s").fetchone()["n"]
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(page, pages)
    rows = c.execute(
        "SELECT s.n, ca.canonical_entity_id AS a_id, ca.display_name AS a_name,"
        " cb.canonical_entity_id AS b_id, cb.display_name AS b_name"
        " FROM (SELECT person_a_entity_id AS a, person_b_entity_id AS b,"
        "       count(DISTINCT company_entity_id) AS n FROM registry_ownership.person_relation_context"
        "       GROUP BY 1, 2) s"
        " JOIN canonical_entity ca ON ca.source_entity_id = s.a"
        " JOIN canonical_entity cb ON cb.source_entity_id = s.b"
        f" ORDER BY {order} LIMIT %s OFFSET %s",
        (PAGE_SIZE, (page - 1) * PAGE_SIZE)).fetchall()
    return render_template(
        "person_relations.html", rows=rows, total=total, page=page, pages=pages,
        sort=("-" if desc else "") + key)


@entities_bp.get("/persons/<int:canonical_entity_id>")
def person(canonical_entity_id):
    c = conn()
    ent = entity(c, canonical_entity_id)
    if ent is None or ent["entity_kind"] != "PERSON":
        abort(404)
    sid = ent["source_entity_id"]
    cycle = cycle_info(c, sid)
    prov = provenance(c, canonical_entity_id)

    incoming = owners_of(c, canonical_entity_id)
    outgoing = holdings_of(c, canonical_entity_id)
    roles = roles_held_by_person(c, canonical_entity_id)
    outgoing_vm = {"direction": OUTGOING, "rows": outgoing,
                   "sum": ownership_sum(outgoing),
                   **indirect_view(c, sid, OUTGOING)}
    return render_template(
        "person.html",
        entity=ent,
        provenance=prov,
        fetch_state=fetch_state(prov),
        control=control_registrations_of_actor(c, canonical_entity_id, sid),
        roles=roles,
        person_overview=_person_overview(c, canonical_entity_id, roles,
                                         outgoing_vm),
        voting_rights=voting_rights_held_by_person(c, canonical_entity_id),
        person_relations=person_relations_of(c, sid),
        person_basis_labels=PERSON_RELATION_BASIS_LABELS,
        cycle=cycle,
        pep=person_pep_info(c, canonical_entity_id),
        structure_status=entity_structure_status(c, canonical_entity_id),
        registration_patterns=registration_patterns(c, canonical_entity_id),
        incoming={"direction": INCOMING, "rows": incoming,
                  "sum": ownership_sum(incoming),
                  "indirect": hydrate_paths(c, indirect_owners_of(c, sid))},
        outgoing=outgoing_vm,
        timeline=entity_timeline(c, canonical_entity_id),
    )


@entities_bp.get("/companies/<int:canonical_entity_id>")
def company(canonical_entity_id):
    c = conn()
    ent = entity(c, canonical_entity_id)
    if ent is None:
        abort(404)
    if ent["entity_kind"] == "PERSON":
        # Personportalen (§17) er ikke bygget i denne fase. A person must not be
        # rendered through company-page framing.
        abort(404)
    if ent["entity_kind"] != "COMPANY":
        # §3.4: ORGANIZATION/PUBLIC_AUTHORITY/UNKNOWN_ENTITY have no full portal —
        # they live on the minimal /entities/:id fallback. /companies/:id is a
        # COMPANY surface; a non-company must not render §16's sections.
        return redirect(entity_url(ent["entity_kind"], canonical_entity_id))
    sid = ent["source_entity_id"]
    cycle = cycle_info(c, sid)
    prov = provenance(c, canonical_entity_id)

    incoming = owners_of(c, canonical_entity_id)
    outgoing = holdings_of(c, canonical_entity_id)
    roles = roles_at_company(c, canonical_entity_id)
    incoming_vm = {"direction": INCOMING, "rows": incoming,
                   "sum": ownership_sum(incoming),
                   **indirect_view(c, sid, INCOMING)}
    outgoing_vm = {"direction": OUTGOING, "rows": outgoing,
                   "sum": ownership_sum(outgoing),
                   **indirect_view(c, sid, OUTGOING)}
    # Product UX consolidation (§9-§11): overview summaries. Governance reads
    # ONLY the Felag authoritative current snapshot (the same §16.3 rows);
    # recent history reuses the Skráseting history view model so wording and
    # occurrence handling can never drift from the primary history page.
    return render_template(
        "company.html",
        entity=ent,
        provenance=prov,
        fetch_state=fetch_state(prov),
        control=control_registration(c, canonical_entity_id, sid),
        roles=roles,
        governance=governance_summary(canonical_entity_id, roles),
        ownership_summary=_company_ownership_summary(incoming_vm, outgoing_vm),
        recent_history=skraseting_views.company_recent_events(
            c, canonical_entity_id, limit=5),
        voting_rights=voting_rights_at_company(c, canonical_entity_id),
        relations=company_relations_of(c, sid),
        basis_labels=RELATION_BASIS_LABELS,
        cycle=cycle,
        structure_status=entity_structure_status(c, canonical_entity_id),
        registration_patterns=registration_patterns(c, canonical_entity_id),
        group_info=ultimate_parent_of(c, canonical_entity_id),
        timeline=entity_timeline(c, canonical_entity_id),
        # A cycle member still has legitimate indirect ownership OUTSIDE its
        # cycle, and it is shown. Only the cyclic relationship itself gets no
        # percentage — that exclusion already happened in the data layer, where
        # an edge is dropped only when both endpoints sit in the SAME cycle
        # component (heimdal_collect.compute_indirect_ownership, bug fixed
        # 2026-08-02 after two audit passes). Blanking the whole block here
        # would reintroduce that exact bug in the UI.
        incoming=incoming_vm,
        outgoing=outgoing_vm,
    )


def _company_registration_number(c, canonical_entity_id):
    """Canonical company -> registry registration number (identity bridge,
    same join as the Skráseting view layer). Returns the RAW inventory value
    ('S5534'); caller normalizes for the projection."""
    row = c.execute(
        "SELECT ci.registration_number FROM company_inventory ci "
        "JOIN canonical_entity ce ON ce.source_entity_id = ci.source_entity_id "
        "WHERE ce.canonical_entity_id = %s "
        "ORDER BY ci.source_system, ci.source_entity_id LIMIT 1",
        (canonical_entity_id,)).fetchone()
    if row is None:
        return None
    return row["registration_number"] if isinstance(row, dict) else row[0]


def _company_ownership_summary(incoming, outgoing):
    """§10 compact ownership summary for the company Overview — counts and
    the top direct owner only; full chains stay in Ejerskab. Pure aggregation
    of the already-fetched §16.1/§16.2 rows (no second query, no new facts)."""
    owners = incoming.get("rows") or []
    top = owners[0] if owners else None
    return {
        "top_owner": ({"person_or_company_id":
                       (top["owner_id"] if isinstance(top, dict) else top[1]),
                       "name": (top["owner_name"] if isinstance(top, dict)
                                else top[2]),
                       "kind": (top["owner_kind"] if isinstance(top, dict)
                                else top[3]),
                       "pct": pct(top["parsed_percentage"])
                       if isinstance(top, dict) else None}
                      if top is not None else None),
        "owner_count": len(owners),
        "owners_total_pct": (pct(incoming["sum"]["total"])
                             if incoming.get("sum") else None),
        "holdings_direct": len(outgoing.get("rows") or []),
        "holdings_indirect": len(outgoing.get("indirect") or []),
    }


def _person_overview(c, canonical_entity_id, roles, outgoing):
    """§39-§41 person Overview summaries. Current roles come ONLY from the
    authoritative Felag snapshot (roles_held_by_person); the history summary
    counts safely-linked Skráseting events via the fail-closed consumer.
    No invented tenure anywhere."""
    current_roles = []
    for group_name, family in (("NEVND", "Bestyrelse"), ("STJORN", "Ledelse")):
        for r in (roles.get(group_name) or []):
            current_roles.append({
                "company_id": r["company_id"],
                "company_name": r["company_name"],
                "role": product_role_label(r["canonical_role_label"]),
                "family": family,
                "source_role": r["canonical_role_label"],
            })
    current_roles.sort(key=lambda r: (r["family"], r["company_name"]))
    # D-03: count and first date come from the whole deduplicated set
    skraseting_events = _skraseting_person_history(c, canonical_entity_id)
    newest = skraseting_events[0] if skraseting_events else {}
    direct_rows = outgoing.get("rows") or []
    return {
        "current_roles": current_roles[:6],
        "current_roles_total": len(current_roles),
        "role_count": len(current_roles),
        "skraseting_event_count": newest.get("total_events") or 0,
        "first_observed": newest.get("first_event_date"),
        "holdings_direct": len(direct_rows),
        "holdings_indirect": len(outgoing.get("indirect") or []),
        "top_holdings": [{"company_id": r["owned_id"],
                          "company_name": r["owned_name"],
                          "pct": pct(r["parsed_percentage"])}
                         for r in direct_rows[:3]],
    }


def _merged_entity_timeline(heimdal_events, skraseting_events):
    """Merge Heimdal snapshot events and Skráseting historical events into one
    entity-scoped timeline. Sources are NEVER collapsed: every entry carries
    an explicit `source` label, and each source keeps its own semantics."""

    def _date_parts(raw):
        """ISO (for <time datetime>) + Danish display (dd.mm.yyyy)."""
        if raw is None:
            return None, None
        iso = str(raw)[:10]
        parts = iso.split("-")
        display = ".".join(reversed(parts)) if len(parts) == 3 else iso
        return iso, display

    merged = []
    for ev in heimdal_events or []:
        iso, display = _date_parts(ev.get("event_timestamp"))
        # Frontend quality (§10): never leak a raw change_category enum into
        # visible product text. Summary is human Danish; when absent, the
        # known category label is used; unknown categories fall back to a
        # neutral phrase (raw token only survives in title attributes).
        summary = _ui_labels.change_summary_label(ev.get("change_summary"))
        detail = summary or _ui_labels.CHANGE_CATEGORY_LABELS.get(
            ev.get("change_category") or "", None) or "Registerændring"
        # T5.1 (F-14): carry the feed's own canonical actor reference (if any)
        # through to the view. v_entity_change_feed.related_entity_id is a
        # canonical_entity_id (verified: 100% join into canonical_entity) —
        # the route enriches kind/visibility; no new matching is performed.
        merged.append({
            "date": iso,
            "date_display": display,
            "source": HEIMDAL_SOURCE_LABEL,
            "source_kind": "heimdal",
            "title": "Registerændring",
            "detail": detail,
            "category": ev.get("change_category"),
            "related_entity_id": ev.get("related_entity_id"),
            "related_entity_name": ev.get("related_entity_name"),
        })
    for ev in skraseting_events or []:
        label = SKRASETING_EVENT_LABELS.get(
            ev.get("event_type") or "", "Registrering ændret")
        action = SKRASETING_ACTION_LABELS.get(ev.get("action") or "",
                                              ev.get("action"))
        if "person_role_id" in ev:
            # person history entry (consumer.get_skraseting_person_history)
            title = label if ev.get("event_type") else "Historisk registrering"
            detail_bits = []
            if action:
                detail_bits.append(action)
            if ev.get("role_title"):
                detail_bits.append(ev["role_title"])
            iso, display = _date_parts(ev.get("event_date"))
            merged.append({
                "date": iso,
                "date_display": display,
                "source": SKRASETING_SOURCE_LABEL,
                "source_kind": "skraseting",
                "title": title,
                "detail": " · ".join(detail_bits) if detail_bits else None,
                "event_type": ev.get("event_type"),
                "action": ev.get("action"),
                "company_canonical_entity_id":
                    ev.get("company_canonical_entity_id"),
                "company_display_name": ev.get("company_display_name"),
                "source_document_id": ev.get("source_document_id"),
                "source_event_id": ev.get("source_event_id"),
            })
        else:
            # company history entry (consumer.get_projected_company_history);
            # event_date carries the shared precedence (temporal, D-13)
            iso, display = _date_parts(ev.get("event_date"))
            merged.append({
                "date": iso,
                "date_display": display,
                "source": SKRASETING_SOURCE_LABEL,
                "source_kind": "skraseting",
                "title": label,
                "detail": action,
                "event_type": ev.get("event_type"),
                "action": ev.get("action"),
                "source_document_id": ev.get("source_document_id"),
                "source_event_id": ev.get("source_event_id"),
            })
    merged.sort(key=lambda e: e["date"] or "", reverse=True)
    return merged


def _enrich_related_entities(c, timeline):
    """T5.1 (F-14): attach entity kind/display ONLY when the feed row's
    canonical actor reference resolves against canonical_entity (secure
    binding that already exists — no name-only/fuzzy matching, no guessing).
    Unresolved rows keep the neutral source-supported description."""
    ids = sorted({e["related_entity_id"] for e in (timeline or [])
                  if e.get("related_entity_id")})
    if not ids:
        return timeline
    kind_map, name_map = {}, {}
    for row in c.execute(
            "SELECT canonical_entity_id, entity_kind, display_name "
            "FROM canonical_entity WHERE canonical_entity_id = ANY(%s)",
            (ids,)).fetchall():
        if isinstance(row, dict):
            kind_map[row["canonical_entity_id"]] = row["entity_kind"]
            name_map[row["canonical_entity_id"]] = row["display_name"]
        else:
            kind_map[row[0]] = row[1]
            name_map[row[0]] = row[2]
    for e in timeline:
        rid = e.get("related_entity_id")
        if rid and rid in kind_map:
            e["related_kind"] = kind_map[rid]
            e["related_display_name"] = (name_map.get(rid)
                                         or e.get("related_entity_name"))
    return timeline


def _skraseting_company_history(registration_number):
    """Skráseting historical events for THIS linked company (fail closed).
    Shown only when the UI may expose projection data (D-02)."""
    if not consumer.ui_exposure_allowed():
        return []
    if not registration_number:
        return []
    regnr = normalize_registration_number(registration_number)
    if not regnr:
        return []
    # T5.1 (F-14): bounded display — the projection feed itself stays the
    # source of truth; the page shows the newest 100 with an explicit note.
    events = consumer.get_projected_company_history(regnr, limit=101)
    if not events:
        return []
    return events


@entities_bp.get("/companies/<int:canonical_entity_id>/history")
def company_history(canonical_entity_id):
    """ENTITY-SCOPED company history (owner defect task section 3/4).

    The company stays the parent context: same header, same entity-scoped
    navigation, Historik active. Route carries the company id - the global
    /aendringer log is a different concept and is never this page.
    """
    c = conn()
    ent = entity(c, canonical_entity_id)
    if ent is None:
        abort(404)
    if ent["entity_kind"] == "PERSON":
        abort(404)
    if ent["entity_kind"] != "COMPANY":
        return redirect(entity_url(ent["entity_kind"], canonical_entity_id))
    prov = provenance(c, canonical_entity_id)

    # (a) existing Heimdal company history - snapshot change feed for THIS
    # entity only (same source as the old tab teaser, now complete)
    heimdal_events = entity_timeline(c, canonical_entity_id) or []
    # (b) Skráseting historical events for the LINKED company only -
    # filtered on this company's registration number via the verified
    # consumer (never the whole registry_projection history)
    regnr = _company_registration_number(c, canonical_entity_id)
    skraseting_events = _skraseting_company_history(regnr)
    # T5.1: Skráseting-grænse — over 100 registreringer vises de nyeste med
    # en eksplicit note (kilderne bevares i Kildeinformation).
    skraseting_truncated = len(skraseting_events) > 100
    if skraseting_truncated:
        skraseting_events = skraseting_events[:100]
    timeline = _enrich_related_entities(
        c, _merged_entity_timeline(heimdal_events, skraseting_events))
    return render_template(
        "company_history.html",
        entity=ent,
        provenance=prov,
        fetch_state=fetch_state(prov),
        timeline=timeline,
        history_all=request.args.get("history") == "all",
        heimdal_count=len(heimdal_events),
        skraseting_count=len(skraseting_events),
        skraseting_truncated=skraseting_truncated,
    )


def _skraseting_person_history(c, canonical_entity_id):
    """Skráseting historical role events for THIS safely linked person.
    Company names resolved on the canonical side; entries without a LINKED
    canonical company keep the company as plain text (never a guess).
    Shown only when the UI may expose projection data (D-02); the person
    overview reuses this helper, so it is gated too."""
    if not consumer.ui_exposure_allowed():
        return []
    # T5.1: bounded display — newest 100 (+1 probe) with explicit note.
    events = consumer.get_skraseting_person_history(canonical_entity_id,
                                                    limit=101)
    if not events:
        return []
    company_ids = sorted({e["company_canonical_entity_id"]
                          for e in events
                          if e.get("company_canonical_entity_id")})
    names = {}
    if company_ids:
        rows = c.execute(
            "SELECT canonical_entity_id, display_name FROM canonical_entity "
            "WHERE canonical_entity_id = ANY(%s)", (company_ids,)).fetchall()
        for row in rows:
            if isinstance(row, dict):
                names[row["canonical_entity_id"]] = row["display_name"]
            else:
                names[row[0]] = row[1]
    for e in events:
        cid = e.get("company_canonical_entity_id")
        e["company_display_name"] = names.get(cid) if cid else None
    return events


@entities_bp.get("/persons/<int:canonical_entity_id>/history")
def person_history(canonical_entity_id):
    """ENTITY-SCOPED person history (owner defect task section 8/9).

    Historical role events only for THIS canonical person; current roles
    keep coming from the authoritative snapshot and are never altered here.
    """
    c = conn()
    ent = entity(c, canonical_entity_id)
    if ent is None or ent["entity_kind"] != "PERSON":
        abort(404)
    prov = provenance(c, canonical_entity_id)
    heimdal_events = entity_timeline(c, canonical_entity_id) or []
    skraseting_events = _skraseting_person_history(c, canonical_entity_id)
    # T5.1: same bounded display contract as company history.
    skraseting_truncated = len(skraseting_events) > 100
    if skraseting_truncated:
        skraseting_events = skraseting_events[:100]
    timeline = _enrich_related_entities(
        c, _merged_entity_timeline(heimdal_events, skraseting_events))
    return render_template(
        "person_history.html",
        entity=ent,
        provenance=prov,
        fetch_state=fetch_state(prov),
        timeline=timeline,
        history_all=request.args.get("history") == "all",
        heimdal_count=len(heimdal_events),
        skraseting_count=len(skraseting_events),
        skraseting_truncated=skraseting_truncated,
    )


@entities_bp.get("/companies/<int:canonical_entity_id>/eigarayvirlit")
def ejarayvirlit(canonical_entity_id):
    """§21a: alle personer bag virksomheden via ejerkæder (kun ejerandele).
    Kun COMPANY-sider; andre typer redirectes ligesom /companies/:id.
    §15: cyklus-medlemmer får forklaring i stedet for procent."""
    c = conn()
    ent = entity(c, canonical_entity_id)
    if ent is None:
        abort(404)
    if ent["entity_kind"] != "COMPANY":
        return redirect(entity_url(ent["entity_kind"], canonical_entity_id))
    prov = provenance(c, canonical_entity_id)
    # F-13: one read of the owners and the indirect paths feeds both the
    # persons list and the tree graph.
    owners = owners_of(c, canonical_entity_id)
    rows = hydrate_paths(c, indirect_owners_of(c, ent["source_entity_id"]))
    tree = persons_behind(owners, rows)
    persons = top_persons(tree)
    graph = ownership_tree_layout(ent, rows, owners)
    return render_template(
        "ejarayvirlit.html",
        entity=ent, provenance=prov, fetch_state=fetch_state(prov),
        cycle=cycle_info(c, ent["source_entity_id"]),
        persons=persons, total_persons=len(tree), tree=graph)


@entities_bp.get("/api/inspect/<int:canonical_entity_id>")
def api_inspect(canonical_entity_id):
    c = conn()
    ent = entity(c, canonical_entity_id)
    if ent is None:
        return jsonify({"error": "not found"}), 404
    kind = ent["entity_kind"]
    data = {
        "id": ent["canonical_entity_id"],
        "name": ent["display_name"],
        "kind": kind,
        "regnr": ent["registration_number"],
        "raw_type": ent["raw_type_token"],
        "inventory_status": ent["inventory_status"],
        "url": entity_url(kind, ent["canonical_entity_id"]),
    }
    if kind == "PERSON":
        direct = c.execute(
            "SELECT owned_id, owned_name, parsed_percentage FROM v_direct_ownership WHERE owner_id = %s "
            "ORDER BY parsed_percentage DESC NULLS LAST LIMIT 5", (canonical_entity_id,)
        ).fetchall()
        roles = c.execute(
            "SELECT company_id, company_name, canonical_role_label FROM v_role_relation WHERE person_id = %s "
            "ORDER BY company_name LIMIT 5", (canonical_entity_id,)
        ).fetchall()
        data["direct_holdings"] = [
            {"id": r["owned_id"], "name": r["owned_name"], "pct": pct(r["parsed_percentage"])}
            for r in direct
        ]
        data["roles"] = [
            {"id": r["company_id"], "name": r["company_name"], "role": r["canonical_role_label"]}
            for r in roles
        ]
    elif kind == "COMPANY":
        owners = c.execute(
            "SELECT owner_id, owner_name, owner_kind, parsed_percentage FROM v_direct_ownership WHERE owned_id = %s "
            "ORDER BY parsed_percentage DESC NULLS LAST LIMIT 5", (canonical_entity_id,)
        ).fetchall()
        holdings = c.execute(
            "SELECT owned_id, owned_name, parsed_percentage FROM v_direct_ownership WHERE owner_id = %s "
            "ORDER BY parsed_percentage DESC NULLS LAST LIMIT 5", (canonical_entity_id,)
        ).fetchall()
        data["owners"] = [
            {"id": r["owner_id"], "name": r["owner_name"], "kind": r["owner_kind"], "pct": pct(r["parsed_percentage"])}
            for r in owners
        ]
        data["holdings"] = [
            {"id": r["owned_id"], "name": r["owned_name"], "pct": pct(r["parsed_percentage"])}
            for r in holdings
        ]
    return jsonify(data)
