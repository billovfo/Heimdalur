"""Quality gates G1-G3 (P1.1; G4-G7 added with their phases).

G1: no silent commit swallowing outside fixtures.
G2: no DB-connect at import (collect works with DB down).
G3: single canonical DSN paths (no password literals outside the one helper).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _py_files(*rel_dirs):
    for rel in rel_dirs:
        base = ROOT / rel
        if base.is_file():
            yield base
        else:
            yield from sorted(base.rglob("*.py"))


def test_g1_no_silent_commit_swallowing():
    offenders = []
    for path in _py_files("heimdal"):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"conn\.commit\(\)", src):
            window = src[max(0, m.start() - 200):m.end() + 120]
            if re.search(r"except[^\n:]*:\s*\n\s*pass", window):
                offenders.append(f"{path}:{src[:m.start()].count(chr(10)) + 1}")
    assert not offenders, offenders


def test_g2_no_db_connect_at_import():
    # Only DIRECT module-level calls break collect; connects inside
    # functions/fixtures are fine (they run with a live DB).
    offenders = []
    # H-10: only what pytest collects (root test_*.py), never an rglob over
    # archive/, tmp-*/ or worktrees
    for path in sorted(ROOT.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            targets = []
            if isinstance(node, ast.Expr):
                targets = [node.value]
            elif isinstance(node, ast.Assign):
                targets = [node.value]
            for value in targets:
                for child in ast.walk(value):
                    if isinstance(child, ast.Call):
                        func = child.func
                        name = func.attr if isinstance(func, ast.Attribute) \
                            else getattr(func, "id", "")
                        if name in ("connect",) or "live_ids" in name.lower() \
                                or "_ids(" in name.lower():
                            offenders.append(f"{path}:{child.lineno}")
        # module-level lazy wrappers are fine; direct _live_ids() call is not
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "_IDS"
                    for t in node.targets):
                offenders.append(f"{path}:{node.lineno}")
    assert not offenders, offenders


# G3 (H-10): every password-bearing DSN form, not one known password. A value
# starting with a placeholder character ($VAR, ${VAR}, <pw>, ***, %(pw)s) is
# not a literal. Offenders are reported as path:line [form], never the value.
_PLACEHOLDER = "${<*%"
_PW_FORMS = {
    "uri": re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^/\s:@'\"]+:([^@\s/'\"]+)@"),
    "conninfo": re.compile(r"\bpassword\s*=\s*['\"]?([^\s'\"]+)"),
    "PGPASSWORD": re.compile(r"\bPGPASSWORD\s*=\s*['\"]?([^\s'\"]+)"),
}
_PW_KEYS = {"password", "PGPASSWORD"}


def _pw_forms(text):
    return [form for form, rx in _PW_FORMS.items() for m in rx.finditer(text)
            if m.group(1)[0] not in _PLACEHOLDER]


def _is_str(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _password_literals(path):
    """[(line, form)]. In Python only string literals count (so code such as
    password=os.environ[...] is fine), plus password=/PGPASSWORD= set to one."""
    src = path.read_text(encoding="utf-8")
    if path.suffix != ".py":
        return [(i, f) for i, line in enumerate(src.splitlines(), 1) for f in _pw_forms(line)]
    hits = set()
    for node in ast.walk(ast.parse(src)):
        if _is_str(node):
            hits.update((node.lineno, f) for f in _pw_forms(node.value))
        elif isinstance(node, ast.keyword) and node.arg in _PW_KEYS and _is_str(node.value):
            hits.add((node.value.lineno, "kwarg"))
        elif isinstance(node, ast.Assign) and _is_str(node.value) and any(
                isinstance(t, ast.Subscript) and _is_str(t.slice) and t.slice.value in _PW_KEYS
                for t in node.targets):
            hits.add((node.lineno, "assign"))
    return sorted(hits)


def test_g3_scanner_catches_every_form(tmp_path):
    # "§" stands for "@" so this file itself holds no DSN with a password (check_j J-1)
    py = tmp_path / "sample.py"
    py.write_text(('A = "postgresql://u:s3cret§h/db"\n'
                   'B = "host=h user=u password=s3cret dbname=d"\n'
                   'psycopg.connect(password="s3cret")\n'
                   'os.environ["PGPASSWORD"] = "s3cret"\n'
                   'OK = ["postgresql://u§h:5432/db", "password=$PW", "postgres://u:${PW}§h",'
                   ' password_var]\n'
                   'psycopg.connect(password=os.environ["PW"])\n').replace("§", "@"))
    sh = tmp_path / "sample.sh"
    sh.write_text(('export PGPASSWORD=s3cret\nexport PGPASSWORD="$PW"\n'
                   'psql postgresql://postgres:s3cret§localhost/x\n').replace("§", "@"))
    assert _password_literals(py) == [(1, "uri"), (2, "conninfo"), (3, "kwarg"), (4, "assign")]
    assert _password_literals(sh) == [(1, "PGPASSWORD"), (3, "uri")]


def test_g3_single_canonical_dsn_paths():
    # Zero tolerance in canonical scope: no password-bearing DSN literal.
    # Auth comes from PGPASSFILE/PGPASSWORD. Historical phase docs and
    # archived scripts are out of scope here; check_j J-1 scans every tracked
    # file for the URI form. scripts/ and .audit-tools/ are in scope (I-4, W6c).
    scope = (list(_py_files("heimdal")) + sorted(ROOT.glob("*.py"))
             + sorted(ROOT.glob("acceptance/*.py")) + sorted(ROOT.glob("tools/**/*.py"))
             + sorted(ROOT.glob("tools/*.sh")) + sorted(ROOT.glob("scripts/*.py"))
             + sorted(ROOT.glob(".audit-tools/*.py"))
             + [ROOT / "docs" / "DRIFT-RUNBOOK-v1.md",
                ROOT / "docs" / "STATUS.md"])
    this_file = Path(__file__).resolve()  # its samples are fake (s3cret)
    offenders = [f"{path.relative_to(ROOT)}:{line} [{form}]"
                 for path in scope if path.exists() and "__pycache__" not in path.parts
                 and path.resolve() != this_file
                 for line, form in _password_literals(path)]
    assert not offenders, offenders


def test_g3_raw_connects_allowlisted():
    allowed = {
        "heimdal/modules/shared/db.py",
        "heimdal/pdfhist/db.py",
        "heimdal/pdfhist/migrate.py",
        "heimdal/pdfhist/migrations.py",
        "heimdal/canonical_migrations.py",  # the canonical ledger's CLI, as pdfhist's (B-17)
        "heimdal/pdfhist/testsupport.py",
        "heimdal/pdfhist/consumer.py",
        "heimdal/modules/review/routes.py",  # two documented read helpers
    }
    offenders = []
    for path in _py_files("heimdal"):
        rel = str(path.relative_to(ROOT))
        if rel in allowed:
            continue
        if "psycopg.connect" in path.read_text(encoding="utf-8"):
            offenders.append(rel)
    assert not offenders, offenders


def test_g4_atomicity_proven():
    src = Path(__file__).resolve().parent.joinpath(
        "test_review_atomicity.py").read_text(encoding="utf-8")
    for name in ("test_injected_failure_leaves_zero_partial_rows",
                 "test_freeze_dryrun_apply_e2e",
                 "test_pin_stable_on_identical_state"):
        assert f"def {name}" in src, name


def test_g5_ledger_immutable_proven():
    src = Path(__file__).resolve().parent.joinpath(
        "test_migration_ledger.py").read_text(encoding="utf-8")
    for name in ("test_empty_ledger_baseline_registers",
                 "test_nonempty_ledger_baseline_refused",
                 "test_changed_checksum_hard_failure",
                 "test_no_rerun_path_in_normal_code"):
        assert f"def {name}" in src, name


def test_g6_bootstrap_exists_and_executable():
    import os
    import stat
    p = Path(__file__).resolve().parent / "tools" / "dev_bootstrap.sh"
    assert p.exists()
    assert bool(os.stat(p).st_mode & stat.S_IXUSR)
    assert (Path(__file__).resolve().parent / "tools" / "dev_env.sh").exists()
    assert (Path(__file__).resolve().parent / "pytest.ini").exists()


def test_g7_status_probe_current():
    import json
    import subprocess
    proc = subprocess.run(
        ["python3", "tools/status_check.py"], capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parent), timeout=60)
    assert proc.returncode == 0, proc.stderr[-500:]
    facts = json.loads(proc.stdout)
    for key in ("projection", "canonical_entities", "backups"):
        assert key in facts, key
