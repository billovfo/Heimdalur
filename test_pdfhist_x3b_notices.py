"""X3b parser proof: liquidator/trustee/supervisory-board ops and mergers.

Synthetic register-style notices only. Real corpus values stay out of git
(audit 2026-11 I-5). No database, no network.
"""
from heimdal.pdfhist.parse import parse_lysing_pdf


def _types(notice):
    return [change.get("type") for change in notice["changes"]]


def test_likvidator_appointment_and_removal_are_management_changes():
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  Sp/f  9101,  Sp/f Testanøvn, 100 Tórshavn.\n"
        "Stjórn:  Innkomin sum likvidator: Eitt Testnavn, Testgøta 1, "
        "100 Tórshavn\n"
        "Farin frá sum likvidator: Annað Testnavn, Testgøta 2, "
        "100 Tórshavn\n"
        "Skráseting Føroya tann  12. mars 2021\n")
    assert notice["review_reasons"] == []
    management = [c for c in notice["changes"]
                  if c["type"] == "MANAGEMENT_CHANGE"]
    assert [(c["action"], c["entity"], c["role"]) for c in management] == [
        ("APPOINTED", "Eitt Testnavn", "Likvidator"),
        ("REMOVED", "Annað Testnavn", "Likvidator"),
    ]


def test_kurator_appointment_and_removal_carry_the_source_role():
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  Sp/f  9101,  Sp/f Testanøvn, 100 Tórshavn.\n"
        "Stjórn:  Innkomin sum kurator: Eitt Testnavn, Testgøta 1, "
        "100 Tórshavn\n"
        "Farin frá sum kurator: Annað Testnavn, Testgøta 2, 100 Tórshavn\n"
        "Skráseting Føroya tann  12. mars 2021\n")
    assert notice["review_reasons"] == []
    management = [c for c in notice["changes"]
                  if c["type"] == "MANAGEMENT_CHANGE"]
    assert [(c["action"], c["role"]) for c in management] == [
        ("APPOINTED", "Kurator"), ("REMOVED", "Kurator")]


def test_grannskodari_appointment_is_an_auditor_change():
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  Sp/f  9101,  Sp/f Testanøvn, 100 Tórshavn.\n"
        "Grannskoðari:  Innkomin sum grannskoðari: P/F Testgrannskoðing, "
        "Testgøta 3, 100 Tórshavn\n"
        "Skráseting Føroya tann  12. mars 2021\n")
    assert notice["review_reasons"] == []
    auditors = [c for c in notice["changes"]
                if c["type"] == "AUDITOR_CHANGE"]
    assert [(c["action"], c["entity"]) for c in auditors] == [
        ("APPOINTED", "P/F Testgrannskoðing")]


def test_nevndarformadur_removal_is_a_board_change():
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  Sp/f  9101,  Sp/f Testanøvn, 100 Tórshavn.\n"
        "Nevnd:  Farin úr nevndini sum nevndarformaður: Eitt Testnavn, "
        "Testgøta 1, 100 Tórshavn\n"
        "Innkomin í nevndina: Annað Testnavn, Testgøta 2, 100 Tórshavn "
        "(nevndarlimur)\n"
        "Skráseting Føroya tann  12. mars 2021\n")
    assert notice["review_reasons"] == []
    board = [c for c in notice["changes"] if c["type"] == "BOARD_CHANGE"]
    assert [(c["action"], c["entity"]) for c in board][0] == (
        "REMOVED", "Eitt Testnavn")


def test_umsjonarrad_ops_parse_in_their_own_section_and_glued_nevnd():
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  Sp/f  9102,  Sp/f Testasparikassi, "
        "100 Tórshavn.\n"
        "Umsjónarráð:  Farin úr umsjónarráðnum: Eitt Testnavn, Testgøta 1, "
        "100 Tórshavn\n"
        "Innkomin í umsjónarráðið: Annað Testnavn, Testgøta 2, "
        "100 Tórshavn\n"
        "Skráseting Føroya tann  12. mars 2021\n")
    assert notice["review_reasons"] == []
    assert [(c["type"], c["action"], c["entity"])
            for c in notice["changes"]] == [
        ("BOARD_CHANGE", "REMOVED", "Eitt Testnavn"),
        ("BOARD_CHANGE", "APPOINTED", "Annað Testnavn")]

    glued = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  Sp/f  9102,  Sp/f Testasparikassi, "
        "100 Tórshavn.\n"
        "Nevnd  Farin úr umsjónarráðnum: Eitt Testnavn, Testgøta 1, "
        "100 Tórshavn (nevndarlimur)\n"
        "Innkomin í umsjónarráðið: Annað Testnavn, Testgøta 2, "
        "100 Tórshavn (nevndarlimur)\n"
        "Skráseting Føroya tann  12. mars 2021\n")
    assert glued["review_reasons"] == []
    assert [(c["action"], c["entity"]) for c in glued["changes"]] == [
        ("REMOVED", "Eitt Testnavn"), ("APPOINTED", "Annað Testnavn")]


def test_merger_plan_with_artifact_and_party_lines():
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  9201,  P/F Test A, 100 Tórshavn.\n"
        "Fusión:  Skráseting Føroya hevur í dag móttikið fusiónsæ tlan "
        "samsv. § 134 a í P/F-lógini:\n"
        "A. Navn: P/F Test A (P/F-nr.9201), 100 Tórshavn\n"
        "B: Navn: P/F Test B (P/F-nr. 9202), 200 Klaksvík.\n"
        "Harumframt hava vit móttikið váttan.\n"
        "Skráseting Føroya tann  12. mars 2021\n")
    assert notice["review_reasons"] == []
    mergers = [c for c in notice["changes"] if c["type"] == "MERGER"]
    assert len(mergers) == 1
    plan = mergers[0]
    assert plan["action"] == "PLAN_REGISTERED"
    assert plan["parties"] == [
        {"side": "A", "name": "P/F Test A", "registration_number": "9201"},
        {"side": "B", "name": "P/F Test B", "registration_number": "9202"},
    ]
    assert "fusiónsæ tlan" in plan["raw_text"]
    assert "A. Navn: P/F Test A" in plan["raw_text"]
    assert "B: Navn: P/F Test B" in plan["raw_text"]
    # party lines are never a name change or a scalar
    assert "NAME_CHANGE" not in _types(notice)
    assert "ADDRESS_CHANGE" not in _types(notice)


def test_merger_plan_accepts_the_fusoin_label_typo():
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  9201,  P/F Test A, 100 Tórshavn.\n"
        "Fusoín:  Skráseting Føroya hevur í dag móttikið fusiónsætlan "
        "samsv. § 134 a í P/F-lógini:\n"
        "A. Navn: P/F Test A (P/F-nr.9201), 100 Tórshavn\n"
        "B: Navn: P/F Test B (P/F-nr. 9202), 200 Klaksvík.\n")
    assert notice["review_reasons"] == []
    plan = next(c for c in notice["changes"] if c["type"] == "MERGER")
    assert plan["action"] == "PLAN_REGISTERED"
    assert [p["registration_number"] for p in plan["parties"]] == \
        ["9201", "9202"]


def test_completed_merger_with_wrapped_sentence():
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  9201,  P/F Test A, 100 Tórshavn.\n"
        "Fusión:  Felagið er samanlagt við skrásetingar-nr. P/F 9203, "
        "P/F Test C, sum samstundis verður\n"
        "strikað.\n"
        "Skráseting Føroya tann  12. mars 2021\n")
    assert notice["review_reasons"] == []
    merger = next(c for c in notice["changes"] if c["type"] == "MERGER")
    assert merger["action"] == "COMPLETED"
    assert merger["counterpart"] == {
        "registration_number": "9203", "name": "P/F Test C"}
    assert merger["raw_text"].startswith("Fusión: Felagið er samanlagt")
    assert "ADDRESS_CHANGE" not in _types(notice)


def test_merger_plan_raw_text_keeps_the_whole_section():
    """Review #1: continuation lines after the party lines are evidence too."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  9201,  P/F Test A, 100 Tórshavn.\n"
        "Fusión:  Skráseting Føroya hevur í dag móttikið fusiónsæ tlan "
        "samsv. § 134 a í P/F-lógini:\n"
        "A. Navn: P/F Test A (P/F-nr.9201), 100 Tórshavn\n"
        "Váttan frá A-boðnum.\n"
        "B: Navn: P/F Test B (P/F-nr. 9202), 200 Klaksvík.\n"
        "Harumframt hava vit móttikið váttan frá metingarmanninum.\n"
        "Skráseting Føroya tann  12. mars 2021\n")
    assert notice["review_reasons"] == []
    plan = next(c for c in notice["changes"] if c["type"] == "MERGER")
    assert plan["parties"] == [
        {"side": "A", "name": "P/F Test A", "registration_number": "9201"},
        {"side": "B", "name": "P/F Test B", "registration_number": "9202"},
    ]
    assert "Váttan frá A-boðnum." in plan["raw_text"]
    assert "Harumframt hava vit móttikið váttan" in plan["raw_text"]


def test_completed_merger_raw_text_keeps_text_after_strikad():
    """Review #1: the section does not end at 'strikað'."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  9201,  P/F Test A, 100 Tórshavn.\n"
        "Fusión:  Felagið er samanlagt við skrásetingar-nr. P/F 9203, "
        "P/F Test C, sum samstundis verður strikað.\n"
        "Fusiónin er ein fusión við virknað beinanvegin.\n"
        "Skráseting Føroya tann  12. mars 2021\n")
    assert notice["review_reasons"] == []
    merger = next(c for c in notice["changes"] if c["type"] == "MERGER")
    assert merger["action"] == "COMPLETED"
    assert "Fusiónin er ein fusión við virknað beinanvegin." \
        in merger["raw_text"]


def test_one_line_merger_plan_parses():
    """Review #2: the single-line layout keeps the A/B markers in the body."""
    notice = parse_lysing_pdf(
        "12.03.2021 Skrásetingar-nr.  P/F  9301,  P/F Test A, "
        "100 Tórshavn. Fusión:  Skráseting Føroya hevur í dag móttikið "
        "fusiónsæ tlan samsv. § 134 a í P/F-lógini: A. Navn: P/F Test A "
        "(P/F-nr.9301), 100 Tórshavn B: Navn: P/F Test B "
        "(P/F-nr. 9302), 200 Klaksvík. Harumframt hava vit móttikið "
        "váttan.")
    assert notice["review_reasons"] == []
    plan = next(c for c in notice["changes"] if c["type"] == "MERGER")
    assert plan["action"] == "PLAN_REGISTERED"
    assert plan["parties"] == [
        {"side": "A", "name": "P/F Test A", "registration_number": "9301"},
        {"side": "B", "name": "P/F Test B", "registration_number": "9302"},
    ]
    assert "Harumframt hava vit móttikið váttan" in plan["raw_text"]
    assert "NAME_CHANGE" not in _types(notice)
    assert "ADDRESS_CHANGE" not in _types(notice)


def test_one_line_non_merger_name_change_is_not_swallowed():
    """X5b review #8: the party-label guard needs a merger label earlier.

    'Test Sp/F. Navn:' is a real scalar change in a non-merger one-line
    notice; the old guard suppressed every '<Capital>.'-preceded Navn."""
    notice = parse_lysing_pdf(
        "12.03.2021 Skrásetingar-nr.  P/F  9401,  P/F Test, 100 Tórshavn. "
        "Test Sp/F. Navn: P/F Nýtt Navn Tekningarreglur: onnur")
    names = [c["entity"] for c in notice["changes"]
             if c["type"] == "NAME_CHANGE"]
    assert names == ["P/F Nýtt Navn"]
    assert notice["review_reasons"] == []


def test_umsjonarrad_glued_family_is_not_repaired():
    """Review #3: only the real glue form ('Nevnd') is repaired."""
    from heimdal.pdfhist.parse import _split_glued_membership_labels
    glued = "Umsjónarráð  Farin úr umsjónarráðnum: Eitt Testnavn"
    assert _split_glued_membership_labels(glued) == glued
    nevnd = "Nevnd  Farin úr umsjónarráðnum: Eitt Testnavn"
    assert _split_glued_membership_labels(nevnd) == (
        "\nNevnd:\nFarin úr umsjónarráðnum: Eitt Testnavn")


def test_merger_without_party_lines_stays_unknown_and_layout_drift():
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  9201,  P/F Test A, 100 Tórshavn.\n"
        "Fusión:  Skráseting Føroya hevur í dag móttikið fusiónsæ tlan "
        "samsv. § 134 a í P/F-lógini:\n")
    assert notice["changes"] == []
    assert {u["label"]: u["verbatim"] for u in notice["unknown_sections"]}[
        "Fusión"] == ("Skráseting Føroya hevur í dag móttikið "
                      "fusiónsæ tlan samsv. § 134 a í P/F-lógini:")
    assert "LAYOUT_DRIFT" in notice["review_reasons"]


def test_typo_op_prefixes_stay_membership_unsorted():
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  Sp/f  9101,  Sp/f Testanøvn, 100 Tórshavn.\n"
        "Nevnd:  Farin úr : Eitt Testnavn, Testgøta 1, 100 Tórshavn\n"
        "Stjórn:  Fari frá sum stjóri: Annað Testnavn, Testgøta 2, "
        "100 Tórshavn\n"
        "Grannskoðari:  Farin frá sum frannskoðari: P/F Testgrannskoðing, "
        "Testgøta 3, 100 Tórshavn\n"
        "Skráseting Føroya tann  12. mars 2021\n")
    assert notice["changes"] == []
    assert "MEMBERSHIP_UNSORTED" in notice["review_reasons"]
    assert {u["label"] for u in notice["unknown_sections"]} >= {
        "(Nevnd usorterede linjer)",
        "(Stjórn usorterede linjer)",
        "(Grannskoðari usorterede linjer)",
    }


def test_three_party_plan_is_not_guessed_into_name_changes():
    """Only the A/B plan shape is learned; extra parties stay source text."""
    notice = parse_lysing_pdf(
        "12.03.2021\n"
        "Skrásetingar-nr.  P/F  9201,  P/F Test A, 100 Tórshavn.\n"
        "Fusiónslýsing:  Skráseting Føroya hevur í dag móttikið "
        "fusiónsætlan samsv. § 237 í Vinnufelagslógini:\n"
        "A. Navn: P/F Test A (P/F-nr.9201), 100 Tórshavn\n"
        "B: Navn: P/F Test B (P/F-nr. 9202), 200 Klaksvík.\n"
        "C: Navn: P/F Test C (P/F-nr. 9203), 300 Vágur\n")
    assert all(c["type"] != "NAME_CHANGE" for c in notice["changes"])
    assert not any(c["type"] == "MERGER" for c in notice["changes"])
    assert {u["label"] for u in notice["unknown_sections"]} >= {
        "Fusiónslýsing", "A. Navn", "B", "C"}
