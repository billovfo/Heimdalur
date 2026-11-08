#!/usr/bin/env python3
"""Audit lane H check (2026-11): the root test suite must be isolatable and honest.

Asserts, by static reading only (imports nothing, touches no database):
  H-1  no root test_*.py hardcodes a live DSN (dbname=heimdal / :5432/heimdal /
       pdfhist_pilot) -- every DB test must resolve its DSN from the environment
       so it can be pointed at a restored clone;
  H-2  no root test_*.py mixes pytest-collected test_* functions with a
       non-raising check() helper -- under pytest such tests pass even when
       every check is false (they can only fail on an exception);
       and no root test_*.py is a zero-test __main__ script (those live in
       acceptance/ and run from tools/gate.sh);
  H-5  no root test_*.py or acceptance/*.py pins a 32/64-hex digest literal:
       snapshot digests and pins live in tests_baseline.py, next to its dump;
  H-10 G2 scans only the collected root test_*.py (no rglob over archives),
       and G3 self-tests every password-bearing DSN form.
Run: python3 tools/audit/check_h.py   (exit 1 while a finding is present)
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE_DSN = re.compile(r"dbname=(heimdal|pdfhist_pilot)\b|:5432/(heimdal|pdfhist_pilot)\b")
DIGEST = re.compile(r"['\"]([0-9a-f]{32}|[0-9a-f]{64})['\"]")


def _raises(fn):
    return any(isinstance(n, (ast.Assert, ast.Raise)) for n in ast.walk(fn))


hardcoded, vacuous, scripts, digests = [], [], [], []
for path in sorted(ROOT.glob("acceptance/*.py")):
    digests += [f"{path.relative_to(ROOT)}:{i}" for i, line in
                enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if DIGEST.search(line)]
for path in sorted(ROOT.glob("test_*.py")):
    src = path.read_text(encoding="utf-8")
    digests += [f"{path.name}:{i}" for i, line in enumerate(src.splitlines(), 1)
                if DIGEST.search(line)]
    hardcoded += [f"{path.name}:{i}" for i, line in enumerate(src.splitlines(), 1)
                  if LIVE_DSN.search(line)]
    body = ast.parse(src).body
    funcs = [n for n in body if isinstance(n, ast.FunctionDef)]
    soft_check = any(f.name == "check" and not _raises(f) for f in funcs)
    tests = [f.name for f in funcs if f.name.startswith("test")]
    if soft_check and tests:
        vacuous.append(f"{path.name} ({len(tests)} tests)")
    if not tests and any(isinstance(n, ast.If) and "__main__" in ast.unparse(n.test)
                         for n in body):
        scripts.append(path.name)

gates = (ROOT / "test_quality_gates.py").read_text(encoding="utf-8")
g2_rglob = '_py_files(".")' in gates
g3_selftest = "def test_g3_scanner_catches_every_form" in gates

print(f"H-1 hardcoded live DSN literals in test files: {len(hardcoded)}")
for item in hardcoded:
    print("   ", item)
print(f"H-2 files whose pytest tests cannot fail on a false check(): {len(vacuous)}")
for item in vacuous:
    print("   ", item)
print(f"H-2 zero-test __main__ scripts named test_*.py: {len(scripts)}", *scripts)
print(f"H-5 digest literals in test files: {len(digests)}", *digests)
print(f"H-10 G2 rglob over the tree: {g2_rglob}; G3 form self-test: {g3_selftest}")
try:
    assert not hardcoded, "H-1: repoint these to heimdal.pdfhist.db.qa_dsn()"
    assert not vacuous, "H-2: make check() raise under pytest, or rename to non-test_ helpers"
    assert not scripts, "H-2: move __main__ runners to acceptance/ (no test_ prefix)"
    assert not digests, "H-5: move snapshot digests into tests_baseline.py"
    assert not g2_rglob and g3_selftest, "H-10: G2 scope / G3 form self-test"
except AssertionError as exc:
    print("FAIL", exc)
    sys.exit(1)
print("OK")
