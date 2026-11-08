"""REGISTRY_ANALYTICS feature flag — fail-closed runtime switch.

Phase 4 portal integration gate (task B42), following the repo's flag
convention (heimdal/pdfhist/flags.py):

* REGISTRY_ANALYTICS_ENABLED - when false (default), no registry_analytics
  data may influence existing Heimdal behavior and no analytics section is
  rendered. Flag OFF -> the portal behaves exactly as before Phase 4; no DB
  rollback is needed because the readers are read-only.

Authoritative source of truth for the runtime flag: the environment
(heimdal config convention). An invalid/unparseable value resolves to False
(fail closed). The default is OFF: the integration must be explicitly
switched on in a deployment.
"""
from __future__ import annotations

import os

_ENV_ANALYTICS = "REGISTRY_ANALYTICS_ENABLED"
_ENV_ANALYTICS_GRAPH = "REGISTRY_ANALYTICS_GRAPH_ENABLED"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _parse_env(name: str) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return False  # default OFF — fail closed
    token = raw.strip().lower()
    if token in _TRUE:
        return True
    return False  # unparseable -> fail closed


def analytics_enabled() -> bool:
    """True only if the analytics switch is explicitly enabled.

    One switch covers both reading (readers are consulted by view-model
    builders only) and UI exposure: the sections are the only consumer and
    the flag is checked immediately before any analytics read.
    """
    return _parse_env(_ENV_ANALYTICS)


def ui_exposure_allowed() -> bool:
    """UI may expose analytics only when the switch is on (single flag)."""
    return analytics_enabled()


def analytics_graph_enabled() -> bool:
    """Phase 5 gate (default OFF, fail closed) — constellation graph layer.

    Layered ON TOP of the Phase 4 switch: the graph UX renders only when
    BOTH flags are explicitly enabled. Turning the Phase 4 flag off (or
    this one) restores the exact pre-Phase-5 behavior with no DB rollback.
    """
    return analytics_enabled() and _parse_env(_ENV_ANALYTICS_GRAPH)