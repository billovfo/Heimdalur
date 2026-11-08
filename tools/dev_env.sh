# Canonical dev environment (P1.3). Source this file:
#   source tools/dev_env.sh
# Dev-box defaults only. NO passwords live here: authentication comes from
# PGPASSFILE (tools/.dev-pgpass, mode 0600, git-ignored) or a PGPASSWORD you
# export yourself. Production uses real secrets via the same names.
#
# Dev DBs since 2026-11-06 (fix plan W2, owner D1/D2): the 2026-09-04 state
# recovered from the old heimdal-qa container. The 2026-08-03 restore
# (`heimdal`) and the empty `pdfhist_pilot` stay untouched as fallbacks.
# Tests never use this file: they refuse these DSNs (conftest.py) and run on
# `source tools/test_env.sh`.
export PGPASSFILE="${PGPASSFILE:-$(cd "$(git rev-parse --git-common-dir)/.." && pwd)/tools/.dev-pgpass}"
export HEIMDAL_DEV_DB="${HEIMDAL_DEV_DB:-heimdal_recovered_20260904}"
export HEIMDAL_DEV_PILOT="${HEIMDAL_DEV_PILOT:-pdfhist_pilot_recovered_20260904}"
_pg=postgresql://postgres@localhost:5432
# Write path (tx(): watchlist toggle) on the dev DB; the pdfhist ledger CLI
# also defaults to this DSN, so always pass --dsn to it (W5b).
export HEIMDAL_PG_DSN="${HEIMDAL_PG_DSN:-$_pg/$HEIMDAL_DEV_DB}"
export HEIMDAL_PDFHIST_PROJECTION_DSN="${HEIMDAL_PDFHIST_PROJECTION_DSN:-$_pg/$HEIMDAL_DEV_PILOT}"
export HEIMDAL_PDFHIST_DB="${HEIMDAL_PDFHIST_DB:-$_pg/$HEIMDAL_DEV_PILOT}"
# Portal reads as the read-only role (B-9, W5b); password via PGPASSFILE.
export HEIMDAL_READ_DB="${HEIMDAL_READ_DB:-postgresql://heimdal_reader@localhost:5432/$HEIMDAL_DEV_DB}"
export HEIMDAL_QA_DB="${HEIMDAL_QA_DB:-$_pg/$HEIMDAL_DEV_DB}"
export HEIMDAL_DB="${HEIMDAL_DB:-$_pg/$HEIMDAL_DEV_DB}"
export HEIMDAL_TEST_ADMIN_DSN="${HEIMDAL_TEST_ADMIN_DSN:-$_pg/postgres}"
export HEIMDAL_TEST_DB="${HEIMDAL_TEST_DB:-pdfhist_test}"
# PDF history stays OFF until fix waves W3a (D-01/D-02) and W4d are green: the
# recovered pilot restores with its DB switches ON, and env wins over the DB.
export PDF_HISTORY_INTEGRATION_ENABLED="${PDF_HISTORY_INTEGRATION_ENABLED:-false}"
export PDF_HISTORY_UI_ENABLED="${PDF_HISTORY_UI_ENABLED:-false}"
unset _pg
# Analytics parked (owner D6, fix plan W4c): build 13 holds wrong ownership
# observations (C-1). Off until the owner decides on a rebuild.
export REGISTRY_ANALYTICS_ENABLED="${REGISTRY_ANALYTICS_ENABLED:-false}"
export REGISTRY_ANALYTICS_GRAPH_ENABLED="${REGISTRY_ANALYTICS_GRAPH_ENABLED:-false}"
