"""Batch screening: factual PEP and structure-observation lookups."""
import re

from heimdal.modules.entities.service import normalize_faroese_query
from heimdal.modules.screening import repository

# A bare number is a registration number ("267" = S267), never an internal id (F-5).
_REGNR = re.compile(r"[Ss#]?\s*([0-9]+)")
_SOURCE_ID = re.compile(r"kilde(?:-id)?:\s*([0-9]+)", re.IGNORECASE)


def _resolve(conn, queries):
    """query -> list of matching entities: exact regnr, explicit source id
    ("kilde:<id>") or whole name. One query per kind, whatever the batch size."""
    keys, regnrs, source_ids, names = {}, [], [], []
    for q in queries:
        if m := _SOURCE_ID.fullmatch(q):
            keys[q] = ("source", int(m[1]))
            source_ids.append(int(m[1]))
        elif m := _REGNR.fullmatch(q):
            keys[q] = ("regnr", f"S{int(m[1])}")
            regnrs.append(keys[q][1])
        else:
            keys[q] = ("name", normalize_faroese_query(q))
            names.append(keys[q][1])
    found = {}
    for kind, values, select in (("regnr", regnrs, repository.entities_by_regnr),
                                 ("source", source_ids, repository.entities_by_source_id),
                                 ("name", names, repository.entities_by_name)):
        for row in select(conn, values) if values else ():
            found.setdefault((kind, row["key"]), []).append(row)
    return {q: found.get(key, []) for q, key in keys.items()}


def batch_screen_entities(conn, identifiers):
    """Batch-lookup a list of identifiers: registration numbers, "kilde:<id>"
    source ids, or whole names (case- and accent-insensitive).

    Results are factual: PEP presence and structure-observation counts from the
    local snapshot. No risk score, severity, or verdict is calculated. A query
    with more than one match is reported as ambiguous, never as one of them.
    has_pep is None on every row while no PEP data is loaded (D14).
    """
    queries = [q for q in (str(i).strip() for i in identifiers) if q]
    matches = _resolve(conn, queries)
    hits = {q: m[0] for q, m in matches.items() if len(m) == 1}
    persons = [e["canonical_entity_id"] for e in hits.values() if e["entity_kind"] == "PERSON"]
    companies = [e["canonical_entity_id"] for e in hits.values() if e["entity_kind"] == "COMPANY"]
    pep = repository.first_pep_titles(conn, persons)  # None: no PEP data loaded
    observations = repository.observation_counts(conn, companies) if companies else {}

    results = []
    for raw in queries:
        ent = hits.get(raw)
        if ent:
            cid = ent["canonical_entity_id"]
            results.append({
                "query": raw,
                "id": cid,
                "name": ent["display_name"],
                "kind": ent["entity_kind"],
                "regnr": ent["registration_number"],
                "has_pep": None if pep is None else cid in pep,
                "pep_title": (pep or {}).get(cid),
                "observation_count": observations.get(cid, 0),
                "match_count": 1,
            })
        else:
            n = len(matches[raw])
            results.append({
                "query": raw,
                "id": None,
                "name": f"Flertydigt: {n} match ('{raw}')" if n else f"Ikke fundet ('{raw}')",
                "kind": "UNKNOWN",
                "regnr": None,
                "has_pep": None if pep is None else False,
                "pep_title": None,
                "observation_count": 0,
                "match_count": n,
            })
    return results
