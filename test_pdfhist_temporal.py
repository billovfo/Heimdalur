"""Temporal change-validity tests (plan FASE 17/22): pure functions, no DB.

SCALAR_REPLACEMENT intervals are [t_i, t_{i+1}); SET_MEMBERSHIP pairs
APPOINTED/REMOVED by entity_normalized only; EVENT_ONLY passes through;
missing dates become CHANGE_DATE_MISSING review entries.
"""

from heimdal.pdfhist.temporal import build_change_history


def _scalar_change(c_type, entity, effective=None, notice=None,
                   registration=None):
    return {"type": c_type, "action": "CHANGED", "entity": entity,
            "entity_normalized": entity.lower(), "effective_date": effective,
            "notice_date": notice, "registration_date": registration,
            "raw_text": f"{c_type}: {entity}"}


def _member_change(c_type, action, entity, effective=None, notice=None,
                   registration=None):
    return {"type": c_type, "action": action, "entity": entity,
            "entity_normalized": entity.lower(), "effective_date": effective,
            "notice_date": notice, "registration_date": registration,
            "raw_text": f"{c_type}: {action}: {entity}"}


def test_scalar_01_replacement_intervals_are_from_inclusive_to_exclusive():
    history = build_change_history([
        _scalar_change("NAME_CHANGE", "P/F Aaaa", notice="2010-04-15"),
        _scalar_change("NAME_CHANGE", "P/F Bbbb", notice="2021-06-02"),
        _scalar_change("NAME_CHANGE", "P/F Cccc", notice="2024-03-17"),
    ])
    names = [e for e in history["entries"] if e["family"] == "NAME"]
    assert [(e["entity"], e["from_date"], e["to_date"]) for e in names] == [
        ("P/F Aaaa", "2010-04-15", "2021-06-02"),
        ("P/F Bbbb", "2021-06-02", "2024-03-17"),
        ("P/F Cccc", "2024-03-17", None),
    ]
    assert history["review"] == []


def test_scalar_date_order_effective_over_notice_over_registration():
    changes = [
        _scalar_change("CAPITAL_CHANGE", "DKK 100", effective="2020-01-10",
                       notice="2020-02-01", registration="2020-03-01"),
    ]
    entries = build_change_history(changes)["entries"]
    assert entries[0]["from_date"] == "2020-01-10"  # effective wins


def test_set_01_board_removal_closes_matching_entity_only():
    changes = [
        _member_change("BOARD_CHANGE", "APPOINTED", "Anna Testsen",
                       notice="2010-04-15"),
        _member_change("BOARD_CHANGE", "APPOINTED", "Bjørn Prøvesen",
                       notice="2026-04-10"),
        _member_change("BOARD_CHANGE", "REMOVED", "Bjørn Prøvesen",
                       effective="2027-03-15"),
    ]
    history = build_change_history(changes)
    removed = next(e for e in history["entries"]
                   if e["entity"] == "Bjørn Prøvesen")
    assert removed["from_date"] == "2026-04-10"
    assert removed["to_date"] == "2027-03-15"
    still_on = next(e for e in history["entries"]
                    if e["entity"] == "Anna Testsen")
    assert still_on["to_date"] is None
    assert history["review"] == []


def test_set_02_removal_without_opening_is_review_evidence():
    changes = [
        _member_change("AUDITOR_CHANGE", "REMOVED", "PwC hf.",
                       notice="2010-04-15"),
    ]
    history = build_change_history(changes)
    entries = [e for e in history["entries"] if e["family"] == "AUDITOR"]
    assert entries == []  # nothing enters the timeline
    assert [r["reason"] for r in history["review"]] == \
        ["MEMBERSHIP_CLOSE_WITHOUT_OPEN"]
    assert history["review"][0]["raw_text"].startswith("AUDITOR_CHANGE:")


def test_set_03_two_auditors_closing_in_one_lysing_pair_correctly():
    changes = [
        _member_change("AUDITOR_CHANGE", "REMOVED", "PwC hf.",
                       notice="2010-04-15"),
        _member_change("AUDITOR_CHANGE", "APPOINTED", "PwC Danmark",
                       notice="2010-04-15"),
    ]
    history = build_change_history(changes)
    audit = {e["entity"]: e for e in history["entries"]
             if e["family"] == "AUDITOR"}
    # a removal without an open interval produces NO timeline entry at all;
    # only the appointment (unchained) exists, open-ended
    assert set(audit) == {"PwC Danmark"}
    assert audit["PwC Danmark"]["to_date"] is None
    reasons = sorted(r["reason"] for r in history["review"])
    assert reasons == ["MEMBERSHIP_CLOSE_WITHOUT_OPEN"]


def test_set_03b_full_pair_appointed_then_removed_closes():
    changes = [
        _member_change("AUDITOR_CHANGE", "APPOINTED", "PwC Danmark",
                       notice="2010-04-15"),
        _member_change("AUDITOR_CHANGE", "REMOVED", "PwC Danmark",
                       notice="2015-11-25"),
        _member_change("AUDITOR_CHANGE", "APPOINTED", "Ny Skoðari",
                       notice="2015-11-25"),
    ]
    history = build_change_history(changes)
    audit = {e["entity"]: e for e in history["entries"]
             if e["family"] == "AUDITOR"}
    assert audit["PwC Danmark"]["to_date"] == "2015-11-25"
    assert audit["Ny Skoðari"]["to_date"] is None


def test_event_01_event_only_passthrough_without_interval_semantics():
    changes = [
        {"type": "ARTICLES_CHANGE", "action": "CHANGED", "entity": None,
         "entity_normalized": None, "effective_date": "2010-03-29",
         "notice_date": "2010-04-15", "registration_date": "2010-04-15",
         "raw_text": "Viðtøkur broyttar: 29.03.2010"},
        {"type": "OTHER_CHANGE", "action": "CHANGED",
         "entity": "alias", "entity_normalized": "alias",
         "effective_date": None, "notice_date": "2010-04-15",
         "registration_date": "2010-04-15", "raw_text": "Hjánøvn: ..."},
    ]
    history = build_change_history(changes)
    families = [e["family"] for e in history["entries"]]
    assert families == ["EVENT_ONLY", "EVENT_ONLY"]
    assert history["entries"][0]["from_date"] == "2010-03-29"
    assert history["review"] == []


def test_change_date_missing_is_a_review_entry():
    changes = [_scalar_change("NAME_CHANGE", "P/F UdenDato")]
    history = build_change_history(changes)
    assert history["entries"] == []
    assert [r["reason"] for r in history["review"]] == ["CHANGE_DATE_MISSING"]


def test_real_fixture_lysing_2596_through_temporal():
    from heimdal.pdfhist.parse import parse_lysing_pdf
    from heimdal.pdfhist.testsupport import fixture_text
    notice = parse_lysing_pdf(fixture_text("lys_2596_15-04-2010.bin.txt"))
    history = build_change_history(notice["changes"],
                                   source_event_date="2010-04-15")
    auditor = [e for e in history["entries"] if e["family"] == "AUDITOR"]
    # PwC hf. removal has no open interval in this notice -> review evidence;
    # PwC Danmark appointment opens an interval
    assert [(e["entity"], e["to_date"]) for e in auditor] == \
        [("PricewaterhouseCoopers Danmark", None)]
    name_entries = [e for e in history["entries"] if e["family"] == "NAME"]
    assert name_entries[-1]["to_date"] is None
    board = [e for e in history["entries"] if e["family"] == "BOARD"]
    assert len(board) == 1  # only the appointment; removal is unopened
    assert {r["reason"] for r in history["review"]} == \
        {"MEMBERSHIP_CLOSE_WITHOUT_OPEN"}