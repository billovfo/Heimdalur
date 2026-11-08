"""Compliance module (routes/service/repository, F-11) — offline, no Flask app or DB."""
from flask import Blueprint

from heimdal.modules.compliance import repository, service
from heimdal.modules.compliance.routes import compliance_bp


def test_repository_functions_exist():
    for name in ("structure_observation_summary", "structure_observation_matrix",
                 "registration_patterns", "entity_structure_observations"):
        assert callable(getattr(repository, name)), name


def test_entity_structure_status_is_observation_only(monkeypatch):
    obs = [{"observation_type": "OWNERSHIP_CYCLE",
            "observation_label": "Lukket ejerrelation registreret (gruppe #4)"}]
    monkeypatch.setattr(repository, "entity_structure_observations", lambda conn, cid: obs)
    assert service.entity_structure_status(None, 5708) == {
        "has_observations": True,
        "observation_count": 1,
        "observations": obs,
    }


def test_no_legacy_score_exports_remain():
    for mod in (repository, service):
        for legacy in ("calculate_aml_risk_score", "entity_aml_status",
                       "compliance_summary", "compliance_matrix", "early_warning_signals",
                       "assert_score_in_0_100", "Severity", "RiskBand"):
            assert not hasattr(mod, legacy), (mod.__name__, legacy)


def test_compliance_blueprint_exists():
    assert isinstance(compliance_bp, Blueprint)
    assert compliance_bp.name == "compliance"
