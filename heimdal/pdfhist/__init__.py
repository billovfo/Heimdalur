"""HEIMDAL PDF-HISTORIK v3.1.2 - lean offline extraction/parse/observational pipeline.

Implements docs/HEIMDAL-PDF-HISTORIK-MASTERPLAN-v3.1.2.md. No live HTTP: the
ingestion service is an offline test service; bytes are supplied by the caller
(fixture files). No parser registry, no promotion gates, no event sourcing - the
whole module set is a few pure parser functions plus small repository helpers.
"""