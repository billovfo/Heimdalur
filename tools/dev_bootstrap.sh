#!/usr/bin/env bash
# One-command bootstrap from clean checkout (P1.3/G6):
#   ./tools/dev_bootstrap.sh
# Provisions the test DB, applies migrations, runs the standard suites.
# Fails with a named prerequisite when postgres/env is missing.
# Runs only against test DBs (tools/test_env.sh; audit 2026-11 H-9).
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source tools/test_env.sh || exit 2
export PYTEST_ADDOPTS="-p no:cacheprovider ${PYTEST_ADDOPTS:-}"
if ! python3 -c "import psycopg, sys; psycopg.connect(sys.argv[1], connect_timeout=5).close()" "$HEIMDAL_PG_DSN"; then
  echo "PREREQUISITE MISSING: test clone not reachable at $HEIMDAL_PG_DSN"
  echo "Start dev postgres (see docs/DRIFT-RUNBOOK-v1.md), then run tools/test_db_refresh.sh."
  exit 3
fi
python3 -c "
import sys; sys.path.insert(0, '.')
from heimdal.pdfhist.testsupport import ensure_test_database
from heimdal.pdfhist import db
print('test DB ready:', ensure_test_database())
print('test DSN helper:', db.test_dsn())
"
# --status would CREATE the ledger table; read it only where it exists.
if python3 -c "import psycopg, sys; sys.exit(psycopg.connect(sys.argv[1]).execute(\"SELECT to_regclass('schema_migrations')\").fetchone()[0] is None)" "$HEIMDAL_PDFHIST_PROJECTION_DSN"; then
  python3 -m heimdal.pdfhist.migrations --status --dsn "$HEIMDAL_PDFHIST_PROJECTION_DSN"
else
  echo "migrations --status: no schema_migrations ledger in the projection DB, skipped"
fi
exec python3 -m pytest test_db_transaction.py test_review_atomicity.py \
  test_migration_ledger.py test_quality_gates.py test_person_review.py \
  test_person_review_enhancements.py test_registry_projection.py -q
