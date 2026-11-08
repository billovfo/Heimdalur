"""Regression test for the live-pilot fetch URL construction.

Round 1 of the live pilot proved that '/api/Skraseting/0' (digit form) 404s;
the live inline JS maps the digit to the WORD:
    method = method===0 ? 'LysingPdf' : 'FelagPdf'
    url = '/api/Skraseting/' + method + '?id=' + id + '&s=' + s
(verified in tmp-pdf-recon/search_10_p1.html). Importing the pilot module
performs NO network access and touches no database."""
import re

import pytest

import pdfhist_live_pilot as pilot


def test_api_url_uses_word_segment_not_digit():
    ref = {"method": "0", "pdf_type": "LysingPdf", "api_id": "41211",
           "token": "tok123"}
    url = pilot.build_api_url(ref)
    assert url == ("https://www.skraseting.fo/api/Skraseting/LysingPdf"
                   "?id=41211&s=tok123")
    ref = {"method": "1", "pdf_type": "FelagPdf", "api_id": "134164",
           "token": "tok456"}
    url = pilot.build_api_url(ref)
    assert url == ("https://www.skraseting.fo/api/Skraseting/FelagPdf"
                   "?id=134164&s=tok456")
    # the digit form must never appear in a built URL
    assert not re.search(r"/api/Skraseting/(0|1)\?", url)


def test_url_logging_never_leaks_the_token():
    ref = {"method": "0", "pdf_type": "LysingPdf", "api_id": "6513",
           "token": "SECRET_TOKEN_VALUE"}
    logged = pilot._url_for_log(pilot.build_api_url(ref))
    assert "SECRET_TOKEN_VALUE" not in logged
    assert "s=<redacted>" in logged


def test_pilot_budget_and_gaps_are_the_approved_ones(monkeypatch):
    assert pilot.SEARCH_GAP >= 1.0
    assert pilot.PDF_GAP >= 1.2
    assert pilot.PILOT_COMPANIES == ["10", "5015", "5019", "8023", "8664",
                                     "931"]
    assert pilot.TOTAL_BUDGET <= 60  # small pilot budget, never a full run
    # the pilot DSN comes only from the env; unset refuses (G2, audit H-8)
    monkeypatch.setenv("HEIMDAL_PDFHIST_PROJECTION_DSN", "postgresql://x@h:1/any")
    assert pilot.pilot_dsn() == "postgresql://x@h:1/any"
    monkeypatch.delenv("HEIMDAL_PDFHIST_PROJECTION_DSN")
    with pytest.raises(RuntimeError, match="not set"):
        pilot.pilot_dsn()