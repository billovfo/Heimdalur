"""Parser fixture tests (plan FASE 9-12 + FASE 22/23): pure-text tests against
the REAL extracted fixture corpus, plus the synthetic PDF builder roundtrip.
No database, no network."""
import re

import pytest

from heimdal.pdfhist.extract import TextExtractionError, extract_text
from heimdal.pdfhist.fixture_pdf import make_pdf, make_pdf_text_roundtrip
from heimdal.pdfhist.parse import (
    FELAG_PDF_TYPE, LYSING_PDF_TYPE, parse_felag_pdf, parse_lysing_pdf,
    split_labeled_sections)
# H(): register person names/addresses are pinned by digest, never in git (I-5)
from heimdal.pdfhist.testsupport import fixture_text, pii_digest as H


def test_data_01_felag_10_company_address_roles_exact():
    snap = parse_felag_pdf(fixture_text("felag_10.bin.txt"))
    # company address = Oknarvegur 5 (NOT the registry footer, NOT a member)
    assert snap["company_address"]["role"] == "COMPANY_ADDRESS"
    assert snap["company_address"]["street"] == "Oknarvegur 5"
    assert snap["company_address"]["postrum"] == "3048"
    assert snap["company_address"]["postal_code"] == "110"
    assert snap["company_address"]["city"] == "Tórshavn"
    assert snap["company_address"]["verbatim"] == \
        "Oknarvegur 5\nPostrúm 3048\n110 Tórshavn"
    assert snap["company_address"]["kommuna"] == "Tórshavnar"
    verbatim = snap["company_address"]["verbatim"]
    assert "Sigmundargøta" not in verbatim  # footer can never become company addr
    assert "Skráseting" not in verbatim


def test_data_01_felag_10_board_and_management_roles_exact():
    snap = parse_felag_pdf(fixture_text("felag_10.bin.txt"))
    assert snap["registration_number"] == "10"
    assert snap["company_name"] == "P/F Føroya Banki"
    assert snap["snapshot_date"] == "2026-08-30"
    roles = [(p["role"], H(p["name"])) for p in snap["board"]]
    assert ("Nevndarformaður", '8bb4042160b01b1a') in roles
    assert ("Næstformaður", '910b339e78a8ba74') in roles
    # footer-interleaved member keeps his OWN split address (fixture artifact)
    member = next(p for p in snap["board"] if H(p["name"]) == 'cef447c65d83f78e')
    assert member["address"]["role"] == "BOARD_MEMBER_ADDRESS"
    assert H(member["address"]["verbatim"]) == '3669b0c65f8df2d2'
    # employee-elected representatives are board members with own addresses
    employee_reps = [p for p in snap["board"]
                     if p["role"] == "Starvsfólkavalt umboð"]
    assert len(employee_reps) == 3
    # single stjóri with her own address (NOT part of the company address)
    assert len(snap["management"]) == 1
    stjori = snap["management"][0]
    assert stjori["role"] == "Stjóri"
    assert H(stjori["name"]) == '16933fe9849dc6ab'
    assert stjori["address"]["role"] == "MANAGEMENT_ADDRESS"
    assert H(stjori["address"]["verbatim"]) == 'ef32ba1ae68389e8'
    # the interleaved footer line must not appear in any member address
    for person in snap["board"] + snap["management"]:
        assert "Skráseting" not in person["address"]["verbatim"]
    # capital / purpose / signing / accounts
    assert snap["capital"] == {"currency": "DKK", "amount": 192000000,
                               "verbatim": "DKK: 192.000.000"}
    assert snap["accounts_dates"] == ["2024-03-22", "2025-03-31",
                                      "2026-04-08"]
    assert snap["signing_rules"].startswith("Bankin verður bundin")
    assert snap["needs_review"] is False


def test_data_02_lysing_unknown_section_lostless():
    notice = parse_lysing_pdf(fixture_text("lys_2596_15-04-2010.bin.txt"))
    unknown_labels = [u["label"] for u in notice["unknown_sections"]]
    assert "Rokning fyri lýsing verður at senda til" in unknown_labels
    unknown = {u["label"]: u["verbatim"]
               for u in notice["unknown_sections"]}
    assert "P/F Føroya Banki" in unknown["Rokning fyri lýsing verður at"
                                       " senda til"]
    # registry prose header remains as evidence too
    assert any(u["label"] == "(preamble)"
               for u in notice["unknown_sections"])
    # losslessness is measured over the whole parse output surface:
    # auditored address evidence lives in change raw_text, not unknown_sections
    evidence = "".join(u["verbatim"] for u in notice["unknown_sections"]) \
        + "".join(c["raw_text"] for c in notice["changes"])
    assert "Hjaltesvej 16" in evidence
    assert "Holstebro" in evidence
    assert "Nýggjur grannskoðari" in evidence  # action anchor retained verbatim


def test_date_01_lysing_dates_are_separate():
    notice = parse_lysing_pdf(fixture_text("lys_2596_15-04-2010.bin.txt"))
    assert notice["notice_date"] == "2010-04-15"        # header date
    assert notice["registration_date"] == "2010-04-15"  # 'tann 15. apríl'
    assert notice["journal_number"] == "10-264"
    # the articles change carries its own EFFECTIVE date, distinct from notice
    articles = next(c for c in notice["changes"]
                    if c["type"] == "ARTICLES_CHANGE")
    assert articles["effective_date"] == "2010-03-29"
    assert notice["dates"]["snapshot_date"] is None  # snapshot is a Felag concept


def test_lysing_2596_changes_membership_pairs():
    notice = parse_lysing_pdf(fixture_text("lys_2596_15-04-2010.bin.txt"))
    changes = notice["changes"]
    auditors = [c for c in changes if c["type"] == "AUDITOR_CHANGE"]
    assert [(c["action"], c["entity"]) for c in auditors] == [
        ("REMOVED", "PricewaterhouseCoopers hf."),
        ("APPOINTED", "PricewaterhouseCoopers Danmark")]
    board = [c for c in changes if c["type"] == "BOARD_CHANGE"]
    assert board[0]["action"] == "REMOVED"
    assert H(board[0]["entity"]) == '2aaa8c1cd9870d69'
    assert board[1]["action"] == "APPOINTED"
    assert H(board[1]["entity"]) == '6a4e07b1bdb89863'


def test_lysing_41211_fra_bo_dari_has_its_own_address_role():
    notice = parse_lysing_pdf(fixture_text("lys_41211_2026.bin.txt"))
    assert notice["reporter"]["name"] == "P/F Føroya Banki"
    assert notice["reporter"]["address"]["role"] == "REPORTER_ADDRESS"
    assert "Oknarvegur 5" in notice["reporter"]["address"]["verbatim"]
    board = [c for c in notice["changes"] if c["type"] == "BOARD_CHANGE"]
    two_in = [c["entity"] for c in board if c["action"] == "APPOINTED"]
    two_out = [c["entity"] for c in board if c["action"] == "REMOVED"]
    assert {H(n) for n in two_in} == {'7ee6121645a84d4f', 'cef447c65d83f78e'}
    assert {H(n) for n in two_out} == {'b3248e2ccf6735d8', '113aa5563dd87d5b'}


def test_layout_drift_review_on_unknown_text():
    parsed = parse_felag_pdf("this is not a printable registry document")
    assert parsed["needs_review"] is True
    assert "LAYOUT_DRIFT" in parsed["review_reasons"]


def test_empty_text_layer_review_flag():
    parsed = parse_felag_pdf("")
    assert parsed["needs_review"] is True
    assert "EMPTY_TEXT_LAYER" in parsed["review_reasons"]


def test_synthetic_pdf_roundtrip_and_parse():
    text = make_pdf_text_roundtrip([
        "Dagf. 06-06-2020",
        "Skrasetingar-nr: 5015",
        "Navn: P/F Testfelag",
        "Adressa: Brekkutún 7",
        "100 Tórshavn",
        "Nevnd: Nevndarformaður Bólv Rótarni",
        "Hagvegur 3, 100 Tórshavn",
    ])
    assert text.startswith("Dagf. 06-06-2020")
    parsed = parse_felag_pdf(text)
    assert parsed["snapshot_date"] == "2020-06-06"
    assert parsed["registration_number"] == "5015"
    assert parsed["company_name"] == "P/F Testfelag"
    assert parsed["company_address"]["street"] == "Brekkutún 7"
    assert parsed["board"][0]["name"] == "Bólv Rótarni"
    assert parsed["board"][0]["address"]["role"] == "BOARD_MEMBER_ADDRESS"


def test_synthetic_pdf_roundtrip_exact_lines():
    text = make_pdf_text_roundtrip(
        ["Dagf. 02-06-2021", "Skrasetingar-nr: 5019", "Navn: P/F Nýtt félag"])
    lines = text.splitlines()
    assert lines == ["Dagf. 02-06-2021", "Skrasetingar-nr: 5019",
                     "Navn: P/F Nýtt félag"]


def test_empty_pdf_text_layer_raises_but_caller_keeps_bytes():
    blank = make_pdf([""])
    with pytest.raises(TextExtractionError):
        extract_text(blank)

def test_lysing_membership_leading_plain_line_is_leftover_not_crash():
    """Live-pilot round 2 defect: a real LysingPdf had a membership section
    starting with a plain line (no action prefix). The parser crashed with
    TypeError; it must instead keep the line verbatim as leftover evidence."""
    text = (
        "15.09.2020\n"
        "Fráboðari: Sp/f Testfelag, Testgøta 1, 100 Tórshavn.\n"
        "Skráseting Føroya tann 15. september 2020\n"
        "Nevnd: Nevndin hevur hesar limir:\n"
        "Innkomin í nevndina: Uppi Lyngs, Vegur 9, 100 Tórshavn"
        " (nevndarlimur)\n"
    )
    notice = parse_lysing_pdf(text)  # must not raise
    board = [c for c in notice["changes"] if c["type"] == "BOARD_CHANGE"]
    assert len(board) == 1
    assert board[0]["action"] == "APPOINTED"
    assert board[0]["entity"] == "Uppi Lyngs"
    leftover = [u for u in notice["unknown_sections"]
                if u["label"] == "(Nevnd usorterede linjer)"]
    assert leftover and leftover[0]["verbatim"] == \
        "Nevndin hevur hesar limir:"


def test_lysing_member_line_before_any_action_is_leftover_not_crash():
    """A '(rolle)' member line arriving before any action prefix cannot be
    attributed to an action: keep as leftover evidence, never crash."""
    text = (
        "20.08.2018\n"
        "Fráboðari: Sp/f Testfelag, Testgøta 1, 100 Tórshavn.\n"
        "Nevnd: Anna Testsen, Gøta 2, 100 Tórshavn (nevndarformaður)\n"
        "Farin úr nevndini: Anna Ø, Øvegisgøta 5, 100 Tórshavn"
        " (nevndarlimur)\n"
    )
    notice = parse_lysing_pdf(text)
    board = [c for c in notice["changes"] if c["type"] == "BOARD_CHANGE"]
    assert len(board) == 1
    assert board[0]["action"] == "REMOVED"
    assert board[0]["entity"] == "Anna Ø"
    leftover = [u for u in notice["unknown_sections"]
                if u["label"] == "(Nevnd usorterede linjer)"]
    assert leftover and "Anna Testsen" in leftover[0]["verbatim"]


# ---------------------------------------------------------------------------
# Live-pilot parser fixes (each backed by a real fetched PDF fixture)
# ---------------------------------------------------------------------------

def _fixture(name):
    from heimdal.pdfhist.testsupport import fixture_text
    return fixture_text(name)


def test_pilot_oneline_lysing_articles_layout_parses():
    """Live discovery (c931, 2018): the whole notice is ONE physical line.
    regnr + Viðtøkur effective date + purpose must parse; gate becomes MATCH."""
    notice = parse_lysing_pdf(_fixture("lys_23421_08-03-2018.bin.txt"))
    assert notice["registration_number"] == "931"
    assert notice["company_name"] == "P/F Varðin"
    assert notice["journal_number"] == "18-142"
    assert notice["notice_date"] == "2018-03-08"
    articles = next(c for c in notice["changes"]
                    if c["type"] == "ARTICLES_CHANGE")
    assert articles["effective_date"] == "2018-03-02"
    assert any(c["type"] == "PURPOSE_CHANGE" for c in notice["changes"])
    assert notice["needs_review"] is False


def test_pilot_oneline_lysing_management_layout_parses():
    notice = parse_lysing_pdf(_fixture("lys_28537_31-07-2020.bin.txt"))
    assert notice["registration_number"] == "931"
    mgmt = [c for c in notice["changes"]
            if c["type"] == "MANAGEMENT_CHANGE"]
    assert [(c["action"], H(c["entity"])) for c in mgmt] == [
        ("APPOINTED", '3f126520e867b905'),
        ("REMOVED", '71a5c94e1afda0e3')]


def test_pilot_oneline_lysing_board_layout_parses():
    notice = parse_lysing_pdf(_fixture("lys_28617_17-08-2020.bin.txt"))
    assert notice["registration_number"] == "931"
    board = [c for c in notice["changes"] if c["type"] == "BOARD_CHANGE"]
    assert [(c["action"], H(c["entity"])) for c in board] == [
        ("APPOINTED", '524d90314acf9a13'),
        ("REMOVED", '71a5c94e1afda0e3')]


def test_pilot_oneline_lysing_auditor_layout_parses():
    notice = parse_lysing_pdf(_fixture("lys_23981_15-06-2018.bin.txt"))
    assert notice["registration_number"] == "5019"
    assert notice["company_name"] == "Sp/f ARA"
    auditors = [c for c in notice["changes"]
                if c["type"] == "AUDITOR_CHANGE"]
    assert [(c["action"], c["entity"]) for c in auditors] == [
        ("APPOINTED", "NUMO"), ("REMOVED", "P/F Januar")]


def test_pilot_oneline_normalizer_leaves_normal_layouts_untouched():
    from heimdal.pdfhist.parse import normalize_oneline_lysing_text
    normal = _fixture("lys_2596_15-04-2010.bin.txt")
    assert normalize_oneline_lysing_text(normal) == normal
    oneline = _fixture("lys_28617_17-08-2020.bin.txt")
    normalized = normalize_oneline_lysing_text(oneline)
    assert normalized != oneline
    assert normalized.count("\n") > oneline.count("\n")
    # every injected line break sits before a known label, never mid-address
    assert "\nSkrásetingar-nr." in normalized
    assert "\nNevnd:" in normalized
    assert "\nInnkomin í nevndina:" in normalized


def test_pilot_felag_direct_stjori_section_is_management():
    """Live discovery (c8664 ÍVF Trolloftið): single-director companies print
    a direct 'Stjóri:' section with no 'Stjórn:' wrapper. The director must
    be structured as management with his OWN address - never the company
    address."""
    snap = parse_felag_pdf(_fixture("felag_8664_direct-stjori.bin.txt"))
    assert snap["registration_number"] == "8664"
    assert snap["company_name"] == "ÍVF Trolloftið"
    assert snap["company_address"]["role"] == "COMPANY_ADDRESS"
    assert snap["company_address"]["street"] == "á Nesi 40"
    assert len(snap["management"]) == 1
    person = snap["management"][0]
    assert person["role"] == "Stjóri"
    assert H(person["name"]) == '70c542052442468b'
    assert person["address"]["role"] == "MANAGEMENT_ADDRESS"
    assert H(person["address"]["verbatim"]) == '961671996519cf1b'
    assert snap["unknown_sections"] == []
    assert snap["needs_review"] is False


def test_pilot_foundation_lysing_layout_is_lossless():
    """Live discovery (c8023/c8664): FOUNDATION notices (Stovningardato,
    Stovnarar, Kapitalur, Inngjaldingarháttur...) are a layout the parser
    does not structure - everything must be retained verbatim, the regnr
    gate must still verify, and no address may be misattributed.

    E2 changes the initial-member semantics: the action-prefix-free Stjórn
    line is an APPOINTED founding entry (origin='FOUNDING'), not a lost
    change - while the surrounding scalar sections stay verbatim evidence.
    """
    notice = parse_lysing_pdf(_fixture("lys_33208_20-06-2022.bin.txt"))
    assert notice["registration_number"] == "8023"
    assert notice["journal_number"] == "22-478"
    # the founding member is structured, so nothing is left unsorted
    assert notice["review_reasons"] == []
    labels = {u["label"] for u in notice["unknown_sections"]}
    assert {"Stovningardato", "Viðtøkudato", "Inngjaldingarháttur",
            "Roknskaparár", "Fyrsta roknskaparár"} <= labels
    # founding values still become dated first-state entries
    types = {(c["type"], c["action"]) for c in notice["changes"]}
    assert ("NAME_CHANGE", "CHANGED") in types
    assert ("CAPITAL_CHANGE", "CHANGED") in types
    # the founding Stjórn line becomes the dated first management entry
    structured_stjorn = [c for c in notice["changes"]
                         if c["type"] == "MANAGEMENT_CHANGE"]
    assert [(c["action"], c["origin"], c["effective_date"],
             H(c["entity"])) for c in structured_stjorn] == [
        ("APPOINTED", "FOUNDING", "2022-06-16", '385eb151942ca1fb')]
    assert '385eb151942ca1fb' in {
        H(part.strip())
        for part in re.split(r"[,\n]", structured_stjorn[0]["raw_text"])}
    # nothing is left under the Stjórn membership label
    assert not [u for u in notice["unknown_sections"]
                if u["label"] == "(Stjórn usorterede linjer)"]


# ---------------------------------------------------------------------------
# 100-company validation parser fixes (each backed by a real fetched fixture)
# ---------------------------------------------------------------------------

def test_stovningardato_with_op_line_in_any_section_is_not_founding():
    """Fix round 1 #4: scan EVERY section for an op line.

    The splitter attaches ``Farin úr nevndini:`` to the non-membership
    ``Navn`` section when no ``Nevnd:`` label precedes it. The founding
    decision must still see it, so the bare ``Grannskoðari`` line stays a
    leftover under review instead of becoming a FOUNDING auditor.
    """
    text = (
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  9001,  P/F Test, 100 Tórshavn.\n"
        "Stovningardato:  25-10-1977\n"
        "Navn:  P/F Nýtt Navn\n"
        "Farin úr nevndini: Anna Ø, Øvegisgøta 5, 100 Tórshavn\n"
        "Grannskoðari:\n"
        "Jógvan Test, Vegur 3, 100 Tórshavn\n"
        "Skráseting Føroya tann  12. mars 2021\n"
    )
    notice = parse_lysing_pdf(text)
    assert [c["type"] for c in notice["changes"]] == ["NAME_CHANGE"]
    assert not [c for c in notice["changes"] if c.get("origin") == "FOUNDING"]
    assert "MEMBERSHIP_UNSORTED" in notice["review_reasons"]
    assert any(u["label"] == "(Grannskoðari usorterede linjer)"
               for u in notice["unknown_sections"])


def test_100val_glued_nevnd_oneline_board_removal():
    """c2400 lys 27798: 'Nevnd  Farin úr nevndini:' glued on one line."""
    notice = parse_lysing_pdf(_fixture("lys_27798_19-03-2020.bin.txt"))
    assert notice["registration_number"] == "2400"
    board = [c for c in notice["changes"] if c["type"] == "BOARD_CHANGE"]
    assert [(c["action"], H(c["entity"])) for c in board] == [
        ("REMOVED", 'b5287160e684ca5d')]


def test_100val_glued_nevnd_multiline_and_naestformadur():
    """c2400 lys 30724: glued label+op AND a 'Innkomin sum næstformaður'
    action phrase (op-table extension)."""
    notice = parse_lysing_pdf(_fixture("lys_30724_18-05-2021.bin.txt"))
    board = [c for c in notice["changes"] if c["type"] == "BOARD_CHANGE"]
    actions = {(c["action"], H(c["entity"])) for c in board}
    assert ("REMOVED", 'bd22b5e6dc62ba4b') in actions
    assert any(a == "APPOINTED" for a, _ in actions)
    assert len(board) == 3


def test_100val_glued_stjorn_management():
    """c5272 lys 37650 (LVAÁ): 'Stjórn  Innkomin sum stjóri:' glued."""
    notice = parse_lysing_pdf(_fixture("lys_37650_20-11-2024.bin.txt"))
    mgmt = [c for c in notice["changes"]
            if c["type"] == "MANAGEMENT_CHANGE"]
    assert {(c["action"], H(c["entity"])) for c in mgmt} == {
        ("APPOINTED", '8f04dee4d8b8ea43'),
        ("REMOVED", 'e33f30775ade9264')}


def test_100val_e_ndamal_artifact_recovers_purpose():
    """c5272 lys 5053 (1977 foundation): extraction split 'E ndamál';
    the narrow artifact join must recover PURPOSE_CHANGE."""
    notice = parse_lysing_pdf(_fixture("lys_5053_25-10-1977.bin.txt"))
    assert notice["registration_number"] == "5272"
    types = {c["type"] for c in notice["changes"]}
    assert "PURPOSE_CHANGE" in types
    assert "NAME_CHANGE" in types


def test_100val_naest_artifact_join():
    from heimdal.pdfhist.parse import _normalize_extraction_artifacts
    assert _normalize_extraction_artifacts(
        "Innkomin sum næ stformaður: X") == \
        "Innkomin sum næstformaður: X"
    assert _normalize_extraction_artifacts("E ndamál: foo") == \
        "Endamál: foo"
    # narrow: unrelated 'næ st' sequences stay untouched
    assert _normalize_extraction_artifacts("næ st") == "næ st"


def test_100val_standalone_hjanavn_labels_are_other_change():
    """c34 lys 36254: 'Nýtt hjánavn:' as its own label -> OTHER_CHANGE."""
    notice = parse_lysing_pdf(_fixture("lys_36254_06-02-2024.bin.txt"))
    others = [c for c in notice["changes"] if c["type"] == "OTHER_CHANGE"]
    assert len(others) == 2
    joined = " ".join(c["raw_text"] for c in others)
    assert "hjánavn" in joined
    assert any("Nýtt hjánavn" in c["raw_text"] for c in others)


def test_100val_felag_forkvinna_board_roles():
    """c29 felag: gendered chair roles (Nevndarforkvinna) must be board
    members with their own addresses - never leftovers."""
    snap = parse_felag_pdf(_fixture("felag_29_forkvinna.bin.txt"))
    roles = [(p["role"], H(p["name"])) for p in snap["board"]]
    assert ("Nevndarforkvinna", '7818ac032d1757d5') in roles
    assert len(snap["board"]) == 4
    chair = next(p for p in snap["board"]
                 if p["role"] == "Nevndarforkvinna")
    assert chair["address"]["role"] == "BOARD_MEMBER_ADDRESS"
    leftovers = [u for u in snap["unknown_sections"]
                 if "usorterede" in u["label"]]
    assert leftovers == []


def test_100val_felag_kurator_is_management():
    """c3742 felag (í konkurs): the court-appointed Kurator is management."""
    snap = parse_felag_pdf(_fixture("felag_3742_kurator.bin.txt"))
    assert snap["registration_number"] == "3742"
    assert len(snap["management"]) == 1
    person = snap["management"][0]
    assert person["role"] == "Kurator"
    assert H(person["name"]) == '3051cb21cc38a28d'
    assert person["address"]["role"] == "MANAGEMENT_ADDRESS"
    # company address stays the c/o address, not the kurator's home
    assert snap["company_address"]["street"] == "c/o " + person["name"]


def test_100val_felag_naestforkvinna_and_forstjori():
    """c5334 felag: Næstforkvinna (board) + Forstjóri (management)."""
    snap = parse_felag_pdf(_fixture(
        "felag_5334_naestforkvinna-forstjori.bin.txt"))
    board_roles = {p["role"] for p in snap["board"]}
    assert "Næstforkvinna" in board_roles
    assert len(snap["board"]) == 6
    assert any(p["role"] == "Forstjóri" for p in snap["management"])
    leftovers = [u for u in snap["unknown_sections"]
                 if "usorterede" in u["label"]]
    assert leftovers == []


# ---------------------------------------------------------------------------
# LYS-GLUE-ONELINE-REGNR-01: glue-split x one-line-normalizer interaction
# (post-reparse regressions c5334/21913, c5334/22188, c5334/24598 and
#  c5273/21917: _split_glued_membership_labels() injects newlines, the count
#  passes _ONELINE_MAX_NEWLINES, so 'fráboðan:Skrásetingar-nr.' stays glued
#  -> registration_number None -> LAYOUT_DRIFT + REGISTRATION_NUMBER_MISSING).
# Binding invariant: one-line classification is decided on the SOURCE text
# layout (newlines as extracted), never on newlines that earlier
# normalizer passes have themselves inserted.
# ---------------------------------------------------------------------------

def test_lys_glue_oneline_regnr_01_c5334_board_repair():
    """c5334 lys 21913 (2017, FVAÁ): regnr pseudo-label glued behind
    'fráboðan:' AND glued family+op lines in the same notice."""
    notice = parse_lysing_pdf(_fixture("lys_21913.bin.txt"))
    assert notice["document_kind"] == "REGISTRATION_NOTICE"
    assert notice["registration_number"] == "5334"
    assert notice["review_reasons"] == []
    board = [c for c in notice["changes"] if c["type"] == "BOARD_CHANGE"]
    actions = {(c["action"], H(c["entity"])) for c in board}
    assert ("REMOVED", '6a0b1ba23c7cd0f7') in actions
    assert ("REMOVED", 'eadf465fb134e2de') in actions
    assert ("APPOINTED", '7c6a7f0f6c36f167') in actions
    assert ("APPOINTED", '23b804cff47ad5bd') in actions
    # raw evidence stays lossless for every parsed op
    assert all(c["raw_text"] for c in notice["changes"])
    joined = "\n".join(c["raw_text"] for c in board)
    assert "Farin úr nevndini" in joined


def test_lys_glue_oneline_regnr_01_c5273_articles_name_board():
    """c5273 lys 21917 (2017, LVAÁ): same interaction in a notice with
    Viðtøkur + Nýtt navn + glued Nevnd ops."""
    notice = parse_lysing_pdf(_fixture("lys_21917.bin.txt"))
    assert notice["document_kind"] == "REGISTRATION_NOTICE"
    assert notice["registration_number"] == "5273"
    assert notice["review_reasons"] == []
    articles = next(c for c in notice["changes"]
                    if c["type"] == "ARTICLES_CHANGE")
    assert articles["effective_date"] == "2017-04-29"
    names = [c for c in notice["changes"] if c["type"] == "NAME_CHANGE"]
    assert names and "Føroya Keypssamtøka" in names[0]["entity"]
    board = [c for c in notice["changes"] if c["type"] == "BOARD_CHANGE"]
    actions = {(c["action"], H(c["entity"])) for c in board}
    assert ("APPOINTED", '5f6a6576d18bde14') in actions
    assert ("REMOVED", '810b0d8d7fcebdd2') in actions
    assert all(c["raw_text"] for c in notice["changes"])


def test_lys_glue_oneline_regnr_01_gate_uses_source_layout_not_repair_lines():
    """The oneline gate classifies the SOURCE text's own newline structure:
    newlines that _split_glued_membership_labels() inserts as part of its
    repair must never flip the classification for a source text inside the
    gate - and a source text genuinely over the gate stays untouched."""
    from heimdal.pdfhist.parse import normalize_oneline_lysing_text
    import re

    def source_text(n_source_newlines):
        glue = "Nevnd  Farin úr nevndini: P. Hansen"
        body = "Fráboðari: X\n" * n_source_newlines
        return glue + body + "fráboðan:Skrásetingar-nr.  FVAÁ  5334"

    # 8 source newlines: at the gate (<= 8) -> must normalize (repair does
    # not count); simulated post-repair text carries the extra lines.
    inside = source_text(8)
    assert inside.count("\n") == 8
    post_repair = re.sub(r"Nevnd  (Farin úr nevndini)",
                         r"Nevnd:\n\1", inside)
    assert post_repair.count("\n") > 8
    assert "\nSkrásetingar-nr." in normalize_oneline_lysing_text(
        post_repair, gate_newlines=8)

    # 9+ source newlines: a genuinely multi-line layout stays byte-identical
    over = source_text(9)
    assert normalize_oneline_lysing_text(over, gate_newlines=9) == over

    # default (no gate) keeps the old single-argument behaviour
    one = "fráboðan:Skrásetingar-nr.  FVAÁ  5334"
    assert "\nSkrásetingar-nr." in normalize_oneline_lysing_text(one)


def test_lys_glue_oneline_regnr_01_scalar_opener_reunion():
    """'Nýtt navn:'/'Nýggj hjánøvn:' detached from a bare parent scalar label
    is the parent's VALUE (multiline convention), never a section of its own.
    Live 21917/26980/24340: without the reunion the scalar keeps an empty
    entity and the value survives only in unknown_sections."""
    from heimdal.pdfhist.parse import normalize_oneline_lysing_text, \
        split_labeled_sections
    glued = ("Skrásetingar-nr.  ÍVF  6781\n"
             "Fráboðari: X, S 4, 110 Tórshavn."
             "Viðtøkur broyttar: 01.08.2018"
             "Navn:  Nýtt navn: ÍVF OJ Service"
             "Hjánøvn:  Nýggj hjánøvn: ÍVF Test. "
             "Skráseting Føroya tann  1. august 2018")
    text = normalize_oneline_lysing_text(glued)
    assert "Navn: Nýtt navn: ÍVF OJ Service" in text
    assert "Hjánøvn: Nýggj hjánøvn:" in text
    sections, _ = split_labeled_sections(text)
    labels = dict(sections)
    assert "Nýtt navn" not in labels          # not its own section
    assert labels.get("Navn", "").startswith("Nýtt navn:")
    # only reunites ADJACENT bare parents; bodies on one line stay untouched
    inline = normalize_oneline_lysing_text("Navn: P/F X")
    assert normalize_oneline_lysing_text(inline) == inline


# ---------------------------------------------------------------------------
# LYS-VARALIMUR-OP-01 + LYS-STANDALONE-NAME-02 (250-expansion discoveries)
# ---------------------------------------------------------------------------
# c200/lys 30810 (2021): 'Farin frá sum varalimur: <x>' is a deputy board
# membership op that was missing from LYSING_OP_TABLE - its line opened an
# unknown section that swallowed the FOLLOWING known op line
# ('Innkomin sum nevndarformaður:'), so the appointment was lost from
# changes[] (raw text stayed lossless).
# c67/lys 4762 (2011): standalone 'Nýtt navn:' (plus 'Kommuna:'/'Adressa:')
# after 'Viðtøkur broyttar:' - the name change was retained only in
# unknown_sections. ø-alias variants 'Nýggj hjánøvn:' follow the existing
# 'Nýtt hjánavn' (c34) handling.

def test_lys_varalimur_op_01_c200_deputy_swallowed_appointment():
    """Deputy removal + new chairman are both BOARD_CHANGEs; the deputy op
    must no longer detach the following known op into an unknown section."""
    notice = parse_lysing_pdf(_fixture("lys_30810.bin.txt"))
    assert notice["registration_number"] == "200"
    assert notice["review_reasons"] == []
    board = [(c["action"], H(c["entity"]))
             for c in notice["changes"] if c["type"] == "BOARD_CHANGE"]
    assert ("REMOVED", '3ae30f7b872419f3') in board
    assert ("REMOVED", 'ba94764da201aa60') in board      # varalimur
    assert ("APPOINTED", 'ba94764da201aa60') in board    # formaður
    labels = [u.get("label") for u in notice["unknown_sections"]]
    assert "Farin frá sum varalimur" not in labels
    # raw evidence stays lossless for every parsed op
    assert all(c["raw_text"] for c in notice["changes"])


def test_lys_standalone_name_02_c67_nyttnavn_kommuna_adressa():
    """Standalone 'Nýtt navn:' becomes a NAME_CHANGE; Kommuna/Adressa stay
    lossless unknown_sections (no lysing address role is guessed)."""
    notice = parse_lysing_pdf(_fixture("lys_4762.bin.txt"))
    assert notice["registration_number"] == "67"
    assert notice["review_reasons"] == []
    name_changes = [c for c in notice["changes"]
                    if c["type"] == "NAME_CHANGE"]
    assert len(name_changes) == 1
    assert name_changes[0]["entity"] == "P/F Akraberg FD 10"
    assert name_changes[0]["action"] == "CHANGED"
    assert name_changes[0]["raw_text"] == \
        "Nýtt navn: P/F Akraberg FD 10"
    assert name_changes[0]["effective_date"] is None           # EVENT_ONLY
    labels = [u.get("label") for u in notice["unknown_sections"]]
    assert "Nýtt navn" not in labels
    assert "Kommuna" in labels and "Adressa" in labels


def test_lys_standalone_name_02_hjanovn_o_variant():
    """'Nýggj hjánøvn:' (ø) parses like the established 'Nýtt hjánavn' (á)
    standalone alias: OTHER_CHANGE with the alias as entity (live c1008,
    c3195, c5047)."""
    notice = parse_lysing_pdf(
        "Skrásetingar-nr.  P/F  1008,  P/F  Vestlax, Vestgøta 1.\n"
        "Nýggj hjánøvn: P/F Vestlax (P/F Bakkafrost Farming West)\n"
        "Skráseting Føroya tann  3. maj 2022")
    assert notice["registration_number"] == "1008"
    alias = [c for c in notice["changes"]
             if c["type"] == "OTHER_CHANGE"]
    assert len(alias) == 1
    assert alias[0]["entity"].startswith("P/F Vestlax")
    assert alias[0]["raw_text"].startswith("Nýggj hjánøvn:")


def test_lys_varalimur_op_01_fixture_bytes_extract_equally():
    """The stored fixture bytes re-extract to exactly the committed fixture
    text - the regression pins the FULL chain from stored PDF bytes."""
    from heimdal.pdfhist.testsupport import fixture_bytes
    text = extract_text(fixture_bytes("lys_30810.bin"))
    assert text == _fixture("lys_30810.bin.txt")


# ---------------------------------------------------------------------------
# LYS-STJORN-ABYRGDARHAVANDI-03 + LYS-NYGG-NAVN-VARIANT-04 (250-expansion)
# ---------------------------------------------------------------------------
# 'Innkomin/Farin frá sum ábyrgdarhavandi' (e.g. the probate court entering
# as person in charge under winding-up) is a Stjórn-level membership op -
# the live glue line 'Stjóri  Innkomin sum ábyrgdarhavandi:' (c6918/33662)
# proves the family. Before the table entries the op line detached into an
# unknown section and the FOLLOWING known ops lost their raw separation
# (c6469: one 'Innkomin sum stjóri' entry swallowed three lines).
# 'Nýgg navn:' is the source's own spelling variant of 'Nýtt navn:'.

def test_lys_stjorn_abyrgdarhavandi_01_c6918_glued_administrator_op():
    notice = parse_lysing_pdf(_fixture("lys_33662.bin.txt"))
    assert notice["registration_number"] == "6918"
    assert notice["review_reasons"] == []
    mgmt = [(c["action"], c["entity"])
            for c in notice["changes"] if c["type"] == "MANAGEMENT_CHANGE"]
    assert ("APPOINTED", "Føroya Skiftirættur") in mgmt
    assert ("REMOVED", '7e7769f65b09918f') in [(a, H(e)) for a, e in mgmt]
    labels = [u.get("label") for u in notice["unknown_sections"]]
    assert "Stjóri  Innkomin sum ábyrgdarhavandi" not in labels
    # each op line keeps ONLY its own raw text (no swallowing of neighbours)
    for c in notice["changes"]:
        assert c["raw_text"].count("Farin frá") <= 1


def test_lys_stjorn_abyrgdarhavandi_01_no_op_swallowing_next_lines():
    """A 'Innkomin sum stjóri' op line must not swallow the following
    'Farin frá sum ábyrgdarhavandi' / 'UPPAFTURTØKA' lines into its raw_text;
    the ábyrgdarhavandi removal becomes its own MANAGEMENT_CHANGE (live
    c6469 pattern, synthetic minimal notice)."""
    notice = parse_lysing_pdf(
        "Skrásetingar-nr.  ÍVF  6469,  ÍVF  Eksempel, 100 Tórshavn.\n"
        "Stjórn:\n"
        "Innkomin sum stjóri: Testur Testsen á Bakka, Testvegur 45,\n"
        "100 Tórshavn\n"
        "Farin frá sum ábyrgdarhavandi: Føroya Skiftirættur, C. Pløyensgøta 1,\n"
        "100 Tórshavn\n"
        "UPPAFTURTØKA: Felagið tann 01.10.2019 afturkallað úr skriftirættinum\n"
        "Skráseting Føroya tann  1. oktober 2019")
    assert notice["registration_number"] == "6469"
    mgmt = [(c["action"], c["entity"])
            for c in notice["changes"] if c["type"] == "MANAGEMENT_CHANGE"]
    assert ("APPOINTED", "Testur Testsen á Bakka") in mgmt
    assert ("REMOVED", "Føroya Skiftirættur") in mgmt
    appointed = next(c for c in notice["changes"]
                     if c["entity"] == "Testur Testsen á Bakka")
    assert "ábyrgdarhavandi" not in appointed["raw_text"]
    assert "UPPAFTURTØKA" not in appointed["raw_text"]
    labels = [u.get("label") for u in notice["unknown_sections"]]
    assert "Farin frá sum ábyrgdarhavandi" not in labels


def test_lys_nygg_navn_variant_04_c8672_name_change():
    """'Nýgg navn:' (source spelling variant) parses as NAME_CHANGE."""
    notice = parse_lysing_pdf(_fixture("lys_38088.bin.txt"))
    assert notice["registration_number"] == "8672"
    name_changes = [c for c in notice["changes"]
                    if c["type"] == "NAME_CHANGE"]
    assert len(name_changes) >= 1
    assert any(c["entity"].startswith("Sp/F Føroya Keypssamtøka")
               for c in name_changes)
    labels = [u.get("label") for u in notice["unknown_sections"]]
    assert "Nýgg navn" not in labels


# ---------------------------------------------------------------------------
# FEL-FOOTER-STREET-05 (250-expansion): a company address ON the registry's
# own street (Sigmundargøta 32) had its street line eaten by the footer
# filter's bare 'Sigmundargøta' alternative. Real footer lines always carry
# 'Skráseting Føroya' or 'Skr. nr.' (corpus-wide scan).

def test_fel_footer_street_05_c6176_company_on_registry_street():
    snap = parse_felag_pdf(_fixture("fel_136811.bin.txt"))
    assert snap["registration_number"] == "6176"
    addr = snap["company_address"]
    assert addr["role"] == "COMPANY_ADDRESS"
    assert addr["street"] == "Sigmundargøta 32"
    assert addr["verbatim"] == "Sigmundargøta 32\n100 Tórshavn"
    assert addr["kommuna"] == "Tórshavnar"
    # footer material still never reaches an attributed address
    assert "Boks" not in addr["verbatim"]
    assert "Skráseting Føroya" not in addr["verbatim"]


def test_fel_footer_street_05_footer_lines_still_filtered():
    """The tightened footer filter still suppresses the real footer line
    form (same lines carry the registry identity)."""
    snap = parse_felag_pdf(_fixture("felag_10.bin.txt"))
    assert snap["company_address"]["street"] == "Oknarvegur 5"
    assert "Sigmundargøta" not in snap["company_address"]["verbatim"]
