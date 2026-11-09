"""Skráseting / PDF-historik UI v1 - view model (read-only, feature-gated).

Builds the presentation model for the separate "Skráseting" surface on the
company page. Everything here is READ-ONLY: it renders the ACTIVE projection
through the consumer adapter plus the separate history method.

Binding product rules implemented:

* exposure gate: ui_exposure_allowed = integration_enabled AND ui_enabled;
  both false -> the view model is a bare "not allowed" shell (the route
  additionally 404s, so no DOM data exists at all);
* current state is a SNAPSHOT - labeled "Registreret status pr. <dato>",
  never "senest opdateret";
* only AUTHORITATIVE_SNAPSHOT rows are rendered as current facts;
* auditor never renders as a factual current value (explicit unsafe-source
  status instead); historical auditor events still render in Historik;
* company_form is never claimed as PDF-derived current state;
* historical events cannot mutate the current-state rendering (separate
  data paths, separate sections);
* date labels name the date kind actually shown; implausible source dates
  are labeled as source-provided and possibly erroneous - never corrected;
* SAME_DAY_UNORDERED -> discrete note, never an invented order;
* no-projection -> explicit safe empty state; projection read failure ->
  local, non-dramatic error state; the existing company page never breaks.
"""
from __future__ import annotations

import logging
from typing import Any

import psycopg

from ...pdfhist import consumer, flags
from ..shared.db import read_savepoint
from ...pdfhist.entity_links import normalize_person_name
from ...pdfhist.normalize import normalize_registration_number

_logger = logging.getLogger(__name__)

SOURCE_LABEL = "Kilde: Skráseting Føroya"
SOURCE_DOCUMENT_TYPE = "FelagPdf / Tekningarútskrift"

# Section 7: minimum fields to surface, with existing Heimdal-style labels.
FIELD_LABELS = {
    "company_name": "Registreret navn",
    "alias_names": "Tidligere/alternative navne",
    "company_address": "Adresse",
    "capital": "Kapital",
    "purpose": "Endamál / formål",
    "signing_rules": "Tekningarreglur",
    "accounts_dates": "Regnskabsdatoer",
}
# ordering follows the visual hierarchy (section 32); board/management get
# their own person cards
FIELD_ORDER = ("company_name", "alias_names", "company_address", "capital",
               "purpose", "signing_rules", "accounts_dates")

# Explicit, distinct natural-language states (sections 13/14):
# KNOWN_EMPTY           - the source explicitly states an empty value
# NOT_PRESENT_IN_SOURCE - the field does not exist in the source
# anything else/missing - the value cannot be authoritatively determined
STATUS_TEXT = {
    "KNOWN_EMPTY": "Ikke angivet",
    "NOT_PRESENT_IN_SOURCE": "Ikke oplyst i Skráseting",
}

NO_PROJECTION_TEXT = ("Der er ingen autoritativ Skráseting-PDF-status "
                      "tilgængelig for denne virksomhed.")
ERROR_TEXT = "Skráseting-data kunne ikke indlæses."
AUDITOR_CURRENT_TEXT = ("Aktuel revisor kan ikke fastslås sikkert fra denne "
                        "kilde.")
SAME_DAY_NOTE = ("Rækkefølgen mellem registreringer samme dag kan ikke "
                 "fastslås.")
SOURCE_DATE_ARTIFACT_NOTE = ("Datoen kommer direkte fra kilden og kan være "
                             "fejlbehæftet.")
_SNAPSHOT_LABEL = "Registreret status pr."

# Implausible source years never order the timeline; they render with the
# artifact caveat (no silent correction).
_ARTIFACT_YEAR_BEFORE = 1990


def _human_value(name: str, value: Any) -> dict | None:
    """Semantic display model for one projection field value (owner UX
    correction UX-03): dict/list structures are NEVER rendered as raw
    Python/JSON repr in user-facing UI. Returns a kind-tagged human model,
    or None when the value is already plain user text.

    Per-field semantics:
    * alias_names  -> list of former/alternative names (chips)
    * company_address -> the source's own human address `lines` (internal
      keys like role/verbatim/postal_code/kommuna are never shown)
    * capital      -> the source verbatim human amount when present, else
      amount + currency with Danish thousands separators
    * accounts_dates -> registered account dates: latest first, then earlier
    * any other list -> text lines; any other dict -> hidden behind the
      source disclosure (fail closed: no repr leak, no invented semantics)
    """
    if not isinstance(value, (dict, list)):
        return None
    if name == "alias_names" and isinstance(value, list):
        return {"kind": "names",
                "names": [str(v) for v in value if str(v).strip()]}
    if name == "company_address" and isinstance(value, dict):
        lines = [str(l).strip() for l in (value.get("lines") or [])
                 if str(l).strip()]
        if not lines:
            lines = [str(value[k]).strip()
                     for k in ("street", "postal_code", "city")
                     if value.get(k)]
        if lines:
            return {"kind": "lines", "lines": lines}
        return {"kind": "hidden"}
    if name == "capital" and isinstance(value, dict):
        verbatim = value.get("verbatim")
        if isinstance(verbatim, str) and verbatim.strip():
            return {"kind": "text", "text": verbatim.strip()}
        amount, currency = value.get("amount"), value.get("currency")
        if isinstance(amount, (int, float)):
            formatted = f"{int(amount):,}".replace(",", ".")
            return {"kind": "text",
                    "text": f"{currency} {formatted}".strip()
                    if currency else formatted}
        return {"kind": "hidden"}
    if name == "accounts_dates" and isinstance(value, list):
        iso = sorted({str(v) for v in value}, reverse=True)
        if iso:
            def _dk(d: str) -> str:
                return f"{d[8:10]}.{d[5:7]}.{d[0:4]}" if len(d) == 10 else d
            return {"kind": "dates",
                    "latest": {"iso": iso[0], "dk": _dk(iso[0])},
                    "earlier": [{"iso": d, "dk": _dk(d)} for d in iso[1:]]}
        return {"kind": "hidden"}
    if isinstance(value, list):
        texts = [str(v) for v in value if str(v).strip()]
        return {"kind": "names", "names": texts} if texts else {"kind": "hidden"}
    return {"kind": "hidden"}  # unexpected dict shape: never a raw repr


def _display_value(field: dict) -> tuple[str, str]:
    """(display_text, status_class) for one projection field.

    Distinct wording per state - never one generic "unknown" phrase:
    KNOWN_EMPTY -> "Ikke angivet"; NOT_PRESENT_IN_SOURCE -> "Ikke oplyst i
    Skráseting"; missing/unresolvable -> "Kan ikke autoritativt fastslås".
    """
    value = field.get("value")
    warning = field.get("warning_code")
    if value in (None, ""):
        if warning == "NOT_PRESENT_IN_SOURCE":
            return STATUS_TEXT["NOT_PRESENT_IN_SOURCE"], "empty"
        if warning in (None, "KNOWN_EMPTY"):
            return STATUS_TEXT["KNOWN_EMPTY"], "empty"
        return "Kan ikke autoritativt fastslås", "unavailable"
    return str(value), "value"


def _person_rows(field: dict | None) -> list[dict]:
    people = (field or {}).get("value") or []
    return [{"name": p.get("name"), "role": p.get("role")} for p in people]


import re as _re
_HEX_TOKEN = _re.compile(r"^[0-9a-f]{16,}$")


# Human action wording (owner UX correction UX-05): rendered ONLY when the
# source action column actually says so (§23 — never infer). CHANGED and any
# unknown action stay neutral ("…ændring registreret").
ACTION_JOIN = {
    "BOARD_CHANGE": "tiltrådte bestyrelsen",
    "MANAGEMENT_CHANGE": "tiltrådte ledelsen",
    "AUDITOR_CHANGE": "tiltrådte som revisor",
}
ACTION_LEAVE = {
    "BOARD_CHANGE": "fratrådte bestyrelsen",
    "MANAGEMENT_CHANGE": "fratrådte ledelsen",
    "AUDITOR_CHANGE": "fratrådte som revisor",
}
# E2: founding appointments are dated at Stovningardato and rendered with
# this explicit source marker.
FOUNDING_ACTION_SUFFIX = "ved stiftelse"
NEUTRAL_ACTION_TEXT = "ændring registreret"

# Presentation vocabulary for source role titles (§25): deterministic
# translation of the observed Faroese titles — no inference; the source
# title itself stays in the source disclosure (§28).
ROLE_TITLE_LABELS = {
    "Nevndarformaður": "Bestyrelsesformand",
    "Nevndarforkvinna": "Bestyrelsesformand",
    "Næstformaður": "Næstformand",
    "Næstforkvinna": "Næstformand",
    "Nevndarlimur": "Bestyrelsesmedlem",
    "Stjóri": "Direktør",
    "Forstjóri": "Direktør",
    "Kurator": "Kurator",
    "Likvidator": "Likvidator",
    "Starvsfólkavalt umboð": "Medarbejderrepræsentant",
}

_EVENT_TYPE_LABELS = {
    "BOARD_CHANGE": "Bestyrelsesændring registreret",
    "MANAGEMENT_CHANGE": "Ledelsesændring registreret",
    "AUDITOR_CHANGE": "Revisorændring registreret",
    "NAME_CHANGE": "Navn ændret",
    "CAPITAL_CHANGE": "Kapital ændret",
    "SIGNING_RULE_CHANGE": "Signeringsregler ændret",
    "ARTICLES_CHANGE": "Vedtægter ændret",
    "PURPOSE_CHANGE": "Formål ændret",
    "ADDRESS_CHANGE": "Adresse ændret",
    "OTHER_CHANGE": "Registrering ændret",
    "MERGER": "Fusion registreret",
}

EVENT_LABELS = _EVENT_TYPE_LABELS  # single label map (§43 — no parallel maps)

# MERGER actions are the source's own two shapes; no join/leave inference.
MERGER_ACTION_LABELS = {
    "PLAN_REGISTERED": "Fusionsplan modtaget",
    "COMPLETED": "Fusion gennemført",
}


def _dk_date_str(iso) -> str | None:
    """Human Danish date for a source ISO date (string or date object);
    display only — the ISO value stays in the `datetime` attribute."""
    if iso is None:
        return None
    if hasattr(iso, "strftime"):  # datetime.date / datetime
        return iso.strftime("%d.%m.%Y")
    iso = str(iso)
    if len(iso) == 10:
        return f"{iso[8:10]}.{iso[5:7]}.{iso[0:4]}"
    return iso


def _event_view(event: dict) -> dict:
    """Render one history event without inventing date or action semantics."""
    label = _EVENT_TYPE_LABELS.get(event.get("event_type"),
                                   event.get("event_type") or "Registrering")
    entity = event.get("entity_key") or ""
    # MERGER: show the counterpart's name and registration number from the
    # source party/counterpart evidence (never inferred from entity_key).
    counterpart = (consumer.merger_counterpart(event)
                   if event.get("event_type") == "MERGER" else None)
    counterpart_name = (counterpart or {}).get("name")
    counterpart_registration_number = \
        (counterpart or {}).get("registration_number")
    if event.get("event_type") == "MERGER" and counterpart:
        entity = " ".join(bit for bit in (
            counterpart_name,
            f"({counterpart_registration_number})"
            if counterpart_registration_number else None) if bit)
    # internal normalized tokens (e.g. hex identifiers) are not user text
    if _HEX_TOKEN.match(entity):
        entity = ""
    action = event.get("action")
    if event.get("event_type") == "MERGER":
        action_label = MERGER_ACTION_LABELS.get(action)
    elif action == "APPOINTED":
        action_label = ACTION_JOIN.get(event.get("event_type"))
    elif action == "REMOVED":
        action_label = ACTION_LEAVE.get(event.get("event_type"))
    else:
        action_label = None  # unknown/CHANGED never becomes join/leave (§23)
    if action_label and event.get("origin") == "FOUNDING":
        action_label = f"{action_label} {FOUNDING_ACTION_SUFFIX}"
    effective = event.get("effective_date")
    registration = event.get("registration_date")
    if effective:
        timeline_date, timeline_label = effective, "Ikrafttrædelsesdato"
    else:
        timeline_date, timeline_label = registration, "Registreringsdato"
    caveats: list[str] = []
    # E2: a founding date is the source's own first-state date, not a later
    # notice's artifact; pre-1990 foundations (lys_5053: 1977) stay dated.
    # NEW-3: a malformed date string skips the caveat instead of raising.
    if timeline_date and event.get("origin") != "FOUNDING":
        try:
            year = int(str(timeline_date)[:4])
        except (TypeError, ValueError):
            year = None
        if year is not None and year < _ARTIFACT_YEAR_BEFORE:
            timeline_date = None  # never order by an implausible artifact date
            caveats.append(SOURCE_DATE_ARTIFACT_NOTE)
    return {
        "event_type": event.get("event_type"),
        "timeline_date": timeline_date,
        "timeline_date_dk": _dk_date_str(timeline_date),
        "timeline_date_label": timeline_label,
        "effective_date": effective,
        "registration_date": registration,
        "type": label,
        "action": action,
        "action_label": action_label,
        "origin": event.get("origin"),
        "description": entity,
        "counterpart_name": counterpart_name,
        "counterpart_registration_number": counterpart_registration_number,
        "source_document_id": event.get("source_document_id"),
        "source_event_id": event.get("source_event_id"),
        "authority_state": event.get("authority_state"),
        "caveats": caveats,
        "persons": [],
    }


def _projection_view(state: dict) -> dict:
    fields = state["fields"]
    ordered = []
    for name in FIELD_ORDER:
        record = fields.get(name)
        if record is None:
            continue  # only authoritative rows are ever rendered
        value, status = _display_value(record)
        ordered.append({
            "name": name,
            "label": FIELD_LABELS[name],
            "value": value,
            "status": status,
            "human": _human_value(name, record.get("value")),
            "warning_code": record.get("warning_code"),
            "source": record.get("source", {}),
        })
    return {
        "run_id": state["projection_run_id"],
        "version": state["projection_version"],
        "snapshot_date": state["snapshot_date"],
        # human date for display (ISO stays available for the source details)
        "snapshot_date_dk": _dk_date_str(state["snapshot_date"]),
        "registered_name": (fields.get("company_name") or {}).get("value"),
        "fields": ordered,
        "board": _person_rows(fields.get("board")),
        "management": _person_rows(fields.get("management")),
        "provenance": state["provenance"],
        "warnings": state["warnings"],
        "same_day_note": any(w["warning_code"] == "SAME_DAY_UNORDERED"
                             for w in state["warnings"]),
    }


def _base_view() -> dict[str, Any]:
    return {
        "allowed": False,
        "has_projection": False,
        "error": False,
        "registered_name": None,
        "canonical_name": None,
        "snapshot_label": _SNAPSHOT_LABEL,
        "source_label": SOURCE_LABEL,
        "source_document_type": SOURCE_DOCUMENT_TYPE,
        "fields": [],
        "board": [],
        "management": [],
        "history": [],
        "same_day_note": False,
        "auditor_current_text": AUDITOR_CURRENT_TEXT,
        "no_projection_text": NO_PROJECTION_TEXT,
        "error_text": ERROR_TEXT,
        "snapshot_date": None,
        "provenance": None,
        "company_form_claimed_from_pdf": False,
        "auditor_shown_as_current_value": False,
    }


def _load_person_links(pc: psycopg.Connection, regnr: str) -> list[dict]:
    """Person occurrences for one projected company, with canonical targets
    ONLY where the projection safely LINKED them (sections 24/26/27/57).

    LEFT-join semantics: a role occurrence whose link is UNRESOLVED or
    AMBIGUOUS still carries its SOURCE name (rendered as plain text with a
    discreet note — never a fabricated link); only LINKED rows carry a
    canonical_entity_id. Read failure -> empty list (fail closed; section
    46), the page still renders with plain-text names.

    Product UX consolidation (§21): the table retains rows from older
    SHADOW projection runs next to the ACTIVE run. Without a run filter on
    pr itself, one and the same source occurrence rendered TWICE — the
    SHADOW-run row (no link) as an unresolved row AND the ACTIVE-run row
    (LINKED) as a canonical row. Filtered to the ACTIVE run here: the
    canonical-linked representation wins, the source name stays in the
    evidence disclosure. Pure read composition — no data change, no
    name-based dedupe (ids only).
    """
    try:
        rows = pc.execute(
            """SELECT pr.id, pr.person_name, pr.entity_normalized,
                      pr.role_title, pr.role_type, pr.source_scope,
                      pr.source_event_id, l.canonical_entity_id
               FROM registry_projection.company_person_role pr
               LEFT JOIN registry_projection.entity_link l
                 ON l.person_role_id = pr.id
                AND l.link_status = 'LINKED'
                AND l.entity_type = 'PERSON'
                AND l.projection_run_id =
                    (SELECT projection_run_id
                     FROM registry_projection.v_active_run)
               WHERE pr.projection_run_id =
                     (SELECT projection_run_id
                      FROM registry_projection.v_active_run)
                 AND pr.company_id = (
                     SELECT company_id
                     FROM registry_projection.company_current_state
                     WHERE projection_run_id =
                           (SELECT projection_run_id
                            FROM registry_projection.v_active_run)
                       AND registration_number = %s
                     ORDER BY company_id LIMIT 1)""",
            (regnr,)).fetchall()
    except psycopg.Error as exc:
        _logger.warning("person link read degraded: %s", repr(exc))
        return []
    keys = ("person_role_id", "person_name", "entity_normalized",
            "role_title", "role_type", "source_scope", "source_event_id",
            "canonical_entity_id")
    return [dict(zip(keys, row)) for row in rows]


def _canonical_display_names(c: psycopg.Connection,
                             canonical_ids: set) -> dict:
    """Batched canonical display names for safely linked person ids (§24):
    the canonical name is the primary user-facing identity; the lowercase
    source-normalized name stays in the source disclosure."""
    ids = sorted({i for i in canonical_ids if i is not None})
    names: dict = {}
    if not ids:
        return names
    rows = c.execute(
        "SELECT canonical_entity_id, display_name FROM canonical_entity "
        "WHERE canonical_entity_id = ANY(%s)", (ids,)).fetchall()
    for row in rows:
        if isinstance(row, dict):
            names[row["canonical_entity_id"]] = row["display_name"]
        else:
            names[row[0]] = row[1]
    return names


def _attach_person_links(view: dict, person_links: list[dict],
                         display_names: dict | None = None) -> None:
    """Attach canonical person links to board/management rows and events.

    Company-page rows come from the snapshot value_json; role occurrences
    from company_person_role. A canonical link is attached ONLY when the
    name maps to exactly ONE linked occurrence (same name + same scope) —
    anything else stays plain source text (fail closed).
    """
    display_names = display_names or {}
    for group_key in ("board", "management"):
        for row in view.get(group_key, []):
            normalized = normalize_person_name(row.get("name"))
            if not normalized:
                continue
            matches = {p["canonical_entity_id"] for p in person_links
                       if p["entity_normalized"] == normalized
                       and p["source_scope"] == "CURRENT_AUTHORITATIVE"
                       and p["canonical_entity_id"] is not None}
            if len(matches) == 1:
                row["canonical_entity_id"] = matches.pop()
    # historical events: person names from the same provenance chain
    # (source_event_id); LINKED -> link, otherwise plain text (section 27).
    # Only role-change events render person occurrences — a person name on
    # e.g. a capital or name change would misrepresent the event semantics.
    role_event_types = ("BOARD_CHANGE", "MANAGEMENT_CHANGE", "AUDITOR_CHANGE")
    by_event: dict[Any, list[dict]] = {}
    for p in person_links:
        if p["source_event_id"] is not None:
            by_event.setdefault(p["source_event_id"], []).append(p)
    for event in view.get("history", []):
        if event.get("event_type") not in ROLE_EVENT_LABELS:
            event["persons"] = []
            continue
        src_event = event.get("source_event_id")
        roles = by_event.get(src_event, [])
        seen: set[tuple] = set()
        persons: list[dict] = []
        for p in roles:
            key = (p["person_name"], p["role_type"], p["canonical_entity_id"])
            if key in seen:
                continue
            seen.add(key)
            cid = p["canonical_entity_id"]
            persons.append({
                "name": p["person_name"],
                "display_name": display_names.get(cid),
                "role_title": p["role_title"],
                "role_type": p["role_type"],
                "role_label": (ROLE_TITLE_LABELS.get(p["role_title"])
                               or ROLE_TYPE_LABELS.get(p["role_type"])
                               or p["role_title"] or p["role_type"]),
                "canonical_entity_id": cid})
        event["persons"] = persons


_SNAPSHOT_LABEL = "Registreret status pr."


def skraseting_view(c: psycopg.Connection, canonical_entity_id: int, *,
                    pilot_conn: psycopg.Connection | None = None,
                    integration: bool | None = None,
                    ui: bool | None = None) -> dict:
    """Complete read-only view model for the Skráseting surface.

    Never raises for projection problems: a read failure degrades to the
    local error state while the canonical entity stays untouched.
    """
    view = _base_view()
    view["same_day_note_text"] = None
    if integration is None or ui is None:
        # env switches first: nothing is opened when one is pinned off (D-11)
        view["allowed"] = consumer.ui_exposure_allowed(pilot_conn)
    else:
        view["allowed"] = bool(integration and ui)
    if not view["allowed"]:
        return view  # hard invariant: no projection rendering behind the gate
    # canonical identity bridge: canonical_entity -> registration number
    row = c.execute(
        "SELECT ci.registration_number, ce.display_name "
        "FROM company_inventory ci "
        "JOIN canonical_entity ce ON ce.source_entity_id = "
        "     ci.source_entity_id "
        "WHERE ce.canonical_entity_id = %s "
        "ORDER BY ci.source_system, ci.source_entity_id LIMIT 1",
        (canonical_entity_id,)).fetchone()
    # rows may be dict_row (QA convention) or plain tuples (tests)
    def _cell(r, key, idx):
        if r is None:
            return None
        if isinstance(r, dict):
            return r.get(key)
        return r[idx]
    regnr = (normalize_registration_number(_cell(row, "registration_number", 0))
             if row else None)
    view["canonical_name"] = _cell(row, "display_name", 1)
    if regnr is None:
        return view
    # keep one pilot connection alive for every projection read below
    try:
        with consumer._connect(pilot_conn) as pc:
            state = consumer.get_projected_company_state(regnr, conn=pc)
            if state is not None:
                pv = _projection_view(state)
                view.update({
                    "has_projection": True,
                    "registered_name": pv["registered_name"],
                    "fields": pv["fields"],
                    "board": pv["board"],
                    "management": pv["management"],
                    "snapshot_date": pv["snapshot_date"],
                    "snapshot_date_dk": pv["snapshot_date_dk"],
                    "provenance": pv["provenance"],
                    "same_day_note": pv["same_day_note"],
                })
            history = consumer.get_projected_company_history(
                regnr, conn=pc, integration_enabled=True) or []
            view["history"] = [_event_view(e) for e in history]
            view["history_same_day_note"] = any(
                w["warning_code"] == "SAME_DAY_UNORDERED"
                for w in consumer.get_projection_warnings(regnr, conn=pc))
            # entity links: only safely LINKED persons become clickable
            # (sections 26/27); a read failure keeps everything plain text
            person_links = _load_person_links(pc, regnr)
            linked_ids = {p["canonical_entity_id"] for p in person_links
                          if p.get("canonical_entity_id") is not None}
            display_names = _canonical_display_names(c, linked_ids)
            _attach_person_links(view, person_links, display_names)
    except (psycopg.Error, consumer.ProjectionUnavailable) as exc:
        # section 29: local error state; the company page itself is fine
        _logger.warning("skraseting view degraded: %s", repr(exc))
        view["error"] = True
    return view


# ---------------------------------------------------------------------------
# Person-side section (task sections 28-33): Skráseting evidence on the
# EXISTING Heimdal person page, same exposure gate, fail closed.
# ---------------------------------------------------------------------------

PERSON_SECTION_LABELS = {
    "current_heading": "Registrerede roller",
    "historical_heading": "Historiske registreringer",
    "companies_heading": "Relaterede virksomheder",
    "source_label": SOURCE_LABEL,
    "historical_note": ("Historiske registreringer er historiske kildeoplysninger "
                        "og gør ikke rollen til en aktuel rolle."),
    "auditor_note": ("Historisk revisorregistrering vises som kildeoplysning — "
                     "den er ikke en aktuel rolle."),
}


def skraseting_person_section(c: psycopg.Connection, canonical_entity_id: int, *,
                              pilot_conn: psycopg.Connection | None = None,
                              integration: bool | None = None,
                              ui: bool | None = None) -> dict | None:
    """View model for the Skráseting section on an EXISTING person page.

    Returns None (fail closed) when the gate is closed, the person has no
    safely linked evidence, or any read fails — the person page itself is
    never affected (sections 45/46).
    """
    try:
        with consumer._connect(pilot_conn) as pc:
            if integration is None:
                integration = flags.integration_enabled(pc)
            if ui is None:
                ui = flags.ui_enabled(pc)
            if not (integration and ui):
                return None
            roles = consumer.get_skraseting_roles_for_person(
                canonical_entity_id, conn=pc, integration_enabled=integration)
            if not roles:
                return None
        # canonical display names for the linked companies (existing
        # Heimdal company pages stay the canonical parent, section 30)
        company_ids = sorted({r["company_link"]["canonical_entity_id"]
                              for r in roles["current"] + roles["historical"]
                              if r.get("company_link")})
        company_names: dict[int, str] = {}
        if company_ids:
            rows = c.execute(
                """SELECT canonical_entity_id, display_name FROM canonical_entity
                   WHERE canonical_entity_id = ANY(%s)""",
                (company_ids,)).fetchall()
            for row in rows:
                if isinstance(row, dict):
                    company_names[row["canonical_entity_id"]] = \
                        row["display_name"]
                else:
                    company_names[row[0]] = row[1]
        current, historical = [], []
        for bucket, target in (("current", current), ("historical", historical)):
            for role in roles[bucket]:
                clink = role.get("company_link")
                target.append({
                    "role_title": role["role_title"],
                    "role_type": role["role_type"],
                    "role_label": (ROLE_TYPE_LABELS.get(role["role_type"])
                                   or role["role_type"] or ""),
                    "person_name": role["person_name"],
                    "source_scope": role["source_scope"],
                    "source_document_id": role["source_document_id"],
                    "company": {
                        "canonical_entity_id":
                            clink["canonical_entity_id"] if clink else None,
                        "name": (company_names.get(
                            clink["canonical_entity_id"])
                            if clink else None),
                        "registration_number": (clink["registration_number"]
                                                if clink else None),
                    },
                })
        related = {}
        for entry in current + historical:
            comp = entry["company"]
            if comp["canonical_entity_id"] and comp["name"]:
                related[comp["canonical_entity_id"]] = comp["name"]
        section = dict(PERSON_SECTION_LABELS)
        section.update({
            "canonical_entity_id": canonical_entity_id,
            "current": current,
            "historical": historical,
            "related_companies": [
                {"canonical_entity_id": cid, "name": name}
                for cid, name in sorted(related.items(),
                                        key=lambda kv: (kv[1] or ""))],
            "projection_run_id": (roles["current"] or roles["historical"])[0]
                .get("projection_run_id"),
        })
        return section
    except (psycopg.Error, consumer.ProjectionUnavailable) as exc:
        _logger.warning("skraseting person section degraded: %s", repr(exc))
        return None


# Remediation plan v1 T3.1: binding product vocabulary — Nevnd→Bestyrelse,
# Stjórn→Ledelse. Source terms remain visible in source/evidence disclosures
# (Kilderolle / Vis kilde); these labels are only the product render layer.
ROLE_TYPE_LABELS = {
    "BOARD": "Bestyrelse",
    "MANAGEMENT": "Ledelse",
    "AUDITOR": "Revisor",
}

# role-change event types may render person occurrences (section 27)
ROLE_EVENT_LABELS = {
    "BOARD_CHANGE": EVENT_LABELS["BOARD_CHANGE"],
    "MANAGEMENT_CHANGE": EVENT_LABELS["MANAGEMENT_CHANGE"],
    "AUDITOR_CHANGE": EVENT_LABELS["AUDITOR_CHANGE"],
}

# ---------------------------------------------------------------------------
# Company Overview recent-history summary (product UX consolidation §11/§20).
# Source-supported rows only: the event label comes from the event-type map,
# the action wording ONLY when the source action column says so (APPOINTED/
# REMOVED; CHANGED and unknown stay neutral), and person rows reuse exactly
# the safely-linked occurrence model of the Skráseting history (canonical
# link wins; unresolved keeps the source name as plain text). No inference,
# no second authority, read-only.
# ---------------------------------------------------------------------------

def company_recent_events(c: psycopg.Connection, canonical_entity_id: int,
                          limit: int = 5) -> list[dict]:
    """The most recent meaningful Skráseting history events for the company
    Overview. Same view model as the Skráseting surface (one composition,
    §19/§21 semantics); each row carries source + evidence link fields so the
    overview can never drift from the history page's wording."""
    try:
        with read_savepoint(c):
            view = skraseting_view(c, canonical_entity_id)
    except Exception as exc:  # noqa: BLE001 — overview must never break
        _logger.warning("company recent events degraded: %s", repr(exc))
        return []
    if not view.get("allowed") or not view.get("has_projection"):
        return []
    rows = []
    for e in view.get("history") or []:
        if e.get("timeline_date") is None:
            continue  # an undated event is never "recent"
        persons = [{
            "name": p["name"],
            "display_name": p.get("display_name"),
            "canonical_entity_id": p.get("canonical_entity_id"),
            "role_label": p.get("role_label"),
        } for p in (e.get("persons") or [])]
        rows.append({
            "date_iso": e.get("timeline_date"),
            "date_dk": e.get("timeline_date_dk") or e.get("timeline_date"),
            "title": e.get("type"),
            "action_label": e.get("action_label"),
            "persons": persons,
            "description": (e.get("description")
                            if e.get("event_type") not in
                            ("BOARD_CHANGE", "MANAGEMENT_CHANGE",
                             "AUDITOR_CHANGE") else None),
            "source": SOURCE_LABEL,
            "source_document_id": e.get("source_document_id"),
        })
    # "Seneste" = nyeste først på den enkelte hændelses egen datobasis
    # (ikrafttrædelse eller registrering) — samme dato-semantik som historik-
    # siden; samme-dag-rækkefølge forbliver udokumenteret (§22).
    rows.sort(key=lambda r: str(r["date_iso"]), reverse=True)
    return rows[:limit]
