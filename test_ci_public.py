"""Public CI contract (lane X5d). Offline: reads repo files, no DB, no network.

The workflow runs only in the public snapshot (Heimdalur): the runner is
ubuntu-latest, its postgres service is postgres:18, and its one script is
tools/ci_public.sh. That script must stay on the public subset: no clone, no
dump, no fixtures, no private docs.
"""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = ROOT / "tools" / "ci_public.sh"


def test_workflow_is_pinned_and_public_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "push:" in text and "pull_request:" in text
    assert "branches: [main]" in text
    assert "ubuntu-latest" in text
    assert "actions/checkout@v5" in text
    assert "actions/setup-python@v6" in text
    assert "python-version: '3.14'" in text
    assert "allow-prereleases: true" in text
    assert "postgres:18" in text
    assert "POSTGRES_PASSWORD: ci" in text
    assert "tools/ci_public.sh" in text
    for private in ("backups/", "HEIMDAL_FIXTURES_DIR", ".dev-pgpass",
                    "docs/audit-2026-11", "docs/phase0_scale_measurement.json"):
        assert private not in text, private


def test_public_runner_never_uses_private_inputs():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "HEIMDAL_GATE_INLINE=1" in text
    assert "dev-pgpass" not in text
    assert "_ci" in text
    for private in ("backups/", "docs/audit-2026-11",
                    "docs/phase0_scale_measurement.json"):
        assert private not in text, private


def test_public_runner_is_valid_shell():
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_public_runner_creates_the_reader_role_before_the_gate():
    """A fresh postgres:18 service has no heimdal_reader; schema.sql grants to it."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "heimdal_reader" in text
    assert "FROM pg_roles WHERE rolname" in text
    assert "IF NOT EXISTS" in text
    assert "CREATE ROLE %I LOGIN" in text
    assert text.index("\nensure_roles\n") < text.index("HEIMDAL_GATE_INLINE=1 tools/gate.sh")


def test_workflow_is_guarded_to_the_public_repo():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "if: ${{ github.event.repository.private == false }}" in text
    assert "run: bash tools/ci_public.sh" in text


def test_canonical_ledger_test_runs_without_the_clone():
    gate = (ROOT / "tools" / "gate.sh").read_text(encoding="utf-8")
    clone_only = gate.split("CLONE_ONLY_TESTS=", 1)[1].split(
        "INLINE_FIXTURE_TESTS", 1)[0]
    assert "test_canonical_db.py" not in clone_only
    canonical = (ROOT / "test_canonical_db.py").read_text(encoding="utf-8")
    assert "psycopg.OperationalError" in canonical
    assert "no heimdal clone" in canonical


def test_inline_gate_prints_per_test_skip_reasons():
    gate = (ROOT / "tools" / "gate.sh").read_text(encoding="utf-8")
    assert "PYTEST+=(-rs)" in gate
    assert "grep -E '^(SKIP|SKIPPED)'" in gate


def test_public_runner_initialises_a_throwaway_repo():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'git -C "$ROOT" init -q' in text


def test_check_e_uses_the_caller_database_server():
    text = (ROOT / "tools" / "audit" / "check_e.py").read_text(encoding="utf-8")
    assert "HEIMDAL_TEST_ADMIN_DSN" in text
    assert "postgresql://postgres@localhost:5432/postgres" not in text
