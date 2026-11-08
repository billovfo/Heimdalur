"""The data baseline the DB tests are pinned to (audit 2026-11 H-5/H-6).

One place for the counts, hashes and pins that only hold for one snapshot. Tests
prefer invariants (sums, idempotence, before == after); what is left here is
what the test intent needs as an absolute anchor.

Measured 2026-11-06 on these restores (the 2026-09-04 dev state, recovered):
  heimdal clone     tools/test_db_refresh.sh <clone>
                    backups/heimdal_recovered_20260904_20261106T023912.dump
                    sha256 e98ad731f70a4e441bd6e1bc04ad8e7920e82e8686a0a6e74b3413214a74fced
  projection clone  createdb -T pdfhist_pilot_recovered_20260904 <name>_proj
                    (= backups/pdfhist_pilot_recovered_20260904_20261106T023912.dump,
                    sha256 e54d734792ed1e5325c3bd86c2194faed7d785ba769c5bdebc8dca23e5aa4f7a)
                    then HEIMDAL_PDFHIST_PROJECTION_DSN=HEIMDAL_PDFHIST_DB=…/<name>_proj.
                    Without it the projection tests skip (conftest.active_projection).
A new dump that changes these numbers is a new baseline: re-measure, update
here in one commit, and name the dump above.
"""

# registry_projection: the ACTIVE run of the recovered pilot (fix plan W2, owner D7).
PROJECTION_RUN = 33
PROJECTION_HASH = "f80d8f36660231f2dd54a1937635b6d972c429d2904e0f8c3f596cb1565ac131"

# registry_analytics: ACTIVE build 13 (pins projection run 4, from before the loss;
# parked by owner D6). Counts are WHERE build_run_id = 13; junction is the whole
# person_group_observation_member table.
ANALYTICS_BUILD = 13
ANALYTICS_HASH = "5ada242a6298c13e6a9efd3bc0a70f06"
ANALYTICS_COUNTS = {"obs": 12059, "periods": 3207, "pairs": 5894, "groups": 2825,
                    "gobs": 8425, "junction": 90092}

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
