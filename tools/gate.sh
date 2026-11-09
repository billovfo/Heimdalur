#!/usr/bin/env bash
# Heimdal gate: the one "definition of done" command (audit 2026-11 W1d; H-3, H-11).
#   ./tools/gate.sh
# a  offline tests    every DSN on a dead port, so no DB can be reached
# b  isolated tests   throwaway $HEIMDAL_TEST_DB only: tools/test_env.sh if present,
#                     else an inline test env (SKIP when postgres is unreachable)
#    clone tests      every other root test_*.py, on the heimdal clone in
#                     HEIMDAL_READ_DB (tools/test_db_refresh.sh); projection tests
#                     skip unless the projection DSN holds the baseline run
#    acceptance       acceptance/*.py __main__ runners (H-2); browser layer only
#                     with HEIMDAL_ACCEPTANCE_URL, projection-bound ones only on the baseline
# c  tools/phase*_test_*.py scripts (pure functions, no DB)
# d  audit checks     names in tools/audit/GREEN must pass; the rest are report-only
# Files run one at a time (keeps the CPU load low). Exit 1 on any FAIL.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1
# Real register PDFs live in the evidence store, outside git (audit 2026-11 I-5)
export HEIMDAL_FIXTURES_DIR="${HEIMDAL_FIXTURES_DIR:-$HOME/.local/share/heimdal/evidence/2026-11-audit/fixtures/pdfhist}"
PYTEST=(python3 -m pytest -p no:cacheprovider -q)
# Inline (public snapshot): list every test-level skip with its reason.
[ "${HEIMDAL_GATE_INLINE:-0}" = 1 ] && PYTEST+=(-rs)

OFFLINE_TESTS=(  # H report class "offline": every test passes with no DB reachable
  test_pathfinder_bfs.py test_input_bounds.py test_export_public.py
  test_compliance_module.py test_dashboard_module.py test_dossier_module.py
  test_entities_module.py test_export_module.py test_geo_module.py test_graph_module.py
  test_heimdal_collect.py test_pdfhist_c1_replay.py test_pdfhist_parsers.py
  test_pdfhist_rd1_pagination.py test_pdfhist_temporal.py test_scaffolding.py
  test_screening_module.py test_search_module.py test_snapshots_module.py
  test_pep_identity_link.py
  test_watchlist_module.py test_export_geo_snapshot_safety.py
  test_ownership_view_models.py test_phase2_ownership_endpoints.py test_pdfhist_audit_finalize.py
  test_pdfhist_lysing_d06.py test_collector_http.py test_pdfhist_extract_limits.py
  test_j2_computed_verdicts.py test_frontend_g_findings.py test_url_map.py
  test_route_matrix.py test_query_counts.py
  test_data_protection.py test_pdfhist_x3b_notices.py
  test_frontend_quality_constellation.py test_acceptance_flags.py
  test_ci_public.py)
ISOLATED_TESTS=(  # dev_bootstrap set + the other H "isolated" files
  test_db_transaction.py test_review_atomicity.py test_migration_ledger.py
  test_person_review.py test_person_review_enhancements.py test_registry_projection.py
  test_pdfhist_ddl.py test_pdfhist_history_review.py test_pdfhist_ingest.py
  test_pdfhist_qa_readonly.py test_quality_gates.py test_ownership_cycles_clone.py
  test_pdfhist_fail_closed.py test_review_batch_e2e.py test_phase1_build_meta.py
  test_pdfhist_projection_integrity.py test_collector_db.py test_canonical_db.py
  test_pdfhist_cov_fallback.py test_pdfhist_founding_origin.py test_pdfhist_reparse_pin.py
  test_pdfhist_x3b_notices_projection.py test_pdfhist_raddr_role_event.py)
CLONE_ONLY_TESTS=(  # inline env (public snapshot): no clone exists, so these skip
  test_ownership_cycles_clone.py test_phase1_build_meta.py)
INLINE_FIXTURE_TESTS=(  # inline env: loads a real-PDF fixture at import, so it skips
  test_pdfhist_ingest.py)
REPORT_ONLY_TESTS=()  # known-red files, owned elsewhere (empty: W5d fixed H-7's geo asserts)
PHASE_SCRIPTS=(  # __main__ scripts over pure functions of phase2/3_build.py (H-11)
  tools/phase2_test_pin.py tools/phase2_test_units.py tools/phase3_test_closure.py
  tools/phase3_test_groups.py tools/phase3_test_pairs.py
  # standalone __main__ (no pytest wrapper): kept here, not in OFFLINE_TESTS, so it
  # runs with every DSN dead after importing the live phase2/3_verify modules (X4 review #3)
  tools/phase2_test_verify_pin.py)
PHASE_INLINE_SKIP=(  # inline env (public snapshot): reads an export-ignored artifact
  tools/phase2_test_pin.py)
DSN_VARS=(HEIMDAL_READ_DB HEIMDAL_QA_DB HEIMDAL_DB HEIMDAL_PG_DSN HEIMDAL_PDFHIST_PROJECTION_DSN
  HEIMDAL_PDFHIST_DB HEIMDAL_PDFHIST_TEST_DB HEIMDAL_TEST_ADMIN_DSN HEIMDAL_DATABASE_URL)
DEAD=postgresql://gate@127.0.0.1:1/none
OFFLINE=(env PGHOST=127.0.0.1 PGPORT=1 PGPASSFILE=/nonexistent/pgpass)
for v in "${DSN_VARS[@]}"; do OFFLINE+=("$v=$DEAD"); done

LOG="$(mktemp -d)" ROWS=() FAILED=0 TESTDB=off
declare -A N
cleanup() {
  rm -rf "$LOG"
  [ "$TESTDB" = on ] && python3 -c 'import os, psycopg
psycopg.connect(os.environ["HEIMDAL_TEST_ADMIN_DSN"], autocommit=True).execute(
    "DROP DATABASE IF EXISTS %s WITH (FORCE)" % os.environ["HEIMDAL_TEST_DB"])'
}
trap cleanup EXIT

record() {  # step name status detail
  ROWS+=("$(printf '%-2s %-5s %-34s %s' "$1" "$3" "$2" "$4")")
  printf '%s\n' "${ROWS[-1]}"
  N[$3]=$(( ${N[$3]:-0} + 1 ))
  [ "$3" = FAIL ] && FAILED=1
  return 0
}

run() {  # step name gate|report cmd...   (report: never fails the gate)
  local step=$1 name=$2 mode=$3 out="$LOG/${#ROWS[@]}" st; shift 3
  if "$@" >"$out" 2>&1; then st=PASS; else st=FAIL; fi
  [ "$mode" = report ] && { [ $st = PASS ] && st=green || st=red; }
  record "$step" "$name" "$st" "$(grep -Ev '^[[:space:]^~]*$|^LINE [0-9]+:' "$out" | tail -n 1 | cut -c1-100)"
  if [ "${HEIMDAL_GATE_INLINE:-0}" = 1 ]; then
    # Surface test-level (pytest -rs) and check-internal SKIP lines in the job output.
    grep -E '^(SKIP|SKIPPED)' "$out" | sed 's/^/      | /'
  fi
  [ "$st" = FAIL ] && tail -n 20 "$out" | sed 's/^/      | /'
  return 0
}

dbname() {  # dbname of a URI or key=value DSN
  local d=$1
  if [[ $d == *dbname=* ]]; then d=${d#*dbname=}; d=${d%% *}; else d=${d%%\?*}; d=${d##*/}; fi
  printf %s "$d"
}

live_guard() {  # fail closed: the test env must not name a live DB anywhere
  [[ ${HEIMDAL_TEST_DB-} =~ ^[a-z_]*_test[a-z0-9_]*$ ]] \
    || { echo "HEIMDAL_TEST_DB='${HEIMDAL_TEST_DB-}' is not a *_test* name"; return 1; }
  local v
  for v in HEIMDAL_READ_DB HEIMDAL_DB; do  # unset = the code's live default
    [ -n "${!v-}" ] || { echo "$v is unset (code defaults it to live heimdal)"; return 1; }
  done
  for v in "${DSN_VARS[@]}"; do
    [ -n "${!v-}" ] || continue
    [[ $(dbname "${!v}") =~ ^(heimdal|pdfhist_pilot|.*_recovered_.*)$ ]] \
      && { echo "$v points at live DB '$(dbname "${!v}")'"; return 1; }
  done
  return 0
}

echo "== a: offline tests (all DSNs dead)"
[ -d "$HEIMDAL_FIXTURES_DIR" ] \
  || record a "real-PDF fixtures" SKIP "HEIMDAL_FIXTURES_DIR absent: the fixture tests skip"
for f in "${OFFLINE_TESTS[@]}"; do run a "$f" gate "${OFFLINE[@]}" "${PYTEST[@]}" "$f"; done

echo "== b: isolated tests"
if [ -f tools/test_env.sh ] && [ "${HEIMDAL_GATE_INLINE:-0}" != 1 ]; then
  ENV_SRC=tools/test_env.sh
  # shellcheck disable=SC1091
  source tools/test_env.sh || record b "source tools/test_env.sh" FAIL "non-zero exit"
else
  ENV_SRC="inline test env"
  export HEIMDAL_TEST_DB="${HEIMDAL_TEST_DB:-pdfhist_test}"
  PG="${HEIMDAL_GATE_PG:-postgresql://postgres@localhost:5432}"
  export HEIMDAL_PG_DSN=$PG/postgres HEIMDAL_TEST_ADMIN_DSN=$PG/postgres \
    HEIMDAL_PDFHIST_PROJECTION_DSN=$PG/$HEIMDAL_TEST_DB HEIMDAL_PDFHIST_DB=$PG/$HEIMDAL_TEST_DB
  # No heimdal clone inline: the canonical read DSNs are dead, so QA reads skip.
  export HEIMDAL_READ_DB=$DEAD HEIMDAL_QA_DB=$DEAD HEIMDAL_DB=$DEAD HEIMDAL_DATABASE_URL=$DEAD
  unset HEIMDAL_PDFHIST_TEST_DB
  # shellcheck disable=SC1091
  source tools/dev_env.sh  # only fills PGPASSFILE: every DSN above is already set
fi
if ! msg=$(live_guard); then
  record b "live-DSN guard ($ENV_SRC)" FAIL "$msg"
elif ! python3 -c 'import os, psycopg
psycopg.connect(os.environ["HEIMDAL_TEST_ADMIN_DSN"], connect_timeout=3).close()' 2>/dev/null; then
  if [ -f tools/test_env.sh ]; then record b "postgres" FAIL "unreachable via HEIMDAL_TEST_ADMIN_DSN"
  else record b "isolated tests" SKIP "no test env (postgres unreachable)"; fi
else
  TESTDB=on
  echo "   ($ENV_SRC, HEIMDAL_TEST_DB=$HEIMDAL_TEST_DB)"
  for f in "${ISOLATED_TESTS[@]}"; do
    if [ "${HEIMDAL_GATE_INLINE:-0}" = 1 ] \
       && [[ " ${CLONE_ONLY_TESTS[*]} " == *" $f "* ]]; then
      record b "$f" SKIP "needs the heimdal clone (not in the public snapshot)"
      continue
    fi
    if [ "${HEIMDAL_GATE_INLINE:-0}" = 1 ] \
       && [[ " ${INLINE_FIXTURE_TESTS[*]} " == *" $f "* ]]; then
      record b "$f" SKIP "needs the real-PDF fixtures (not in the public snapshot)"
      continue
    fi
    run b "$f" gate "${PYTEST[@]}" "$f"
  done
  if [ "$HEIMDAL_READ_DB" = "$DEAD" ]; then
    record b "clone tests" SKIP "no heimdal clone in the inline test env"
  elif ! python3 -c 'import os, psycopg
psycopg.connect(os.environ["HEIMDAL_READ_DB"], connect_timeout=3).close()' 2>/dev/null; then
    record b "clone tests" FAIL "no heimdal clone in HEIMDAL_READ_DB (tools/test_db_refresh.sh)"
  else
    listed=" ${OFFLINE_TESTS[*]} ${ISOLATED_TESTS[*]} "
    for f in test_*.py; do  # the H "live" classes, now on the clone (H-6)
      [[ $listed == *" $f "* ]] && continue
      mode=gate; [[ " ${REPORT_ONLY_TESTS[*]} " == *" $f "* ]] && mode=report
      run b "$f" "$mode" "${PYTEST[@]}" "$f"
    done
    run b "acceptance/frontend_quality" gate python3 -m acceptance.frontend_quality
    run b "acceptance/phase5_graph_ux" gate python3 -m acceptance.phase5_graph_ux
    if python3 tests_baseline.py; then  # needs the baseline projection run
      run b "acceptance/owner_acceptance_ux" gate python3 -m acceptance.owner_acceptance_ux
    else
      record b "acceptance/owner_acceptance_ux" SKIP "projection DB is not the baseline run (tests_baseline.py)"
    fi
  fi
fi

echo "== c: tools/phase*_test_*.py"
for f in "${PHASE_SCRIPTS[@]}"; do
  if [ "${HEIMDAL_GATE_INLINE:-0}" = 1 ] \
     && [[ " ${PHASE_INLINE_SKIP[*]} " == *" $f "* ]]; then
    record c "$f" SKIP "needs docs/phase0_scale_measurement.json (export-ignored)"
    continue
  fi
  run c "$f" gate "${OFFLINE[@]}" python3 "$f"
done

echo "== d: audit checks (GREEN must pass; others report-only)"
mapfile -t GREEN < <(sed 's/#.*//; s/[[:space:]]//g; s/\.py$//' tools/audit/GREEN 2>/dev/null | grep .)
audit() {  # name gate|report
  case $1 in
    check_b|check_c|check_f)  # read-only SELECTs on HEIMDAL_READ_DB
      if [ "$TESTDB" = on ] && [ "$HEIMDAL_READ_DB" != "$DEAD" ]; then run d "$1" "$2" python3 "tools/audit/$1.py"
      else record d "$1" SKIP "needs a heimdal clone in HEIMDAL_READ_DB (tools/test_env.sh)"; fi ;;
    check_e)  # creates and drops its own review_e_check DB
      if [ "$TESTDB" = on ]; then run d "$1" "$2" python3 "tools/audit/$1.py"
      else record d "$1" SKIP "needs postgres (test env)"; fi ;;
    *) run d "$1" "$2" "${OFFLINE[@]}" python3 "tools/audit/$1.py" ;;  # static/in-process
  esac
}
for n in "${GREEN[@]}"; do
  if [ -f "tools/audit/$n.py" ]; then audit "$n" gate; else record d "$n" FAIL "in GREEN, no such check"; fi
done
for f in tools/audit/check_*.py; do
  n=$(basename "$f" .py)
  [[ " ${GREEN[*]} " == *" $n "* ]] || audit "$n" report
done

echo
echo "== gate summary ($ENV_SRC)"
printf '%s\n' "${ROWS[@]}"
echo "PASS=${N[PASS]:-0} FAIL=${N[FAIL]:-0} SKIP=${N[SKIP]:-0} | report-only: green=${N[green]:-0} red=${N[red]:-0}"
if [ "$FAILED" = 0 ]; then echo "GATE: PASS"; else echo "GATE: FAIL"; fi
exit "$FAILED"
