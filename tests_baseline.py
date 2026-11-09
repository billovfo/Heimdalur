"""The data baseline the DB tests are pinned to (audit 2026-11 H-5/H-6).

One place for the counts, hashes and pins that only hold for one snapshot. Tests
prefer invariants (sums, idempotence, before == after); what is left here is
what the test intent needs as an absolute anchor.

Measured 2026-11-06 on the recovered 2026-09-04 dev state, and re-measured 2026-11-08
after X3 activated run 34 on these restores:
  heimdal clone     tools/test_db_refresh.sh <clone>
                    backups/heimdal_recovered_20260904_20261108T220320.dump
                    sha256 80387cd8812c327385a596a191798c5438bb9374ec30b798c9c8b1c18c961b17
                    (after the X4 analytics builds 14/15; canonical identity unchanged)
  projection clone  createdb -T pdfhist_pilot_recovered_20260904 <name>_proj
                    (= backups/pdfhist_pilot_recovered_20260904_20261108T220320.dump,
                    sha256 f15f80266e758d39d1f2b4ad4dbf5ac6815b3930e80722df1fd695c755179c5e)
                    then HEIMDAL_PDFHIST_PROJECTION_DSN=HEIMDAL_PDFHIST_DB=…/<name>_proj.
                    Without it the projection tests skip (conftest.active_projection).
A new dump that changes these numbers is a new baseline: re-measure, update
here in one commit, and name the dump above.
"""

# registry_projection: the ACTIVE run of the recovered pilot. Run 34 (decision plan X3,
# owner G1 2026-11-08) replaced run 33 (fix plan W2, owner D7).
PROJECTION_RUN = 34
PROJECTION_HASH = "f860e16594edfdd6094ffa0b8422e2456b95c55e58c32709fd5412e4d3a428f5"

# registry_analytics: ACTIVE build 15 (phase3, after phase2 build 14), pinned to projection
# run 34 (decision plan X4, 2026-11-08); build 13 (pinned to run 4) is SUPERSEDED.
# Counts are WHERE build_run_id = 15; junction is the whole
# person_group_observation_member table (all builds).
ANALYTICS_BUILD = 15
ANALYTICS_HASH = "2cccb5711342603a50b59cc6f944803c"
ANALYTICS_COUNTS = {"obs": 27665, "periods": 8307, "pairs": 15913, "groups": 6348,
                    "gobs": 18107, "junction": 141375}
# Build 15's own coverage metric (coverage->>'unresolved'): historical person
# events that could not be resolved to canonical persons. Measured on the
# heimdal_test clone above; the analytics pages surface it as a limitation.
ANALYTICS_UNRESOLVED_HISTORICAL = 4902

# canonical identity (WP-C1, frozen input set): the get-table bodies name
# OBSERVED_ENTITIES distinct endpoints; canonical_entity adds the inventory-only
# companies that are never a relation endpoint.
OBSERVED_ENTITIES = 9801
CANONICAL_ROWS = 9806
PERSON_ROWS = 5083
NATURAL_PERSON_ROWS = 49  # raw_type_token 'Natural Person'; the rest is 'Persónur'
COLLISION_GROUPS = 169
COLLISION_ENTITIES = 356


def active_projection_run(dsn):
    """(run id, content hash) of the ACTIVE run in the projection DB, or None.

    The projection tests (conftest.baseline_projection) and the gate's
    projection-dependent acceptance step run only when this is the baseline."""
    import psycopg
    if not dsn:
        return None
    try:
        with psycopg.connect(dsn, connect_timeout=3) as c:
            return c.execute(
                "SELECT projection_run_id, content_hash FROM registry_projection.projection_run"
                " WHERE status = 'ACTIVE'").fetchone()
    except psycopg.Error:
        return None


if __name__ == "__main__":  # exit 0 iff $HEIMDAL_PDFHIST_PROJECTION_DSN is the baseline
    import os
    import sys
    sys.exit(active_projection_run(os.environ.get("HEIMDAL_PDFHIST_PROJECTION_DSN"))
             != (PROJECTION_RUN, PROJECTION_HASH))
