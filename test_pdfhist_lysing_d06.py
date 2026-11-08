"""Lysing parser keeps board changes (audit 2026-11 D-06, lane W4d). Offline.

The 4 of 21 Lysing fixtures whose membership lines fell outside changes[]
with no review flag: lys_38088 (artefact 'InnkomSin' lost 3 appointments)
and the founding notices lys_33208, lys_36724, lys_5053 (initial members
unsorted, notice_date = founding date).
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


@pytest.mark.parametrize("name, notice_date, members", [
    ("lys_33208_20-06-2022.bin.txt", "2022-06-20", ['385eb151942ca1fb']),
    ("lys_36724_29-05-2024.bin.txt", "2024-05-27", ['70c542052442468b']),
    ("lys_5053_25-10-1977.bin.txt", "2012-03-28",
     ['a04eb2572c32fbc2', 'f164a0e66601d095',
      '1c7bb8dfeba7ab3f']),
])
def test_founding_notice_members_are_flagged_not_dropped(name, notice_date,
                                                          members):
    notice = _parse(name)
    assert notice["needs_review"] is True
    assert "MEMBERSHIP_UNSORTED" in notice["review_reasons"]
    parts = {H(p.strip()) for p in re.split(r"[,\n]", _unsorted(notice))}
    for member in members:  # lossless: verbatim under its membership label
        assert member in parts
    # the letter date, never the 'Stovningardato:' line
    assert notice["notice_date"] == notice_date
    assert not any(c["type"].endswith("_CHANGE") and c["entity"]
                   and c["entity"].startswith("(") for c in notice["changes"])


def test_unparsed_layout_with_a_regnr_is_layout_drift():
    """LAYOUT_DRIFT is decided after the section loop: a regnr alone is not
    an understood notice."""
    notice = parse_lysing_pdf(
        "12.03.2021\nSkrásetingar-nr.  P/F  10,  P/F Test, 100 Tórshavn.\n"
        "Umsjónarráð:  Farin úr umsjónarráðnum: A B, Vegur 1\n")
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
