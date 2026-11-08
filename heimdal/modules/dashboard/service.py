"""Dashboard: chart distributions, macro stats, top lists."""
from heimdal.modules.dashboard import repository
from heimdal.modules.entities.service import person_list
from heimdal.modules.groups.service import all_corporate_groups
from heimdal.modules.snapshots.service import entity_change_feed


def dashboard_charts(conn):
    """Fordelinger til dashboardets farvegrafer — rene tællinger fra
    kildelagene. Barerne er illustration; tallene står ved siden af i selve
    grafen (samme princip som §21: grafen er supplement, ikke eneste kilde)."""
    kind_labels = {
        "PERSON": ("Personer", "/persons"),
        "COMPANY": ("Virksomheder", "/companies?kind=COMPANY"),
        "ORGANIZATION": ("Organisationer", "/companies?kind=ORGANIZATION"),
        "PUBLIC_AUTHORITY": ("Offentlige myndigheder", "/companies?kind=PUBLIC_AUTHORITY"),
        "UNKNOWN_ENTITY": ("Ukendt type", "/companies?kind=UNKNOWN_ENTITY")
    }
    kinds = [{
        "label": kind_labels.get(r["entity_kind"], (r["entity_kind"], "/companies"))[0],
        "url": kind_labels.get(r["entity_kind"], (r["entity_kind"], "/companies"))[1],
        "n": r["n"]
    } for r in repository.select_kind_distribution(conn)]

    relations = [{
        "label": {"DIRECT_OWNERSHIP": "Direkte ejerskab",
                  "REGISTERED_CONTROL_ACTOR": "Kontrolregistrering",
                  "OTHER_SOURCE_RELATION": "Roller"}.get(r["canonical_relation_type"],
                                                        r["canonical_relation_type"]),
        "url": "/companies",
        "n": r["n"]
    } for r in repository.select_relation_distribution(conn)]

    band_urls = {
        "100 %": "/companies?ownership_band=100",
        "50–99 %": "/companies?ownership_band=50-99",
        "25–49 %": "/companies?ownership_band=25-49",
        "10–24 %": "/companies?ownership_band=10-24",
        "1–9 %": "/companies?ownership_band=1-9"
    }
    bands = [{
        "label": r["band"],
        "url": band_urls.get(r["band"], "/companies"),
        "n": r["n"]
    } for r in repository.select_ownership_band_distribution(conn)]
    return {"kinds": kinds, "relations": relations, "bands": bands}


def dashboard_macro_stats(conn):
    """Retrieve top-level macro registry KPIs in an efficient single query."""
    companies_count = repository.select_companies_count(conn)
    persons_count = repository.select_persons_count(conn)
    relations_count = repository.select_relations_count(conn)
    groups_count = repository.select_groups_count(conn)
    observed_count = repository.select_observed_companies_count(conn)

    return {
        "total_companies": companies_count,
        "total_persons": persons_count,
        "total_relations": relations_count,
        "total_groups": groups_count,
        "total_observed_companies": observed_count
    }


def top_lists(conn):
    """Top 5-lister til dashboardet: genbruger person_list's testede CTE'er
    i stedet for at duplikere tællelogikken."""
    def rows(sort):
        return [{"id": r["canonical_entity_id"], "name": r["display_name"],
                 "n": r[{"-direct": "direct_count", "-indirect": "indirect_count",
                         "-nevn": "nevn_count", "-stjorn": "stjorn_count"}[sort]]}
                for r in person_list(conn, "", 1, sort=sort, per_page=5)["rows"]]
    pairs = repository.select_top_relation_pairs(conn)
    top_groups = all_corporate_groups(conn, limit=5)
    recent_events = entity_change_feed(conn, limit=5)
    return {
        "direct": rows("-direct"),
        "indirect": rows("-indirect"),
        "nevn": rows("-nevn"),
        "stjorn": rows("-stjorn"),
        "relations": [{"a_id": r["a_id"], "a_name": r["a_name"],
                       "b_id": r["b_id"], "b_name": r["b_name"], "n": r["n"]}
                      for r in pairs],
        "groups": top_groups,
        "recent_events": recent_events
    }
