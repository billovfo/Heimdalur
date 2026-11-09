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
_MANAGEMENT_ROLES = ("Stjóri", "Forstjóri", "Kurator", "Likvidator",
                     "Stjórnarmaður", "Stjórarmaður", "Stjórnarformaður",
                     "Varamaður")
# Notice-only role tails that LYSING_OP_TABLE also knows; a founding member
# line may close with any of them (e.g. '(varalimur)', '(ábyrgdarhavandi)').
_NOTICE_ROLE_TAILS = ("Varalimur", "Ábyrgdarhavandi", "Grannskoðari")
# Member lines in a founding notice close with the register's own lowercase
# role parenthetical, e.g. "(stjóri)" / "(nevndarlimur)".
_FOUNDING_ROLE_TAILS = {role.casefold()
                        for role in _BOARD_ROLES + _MANAGEMENT_ROLES
                        + _NOTICE_ROLE_TAILS}
_FOUNDING_ROLE_SECTIONS = {"Nevnd", "Stjórn", "Stjóri", "Umsjónarráð"}


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
    # merger section labels; 'Fusoín' is the source's own typo of the label
    # itself (corpus obs_3163) and is accepted ONLY as a merger label.
    "Fusión", "Fusiónslýsing", "Fusoín",
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
    # winding-up/trustee appointments: same Stjórn-level family as
    # 'ábyrgdarhavandi', with the source title carried as the change role.
    "Innkomin sum likvidator":      ("MANAGEMENT_CHANGE", "APPOINTED"),
    "Farin frá sum likvidator":     ("MANAGEMENT_CHANGE", "REMOVED"),
    "Innkomin sum kurator":         ("MANAGEMENT_CHANGE", "APPOINTED"),
    "Farin frá sum kurator":        ("MANAGEMENT_CHANGE", "REMOVED"),
    "Innkomin sum grannskoðari":    ("AUDITOR_CHANGE", "APPOINTED"),
    "Farin úr nevndini sum nevndarformaður": ("BOARD_CHANGE", "REMOVED"),
    # savings-bank supervisory board (Umsjónarráð)
    "Farin úr umsjónarráðnum":      ("BOARD_CHANGE", "REMOVED"),
    "Innkomin í umsjónarráðið":     ("BOARD_CHANGE", "APPOINTED"),
}
# source title carried on the change (projection uses it as role_title)
_LYSING_OP_ROLES = {
    "Innkomin sum likvidator": "Likvidator",
    "Farin frá sum likvidator": "Likvidator",
    "Innkomin sum kurator": "Kurator",
    "Farin frá sum kurator": "Kurator",
}
_LYSING_OP_RE = re.compile(
    "^(" + "|".join(sorted(LYSING_OP_TABLE, key=len, reverse=True)) + r")\s*:\s*(.*)$")
# A member line that already closes with a '(rolle)' parenthetical is another
# entity listed under the same action prefix (real fixture: two 'Innkomin' lines).
_MEMBER_RE = re.compile(r"\(([^()]*)\)\s*$")

SCALAR_LYSING_LABELS = {
    "Navn": "NAME_CHANGE", "Partapeningur": "CAPITAL_CHANGE",
    "Endamál": "PURPOSE_CHANGE", "Tekningarreglur": "SIGNING_RULE_CHANGE",
    "Felagsslag": "COMPANY_FORM_CHANGE",
}
LYSING_MEMBERSHIP_LABELS = {"Nevnd": "BOARD_CHANGE", "Stjórn": "MANAGEMENT_CHANGE",
                            "Stjóri": "MANAGEMENT_CHANGE",
                            "Grannskoðari": "AUDITOR_CHANGE",
                            # savings-bank supervisory board: board-level
                            # membership actions under its own section label.
                            "Umsjónarráð": "BOARD_CHANGE"}

# E2: initial members of a founding notice (Stovningardato) become APPOINTED
# history entries dated by the founding date, marked with this origin. The UI
# renders the marker as "ved stiftelse".
FOUNDING_ORIGIN = "FOUNDING"
_FOUNDING_DATE_LABEL = "Stovningardato"

# Field/section labels that can never be a member name. A founding
# Grannskoðari body may still print a scalar line ('Lutapeningur, kr.'); the
# single-token auditor exception must not turn such a label into a firm.
_NON_MEMBER_HEAD_LABELS = {
    label.strip(" .").casefold()
    for label in (set(_FELAG_KNOWN_LABELS) | set(_LYSING_KNOWN_LABELS)
                  | set(SCALAR_LYSING_LABELS)
                  | set(LYSING_MEMBERSHIP_LABELS)
                  | {"Lutapeningur", "Kapitalur", "Stovningardato",
                     "Viðtøkudato", "Stovnarar", "Inngjaldingarháttur",
                     "Roknskaparár", "Fyrsta roknskaparár", "Adressa",
                     "Kommuna", "Postrúm", "Postbox"})
}
# An address tail carries a postal code, a PO box or a street number; the
# one-token auditor exception needs it so a place-only wrapped line
# ('Søborg, Danmark') or a date ('Tórshavn, 12. mars 2021') is never a firm.
_ADDRESS_TAIL_RE = re.compile(
    r"\b\d{3,4}\b"                    # Faroese/Danish/Norwegian postal code
    r"|\b(?:Postbox|Postrúm|Box)\b"   # PO box marker
    r"|\b\d{1,4}[A-Za-z]?\b")         # street/house number
_MONTH_NAME_RE = re.compile(
    r"\b(januar|februar|mars|apr[íi]l|mai|juni|juli|august|september|"
    r"oktober|november|desember)\b", re.IGNORECASE)
_SHORT_DATE_RE = re.compile(
    r"^\s*\d{1,2}\s*[.\-/]\s*\d{1,2}(?:\s*[.\-/]\s*\d{2,4})?\s*$")


def _looks_like_date(text_value: str) -> bool:
    """A source date in a member tail ('12. mars 2021', '12.03.2021')."""
    return bool(parse_date(text_value) or _MONTH_NAME_RE.search(text_value)
                or _SHORT_DATE_RE.match(text_value))


def _has_address_tail(line: str) -> bool:
    """True when the text after the member-name comma looks like an address.

    Deliberately narrow: used only for one-word auditor heads, where a bare
    capitalized token could otherwise be a place, a scalar label or a date.
    """
    tail = normalize_whitespace(line.split(",", 1)[1])
    if not tail or _looks_like_date(tail):
        return False
    if not any(ch.isalpha() for ch in tail):
        return False
    return bool(_ADDRESS_TAIL_RE.search(tail))


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
    # D-06/E2: the same for the bare share-capital label ('Lutapeningur
    # kr.', lys_5053); without the split it glues onto the preceding
    # Grannskoðari section and becomes a false unsorted membership line.
    text = re.sub(r"(?m)^(Lutapeningur)[ \t]+(?=kr\.)", r"\1: ", text)
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


def _initial_member_entity(line: str, label: str) -> str | None:
    """Entity on an initial (action-prefix-free) founding member line.

    The register's one-line member layout is 'Name, address, ... (role)'.
    A line without that comma layout is not a member line - it stays leftover
    evidence (section header, stray scalar label, wrapped address). Board and
    management lines must also close with a known role parenthetical, and the
    head must look like a name/company (no digits, 2+ tokens, capitalised).
    A single capitalised alphabetic token is accepted only for the
    Grannskoðari/AUDITOR_CHANGE section (a legitimate one-word audit firm),
    never for board or management - and only when the text after its comma
    looks like a postal address and the head is not a known label.
    No pseudo persons: an entity that starts with '(' is never created.
    """
    if "," not in line:
        return None
    if label in _FOUNDING_ROLE_SECTIONS:
        role_match = _MEMBER_RE.search(line)
        if role_match is None:
            return None
        if normalize_whitespace(role_match.group(1)).casefold() \
                not in _FOUNDING_ROLE_TAILS:
            return None
    entity = _entity_from_text(line)
    member_tail = _MEMBER_RE.search(entity) if entity else None
    if member_tail and normalize_whitespace(member_tail.group(1)).casefold() \
            in _FOUNDING_ROLE_TAILS:
        # strip a trailing parenthetical from the name head only when it is a
        # known role tail - a company alias parenthetical must survive
        entity = normalize_whitespace(_MEMBER_RE.sub("", entity))
    if not entity or entity.startswith("("):
        return None
    if not any(ch.isalpha() for ch in entity):
        return None
    if any(ch.isdigit() for ch in entity):
        return None
    tokens = entity.split()
    if len(tokens) < 2:
        if (label != "Grannskoðari" or len(tokens) != 1
                or not entity.isalpha() or not entity[0].isupper()):
            return None
        # NEW-1: the one-token exception is for real audit firms only. A
        # scalar/section label ('Lutapeningur, kr.') is never a head, and the
        # tail after the comma must look like an address - not a date and not
        # a place/country-only wrapped line ('Søborg, Danmark').
        if entity.strip(" .").casefold() in _NON_MEMBER_HEAD_LABELS:
            return None
        if not _has_address_tail(line):
            return None
    elif not entity[0].isupper():
        return None
    return entity


def _initial_member_entries(label: str, change_type: str, lines: list[str],
                            founding_date: str, notice_date: str | None,
                            registration_date: str | None
                            ) -> tuple[list[dict], list[str]]:
    """Turn the action-prefix-free lines of a founding member section into
    APPOINTED history entries dated by Stovningardato.

    In a founding Grannskoðari section a line that continues the previous
    line (the previous line ends with ',' or this line starts lower-case) is
    a wrapped address/firm-description continuation, never a second audit
    firm. It stays leftover evidence (MEMBERSHIP_UNSORTED). The real joint
    auditor shape - '<Firm A>, ..., 100 Tórshavn' followed by '<Firm B>,
    statsaut. revisorar, ...' - is unaffected: the first line does not end
    with ',' and the second starts upper-case."""
    entries: list[dict] = []
    leftovers: list[str] = []
    previous: str | None = None
    for line in lines:
        continuation = (label == "Grannskoðari" and previous is not None
                        and (previous.rstrip().endswith(",")
                             or line[:1].islower()))
        if continuation:
            leftovers.append(line)
            previous = line
            continue
        entity = _initial_member_entity(line, label)
        if entity is None:
            leftovers.append(line)
            previous = line
            continue
        entries.append({
            "type": change_type,
            "action": "APPOINTED",
            "entity": entity,
            "entity_normalized": normalize_entity_name(entity),
            "effective_date": founding_date,
            "registration_date": registration_date,
            "notice_date": notice_date,
            "origin": FOUNDING_ORIGIN,
            "raw_text": normalize_whitespace(line),
        })
        previous = line
    return entries, leftovers


def _membership_change_entries(label: str, body: str,
                               notice_date: str | None,
                               registration_date: str | None,
                               founding_date: str | None = None
                               ) -> list[dict]:
    """Turn a membership section (Nevnd / Stjorn / Grannskoari) into change
    entries. Every action-prefixed line opens an entry; lines that close with a
    '(rolle)' parenthetical are ADDITIONAL entities under the SAME action
    (real fixture: two 'Innkomin' lines); anything else continues the current
    entity text. All text is kept verbatim in raw_text.

    E2: in a founding notice (a parseable Stovningardato and no action prefix
    in ANY membership section) a membership section with NO action prefix is
    the initial member list - each member becomes an APPOINTED entry dated by
    the founding date. A section mixing action prefixes and bare member lines
    is ambiguous and keeps today's behaviour (leftover lines ->
    MEMBERSHIP_UNSORTED + needs_review)."""
    change_type = LYSING_MEMBERSHIP_LABELS[label]
    lines = _body_without_footer(body)
    if founding_date is not None and not any(
            _LYSING_OP_RE.match(line) for line in lines):
        return _initial_member_entries(
            label, change_type, lines, founding_date, notice_date,
            registration_date)
    entries: list[dict] = []
    leftovers: list[str] = []
    current: dict | None = None
    for line in lines:
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
            if action_prefix in _LYSING_OP_ROLES:
                current["role"] = _LYSING_OP_ROLES[action_prefix]
            entries.append(current)
        elif current is None:
            # line before any action prefix in this section: it belongs to no
            # entity yet - keep it verbatim as leftover evidence (lossless)
            leftovers.append(line)
        elif _MEMBER_RE.search(line) and not line.startswith("("):
            # a line that is ONLY '(rolle)' is the wrapped tail of the
            # previous member (D-06: 298 '(nevndarlimur)' entities in run 33)
            entity = _entity_from_text(line.strip())
            extra = {
                "type": current["type"],
                "action": current["action"],
                "entity": entity,
                "entity_normalized": normalize_entity_name(entity),
                "effective_date": current["effective_date"],
                "registration_date": registration_date,
                "notice_date": notice_date,
                "raw_text": normalize_whitespace(line),
            }
            if current.get("role"):
                extra["role"] = current["role"]
            entries.append(extra)
        else:
            # plain continuation (e.g. city wrapped onto the next line):
            # raw evidence grows, the entity itself is NOT recomputed
            current["raw_text"] = normalize_whitespace(
                current["raw_text"] + " " + line)
    return entries, leftovers



# ---------------------------------------------------------------------------
# Merger notices (Fusión / Fusiónslýsing / the source typo Fusoín)
# ---------------------------------------------------------------------------
# Two source shapes only:
#   plan:      'móttikið fusiónsæ tlan ...' + 'A. Navn: ...' + 'B: Navn: ...'
#   completed: 'Felagið er samanlagt við skrásetingar-nr. <form> <n>, <name>,
#               sum samstundis verður strikað.'
# Anything else stays an unknown section (today's LAYOUT_DRIFT behaviour).
_MERGER_SECTION_LABELS = {"Fusión", "Fusiónslýsing", "Fusoín"}
# PDF text artefact: 'fusiónsæ tlan' (space inside 'fusiónsætlan'); tolerate
# both the artefact and the joined spelling, and either 'ó' glyph.
_MERGER_PLAN_RE = re.compile(
    r"móttikið\s+fusi[oó]ns[æe]\s*tlan", re.IGNORECASE)
_MERGER_COMPLETED_RE = re.compile(
    r"^Felagið er samanlagt við skrásetingar-nr\.\s*"
    r"(?:[^\d,\s]+\s*)?(\d+)\s*,\s*(.+?)\s*,?\s*"
    r"sum samstundis verður strikað\b", re.IGNORECASE)
# party line: '<name> (<form>-nr.<n>), <place>' - take the LAST parenthetical
_MERGER_PARTY_RE = re.compile(r"^(.*)\(([^()]*?)-nr\.?\s*(\d+)\)",
                              re.IGNORECASE)
# one-line notices keep the party markers inside the flattened section body;
# here the FIRST marker is the party's own registration number, not a later
# parenthetical in the trailing text.
_MERGER_PARTY_FIRST_RE = re.compile(r"^(.*?)\(([^()]*?)-nr\.?\s*(\d+)\)",
                                    re.IGNORECASE)
# 'A. Navn:' / 'B: Navn:' / 'B. Navn:' inside a flattened section body.
_MERGER_PARTY_MARKER_RE = re.compile(
    r"(?<![A-Za-zÀ-ÿ])([A-Z])(?:\.\s*|\s*:\s*)Navn\s*:\s*")


def _merger_party(value: str, side: str, *,
                  first_match: bool = False) -> dict | None:
    """One merger party from a source party line; None when the line does not
    carry the register's '<form>-nr.<n>' marker (no guessing)."""
    lines = [line for line in value.splitlines() if line.strip()]
    if not lines:
        return None
    text_value = normalize_whitespace(lines[0])
    # 'B:' is only a side label, so its body repeats 'Navn:'.
    text_value = re.sub(r"^Navn\s*:\s*", "", text_value, flags=re.IGNORECASE)
    pattern = _MERGER_PARTY_FIRST_RE if first_match else _MERGER_PARTY_RE
    match = pattern.match(text_value)
    if not match:
        return None
    name = normalize_whitespace(match.group(1)).rstrip(", ")
    if not name:
        return None
    return {"side": side, "name": name,
            "registration_number": match.group(3)}


def _merger_plan_from_flat(body: str) -> tuple[dict, dict] | None:
    """A/B parties still inside a flattened (single-line) section body.

    Only the exact two-party order is accepted; a third side returns None so
    the section keeps today's unknown-section handling.
    """
    markers = list(_MERGER_PARTY_MARKER_RE.finditer(body))
    if [marker.group(1) for marker in markers] != ["A", "B"]:
        return None
    a_text = body[markers[0].end():markers[1].start()]
    b_text = body[markers[1].end():]
    party_a = _merger_party(a_text, "A", first_match=True)
    party_b = _merger_party(b_text, "B", first_match=True)
    if party_a is None or party_b is None:
        return None
    return party_a, party_b


def _merger_change(label: str, body: str, following: list[tuple[str, str]]
                   ) -> tuple[dict | None, int]:
    """(change, number of following party sections consumed).

    Only the two exact shapes produce a change; everything else returns
    (None, 0) and keeps the caller's unknown-section handling.
    """
    flat = normalize_whitespace(body)
    completed = _MERGER_COMPLETED_RE.match(flat)
    if completed:
        counterpart = {
            "registration_number": completed.group(1),
            "name": normalize_whitespace(completed.group(2)),
        }
        return {
            "type": "MERGER", "action": "COMPLETED",
            "counterpart": counterpart,
            # the whole section, including any sentence after 'strikað'
            "raw_text": normalize_whitespace(label + ": " + body),
        }, 0
    if not _MERGER_PLAN_RE.search(flat):
        return None, 0
    if len(following) >= 2:
        (a_label, a_body), (b_label, b_body) = following[0], following[1]
        if a_label == "A. Navn" and b_label in ("B", "B. Navn"):
            # A/B is the learned two-party shape; a third party means this is
            # not that shape - never emit a partial plan that drops a party.
            if len(following) >= 3 and re.fullmatch(
                    r"[A-Z](?:\.\s*Navn)?", following[2][0]):
                return None, 0
            party_a = _merger_party(a_body, "A")
            party_b = _merger_party(b_body, "B")
            if party_a is not None and party_b is not None:
                # verbatim section: label + body + both full party sections
                # (continuation lines included, never only their first line)
                raw_text = normalize_whitespace(
                    f"{label}: {body}\n{a_label}: {a_body}\n"
                    f"{b_label}: {b_body}")
                return {
                    "type": "MERGER", "action": "PLAN_REGISTERED",
                    "parties": [party_a, party_b],
                    "raw_text": raw_text,
                }, 2
    # single-line layout: the party lines never left the section body
    parties = _merger_plan_from_flat(flat)
    if parties is None:
        return None, 0
    return {
        "type": "MERGER", "action": "PLAN_REGISTERED",
        "parties": list(parties),
        "raw_text": normalize_whitespace(label + ": " + body),
    }, 0


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


def _lysing_founding_date(sections) -> str | None:
    """Stovningardato as an ISO date, or None. Presence of this labeled field
    is the first half of the founding-notice test; the initial (action-prefix-
    free) member sections are the second half (see _membership_change_entries).

    Only ONE distinct parseable value counts: an empty or repeated/conflicting
    label stays ambiguous and disables the founding conversion (the member
    lines then remain unsorted leftovers under review)."""
    values = {parsed.isoformat()
              for label, body in sections
              if label == _FOUNDING_DATE_LABEL
              for parsed in (parse_date(body),) if parsed is not None}
    return next(iter(values)) if len(values) == 1 else None


def _membership_sections_have_actions(sections) -> bool:
    """True when ANY section holds an action-prefixed line.

    A founding notice's member sections are action-prefix-free by definition.
    The splitter can attach an op line to a non-membership section (for
    example ``Farin úr nevndini:`` under ``Navn:`` when no ``Nevnd:`` label
    precedes it), so scanning only the membership labels would miss it. An op
    line anywhere makes the whole notice a later change notice that merely
    quotes Stovningardato (X5b review #1 and fix round 1 #4), so the bare
    member lines of the other sections must stay leftovers under review."""
    return any(
        _LYSING_OP_RE.match(line)
        for _label, body in sections
        for line in _body_without_footer(body))


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
    founding_date = _lysing_founding_date(sections)
    # Review #1: founding is decided once per NOTICE. A later notice that only
    # quotes Stovningardato and carries an op line in any membership section
    # is not a founding notice; its bare member lines stay leftovers.
    if founding_date is not None and _membership_sections_have_actions(sections):
        founding_date = None

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
    consumed_sections: set[int] = set()
    for index, (label, body) in enumerate(sections):
        if index in consumed_sections:
            continue
        if label in _MERGER_SECTION_LABELS:
            merger, consumed = _merger_change(
                label, body, sections[index + 1:index + 4])
            if merger is not None:
                notice["changes"].append(merger)
                consumed_sections.update(
                    range(index + 1, index + 1 + consumed))
                continue
        if label in LYSING_MEMBERSHIP_LABELS:
            entries, leftover_lines = _membership_change_entries(
                label, body,
                notice_date.isoformat() if notice_date else None,
                registration_date.isoformat() if registration_date else None,
                founding_date=founding_date)
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
    "Innkomin sum likvidator", "Farin frá sum likvidator",
    "Innkomin sum kurator", "Farin frá sum kurator",
    "Innkomin sum grannskoðari",
    "Farin úr nevndini sum nevndarformaður",
    "Farin úr umsjónarráðnum", "Innkomin í umsjónarráðið",
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
    "Umsjónarráð", "Fusión", "Fusiónslýsing", "Fusoín",
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

    def _split_label(match: re.Match) -> str:
        # A merger party line 'A. Navn:' / 'B: Navn:' repeats the scalar
        # label 'Navn'. Splitting there would turn a party into NAME_CHANGE;
        # the section label belongs to the party side, not to Navn. Only a
        # merger label earlier in the text can introduce that party shape;
        # elsewhere 'Sp/F. Navn:' is a real change (review #8).
        if match.group(1) == "Navn":
            before = match.string[:match.start()].rstrip()
            merger_earlier = any(label in before
                                 for label in _MERGER_SECTION_LABELS)
            # any party side marker: 'A. Navn:', 'B: Navn:', 'C: Navn:' ...
            if merger_earlier and re.search(
                    r"(?:^|[^A-Za-zÀ-ÿ])[A-Z][.:]$", before):
                return match.group(0)
        return "\n" + match.group(1) + ":"

    text = re.sub(r"(?<=[^\n])\s*(" + labels + r")\s*:", _split_label, text)
    # Reunite a bare parent scalar label with its immediately following
    # 'Nýtt navn:'/'Nýggj hjánøvn:' opener. The multiline convention (real
    # fixtures: 79 docs) treats that opener as the parent's VALUE (see the
    # Nýtt-prefixed sub_match in parse_lysing_pdf); the split above otherwise
    # detaches it into its own section and the scalar keeps an empty entity
    # (live 21917/26980/24340). Adjacency-anchored, oneline-scoped only.
    text = re.sub(r"(?m)^(Navn:)\s*\n(Nýtt navn:)", r"\1 \2", text)
    text = re.sub(r"(?m)^(Hjánøvn:)\s*\n(Nýggj hjánøvn:)", r"\1 \2", text)
    return text
