"""Dashboard route: / (root)."""
from flask import Blueprint, render_template

from heimdal.modules.shared.db import conn
from heimdal.modules.dashboard.service import (
    dashboard_charts, dashboard_macro_stats, top_lists,
)

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.get("/")
def dashboard():
    c = conn()
    return render_template("home.html", tops=top_lists(c),
                           charts=dashboard_charts(c),
                           macro=dashboard_macro_stats(c))
