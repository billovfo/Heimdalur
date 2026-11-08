"""Skráseting / PDF-historik UI v1 - routes.

A separate, isolated surface on the company page:

    /companies/<id>/skraseting

Hard gate (section 4/42/43): when `ui_exposure_allowed` is false the route
404s and the company page carries no Skráseting entry - no DOM-hidden data.
Read-only: no write actions exist anywhere on this surface.
"""
from __future__ import annotations

import psycopg

from flask import Blueprint, abort, render_template

from ...pdfhist import consumer
from ..shared.db import conn as qa_conn
from . import views

skraseting_bp = Blueprint("skraseting", __name__)


@skraseting_bp.get("/companies/<int:canonical_entity_id>/skraseting")
def skraseting(canonical_entity_id: int):
    c = qa_conn()
    ent = c.execute(
        "SELECT canonical_entity_id, source_entity_id, entity_kind, "
        "display_name FROM canonical_entity WHERE canonical_entity_id = %s",
        (canonical_entity_id,)).fetchone()
    if ent is None:
        abort(404)
    # hard gate: UI exposure requires integration_enabled AND ui_enabled,
    # resolved against the projection database (the flags live there).
    # Fail closed with 404 - no hidden DOM data, no redirect leaking state.
    if not consumer.ui_exposure_allowed():
        abort(404)
    try:
        view = views.skraseting_view(c, canonical_entity_id,
                                     integration=None, ui=None)
    except Exception:  # noqa: BLE001 - section 29: the company page must
        # never break because of this surface; degrade to a local error state
        view = views._base_view()
        view["allowed"] = True
        view["error"] = True
    return render_template("skraseting.html", view=view,
                           canonical_entity_id=canonical_entity_id,
                           entity_name=view.get("canonical_name"))