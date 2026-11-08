"""D-04: PDF extraction has byte, page and wall-clock limits, and the live
pilot streams response bodies under a byte cap. Offline: synthetic PDFs built
here, a fake HTTP session, no DB."""
import multiprocessing
import os
import time

import pytest

import pdfhist_live_pilot as pilot
from heimdal.pdfhist import extract
from heimdal.pdfhist.fixture_pdf import make_hostile_pdf, make_pdf

OP = b"BT /F1 1 Tf 0 0 Td (A) Tj ET\n"


def _limit(pdf_bytes):
    with pytest.raises(extract.ExtractLimitExceeded) as caught:
        extract.extract_text(pdf_bytes)
    assert caught.value.review_reason == "EXTRACT_LIMIT_EXCEEDED"
    assert isinstance(caught.value, extract.TextExtractionError)  # REVIEW path
    return str(caught.value)


def test_byte_cap_rejects_before_parsing():
    started = time.monotonic()
    assert "bytes >" in _limit(b"%PDF-1.7\n" + b" " * extract.MAX_PDF_BYTES)
    assert time.monotonic() - started < 1


def test_page_cap_rejects_many_pages_sharing_one_stream():
    pages = extract.MAX_PAGES + 1
    assert f"{pages} pages" in _limit(make_hostile_pdf(OP, pages=pages))
    text = extract.extract_text(make_hostile_pdf(OP, pages=extract.MAX_PAGES))
    assert text.count("A") == extract.MAX_PAGES  # the cap itself still passes


def test_time_cap_kills_a_content_bomb(monkeypatch):
    monkeypatch.setattr(extract, "EXTRACT_TIMEOUT_S", 1.0)
    bomb = make_hostile_pdf(OP * (4_000_000 // len(OP)))  # ~10 KB, ~30 s pypdf
    assert len(bomb) < 20_000
    started = time.monotonic()
    assert "exceeded 1 s" in _limit(bomb)
    assert time.monotonic() - started < 5
    assert multiprocessing.active_children() == []  # killed and reaped


def test_a_dying_extractor_is_a_plain_extraction_error(monkeypatch):
    monkeypatch.setattr(extract, "_extract_in_child",
                        lambda *_: os._exit(3))
    with pytest.raises(extract.TextExtractionError) as caught:
        extract.extract_text(make_pdf(["x"]))
    assert caught.value.review_reason == "EMPTY_TEXT_LAYER"


class _Resp:
    def __init__(self, status, chunks):
        self.status_code, self.headers = status, {}
        self.chunks, self.read, self.closed = chunks, 0, False

    def iter_content(self, _size):
        for chunk in self.chunks:
            self.read += 1
            yield chunk

    def close(self):
        self.closed = True


def _serve(monkeypatch, resp):
    calls = []
    monkeypatch.setattr(pilot.session, "get",
                        lambda *a, **kw: calls.append(kw) or resp)
    monkeypatch.setattr(pilot, "PDF_GAP", 0)
    monkeypatch.setattr(pilot, "budget", {"search": 0, "pdf": 0, "total": 0})
    return calls


def test_http_get_streams_and_drops_a_body_over_the_cap(monkeypatch):
    chunk = b"0" * 65536
    resp = _Resp(200, [b"%PDF-"] + [chunk] * 1000)  # 65 MB on offer
    calls = _serve(monkeypatch, resp)
    assert pilot.http_get("https://www.skraseting.fo/x", "pdf") == \
        ("BODY_TOO_LARGE", b"", {})
    assert calls[0]["stream"] is True and resp.closed
    assert resp.read <= pilot.MAX_BODY_BYTES // len(chunk) + 2  # stopped early


def test_http_get_returns_a_body_under_the_cap(monkeypatch):
    resp = _Resp(200, [b"%PDF-1.7 ", b"small"])
    _serve(monkeypatch, resp)
    assert pilot.http_get("https://www.skraseting.fo/x", "pdf") == \
        (200, b"%PDF-1.7 small", {})
    assert resp.closed


def test_fetch_reports_body_too_large(monkeypatch):
    monkeypatch.setattr(pilot, "http_get",
                        lambda *a, **kw: ("BODY_TOO_LARGE", b"", {}))
    outcome = pilot.fetch_pdf_document(
        "10", {"pdf_type": "FelagPdf", "api_id": "1", "token": "t"}, "r")
    assert outcome["body"] is None and outcome["error"] == "BODY_TOO_LARGE"
