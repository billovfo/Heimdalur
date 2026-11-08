"""Structured reason codes for human-review decisions (E1, post-MVP).

Decision states are UNCHANGED. This module only maps each decision to its
allowed reason_code set and validates combinations. reason_code persists in
the existing immutable decision payload column; no schema change.
"""
from __future__ import annotations

AMBIGUOUS_REASONS = (
    "MULTIPLE_PLAUSIBLE_CANDIDATES",
    "CONFLICTING_INDEPENDENT_EVIDENCE",
    "INSUFFICIENT_DISCRIMINATOR",
    "TEMPORAL_CONFLICT",
    "ROLE_CONFLICT",
    "IDENTITY_CONFLICT",
    "OTHER_AMBIGUOUS",
)

DEFER_REASONS = (
    "INSUFFICIENT_INDEPENDENT_EVIDENCE",
    "NO_CANONICAL_CANDIDATE",
    "NEEDS_EXTERNAL_EVIDENCE",
    "SOURCE_CONTEXT_INCOMPLETE",
    "WAIT_FOR_NEW_CANONICAL",
    "WAIT_FOR_NEW_SOURCE_EVIDENCE",
    "OTHER_DEFER",
)

REJECT_REASONS = (
    "NO_MATCH",
    "WRONG_PERSON",
    "WRONG_ENTITY_TYPE",
    "CONTRADICTORY_EVIDENCE",
    "OTHER_REJECT",
)

# APPROVE_LINK carries no reason; the explicit choice + evidence pin is the record.
APPROVE_REASONS = ()

ALLOWED = {
    "APPROVE_LINK": frozenset(APPROVE_REASONS),
    "REJECT": frozenset(REJECT_REASONS),
    "AMBIGUOUS": frozenset(AMBIGUOUS_REASONS),
    "DEFER": frozenset(DEFER_REASONS),
}

# Decisions that REQUIRE a reason code (empty/None rejected).
REQUIRES_REASON = frozenset({"AMBIGUOUS", "DEFER"})


def validate(decision: str, reason_code: str | None) -> str | None:
    """Return the normalized reason_code, or raise ValueError.

    - Unknown decision -> ValueError.
    - AMBIGUOUS/DEFER require a reason from their mapped set.
    - REJECT requires a reason from its set (NO_MATCH arrives via the
      explicit NO MATCH action, never typed).
    - APPROVE_LINK must not carry a reason.
    """
    allowed = ALLOWED.get(decision)
    if allowed is None:
        raise ValueError("unknown decision %r" % (decision,))
    reason = (reason_code or "").strip() or None
    if decision == "APPROVE_LINK":
        if reason is not None:
            raise ValueError("APPROVE_LINK carries no reason_code")
        return None
    if reason is None:
        if decision in REQUIRES_REASON or decision == "REJECT":
            raise ValueError("%s requires a reason_code" % decision)
        return None
    if reason not in allowed:
        raise ValueError("%r is not a valid reason for %s" % (reason, decision))
    return reason


def normalize_no_match() -> tuple[str, str]:
    """NO MATCH button mapping: decision = REJECT, reason_code = NO_MATCH."""
    return "REJECT", "NO_MATCH"
