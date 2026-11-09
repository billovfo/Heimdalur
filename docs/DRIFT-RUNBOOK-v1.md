# HEIMDAL DRIFT RUNBOOK v1 (canonical — P2.1)

Single operations page. If it is not here, it is not canonical.

## 1. Dev-Postgres start/stop

```bash
# start (foreground postmaster as a managed background job)
~/.local/share/heimdal/pg/server/postgresql-18.4.0-x86_64-unknown-linux-gnu/bin/postgres \
  -D ~/.local/share/heimdal/pg/data -c listen_addresses=127.0.0.1 -p 5432
# stop: terminate that job/process, then verify
ss -ltn | grep 5432 || echo "postgres stopped"
```

Cluster home (since 2026-11-05): `~/.local/share/heimdal/pg/` — `data/`
and `server/` (binaries). Moved out of the repo's `tmp-pg/`, which matched the
git-ignored `/tmp-*` scratch pattern. Locale: `template1` is `en_US.utf8` (since
2026-11-06), so every new DB, including test clones, matches the original cluster. A `C` locale
breaks lower() for Faroese letters and the run-33 content hash. Databases: dev
`heimdal_recovered_20260904` + `pdfhist_pilot_recovered_20260904` (since 2026-11-06, see
`docs/STATUS.md`), fallbacks `heimdal` + `pdfhist_pilot`, and `*_test*` names for tests only
(user `postgres`; password held only in `tools/.dev-pgpass`,
mode 0600, git-ignored — never written here or anywhere else).
Cluster or data missing? Run `podman ps -a` and `podman volume ls` before you call it lost, and
never `podman start` an old heimdal container (audit 2026-11 R-8, JI-1).

## 2. Application launch + flag matrix

```bash
source tools/dev_env.sh
python3 -m flask --app heimdal.app run --host 127.0.0.1 --port 8408 --no-reload
```

| Flag | Effect | Default |
|---|---|---|
| `HEIMDAL_REVIEW_UI=1` | review routes live | unset = 404 everywhere |
| `PDF_HISTORY_INTEGRATION_ENABLED=true` + `PDF_HISTORY_UI_ENABLED=true` | PDF-history data + UI surface | off (pinned `false` in `tools/dev_env.sh`) |
| `REGISTRY_ANALYTICS_ENABLED=1` (+`_GRAPH_ENABLED`) | analytics sections | off (pinned `false`; parked, owner D6) |
| `HEIMDAL_READ_DB` / `HEIMDAL_PDFHIST_PROJECTION_DSN` | read/projection DSNs | fail-closed (no password defaults) |

All binds are 127.0.0.1; debug off. Never bind 0.0.0.0 without the auth task. The app also
refuses non-loopback clients itself (403, `heimdal/modules/shared/web.py`).

## 3. DSN/config/secrets

One reader: `tools/dev_env.sh` (dev defaults) + `heimdal/config.py` /
`heimdal/pdfhist/db.py` (single helpers). Prod paths require env (raise when
unset). DB password: `PGPASSWORD`/`PGPASSFILE`, never in files. Rotate by
changing the role password and updating the secret holder only.

## 4. Backup / restore

Automatic: cron line below (install with `crontab -e` where permitted).

```cron
17 3 * * 0 cd <repo> && ./tools/pg_backup.sh >> ~/.local/share/heimdal/pg/backup.log 2>&1
```

Manual: `./tools/pg_backup.sh [--dry-run]`. Retention: newest 4 per DB
(auto-pruned). Age observable: `backups/LATEST.json` (STATUS.md surfaces it;
stale beyond 8 days warns). Recovery: pick a dump, `createdb scratch`,
`pg_restore -d scratch`, verify counts, promote. Drill quarterly, log the date
in STATUS.md.

## 5. Migrations

```bash
python3 -m heimdal.pdfhist.migrations --status --dsn "$HEIMDAL_PDFHIST_PROJECTION_DSN"
python3 -m heimdal.pdfhist.migrations --apply --dsn ...     # pending only
python3 -m heimdal.pdfhist.migrations --baseline --dsn ...  # empty ledger only
```

Immutable history: mismatch = hard failure, fix = new file. Never re-run changed.

## 6. Tests

```bash
./tools/dev_bootstrap.sh   # provisions test DB, migrates, runs standard suites
```

Needs: postgres up (see §1) and a test clone (`tools/test_db_refresh.sh`). The script sources
`tools/test_env.sh`, never `tools/dev_env.sh`. Definition of done: `./tools/gate.sh`.
`pytest.ini` already neutralises the broken langsmith plugin.

## 7. Shutdown checklist

1. Stop app server job → `curl` the port shows closed.
2. `pg_ctl stop` / terminate postmaster job → 5432 closed.
3. `git status` shows no unintended writes; decisions stay PENDING unless an
   explicit apply task ran.

## 8. Ports

8408 review dev server · 5432 dev postgres.
Nothing listens on 8406/8407/8410 unless a QA run started it.

## 9. External requests (owner D12, audit G-09)

The portal makes one kind of external request: OpenStreetMap map tiles on Geo-Kort
(`/geo-kort`), from `https://tile.openstreetmap.org` (`heimdal/config.py`). The owner accepted
this on 2026-11-06 as a documented external dependency, under the OSM tile usage policy.

- Attribution "© OpenStreetMap contributors", linked to the OSM copyright page, is shown on
  the map (`static/geo_map.js`).
- Referrer: the app sends `Referrer-Policy: same-origin`, so its pages send no Referer to other
  hosts. Geo-Kort alone sets `<meta name="referrer" content="origin">`, because the tile policy
  asks for a valid Referer. Tile requests therefore carry only `http://127.0.0.1:<port>/`, never
  a path or query.
- The CSP allows images from `tile.openstreetmap.org` and from no other host.
- Each Geo-Kort view sends the user's IP address to the OSM tile servers (12–16 tiles, no
  prefetch). The map data itself is local.
- An offline basemap would remove this request. It is not planned.

## 10. Public CI (Heimdalur)

GitHub Actions runs only in the public snapshot, Heimdalur: the repo is public,
Actions is enabled there, and the run is free. **The private repo has Actions disabled**, so
no workflow may run there.

`.github/workflows/ci.yml` starts an `ubuntu-latest` job with Python 3.14 and a `postgres:18`
service, installs `requirements.txt`, and calls `bash tools/ci_public.sh`. The job carries a
job-level `if:` that names the public repository exactly, so it cannot run in a private fork
even if Actions is enabled there. That script runs the public subset of `tools/gate.sh` in its
inline test env: offline tests, the phase scripts, the isolated tests that build their own DB
from the repo migrations, and the audit checks that need only the repo and that DB. It never
needs the fixtures, a heimdal clone, a dump, the baseline projection or anything under an
export-ignored path; those parts SKIP with a reason, and the run prints every gate SKIP row,
every pytest-level skip (`-rs`) and every check-internal `SKIP ...` line.

Credentials come from the environment only: `PGHOST`/`PGPASSWORD`, or one
`HEIMDAL_CI_PG=postgresql://user:<pw>@host:port/db` (only the authority is used; the database
path and query are ignored). The script never reads a passfile. The gate's test DB ends in
`_ci`; auxiliary DBs (`review_e_check_test`, `<test>_raw`) are dropped by their checks/tests.
To check a local export: `PGHOST=127.0.0.1 PGPASSWORD=<pw> tools/ci_public.sh` from the export
directory. A raw `tools/export_public.sh` snapshot has no `.git`, so the script first
initialises a throwaway repo inside that directory: `check_j` and `test_export_public.py` need
git metadata.

