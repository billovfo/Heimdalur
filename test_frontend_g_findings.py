"""Offline regressions for audit lane G (W5d): G-02/G-05 CSP, G-03 print, G-04
contrast tokens, G-06 axe items, H-7 geo tests. Static reads of templates/,
static/ and tools/ through tools/audit/check_g.py, plus the CSP header the app
sends on a static file. No DB, no browser: the browser proof is
tools/verify_print_pdf.py and an axe run."""
import importlib.util
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).parent
_spec = importlib.util.spec_from_file_location("check_g", ROOT / "tools/audit/check_g.py")
check_g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_g)


@pytest.fixture(autouse=True)
def _fresh_fail_list():
    check_g.FAIL.clear()


@pytest.mark.parametrize("name", ["check_g02", "check_g03", "check_g04", "check_g06", "check_h7"])
def test_check_g(name):
    getattr(check_g, name)()
    assert check_g.FAIL == []


def test_contrast_ratio_matches_wcag():
    assert round(check_g.contrast("#FFFFFF", "#000000"), 1) == 21.0
    assert check_g.contrast("#6B8296", "#121E2B") < 4.5  # the old dark --text-3 (G-04)
    assert check_g.contrast("#FFFFFF", "#0EA5E9") < 4.5  # the old .btn-primary


def test_csp_scripts_self_only():
    from heimdal.app import app

    app.config["TESTING"] = True
    csp = app.test_client().get("/static/app.js").headers["Content-Security-Policy"]
    assert re.search(r"script-src([^;]*);", csp).group(1).split() == ["'self'"]
    assert "style-src 'self' 'unsafe-inline'" in csp  # style="" attributes stay (G-05)


def test_json_data_blocks_go_through_tojson():
    # data reaches JS in <script type="application/json">; |tojson escapes < > &
    blocks = 0
    for f in (ROOT / "templates").glob("*.html"):
        for m in re.finditer(r'<script type="application/json"[^>]*>(.*?)</script>', f.read_text(), re.S):
            assert m.group(1).strip().endswith("|tojson }}"), f.name
            blocks += 1
    assert blocks >= 2  # geo map + pathfinder
