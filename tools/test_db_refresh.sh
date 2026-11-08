#!/usr/bin/env bash
# (Re)create the heimdal test clone from a backup dump (audit 2026-11 H-1):
#   tools/test_db_refresh.sh [TARGET_DB [DUMP]]
# TARGET_DB defaults to $HEIMDAL_TEST_CLONE, else heimdal_test, and must match
# ^[a-z_]*_test[a-z0-9_]*$ (never a live DB). DUMP defaults to the newest
# backups/heimdal_*T*.dump. PGBIN overrides the Postgres binaries.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PGBIN="${PGBIN:-$HOME/.local/share/heimdal/pg/server/postgresql-18.4.0-x86_64-unknown-linux-gnu/bin}"
export PGPASSFILE="${PGPASSFILE:-$(cd "$(git rev-parse --git-common-dir)/.." && pwd)/tools/.dev-pgpass}"
TARGET="${1:-${HEIMDAL_TEST_CLONE:-heimdal_test}}"
if [[ ! "$TARGET" =~ ^[a-z_]*_test[a-z0-9_]*$ ]]; then
  echo "test_db_refresh.sh: refusing target '$TARGET' (must match ^[a-z_]*_test[a-z0-9_]*\$)" >&2
  exit 2
fi
# Default: newest dump of the dev DB (tools/dev_env.sh HEIMDAL_DEV_DB).
DEV_DB="${HEIMDAL_DEV_DB:-heimdal_recovered_20260904}"
DUMP="${2:-$(ls "$ROOT"/backups/"$DEV_DB"_[0-9]*T[0-9]*.dump 2>/dev/null | sort | tail -n 1 || true)}"
if [[ ! -f "$DUMP" ]]; then
  echo "test_db_refresh.sh: no dump found (pass one as the 2nd argument)" >&2
  exit 2
fi
PG=(-h localhost -p 5432 -U postgres)
"$PGBIN/dropdb" "${PG[@]}" --if-exists --force "$TARGET"
"$PGBIN/createdb" "${PG[@]}" "$TARGET"
"$PGBIN/pg_restore" "${PG[@]}" --no-owner --no-acl --exit-on-error -d "$TARGET" "$DUMP"
# Bring the clone to the repo's schema head (audit B-17): the canonical DDL ledger.
(cd "$ROOT" && python3 -m heimdal.canonical_migrations --apply \
  --dsn "postgresql://postgres@localhost:5432/$TARGET" >/dev/null)
echo "test clone $TARGET restored from $DUMP, canonical ledger applied"
