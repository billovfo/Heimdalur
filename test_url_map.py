"""F-11 (W6d), offline: the package root is no longer a re-export shim, and
flattening compliance/graph left every blueprint and URL as it was."""
import subprocess
import sys

# Frozen before the W6d refactor: (rule, endpoint, methods) for every route.
URL_MAP = [
    ('/', 'dashboard.dashboard', 'GET HEAD OPTIONS'),
    ('/aendringer', 'snapshots.changelog', 'GET HEAD OPTIONS'),
    ('/api/analytics/graph/<int:canonical_entity_id>', 'graph.api_analytics_graph', 'GET HEAD OPTIONS'),
    ('/api/bulk/screen', 'screening.api_bulk_screen', 'OPTIONS POST'),
    ('/api/export/company/<int:canonical_entity_id>/ejarayvirlit.csv', 'export.export_ejarayvirlit_csv', 'GET HEAD OPTIONS'),
    ('/api/export/company/<int:canonical_entity_id>/owners.csv', 'export.export_company_owners_csv', 'GET HEAD OPTIONS'),
    ('/api/export/compliance/matrix.csv', 'compliance.export_compliance_matrix_csv', 'GET HEAD OPTIONS'),
    ('/api/export/compliance/matrix.json', 'compliance.export_compliance_matrix_json', 'GET HEAD OPTIONS'),
    ('/api/geo/clusters', 'geo.api_geo_clusters', 'GET HEAD OPTIONS'),
    ('/api/geo/companies', 'geo.api_geo_companies', 'GET HEAD OPTIONS'),
    ('/api/groups/<int:canonical_entity_id>/flow', 'groups.api_group_flow', 'GET HEAD OPTIONS'),
    ('/api/inspect/<int:canonical_entity_id>', 'entities.api_inspect', 'GET HEAD OPTIONS'),
    ('/api/network/<int:canonical_entity_id>', 'graph.api_network', 'GET HEAD OPTIONS'),
    ('/api/pathfinder', 'graph.api_pathfinder', 'GET HEAD OPTIONS'),
    ('/api/search', 'search.api_search', 'GET HEAD OPTIONS'),
    ('/api/watchlist', 'watchlist.api_watchlist', 'GET HEAD OPTIONS'),
    ('/api/watchlist/toggle', 'watchlist.api_watchlist_toggle', 'OPTIONS POST'),
    ('/bestand', 'entities.bestand', 'GET HEAD OPTIONS'),
    ('/bulk-screening', 'screening.bulk_screening_page', 'GET HEAD OPTIONS'),
    ('/companies', 'entities.companies', 'GET HEAD OPTIONS'),
    ('/companies/<int:canonical_entity_id>', 'entities.company', 'GET HEAD OPTIONS'),
    ('/companies/<int:canonical_entity_id>/dossier', 'dossier.company_dossier', 'GET HEAD OPTIONS'),
    ('/companies/<int:canonical_entity_id>/eigarayvirlit', 'entities.ejarayvirlit', 'GET HEAD OPTIONS'),
    ('/companies/<int:canonical_entity_id>/history', 'entities.company_history', 'GET HEAD OPTIONS'),
    ('/companies/<int:canonical_entity_id>/kyc', 'dossier.company_kyc_dossier', 'GET HEAD OPTIONS'),
    ('/companies/<int:canonical_entity_id>/network', 'graph.company_network', 'GET HEAD OPTIONS'),
    ('/companies/<int:canonical_entity_id>/skraseting', 'skraseting.skraseting', 'GET HEAD OPTIONS'),
    ('/compliance', 'compliance.compliance', 'GET HEAD OPTIONS'),
    ('/entities/<int:canonical_entity_id>', 'entities.entity_page', 'GET HEAD OPTIONS'),
    ('/favicon.ico', 'favicon', 'GET HEAD OPTIONS'),
    ('/find-forbindelse', 'graph.pathfinder', 'GET HEAD OPTIONS'),
    ('/geo-kort', 'geo.geo_map_page', 'GET HEAD OPTIONS'),
    ('/groups', 'groups.groups_overview', 'GET HEAD OPTIONS'),
    ('/groups/<int:canonical_entity_id>', 'groups.group_detail', 'GET HEAD OPTIONS'),
    ('/kildeinformation/<int:canonical_entity_id>', 'entities.sourceinfo', 'GET HEAD OPTIONS'),
    ('/overvaagning', 'watchlist.watchlist_page', 'GET HEAD OPTIONS'),
    ('/persons', 'entities.persons', 'GET HEAD OPTIONS'),
    ('/persons/<int:canonical_entity_id>', 'entities.person', 'GET HEAD OPTIONS'),
    ('/persons/<int:canonical_entity_id>/dossier', 'dossier.person_dossier', 'GET HEAD OPTIONS'),
    ('/persons/<int:canonical_entity_id>/history', 'entities.person_history', 'GET HEAD OPTIONS'),
    ('/persons/<int:canonical_entity_id>/network', 'graph.person_network', 'GET HEAD OPTIONS'),
    ('/persons/relationer', 'entities.person_relation_pairs', 'GET HEAD OPTIONS'),
    ('/persons/review', 'review.queue', 'GET HEAD OPTIONS'),
    ('/persons/review/<int:person_role_id>', 'review.case', 'GET HEAD OPTIONS'),
    ('/persons/review/<int:person_role_id>/decision', 'review.decide', 'OPTIONS POST'),
    ('/persons/review/batch/<batch_id>/qa', 'review.batch_qa', 'GET HEAD OPTIONS'),
    ('/persons/review/batch/apply', 'review.batch_apply', 'OPTIONS POST'),
    ('/persons/review/batch/dry-run', 'review.batch_dry_run', 'OPTIONS POST'),
    ('/persons/review/batch/freeze', 'review.batch_freeze', 'OPTIONS POST'),
    ('/persons/review/batch/qa/mark', 'review.batch_qa_mark', 'OPTIONS POST'),
    ('/persons/review/decided', 'review.decided', 'GET HEAD OPTIONS'),
    ('/persons/review/export', 'review.export_csv', 'GET HEAD OPTIONS'),
    ('/static/<path:filename>', 'static', 'GET HEAD OPTIONS'),
]


def test_url_map_unchanged():
    from heimdal.app import app
    got = sorted((r.rule, r.endpoint, " ".join(sorted(r.methods)))
                 for r in app.url_map.iter_rules())
    assert got == URL_MAP


def test_package_root_builds_no_app():
    # A CLI importing heimdal.pdfhist.* must not load Flask and every portal module.
    code = ("import sys, heimdal, heimdal.pdfhist.flags; "
            "print(sorted(k for k in vars(heimdal) if not k.startswith('__')), "
            "'flask' in sys.modules, 'heimdal.app' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True).stdout.split()
    assert out == ["['pdfhist']", "False", "False"]


def test_rate_limit_reads_app_config(monkeypatch):
    from heimdal.app import create_app
    from heimdal.modules.shared import web
    a = create_app()
    a.config.update(RATE_LIMIT=2, RATE_WINDOW=7)
    monkeypatch.setattr(web, "_requests", {})
    codes = [a.test_client().get("/favicon.ico") for _ in range(3)]
    assert [r.status_code for r in codes] == [200, 200, 429]
    assert codes[-1].headers["Retry-After"] == "7"
