"""Offline PDF parsers: parse_felag_pdf / parse_lysing_pdf (plan v3.1.2
phases 9-12). Pure functions text -> structured dict. Binding rules:

* lossless: unknown labeled sections land in unknown_sections verbatim; the
  parser may fail to understand, never discard.
* Dagf. is the ONLY source of snapshot_date (never fetched_at fallback).
* role-aware addresses: roles come from section semantics; no postcode
  proximity logic; the Skraaseting Foeroya footer can never become the company
  address.
* all dates stay separate (snapshot/effective/registration/notice/source).
"""
from __future__ import annotations

import re
from datetime import date

from .normalize import (
    normalize_entity_name,
    normalize_registration_number,
    normalize_whitespace,
    parse_date,
    parse_money,
)

# ---------------------------------------------------------------------------
# Section splitting
# ---------------------------------------------------------------------------

# Pseudo-labels that occur in fixtures WITHOUT a trailing colon.
_PSEUDO_LABELS = ("Dagf.", "Skrásetingar-nr.")
_LABEL_RE = re.compile(r"^([^\W\d_][\wÀ-ÿ .,'()\-]{0,58}?)\s*:\s*(.*)$")
# Registry printout footer - may be interleaved into address blocks by text
# order and must never become an attributed address line.
FOOTER_RE = re.compile(
    r"Skr\. nr\.|Skráseting Føroya\s*[•|]|Skráseting Føroya.*(Tlf|tann)|"
    r"Skráseting Føroya hevur|Boks 264")
# NOTE: a bare 'Sigmundargøta' alternative was removed after the 250-company
# validation found a genuine company address ON the registry's own street
# (c6176 Sigmundargøta 32 - its street line was lost to the footer filter).
# Every real footer line in the corpus also carries 'Skráseting Føroya' or
# 'Skr. nr.' (corpus-wide scan, 6 company lines vs 0 bare footer lines).

FELAG_PDF_TYPE = "FelagPdf"
LYSING_PDF_TYPE = "LysingPdf"

SCALAR_FAMILIES = {
    "ADDRESS", "NAME", "CAPITAL", "PURPOSE", "SIGNING_RULES", "COMPANY_FORM",
}
MEMBERSHIP_FAMILIES = {"BOARD", "MANAGEMENT", "AUDITOR"}
EVENT_ONLY_FAMILIES = {"ARTICLES", "OTHER", "HJANOEVN"}


def split_labeled_sections(text: str) -> tuple[list[tuple[str, str]], str]:
    """Split extracted text into (ordered) labeled sections + preamble.

    Labels are known fixture tags (with or without trailing colon, e.g. the
    colon-free 'Dagf. 30-08-2026' and 'Skrásetingar-nr.  P/F 10, ...' lines).
    Continuation lines append to the current section; text before the first
    label is kept as the preamble. Ordered so that verbatim evidence survives
    reconstruction.
    """
    sections: list[tuple[str, str]] = []
    preamble_lines: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        pseudo = next((p for p in _PSEUDO_LABELS if line.startswith(p)), None)
        label_match = _LABEL_RE.match(line)
        op_match = _LYSING_OP_RE.match(line)
        if pseudo:
            value = line[len(pseudo):].strip()
            sections.append((pseudo, value))
        elif op_match:
            # membership action line (Farin ... / Innkomin ... / Nyggjur ...):
            # continues the preceding family section, never a section of its own
            if sections:
                label, body = sections[-1]
                body = (body + "\n" + line) if body else line
                sections[-1] = (label, body)
            else:
                sections.append(("(unsectioned ops)", line))
        elif label_match and label_match.group(1).strip():
            label = label_match.group(1).strip(" .,")
            sections.append((label, label_match.group(2)))
        else:
            if sections:
                label, body = sections[-1]
                body = (body + "\n" + line) if body else line
                sections[-1] = (label, body)
            else:
                preamble_lines.append(line)

    return sections, "\n".join(preamble_lines)


def _section_bodies(text: str) -> tuple[dict[str, str], str]:
    """Collapse the ordered sections into label -> body (later duplicates are
    joined so nothing is lost)."""
    sections, preamble = split_labeled_sections(text)
    bodies: dict[str, str] = {}
    for label, body in sections:
        if label in bodies:
            bodies[label] = bodies[label] + "\n" + body
        else:
            bodies[label] = body
    return bodies, preamble


def section_lines(body: str) -> list[str]:
    """Section body as non-empty lines (footer lines not removed here)."""
    return [line.strip() for line in body.splitlines() if line.strip()]


def _body_without_footer(body: str) -> list[str]:
    return [line for line in section_lines(body)
            if not FOOTER_RE.search(line)]


# ---------------------------------------------------------------------------
# FelagPdf (TEKNINGARUTSKRIFT) - FULL_STATE_SNAPSHOT
# ---------------------------------------------------------------------------

_FELAG_KNOWN_LABELS = {
    "Dagf.", "Skrásetingar-nr", "Navn", "Hjánøvn", "Adressa", "Postrúm",
    "Kommuna", "Nevnd", "Stjórn", "Stjóri", "Partapeningur", "Endamál",
    "Tekningarreglur", "Roknskapir dato",
}

_BOARD_ROLES = ("Nevndarformaður", "Nevndarforkvinna", "Næstformaður",
                "Næstforkvinna", "Nevndarlimur",
                "Starvsfólkavalt umboð", "Varamaður", "Vallarformaður",
                "Starvsfólkavaltar umboð")
_MANAGEMENT_ROLES = ("Stjóri", "Forstjóri", "Kurator", "Stjórnarmaður",
                     "Stjórarmaður", "Stjórnarformaður", "Varamaður")


def _parse_people(section_body: str, address_role: str) -> tuple[list[dict], list[str], list[str]]:
    """Parse person entries from a Nevnd/Stjorn section.

    A person line starts with a known role prefix; following non-role,
    non-footer lines belong to that person's address. Returns
    (people, leftover_lines, roles_that_were_missing).
    """
    people: list[dict] = []
    leftovers: list[str] = []
    unknown_roles: list[str] = []
    all_roles = _BOARD_ROLES + _MANAGEMENT_ROLES

    for line in _body_without_footer(section_body):
        if not line:
            continue
        role = next((prefix for prefix in all_roles if line.startswith(prefix)),
                    None)
        if role:
            people.append({
                "role": role,
                "name": normalize_whitespace(line[len(role):]) or None,
                "address": {
                    "role": address_role,
                    "verbatim": "",
                    "lines": [],
                },
            })
        elif people:
            current = people[-1]
            current["address"]["lines"].append(line)
            current["address"]["verbatim"] = "\n".join(current["address"]["lines"])
        else:
            leftovers.append(line)
    return people, leftovers, unknown_roles


def _parse_company_address(body: str) -> dict | None:
    """Adressa section -> company address (role = COMPANY_ADDRESS).

    Layout from real fixtures:
      Adressa: Oknarvegur 5      <- street (value on the label line)
      Postrúm 3048               <- PO box
      110 Tórshavn               <- 'postal city'
    """
    lines = _body_without_footer(body)
    if not lines and not section_lines(body):
        return None
    address: dict = {"role": "COMPANY_ADDRESS", "verbatim": "",
                     "lines": lines, "street": None, "postal_code": None,
                     "city": None, "postrum": None, "kommuna": None}
    for line in lines:
        match = re.match(r"^Postrúm\s*:?\s*(\d+)\s*$", line)
        if match:
            address["postrum"] = match.group(1)
        match = re.match(r"^(\d{3})\s+(\S.*)$", line)
        if match:
            address["postal_code"] = match.group(1)
            address["city"] = normalize_whitespace(match.group(2))
    if lines:
        address["street"] = lines[0] if not re.match(r"^\d{3}\s", lines[0]) \
            else None
    address["verbatim"] = "\n".join(lines)
    return address


def parse_felag_pdf(text: str) -> dict:
    """Parse a FelagPdf printout into a COMPANY_SNAPSHOT dict.

    Lossless: every labeled section the parser does not structure lands in
    unknown_sections[] verbatim. Dagf. -> snapshot_date or SNAPSHOT_DATE_MISSING
    review; fetched_at is never used as a date fallback.
    """
    bodies, preamble = _section_bodies(text)
    snapshot: dict = {
        "document_kind": "COMPANY_SNAPSHOT",
        "registration_number": None,
        "company_name": None,
        "snapshot_date": None,
        "company_address": None,
        "board": [],
        "management": [],
        "capital": None,
        "purpose": None,
        "signing_rules": None,
        "accounts_dates": [],
        "alias_names": None,
        "unknown_sections": [],
        "dates": {
            "snapshot_date": None, "document_date": None,
            "registration_date": None, "publication_date": None,
            "effective_date": None,
        },
    }
    review: list[str] = []

    if not text.strip():
        review.append("EMPTY_TEXT_LAYER")
    elif not any(label in bodies for label in _FELAG_KNOWN_LABELS):
        review.append("LAYOUT_DRIFT")

    dagf_body = bodies.get("Dagf.", "")
    parsed = parse_date(dagf_body)
    if parsed:
        snapshot["snapshot_date"] = parsed.isoformat()
        snapshot["dates"]["snapshot_date"] = parsed.isoformat()
    else:
        review.append("SNAPSHOT_DATE_MISSING")

    regnr_value = next(
        (bodies[key] for key in
         ("Skrásetingar-nr.", "Skrásetingar-nr", "Skrasetingar-nr.",
          "Skrasetingar-nr")
         if key in bodies), None)
    regnr = normalize_registration_number(regnr_value)
    snapshot["registration_number"] = regnr
    if not regnr:
        review.append("REGISTRATION_NUMBER_MISSING")

    navn = bodies.get("Navn")
    if navn:
        snapshot["company_name"] = normalize_whitespace(navn.splitlines()[0])
    hjanoevn = bodies.get("Hjánøvn")
    if hjanoevn:
        snapshot["alias_names"] = section_lines(hjanoevn)

    address_body = bodies.get("Adressa")
    if address_body is not None:
        snapshot["company_address"] = _parse_company_address(address_body)
        kommuna = bodies.get("Kommuna")
        if kommuna and snapshot["company_address"]:
            snapshot["company_address"]["kommuna"] = \
                normalize_whitespace(kommuna.splitlines()[0])

    board, board_left, _ = _parse_people(bodies.get("Nevnd", ""),
                                         "BOARD_MEMBER_ADDRESS")
    snapshot["board"] = board
    # live layout variant (pilot, c8664 ÍVF Trolloftið): single-director
    # companies print a direct 'Stjóri:' section with no 'Stjórn:' wrapper
    management_body = bodies.get("Stjórn")
    if management_body is None:
        management_body = bodies.get("Stjóri")
    management, mgmt_left, _ = _parse_people(management_body or "",
                                             "MANAGEMENT_ADDRESS")
    snapshot["management"] = management

    capital_body = bodies.get("Partapeningur")
    if capital_body:
        currency, amount = parse_money(capital_body)
        snapshot["capital"] = {"currency": currency, "amount": amount,
                               "verbatim": normalize_whitespace(capital_body)}
    purpose = bodies.get("Endamál")
    if purpose:
        snapshot["purpose"] = normalize_whitespace(purpose)
    signing = bodies.get("Tekningarreglur")
    if signing:
        snapshot["signing_rules"] = normalize_whitespace(signing)
    accounts = bodies.get("Roknskapir dato")
    if accounts:
        parsed_dates = []
        for token in accounts.split(","):
            parsed = parse_date(token)
            if parsed:
                parsed_dates.append(parsed.isoformat())
        snapshot["accounts_dates"] = parsed_dates

    known = _FELAG_KNOWN_LABELS
    for label, body in [(l, b) for l, b in [(k, bodies[k]) for k in bodies]
                        if l not in known]:
        snapshot["unknown_sections"].append(
            {"label": label, "verbatim": body})
    if preamble.strip():
        snapshot["unknown_sections"].append(
            {"label": "(preamble)", "verbatim": preamble})
    if board_left:
        snapshot["unknown_sections"].append(
            {"label": "(Nevnd usorterede linjer)", "verbatim": "\n".join(board_left)})
    if mgmt_left:
        snapshot["unknown_sections"].append(
            {"label": "(Stjórn usorterede linjer)", "verbatim": "\n".join(mgmt_left)})

    snapshot["needs_review"] = bool(review)
    snapshot["review_reasons"] = review
    return snapshot


# ---------------------------------------------------------------------------
# LysingPdf - DELTA / CHANGE NOTICE (0..N changes)
# ---------------------------------------------------------------------------

_LYSING_KNOWN_LABELS = {
    "Skrásetingar-nr.", "Navn", "Hjánøvn", "Nevnd", "Stjórn", "Grannskoðari",
    "Fráboðari", "Viðtøkur broyttar", "Partapeningur", "Endamál",
    "Tekningarreglur", "Felagsslag",
}

# action-prefix table: section-label -> change type; each op line declares the
# action itself (APPOINTED/REMOVED), so one PDF yields 0..N change entries.
LYSING_OP_TABLE = {
    "Farin úr nevndini":            ("BOARD_CHANGE", "REMOVED"),
    "Innkomin í nevndina":          ("BOARD_CHANGE", "APPOINTED"),
    "Farin úr nevndinum":           ("BOARD_CHANGE", "REMOVED"),
    "Innkomin sum nevndarformaður": ("BOARD_CHANGE", "APPOINTED"),
    "Farin frá sum nevndarformaður": ("BOARD_CHANGE", "REMOVED"),
    "Innkomin sum næstformaður":    ("BOARD_CHANGE", "APPOINTED"),
    "Farin frá sum næstformaður":   ("BOARD_CHANGE", "REMOVED"),
    "Innkomin sum nevndarforkvinna": ("BOARD_CHANGE", "APPOINTED"),
    "Farin frá sum nevndarforkvinna": ("BOARD_CHANGE", "REMOVED"),
    "Innkomin sum næstforkvinna":   ("BOARD_CHANGE", "APPOINTED"),
    "Farin frá sum næstforkvinna":  ("BOARD_CHANGE", "REMOVED"),
    "Farin frá sum stjóri":         ("MANAGEMENT_CHANGE", "REMOVED"),
    "Innkomin sum stjóri":          ("MANAGEMENT_CHANGE", "APPOINTED"),
    "Farin frá sum forstjóri":      ("MANAGEMENT_CHANGE", "REMOVED"),
    "Innkomin sum forstjóri":       ("MANAGEMENT_CHANGE", "APPOINTED"),
    "Farin frá sum grannskoðari":   ("AUDITOR_CHANGE", "REMOVED"),
    "Nýggjur grannskoðari":         ("AUDITOR_CHANGE", "APPOINTED"),
    # live 250-expansion: deputy (varalimur) membership actions (c200/30810);
    # before these existed the deputy line opened its own unknown section and
    # swallowed the FOLLOWING known op line (appointments were lost from
    # changes[] while raw text stayed lossless)
    "Farin frá sum varalimur":      ("BOARD_CHANGE", "REMOVED"),
    "Innkomin sum varalimur":       ("BOARD_CHANGE", "APPOINTED"),
    # 250-expansion: 'ábyrgdarhavandi' (person in charge, e.g. the probate
    # court under winding-up) is a Stjórn-level role - live glue line
    # 'Stjóri  Innkomin sum ábyrgdarhavandi:' (c6918/33662) proves the family
    "Innkomin sum ábyrgdarhavandi": ("MANAGEMENT_CHANGE", "APPOINTED"),
    "Farin frá sum ábyrgdarhavandi": ("MANAGEMENT_CHANGE", "REMOVED"),
}
_LYSING_OP_RE = re.compile(
    "^(" + "|".join(sorted(LYSING_OP_TABLE, key=len, reverse=True)) + r")\s*:\s*(.*)$")
# A member line that already closes with a '(rolle)' parenthetical is another
# entity listed under the same action prefix (real fixture: two 'Innkomin' lines).
_MEMBER_RE = re.compile(r"\([^()]*\)\s*$")

SCALAR_LYSING_LABELS = {
    "Navn": "NAME_CHANGE", "Partapeningur": "CAPITAL_CHANGE",
    "Endamál": "PURPOSE_CHANGE", "Tekningarreglur": "SIGNING_RULE_CHANGE",
    "Felagsslag": "COMPANY_FORM_CHANGE",
}
LYSING_MEMBERSHIP_LABELS = {"Nevnd": "BOARD_CHANGE", "Stjórn": "MANAGEMENT_CHANGE",
                            "Stjóri": "MANAGEMENT_CHANGE",
                            "Grannskoðari": "AUDITOR_CHANGE"}


def _normalize_extraction_artifacts(text: str) -> str:
    """Live-source letter-split artifacts seen in 100-company validation
    (~14 docs 'E ndamál', ~6 docs 'næ stformaður'): narrow, unambiguous
    joins only - never a general fuzzy normalizer."""
    text = re.sub(r"E ndamál", "Endamál", text)
    text = re.sub(r"næ st(?=f)", "næst", text)
    # D-06: stray glyph in the op prefix (lys_38088) - 3 board changes lost
    text = re.sub(r"InnkomSin(?= í nevndina)", "Innkomin", text)
    # D-06: a bare family label line without its colon (lys_5053 'Nevnd  ')
    # must still open its membership section
    text = re.sub(r"(?m)^(Nevnd|Stjórn|Stjóri|Grannskoðari)[ \t]*$", r"\1:",
                  text)
    return text


def _split_glued_membership_labels(text: str) -> str:
    """Live layouts glue a membership family label (without its colon) to the
    action phrase on one line, e.g. 'Nevnd  Farin úr nevndini: ...'
    (c2400) or 'Stjórn  Innkomin sum stjóri: ...' (c5272). Without the split
    the label regex swallows both and the change is lost. Only fires on a
    KNOWN family + KNOWN action + colon - deterministic, no guessing."""
    families = "Nevnd|Stjórn|Grannskoðari|Stjóri"
    actions = "|".join(re.escape(name) for name in
                      sorted(LYSING_OP_TABLE, key=len, reverse=True))
    pattern = re.compile(
        r"(?m)(?:^|(?<=[^\n]))[ \t]*(" + families + r")[ \t]{2,}("
        + actions + r")[ \t]*:")
    return pattern.sub(r"\n\1:\n\2:", text)


def _entity_from_text(text_value: str) -> str | None:
    """Entity name = text before the first comma (fixture pattern:
    'Name, street, 123 city (role)')."""
    head = text_value.split(",", 1)[0]
    head = normalize_whitespace(head)
    return head or None


def _membership_change_entries(label: str, body: str,
                               notice_date: str | None,
                               registration_date: str | None) -> list[dict]:
    """Turn a membership section (Nevnd / Stjorn / Grannskoari) into change
    entries. Every action-prefixed line opens an entry; lines that close with a
    '(rolle)' parenthetical are ADDITIONAL entities under the SAME action
    (real fixture: two 'Innkomin' lines); anything else continues the current
    entity text. All text is kept verbatim in raw_text."""
    change_type = LYSING_MEMBERSHIP_LABELS[label]
    entries: list[dict] = []
    leftovers: list[str] = []
    current: dict | None = None
    for line in _body_without_footer(body):
        op_match = _LYSING_OP_RE.match(line)
        if op_match:
            action_prefix = op_match.group(1)
            value = op_match.group(2)
            change_type_op, action = LYSING_OP_TABLE[action_prefix]
            effective = parse_date(value)
            entity = _entity_from_text(value)
            current = {
                "type": change_type_op,
                "action": action,
                "entity": entity,
                "entity_normalized": normalize_entity_name(entity),
                "effective_date": effective.isoformat() if effective else None,
                "registration_date": registration_date,
                "notice_date": notice_date,
                "raw_text": normalize_whitespace(line),
            }
            entries.append(current)
        elif current is None:
            # line before any action prefix in this section: it belongs to no
            # entity yet - keep it verbatim as leftover evidence (lossless)
            leftovers.append(line)
        elif _MEMBER_RE.search(line) and not line.startswith("("):
            # a line that is ONLY '(rolle)' is the wrapped tail of the
            # previous member (D-06: 298 '(nevndarlimur)' entities in run 33)
            entity = _entity_from_text(line.strip())
            entries.append({
                "type": current["type"],
                "action": current["action"],
                "entity": entity,
                "entity_normalized": normalize_entity_name(entity),
                "effective_date": current["effective_date"],
                "registration_date": registration_date,
                "notice_date": notice_date,
                "raw_text": normalize_whitespace(line),
            })
        else:
            # plain continuation (e.g. city wrapped onto the next line):
            # raw evidence grows, the entity itself is NOT recomputed
            current["raw_text"] = normalize_whitespace(
                current["raw_text"] + " " + line)
    return entries, leftovers



_BARE_DATE_RE = re.compile(r"\d{1,2}[.-]\d{1,2}[.-]\d{4}")
# the journal number may be glued on: 'Tórshavn 16.12.201616-844J. nr.'
_SIGNED_DATE_RE = re.compile(r"\bTórshavn[ \t]+(\d{1,2}\.\d{1,2}\.\d{4})")


def _lysing_header_dates(text: str) -> tuple[date | None, date | None]:
    """(notice_date, registration_date). notice_date = the bare DD.MM.YYYY
    line of the letter head (first 12 lines), else the 'Tórshavn DD.MM.YYYY'
    signature line of a founding notice - never a labeled line such as
    'Stovningardato: ...' (D-06: lys_5053 gave the 1977 founding date for a
    2012 notice). registration_date = 'Skráseting Føroya tann DD. <month>
    YYYY'. Kept strictly separate - never merged into one event date."""
    notice = next((parse_date(line) for line in text.splitlines()[:12]
                   if _BARE_DATE_RE.fullmatch(line.strip())), None)
    signed = _SIGNED_DATE_RE.search(text) if notice is None else None
    if signed:
        notice = parse_date(signed.group(1))
    registration = None
    reg_match = re.search(
        r"Skráseting Føroya tann\s+(\d{1,2})\.\s*([A-Za-zÀ-ÿ]+)\s+(\d{4})", text)
    if reg_match:
        registration = parse_date(reg_match.group(0))
    return notice, registration


def _lysing_journal_number(text: str) -> str | None:
    journal = re.search(r"(\d{1,3}-\d{2,5})\s*J\. nr\.", text)
    return journal.group(1) if journal else None


def _lysing_registration_number(sections) -> tuple[str | None, str | None]:
    """(regnr, company_name) from the 'Skrásetingar-nr.' pseudo-label line."""
    for label, body in sections:
        if label == "Skrásetingar-nr.":
            first_line = body.strip()
            regnr = normalize_registration_number(first_line.split(",", 1)[0])
            name = None
            if "," in first_line:
                name = _entity_from_text(first_line.split(",", 1)[1])
            return regnr, name
    return None, None


def parse_lysing_pdf(text: str, source_event_date: str | None = None) -> dict:
    """Parse a LysingPdf notice into a REGISTRATION_NOTICE dict with 0..N
    changes[]. Lossless: unknown labeled sections are kept verbatim in
    unknown_sections[]; footer/registry text never enters any address role.
    """
    text = _normalize_extraction_artifacts(text)
    # the one-line gate is decided on the source text BEFORE the glue-split
    # repair adds its own newlines (see normalize_oneline_lysing_text)
    oneline_gate = text.count("\n")
    text = _split_glued_membership_labels(text)
    text = normalize_oneline_lysing_text(text, gate_newlines=oneline_gate)
    sections, preamble = split_labeled_sections(text)
    notice_date, registration_date = _lysing_header_dates(text)
    journal_number = _lysing_journal_number(text)
    regnr, company_name = _lysing_registration_number(sections)

    notice: dict = {
        "document_kind": "REGISTRATION_NOTICE",
        "registration_number": regnr,
        "company_name": company_name,
        "notice_date": notice_date.isoformat() if notice_date else None,
        "registration_date": registration_date.isoformat() if registration_date else None,
        "journal_number": journal_number,
        "reporter": None,
        "changes": [],
        "unknown_sections": [],
        "dates": {
            "snapshot_date": None,
            "document_date": None,
            "registration_date": registration_date.isoformat() if registration_date else None,
            "publication_date": None,
            "effective_date": None,
        },
    }
    review: list[str] = []
    unsorted = False
    for label, body in sections:
        if label in LYSING_MEMBERSHIP_LABELS:
            entries, leftover_lines = _membership_change_entries(
                label, body,
                notice_date.isoformat() if notice_date else None,
                registration_date.isoformat() if registration_date else None)
            notice["changes"].extend(entries)
            if leftover_lines:
                unsorted = True
                notice["unknown_sections"].append({
                    "label": f"({label} usorterede linjer)",
                    "verbatim": "\n".join(leftover_lines)})
        elif label == "Viðtøkur broyttar":
            effective = parse_date(body)
            notice["changes"].append({
                "type": "ARTICLES_CHANGE",
                "action": "CHANGED",
                "entity": None,
                "entity_normalized": None,
                "effective_date": effective.isoformat() if effective else None,
                "registration_date": registration_date.isoformat() if registration_date else None,
                "notice_date": notice_date.isoformat() if notice_date else None,
                "raw_text": normalize_whitespace(label + ": " + body),
            })
        elif label in SCALAR_LYSING_LABELS:
            value_lines = section_lines(body)
            value = value_lines[0] if value_lines else ""
            sub_match = re.match(
                r"^Nýtt(?: navn| hjánøvn| ur|t)?\s*:\s*(.*)$", value)
            if sub_match:
                value = sub_match.group(1)
            notice["changes"].append({
                "type": SCALAR_LYSING_LABELS[label],
                "action": "CHANGED",
                "entity": normalize_whitespace(value),
                "entity_normalized": normalize_entity_name(value),
                "effective_date": None,
                "registration_date": registration_date.isoformat() if registration_date else None,
                "notice_date": notice_date.isoformat() if notice_date else None,
                "raw_text": normalize_whitespace(label + ": " + body),
            })
        elif label == "Hjánøvn":
            value_lines = section_lines(body)
            value = value_lines[0] if value_lines else ""
            sub_match = re.match(r"^Ný(?:tt|ggj|ggjur|jtt)\b\s*[^:\n]*:\s*(.*)$",
                                 value)
            if sub_match:
                value = sub_match.group(1)
            notice["changes"].append({
                "type": "OTHER_CHANGE",
                "action": "CHANGED",
                "entity": normalize_whitespace(value),
                "entity_normalized": normalize_entity_name(value),
                "effective_date": None,
                "registration_date": registration_date.isoformat() if registration_date else None,
                "notice_date": notice_date.isoformat() if notice_date else None,
                "raw_text": normalize_whitespace(label + ": " + body),
            })
        elif label in ("Nýtt hjánavn", "Nýggj hjánavn", "Strikað hjánavn",
                       "Nýtt hjánøvn", "Nýggj hjánøvn", "Strikað hjánøvn",
                       "Nýtt navn", "Nýgg navn"):
            # standalone alias/name add/change labels (live c34 et al.;
            # ø-variants + 'Nýtt navn:' from 250-expansion c67/4762 and the
            # source's own 'Nýgg navn:' spelling variant (c8672/38088))
            value_lines = section_lines(body)
            value = value_lines[0] if value_lines else ""
            notice["changes"].append({
                "type": ("NAME_CHANGE" if label in ("Nýtt navn", "Nýgg navn")
                         else "OTHER_CHANGE"),
                "action": "CHANGED",
                "entity": normalize_whitespace(value),
                "entity_normalized": normalize_entity_name(value),
                "effective_date": None,
                "registration_date": registration_date.isoformat() if registration_date else None,
                "notice_date": notice_date.isoformat() if notice_date else None,
                "raw_text": normalize_whitespace(label + ": " + body),
            })
        elif label == "Skrásetingar-nr.":
            pass  # parsed by _lysing_registration_number
        elif label == "Fráboðari":
            lines = _body_without_footer(body)
            if lines:
                notice["reporter"] = {
                    "name": _entity_from_text(normalize_whitespace(lines[0])),
                    "address": {
                        "role": "REPORTER_ADDRESS",
                        "verbatim": "\n".join(lines),
                    },
                }
        else:
            notice["unknown_sections"].append(
                {"label": label, "verbatim": body})

    if preamble.strip():
        notice["unknown_sections"].append(
            {"label": "(preamble)", "verbatim": preamble})
    # D-06: decided only after changes/reporter are filled - a notice the
    # parser could not turn into a single change or reporter is layout drift
    if not text.strip():
        review.append("EMPTY_TEXT_LAYER")
    elif not (notice["changes"] or notice["reporter"]):
        review.append("LAYOUT_DRIFT")
    if not regnr:
        review.append("REGISTRATION_NUMBER_MISSING")
    # D-06: a membership line outside changes[] is a possibly lost change
    if unsorted:
        review.append("MEMBERSHIP_UNSORTED")

    notice["needs_review"] = bool(review)
    notice["review_reasons"] = review
    return notice


# ---------------------------------------------------------------------------
# Live 2018-2020 oneline LysingPdf layout (pilot discovery, 931 + 5019)
# ---------------------------------------------------------------------------
# In these PDFs the whole notice body is ONE physical line: labels run
# together ('...Syðrugøta.Nevnd:  Innkomin í nevndina: ...'). The line-based
# section splitter cannot read that, so the parse fails closed (LAYOUT_DRIFT
# + REGISTRATION_NUMBER_MISSING). normalize_oneline_lysing_text() inserts
# newlines before KNOWN labels/anchors, and ONLY for clearly single-line
# texts; every other layout passes through byte-identical.

_ONELINE_MAX_NEWLINES = 8
_ONELINE_SPLIT_NO_COLON = ("Skrásetingar-nr.", "Skráseting Føroya tann")
_ONELINE_SPLIT_WITH_COLON = (
    "Nýggjur grannskoðari", "Farin frá sum grannskoðari",
    "Innkomin í nevndina", "Farin úr nevndini", "Innkomin sum stjóri",
    "Farin frá sum stjóri", "Nýtt navn", "Nýggj hjánøvn",
    "Innkomin sum nevndarformaður", "Farin frá sum nevndarformaður",
    "Farin frá sum varalimur", "Innkomin sum varalimur",
    "Innkomin sum ábyrgdarhavandi", "Farin frá sum ábyrgdarhavandi",
    "Innkomin sum næstformaður", "Farin frá sum næstformaður",
    "Innkomin sum nevndarforkvinna", "Farin frá sum nevndarforkvinna",
    "Innkomin sum næstforkvinna",
    "Farin frá sum næstforkvinna", "Innkomin sum forstjóri",
    "Farin frá sum forstjóri",
    "Viðtøkur broyttar", "Fráboðari", "Grannskoðari",
    "Nevnd", "Stjórn", "Stjóri", "Navn", "Adressa", "Kommuna", "Hjánøvn",
    "Partapeningur", "Kapitalur", "Tekningarreglur", "Endamál",
    "Stovningardato", "Viðtøkudato", "Stovnarar", "Inngjaldingarháttur",
    "Roknskaparár", "Fyrsta roknskaparár",
)


def normalize_oneline_lysing_text(text: str,
                                  gate_newlines: int | None = None) -> str:
    """See block comment above. Mechanical, label-anchored newline injection
    for the single-line notice layout; any text with normal line structure is
    returned unchanged.

    gate_newlines: newline count measured BEFORE
    _split_glued_membership_labels() runs. The glue-split inserts newlines as
    part of its own repair; those repair lines must never push an otherwise
    single-line notice (mechanism: '...fráboðan:Skrásetingar-nr. ...' glued
    behind the preamble, live c5334/c5273) past this gate - if they did, the
    regnr pseudo-label would stay glued and the document would fail closed
    as LAYOUT_DRIFT. None keeps the old behaviour (count on the passed text)."""
    if (text.count("\n") if gate_newlines is None else gate_newlines) \
            > _ONELINE_MAX_NEWLINES:
        return text
    if "Skrásetingar-nr" not in text:
        return text
    for token in _ONELINE_SPLIT_NO_COLON:
        text = re.sub(r"(?<=[^\n])\s*" + re.escape(token),
                      "\n" + token, text)
    labels = "|".join(re.escape(name) for name in
                      sorted(set(_ONELINE_SPLIT_WITH_COLON),
                             key=len, reverse=True))
    text = re.sub(r"(?<=[^\n])\s*(" + labels + r")\s*:", r"\n\1:", text)
    # Reunite a bare parent scalar label with its immediately following
    # 'Nýtt navn:'/'Nýggj hjánøvn:' opener. The multiline convention (real
    # fixtures: 79 docs) treats that opener as the parent's VALUE (see the
    # Nýtt-prefixed sub_match in parse_lysing_pdf); the split above otherwise
    # detaches it into its own section and the scalar keeps an empty entity
    # (live 21917/26980/24340). Adjacency-anchored, oneline-scoped only.
    text = re.sub(r"(?m)^(Navn:)\s*\n(Nýtt navn:)", r"\1 \2", text)
    text = re.sub(r"(?m)^(Hjánøvn:)\s*\n(Nýggj hjánøvn:)", r"\1 \2", text)
    return text
