# heimdal/modules/geo/service.py
"""Geo-intelligence: factual Faroese company city aggregates."""
from heimdal.modules.geo import repository


def geo_map_data(conn):
    """Return city aggregates and location coverage for the map snapshot."""
    return {
        "cities": repository.select_city_clusters(conn),
        "coverage": repository.select_location_coverage(conn),
    }


def geo_company_list(conn, *, city=None, region=None):
    """Return validated company rows for exactly one city or region selector."""
    selectors = {k: v for k, v in (("city", city), ("region", region)) if v}
    if len(selectors) != 1:
        raise ValueError("exactly one non-empty city or region selector is required")
    if not repository.postcode_selector_exists(conn, **selectors):
        raise ValueError(f"unknown {next(iter(selectors))}: {next(iter(selectors.values()))!r}")
    return repository.select_geo_companies(conn, **selectors)
