# heimdal/app.py
"""Flask app factory: instantiates app, registers hooks, filters, globals."""
import os

from flask import Flask, Response, g, has_request_context, render_template, url_for

from heimdal.config import _RATE_LIMIT, _RATE_WINDOW
from heimdal.modules.shared.db import _close
from heimdal.pdfhist import flags as pdfhist_flags
from heimdal.modules.shared.web import _local_only, _rate_limit, _security_headers
from heimdal.modules.shared.inputs import reject_nul
from heimdal.modules.shared.templates import pct, dk, heatband, fail
from heimdal.modules.shared.entity import entity_url

# Templates and static files live at the project root (next to the original
# heimdal_portal.py), not inside the heimdal/ package.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def create_app():
    """Application factory: create Flask app, register all lifecycle hooks,
    template filters, and template globals."""
    a = Flask(__name__, root_path=_PROJECT_ROOT)
    # Any other Host header (DNS rebinding) gets 400 (F-7).
    a.config["TRUSTED_HOSTS"] = ["127.0.0.1", "localhost"]
    a.config["RATE_LIMIT"], a.config["RATE_WINDOW"] = _RATE_LIMIT, _RATE_WINDOW

    # F-13: PDF-history flags resolve once per request, not once per call.
    pdfhist_flags.set_memo_hook(
        lambda: g.setdefault("flag_memo", {}) if has_request_context() else None)

    # --- lifecycle hooks ---
    a.before_request(_local_only)  # first: before rate limit and any DB access
    a.before_request(_rate_limit)
    a.before_request(reject_nul)  # F-6: a NUL in any arg is a 400, before any DB access
    a.teardown_appcontext(_close)
    a.after_request(_security_headers)

    # --- template filters ---
    a.template_filter("pct")(pct)
    a.template_filter("dk")(dk)
    a.template_filter("heatband")(heatband)

    # --- template globals ---
    a.template_global()(fail)
    a.template_global()(entity_url)

    from heimdal.modules.shared.presentation import (
        register_presentation,
    )
    register_presentation(a)

    from heimdal.modules.entities.routes import entities_bp
    a.register_blueprint(entities_bp)

    # Skráseting / PDF-historik v1: separate feature-gated surface (read-only).
    # ui_exposure_allowed = integration AND ui; flag off -> routes 404 and the
    # company page carries no entry.
    from heimdal.modules.skraseting.routes import skraseting_bp
    a.register_blueprint(skraseting_bp)

    from heimdal.modules.compliance.routes import compliance_bp
    a.register_blueprint(compliance_bp)

    from heimdal.modules.graph.routes import graph_bp
    a.register_blueprint(graph_bp)

    from heimdal.modules.dossier.routes import dossier_bp
    a.register_blueprint(dossier_bp)

    from heimdal.modules.groups.routes import groups_bp
    a.register_blueprint(groups_bp)

    from heimdal.modules.snapshots.routes import snapshots_bp
    a.register_blueprint(snapshots_bp)

    from heimdal.modules.watchlist.routes import watchlist_bp
    a.register_blueprint(watchlist_bp)

    from heimdal.modules.dashboard.routes import dashboard_bp
    a.register_blueprint(dashboard_bp)

    from heimdal.modules.search.routes import search_bp
    a.register_blueprint(search_bp)

    from heimdal.modules.export.routes import export_bp
    a.register_blueprint(export_bp)

    from heimdal.modules.geo.routes import geo_bp
    a.register_blueprint(geo_bp)

    from heimdal.modules.screening.routes import screening_bp
    a.register_blueprint(screening_bp)

    # Person human-review v1: fail-closed unless HEIMDAL_REVIEW_UI=1 (dev/local).
    from heimdal.modules.review.routes import review_bp
    a.register_blueprint(review_bp)

    @a.get("/favicon.ico")
    def favicon():
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#3b82f6"/><text x="16" y="22" font-size="18" font-family="sans-serif" font-weight="bold" fill="#ffffff" text-anchor="middle">H</text></svg>'
        return Response(svg, mimetype="image/svg+xml")

    @a.errorhandler(404)
    def _not_found(_e):
        return render_template("notfound.html"), 404

    return a


app = create_app()
