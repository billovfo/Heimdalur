"""Module-level constants for the Heimdal portal."""
import os
from decimal import Decimal

DB_URI = os.environ.get(
    "HEIMDAL_READ_DB", "postgresql://heimdal_reader@localhost:5432/heimdal")

HUNDRED = Decimal("100")

# --- ownership direction (SoT §16.2) -----------------------------------------
INCOMING = "INCOMING"
OUTGOING = "OUTGOING"

_DIRECT_COLS = """relation_id, owner_id, owner_name, owner_kind,
                  owned_id, owned_name, owned_kind,
                  parsed_percentage, raw_percentage_label, percentage_parse_status,
                  semantic_direction_confidence, source_attempt_id"""

# --- fetch state (§10 freshness, D-8) ----------------------------------------
FETCHED_OK = "FETCHED_OK"
FETCH_FAILED = "FETCH_FAILED"
NEVER_FETCHED = "NEVER_FETCHED"

# --- pagination / caps -------------------------------------------------------
PAGE_SIZE = 60
PERSON_OWNERS_CAP = 100
NETWORK_NODE_CAP = 60

# --- rate limiting (security review 2026-08-15) ------------------------------
_RATE_LIMIT = 300
_RATE_WINDOW = 60
_RATE_EXCEEDED_TEXT = "For mange forespørgsler — prøv igen om et minut."


def openstreetmap_tile_url():
    return "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
