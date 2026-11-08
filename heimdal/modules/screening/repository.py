"""SQL queries for batch screening: one query per identifier kind (F-5), never one per item."""

_COLS = "canonical_entity_id, display_name, entity_kind, registration_number"

# Python twin: entities.service.normalize_faroese_query (lower, á->a ... æ->ae, ø->o).
_NORMALIZED_NAME = ("replace(translate(lower(display_name), 'áðíóúýøö', 'adiouyoo'),"
                    " 'æ', 'ae')")


def entities_by_regnr(conn, regnrs):
    """Exact registration numbers ('S267')."""
    return conn.execute(
        f"SELECT {_COLS}, registration_number AS key FROM v_entity"
        " WHERE registration_number = ANY(%s) ORDER BY canonical_entity_id",
        (regnrs,)).fetchall()


def entities_by_source_id(conn, source_ids):
    """Exact source (Kilde-) ids."""
    return conn.execute(
        f"SELECT {_COLS}, source_entity_id AS key FROM v_entity"
        " WHERE source_entity_id = ANY(%s) ORDER BY canonical_entity_id",
        (source_ids,)).fetchall()


def entities_by_name(conn, normalized_names):
    """Whole names, case- and Faroese-accent-insensitive; homonyms all come back."""
    return conn.execute(
        f"SELECT {_COLS}, {_NORMALIZED_NAME} AS key FROM v_entity"
        f" WHERE {_NORMALIZED_NAME} = ANY(%s) ORDER BY canonical_entity_id",
        (normalized_names,)).fetchall()


def first_pep_titles(conn, person_ids):
    """person id -> its first PEP office title, or None while no PEP record is
    linked to a person (D14: PEP stays empty until a sourced, dated import).
    v_pep_actors joins by the identity link only, never by name (B-6/C-5)."""
    loaded = conn.execute("SELECT EXISTS (SELECT FROM v_pep_actors) AS loaded").fetchone()
    if not (loaded and loaded["loaded"]):
        return None
    rows = conn.execute(
        "SELECT DISTINCT ON (canonical_person_id) canonical_person_id, office_title"
        " FROM v_pep_actors WHERE canonical_person_id = ANY(%s)"
        " ORDER BY canonical_person_id, pep_id", (person_ids,)).fetchall() if person_ids else []
    return {r["canonical_person_id"]: r["office_title"] for r in rows}


def observation_counts(conn, company_ids):
    """company id -> number of structure observations."""
    rows = conn.execute(
        "SELECT company_id, count(*) AS n FROM v_structure_observations"
        " WHERE company_id = ANY(%s) GROUP BY company_id", (company_ids,)).fetchall()
    return {r["company_id"]: r["n"] for r in rows}
