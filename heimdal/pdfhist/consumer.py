"""Read-only consumer adapter for the ACTIVE registry projection.

Task "CONSUMER WIRING I SHADOW MODE" - the application-side read path:

    existing Heimdal consumer
            |
    feature-gated registry projection adapter (THIS module)
            |
    registry_projection.v_active_*

Binding principles implemented here:

* READ-ONLY: this module never writes lifecycle, settings or data; sessions it
  opens are pinned read-only (`SET default_transaction_read_only = on`). All
  lifecycle writes belong to the admin CLI (scripts/registry_projection_admin).
* FAIL-CLOSED: integration disabled, flag resolution failure, missing or
  inconsistent ACTIVE pointer, unreadable projection, or missing company row
  all resolve to "no projection data" so the caller keeps the existing
  canonical result. The projection layer can never break a company lookup.
* ACTIVE-ONLY: consumers read through registry_projection.v_active_run - never
  "latest run", "highest run_id" or "latest created_at", and no hardcoded
  run id or version. A missing/ambiguous ACTIVE pointer fails closed.
* FIELD CONTRACT: only the approved snapshot fields are ever consumed, only
  with authority_state = AUTHORITATIVE_SNAPSHOT, filtered by approved
  field_name, so a malformed row cannot become consumer current state.
* auditor is NEVER delivered as authoritative current state (binding rule
  "AUDITOR CURRENT STATE NOT SAFE FOR CANONICAL USE"); company_form is NEVER
  sourced from the PDF projection; board/management come only from
  snapshot-authoritative rows - historical Lysing events are exposed through
  the separate history method and can never override current membership.
* History is a separate method; the current-state consumer never touches
  company_history_event.
* Warnings are available for shadow/admin verification but are never injected
  into normal user responses by this task.

Enabled-mode merge policy (explicit, per field - no generic coalesce):

    PDF projection authoritative for the approved snapshot fields
    (including its explicit KNOWN_EMPTY semantics);
    canonical retained for non-PDF/excluded fields;
    company_form stays with the existing canonical source;
    auditor never becomes a value - only the explicit unavailable status.
"""
from __future__ import annotations

import logging
from contextlib import ExitStack, contextmanager
from typing import Any, Callable

import psycopg

from . import flags, projection
from .normalize import normalize_registration_number
from .temporal import governing_date_sql

# The projection layer lives in the pdfhist pilot database (established
# convention); the QA heimdal database is only read for shadow comparison.
# Env-only (fail-closed P1.1): no password-bearing defaults.


def projection_dsn() -> str:
    import os
    dsn = os.environ.get("HEIMDAL_PDFHIST_PROJECTION_DSN", "")
    if not dsn:
        raise RuntimeError(
            "HEIMDAL_PDFHIST_PROJECTION_DSN is not set; refusing implicit DSN")
    return dsn

_logger = logging.getLogger(__name__)

# Approved promotable fields (section 4). auditor and company_form are
# deliberately absent: they must never become PDF-derived current state.
APPROVED_FIELDS = projection.SNAPSHOT_FIELDS

# Fields that must never be merged from the projection under any flag state.
NEVER_FROM_PROJECTION = ("auditor", "company_form")

AUDITOR_STATUS_CODE = "AUDITOR_CURRENT_STATE_UNAVAILABLE"

# Legal-form prefixes the Felag source writes as part of the registered name.
# Used ONLY for name comparison in the shadow report - never for storage.
LEGAL_FORM_PREFIXES = ("Sp/F ", "P/F ", "ÍVF ", "F/F ", "I/F ", "Amba ",
                       "Í/F ", "Spk ", "Fil ", "Eft/gr ", "LVAÁ ", "FVAÁ ")


def _canonical_base_name(raw_display_name: str | None) -> str:
    """Canonical name minus the '(type S-nr, address)' parenthetical."""
    if not raw_display_name:
        return ""
    base = raw_display_name.split(" (", 1)[0]
    return base.strip()


def _comparable_name(name: str) -> str:
    import re as _re
    lowered = _re.sub(r"\s+", " ", (name or "").strip().casefold())
    for prefix in LEGAL_FORM_PREFIXES:
        if lowered.startswith(prefix.casefold()):
            lowered = lowered[len(prefix):].strip()
    return lowered

# Operational event hook (section 26): lets callers observe fail-closed
# fallbacks without coupling this module to a logging framework.
OperationalCallback = Callable[[str, dict], None]


def _default_operational_event(event: str, details: dict) -> None:
    _logger.warning("registry projection consumer: %s %s", event, details)


_operational_event: OperationalCallback = _default_operational_event


def set_operational_event_hook(callback: OperationalCallback | None) -> None:
    """Replace the operational-event sink (used by tests; default logs)."""
    global _operational_event
    _operational_event = callback or _default_operational_event


class ProjectionUnavailable(Exception):
    """Internal: ACTIVE projection not usable; callers fail closed."""


def _read_only_conn(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn, autocommit=True)
    conn.execute("SET default_transaction_read_only = on")
    return conn


@contextmanager
def _connect(conn: psycopg.Connection | None):
    """Use the caller's connection or open a pinned read-only one.

    No DSN or an unreachable DB raises ProjectionUnavailable, which every
    fail-closed path already catches."""
    if conn is not None:
        yield conn
        return
    try:
        own = _read_only_conn(projection_dsn())
    except (RuntimeError, psycopg.Error) as exc:
        raise ProjectionUnavailable(repr(exc)) from exc
    try:
        yield own
    finally:
        own.close()


@contextmanager
def _gated(conn: psycopg.Connection | None,
           integration_enabled: bool | None = None):
    """The one fail-closed entry of every gated read (F-1/D-01/D-11).

    Yields a projection connection only when integration is on and the
    projection is reachable, else None (= "no projection data"). The env
    switch is resolved first (flags.py precedence), so
    PDF_HISTORY_INTEGRATION_ENABLED=false opens no connection at all; only
    an unset env falls back to the DB mirror. A missing DSN, an unreachable
    DB or a flag read error yields None, never an exception.
    """
    if integration_enabled is None:
        integration_enabled = flags._parse_env(flags._ENV_INTEGRATION)
    if integration_enabled is False:
        yield None
        return
    with ExitStack() as stack:
        try:
            c = stack.enter_context(_connect(conn))
            if integration_enabled is None:
                integration_enabled = flags.integration_enabled(c)
        except Exception as exc:  # noqa: BLE001 - fail closed by design
            _operational_event("projection_unavailable", {"error": repr(exc)})
            c = None
        yield c if integration_enabled else None


def _resolve_active_run(conn: psycopg.Connection) -> int | None:
    """ACTIVE generation through the lifecycle view - fail closed on any
    inconsistency (no ACTIVE, or more than one ACTIVE row)."""
    rows = conn.execute(
        "SELECT projection_run_id, projection_version, status "
        "FROM registry_projection.v_active_run").fetchall()
    if len(rows) != 1 or rows[0][2] != "ACTIVE":
        _operational_event("active_pointer_unavailable", {"rows": len(rows)})
        return None
    return rows[0][0]


def _company_row(conn: psycopg.Connection, run_id: int,
                 regnr: str) -> tuple | None:
    return conn.execute(
        "SELECT company_id, registration_number, snapshot_date, "
        "source_document_id, source_observation_id, source_parse_result_id "
        "FROM registry_projection.company_current_state "
        "WHERE projection_run_id = %s AND registration_number = %s",
        (run_id, regnr)).fetchone()


def _load_state_unchecked(conn: psycopg.Connection,
                          regnr: str) -> dict | None:
    """Read the ACTIVE projection state for one company (no flag gate).

    Raises ProjectionUnavailable when the ACTIVE generation cannot be
    resolved; returns None when the company has no projection row.
    """
    run_id = _resolve_active_run(conn)
    if run_id is None:
        raise ProjectionUnavailable("no consistent ACTIVE projection")
    row = _company_row(conn, run_id, regnr)
    if row is None:
        return None
    company_id = row[0]
    version = conn.execute(
        "SELECT projection_version FROM registry_projection.projection_run "
        "WHERE projection_run_id = %s", (run_id,)).fetchone()[0]
    fields: dict[str, dict] = {}
    for (field_name, value_json, authority_state, warning_code, doc_id,
         obs_id, parse_id, snap_date) in conn.execute(
            "SELECT field_name, value_json, authority_state, warning_code, "
            "source_document_id, source_observation_id, "
            "source_parse_result_id, source_snapshot_date "
            "FROM registry_projection.company_current_state_field "
            "WHERE projection_run_id = %s AND company_id = %s "
            "AND field_name = ANY(%s) "
            "AND authority_state = 'AUTHORITATIVE_SNAPSHOT'",
            (run_id, company_id, sorted(APPROVED_FIELDS))).fetchall():
        # read-time authority gate (section 29): only approved field names
        # with snapshot-authoritative rows are consumed
        fields[field_name] = {
            "value": value_json,
            "authority_state": authority_state,
            "warning_code": warning_code,
            "source": {
                "document_id": doc_id,
                "observation_id": obs_id,
                "parse_result_id": parse_id,
                "snapshot_date": snap_date,
            },
        }
    warnings = [
        {"warning_code": w, "severity": sev, "details": details}
        for (w, sev, details) in conn.execute(
            "SELECT warning_code, severity, details_json "
            "FROM registry_projection.projection_warning "
            "WHERE projection_run_id = %s AND company_id = %s",
            (run_id, company_id)).fetchall()
    ]
    return {
        "registration_number": row[1],
        "projection_found": True,
        "projection_run_id": run_id,
        "projection_version": version,
        "snapshot_date": row[2],
        "fields": fields,
        "warnings": warnings,
        "provenance": {
            "document_id": row[3],
            "observation_id": row[4],
            "parse_result_id": row[5],
            "authority_state": "AUTHORITATIVE_SNAPSHOT",
        },
    }


def get_projected_company_state(registration_number: str, *,
                                conn: psycopg.Connection | None = None,
                                integration_enabled: bool | None = None
                                ) -> dict | None:
    """Feature-gated ACTIVE-projection state for one company.

    Returns None when integration is disabled (or any fail-closed condition
    holds) - callers must then use the existing canonical result unchanged.
    Returns the response model only when the ACTIVE projection actually has
    the company.
    """
    with _gated(conn, integration_enabled) as c:
        if c is None:
            return None  # disabled: projection must not affect the result
        regnr = normalize_registration_number(registration_number)
        if regnr is None:
            return None
        try:
            return _load_state_unchecked(c, regnr)
        except (ProjectionUnavailable, psycopg.Error) as exc:
            # section 26: projection read failure -> fail closed to canonical
            _operational_event("projection_read_failed", {
                "registration_number": registration_number,
                "error": repr(exc)})
            return None


def get_projected_company_field(registration_number: str, field: str, *,
                                conn: psycopg.Connection | None = None,
                                integration_enabled: bool | None = None
                                ) -> dict | None:
    """One approved field from the ACTIVE projection (None otherwise)."""
    if field not in APPROVED_FIELDS:
        return None  # auditor/company_form/etc can never be consumed
    state = get_projected_company_state(registration_number, conn=conn,
                                        integration_enabled=integration_enabled)
    if state is None:
        return None
    record = state["fields"].get(field)
    if record is not None:
        record = {**record, "projection_run_id": state["projection_run_id"]}
    return record


def get_projection_warnings(registration_number: str, *,
                            conn: psycopg.Connection | None = None,
                            require_integration: bool = False) -> list[dict]:
    """Projection warnings for admin/shadow verification (NOT user-facing).

    Reading warning metadata is read-only verification data; callers must not
    inject these into normal user responses (this task does not).
    """
    with _gated(conn, None if require_integration else True) as c:
        if c is None:
            return []
        try:
            run_id = _resolve_active_run(c)
            if run_id is None:
                return []
            regnr = normalize_registration_number(registration_number)
            if regnr is None:
                return []
            row = _company_row(c, run_id, regnr)
            if row is None:
                return []
            return [
                {"warning_code": w, "severity": sev, "details": details}
                for (w, sev, details) in c.execute(
                    "SELECT warning_code, severity, details_json "
                    "FROM registry_projection.projection_warning "
                    "WHERE projection_run_id = %s AND company_id = %s",
                    (run_id, row[0])).fetchall()
            ]
        except psycopg.Error as exc:
            _operational_event("projection_read_failed", {
                "registration_number": registration_number,
                "error": repr(exc)})
            return []


def get_projected_company_history(registration_number: str, *, conn=None,
                                  integration_enabled: bool | None = None,
                                  limit: int | None = None
                                  ) -> list[dict] | None:
    """SEPARATE history retrieval path (section 21).

    Never mixed into current state; historical Lysing events cannot override
    snapshot-authoritative membership through the consumer.
    """
    with _gated(conn, integration_enabled) as c:
        if c is None:
            return None
        try:
            run_id = _resolve_active_run(c)
            if run_id is None:
                return None
            regnr = normalize_registration_number(registration_number)
            if regnr is None:
                return None
            row = _company_row(c, run_id, regnr)
            if row is None:
                return None
            return [
                {"event_type": et, "action": action, "entity_key": key,
                 "effective_date": str(ed) if ed else None,
                 "registration_date": str(rd) if rd else None,
                 "event_date": str(evd) if evd else None,
                 "source_document_id": doc,
                 "source_event_id": src_event,
                 "authority_state": auth}
                for (et, action, key, ed, rd, evd, doc, src_event, auth)
                in c.execute(
                    "SELECT event_type, action, entity_key, effective_date, "
                    f"registration_date, {governing_date_sql()}, "
                    "source_document_id, source_event_id, authority_state "
                    "FROM registry_projection.company_history_event "
                    "WHERE projection_run_id = %s AND company_id = %s "
                    # D-03: newest first, so a LIMIT keeps the newest
                    # (D-13: the shared date precedence)
                    f"ORDER BY {governing_date_sql()} "
                    "DESC NULLS LAST, id DESC "
                    + ("LIMIT %s" if limit else "") + "",
                    (run_id, row[0]) + ((limit,) if limit else ())).fetchall()
            ]
        except psycopg.Error as exc:
            _operational_event("projection_read_failed",
                               {"error": repr(exc)})
            return None


# --------------------------------------------------------------------------
# Enabled-mode merge contract (explicit, field by field - section 14/15)
# --------------------------------------------------------------------------

# Per-field policy table. PDF_AUTHORITY: the ACTIVE projection is
# authoritative for the field once integration is enabled (including its
# explicit KNOWN_EMPTY / NOT_PRESENT_IN_SOURCE semantics - no silent fallback
# to canonical values). CANONICAL_RETAINED: the existing canonical source
# stays authoritative; the projection never overrides it.
FIELD_CONTRACT = {
    field: "PDF_AUTHORITATIVE" for field in APPROVED_FIELDS
}
FIELD_CONTRACT["company_form"] = "CANONICAL_RETAINED"
FIELD_CONTRACT["auditor"] = "EXCLUDED_NOT_SAFE"


def merge_pdf_current_state(canonical_result: dict,
                            projection_state: dict | None) -> dict:
    """Deterministic enabled-mode merge (section 13/14).

    Per-field explicit policy; no generic `projection or canonical`
    coalescing. Returns a NEW dict; never mutates canonical_result.
    """
    merged = dict(canonical_result)
    if not projection_state or not projection_state.get("projection_found"):
        return merged
    fields = projection_state.get("fields", {})
    # approved PDF fields are authoritative (including explicit empties)
    for field, record in fields.items():
        policy = FIELD_CONTRACT.get(field)
        if policy == "PDF_AUTHORITATIVE":
            merged[f"pdf_{field}"] = record["value"]
            merged[f"pdf_{field}_authority"] = record["authority_state"]
            if record.get("warning_code"):
                merged[f"pdf_{field}_status"] = record["warning_code"]
    # excluded fields keep their canonical source - never projected values
    merged.setdefault("pdf_company_form_policy", "CANONICAL_RETAINED")
    merged["pdf_auditor_status"] = AUDITOR_STATUS_CODE
    merged["pdf_projection_run_id"] = projection_state["projection_run_id"]
    merged["pdf_snapshot_date"] = (str(projection_state["snapshot_date"])
                                   if projection_state.get("snapshot_date")
                                   else None)
    merged["pdf_provenance"] = projection_state["provenance"]
    return merged


def compare_with_canonical(registration_number: str, *,
                           conn: psycopg.Connection | None = None,
                           canonical_conn: psycopg.Connection | None = None,
                           canonical_lookup: Callable[[str], dict | None]
                           | None = None) -> dict:
    """READ-ONLY single-company shadow comparison (section 22).

    Reads canonical + ACTIVE projection, reports differences; never changes
    any runtime result and never writes.
    """
    regnr = normalize_registration_number(registration_number)
    canonical = (canonical_lookup(regnr) if canonical_lookup else None)
    if canonical is None:
        # identity bridge: QA inventory stores 'S'-prefixed registration
        # numbers; the join uses the validated digit normalization (section 16)
        with _connect(canonical_conn) as cc:
            row = cc.execute(
                "SELECT registration_number, raw_display_name "
                "FROM company_inventory").fetchall()
        canonical_row = next(
            (r for r in row
             if normalize_registration_number(r[0]) == regnr), None)
        canonical = ({"registration_number": regnr, "name": canonical_row[1]}
                     if canonical_row else None)
    result: dict = {"registration_number": regnr,
                    "canonical_found": canonical is not None}
    # shadow comparison is the verification path: it reads the ACTIVE
    # projection even while integration is disabled (section 22) and never
    # affects any runtime result
    with _connect(conn) as c:
        try:
            state = _load_state_unchecked(c, regnr) if regnr else None
        except (ProjectionUnavailable, psycopg.Error):
            state = None
    result["projection_found"] = state is not None
    result["warnings"] = []
    if state:
        result["projection_run_id"] = state["projection_run_id"]
        proj_name = state["fields"].get("company_name", {}).get("value")
        result["name_same"] = (canonical is not None and
                               _comparable_name(proj_name or "") ==
                               _comparable_name(
                                   _canonical_base_name(
                                       canonical.get("name"))))
        result["name_projection"] = (state["fields"].get("company_name", {})
                                     .get("value"))
        result["name_canonical"] = canonical.get("name") if canonical else None
        result["projection_only_fields_available"] = sorted(
            f for f, rec in state["fields"].items()
            if rec.get("value") is not None or rec.get("warning_code")
            in (None, "KNOWN_EMPTY"))
        result["warnings"] = [w["warning_code"] for w in state["warnings"]]
    return result


def integration_flag(conn: psycopg.Connection | None = None) -> bool:
    """Fail-closed integration flag for consumer decisions (section 11)."""
    with _gated(conn) as c:
        return c is not None


def ui_exposure_allowed(conn: psycopg.Connection | None = None) -> bool:
    """UI exposure = integration_enabled AND ui_enabled (flags.py semantics).

    Env switches first (no connection when either is pinned off), then the
    DB mirror in the registry_projection schema; an unreachable projection
    or any read error fails closed.
    """
    if flags._parse_env(flags._ENV_UI) is False:
        return False
    return flags.memo("ui_exposure_allowed", lambda: _ui_exposure_allowed(conn))


def _ui_exposure_allowed(conn: psycopg.Connection | None) -> bool:
    with _gated(conn) as c:
        if c is None:
            return False
        try:
            return flags.ui_enabled(c)
        except Exception:  # noqa: BLE001 - fail closed by design
            return False


def shadow_compare_population(*, conn=None, canonical_conn=None) -> dict:
    """Full-population READ-ONLY shadow comparison (sections 22/23).

    Compares every ACTIVE-projection company against the canonical inventory.
    Never mutates anything; safe to run while integration is disabled.
    """
    with _connect(conn) as c, _connect(canonical_conn) as cc:
        state_rows = c.execute(
            "SELECT cs.registration_number, cs.snapshot_date, "
            "cs.projection_run_id, "
            "(SELECT value_json FROM "
            " registry_projection.company_current_state_field f "
            " WHERE f.projection_run_id = cs.projection_run_id "
            "   AND f.company_id = cs.company_id "
            "   AND f.field_name = 'company_name' "
            "   AND f.authority_state = 'AUTHORITATIVE_SNAPSHOT'), "
            "EXISTS (SELECT 1 FROM registry_projection.projection_warning w "
            "        WHERE w.projection_run_id = cs.projection_run_id "
            "        AND w.company_id = cs.company_id) "
            "FROM registry_projection.company_current_state cs "
            "WHERE cs.projection_run_id IN "
            "      (SELECT projection_run_id "
            "       FROM registry_projection.v_active_run) "
            "ORDER BY cs.registration_number").fetchall()
        canonical = {}
        for (raw_regnr, display_name) in cc.execute(
                "SELECT registration_number, raw_display_name "
                "FROM company_inventory").fetchall():
            canonical.setdefault(
                normalize_registration_number(raw_regnr), display_name)
    compared = {"name_same": 0, "name_different": 0, "canonical_missing": 0}
    name_diffs = []
    for regnr, snap_date, run_id, name, has_warning in state_rows:
        if regnr not in canonical:
            compared["canonical_missing"] += 1
            continue
        proj_base = _comparable_name(name or "")
        canon_base = _comparable_name(_canonical_base_name(canonical[regnr]))
        if proj_base == canon_base:
            compared["name_same"] += 1
        else:
            compared["name_different"] += 1
            name_diffs.append({"registration_number": regnr,
                               "canonical_name": canonical[regnr],
                               "canonical_name_base": canon_base,
                               "projection_name": name,
                               "snapshot_date": str(snap_date),
                               "projection_run_id": run_id})
    return {
        "companies_compared": len(state_rows),
        "projection_found": len(state_rows),
        "canonical_found": len(state_rows) - compared["canonical_missing"],
        "canonical_missing": compared["canonical_missing"],
        "name_same": compared["name_same"],
        "name_different": compared["name_different"],
        "companies_with_warnings": sum(1 for r in state_rows if r[4]),
        "name_differences": name_diffs,
        "errors": [],
    }

# ---------------------------------------------------------------------------
# Entity-link readers (task: KNYT PDF-DATA TIL EKSISTERENDE VIRKSOMHEDER OG
# PERSONER, section 43). Small read-only helpers on the ACTIVE generation.
# Same gating + fail-closed conventions as get_projected_company_state.
# ---------------------------------------------------------------------------

_LINK_COLS = """l.entity_link_id, l.projection_run_id, l.entity_type,
  l.source_entity_key, l.canonical_entity_id, l.registration_number,
  l.person_role_id, l.link_status, l.link_method, l.match_metadata,
  l.link_code_version"""

_LINK_ROW_FIELDS = ("entity_type", "source_entity_key", "canonical_entity_id",
                    "registration_number", "person_role_id", "link_status",
                    "link_method", "match_metadata")


def get_entity_link(entity_type: str, source_entity_key: str, *,
                    conn: psycopg.Connection | None = None,
                    integration_enabled: bool | None = None) -> dict | None:
    """Link row for one source identity on the ACTIVE generation.

    Returns None (fail closed) when integration is disabled, the projection
    is unavailable, or no link row exists.
    """
    with _gated(conn, integration_enabled) as c:
        if c is None or entity_type not in ("COMPANY", "PERSON"):
            return None
        try:
            run_id = _resolve_active_run(c)
            if run_id is None:
                return None
            row = c.execute(
                f"""SELECT {_LINK_COLS} FROM registry_projection.entity_link l
                    WHERE l.projection_run_id = %s AND l.entity_type = %s
                      AND l.source_entity_key = %s""",
                (run_id, entity_type, source_entity_key)).fetchone()
        except Exception as exc:  # noqa: BLE001 - fail closed by design
            _operational_event("entity_link_read_failed",
                               {"entity_type": entity_type,
                                "source_entity_key": source_entity_key,
                                "error": repr(exc)})
            return None
        if row is None:
            return None
        return dict(zip(_LINK_ROW_FIELDS, row[2:]))


def get_linked_person_for_role(person_role_id: int, *,
                               conn: psycopg.Connection | None = None,
                               integration_enabled: bool | None = None) -> dict | None:
    """LINKED canonical person for one source role occurrence, else None.

    LINKED only: UNRESOLVED/AMBIGUOUS never produce a canonical person
    (sections 19/20/57).
    """
    link = get_entity_link("PERSON", f"ROLE:{person_role_id}", conn=conn,
                           integration_enabled=integration_enabled)
    if link is not None and link.get("link_status") == "LINKED":
        return link
    return None


def get_skraseting_roles_for_person(
        canonical_entity_id: int, *,
        conn: psycopg.Connection | None = None,
        integration_enabled: bool | None = None) -> dict | None:
    """All safely linked Skráseting role occurrences for one canonical person.

    Returns None when integration is disabled or no linked evidence exists.
    Returns {'current': [...], 'historical': [...]} where each role entry
    carries the role, the role's canonical company (only when the company
    itself is LINKED), the company display key (registration number), role
    title, role_type, scope and source provenance (document id).
    """
    with _gated(conn, integration_enabled) as c:
        if c is None:
            return None
        try:
            run_id = _resolve_active_run(c)
            if run_id is None:
                return None
            rows = c.execute(
                f"""SELECT {_LINK_COLS},
                           l.projection_run_id,
                           pr.role_type, pr.role_title, pr.person_name,
                           pr.entity_normalized, pr.source_scope,
                           pr.source_document_id, pr.source_event_id,
                           pr.company_id
                    FROM registry_projection.entity_link l
                    JOIN registry_projection.company_person_role pr
                      ON pr.id = l.person_role_id
                    WHERE l.projection_run_id = %s
                      AND l.entity_type = 'PERSON'
                      AND l.link_status = 'LINKED'
                      AND l.canonical_entity_id = %s
                    ORDER BY pr.source_scope, pr.company_id, pr.id""",
                (run_id, canonical_entity_id)).fetchall()
            if not rows:
                return None
            fields = _LINK_ROW_FIELDS + ("link_code_version",
                                         "projection_run_id", "role_type",
                                         "role_title", "person_name",
                                         "entity_normalized",
                                         "source_scope", "source_document_id",
                                         "source_event_id", "company_id")
            roles = [dict(zip(fields, row[2:])) for row in rows]
            # attach canonical company per role: only when the company itself
            # is LINKED (reciprocal identity, section 54)
            company_ids = sorted({r["company_id"] for r in roles})
            company_rows = c.execute(
                f"""SELECT {_LINK_COLS}, s.company_id
                    FROM registry_projection.entity_link l
                    JOIN registry_projection.company_current_state s
                      ON s.projection_run_id = l.projection_run_id
                     AND s.registration_number = l.registration_number
                    WHERE l.projection_run_id = %s
                      AND l.entity_type = 'COMPANY'
                      AND s.company_id = ANY(%s)""",
                (run_id, company_ids)).fetchall()
            comp_fields = _LINK_ROW_FIELDS + ("link_code_version",
                                              "company_id")
            company_by_id = {row[-1]: dict(zip(comp_fields, row[2:-1]))
                             for row in company_rows}
            current, historical = [], []
            for role in roles:
                clink = company_by_id.get(role["company_id"])
                entry = dict(role)
                entry["company_link"] = clink
                if role["source_scope"] == "CURRENT_AUTHORITATIVE":
                    current.append(entry)
                else:
                    historical.append(entry)
            return {"current": current, "historical": historical}
        except Exception as exc:  # noqa: BLE001 - fail closed by design
            _operational_event("entity_link_read_failed", {
                "canonical_entity_id": canonical_entity_id,
                "error": repr(exc)})
            return None


def get_skraseting_person_history(
        canonical_entity_id: int, *,
        conn: psycopg.Connection | None = None,
        integration_enabled: bool | None = None,
        limit: int | None = None) -> list[dict] | None:
    """Historical Skráseting role EVENTS for one safely linked canonical person.

    Entity-history surface (owner defect task section 9): each entry carries
    event date, company link info, role and action - HISTORICAL_EVENT scope
    only, never the authoritative snapshot rows. Company context is attached
    only when the company itself is LINKED (reciprocal identity); the caller
    resolves canonical company display names on the canonical side.
    Fail closed: returns None on gate closed / no links / read errors.
    """
    with _gated(conn, integration_enabled) as c:
        if c is None:
            return None
        try:
            run_id = _resolve_active_run(c)
            if run_id is None:
                return None
            # D-03: dedup (one source event may repeat across notices)
            # BEFORE the LIMIT, newest first; count and first date are
            # window aggregates over the whole deduplicated set.
            rows = c.execute(
                f"""SELECT d.*, count(*) OVER () AS total_events,
                          min(d.event_date) OVER () AS first_event_date
                   FROM (
                   SELECT DISTINCT ON (pr.id, he.event_type, he.action,
                                       event_date)
                          l.canonical_entity_id AS person_canonical_id,
                          l.registration_number AS person_registration_number,
                          pr.id AS person_role_id, pr.company_id,
                          pr.role_type, pr.role_title, pr.person_name,
                          pr.entity_normalized, pr.source_document_id,
                          pr.source_event_id,
                          s.registration_number AS company_registration_number,
                          cl.canonical_entity_id AS company_canonical_id,
                          he.event_type, he.action,
                          {governing_date_sql("he")} AS event_date
                   FROM registry_projection.entity_link l
                   JOIN registry_projection.company_person_role pr
                     ON pr.id = l.person_role_id
                   JOIN registry_projection.company_current_state s
                     ON s.projection_run_id = l.projection_run_id
                    AND s.company_id = pr.company_id
                   LEFT JOIN registry_projection.entity_link cl
                     ON cl.projection_run_id = l.projection_run_id
                    AND cl.entity_type = 'COMPANY'
                    AND cl.link_status = 'LINKED'
                    AND cl.registration_number = s.registration_number
                   LEFT JOIN registry_projection.company_history_event he
                     ON he.projection_run_id = l.projection_run_id
                    AND he.company_id = pr.company_id
                    AND he.source_event_id = pr.source_event_id
                    AND (pr.entity_normalized IS NULL
                         OR he.entity_key = pr.entity_normalized)
                   WHERE l.projection_run_id = %s
                     AND l.entity_type = 'PERSON'
                     AND l.link_status = 'LINKED'
                     AND l.canonical_entity_id = %s
                     AND pr.source_scope = 'HISTORICAL_EVENT'
                   ORDER BY pr.id, he.event_type, he.action, event_date,
                            cl.canonical_entity_id) d
                   ORDER BY d.event_date DESC NULLS LAST, d.person_role_id"""
                + (" LIMIT %s" if limit else ""),
                (run_id, canonical_entity_id)
                + ((limit,) if limit else ())).fetchall()
            if not rows:
                return None
            events = []
            for row in rows:
                (person_canonical_id, _preg, role_id, company_id, role_type,
                 role_title, person_name, entity_normalized, source_doc,
                 source_event_id, company_registration_number,
                 company_canonical_id, event_type, action, event_date,
                 total_events, first_event_date) = row
                events.append({
                    "person_role_id": role_id,
                    "company_id": company_id,
                    "company_canonical_entity_id": company_canonical_id,
                    "company_registration_number": company_registration_number,
                    "role_type": role_type,
                    "role_title": role_title,
                    "person_name": person_name,
                    "normalized_name": entity_normalized,
                    "event_type": event_type,
                    "action": action,
                    "event_date": str(event_date) if event_date else None,
                    "source_document_id": source_doc,
                    "source_event_id": source_event_id,
                    "total_events": total_events,
                    "first_event_date": (str(first_event_date)
                                         if first_event_date else None),
                })
            return events
        except Exception as exc:  # noqa: BLE001 - fail closed by design
            _operational_event("entity_link_read_failed", {
                "canonical_entity_id": canonical_entity_id,
                "error": repr(exc)})
            return None
