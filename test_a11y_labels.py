"""Accessibility: form fields must have an associated label or aria-label.

Catches the post-review findings 2026-08-18:
- cmd-palette-input on every page (base.html)
- sim-company-id, sim-pct-slider, sim-owner-id on /simulator
- file-upload, bulk-input on /bulk-screening
"""
import re
import pytest
from heimdal.app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


PAGES_WITH_PALETTE = [
    "/",
    "/bestand",
    "/companies",
    "/persons",
    "/compliance",
    "/overvaagning",
    "/bulk-screening",
    "/geo-kort",
    "/find-forbindelse",
    "/groups",
]


def _field_has_label(body, field_id):
    """True if either <label for="ID"> or aria-label="..." exists on the input."""
    has_label_for = bool(re.search(
        r'<label[^>]*\sfor="%s"' % re.escape(field_id), body, re.I
    ))
    has_aria = bool(re.search(
        r'(id="%s"[^>]*\saria-label|aria-label[^>]*\sid="%s")' % (
            re.escape(field_id), re.escape(field_id)
        ), body, re.I
    ))
    return has_label_for or has_aria


@pytest.mark.parametrize("page", PAGES_WITH_PALETTE)
def test_cmd_palette_input_has_label(client, page):
    res = client.get(page)
    assert res.status_code == 200
    assert _field_has_label(res.text, "cmd-palette-input"), (
        f"{page}: #cmd-palette-input has no <label for=...> or aria-label"
    )


def test_bulk_screening_fields_have_labels(client):
    res = client.get("/bulk-screening")
    assert res.status_code == 200
    for field_id in ("file-upload", "bulk-input"):
        assert _field_has_label(res.text, field_id), (
            f"/bulk-screening: #{field_id} has no associated label"
        )
