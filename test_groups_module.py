"""Verify groups module exists and exports functions, plus regression test
that a PERSON majority owner is NOT followed as a corporate parent."""
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app
from heimdal.config import DB_URI
from heimdal.modules.groups.service import (
    ultimate_parent_of, corporate_group_tree,
    calculate_group_control_metrics, corporate_group_flow,
    all_corporate_groups,
)
from heimdal.modules.groups import repository
from heimdal.modules.groups.routes import groups_bp
from flask import Blueprint


# Entity 7 = BS Tøkni (COMPANY), owned 100% by entity 37 (a PERSON).
# Before fix: ultimate_parent_of(7) returned person 37 as "ultimate_parent".
# After fix:  the chain stops at company 7 itself — no corporate parent found.
PERSON_OWNED_COMPANY = 7
PERSON_OWNER_ID = 37


def _conn():
    return psycopg.connect(DB_URI, row_factory=dict_row)


def test_groups_functions_exist():
    assert callable(ultimate_parent_of)
    assert callable(corporate_group_tree)
    assert callable(calculate_group_control_metrics)
    assert callable(corporate_group_flow)
    assert callable(all_corporate_groups)


def test_groups_blueprint_exists():
    assert isinstance(groups_bp, Blueprint)
    assert groups_bp.name == "groups"


def test_select_majority_parent_skips_person_owner():
    """select_majority_parent must return None when the only >50% owner is a PERSON.
    A person is not a corporate parent and must not be followed up the chain."""
    with _conn() as c:
        parent = repository.select_majority_parent(c, PERSON_OWNED_COMPANY)
        assert parent is None, (
            f"Expected None (only owner is PERSON {PERSON_OWNER_ID}), "
            f"got owner_id={parent['owner_id'] if parent else 'n/a'} "
            f"owner_kind={parent['owner_kind'] if parent else 'n/a'}"
        )


def test_ultimate_parent_of_person_owned_company_is_self():
    """When the only majority owner is a PERSON, the company is its own
    ultimate parent — the chain stops at the company, not at the person."""
    with _conn() as c:
        info = ultimate_parent_of(c, PERSON_OWNED_COMPANY)
        assert info["is_ultimate_parent"] is True
        assert info["ultimate_parent"]["canonical_entity_id"] == PERSON_OWNED_COMPANY
        assert info["ultimate_parent"]["entity_kind"] == "COMPANY"
        # chain_depth 0 because no corporate parent was followed
        assert info["chain_depth"] == 0


def test_company_page_for_person_owned_company_links_to_self_not_404():
    """The 'Se Koncerndiagram' link on /companies/7 must point at a real
    company page, not at /companies/37 (a PERSON id that 404s)."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        res = client.get(f"/companies/{PERSON_OWNED_COMPANY}")
        assert res.status_code == 200
        body = res.text
        # The broken link /companies/37 must not appear in the group box
        assert "/companies/37" not in body, (
            "Group box still links to /companies/37 (PERSON page that 404s)."
        )
        # If a group link is shown, it must target a real COMPANY/ORG entity
        if "Se Koncerndiagram" in body:
            # The link should be /groups/7 (self) since 7 is now its own parent
            assert "/groups/7" in body


def test_all_corporate_groups_excludes_person_parents():
    """all_corporate_groups must not list PERSON entities as parents."""
    with _conn() as c:
        groups = all_corporate_groups(c, limit=100)
        for g in groups:
            assert g["parent_kind"] != "PERSON", (
                f"Group list still includes PERSON parent: "
                f"id={g['parent_id']} name={g['parent_name']}"
            )
