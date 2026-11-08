"""SQL queries for dashboard — chart distributions, macro stats, top-list queries."""


def select_kind_distribution(conn):
    return conn.execute(
        "SELECT entity_kind, count(*) AS n FROM canonical_entity"
        " GROUP BY entity_kind ORDER BY n DESC").fetchall()


def select_relation_distribution(conn):
    return conn.execute(
        "SELECT canonical_relation_type, count(*) AS n FROM registry_ownership.relation"
        " GROUP BY 1 ORDER BY n DESC").fetchall()


def select_ownership_band_distribution(conn):
    return conn.execute(
        "SELECT CASE WHEN parsed_percentage >= 100 THEN '100 %'"
        " WHEN parsed_percentage >= 50 THEN '50–99 %'"
        " WHEN parsed_percentage >= 25 THEN '25–49 %'"
        " WHEN parsed_percentage >= 10 THEN '10–24 %'"
        " ELSE '1–9 %' END AS band, count(*) AS n"
        " FROM v_direct_ownership WHERE parsed_percentage IS NOT NULL"
        " GROUP BY 1 ORDER BY min(parsed_percentage)").fetchall()


def select_companies_count(conn):
    return conn.execute(
        "SELECT count(*) AS n FROM canonical_entity WHERE entity_kind = 'COMPANY'"
    ).fetchone()["n"]


def select_persons_count(conn):
    return conn.execute(
        "SELECT count(*) AS n FROM canonical_entity WHERE entity_kind = 'PERSON'"
    ).fetchone()["n"]


def select_relations_count(conn):
    return conn.execute("SELECT count(*) AS n FROM v_direct_ownership").fetchone()["n"]


def select_groups_count(conn):
    return conn.execute(
        "SELECT count(DISTINCT owner_id) AS n FROM v_direct_ownership WHERE parsed_percentage > 50.0"
    ).fetchone()["n"]


def select_observed_companies_count(conn):
    return conn.execute(
        "SELECT count(DISTINCT company_id) AS n FROM v_structure_observations"
    ).fetchone()["n"]


def select_top_relation_pairs(conn):
    return conn.execute(
        "SELECT ca.canonical_entity_id AS a_id, ca.display_name AS a_name,"
        " cb.canonical_entity_id AS b_id, cb.display_name AS b_name, s.n"
        " FROM (SELECT person_a_entity_id AS a, person_b_entity_id AS b,"
        "       count(DISTINCT company_entity_id) AS n FROM registry_ownership.person_relation_context"
        "       GROUP BY 1, 2 ORDER BY n DESC LIMIT 5) s"
        " JOIN canonical_entity ca ON ca.source_entity_id = s.a"
        " JOIN canonical_entity cb ON cb.source_entity_id = s.b"
    ).fetchall()
