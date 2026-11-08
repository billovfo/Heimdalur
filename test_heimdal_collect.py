#!/usr/bin/env python3
"""Checks for the display-text parsers and response-shape guards.

Every string below is a verbatim value observed during reconnaissance
(HEIMDAL-COMPANY-DISCOVERY-RECONNAISSANCE.md). Run: python3 test_heimdal_collect.py
"""
from heimdal_collect import (SHAPES, classify_company_location,
                             classify_felag_pdf_address, classify_edge,
                             classify_role, compute_indirect_ownership, entity_kind,
                             PATH_SAMPLE_CAP,
                             entity_side_type, extract_felag_pdf_location,
                             find_cycle_components, is_person_type,
                             normalize_name, parse_search_display, parse_search_location,
                             parse_table_name, relation_basis)


def test_table_name_basic():
    assert parse_table_name("Betri #3690 - P/F, Kongabrúgvin Postrúm 329") == \
        ("Betri", "S3690", "P/F")
    assert parse_table_name("Anna Testsen Prøvesen - Persónur, Testvegur 9  Skopun Føroyar") == \
        ("Anna Testsen Prøvesen", None, "Persónur")


def test_table_name_embedded_separator():
    """The whole reason the split must be greedy: names contain " - " themselves.

    A leftmost split yields type="Búsetur #6479 - Í/F", which is nonsense.
    """
    name, regnr, type_text = parse_table_name(
        "Húsalánsgrunnurin - Búsetur #6479 - Í/F, Heykavegur 8")
    assert type_text == "Í/F", type_text
    assert regnr == "S6479"
    assert name == "Húsalánsgrunnurin - Búsetur"

    assert parse_table_name("Føroya Bústaðarfelag - FØ Eind, Tórshavn")[2] == "FØ Eind"


def test_table_name_multiword_type():
    assert parse_table_name("X #1 - Ikki vinnurekandi grunnur, Y")[2] == "Ikki vinnurekandi grunnur"
    assert parse_table_name("X - Útlendskir grunnar o.l., Y")[2] == "Útlendskir grunnar o.l."


def test_table_name_rejects_unparseable():
    assert parse_table_name("no separators at all") is None
    assert parse_table_name("name - type without comma") is None
    assert parse_table_name("name -  , addr") is None          # empty type


def test_search_display():
    assert parse_search_display("Betri (P/F S3690 , Kongabrúgvin Postrúm 329)") == \
        ("Betri", "S3690", "P/F")
    assert parse_search_display(
        "Anna Testsen Prøvesen (Persónur, Testvegur 9  Skopun Føroyar)") == \
        ("Anna Testsen Prøvesen", None, "Persónur")
    assert parse_search_display("01-07-2026 (Sp/F S9439 , Støðlagøta 14 )") == \
        ("01-07-2026", "S9439", "Sp/F")
    assert parse_search_display("garbage without parens") is None


def test_search_display_retains_its_address_tail():
    assert parse_search_location("Betri (P/F S3690 , Kongabrúgvin Postrúm 329)") == (
        "Betri", "S3690", "P/F", "Kongabrúgvin Postrúm 329")


def test_location_classification_never_guesses():
    postcodes = {"100": {"city": "Tórshavn"}}
    assert classify_company_location("Bryggjubakki 3, 100 Tórshavn", postcodes) == {
        "postal_code": "100", "country_code": "FO", "location_status": "FAROESE_CITY"}
    assert classify_company_location("2400 København NV, Danmark", postcodes) == {
        "postal_code": None, "country_code": "DK", "location_status": "FOREIGN"}
    assert classify_company_location("Address without a verified postcode", postcodes) == {
        "postal_code": None, "country_code": None, "location_status": "UNKNOWN"}


def test_po_box_numbers_never_claim_a_city():
    postcodes = {"180": {"city": "Kaldbak"}}
    for addr in ("Heiðavegur 21 Postrúm 180",
                 "Heiðavegur 21 Pakkaboks 180",
                 "Heiðavegur 21 Boks 180",
                 "Heiðavegur 21 PO Box 180",
                 "Heiðavegur 21 Box 180",
                 "Heiðavegur 21 postrúm 180"):
        assert classify_company_location(addr, postcodes) == {
            "postal_code": None, "country_code": None, "location_status": "UNKNOWN"}, addr


def test_foreign_marker_wins_over_colliding_faroese_postcode():
    postcodes = {"110": {"city": "Tórshavn"}}
    assert classify_company_location("110 Reykjavík, Ísland", postcodes) == {
        "postal_code": None, "country_code": "IS", "location_status": "FOREIGN"}


def test_felag_pdf_address_stops_before_board_addresses():
    text = """Navn: P/F Example
Adressa: Havnarvegur 1
100 Tórshavn
Kommuna: Tórshavnar
Nevnd: Navn Pætursgøta 8 700 Klaksvík"""
    assert classify_felag_pdf_address(text, {"100", "700"}) == {
        "postal_code": "100", "country_code": "FO", "location_status": "FAROESE_CITY"
    }


def test_felag_pdf_requires_kommuna_boundary():
    assert classify_felag_pdf_address("Adressa: Bryggjubakki 22 100 Tórshavn", {"100"}) is None


def test_felag_pdf_box_only_address_is_not_a_city():
    text = "Adressa: Kongabrúgvin Postrúm 329 110 Tórshavn\nKommuna: Tórshavnar"
    assert classify_felag_pdf_address(text, {"110"}) == {
        "postal_code": "110", "country_code": "FO", "location_status": "FAROESE_CITY"
    }


def test_felag_pdf_invalid_bytes_return_none():
    assert extract_felag_pdf_location(b"not a pdf", {"100"}) is None


def test_registration_number_is_the_same_across_endpoints():
    """get-table writes #3690, search-cbi writes S3690 — same company, same number."""
    from_table = parse_table_name("Betri #3690 - P/F, Kongabrúgvin Postrúm 329")
    from_search = parse_search_display("Betri (P/F S3690 , Kongabrúgvin Postrúm 329)")
    assert from_table[1] == from_search[1] == "S3690"


def test_normalize_name():
    assert normalize_name("Betri Banki") == "betri banki"
    assert normalize_name("Støðlagøta") == "stodlagota"
    assert normalize_name("Húsalánsgrunnurin") == "husalansgrunnurin"
    assert normalize_name("Á-Glyvrar") == "a-glyvrar"


def test_shapes_accept_real_payloads():
    assert SHAPES["search-cbi"]([{"r": 22, "d": "Betri (P/F S3690 , x)"}])
    assert SHAPES["search-cbi"]([])                              # empty is valid
    assert SHAPES["get-table"]([{"fromId": 1, "fromName": "a - T, x", "cbTypeId": 1,
                                 "cbTypeName": "Eigari 80.0%", "toId": 2, "toName": "b - T, y"}])
    assert SHAPES["get-table"]([])                               # zero relations is valid
    assert SHAPES["get-graph"]({"data": "<data><nodes ID=\"1\"/></data>"})


def test_classify_edge_direct_ownership():
    kind, f = classify_edge({"fromId": 1, "fromName": "a", "toId": 2, "toName": "b",
                             "cbTypeId": 1, "cbTypeName": "Eigari 15.68%"})
    assert kind == "RELATION"
    assert f["canonical_relation_type"] == "DIRECT_OWNERSHIP"
    assert f["canonical_subject_entity_id"] == 1          # owner = From (§14b.1)
    assert f["canonical_object_entity_id"] == 2
    assert f["parsed_percentage"] == 15.68
    assert f["percentage_parse_status"] == "PARSED"


def test_classify_edge_ownership_unparseable_label_never_becomes_zero():
    _, f = classify_edge({"fromId": 1, "fromName": "a", "toId": 2, "toName": "b",
                          "cbTypeId": 1, "cbTypeName": "Eigari mysterious"})
    assert f["percentage_parse_status"] == "FAILED"
    assert f["parsed_percentage"] is None                 # never a silent 0 (§14)


def test_classify_edge_malformed_percentage_never_crashes():
    """Regex allows multiple dots; float() must not blow up the whole batch on one bad label."""
    _, f = classify_edge({"fromId": 1, "fromName": "a", "toId": 2, "toName": "b",
                          "cbTypeId": 1, "cbTypeName": "Eigari 1.2.3%"})
    assert f["percentage_parse_status"] == "FAILED"
    assert f["parsed_percentage"] is None


def test_classify_edge_registered_control_actor():
    kind, f = classify_edge({"fromId": 5, "fromName": "Aktør A", "toId": 6, "toName": "Entity",
                             "cbTypeId": 41, "cbTypeName": "Veruligur Eigari"})
    assert kind == "RELATION"
    assert f["canonical_relation_type"] == "REGISTERED_CONTROL_ACTOR"
    assert f["canonical_subject_entity_id"] == 5
    assert f["raw_percentage_label"] is None               # not an ownership %, never assigned one


def test_classify_edge_ove_self_edge_is_status_not_relation():
    kind, f = classify_edge({"fromId": 9, "fromName": "Betri Banki", "toId": 9,
                             "toName": "Betri Banki", "cbTypeId": 81, "cbTypeName": "O.V.E."})
    assert kind == "STATUS"
    assert f == {"source_entity_id": 9, "canonical_status_type": "NO_REGISTERED_CONTROL_ACTOR",
                "raw_source_label": "O.V.E."}


def test_classify_edge_ove_non_self_edge_is_unresolved_anomaly_not_guessed():
    """§14a.2: O.V.E. with different From/To is an anomaly — never a canonical edge."""
    kind, f = classify_edge({"fromId": 9, "fromName": "A", "toId": 10, "toName": "B",
                             "cbTypeId": 81, "cbTypeName": "O.V.E."})
    assert kind == "RELATION"
    assert f["canonical_relation_type"] == "OTHER_SOURCE_RELATION"
    assert f["canonical_subject_entity_id"] is None
    assert f["semantic_direction_confidence"] == "UNCONFIRMED"
    assert f["semantic_status"] == "UNRESOLVED_SOURCE_ANOMALY"    # bug found 2026-08-02: was never set


def test_classify_edge_semantic_status_is_none_for_ordinary_rows():
    _, f = classify_edge({"fromId": 1, "fromName": "a", "toId": 2, "toName": "b",
                          "cbTypeId": 1, "cbTypeName": "Eigari 10.0%"})
    assert f["semantic_status"] is None


def test_classify_edge_voting_rights_not_conflated_with_ownership():
    """cbTypeId=101 also carries a '%' but is a different concept — must not be treated as Eigari."""
    kind, f = classify_edge({"fromId": 1, "fromName": "a", "toId": 2, "toName": "b",
                             "cbTypeId": 101, "cbTypeName": "Atkvøðurættur 100.0%"})
    assert f["canonical_relation_type"] == "OTHER_SOURCE_RELATION"
    assert f["parsed_percentage"] is None


def test_classify_edge_unknown_type_is_raw_only():
    _, f = classify_edge({"fromId": 1, "fromName": "a", "toId": 2, "toName": "b",
                          "cbTypeId": 32, "cbTypeName": "Nevndarlimur"})
    assert f["canonical_relation_type"] == "OTHER_SOURCE_RELATION"
    assert f["canonical_subject_entity_id"] is None        # direction UNCONFIRMED (§4)


def test_cycle_components_chain_has_no_cycle():
    assert find_cycle_components([("A", "B"), ("B", "C")]) == []


def test_cycle_components_self_loop():
    assert find_cycle_components([("A", "A")]) == [frozenset({"A"})]


def test_cycle_components_two_node_cycle():
    comps = find_cycle_components([("A", "B"), ("B", "A")])
    assert comps == [frozenset({"A", "B"})]


def test_cycle_components_self_loop_plus_larger_cycle_is_one_component():
    """Bug found 2026-08-02 (live data: entity 2537): a node can have BOTH a
    self-loop AND membership in a genuine larger cycle. It must appear in
    exactly ONE component (the larger one) — listing it twice gave it two
    different component indices downstream, breaking the same-component
    check for its real cycle partner.
    """
    comps = find_cycle_components([("A", "A"), ("A", "B"), ("B", "A")])
    assert comps == [frozenset({"A", "B"})]


def test_cycle_components_three_node_cycle():
    comps = find_cycle_components([("A", "B"), ("B", "C"), ("C", "A")])
    assert comps == [frozenset({"A", "B", "C"})]


def test_indirect_ownership_betri_worked_example():
    """The SoT doc's own example: Betri -> 100% -> Betri Banki -> 100% -> Betri Heim
    should yield exactly one indirect row, Betri -> Betri Heim at 100%, and no
    indirect row for either direct 1-hop pair.
    """
    edges = [("Betri", "BetriBanki", 100.0), ("BetriBanki", "BetriHeim", 100.0)]
    results = compute_indirect_ownership(edges, cycle_of={})
    assert len(results) == 1
    r = results[0]
    assert (r["owner_entity_id"], r["owned_entity_id"]) == ("Betri", "BetriHeim")
    assert r["computed_percentage"] == 100.0
    assert r["path_count"] == 1
    assert r["contributing_paths"] == [{"path": ["Betri", "BetriBanki", "BetriHeim"],
                                        "percentage": 100.0}]


def test_indirect_ownership_sums_multiple_paths():
    """Diamond: A owns 50% of B and 50% of C; B and C each own 100% of D.
    A's indirect stake in D is the SUM over both paths: 50% + 50% = 100%.
    """
    edges = [("A", "B", 50.0), ("A", "C", 50.0), ("B", "D", 100.0), ("C", "D", 100.0)]
    results = compute_indirect_ownership(edges, cycle_of={})
    ad = next(r for r in results if r["owner_entity_id"] == "A" and r["owned_entity_id"] == "D")
    assert ad["computed_percentage"] == 100.0
    assert ad["path_count"] == 2


def test_indirect_ownership_path_cap_keeps_totals_exact():
    """C-10 (audit 2026-11): real data never reaches PATH_SAMPLE_CAP, so the cap
    is checked here. 25 paths A -> Bi -> X: count and % stay exact, only the
    listed sample is capped, and the row says so."""
    edges = [("A", f"B{i}", 4.0) for i in range(25)] + [(f"B{i}", "X", 100.0) for i in range(25)]
    ax = next(r for r in compute_indirect_ownership(edges, cycle_of={})
              if (r["owner_entity_id"], r["owned_entity_id"]) == ("A", "X"))
    assert ax["path_count"] == 25 and ax["paths_truncated"] is True
    assert len(ax["contributing_paths"]) == PATH_SAMPLE_CAP
    assert abs(ax["computed_percentage"] - 100.0) < 1e-9


def test_indirect_ownership_direct_only_pair_produces_no_row():
    results = compute_indirect_ownership([("A", "B", 50.0)], cycle_of={})
    assert results == []


def test_indirect_ownership_excludes_only_the_cyclic_pair():
    """A 2-cycle between X and Y must never produce an indirect row between
    X and Y themselves — but must NOT block indirect ownership through Y's
    other, non-cyclic edges. Bug found 2026-08-02 (two independent audits):
    an earlier version excluded an entity ENTIRELY the moment it appeared in
    ANY cycle, silently dropping every unrelated indirect stake that happened
    to route through it (e.g. W's indirect stake in Z below).
    """
    edges = [("X", "Y", 30.0), ("Y", "X", 40.0), ("Y", "Z", 100.0), ("W", "Y", 50.0)]
    components = find_cycle_components([(o, w) for o, w, _ in edges])
    cycle_of = {n: i for i, comp in enumerate(components) for n in comp}
    assert set(cycle_of) == {"X", "Y"}

    results = compute_indirect_ownership(edges, cycle_of)
    assert not any({r["owner_entity_id"], r["owned_entity_id"]} <= {"X", "Y"} for r in results)
    wz = next(r for r in results if r["owner_entity_id"] == "W" and r["owned_entity_id"] == "Z")
    assert wz["computed_percentage"] == 50.0                # W -50%-> Y -100%-> Z, unaffected by X<->Y cycle


def test_indirect_ownership_self_loop_does_not_block_unrelated_edges():
    """Same bug, self-loop flavor (the real-data case: a company holding a
    slice of itself). K's self-holding must not erase P's indirect stake in
    K's separate, non-cyclic subsidiary Z.
    """
    edges = [("K", "K", 22.22), ("P", "K", 11.11), ("K", "Z", 17.6)]
    components = find_cycle_components([(o, w) for o, w, _ in edges])
    cycle_of = {n: i for i, comp in enumerate(components) for n in comp}
    assert set(cycle_of) == {"K"}

    results = compute_indirect_ownership(edges, cycle_of)
    pz = next(r for r in results if r["owner_entity_id"] == "P" and r["owned_entity_id"] == "Z")
    assert abs(pz["computed_percentage"] - 11.11 * 17.6 / 100) < 1e-9


def test_classify_role_gendered_pairs_collapse_to_neutral_label():
    """Owner instruction 2026-08-02: gendered role names must not surface as
    distinct labels — only the neutral form is shown, though both cbTypeIds
    keep their own group.
    """
    assert classify_role(34) == classify_role(22) == ("NEVND", "Nevndarformaður")
    assert classify_role(30) == classify_role(37) == ("NEVND", "Næstformaður")


def test_classify_role_ownership_types_are_eigarar_group():
    assert classify_role(1) == ("EIGARAR", "Eigari")
    assert classify_role(41) == ("EIGARAR", "Veruligur Eigari")


def test_classify_role_unknown_cbtype_is_unmapped():
    assert classify_role(9999) == (None, None)


def test_entity_side_type_person_vs_company():
    assert entity_side_type("Anna Testsen Prøvesen - Persónur, Testvegur 9  Skopun Føroyar") \
        == "Persónur"
    assert entity_side_type("Betri #3690 - P/F, Kongabrúgvin Postrúm 329") == "P/F"


def test_entity_side_type_unparseable_is_none():
    assert entity_side_type("no separators at all") is None


def test_is_person_type_covers_both_faroese_and_foreign_persons():
    """Bug found 2026-08-02: foreign persons use a SECOND token, "Natural
    Person" — missing it silently sorted 56 real people as companies.
    """
    assert is_person_type("Persónur")
    assert is_person_type("Natural Person")
    assert not is_person_type("Sp/F")
    assert not is_person_type(None)


def test_entity_kind_covers_person_company_organization_public_authority():
    assert entity_kind("Persónur") == "PERSON"
    assert entity_kind("Natural Person") == "PERSON"
    assert entity_kind("Sp/F") == "COMPANY"
    assert entity_kind("GmbH") == "COMPANY"          # foreign company forms map too
    assert entity_kind("Fakfelag") == "ORGANIZATION"
    assert entity_kind("Kommuna") == "PUBLIC_AUTHORITY"


def test_entity_kind_unknown_token_is_unknown_entity_never_guessed():
    assert entity_kind("some future token nobody has seen") == "UNKNOWN_ENTITY"


def test_relation_basis_shared_owner():
    assert relation_basis("DIRECT_OWNERSHIP", "EIGARAR", "DIRECT_OWNERSHIP", "EIGARAR") == "SHARED_OWNER"


def test_relation_basis_shared_beneficial_owner():
    assert relation_basis("REGISTERED_CONTROL_ACTOR", "EIGARAR",
                          "REGISTERED_CONTROL_ACTOR", "EIGARAR") == "SHARED_BENEFICIAL_OWNER"


def test_relation_basis_shared_board_and_management():
    assert relation_basis("OTHER_SOURCE_RELATION", "NEVND",
                          "OTHER_SOURCE_RELATION", "NEVND") == "SHARED_BOARD_PERSON"
    assert relation_basis("OTHER_SOURCE_RELATION", "STJORN",
                          "OTHER_SOURCE_RELATION", "STJORN") == "SHARED_MANAGEMENT_PERSON"


def test_relation_basis_cross_role_when_groups_differ():
    assert relation_basis("OTHER_SOURCE_RELATION", "NEVND",
                          "OTHER_SOURCE_RELATION", "STJORN") == "CROSS_ROLE_PERSON"
    assert relation_basis("DIRECT_OWNERSHIP", "EIGARAR",
                          "REGISTERED_CONTROL_ACTOR", "EIGARAR") == "CROSS_ROLE_PERSON"


def test_shapes_reject_drift():
    assert not SHAPES["get-table"]([{"fromId": 1}])              # missing keys
    assert not SHAPES["get-table"]({"rows": []})                 # not an array
    assert not SHAPES["search-cbi"]([{"id": 1, "name": "x"}])    # renamed keys
    assert not SHAPES["get-graph"]({"data": {"nodes": []}})      # data no longer a string


def test_pdf_fetch_report_contains_counts_not_urls_or_hashes(capsys):
    from heimdal_collect import print_pdf_location_report
    print_pdf_location_report({"APPLIED": 1, "NO_LOCATION": 2, "FAILED": 3, "UNMATCHED": 4})
    output = capsys.readouterr().out
    assert "APPLIED: 1" in output
    assert "https://" not in output and "sha256" not in output.casefold()


if __name__ == "__main__":
    import inspect
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        if any(p.default is p.empty for p in inspect.signature(t).parameters.values()):
            continue                       # pytest-fixture tests run under pytest only
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} checks passed")
