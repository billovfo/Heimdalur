"""Shared presentation registrations (P2.2 consolidation).

All template globals/filters and the global context processor live here so
heimdal/app.py stays a pure factory: imports + register calls + blueprints.
Every global is a thin fail-closed wrapper delegating to its owning module;
no domain logic lives in this file. DB reads run in read_savepoint(), so one
failed global cannot poison the request connection for the rest (F-8/D-12).
"""
from __future__ import annotations

from flask import url_for

from heimdal.modules.shared.db import read_savepoint


def register_presentation(a):
    # Frontend quality consolidation (§91): presentation-only label maps.
    # Source values in the DB are never changed — these are read-only maps
    # used at the last rendering step. Exposed to templates as `L`.
    from heimdal.modules.shared import labels as _ui_labels
    a.template_global("L")(_ui_labels)

    @a.template_global()
    def skraseting_allowed() -> bool:
        """Company-page entry visibility (integration AND ui flags)."""
        try:
            from heimdal.pdfhist import consumer
            return consumer.ui_exposure_allowed()
        except Exception:
            return False  # fail closed: no entry on any error

    @a.template_global()
    def skraseting_person_section(canonical_entity_id):
        """Skráseting section view for the EXISTING person page.

        Fail closed in every dimension (task sections 45/46): gate closed,
        no linked evidence, or any read error -> None, and the person page
        renders exactly as before. The section never makes the projection a
        dependency for the rest of the page.
        """
        try:
            from heimdal.pdfhist import consumer
            if not consumer.ui_exposure_allowed():
                return None
            from heimdal.modules.skraseting.views import (
                skraseting_person_section as build_section)
            with read_savepoint() as c:
                return build_section(c, canonical_entity_id)
        except Exception:
            return None  # fail closed: no Skráseting section on any error

    @a.template_global()
    def analytics_enabled() -> bool:
        """Phase 4 gate: analytics sections render only when explicitly
        enabled (REGISTRY_ANALYTICS_ENABLED). Fail closed on any error."""
        try:
            from heimdal.modules.analytics.flags import analytics_enabled as _f
            return _f()
        except Exception:
            return False

    @a.template_global()
    def analytics_person_section(canonical_entity_id):
        """Phase 4 view model for the analytics section on the EXISTING
        person page (B12). Read-only against the single ACTIVE analytics
        build; no ACTIVE build or any read failure -> None, and the person
        page renders exactly as before (B5/B51)."""
        try:
            from heimdal.modules.analytics.flags import analytics_enabled as _f
            if not _f():
                return None
            from heimdal.modules.analytics import views as analytics_views
            with read_savepoint() as c:
                return analytics_views.person_analytics_section(
                    c, canonical_entity_id)
        except Exception:
            return None  # fail closed: no analytics section on any error

    @a.template_global()
    def analytics_company_section(canonical_entity_id):
        """Phase 4 view model for the analytics supplement on the EXISTING
        company page Personer panel (B19-B22). Fail closed like the person
        section."""
        try:
            from heimdal.modules.analytics.flags import analytics_enabled as _f
            if not _f():
                return None
            from heimdal.modules.analytics import views as analytics_views
            with read_savepoint() as c:
                return analytics_views.company_analytics_section(
                    c, canonical_entity_id)
        except Exception:
            return None

    @a.template_filter("product_role")
    def product_role_filter(value):
        """Plan v1 T3.1: Danish product label for a source role token.
        Binding vocabulary from shared/labels.py; unknown → neutral fallback."""
        try:
            from heimdal.modules.shared.labels import product_role_label
            return product_role_label(value)
        except Exception:
            return value  # fail safe: never break a render on labels

    @a.template_filter("product_token")
    def product_token_filter(value):
        """Plan v1 T3.1: Danish label for a technical/English token
        (FETCHED / DIRECT OWNERSHIP / OTHER SOURCE RELATION /
        REGISTERED CONTROL ACTOR / ULTIMATE PARENT). Unknown → unchanged."""
        try:
            from heimdal.modules.shared.labels import product_token_label
            return product_token_label(value)
        except Exception:
            return value

    @a.template_filter("inventory_status")
    def inventory_status_filter(value):
        """Plan v1 T3.2: Danish human label for the technical ingest/
        inventory status (FETCHED / DISCOVERED / RETRYABLE_ERROR)."""
        try:
            from heimdal.modules.shared.labels import inventory_status_label
            return inventory_status_label(value)
        except Exception:
            return value

    @a.template_global()
    def network_preview_summary(canonical_entity_id, entity_kind):
        """Product UX consolidation (§37): compact network summary numbers
        for the detail-page preview (same ego-graph read as the full /network
        page; fail closed -> None, preview renders without counts)."""
        try:
            from heimdal.modules.analytics import views as analytics_views
            with read_savepoint() as c:
                return analytics_views.network_preview_summary(
                    c, canonical_entity_id, entity_kind)
        except Exception:
            return None

    @a.template_global()
    def skraseting_registry_facts(canonical_entity_id):
        """Owner acceptance UX correction (UX-04): compact, human-graded
        CURRENT registry facts for the company Overview — the same gated,
        read-only Skráseting view model as the dedicated route, so the
        separate route stays a source/debug surface and the overview carries
        the human current state. None (fail closed) when the exposure gate
        is closed, no projection exists, or any read fails."""
        try:
            from heimdal.modules.skraseting.views import skraseting_view
            with read_savepoint() as c:
                view = skraseting_view(c, canonical_entity_id)
            if not (view.get("allowed") and view.get("has_projection")) \
                    or view.get("error"):
                return None
            fields = {f["name"]: f for f in view.get("fields", [])}
            return {
                "snapshot_date": view.get("snapshot_date"),
                "snapshot_date_dk": view.get("snapshot_date_dk"),
                "registered_name": view.get("registered_name"),
                "source_label": view.get("source_label"),
                "aliases": (fields.get("alias_names") or {}).get("human"),
                "address": (fields.get("company_address") or {}).get("human"),
                "capital": (fields.get("capital") or {}).get("human"),
                "purpose": (fields.get("purpose") or {}).get("value"),
                "signing_rules": (fields.get("signing_rules") or {}).get("value"),
                "accounts_dates": (fields.get("accounts_dates") or {}).get("human"),
                "route_url": url_for("skraseting.skraseting",
                                     canonical_entity_id=canonical_entity_id),
            }
        except Exception:
            return None  # fail closed: overview card simply absent

    @a.template_global()
    def analytics_relation_supplement(canonical_entity_id, peer_ids):
        """B29: pair analytics as an explaining supplement next to the
        existing canonical relation facts. Returns {peer_id: summary} or
        None (gate closed / no ACTIVE build / read failure)."""
        try:
            from heimdal.modules.analytics.flags import analytics_enabled as _f
            if not _f():
                return None
            from heimdal.modules.analytics import views as analytics_views
            with read_savepoint() as c:
                return analytics_views.relation_analytics_supplement(
                    c, canonical_entity_id, peer_ids)
        except Exception:
            return None

    @a.template_global()
    def analytics_network_summary(canonical_entity_id):
        """B30 minimal network integration: read-only top-collaborator
        summary with click-through. None = gate closed / no data / failure."""
        try:
            from heimdal.modules.analytics.flags import analytics_enabled as _f
            if not _f():
                return None
            from heimdal.modules.analytics import views as analytics_views
            with read_savepoint() as c:
                return analytics_views.network_analytics_summary(
                    c, canonical_entity_id)
        except Exception:
            return None

    # --- context processor: watchlist count + source disclaimer ---
    @a.context_processor
    def inject_global_template_context():
        try:
            from heimdal.modules.watchlist.service import get_watchlist
            with read_savepoint() as c:
                w_count = len(get_watchlist(c))
        except Exception:
            w_count = 0
        return {
            "watchlist_count": w_count,
            "source_disclaimer": "Skráseting Føroya oplyser, at deres IT-system er under opbygning. Der tages derfor forbehold for fejl eller mangler i kildedata."
        }

