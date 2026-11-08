# heimdal/modules/compliance/service.py
"""Per-entity structure status: the neutral observations of one company."""
from heimdal.modules.compliance import repository


def entity_structure_status(conn, canonical_entity_id):
    observations = repository.entity_structure_observations(conn, canonical_entity_id)
    return {
        "has_observations": bool(observations),
        "observation_count": len(observations),
        "observations": observations,
    }
