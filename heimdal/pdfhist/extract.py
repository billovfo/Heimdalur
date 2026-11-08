"""pypdf-based text extraction (plan v3.1.2, phase 8).

Primary and only extractor for this implementation. Extraction failures NEVER
lose the document: the caller keeps the original bytes and downgrades the parse
to REVIEW per plan rules (the exception's review_reason). No OCR (deferred).

Hard limits (audit D-04): pypdf caps one stream's decompressed size, but not
page count, stream reuse across pages or total work, so a 10 KB PDF can run for
minutes. extract_text therefore refuses oversized input up front and runs pypdf
in a forked child that is killed at the wall-clock limit. Limits come from the
16,400 PDFs of the recovered 2026-09-04 corpus (p50 / p99 / max): bytes 27 KB /
130 KB / 144 KB; pages 1 / 2 / 3; seconds per PDF through this function 0.02 /
0.28 / 0.92. All 16,400 extract identically under these limits.
"""
from __future__ import annotations

import io
import multiprocessing

from pypdf import PdfReader

MAX_PDF_BYTES = 2_000_000    # corpus max 144 KB (x14)
MAX_PAGES = 20               # corpus max 3 (x6)
EXTRACT_TIMEOUT_S = 10.0     # corpus max 0.92 s (x10), p99 0.28 s

# fork: the child starts with pypdf already imported (a fresh interpreter
# costs ~0.6 s per document here).
# ponytail: fork assumes single-threaded callers (CLI ingest, collector); a
# threaded caller would need the forkserver context with this module preloaded.
_FORK = multiprocessing.get_context("fork")


class TextExtractionError(Exception):
    """pypdf cannot extract any text layer (caller keeps bytes)."""
    review_reason = "EMPTY_TEXT_LAYER"


class ExtractLimitExceeded(TextExtractionError):
    """The PDF exceeds a byte, page or wall-clock limit (caller keeps bytes)."""
    review_reason = "EXTRACT_LIMIT_EXCEEDED"


def _extract_in_child(pdf_bytes: bytes, max_pages: int, send) -> None:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if len(reader.pages) > max_pages:
            send.send(("PAGES", len(reader.pages)))
            return
        send.send(("OK", "\n".join(page.extract_text() or ""
                                   for page in reader.pages)))
    except Exception as error:  # noqa: BLE001 - pypdf raises many variants
        send.send(("ERROR", str(error)))


def extract_text(pdf_bytes: bytes) -> str:
    """Return the full extracted text of a PDF (joined with newlines).

    Raises ExtractLimitExceeded past MAX_PDF_BYTES, MAX_PAGES or
    EXTRACT_TIMEOUT_S, and TextExtractionError on corrupt/empty text layers.
    The ingest layer turns either into parse_status='REVIEW' with the
    exception's review_reason while retaining the raw bytes.
    """
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise ExtractLimitExceeded(
            f"{len(pdf_bytes)} bytes > {MAX_PDF_BYTES}")
    recv, send = _FORK.Pipe(duplex=False)
    child = _FORK.Process(target=_extract_in_child,
                          args=(pdf_bytes, MAX_PAGES, send), daemon=True)
    child.start()
    send.close()
    try:
        if not recv.poll(EXTRACT_TIMEOUT_S):
            raise ExtractLimitExceeded(
                f"extraction exceeded {EXTRACT_TIMEOUT_S:g} s")
        kind, value = recv.recv()
    except EOFError:
        raise TextExtractionError("extractor process died") from None
    finally:
        child.kill()
        child.join()
        recv.close()
    if kind == "PAGES":
        raise ExtractLimitExceeded(f"{value} pages > {MAX_PAGES}")
    if kind == "ERROR":
        raise TextExtractionError(value)
    if not value.strip():
        raise TextExtractionError("empty text layer")
    return value
