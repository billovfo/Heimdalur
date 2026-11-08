# heimdal/modules/analytics/__init__.py
"""Phase 4 — registry analytics portal integration (read-only).

Versioned readers over registry_analytics plus view-model builders for the
EXISTING portal surfaces (Person/Company/Personer/Relationer/Netværk).
Nothing here writes: the analytics tables are Phase 3 build products and are
consumed through one ACTIVE-build pin per request.
"""