-- HISTORICAL (audit B-17, 2026-11-06): the canonical DB's schema is the ledger in
-- sql/migrations/ (python3 -m heimdal.canonical_migrations). Change it there, not here.
-- Heimdal Phase 1 — raw acquisition storage (PostgreSQL 18).
--
-- COLLECTION_RUN, COMPANY_INVENTORY, REQUEST_ATTEMPT, RESPONSE_CONTENT,
-- ENUMERATION_PREFIX cover raw acquisition (Fase A/B).
--
-- CANONICAL_RELATION and ENTITY_STATUS (owner-authorized 2026-08-02, scope:
-- canonical relation storage only — see HEIMDAL-PRODUCT-SOURCE-OF-TRUTH.md
-- §14/§14a/§14b), INDIRECT_OWNERSHIP (§15), the INFERRED role-group tagging on
-- CANONICAL_RELATION, PERSON_RELATION_CONTEXT/COMPANY_RELATION_CONTEXT
-- (§18/§19), and CANONICAL_ENTITY (§3.1/§3.2, Fase 0 of the portal/API tier,
-- owner-authorized 2026-08-02) are all built from local RESPONSE_CONTENT
-- snapshots, never from a new upstream request. No portals, no live API yet
-- — those remain the next tier.

-- Post-cutover guard (audit 2026-11 B-3/A-6). This file is pre-cutover DDL.
-- After tools/phase1_cutover.py the ownership tables live in registry_ownership
-- and the public ones are *_legacy, so re-applying it would recreate them empty
-- and point the read views at them. The guard runs first, inside the file's
-- one transaction, so even a plain `psql -f` applies nothing.
BEGIN;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'registry_ownership')
     OR EXISTS (SELECT 1 FROM pg_tables
                WHERE schemaname = 'public' AND tablename LIKE '%\_legacy') THEN
    RAISE EXCEPTION 'refusing pre-cutover DDL on post-cutover database "%": schema registry_ownership or a public.*_legacy table exists (audit B-3)', current_database();
  END IF;
END
$$;

CREATE TABLE IF NOT EXISTS collection_run (
    collection_run_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_type           text NOT NULL
                         CHECK (run_type IN ('INVENTORY', 'ACQUISITION', 'TYPE_VALIDATION',
                                              'RELATION_PROCESSING', 'INDIRECT_OWNERSHIP',
                                              'ROLE_NORMALIZATION', 'RELATION_CONTEXTS',
                                              'CANONICAL_ENTITY_ASSIGNMENT',
                                              'PDF_LOCATION_ENRICHMENT')),
    run_status         text NOT NULL DEFAULT 'RUNNING'
                         CHECK (run_status IN ('RUNNING', 'COMPLETED', 'STOPPED')),
    stop_reason        text,
    started_at         timestamptz NOT NULL DEFAULT now(),
    ended_at           timestamptz,
    collector_version  text NOT NULL,
    config             jsonb NOT NULL,

    -- Permission record (SoT §9.1). Unknown administrative detail is NOT_RECORDED
    -- and must not block the run.
    permission_status                                text NOT NULL,
    permission_scope                                 text NOT NULL,
    permission_granted_by                            text NOT NULL DEFAULT 'NOT_RECORDED',
    permission_granted_at                            text NOT NULL DEFAULT 'NOT_RECORDED',
    permission_channel                               text NOT NULL DEFAULT 'NOT_RECORDED',
    owner_attestation_recorded_at                    date NOT NULL,
    source_owner_contact_required_before_collection  boolean NOT NULL,
    source_support_availability                      text NOT NULL
);

-- Idempotent migration: allow the resumable PDF location backfill run type on a
-- database whose collection_run already exists with the older CHECK constraint.
ALTER TABLE collection_run DROP CONSTRAINT IF EXISTS collection_run_run_type_check;
ALTER TABLE collection_run ADD CONSTRAINT collection_run_run_type_check CHECK (run_type IN (
    'INVENTORY', 'ACQUISITION', 'TYPE_VALIDATION', 'RELATION_PROCESSING',
    'INDIRECT_OWNERSHIP', 'ROLE_NORMALIZATION', 'RELATION_CONTEXTS',
    'CANONICAL_ENTITY_ASSIGNMENT', 'PDF_LOCATION_ENRICHMENT'
));

-- Response content is deduplicated by SHA-256: identical bytes are stored once.
-- byte_length is generated so it can never drift from the stored bytes.
CREATE TABLE IF NOT EXISTS response_content (
    response_sha256  char(64) PRIMARY KEY,
    response_bytes   bytea NOT NULL,
    content_type     text,
    byte_length      integer GENERATED ALWAYS AS (octet_length(response_bytes)) STORED,
    first_stored_at  timestamptz NOT NULL DEFAULT now()
);

-- One row per HTTP attempt, never deduplicated. Many attempts may reference the
-- same response_content row; content deduplication must never remove request history.
-- retry_number is capped at 1 so "at most 2 attempts per request" is a schema
-- invariant, not just collector behaviour.
CREATE TABLE IF NOT EXISTS request_attempt (
    attempt_id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    collection_run_id           bigint NOT NULL REFERENCES collection_run,
    endpoint                    text NOT NULL,
    parameters                  jsonb NOT NULL,
    requested_source_entity_id  bigint,          -- NULL for search-cbi; prefix lives in parameters
    requested_at                timestamptz NOT NULL,
    response_received_at        timestamptz,
    http_status                 integer,
    error_category              text NOT NULL,
    retry_number                smallint NOT NULL DEFAULT 0 CHECK (retry_number BETWEEN 0 AND 1),
    collector_version           text NOT NULL,
    response_sha256             char(64) REFERENCES response_content
);

CREATE INDEX IF NOT EXISTS request_attempt_entity_idx ON request_attempt (requested_source_entity_id);
CREATE INDEX IF NOT EXISTS request_attempt_run_idx    ON request_attempt (collection_run_id);
CREATE INDEX IF NOT EXISTS request_attempt_endpoint_idx ON request_attempt (endpoint);

-- Idempotent migration (audit A-5): a non-HTTP error (timeout, network, TLS,
-- short read) has no response body. Its text lives here and response_sha256
-- stays NULL, so an exception message is never stored as a raw body.
ALTER TABLE request_attempt ADD COLUMN IF NOT EXISTS error_text text;

-- The raw tier is append-only (audit A-14): UPDATE, DELETE and TRUNCATE on
-- request_attempt / response_content are refused. Idempotent.
CREATE OR REPLACE FUNCTION raw_tier_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% on %.% refused: the raw tier is append-only (audit A-14)',
    TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME;
END
$$;
CREATE OR REPLACE TRIGGER request_attempt_append_only
  BEFORE UPDATE OR DELETE OR TRUNCATE ON request_attempt
  FOR EACH STATEMENT EXECUTE FUNCTION raw_tier_append_only();
CREATE OR REPLACE TRIGGER response_content_append_only
  BEFORE UPDATE OR DELETE OR TRUNCATE ON response_content
  FOR EACH STATEMENT EXECUTE FUNCTION raw_tier_append_only();

CREATE TABLE IF NOT EXISTS company_inventory (
    source_system           text   NOT NULL,
    source_entity_id        bigint NOT NULL,
    registration_number     text,            -- NULL: persons and foreign/non-registered entities
    raw_display_name        text   NOT NULL,
    normalized_search_name  text   NOT NULL,
    raw_entity_type_text    text,            -- NULL: display text did not parse
    parsed_entity_type      text,            -- NULL: UNKNOWN_ENTITY
    typing_status           text   NOT NULL
                              CHECK (typing_status IN ('PARSED', 'UNKNOWN_ENTITY', 'CONFLICT')),
    discovery_method        text   NOT NULL,
    first_discovered_at     timestamptz NOT NULL DEFAULT now(),
    last_confirmed_at       timestamptz NOT NULL DEFAULT now(),
    inventory_run_id        bigint NOT NULL REFERENCES collection_run,
    raw_response_reference  char(64) NOT NULL REFERENCES response_content,
    inventory_status        text   NOT NULL DEFAULT 'DISCOVERED'
                              CHECK (inventory_status IN ('DISCOVERED', 'QUEUED', 'FETCHED',
                                                          'NOT_FOUND', 'RETRYABLE_ERROR',
                                                          'TERMINAL_ERROR')),
    PRIMARY KEY (source_system, source_entity_id)
);

CREATE INDEX IF NOT EXISTS company_inventory_status_idx ON company_inventory (inventory_status);
CREATE INDEX IF NOT EXISTS company_inventory_regnr_idx  ON company_inventory (registration_number);
CREATE INDEX IF NOT EXISTS company_inventory_name_idx   ON company_inventory (normalized_search_name);

-- Geographic reference (task 2026-08-20 company-city-map). geo_postcode is the
-- reviewed Faroese postcode reference, seeded from Posta
-- (docs/sources/2026-08-20-posta-postcodes.md); company_location maps inventory
-- rows to a postcode/country with an enforced placement status. The FK keeps
-- location rows aligned with inventory rows; the status checks reject partial or
-- invented Faroe placements.
CREATE TABLE IF NOT EXISTS geo_postcode (
    postal_code text PRIMARY KEY CHECK (postal_code ~ '^[0-9]{3}$'),
    city text NOT NULL CHECK (city <> ''),
    region text NOT NULL CHECK (region IN (
        'SUDUROY', 'SANDOY', 'SUDURSTREYMOY', 'NORDURSTREYMOY',
        'VAGAR', 'EYSTUROY', 'NORDOYGGJAR')),
    latitude numeric(8, 5) NOT NULL CHECK (latitude > 61 AND latitude < 63),
    longitude numeric(8, 5) NOT NULL CHECK (longitude > -8 AND longitude < -6)
);

CREATE TABLE IF NOT EXISTS company_location (
    source_system text NOT NULL,
    source_entity_id bigint NOT NULL,
    postal_code text REFERENCES geo_postcode(postal_code),
    country_code text CHECK (country_code ~ '^[A-Z]{2}$'),
    location_status text NOT NULL CHECK (location_status IN ('FAROESE_CITY', 'FOREIGN', 'UNKNOWN')),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_system, source_entity_id),
    FOREIGN KEY (source_system, source_entity_id)
        REFERENCES company_inventory(source_system, source_entity_id) ON DELETE CASCADE,
    CHECK (
        (location_status = 'FAROESE_CITY' AND postal_code IS NOT NULL AND country_code = 'FO')
        OR (location_status = 'FOREIGN' AND postal_code IS NULL AND country_code IS NOT NULL)
        OR (location_status = 'UNKNOWN' AND postal_code IS NULL AND country_code IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS company_location_pdf_fetch (
    source_system text NOT NULL,
    source_entity_id bigint NOT NULL,
    source_document_id bigint NOT NULL,  -- no FK: source.documents is not owned by this DDL (audit B-4/D13)
    source_url text NOT NULL,
    response_sha256 char(64),
    fetched_at timestamptz NOT NULL DEFAULT now(),
    outcome text NOT NULL CHECK (outcome IN ('APPLIED', 'NO_LOCATION', 'FAILED')),
    http_status integer,
    failure_kind text CHECK (failure_kind IN ('HTTP_4XX', 'HTTP_5XX', 'NETWORK', 'INVALID_PDF')),
    attempt_count smallint NOT NULL CHECK (attempt_count BETWEEN 1 AND 2),
    PRIMARY KEY (source_system, source_entity_id),
    FOREIGN KEY (source_system, source_entity_id)
        REFERENCES company_inventory(source_system, source_entity_id) ON DELETE CASCADE,
    CHECK ((outcome = 'FAILED') = (failure_kind IS NOT NULL)),
    CHECK ((outcome = 'FAILED') = (response_sha256 IS NULL))
);

-- Private provenance: revoke the default public-schema reader ACL
-- (postgres =r heimdal_reader) so heimdal_reader cannot read fetch records.
REVOKE SELECT ON company_location_pdf_fetch FROM heimdal_reader;

GRANT SELECT ON geo_postcode, company_location TO heimdal_reader;

-- Reviewed Faroese postcode reference seeded from Posta 2026-08-20 (see
-- docs/sources/2026-08-20-posta-postcodes.md). Coordinates are OSM city centres,
-- never business addresses. One statement; rerun-safe via ON CONFLICT DO UPDATE.
INSERT INTO geo_postcode (postal_code, city, region, latitude, longitude) VALUES
    ('100', 'Tórshavn', 'SUDURSTREYMOY', 62.01014, -6.77157),
    ('101', 'Tórshavn', 'SUDURSTREYMOY', 62.01014, -6.77157),
    ('102', 'Tórshavn', 'SUDURSTREYMOY', 62.01014, -6.77157),
    ('103', 'Tórshavn', 'SUDURSTREYMOY', 62.01014, -6.77157),
    ('110', 'Tórshavn', 'SUDURSTREYMOY', 62.01014, -6.77157),
    ('160', 'Argir', 'SUDURSTREYMOY', 61.99584, -6.77146),
    ('165', 'Argir', 'SUDURSTREYMOY', 61.99584, -6.77146),
    ('175', 'Kirkjubøur', 'SUDURSTREYMOY', 61.95371, -6.79341),
    ('176', 'Velbastaður', 'SUDURSTREYMOY', 61.98284, -6.85522),
    ('177', 'Syðradalur S', 'SUDURSTREYMOY', 62.01873, -6.91324),
    ('178', 'Norðradalur', 'SUDURSTREYMOY', 62.03976, -6.92522),
    ('180', 'Kaldbak', 'SUDURSTREYMOY', 62.06008, -6.83143),
    ('185', 'Kaldbaksbotnur', 'SUDURSTREYMOY', 62.06573, -6.91091),
    ('186', 'Sund', 'SUDURSTREYMOY', 62.04935, -6.85008),
    ('187', 'Hvítanes', 'SUDURSTREYMOY', 62.04466, -6.77260),
    ('188', 'Hoyvík', 'SUDURSTREYMOY', 62.03215, -6.77455),
    ('189', 'Hoyvík', 'SUDURSTREYMOY', 62.03215, -6.77455),
    ('210', 'Sandur', 'SANDOY', 61.83485, -6.81672),
    ('211', 'Sandur', 'SANDOY', 61.83485, -6.81672),
    ('215', 'Sandur', 'SANDOY', 61.83485, -6.81672),
    ('220', 'Skálavík', 'SANDOY', 61.83027, -6.66403),
    ('230', 'Húsavík', 'SANDOY', 61.80989, -6.67962),
    ('235', 'Dalur', 'SANDOY', 61.78430, -6.67642),
    ('236', 'Skarvanes', 'SANDOY', 61.79414, -6.74813),
    ('240', 'Skopun', 'SANDOY', 61.90394, -6.88464),
    ('260', 'Skúvoy', 'SANDOY', 61.77180, -6.80293),
    ('270', 'Nólsoy', 'SUDURSTREYMOY', 62.00970, -6.67358),
    ('280', 'Hestur', 'SUDURSTREYMOY', 61.95562, -6.88305),
    ('285', 'Koltur', 'SUDURSTREYMOY', 61.98618, -6.96769),
    ('286', 'Stóra Dímun', 'SANDOY', 61.68532, -6.75449),
    ('330', 'Stykkið', 'NORDURSTREYMOY', 62.11606, -7.04969),
    ('335', 'Leynar', 'NORDURSTREYMOY', 62.11700, -7.04086),
    ('336', 'Skælingur', 'NORDURSTREYMOY', 62.09515, -7.00706),
    ('340', 'Kvívík', 'NORDURSTREYMOY', 62.11962, -7.07638),
    ('350', 'Vestmanna', 'NORDURSTREYMOY', 62.15519, -7.17497),
    ('351', 'Vestmanna', 'NORDURSTREYMOY', 62.15519, -7.17497),
    ('355', 'Vestmanna', 'NORDURSTREYMOY', 62.15519, -7.17497),
    ('358', 'Válur', 'NORDURSTREYMOY', 62.15008, -7.15291),
    ('360', 'Sandavágur', 'VAGAR', 62.05448, -7.15107),
    ('370', 'Miðvágur', 'VAGAR', 62.04795, -7.19220),
    ('371', 'Miðvágur', 'VAGAR', 62.04795, -7.19220),
    ('375', 'Miðvágur', 'VAGAR', 62.04795, -7.19220),
    ('380', 'Sørvágur', 'VAGAR', 62.07075, -7.30605),
    ('381', 'Sørvágur', 'VAGAR', 62.07075, -7.30605),
    ('385', 'Vatnsoyrar', 'VAGAR', 62.07461, -7.25013),
    ('386', 'Bøur', 'VAGAR', 62.08798, -7.37145),
    ('387', 'Gásadalur', 'VAGAR', 62.10974, -7.43601),
    ('388', 'Mykines', 'VAGAR', 62.10425, -7.64765),
    ('400', 'Oyrarbakki', 'EYSTUROY', 62.20386, -6.99468),
    ('405', 'Oyrarbakki', 'EYSTUROY', 62.20386, -6.99468),
    ('410', 'Kollafjørður', 'NORDURSTREYMOY', 62.11739, -6.90242),
    ('415', 'Oyrareingir', 'NORDURSTREYMOY', 62.09859, -6.94733),
    ('416', 'Signabøur', 'NORDURSTREYMOY', 62.09655, -6.92375),
    ('420', 'Hósvík', 'NORDURSTREYMOY', 62.15281, -6.94340),
    ('430', 'Hvalvík', 'NORDURSTREYMOY', 62.18701, -7.03630),
    ('435', 'Streymnes', 'NORDURSTREYMOY', 62.19181, -7.03210),
    ('436', 'Saksun', 'NORDURSTREYMOY', 62.24423, -7.17125),
    ('437', 'Nesvík', 'NORDURSTREYMOY', 62.20729, -7.00818),
    ('438', 'Langasandur', 'NORDURSTREYMOY', 62.23346, -7.05095),
    ('440', 'Haldórsvík', 'NORDURSTREYMOY', 62.27590, -7.09403),
    ('445', 'Tjørnuvík', 'NORDURSTREYMOY', 62.28951, -7.14917),
    ('450', 'Oyri', 'EYSTUROY', 62.19039, -6.98505),
    ('460', 'Norðskáli', 'EYSTUROY', 62.21406, -7.00125),
    ('461', 'Norðskáli', 'EYSTUROY', 62.21406, -7.00125),
    ('465', 'Svínáir', 'EYSTUROY', 62.23004, -7.02697),
    ('466', 'Ljósá', 'EYSTUROY', 62.26694, -7.06143),
    ('470', 'Eiði', 'EYSTUROY', 62.29961, -7.09104),
    ('475', 'Funningur', 'EYSTUROY', 62.28707, -6.96634),
    ('476', 'Gjógv', 'EYSTUROY', 62.32574, -6.94303),
    ('477', 'Funningsfjørður', 'EYSTUROY', 62.23649, -6.93152),
    ('478', 'Elduvík', 'EYSTUROY', 62.28228, -6.91183),
    ('480', 'Skála', 'EYSTUROY', 62.16322, -6.78732),
    ('481', 'Skála', 'EYSTUROY', 62.16322, -6.78732),
    ('485', 'Skálabotnur', 'EYSTUROY', 62.19572, -6.85733),
    ('490', 'Strendur', 'EYSTUROY', 62.10948, -6.76122),
    ('494', 'Innan Glyvur', 'EYSTUROY', 62.13953, -6.75994),
    ('495', 'Kolbeinagjógv', 'EYSTUROY', 62.11004, -6.78596),
    ('496', 'Morskranes', 'EYSTUROY', 62.13060, -6.83842),
    ('497', 'Selatrað', 'EYSTUROY', 62.16007, -6.87789),
    ('510', 'Gøta', 'EYSTUROY', 62.20093, -6.74192),
    ('511', 'Gøtugjógv', 'EYSTUROY', 62.19251, -6.74707),
    ('512', 'Norðragøta', 'EYSTUROY', 62.20093, -6.74192),
    ('513', 'Syðrugøta', 'EYSTUROY', 62.18569, -6.75590),
    ('515', 'Gøta', 'EYSTUROY', 62.20093, -6.74192),
    ('516', 'Gøta', 'EYSTUROY', 62.20093, -6.74192),
    ('520', 'Leirvík', 'EYSTUROY', 62.20961, -6.70790),
    ('530', 'Fuglafjørður', 'EYSTUROY', 62.24384, -6.81332),
    ('535', 'Fuglafjørður', 'EYSTUROY', 62.24384, -6.81332),
    ('600', 'Saltangará', 'EYSTUROY', 62.11892, -6.72021),
    ('610', 'Saltangará', 'EYSTUROY', 62.11892, -6.72021),
    ('620', 'Runavík', 'EYSTUROY', 62.10840, -6.72167),
    ('621', 'Runavík', 'EYSTUROY', 62.10840, -6.72167),
    ('625', 'Glyvrar', 'EYSTUROY', 62.12836, -6.72401),
    ('626', 'Lambareiði', 'EYSTUROY', 62.14277, -6.73158),
    ('627', 'Lamba', 'EYSTUROY', 62.14210, -6.71015),
    ('640', 'Rituvík', 'EYSTUROY', 62.10744, -6.68206),
    ('645', 'Æðuvík', 'EYSTUROY', 62.06969, -6.69364),
    ('650', 'Toftir', 'EYSTUROY', 62.09097, -6.73224),
    ('655', 'Nes Eysturoy', 'EYSTUROY', 62.09500, -6.70611),
    ('656', 'Saltnes', 'EYSTUROY', 62.10806, -6.74383),
    ('660', 'Søldarfjørður', 'EYSTUROY', 62.15751, -6.75256),
    ('665', 'Skipanes', 'EYSTUROY', 62.17347, -6.77024),
    ('666', 'Undir Gøtueiði', 'EYSTUROY', 62.17603, -6.77570),
    ('690', 'Oyndarfjørður', 'EYSTUROY', 62.27717, -6.85408),
    ('695', 'Hellurnar', 'EYSTUROY', 62.26299, -6.84514),
    ('700', 'Klaksvík', 'NORDOYGGJAR', 62.22549, -6.58378),
    ('701', 'Klaksvík', 'NORDOYGGJAR', 62.22549, -6.58378),
    ('710', 'Klaksvík', 'NORDOYGGJAR', 62.22549, -6.58378),
    ('725', 'Norðoyri', 'NORDOYGGJAR', 62.21338, -6.54358),
    ('726', 'Ánirnar', 'NORDOYGGJAR', 62.25291, -6.57990),
    ('727', 'Árnafjørður', 'NORDOYGGJAR', 62.25540, -6.53630),
    ('730', 'Norðdepil', 'NORDOYGGJAR', 62.29586, -6.52639),
    ('735', 'Depil', 'NORDOYGGJAR', 62.28689, -6.52893),
    ('736', 'Norðtoftir', 'NORDOYGGJAR', 62.27529, -6.51492),
    ('737', 'Múli', 'NORDOYGGJAR', 62.35112, -6.58221),
    ('740', 'Hvannasund', 'NORDOYGGJAR', 62.29794, -6.52013),
    ('750', 'Viðareiði', 'NORDOYGGJAR', 62.36020, -6.53546),
    ('765', 'Svínoy', 'NORDOYGGJAR', 62.27803, -6.34236),
    ('766', 'Kirkja', 'NORDOYGGJAR', 62.31852, -6.31634),
    ('767', 'Hattarvík', 'NORDOYGGJAR', 62.32959, -6.27561),
    ('780', 'Kunoy', 'NORDOYGGJAR', 62.29294, -6.67003),
    ('785', 'Haraldssund', 'NORDOYGGJAR', 62.27376, -6.60629),
    ('795', 'Syðradalur K', 'NORDOYGGJAR', 62.24527, -6.66777),
    ('796', 'Húsar', 'NORDOYGGJAR', 62.26491, -6.69712),
    ('797', 'Mikladalur', 'NORDOYGGJAR', 62.33519, -6.76608),
    ('798', 'Trøllanes', 'NORDOYGGJAR', 62.36129, -6.78747),
    ('800', 'Tvøroyri', 'SUDUROY', 61.55533, -6.80468),
    ('801', 'Tvøroyri', 'SUDUROY', 61.55533, -6.80468),
    ('810', 'Tvøroyri', 'SUDUROY', 61.55533, -6.80468),
    ('825', 'Froðba', 'SUDUROY', 61.54687, -6.77527),
    ('826', 'Trongisvágur', 'SUDUROY', 61.56403, -6.83957),
    ('827', 'Øravík', 'SUDUROY', 61.53306, -6.81100),
    ('850', 'Hvalba', 'SUDUROY', 61.60063, -6.95683),
    ('860', 'Sandvík', 'SUDUROY', 61.63418, -6.92884),
    ('870', 'Fámjin', 'SUDUROY', 61.52469, -6.87927),
    ('900', 'Vágur', 'SUDUROY', 61.47389, -6.81146),
    ('910', 'Vágur', 'SUDUROY', 61.47389, -6.81146),
    ('926', 'Lopra', 'SUDUROY', 61.44311, -6.77034),
    ('927', 'Akrar', 'SUDUROY', 61.45629, -6.75749),
    ('928', 'Víkarbyrgi', 'SUDUROY', 61.44265, -6.72821),
    ('950', 'Porkeri', 'SUDUROY', 61.48196, -6.74630),
    ('960', 'Hov', 'SUDUROY', 61.50694, -6.75574),
    ('970', 'Sumba', 'SUDUROY', 61.40333, -6.71174)
ON CONFLICT (postal_code) DO UPDATE SET
    city = EXCLUDED.city,
    region = EXCLUDED.region,
    latitude = EXCLUDED.latitude,
    longitude = EXCLUDED.longitude;

-- The resumable enumeration worklist: one row per registration number probed.
--
-- search-cbi's "S{number}" form is an EXACT registration-number lookup, not a
-- prefix or substring match (verified: q=S2 returns the company registered as S2,
-- not S2730). The key space is therefore swept one number at a time, and this
-- table IS the denominator: every probed number ends as HIT or MISS, so presence
-- and absence are both recorded facts rather than inferences.
--
-- Terminal condition: no PENDING key left.
CREATE TABLE IF NOT EXISTS enumeration_key (
    search_key  text PRIMARY KEY,           -- e.g. 'S3690'
    key_number  integer NOT NULL UNIQUE,    -- 3690, so the sweep orders numerically
    status      text NOT NULL DEFAULT 'PENDING'
                  CHECK (status IN ('PENDING', 'HIT', 'MISS', 'ERROR')),
    row_count   integer,
    attempt_id  bigint REFERENCES request_attempt,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS enumeration_key_status_idx ON enumeration_key (status);
CREATE INDEX IF NOT EXISTS enumeration_key_number_idx ON enumeration_key (key_number);

-- WP-C1 frozen input set + parse harness (relation-processing plan).
--
-- One row per distinct get-table level=1 response body that backs a FETCHED
-- company (company_inventory -> request_attempt on requested_source_entity_id,
-- endpoint='get-table', http_status=200 -> response_content). This IS the
-- frozen input list: 4,527 input rows / 4,524 distinct bodies in the
-- 2026-08-02 snapshot (4 companies share one "[]" body — content dedup, a
-- finding to record, never a row to drop). Bodies are never modified; a
-- malformed body is a FAILED row with its reason, itemized by the
-- parse-inputs command's report — a finding, never a stop.
--
-- Recomputed from scratch by `parse-inputs` on every run (local data only,
-- deterministic, rerun-idempotent). company_count counts the FETCHED
-- companies each body is the input for, so the frozen input enumeration is
-- reconstructible from this table alone.
CREATE TABLE IF NOT EXISTS input_parse_status (
    response_sha256   char(64) PRIMARY KEY REFERENCES response_content,
    parse_status      text NOT NULL CHECK (parse_status IN ('OK', 'FAILED')),
    failure_reason    text CHECK (failure_reason IN
                          ('NON_JSON', 'EMPTY_BODY', 'NOT_ARRAY', 'BAD_ROW_KEYS')),
    row_count         integer,
    company_count     integer NOT NULL,
    computed_at       timestamptz NOT NULL DEFAULT now()
);

-- Canonical relations, built ONLY from cbTypeId values whose direction is
-- OBSERVED per the Direction Matrix (HEIMDAL-CURRENT-RELATIONS-SOURCE-CONTRACT.md
-- §15.5): cbTypeId=1 "Eigari N%" (DIRECT_OWNERSHIP), cbTypeId=41
-- "Veruligur Eigari" (REGISTERED_CONTROL_ACTOR). Every other cbTypeId — board/
-- committee roles, voting-rights percentages (cbTypeId=101, a DIFFERENT concept
-- from ownership despite also carrying a "%"), anything unrecognised, and the
-- rare O.V.E. row that is NOT a self-edge (§14a.2 anomaly) — is stored as
-- OTHER_SOURCE_RELATION: raw fields preserved, no canonical subject/object
-- claimed, semantic_direction_confidence=UNCONFIRMED. Never guessed, never
-- dropped.
--
-- One row per distinct (from, to, type): the same edge is visible from both
-- endpoints' get-table responses (each side is independently inventoried and
-- fetched), and the unique constraint + ON CONFLICT DO NOTHING is the dedup.
CREATE TABLE IF NOT EXISTS canonical_relation (
    relation_id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    raw_from_source_entity_id      bigint NOT NULL,
    raw_from_source_entity_label   text NOT NULL,
    raw_to_source_entity_id        bigint NOT NULL,
    raw_to_source_entity_label     text NOT NULL,
    raw_source_relation_type_id    integer NOT NULL,   -- cbTypeId
    raw_source_relation_label      text NOT NULL,       -- cbTypeName, verbatim
    canonical_relation_type        text NOT NULL
                                     CHECK (canonical_relation_type IN
                                       ('DIRECT_OWNERSHIP', 'REGISTERED_CONTROL_ACTOR',
                                        'OTHER_SOURCE_RELATION')),
    canonical_subject_entity_id    bigint,   -- NULL for OTHER_SOURCE_RELATION
    canonical_object_entity_id     bigint,   -- (direction unconfirmed — never guessed)
    semantic_direction_confidence  text NOT NULL
                                     CHECK (semantic_direction_confidence IN
                                       ('OBSERVED_AND_OWNER_ACCEPTED',
                                        'OBSERVED_FOR_SUPPLIED_EXAMPLES', 'UNCONFIRMED')),
    raw_percentage_label            text,           -- DIRECT_OWNERSHIP only
    parsed_percentage               numeric(6,3),   -- NULL on parse failure, never 0
    percentage_parse_status         text CHECK (percentage_parse_status IN ('PARSED', 'FAILED')),
    CONSTRAINT canonical_relation_percentage_invariant CHECK (
        -- a FAILED parse can never hold a percentage (never silently 0, §14);
        -- a percentage can only exist with PARSED, and only DIRECT_OWNERSHIP
        -- rows get either column at all. §14a.1: a REGISTERED_CONTROL_ACTOR
        -- row has NO percentage field — enforced here by the type linkage,
        -- not left to a convention a later query could ignore.
        (canonical_relation_type = 'DIRECT_OWNERSHIP') = (raw_percentage_label IS NOT NULL)
        AND (percentage_parse_status = 'PARSED') = (parsed_percentage IS NOT NULL)
    ),

    -- §14a.2: an O.V.E. row with From <> To is an anomaly outside the
    -- evidence base (the normal pattern is From=To, converted to an
    -- ENTITY_STATUS row instead of a canonical edge — see below). Found
    -- missing during independent audit (2026-08-02): 8 such rows existed,
    -- correctly stored as OTHER_SOURCE_RELATION with UNCONFIRMED direction,
    -- but with no flag distinguishing "confirmed anomaly" from an ordinary
    -- unclassified relation type. NULL for every other row.
    semantic_status                 text CHECK (semantic_status IN ('UNRESOLVED_SOURCE_ANOMALY')),

    source_attempt_id               bigint NOT NULL REFERENCES request_attempt,
    first_stored_at                 timestamptz NOT NULL DEFAULT now(),

    -- Source group (Nevnd/Stjórn/Eigarar/Annað, SoT §18/§19 prerequisite).
    -- The source's own /api/relations group taxonomy is NEVER attached to a
    -- get-table edge (only cbTypeId/cbTypeName are) — no OBSERVED cbTypeId->
    -- groupId link exists anywhere in the source contract. inferred_group is
    -- therefore always INFERRED from cbTypeName text similarity, set by the
    -- `normalize-roles` command, never OBSERVED. canonical_role_label is a
    -- gender-neutral display label (owner instruction 2026-08-02: Næstforkvinna
    -- -> Næstformaður, Nevndarforkvinna -> Nevndarformaður).
    inferred_group             text CHECK (inferred_group IN ('EIGARAR', 'NEVND', 'STJORN', 'ANNAD')),
    inferred_group_confidence  text CHECK (inferred_group_confidence IN ('INFERRED')),
    canonical_role_label       text,

    UNIQUE (raw_from_source_entity_id, raw_to_source_entity_id, raw_source_relation_type_id)
);

CREATE INDEX IF NOT EXISTS canonical_relation_subject_idx ON canonical_relation (canonical_subject_entity_id);
CREATE INDEX IF NOT EXISTS canonical_relation_object_idx  ON canonical_relation (canonical_object_entity_id);
CREATE INDEX IF NOT EXISTS canonical_relation_type_idx    ON canonical_relation (canonical_relation_type);

-- Backfill the percentage invariant onto pre-existing databases (the CREATE
-- TABLE above only guards fresh installs). Older installs carry the weaker
-- 2026-08-02 shape without the §14a.1 type linkage — drop and re-add so the
-- §14a.1 form always holds. Idempotent: identical outcome on every run.
DO $$
BEGIN
    ALTER TABLE canonical_relation
        DROP CONSTRAINT IF EXISTS canonical_relation_percentage_invariant;
    ALTER TABLE canonical_relation
        ADD CONSTRAINT canonical_relation_percentage_invariant CHECK (
            (canonical_relation_type = 'DIRECT_OWNERSHIP') = (raw_percentage_label IS NOT NULL)
            AND (percentage_parse_status = 'PARSED') = (parsed_percentage IS NOT NULL)
        );
END $$;

-- O.V.E. (cbTypeId=81) self-edges only (§14a.2): "no registered control actor"
-- is an entity STATUS, never a canonical self-edge. A non-self-edge O.V.E. row
-- is the §14a.2 anomaly and goes to canonical_relation as OTHER_SOURCE_RELATION
-- instead — never here, never guessed.
CREATE TABLE IF NOT EXISTS entity_status (
    entity_status_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_entity_id     bigint NOT NULL,
    canonical_status_type text NOT NULL CHECK (canonical_status_type IN ('NO_REGISTERED_CONTROL_ACTOR')),
    raw_source_label      text NOT NULL,
    source_attempt_id     bigint NOT NULL REFERENCES request_attempt,
    first_stored_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_entity_id, canonical_status_type)
);

-- Indirect ownership (owner-authorized 2026-08-02, SoT §15), computed locally
-- from CANONICAL_RELATION's DIRECT_OWNERSHIP rows only — no new upstream
-- request. Recomputed from scratch on every run (cheap, local); the table is
-- cleared and rebuilt, not incrementally patched.
--
-- computed_percentage/path_count are exact regardless of any display cap: they
-- come from a scalar DP over the ownership DAG, never from summing a truncated
-- path list. contributing_paths is a best-effort SAMPLE of concrete paths for
-- display, capped at a fixed size per pair — see PATH_SAMPLE_CAP in
-- heimdal_collect.py. paths_truncated is set whenever path_count exceeds the
-- number of sampled paths actually stored.
--
-- An entity that owns a fraction of itself, or sits in any cross-ownership
-- cycle, is excluded from this table entirely on both sides (owner and owned)
-- per SoT §15's cycle rule: no indirect percentage is shown for a cycle. Its
-- direct relations remain untouched in CANONICAL_RELATION; see
-- OWNERSHIP_CYCLE_MEMBER for which entities and why.
CREATE TABLE IF NOT EXISTS indirect_ownership (
    indirect_ownership_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_entity_id         bigint NOT NULL,
    owned_entity_id         bigint NOT NULL,
    computed_percentage     numeric(9,4) NOT NULL,
    path_count              integer NOT NULL,
    contributing_paths      jsonb NOT NULL,
    paths_truncated         boolean NOT NULL DEFAULT false,
    computed_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_entity_id, owned_entity_id)
);

CREATE INDEX IF NOT EXISTS indirect_ownership_owner_idx ON indirect_ownership (owner_entity_id);
CREATE INDEX IF NOT EXISTS indirect_ownership_owned_idx ON indirect_ownership (owned_entity_id);

-- Which entities are excluded from indirect_ownership, and why: every entity
-- in the same cycle_group_id is mutually reachable through DIRECT_OWNERSHIP
-- edges (or, for a single-member group, owns a fraction of itself). A group
-- of size 1 is a self-loop; size >1 is a genuine cross-ownership cycle.
CREATE TABLE IF NOT EXISTS ownership_cycle_member (
    cycle_group_id  integer NOT NULL,
    entity_id       bigint NOT NULL,
    PRIMARY KEY (cycle_group_id, entity_id)
);

-- Person-to-person relation contexts (owner-authorized 2026-08-02, SoT §18),
-- the atomic unit from §18.4: person_a, person_b, company, each person's
-- relation, snapshot. Built from two CANONICAL_RELATION rows that share the
-- same company side and have a different person on the other side — person
-- vs company is read from the get-table label's own type token ("Persónur"),
-- never guessed from company_inventory membership (a foreign/unregistered
-- COMPANY is absent from that inventory too and must not be misread as a
-- person). Recomputed from scratch each run, like INDIRECT_OWNERSHIP.
--
-- person_a_entity_id < person_b_entity_id is an enforced canonical ordering
-- (the pair is symmetric — "A and B share company C" has no natural direction)
-- so the same pair is never stored twice in swapped order. A person with
-- multiple roles at the same company (e.g. owner AND Nevndarlimur) produces
-- one context row per distinct pair of underlying rows, not one merged row —
-- matching §18.3's requirement that cross-role combinations stay visible.
CREATE TABLE IF NOT EXISTS person_relation_context (
    context_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_a_entity_id    bigint NOT NULL,
    person_b_entity_id    bigint NOT NULL,
    company_entity_id     bigint NOT NULL,
    person_a_relation_id  bigint NOT NULL REFERENCES canonical_relation (relation_id),
    person_a_group        text,          -- copied from canonical_relation.inferred_group (INFERRED)
    person_a_role_label   text,          -- copied from canonical_relation.canonical_role_label
    person_b_relation_id  bigint NOT NULL REFERENCES canonical_relation (relation_id),
    person_b_group        text,
    person_b_role_label   text,
    is_cross_role         boolean NOT NULL,   -- person_a_group <> person_b_group
    computed_at           timestamptz NOT NULL DEFAULT now(),
    CHECK (person_a_entity_id < person_b_entity_id)
);

CREATE INDEX IF NOT EXISTS person_relation_context_a_idx   ON person_relation_context (person_a_entity_id);
CREATE INDEX IF NOT EXISTS person_relation_context_b_idx   ON person_relation_context (person_b_entity_id);
CREATE INDEX IF NOT EXISTS person_relation_context_co_idx  ON person_relation_context (company_entity_id);

-- Company-to-company relation contexts (owner-authorized 2026-08-02, SoT §19).
-- relation_basis distinguishes several structurally different kinds of link
-- between two companies, per SoT §19's enum — a combined count is never
-- shown without this breakdown:
--   DIRECT_OWNERSHIP / INDIRECT_OWNERSHIP  — company_a owns company_b, directly
--     or via the DP in INDIRECT_OWNERSHIP. Directional: company_a is the owner.
--     related_entity_id is NULL (the link IS the two companies, no third party).
--   SHARED_OWNER / SHARED_BENEFICIAL_OWNER / SHARED_BOARD_PERSON /
--   SHARED_MANAGEMENT_PERSON / SHARED_OTHER_RELATION / CROSS_ROLE_PERSON —
--     the same person (related_entity_id) has a relation to both companies.
--     Symmetric: company_a_entity_id/company_b_entity_id carry no ordering
--     guarantee for these bases, but each unordered pair is only ever computed
--     once per person per run (see build-relation-contexts).
--   DIRECT_CONTROL_ACTOR — owner extension beyond SoT §19's literal 8-value
--     enum (owner decision 2026-08-02, found during verification): the source
--     allows a COMPANY, not only a person, to be registered as another
--     company's "Veruligur Eigari" (cbTypeId=41, confirmed direction per the
--     Direction Matrix). Directional like DIRECT_OWNERSHIP: company_a is the
--     registered control actor. related_entity_id NULL, self-edges excluded.
CREATE TABLE IF NOT EXISTS company_relation_context (
    context_id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_a_entity_id  bigint NOT NULL,
    company_b_entity_id  bigint NOT NULL,
    relation_basis       text NOT NULL CHECK (relation_basis IN
                           ('DIRECT_OWNERSHIP', 'INDIRECT_OWNERSHIP', 'SHARED_OWNER',
                            'SHARED_BENEFICIAL_OWNER', 'SHARED_BOARD_PERSON',
                            'SHARED_MANAGEMENT_PERSON', 'CROSS_ROLE_PERSON',
                            'SHARED_OTHER_RELATION', 'DIRECT_CONTROL_ACTOR')),
    related_entity_id    bigint,     -- the shared person; NULL for DIRECT/INDIRECT_OWNERSHIP
    company_a_relation   text,
    company_b_relation   text,
    source_relation_id   bigint REFERENCES canonical_relation (relation_id),  -- NULL for INDIRECT_OWNERSHIP (no single row)
    computed_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (company_a_entity_id <> company_b_entity_id)
);

CREATE INDEX IF NOT EXISTS company_relation_context_a_idx    ON company_relation_context (company_a_entity_id);
CREATE INDEX IF NOT EXISTS company_relation_context_b_idx    ON company_relation_context (company_b_entity_id);
CREATE INDEX IF NOT EXISTS company_relation_context_basis_idx ON company_relation_context (relation_basis);

-- Canonical entity IDs (SoT §3.1/§3.2, Fase 0 of the portal/API tier,
-- owner-authorized 2026-08-02). Portal/API routes are built on
-- canonical_entity_id, never on raw_*_source_entity_id directly — §3.2
-- requires an ID stable and never reused, separate from the source's own
-- pkCbi. THIS TABLE IS APPEND-ONLY: populated by `assign-canonical-ids` via
-- INSERT ... ON CONFLICT DO NOTHING keyed on (source_system,
-- source_entity_id), never TRUNCATEd and rebuilt like the other derived
-- tables — rebuilding would reassign IDs and break every published link.
--
-- entity_kind (PERSON/COMPANY/ORGANIZATION/PUBLIC_AUTHORITY/UNKNOWN_ENTITY,
-- SoT §3.1) is INFERRED from the label's own type token — confirmed
-- exhaustive against the live data (46 distinct tokens, 0 unparseable,
-- 0 falling to UNKNOWN_ENTITY) but still a labelled best-guess, same
-- discipline as CANONICAL_RELATION.inferred_group.
--
-- display_name is the PARSED name only, never the raw label (owner decision
-- 2026-08-02: raw labels for PERSON entities carry a private home address,
-- e.g. "Jane Doe - Natural Person, Flat 1, 1 Example Avenue
-- London England" — the address must never surface in search results, list
-- rows, or graph node labels; it is only ever shown inside a page's raw
-- source-text disclosure, read directly from CANONICAL_RELATION).
CREATE TABLE IF NOT EXISTS canonical_entity (
    canonical_entity_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_system           text NOT NULL,
    source_entity_id        bigint NOT NULL,
    entity_kind             text NOT NULL
                              CHECK (entity_kind IN ('PERSON', 'COMPANY', 'ORGANIZATION',
                                                      'PUBLIC_AUTHORITY', 'UNKNOWN_ENTITY')),
    entity_kind_confidence  text NOT NULL CHECK (entity_kind_confidence IN ('INFERRED')),
    raw_type_token          text,
    display_name            text NOT NULL,
    first_seen_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_system, source_entity_id)
);

CREATE INDEX IF NOT EXISTS canonical_entity_kind_idx ON canonical_entity (entity_kind);
CREATE INDEX IF NOT EXISTS canonical_entity_source_idx ON canonical_entity (source_system, source_entity_id);

COMMIT;
