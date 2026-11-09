# HEIMDAL STATUS (canonical current-status source)

> Verified 2026-11-09 (decision wave X5) with `source tools/dev_env.sh && python3 tools/status_check.py`.
> If this page disagrees with a fresh probe run, **the page is STALE**. Update it; do not argue.
> Older docs that state another ACTIVE run, build or decision count are historical. This page
> overrules them. Stale docs carry a HISTORICAL banner pointing here.

| Fact | Value (2026-11-09) |
|---|---|
| Dev DBs | `heimdal_recovered_20260904` (canonical) and `pdfhist_pilot_recovered_20260904` (PDF history/projection): the 2026-09-04 state, recovered from the old `heimdal-qa` container (W2, owner D1/D2). Both use collation en_US.utf8 |
| Fallback DBs | `heimdal` (the 2026-08-03 restore, public-only) and `pdfhist_pilot` (empty). Untouched, still backed up |
| Schema | Canonical DDL ledger `sql/migrations/0001–0008` APPLIED (`heimdal/canonical_migrations.py`). Pilot ledger: projection, review and review-immutability APPLIED |
| PDF projection | run **34 ACTIVE** (X3, owner G1 2026-11-08), content hash `f860e165…`, parser v2 pinned. Run 33 is RETIRED and can be rolled back; caveat: it holds the 81 wrong COV links, which the flags-on portal would show. COV-HISTORICAL is off (stale frozen map). Diff: `docs/audit-2026-11/waves/X3-run-diff.md` |
| PDF corpus | 16,400 documents, sha256-verified. 16,449 parses at parser v1 (stored) and v2 (offline re-parse in X3); 22 notices went PARSED→REVIEW |
| PDF history flags | **ON by default** (owner G2, 2026-11-08): the four flags (PDF history integration/UI, analytics, analytics graph) default to `true` in `tools/dev_env.sh`; env can turn them off. `tools/route_matrix.py` against a flags-on portal: 0×5xx (45 routes) |
| Analytics | Build **15 ACTIVE** (X4, 2026-11-08), pinned to projection run 34; build 13 SUPERSEDED. phase2_verify 22/22, phase3_verify 17/17, check_c OK. Flags ON (G2). The Phase 0 memory limit is 1300 MB (owner X4) |
| Owner review decisions | **12 recovered**: the 5 APPROVE_LINK were made outside the shown candidates and are retired as STALE `OFF-LIST-D15` (D15, decision E1, 2026-11-08); the 7 DEFER stay PENDING. The batch path is fixed (W4a) |
| PEP | Empty: no name-matched records; it waits for a sourced import with an identity link (D14) |
| Portal DB role | `heimdal_reader`: read-only (`default_transaction_read_only=on`, SELECT only). Writes go through `tx()` with `HEIMDAL_PG_DSN`. Passwords only in `tools/.dev-pgpass` (0600, git-ignored) |
| Network | 127.0.0.1 only (TRUSTED_HOSTS + loopback guard). Same-origin POSTs only. CSP without inline scripts. OSM tiles are the only external request |
| Dev postgres | 127.0.0.1:5432, cluster `~/.local/share/heimdal/pg/`, `template1` en_US.utf8, scram-sha-256 |
| Last backup | 2026-11-09 (`backups/LATEST.json`, all 4 DBs, 20261109T004317); restore points `backups/pre-X0-2026-11-08/` and `backups/pre-X3-2026-11-08/`. Restore drill (2026-11-09): both recovered DBs restored from the newest dumps, identical row counts in every table (35 + 19 tables). Source dumps of the recovery stay in `backups/recovered-20260904/` |
| Real-data evidence | `~/.local/share/heimdal/evidence/2026-11-audit/` (outside git, 0700, MANIFEST.sha256). The register-PDF fixtures live there; tests read them through `HEIMDAL_FIXTURES_DIR` |
| Tests | `source tools/test_env.sh`; conftest.py refuses live and recovered DSNs. Definition of done: `./tools/gate.sh` → PASS 126 / FAIL 0 / SKIP 1 (2026-11-08). Baseline: run 34, analytics build 15 (`tests_baseline.py`) |
| Audit checks | Green and gated: a, b, c, d, e, f, g, h, j (c added in X4) |
| Off-machine backup | Deferred by the owner (2026-11-05): "when Heimdal is fully developed" |
| SOT | v2.5 **APPROVED** by the owner 2026-11-09 (G2b): §0.8 records the X0–X5 result, §29d the decisions. `implementation_authorized: true` for the built scope |
| Collector runs | 46 COMPLETED, 6 STOPPED. The stale RUNNING runs 7, 8, 22, 23 were closed 2026-11-08 (E7) |
| Decision plan | `docs/audit-2026-11/04-DECISION-PLAN.md` rev 2 plus addenda: X0–X4 are done; X5 items 1–6 are done (route matrix lane, portal check, G2, flags on, SOT v2.5 G2b, backup + drill + gate). Next: X5.7 final review, X5.8 Heimdalur (needs OK), X6 (G3), X7 |
