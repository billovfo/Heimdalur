# Heimdal

Heimdal is a local, snapshot-based relation system. It collects current structured company data
from public Faroese registers (sg.vs.fo, and Skráseting PDFs for history), stores it locally, and
makes it searchable as clickable person, company and ownership portals. It runs on one machine,
on 127.0.0.1 only.

**Current state and numbers:** [docs/STATUS.md](docs/STATUS.md).

## Which document decides what

| Topic | Document |
|---|---|
| Product scope and rules | [SOT v2.5](docs/HEIMDAL-PRODUCT-SOURCE-OF-TRUTH.md) (approved 2026-11-09) |
| PDF-history rules | [MASTERPLAN v3.1.2](docs/HEIMDAL-PDF-HISTORIK-MASTERPLAN-v3.1.2.md) |
| Projection contract | [architecture](docs/HEIMDAL-REGISTRY-PROJECTION-ARCHITECTURE-v1.md), [consumer](docs/HEIMDAL-REGISTRY-PROJECTION-CONSUMER-v1.md) |
| Person review | [system plan](docs/HEIMDAL-PERSON-HUMAN-REVIEW-SYSTEM-PLAN-v1.md), [contract freeze](docs/HEIMDAL-HUMAN-REVIEW-PRE-P1-CONTRACT-FREEZE-v1.md) |
| Current state | [docs/STATUS.md](docs/STATUS.md), `tools/status_check.py` |
| Operations, flags, backups | [DRIFT-RUNBOOK](docs/DRIFT-RUNBOOK-v1.md) |
| Audit 2026-11 and fix plan | `docs/audit-2026-11/` (internal working notes, not in the public copy) |

A doc that starts with `HISTORICAL (audit 2026-11)` is history: its numbers are not current.
Each doc chain keeps only its latest version. Older versions are in git history.

## Quick start (dev)

Needs Python 3.14 (`python3 -m pip install -r requirements.txt`) and the Postgres 18.4 cluster
under `~/.local/share/heimdal/pg/`.

1. Start Postgres: [runbook §1](docs/DRIFT-RUNBOOK-v1.md#1-dev-postgres-startstop).
2. `source tools/dev_env.sh`
3. `python3 -m flask --app heimdal.app run --host 127.0.0.1 --port 8408 --no-reload`
4. Open <http://127.0.0.1:8408/>.

## Dev env and test env

- `tools/dev_env.sh` points every DSN at the dev DBs, which hold the recovered 2026-09-04 state.
  It pins PDF history and analytics off. Use it to run the app and read-only probes such as
  `tools/status_check.py`. Never use it for tests.
- `tools/test_env.sh` points every DSN at throwaway test DBs whose names must contain `_test`.
  `tools/test_db_refresh.sh` clones the newest dev dump into the test clone. The root
  `conftest.py` refuses live and recovered DSNs.

## Definition of done

`./tools/gate.sh` runs the offline tests, the isolated tests (test env), the phase scripts and
the audit checks listed in `tools/audit/GREEN`. Exit 0 means done. Heimdal has no CI.

## Layout

`heimdal/` portal (Flask app, `modules/`, `pdfhist/`) · `heimdal_collect.py` collector ·
`schema*.sql` · `tools/` ops, gate, phase builders, `audit/` checks · `scripts/` one-off,
unmaintained · `test_*.py` · `fixtures/` · `templates/`, `static/` · `docs/`.

## Rules

- **127.0.0.1 only.** There is no login and no auth. The app refuses non-loopback clients, but
  never bind it to another address or put it behind a proxy until auth exists. Postgres listens
  on 127.0.0.1 only.
- **Never push without the owner's OK.**
- No passwords in files. Use `PGPASSFILE` (`tools/.dev-pgpass`, git-ignored).
- No real personal data in git. Evidence stays outside the repo.
- Treat the dev DBs as read-only and run tests only in the test env.
- Never `git add -A`. Add files by name.
- Never `podman start` an old heimdal container.
- The only external request is OSM map tiles on Geo-Kort
  ([runbook §9](docs/DRIFT-RUNBOOK-v1.md#9-external-requests-owner-d12-audit-g-09)).
- Backups: `tools/pg_backup.sh`, newest in `backups/LATEST.json`. Restore drills are recorded in
  STATUS.
