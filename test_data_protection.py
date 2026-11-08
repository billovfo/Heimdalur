"""Data protection (audit 2026-11 I-5, lane W6c). Offline.

Real register PDFs live outside git, in $HEIMDAL_FIXTURES_DIR (default: the
evidence store). A missing fixture skips with a reason that names the variable;
pii_digest() pins register names in tests without holding them in git.
"""
import os
import re
import subprocess
import sys

import pytest

from heimdal.pdfhist import testsupport
from heimdal.pdfhist.testsupport import pii_digest


def test_missing_fixture_skips_and_names_the_variable(monkeypatch, tmp_path):
    monkeypatch.setattr(testsupport, "FIXTURE_DIR", tmp_path)
    with pytest.raises(pytest.skip.Exception, match="HEIMDAL_FIXTURES_DIR"):
        testsupport.fixture_text("felag_10.bin.txt")


def test_present_fixture_is_read_from_the_fixture_dir(monkeypatch, tmp_path):
    (tmp_path / "x.bin.txt").write_text("Dagf. 01-01-2020", encoding="utf-8")
    monkeypatch.setattr(testsupport, "FIXTURE_DIR", tmp_path)
    assert testsupport.fixture_text("x.bin.txt") == "Dagf. 01-01-2020"


def test_fixture_dir_comes_from_the_env_and_defaults_to_the_store(tmp_path):
    def fixture_dir(**env):
        code = "from heimdal.pdfhist.testsupport import FIXTURE_DIR; print(FIXTURE_DIR)"
        e = {k: v for k, v in os.environ.items() if k != "HEIMDAL_FIXTURES_DIR"}
        return subprocess.run([sys.executable, "-c", code], env={**e, **env}, check=True,
                              capture_output=True, text=True,
                              cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip()
    assert fixture_dir(HEIMDAL_FIXTURES_DIR=str(tmp_path)) == str(tmp_path)
    assert fixture_dir().endswith("/.local/share/heimdal/evidence/2026-11-audit/fixtures/pdfhist")


def test_pii_digest_is_exact_and_order_free_for_sets():
    assert re.fullmatch(r"[0-9a-f]{16}", pii_digest("Anna Testsen"))
    assert pii_digest("Anna Testsen") != pii_digest("Anna Testsen ")
    assert pii_digest({"b", "a"}) == pii_digest({"a", "b"}) == pii_digest(["a", "b"])
    assert pii_digest(("APPOINTED", "x")) != pii_digest(("REMOVED", "x"))
