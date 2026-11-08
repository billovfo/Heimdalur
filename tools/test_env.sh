# Test environment (audit 2026-11 H-1): every DSN points at a throwaway test DB,
# overriding any live value (e.g. from tools/dev_env.sh). Source it:
#   source tools/test_env.sh
# Knobs: HEIMDAL_TEST_DB    pdfhist test DB, recreated by the tests (pdfhist_test)
#        HEIMDAL_TEST_CLONE heimdal clone, made by tools/test_db_refresh.sh (heimdal_test)
# Both names must contain "_test". The root conftest.py refuses live DSNs.
export PGPASSFILE="${PGPASSFILE:-$(cd "$(git rev-parse --git-common-dir)/.." && pwd)/tools/.dev-pgpass}"
# Real register PDFs (personal data) live in the evidence store, outside git (I-5)
export HEIMDAL_FIXTURES_DIR="${HEIMDAL_FIXTURES_DIR:-$HOME/.local/share/heimdal/evidence/2026-11-audit/fixtures/pdfhist}"
export HEIMDAL_TEST_DB="${HEIMDAL_TEST_DB:-pdfhist_test}"
export HEIMDAL_TEST_CLONE="${HEIMDAL_TEST_CLONE:-heimdal_test}"
case "$HEIMDAL_TEST_DB/$HEIMDAL_TEST_CLONE" in
  *_test*/*_test*) ;;
  *) echo "test_env.sh: HEIMDAL_TEST_DB and HEIMDAL_TEST_CLONE must contain _test" >&2
     return 2 ;;
esac
_pg=postgresql://postgres@localhost:5432
export HEIMDAL_TEST_ADMIN_DSN=$_pg/postgres
export HEIMDAL_READ_DB=$_pg/$HEIMDAL_TEST_CLONE
export HEIMDAL_QA_DB=$_pg/$HEIMDAL_TEST_CLONE
export HEIMDAL_PG_DSN=$_pg/$HEIMDAL_TEST_CLONE
export HEIMDAL_DB=$_pg/$HEIMDAL_TEST_CLONE
export HEIMDAL_PDFHIST_PROJECTION_DSN=$_pg/$HEIMDAL_TEST_DB
export HEIMDAL_PDFHIST_DB=$_pg/$HEIMDAL_TEST_DB
unset HEIMDAL_PDFHIST_TEST_DB HEIMDAL_ALLOW_LIVE _pg
