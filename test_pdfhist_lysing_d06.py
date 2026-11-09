"""Lysing parser keeps board changes and founds initial members (D-06, E2).

The 4 of 21 Lysing fixtures whose membership lines fell outside changes[]
with no review flag: lys_38088 (artefact 'InnKomSin' lost 3 appointments)
and the founding notices lys_33208, lys_36724, lys_5053. E2: the initial
members of a founding notice become APPOINTED history entries dated by
Stovningardato and marked origin='FOUNDING' ("ved stiftelse").
"""
import re

import pytest

from heimdal.pdfhist.parse import parse_lysing_pdf
# H(): register person names are pinned by digest, never in git (I-5)
from heimdal.pdfhist.testsupport import fixture_text, pii_digest as H


def _parse(name):
    return parse_lysing_pdf(fixture_text(name))


def _unsorted(notice):
    return "\n".join(u["verbatim"] for u in notice["unknown_sections"]
                     if "usorterede" in u["label"])


def test_38088_artefact_join_keeps_three_board_appointments():
    notice = _parse("lys_38088.bin.txt")
    board = [(c["action"], H(c["entity"])) for c in notice["changes"]
             if c["type"] == "BOARD_CHANGE"]
    assert board == [("APPOINTED", '1892084a2612d817'),
                     ("APPOINTED", '2611a89b21c13dc8'),
                     ("APPOINTED", 'f6a403eea5877224')]
    # the wrapped '(nevndarformaður)' line is the first member's tail, not a member
    chair = notice["changes"][[H(c["entity"]) for c in notice["changes"]]
                              .index('1892084a2612d817')]
    assert chair["raw_text"].endswith("Danmark (nevndarformaður)")
    assert _unsorted(notice) == ""
    assert notice["review_reasons"] == []
    assert notice["notice_date"] == "2025-02-17"


@pytest.mark.parametrize("name, notice_date, founding_date, members", [
    ("lys_33208_20-06-2022.bin.txt", "2022-06-20", "2022-06-16",
     [("MANAGEMENT_CHANGE", '385eb151942ca1fb')]),
    ("lys_36724_29-05-2024.bin.txt", "2024-05-27", "2024-05-25",
     [("MANAGEMENT_CHANGE", '70c542052442468b')]),
    ("lys_5053_25-10-1977.bin.txt", "2012-03-28", "1977-10-25",
     [("BOARD_CHANGE", 'a04eb2572c32fbc2'),
      ("BOARD_CHANGE", 'db7d5822e4068a8e'),
      ("BOARD_CHANGE", 'a2f3361f1a1cdd4d'),
      ("BOARD_CHANGE", '8bcf21b1c3975da8'),
      ("BOARD_CHANGE", 'f164a0e66601d095'),
      ("MANAGEMENT_CHANGE", 'a04eb2572c32fbc2'),
      ("AUDITOR_CHANGE", '1c7bb8dfeba7ab3f')]),
])
def test_founding_notice_members_become_appointed_origin_founding(
        name, notice_date, founding_date, members):
    notice = _parse(name)
    founding = [(c["type"], H(c["entity"]), c["action"],
                 c["effective_date"], c["origin"])
                for c in notice["changes"] if c.get("origin") == "FOUNDING"]
    assert founding
    # every initial member, of the right family, appointed at founding
    assert [(change_type, digest) for change_type, digest, *_ in founding] \
        == members
    assert all(action == "APPOINTED" for *_, action, _, _ in founding)
    assert all(effective == founding_date for *_, effective, _ in founding)
    # all three fixture notices are review-clean, so ingest stores them
    # PARSED and their founding entries reach the projection
    assert notice["review_reasons"] == []
    assert notice["needs_review"] is False
    # the initial members are no longer unsorted leftover evidence
    parts = {H(part.strip()) for part in re.split(r"[,\n]", _unsorted(notice))}
    assert {digest for _, digest in members}.isdisjoint(parts)
    # no pseudo persons ("(nevndarlimur)" etc.) are ever created
    assert not any(c["entity"] and c["entity"].startswith("(")
                   for c in notice["changes"])
    # the letter date, never the 'Stovningardato:' line
    assert notice["notice_date"] == notice_date


def test_founding_stray_capital_label_stays_lossless_evidence():
    """lys_5053's bare 'Lutapeningur   kr.' line must not become a member and
    must not turn into a MEMBERSHIP_UNSORTED review flag - but it is kept."""
    notice = _parse("lys_5053_25-10-1977.bin.txt")
    preserved = {u["label"]: u["verbatim"]
                 for u in notice["unknown_sections"]}
    assert "kr." in preserved["Lutapeningur"]
    assert notice["review_reasons"] == []


def test_synthetic_founding_notice_appoints_initial_members():
    """Stovningardato + action-prefix-free member sections = founding."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  01.02.1990\n"
        "Nevnd:\n"
        "Anna Test, Vegur 1, 100 Tórshavn (nevndarformaður)\n"
        "Grannskoðari:\n"
        "Sp/f Test Revisor, Vegur 2, 100 Tórshavn\n")
    founding = [(c["type"], c["action"], c["entity"], c["effective_date"])
                for c in notice["changes"] if c.get("origin") == "FOUNDING"]
    assert founding == [
        ("BOARD_CHANGE", "APPOINTED", "Anna Test", "1990-02-01"),
        ("AUDITOR_CHANGE", "APPOINTED", "Sp/f Test Revisor", "1990-02-01")]
    assert notice["notice_date"] == "2021-03-12"
    assert notice["review_reasons"] == []


def test_single_token_auditor_firm_is_a_founding_member():
    """A one-word audit firm name is legitimate for Grannskoðari."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  01.02.1990\n"
        "Nevnd:\n"
        "Anna Test, Vegur 1, 100 Tórshavn (nevndarformaður)\n"
        "Grannskoðari:\n"
        "KPMG, Vegur 2, 100 Tórshavn\n")
    founding = [(c["type"], c["action"], c["entity"], c["effective_date"])
                for c in notice["changes"] if c.get("origin") == "FOUNDING"]
    assert founding == [
        ("BOARD_CHANGE", "APPOINTED", "Anna Test", "1990-02-01"),
        ("AUDITOR_CHANGE", "APPOINTED", "KPMG", "1990-02-01")]
    assert notice["review_reasons"] == []


def test_single_token_board_name_stays_leftover():
    """The one-token exception is auditor-only; board keeps the 2-token rule."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  01.02.1990\n"
        "Nevnd:\n"
        "Anna, Vegur 1, 100 Tórshavn (nevndarlimur)\n")
    assert not any(c.get("origin") == "FOUNDING" for c in notice["changes"])
    assert "Anna" in _unsorted(notice)
    assert "MEMBERSHIP_UNSORTED" in notice["review_reasons"]


def test_single_token_auditor_keeps_real_firm_address_shapes():
    """NEW-1: a description + street/postbox address is still an audit firm."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  01.02.1990\n"
        "Grannskoðari:\n"
        "Numo, løggilt grannskoðanarvirki, Klaksvíksvegur 77, "
        "Postbox 144, 700 Klaksvík\n")
    founding = [(c["type"], c["action"], c["entity"], c["effective_date"])
                for c in notice["changes"] if c.get("origin") == "FOUNDING"]
    assert founding == [("AUDITOR_CHANGE", "APPOINTED", "Numo",
                         "1990-02-01")]
    assert notice["review_reasons"] == []


def test_founding_auditor_junk_lines_are_not_one_token_firms():
    """NEW-1: a date, a place-only wrapped line and a scalar label inside a
    founding Grannskoðari section are leftovers, never fabricated auditors."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  01.02.1990\n"
        "Nevnd:\n"
        "Anna Test, Vegur 1, 100 Tórshavn (nevndarformaður)\n"
        "Grannskoðari:\n"
        "Tórshavn, 12. mars 2021\n"
        "Søborg, Danmark\n"
        "Lutapeningur, kr.\n")
    assert [c for c in notice["changes"]
            if c["type"] == "AUDITOR_CHANGE"] == []
    unsorted = _unsorted(notice)
    for junk in ("Tórshavn, 12. mars 2021", "Søborg, Danmark",
                 "Lutapeningur, kr."):
        assert junk in unsorted
    assert "MEMBERSHIP_UNSORTED" in notice["review_reasons"]


def test_company_parenthetical_alias_is_not_stripped_as_a_role_tail():
    """Only a known role parenthetical is stripped from the name head."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  01.02.1990\n"
        "Grannskoðari:\n"
        "Sp/f Test (Tvey), Vegur 2, 100 Tórshavn\n")
    assert [c["entity"] for c in notice["changes"]
            if c.get("origin") == "FOUNDING"] == ["Sp/f Test (Tvey)"]
    assert notice["review_reasons"] == []


def test_notice_only_role_tails_are_founding_members():
    """varalimur / ábyrgdarhavandi are valid member-role tails."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  01.02.1990\n"
        "Nevnd:\n"
        "Anna Test, Vegur 1, 100 Tórshavn (varalimur)\n"
        "Stjóri:\n"
        "Bjørt Test, Vegur 2, 100 Tórshavn (ábyrgdarhavandi)\n")
    founding = [(c["type"], c["entity"]) for c in notice["changes"]
                if c.get("origin") == "FOUNDING"]
    assert founding == [
        ("BOARD_CHANGE", "Anna Test"),
        ("MANAGEMENT_CHANGE", "Bjørt Test")]
    assert notice["review_reasons"] == []


def test_notice_quoting_stovningardato_with_actions_is_not_founded():
    """A later notice that only quotes the founding date is not converted."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  01.01.1990\n"
        "Nevnd:  Farin úr nevndini: Anna Test, Vegur 1, 100 Tórshavn"
        " (nevndarlimur)\n")
    board = [c for c in notice["changes"] if c["type"] == "BOARD_CHANGE"]
    assert [(c["action"], c["entity"]) for c in board] == \
        [("REMOVED", "Anna Test")]
    assert not any(c.get("origin") == "FOUNDING" for c in notice["changes"])
    assert notice["notice_date"] == "2021-03-12"


def test_action_in_one_membership_section_disables_founding_notice_wide():
    """X5b review #1: founding is decided once per notice.

    An op line in ANY membership section marks a later change notice; the
    bare member lines of the other sections are leftovers, never FOUNDING."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  01.02.1990\n"
        "Nevnd:  Farin úr nevndini: Anna Test, Vegur 1, 100 Tórshavn\n"
        "Grannskoðari:\n"
        "Sp/f Test Revisor, Vegur 2, 100 Tórshavn\n")
    assert not any(c.get("origin") == "FOUNDING" for c in notice["changes"])
    assert not [c for c in notice["changes"]
                if c["type"] == "AUDITOR_CHANGE"]
    assert "Sp/f Test Revisor, Vegur 2, 100 Tórshavn" in _unsorted(notice)
    assert "MEMBERSHIP_UNSORTED" in notice["review_reasons"]


def test_founding_umsjonarrad_requires_a_role_tail():
    """X5b review #2: a founding Umsjónarráð line needs its (rolle) tail."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  01.02.1990\n"
        "Umsjónarráð:\n"
        "Eitt Testnavn, Testgøta 1, 100 Tórshavn\n")
    assert not any(c.get("origin") == "FOUNDING" for c in notice["changes"])
    assert not [c for c in notice["changes"]
                if c["type"] == "BOARD_CHANGE"]
    assert "Eitt Testnavn, Testgøta 1, 100 Tórshavn" in _unsorted(notice)
    assert "MEMBERSHIP_UNSORTED" in notice["review_reasons"]


def test_founding_auditor_wrapped_address_is_a_continuation():
    """X5b review #2: a line after one ending in ',' continues that firm."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  01.02.1990\n"
        "Grannskoðari:\n"
        "Sp/f Test Inpact, Testgøta 1,\n"
        "Smyrilsvegur, 100 Tórshavn\n")
    founding = [c["entity"] for c in notice["changes"]
                if c.get("origin") == "FOUNDING"]
    assert founding == ["Sp/f Test Inpact"]
    assert "Smyrilsvegur, 100 Tórshavn" in _unsorted(notice)
    assert "MEMBERSHIP_UNSORTED" in notice["review_reasons"]


def test_founding_auditor_firm_description_continuation_is_not_a_member():
    """X5b review #2: a capitalised firm-description wrap is not a firm.

    The previous line ends with ',' so 'Statsaut. revisorar' (capitalised,
    two tokens, no digits) must stay leftover evidence."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  01.02.1990\n"
        "Grannskoðari:\n"
        "Sp/f Test Inpact,\n"
        "Statsaut. revisorar, R. C. Effersøesgøta 26, 100 Tórshavn\n")
    founding = [c["entity"] for c in notice["changes"]
                if c.get("origin") == "FOUNDING"]
    assert founding == ["Sp/f Test Inpact"]
    assert "MEMBERSHIP_UNSORTED" in notice["review_reasons"]


def test_founding_auditor_two_firms_on_their_own_lines_stay_members():
    """X5b review #2: genuine joint auditors must both survive.

    The first line does not end with ',' and the second starts upper-case, so
    neither is a continuation."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  01.02.1990\n"
        "Grannskoðari:\n"
        "Sp/f Test A, Testgøta 1, 100 Tórshavn\n"
        "Sp/f Test B, statsaut. revisorar, Testgøta 2, 100 Tórshavn\n")
    founding = [c["entity"] for c in notice["changes"]
                if c.get("origin") == "FOUNDING"]
    assert founding == ["Sp/f Test A", "Sp/f Test B"]
    assert notice["review_reasons"] == []


def test_mixed_membership_section_stays_unsorted_and_needs_review():
    """An ambiguous section (action prefix + unprefixed member line) keeps the
    unprefixed line verbatim and the review flag - never a silent founder."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  01.02.1990\n"
        "Nevnd:\n"
        "Bjørn Test, Vegur 2, 100 Tórshavn (nevndarlimur)\n"
        "Farin úr nevndini: Anna Test, Vegur 1, 100 Tórshavn\n")
    assert any(c["action"] == "REMOVED" for c in notice["changes"])
    assert "Bjørn Test" in _unsorted(notice)
    assert "MEMBERSHIP_UNSORTED" in notice["review_reasons"]
    assert notice["needs_review"] is True
    assert not any(c.get("origin") == "FOUNDING" for c in notice["changes"])


def test_wrapped_or_role_prefixed_lines_do_not_invent_founding_members():
    """Continuation lines are not members: they stay leftover evidence and
    keep the review flag instead of projecting invented founders."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  01.02.1990\n"
        "Nevnd:\n"
        "Nevndarformaður Anna Test, Vegur 1, 100 Tórshavn\n"
        "Grannskoðari:\n"
        "Sp/f Test Inpact,\n"
        "statsaut. revisorar, R. C. Effersøesgøta 26, 100 Tórshavn\n")
    founding = [c["entity"] for c in notice["changes"]
                if c.get("origin") == "FOUNDING"]
    assert founding == ["Sp/f Test Inpact"]
    assert "statsaut. revisorar" not in founding
    assert "MEMBERSHIP_UNSORTED" in notice["review_reasons"]
    assert notice["needs_review"] is True


def test_duplicate_stovningardato_with_one_parseable_value_is_founded():
    """A repeated label with an empty body is not a conflict."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:\n"
        "Stovningardato:  01.02.1990\n"
        "Nevnd:\n"
        "Anna Test, Vegur 1, 100 Tórshavn (nevndarformaður)\n")
    assert [(c["entity"], c["effective_date"]) for c in notice["changes"]
            if c.get("origin") == "FOUNDING"] == \
        [("Anna Test", "1990-02-01")]


def test_conflicting_stovningardato_disables_founding_conversion():
    """Two different founding dates are ambiguous - never pick one."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  01.02.1990\n"
        "Stovningardato:  02.02.1991\n"
        "Nevnd:\n"
        "Anna Test, Vegur 1, 100 Tórshavn (nevndarformaður)\n")
    assert not any(c.get("origin") == "FOUNDING" for c in notice["changes"])
    assert "MEMBERSHIP_UNSORTED" in notice["review_reasons"]


def test_unparsed_layout_with_a_regnr_is_layout_drift():
    """LAYOUT_DRIFT is decided after the section loop: a regnr alone is not
    an understood notice."""
    notice = parse_lysing_pdf(
        "12.03.2021\nSkrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Ókend grein:  A B, Vegur 1\n")
    assert notice["registration_number"] == "10"
    assert notice["changes"] == [] and notice["reporter"] is None
    assert "LAYOUT_DRIFT" in notice["review_reasons"]


def test_board_removal_of_a_female_chair():
    notice = parse_lysing_pdf(
        "12.03.2021\nSkrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Nevnd:  Farin frá sum nevndarforkvinna: Anna Test, Vegur 1, "
        "100 Tórshavn\n")
    assert [(c["type"], c["action"], c["entity"]) for c in notice["changes"]] \
        == [("BOARD_CHANGE", "REMOVED", "Anna Test")]
    assert notice["review_reasons"] == []
