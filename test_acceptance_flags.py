"""Offline regression (X5c): acceptance runners must set the feature flags
they accept, not inherit tools/test_env.sh's pinned-off values.

Each runner is imported in a scratch interpreter whose environment has all
four flags false (the test-env state); importing the runner must leave them
all true. No DB is touched: import only builds the Flask app.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FLAGS = (
    "PDF_HISTORY_INTEGRATION_ENABLED",
    "PDF_HISTORY_UI_ENABLED",
    "REGISTRY_ANALYTICS_ENABLED",
    "REGISTRY_ANALYTICS_GRAPH_ENABLED",
)


def _flags_after_import(module):
    env = dict(os.environ)
    env.update({flag: "false" for flag in FLAGS})
    env.setdefault("HEIMDAL_READ_DB", "postgresql://gate@127.0.0.1:1/none")
    env.setdefault("HEIMDAL_QA_DB", "postgresql://gate@127.0.0.1:1/none")
    code = (
        "import json, os\n"
        f"import {module}\n"
        f"print(json.dumps({{f: os.environ[f] for f in {FLAGS!r}}}))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


def _assert_all_on(module):
    flags = _flags_after_import(module)
    assert all(flags.get(flag, "").lower() in {"1", "true", "yes", "on"}
               for flag in FLAGS), flags


def test_owner_acceptance_sets_flags_it_accepts():
    _assert_all_on("acceptance.owner_acceptance_ux")


def test_frontend_quality_sets_flags_it_accepts():
    _assert_all_on("acceptance.frontend_quality")
