# heimdal/modules/geo/repository.py
"""SQL queries for geo module. No rows means no markers (G-01): never a demo fallback."""


def select_city_clusters(conn):
    return conn.execute(
        """
        SELECT gp.city, gp.region, gp.latitude::float8 AS latitude,
               gp.longitude::float8 AS longitude, count(*) AS company_count
        FROM canonical_entity ce
        JOIN company_location cl
          ON cl.source_system = ce.source_system AND cl.source_entity_id = ce.source_entity_id
        JOIN geo_postcode gp ON gp.postal_code = cl.postal_code
        WHERE ce.entity_kind = 'COMPANY' AND cl.location_status = 'FAROESE_CITY'
        GROUP BY gp.city, gp.region, gp.latitude, gp.longitude
        ORDER BY gp.region, gp.city
        """
    ).fetchall()


def select_geo_companies(conn, *, city=None, region=None):
    # ponytail: column is an internal constant chosen by branch, never user input
    column, value = ("gp.city", city) if city is not None else ("gp.region", region)
    return conn.execute(
        f"""
        SELECT ce.canonical_entity_id, ce.display_name
        FROM canonical_entity ce
        JOIN company_location cl
          ON cl.source_system = ce.source_system AND cl.source_entity_id = ce.source_entity_id
        JOIN geo_postcode gp ON gp.postal_code = cl.postal_code
        WHERE ce.entity_kind = 'COMPANY'
          AND cl.location_status = 'FAROESE_CITY'
          AND {column} = %s
        ORDER BY ce.display_name, ce.canonical_entity_id
        """,
        (value,),
    ).fetchall()


def postcode_selector_exists(conn, *, city=None, region=None):
    column, value = ("city", city) if city is not None else ("region", region)
    return conn.execute(
        f"SELECT 1 FROM geo_postcode WHERE {column} = %s LIMIT 1", (value,)
    ).fetchone() is not None


def select_location_coverage(conn):
    return conn.execute(
        """
        SELECT count(*) FILTER (WHERE coalesce(cl.location_status, 'UNKNOWN') = 'FAROESE_CITY') AS faroe_islands,
               count(*) FILTER (WHERE coalesce(cl.location_status, 'UNKNOWN') = 'FOREIGN') AS foreign,
               count(*) FILTER (WHERE coalesce(cl.location_status, 'UNKNOWN') = 'UNKNOWN') AS unknown
        FROM canonical_entity ce
        LEFT JOIN company_location cl
          ON cl.source_system = ce.source_system AND cl.source_entity_id = ce.source_entity_id
        WHERE ce.entity_kind = 'COMPANY'
        """
    ).fetchone()
