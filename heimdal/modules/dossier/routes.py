# heimdal/modules/dossier/routes.py
"""Dossier routes: /companies/<id>/dossier, /companies/<id>/kyc,
/persons/<id>/dossier."""
import time
from flask import Blueprint, abort, redirect, render_template

from heimdal.modules.shared.db import conn
from heimdal.modules.shared.entity import entity, provenance, entity_url, fetch_state
from heimdal.modules.entities.service import (
    owners_of, holdings_of, ownership_sum,
    person_owners_tree, owner_chain_rows,
    control_registration, control_registrations_of_actor,
    roles_at_company, roles_held_by_person,
)
from heimdal.modules.compliance.service import entity_structure_status
from heimdal.modules.groups.service import ultimate_parent_of

dossier_bp = Blueprint("dossier", __name__)


@dossier_bp.get("/companies/<int:canonical_entity_id>/dossier")
def company_dossier(canonical_entity_id):
    """Full Due Diligence Dossier report view for a company."""
    c = conn()
    ent = entity(c, canonical_entity_id)
    if ent is None:
        abort(404)
    if ent["entity_kind"] != "COMPANY":
        return redirect(entity_url(ent["entity_kind"], canonical_entity_id))
    sid = ent["source_entity_id"]
    prov = provenance(c, canonical_entity_id)
    incoming = owners_of(c, canonical_entity_id)
    tree = owner_chain_rows(person_owners_tree(c, canonical_entity_id, sid))

    return render_template(
        "dossier_company.html",
        entity=ent,
        provenance=prov,
        fetch_state=fetch_state(prov),
        generated_at=time.strftime("%d-%m-%Y %H:%M"),
        control=control_registration(c, canonical_entity_id, sid),
        roles=roles_at_company(c, canonical_entity_id),
        structure_status=entity_structure_status(c, canonical_entity_id),
        group_info=ultimate_parent_of(c, canonical_entity_id),
        ejarayvirlit=tree,
        incoming={"rows": incoming, "sum": ownership_sum(incoming)}
    )


@dossier_bp.get("/companies/<int:canonical_entity_id>/kyc")
def company_kyc_dossier(canonical_entity_id):
    """Full Bank-KYC Due Diligence Report view for a company."""
    c = conn()
    ent = entity(c, canonical_entity_id)
    if ent is None:
        abort(404)
    if ent["entity_kind"] != "COMPANY":
        return redirect(entity_url(ent["entity_kind"], canonical_entity_id))
    sid = ent["source_entity_id"]
    prov = provenance(c, canonical_entity_id)
    tree = owner_chain_rows(person_owners_tree(c, canonical_entity_id, sid))

    return render_template(
        "dossier_kyc.html",
        entity=ent,
        provenance=prov,
        fetch_state=fetch_state(prov),
        generated_at=time.strftime("%d-%m-%Y %H:%M"),
        control=control_registration(c, canonical_entity_id, sid),
        roles=roles_at_company(c, canonical_entity_id),
        structure_status=entity_structure_status(c, canonical_entity_id),
        group_info=ultimate_parent_of(c, canonical_entity_id),
        tree=tree
    )


@dossier_bp.get("/persons/<int:canonical_entity_id>/dossier")
def person_dossier(canonical_entity_id):
    """Full Due Diligence Dossier report view for a physical person."""
    c = conn()
    ent = entity(c, canonical_entity_id)
    if ent is None or ent["entity_kind"] != "PERSON":
        abort(404)
    sid = ent["source_entity_id"]
    prov = provenance(c, canonical_entity_id)
    outgoing = holdings_of(c, canonical_entity_id)

    return render_template(
        "dossier_person.html",
        entity=ent,
        provenance=prov,
        fetch_state=fetch_state(prov),
        generated_at=time.strftime("%d-%m-%Y %H:%M"),
        control=control_registrations_of_actor(c, canonical_entity_id, sid),
        roles=roles_held_by_person(c, canonical_entity_id),
        outgoing={"rows": outgoing}
    )
