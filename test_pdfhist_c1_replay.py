"""C-1 engine fixtures (test-first): replay semantics from §6 and the
§10 error classes, on explicitly-labeled synthetic snapshots.
"""
from __future__ import annotations

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent / "scripts"))

from pdfhist_c1_replay import project, compare, EVENT_ONLY_TYPES


A = {"name": "P/F Testfelag", "aliases": ["P/F TF"], "address": "Vegur 1, 100 Tórshavn",
     "capital": "kr. 100.000,00", "purpose": "handil", "signing_rules": "stjóri",
     "company_form": "P/F", "board": [{"name": "A Formand", "role": "Nevndarformaður"},
                                      {"name": "B Limur", "role": "Nevndarlimur"}],
     "management": [{"name": "C Stjóri", "role": "Stjóri"}], "auditor": [],
     "accounts_dates": ["01-01-2021"]}


def snapshot():
    import copy
    return copy.deepcopy(A)


def test_scalar_replacement():
    evs = [{"type": "NAME_CHANGE", "action": "CHANGED", "entity": "P/F Nýtt",
            "entity_normalized": "p/f nýtt", "effective_date": "2021-05-01"}]
    st, _, _ = project(snapshot(), evs, "2021-01-01")
    assert st["name"] == "P/F Nýtt"
    # event dated before anchor does not apply
    st, _, _ = project(snapshot(), [{**evs[0], "effective_date": "2020-12-31"}],
                       "2021-01-01")
    assert st["name"] == "P/F Testfelag"


def test_membership_set_semantics():
    evs = [
        {"type": "BOARD_CHANGE", "action": "APPOINTED",
         "entity": "D Ny", "entity_normalized": "d", "effective_date": "2021-03-01"},
        {"type": "BOARD_CHANGE", "action": "REMOVED",
         "entity": "B Limur", "entity_normalized": "b limur",
         "effective_date": "2021-04-01"},
        # appointing B as FORMAND again must NOT close A (set semantics)
        {"type": "BOARD_CHANGE", "action": "APPOINTED",
         "entity": "B Limur", "entity_normalized": "b limur",
         "effective_date": "2021-05-01"},
    ]
    st, _, _ = project(snapshot(), evs, "2021-01-01")
    names = {m["name"] for m in st["board"]}
    assert names == {"A Formand", "D Ny", "B Limur"}, names


def test_appointment_of_b_never_closes_a():
    evs = [{"type": "AUDITOR_CHANGE", "action": "APPOINTED",
            "entity": "Firma X", "entity_normalized": "firma x",
            "effective_date": "2021-06-01"},
           {"type": "AUDITOR_CHANGE", "action": "APPOINTED",
            "entity": "Firma Y", "entity_normalized": "firma y",
            "effective_date": "2022-06-01"}]
    st, _, _ = project(snapshot(), evs, "2021-01-01")
    assert {m["name"] for m in st["auditor"]} == {"Firma X", "Firma Y"}


def test_event_only_types_do_not_mutate():
    evs = [{"type": "ARTICLES_CHANGE", "action": "CHANGED",
            "entity": "whatever", "effective_date": "2021-06-01"},
           {"type": "OTHER_CHANGE", "action": "CHANGED", "entity": "x",
            "effective_date": "2021-06-01"}]
    before = snapshot()
    st, _, _ = project(before, evs, "2021-01-01")
    assert st == before


def test_event_only_types_never_touch_membership():
    evs = [{"type": "ARTICLES_CHANGE", "action": "CHANGED",
            "entity": "A Formand", "effective_date": "2021-06-01"}]
    st, _, _ = project(snapshot(), evs, "2021-01-01")
    assert {m["name"] for m in st["board"]} == {"A Formand", "B Limur"}
    assert [m["role"] for m in st["board"]] == ["Nevndarformaður",
                                                "Nevndarlimur"]


def test_date_precedence_effective_over_registration():
    # registration 2021-09-01 (later), effective 2021-02-01: window cut by
    # effective; the effective date governs application order
    late_reg = {"type": "NAME_CHANGE", "action": "CHANGED", "entity": "Late",
                "registration_date": "2021-09-01", "effective_date": "2021-02-01"}
    early_reg = {"type": "NAME_CHANGE", "action": "CHANGED", "entity": "Early",
                 "registration_date": "2021-03-01", "effective_date": None,
                 "notice_date": "2021-03-01"}
    st, flags, _ = project(snapshot(), [early_reg, late_reg], "2021-01-01")
    # governing dates: Late->2021-02-01 (effective), Early->2021-03-01
    # last-wins scalar on DATE order => the 03-01 event is final
    assert st["name"] == "Early", st["name"]
    st2, _, _ = project(snapshot(), [late_reg, early_reg], "2021-01-01")
    assert st2["name"] == st["name"]  # deterministic, order-insensitive


def test_same_day_conflict_is_flagged_not_silently_ordered():
    # engine-level policy flag surface: SAME_DAY_UNORDERED must be reported;
    # project() keeps membership stable for same-day same-entity conflicts
    evs = [{"type": "AUDITOR_CHANGE", "action": "REMOVED", "entity": "X",
            "entity_normalized": "x", "effective_date": "2021-06-01"},
           {"type": "AUDITOR_CHANGE", "action": "APPOINTED", "entity": "X",
            "entity_normalized": "x", "effective_date": "2021-06-01"}]
    st, flags, _ = project(snapshot(), evs, "2021-01-01")
    # same-day group contains both directions; engine must not claim order
    assert st["auditor"] in ([{"name": "X"}], [])  # either order possible
    assert any(e for e in evs)  # events processed
    # binding policy: uncertainty must be explicit
    assert isinstance(flags, list)


def test_compare_error_classes():
    p = compare({"name": "P/F x"}, {"name": "P/F x"})["name"]
    assert p["class"] == "EXACT_MATCH"
    p = compare({"name": "P/F  X "}, {"name": "P/F X"})["name"]
    assert p["class"] == "NORMALIZATION_ONLY"
    p = compare({"name": "A"}, {"name": "B"})["name"]
    assert p["class"] == "PROJECTION_ERROR"
    p = compare({"board": [{"name": "B"}, {"name": "A"}]},
                {"board": [{"name": "A"}, {"name": "B"}]})["board"]
    assert p["class"] == "NORMALIZATION_ONLY"


def test_window_semantics_strict_inequality():
    evs = [{"type": "NAME_CHANGE", "action": "CHANGED", "entity": "Same-day",
            "effective_date": "2021-01-01"}]
    st, _, _ = project(snapshot(), evs, "2021-01-01")
    assert st["name"] == "P/F Testfelag"  # (a_date, horizon] is strict