"""Snapshots: entity change feed and timeline."""
from heimdal.modules.snapshots import repository


def entity_change_feed(conn, canonical_entity_id=None, category=None, from_date=None, to_date=None, limit=50, offset=0):
    """Retrieve historical change feed events across snapshots."""
    clauses = []
    params = []
    if canonical_entity_id:
        clauses.append("canonical_entity_id = %s")
        params.append(canonical_entity_id)
    if category:
        clauses.append("change_category = %s")
        params.append(category)
    if from_date:
        clauses.append("event_timestamp >= %s")
        params.append(from_date)
    if to_date:
        clauses.append("event_timestamp <= %s")
        params.append(to_date + " 23:59:59")
        
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.extend([limit, offset])
    
    return repository.select_change_feed(conn, where_sql, params)


def entity_timeline(conn, canonical_entity_id):
    """Retrieve timeline events for a single entity."""
    return entity_change_feed(conn, canonical_entity_id=canonical_entity_id, limit=100)
