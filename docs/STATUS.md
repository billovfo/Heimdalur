# HEIMDAL STATUS (canonical current-status source)

> Verified 2026-11-06 (fix wave W7) with `source tools/dev_env.sh && python3 tools/status_check.py`.
> If this page disagrees with a fresh probe run, **the page is STALE**. Update it; do not argue.
> Older docs that state another ACTIVE run, build or decision count are historical. This page
> overrules them. Stale docs carry a HISTORICAL banner pointing here.

| Fact | Value (2026-11-06) |
|---|---|
| Dev DBs | `heimdal_recovered_20260904` (canonical) and `pdfhist_pilot_recovered_20260904` (PDF history/projection): the 2026-09-04 state, recovered from the old `heimdal-qa` container (W2, owner D1/D2). Both use collation en_US.utf8 |
| Fallback DBs | `heimdal` (the 2026-08-03 restore, public-only) and `pdfhist_pilot` (empty). Untouched, still backed up |
| Schema | Canonical DDL ledger `sql/migrations/0001–0008` APPLIED (`heimdal/canonical_migrations.py`). Pilot ledger: projection, review and review-immutability APPLIED |
| PDF projection | run **33 ACTIVE**, content hash `f80d8f36…` (validate: hash matches) |
| PDF corpus | 16,400 documents, sha256-verified. The extraction limits fit all of them |
| PDF history flags | **OFF via env** (`tools/dev_env.sh`). W3a/W4d are done; switching them on is an owner choice |
| Analytics | **Parked** (owner D6): flags OFF. Build 13 is still ACTIVE in the DB and its ownership observations are wrong (C-1). A corrected build was proven on a clone; the rebuild awaits the owner |
| Owner review decisions | **12 recovered, PENDING**: 5 APPROVE_LINK, 7 DEFER. The batch path is fixed (W4a). The 5 approvals were made outside the shown candidates and need owner decision D15 before they can apply |
| PEP | Empty: no name-matched records; it waits for a sourced import with an identity link (D14) |
| Portal DB role | `heimdal_reader`: read-only (`default_transaction_read_only=on`, SELECT only). Writes go through `tx()` with `HEIMDAL_PG_DSN`. Passwords only in `tools/.dev-pgpass` (0600, git-ignored) |
| Network | 127.0.0.1 only (TRUSTED_HOSTS + loopback guard). Same-origin POSTs only. CSP without inline scripts. OSM tiles are the only external request |
| Dev postgres | 127.0.0.1:5432, cluster `~/.local/share/heimdal/pg/`, `template1` en_US.utf8, scram-sha-256 |
| Last backup | 2026-11-06 (`backups/LATEST.json`, all 4 DBs). Restore drill: identical row counts. Source dumps of the recovery stay in `backups/recovered-20260904/` |
| Real-data evidence | `~/.local/share/heimdal/evidence/2026-11-audit/` (outside git, 0700, MANIFEST.sha256). The register-PDF fixtures live there; tests read them through `HEIMDAL_FIXTURES_DIR` |
| Tests | `source tools/test_env.sh`; conftest.py refuses live and recovered DSNs. Definition of done: `./tools/gate.sh` → PASS 116 / FAIL 0 / SKIP 1 (2026-11-06) |
| Audit checks | Green and gated: a, b, d, e, f, g, h, j. Red: c (C-1, because analytics is parked) |
| Off-machine backup | Deferred by the owner (2026-11-05): "when Heimdal is fully developed" |
