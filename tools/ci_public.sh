#!/usr/bin/env bash
# Public CI entry point (Heimdalur): the part of tools/gate.sh that runs from the
# public snapshot alone, plus a postgres:18 service container (or a local server).
#
#   PGHOST=127.0.0.1 PGPASSWORD=<pw> tools/ci_public.sh
#   HEIMDAL_CI_PG=postgresql://postgres:<pw>@127.0.0.1:5432/postgres tools/ci_public.sh
#
# The snapshot has no real-PDF fixtures, no heimdal clone, no dump and no
# baseline projection. Those parts SKIP with a reason; the gate prints every
# SKIP row and this script repeats the list. Exit code: non-zero on any failure.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# check_j and test_export_public.py read git metadata, but a raw export has no
# .git. Create a throwaway repo inside the snapshot only (never a parent repo).
if [ ! -e "$ROOT/.git" ]; then
  echo "== public CI: initialising a throwaway git repo (the snapshot has no .git)"
  git -C "$ROOT" init -q
  git -C "$ROOT" add -A
  git -C "$ROOT" -c user.name='public-ci' -c user.email='public-ci@localhost' \
    commit -q -m 'public snapshot'
fi

# Credentials come from the environment only: never a passfile in the tree.
export PGPASSFILE=/nonexistent/heimdal-ci-no-pgpass

# HEIMDAL_CI_PG: only the authority (host, port, user, password) is used. The
# database path and query are ignored because the script creates its own scratch DB.

if [ -n "${HEIMDAL_CI_PG:-}" ]; then
  parsed=$(python3 - "$HEIMDAL_CI_PG" <<'PY'
import shlex
import sys
from urllib.parse import quote, unquote, urlsplit

u = urlsplit(sys.argv[1])
if u.scheme not in ("postgres", "postgresql") or not u.hostname:
    raise SystemExit(2)
host = u.hostname
port = u.port or 5432
user = unquote(u.username) if u.username else "postgres"
pw = unquote(u.password) if u.password else ""
base = "postgresql://" + quote(user, safe="")
if pw:
    base += ":" + quote(pw, safe="")
base += "@" + host + ":" + str(port)
print("export PGHOST=" + shlex.quote(host))
print("export PGPORT=" + shlex.quote(str(port)))
print("export PGUSER=" + shlex.quote(user))
if pw:
    print("export PGPASSWORD=" + shlex.quote(pw))
print("export HEIMDAL_GATE_PG=" + shlex.quote(base))
PY
) || { echo "ci_public: HEIMDAL_CI_PG is not a postgresql URI" >&2; exit 2; }
  eval "$parsed"
fi

: "${PGHOST:=127.0.0.1}"
: "${PGPORT:=5432}"
: "${PGUSER:=postgres}"
: "${HEIMDAL_GATE_PG:=postgresql://${PGUSER}@${PGHOST}:${PGPORT}}"
export PGHOST PGPORT PGUSER HEIMDAL_GATE_PG

CI_DB="${HEIMDAL_TEST_DB:-pdfhist_test_ci}"
if [[ ! "$CI_DB" =~ ^[a-z_]*_test[a-z0-9_]*_ci$ ]]; then
  echo "ci_public: HEIMDAL_TEST_DB must match ^[a-z_]*_test[a-z0-9_]*_ci\$ (got '$CI_DB')" >&2
  exit 2
fi
export HEIMDAL_TEST_DB="$CI_DB"

# The public snapshot never ships the real register PDFs: point the fixture
# loader at a path that does not exist, so those tests skip and say why.
export HEIMDAL_FIXTURES_DIR="$ROOT/.ci-no-fixtures"
if [ -e "$HEIMDAL_FIXTURES_DIR" ]; then
  echo "ci_public: refusing: $HEIMDAL_FIXTURES_DIR exists, so this is not a clean snapshot" >&2
  exit 2
fi

# Same feature-flag baseline as tools/test_env.sh (the private gate).
export PDF_HISTORY_INTEGRATION_ENABLED=false
export PDF_HISTORY_UI_ENABLED=false
export REGISTRY_ANALYTICS_ENABLED=false
export REGISTRY_ANALYTICS_GRAPH_ENABLED=false

export CI_ADMIN_DSN="postgresql://${PGUSER}@${PGHOST}:${PGPORT}/postgres"
export PYTHONDONTWRITEBYTECODE=1

# A fresh cluster (the postgres:18 service container) has no heimdal_reader
# role, but schema.sql and the migration ledger grant to it. Create the roles
# the tests and migrations expect, idempotently, before the gate.
# HEIMDAL_CI_EXTRA_ROLES exists only for the local proof: it adds LOGIN roles
# with no privileges, so a role that is absent here can exercise the same SQL.
ensure_roles() {
  python3 - <<'PY'
import os

import psycopg
from psycopg import sql

roles = ["heimdal_reader"] + os.environ.get("HEIMDAL_CI_EXTRA_ROLES", "").split()
with psycopg.connect(os.environ["CI_ADMIN_DSN"], autocommit=True) as conn:
    for role in roles:
        conn.execute(sql.SQL("""
DO $do$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {role}) THEN
    EXECUTE format('CREATE ROLE %I LOGIN', {role});
  END IF;
END
$do$
""").format(role=sql.Literal(role)))
PY
}

drop_test_db() {
  python3 - "$HEIMDAL_TEST_DB" <<'PY' >/dev/null 2>&1 || true
import os
import sys

import psycopg

name = sys.argv[1]
if not name.endswith("_ci") or "_test" not in name:
    raise SystemExit(0)
with psycopg.connect(os.environ["CI_ADMIN_DSN"], autocommit=True) as conn:
    conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                 " WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
    conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
PY
}

log=$(mktemp -t heimdal-ci.XXXXXX)
cleanup() {
  rm -f "$log"
  drop_test_db
}
trap cleanup EXIT

tries=0
until python3 -c 'import os, psycopg
psycopg.connect(os.environ["CI_ADMIN_DSN"], connect_timeout=3).close()' 2>/dev/null; do
  tries=$((tries + 1))
  if [ "$tries" -ge 30 ]; then
    echo "ci_public: postgres is not reachable at ${PGHOST}:${PGPORT} as ${PGUSER}" >&2
    exit 2
  fi
  sleep 2
done

ensure_roles

echo "== public CI (public snapshot)"
echo "   postgres: ${PGUSER}@${PGHOST}:${PGPORT}"
echo "   roles:    heimdal_reader (created if missing)"
echo "   test DB:  ${HEIMDAL_TEST_DB}"
echo "   not run:  clone tests, baseline projection/acceptance, real-PDF fixtures"
echo "             (none of them is in the public snapshot; each SKIP says why)"
echo

HEIMDAL_GATE_INLINE=1 tools/gate.sh 2>&1 | tee "$log"
status=${PIPESTATUS[0]}

echo
echo "== public CI skips"
echo "   gate rows, pytest -rs skip reasons and check-internal SKIP lines:"
grep -E '^[a-d]  SKIP |^      \| (SKIP|SKIPPED)' "$log" | sed 's/^/   /'
summary=$(grep -E '^PASS=[0-9]+ FAIL=[0-9]+ SKIP=[0-9]+' "$log" | tail -n 1)
[ -n "$summary" ] && echo "public CI counts: $summary"

exit "$status"
