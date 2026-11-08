"""Reviewer session / fatigue guard logic (E4, post-MVP).

Server side holds NO session state (no auth, no tracking): this module is a
pure threshold helper shared by tests and documentation. Live counting lives
in static/review.js sessionStorage and NEVER affects queue, scores, cases,
decisions, or application. Prompts are soft-only; no hard stop exists.
"""
from __future__ import annotations

SOFT_PROMPT_AFTER_DECISIONS = 40
SOFT_PROMPT_AFTER_AMBIGUOUS_HEAVY = 25


def fatigue_notice(reviewed: int, ambiguous_heavy: int) -> str | None:
    """Return a soft prompt message, or None when below thresholds."""
    if ambiguous_heavy >= SOFT_PROMPT_AFTER_AMBIGUOUS_HEAVY:
        return ("You have reviewed %d ambiguous-heavy cases in this session. "
                "Consider starting a fresh review session if decisions are "
                "becoming slower." % ambiguous_heavy)
    if reviewed >= SOFT_PROMPT_AFTER_DECISIONS:
        return ("You have reviewed %d cases in this session. "
                "Consider starting a fresh review session if decisions are "
                "becoming slower." % reviewed)
    return None
