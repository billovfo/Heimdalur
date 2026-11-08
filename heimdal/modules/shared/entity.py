# heimdal/modules/shared/entity.py
"""Entity lookup helpers shared by all modules."""
from flask import g, has_request_context

from heimdal.config import _DIRECT_COLS, FETCHED_OK, FETCH_FAILED, NEVER_FETCHED
from heimdal.modules.shared.db import either_side


def entity(conn, canonical_entity_id):
    """Explicit column list: v_entity also exposes raw_inventory_label, which
    must never reach a template (a PERSON row's raw label carries an address).
    F-13: memoised per request (flask.g): one company page asked for the same
    row four times. Callers treat the row as read-only."""
    memo = g.setdefault("entity_memo", {}) if has_request_context() else {}
    if canonical_entity_id not in memo:
        memo[canonical_entity_id] = conn.execute(
            "SELECT canonical_entity_id, source_entity_id, entity_kind, display_name,"
            " registration_number, inventory_status, typing_status, raw_type_token"
            " FROM v_entity WHERE canonical_entity_id = %s",
            (canonical_entity_id,)).fetchone()
    return memo[canonical_entity_id]


def provenance(conn, canonical_entity_id):
    """None means this entity was never itself the subject of a fetch — true for
    the ~192 foreign/non-inventoried companies. Not an error (§24)."""
    return conn.execute(
        "SELECT last_fetched_at, snapshot_sha256, last_http_status, last_error_category"
        " FROM v_provenance WHERE canonical_entity_id = %s",
        (canonical_entity_id,)).fetchone()


def fetch_state(prov):
    """Classify v_provenance. None → NEVER_FETCHED; last fetch OK → FETCHED_OK;
    any other terminal error → FETCH_FAILED."""
    if prov is None:
        return NEVER_FETCHED
    if prov["last_error_category"] == "OK":
        return FETCHED_OK
    return FETCH_FAILED


def entity_url(kind, entity_id):
    """§3.3/§3.4 clickability: PERSON → /persons/:id, COMPANY → /companies/:id,
    every other kind → the minimal /entities/:id fallback."""
    if kind == "PERSON":
        return f"/persons/{entity_id}"
    if kind == "COMPANY":
        return f"/companies/{entity_id}"
    return f"/entities/{entity_id}"


def entity_relations(conn, canonical_entity_id):
    """Every relation this entity appears in, as a flat clickable list.

    Three views, one shape: the counterparty (canonical id/name/kind) plus a
    relation label. Direction is stated as text, never as an owner/owned
    column pair — §16.2's in/out separation belongs to the ownership portal
    and is deliberately not reproduced here (a fallback is not a portal).
    """
    rows = []
    one = {"id": canonical_entity_id}
    for r in conn.execute(
            either_side(_DIRECT_COLS, "v_direct_ownership", "owner_id", "owned_id"), one).fetchall():
        if r["owner_id"] == canonical_entity_id:
            rows.append({"counterparty_id": r["owned_id"], "counterparty_name": r["owned_name"],
                         "counterparty_kind": r["owned_kind"],
                         "label": "ejer af", "raw_label": r["raw_percentage_label"]})
        else:
            rows.append({"counterparty_id": r["owner_id"], "counterparty_name": r["owner_name"],
                         "counterparty_kind": r["owner_kind"],
                         "label": "ejes af", "raw_label": r["raw_percentage_label"]})
    for r in conn.execute(
            either_side("actor_id, actor_name, actor_kind, registered_id, registered_name,"
                        " raw_source_relation_label", "v_control_registration",
                        "actor_id", "registered_id"), one).fetchall():
        if r["actor_id"] == canonical_entity_id:
            rows.append({"counterparty_id": r["registered_id"], "counterparty_name": r["registered_name"],
                         "counterparty_kind": "COMPANY",
                         "label": "registreret kontrolaktør for",
                         "raw_label": r["raw_source_relation_label"]})
        else:
            rows.append({"counterparty_id": r["actor_id"], "counterparty_name": r["actor_name"],
                         "counterparty_kind": r["actor_kind"],
                         "label": "har registreret kontrolaktør",
                         "raw_label": r["raw_source_relation_label"]})
    for r in conn.execute(
            either_side("person_id, person_name, company_id, company_name, canonical_role_label",
                        "v_role_relation", "person_id", "company_id"), one).fetchall():
        if r["person_id"] == canonical_entity_id:
            rows.append({"counterparty_id": r["company_id"], "counterparty_name": r["company_name"],
                         "counterparty_kind": "COMPANY",
                         "label": "rolle i", "raw_label": r["canonical_role_label"]})
        else:
            rows.append({"counterparty_id": r["person_id"], "counterparty_name": r["person_name"],
                         "counterparty_kind": "PERSON",
                         "label": "har rolle", "raw_label": r["canonical_role_label"]})
    rows.sort(key=lambda r: (r["counterparty_name"] or "", r["label"]))
    return rows
