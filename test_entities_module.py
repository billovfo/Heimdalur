"""Verify entities module skeleton and constants."""
from heimdal.modules.entities.service import (
    COMPANY_KINDS, PERSON_SORT_KEYS,
    PARTIAL_POPULATION, ALL_TERMINAL, _UNRESOLVED,
    RELATION_BASIS_LABELS, PERSON_RELATION_BASIS_LABELS,
)


def test_company_kinds():
    assert COMPANY_KINDS == ("COMPANY", "ORGANIZATION", "PUBLIC_AUTHORITY", "UNKNOWN_ENTITY")


def test_person_sort_keys():
    assert "name" in PERSON_SORT_KEYS
    assert "direct" in PERSON_SORT_KEYS
    assert "nevn" in PERSON_SORT_KEYS
    assert "stjorn" in PERSON_SORT_KEYS


def test_population_constants():
    assert PARTIAL_POPULATION == "PARTIAL_POPULATION"
    assert ALL_TERMINAL == "ALL_TERMINAL"
    assert _UNRESOLVED == ("DISCOVERED", "QUEUED", "RETRYABLE_ERROR")


def test_relation_basis_labels():
    assert "DIRECT_OWNERSHIP" in RELATION_BASIS_LABELS
    assert "SHARED_OWNER" in PERSON_RELATION_BASIS_LABELS


from heimdal.modules.entities.service import (
    owners_of, holdings_of, ownership_sum, SUM_UNDER,
)
from decimal import Decimal


def test_ownership_functions_exist():
    assert callable(owners_of)
    assert callable(holdings_of)
    assert callable(ownership_sum)


def test_ownership_sum_empty():
    s = ownership_sum([])
    assert s["total"] == Decimal("0")
    assert s["state"] == SUM_UNDER
    assert s["largest"] is None


def test_ownership_sum_single():
    rows = [{"parsed_percentage": Decimal("50.0"), "percentage_parse_status": "PARSED"}]
    s = ownership_sum(rows)
    assert s["total"] == Decimal("50.0")
    assert s["largest"] == Decimal("50.0")


from heimdal.modules.entities.service import (
    control_state, control_registration, control_registrations_of_actor,
    roles_at_company, voting_rights_at_company,
    roles_held_by_person, voting_rights_held_by_person,
)


def test_control_functions_exist():
    assert callable(control_state)
    assert callable(control_registration)
    assert callable(control_registrations_of_actor)
    assert callable(roles_at_company)
    assert callable(voting_rights_at_company)
    assert callable(roles_held_by_person)
    assert callable(voting_rights_held_by_person)


from heimdal.modules.entities.service import (
    cycle_info, indirect_owners_of, indirect_holdings_of,
    hydrate_paths, top_persons,
)


def test_indirect_functions_exist():
    assert callable(cycle_info)
    assert callable(indirect_owners_of)
    assert callable(indirect_holdings_of)
    assert callable(hydrate_paths)
    assert callable(top_persons)


def test_top_persons_empty():
    assert top_persons([]) == []


def test_top_persons_cap():
    persons = [{"name": f"p{i}", "paths": [i]} for i in range(150)]
    result = top_persons(persons, cap=10)
    assert len(result) <= 10


from heimdal.modules.entities.service import (
    ownership_tree_layout, person_owners_tree,
)


def test_tree_functions_exist():
    assert callable(ownership_tree_layout)
    assert callable(person_owners_tree)


from heimdal.modules.entities.service import (
    population_state, overview, normalize_faroese_query,
    entity_list, person_list, ego_graph, ego_layout,
)


def test_listing_functions_exist():
    assert callable(population_state)
    assert callable(overview)
    assert callable(normalize_faroese_query)
    assert callable(entity_list)
    assert callable(person_list)
    assert callable(ego_graph)
    assert callable(ego_layout)


def test_population_state_unresolved():
    assert population_state(("DISCOVERED", "QUEUED")) == "PARTIAL_POPULATION"


def test_population_state_all_terminal():
    assert population_state(()) == "ALL_TERMINAL"


def test_normalize_faroese_query_basic():
    result = normalize_faroese_query("Tórshavn")
    assert isinstance(result, str)
    assert len(result) > 0


from heimdal.modules.entities.service import (
    company_relations_of, person_relations_of,
    person_pep_info,
)


def test_relations_functions_exist():
    assert callable(company_relations_of)
    assert callable(person_relations_of)
    assert callable(person_pep_info)

from heimdal.modules.entities.routes import entities_bp


def test_blueprint_exists():
    assert hasattr(entities_bp, 'name')
    assert entities_bp.name == 'entities'


def test_blueprint_has_routes():
    from flask import Blueprint
    assert isinstance(entities_bp, Blueprint)
