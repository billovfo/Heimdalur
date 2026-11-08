-- HEIMDAL REGISTRY PROJECTION v1 - isolated projection-layer migration.
-- Normative source: docs/HEIMDAL-REGISTRY-PROJECTION-ARCHITECTURE-v1.md and the
-- owner task "ISOLERET DATABASE-LAG + KILL SWITCH + VERSIONERET PROJECTION".
--
-- Placement: this migration runs in the PDF-history database (pdfhist_pilot /
-- HEIMDAL_PDFHIST_DB), the database that already owns the immutable pdf_*
-- evidence layer. Dependency direction is projection -> evidence only:
-- projection tables may FK to evidence; evidence never depends on projection.
-- The heimdal QA/production database receives NOTHING in this task.
--
-- Conventions follow heimdal/pdfhist/migration.sql: plain SQL, idempotent
-- (IF NOT EXISTS / CREATE OR REPLACE), binding create order, no stored
-- procedures, no semicolons inside string/comment text, no mutation of any
-- pre-existing table.

-- 0) Isolated schema. If this name already exists for a different purpose,
--    stop and inspect before reuse (task section 4).
CREATE SCHEMA IF NOT EXISTS registry_projection;

-- 1) Projection generation registry. One row per projection build.
CREATE TABLE IF NOT EXISTS registry_projection.projection_run (
    projection_run_id        bigserial PRIMARY KEY,
    projection_version       text NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    completed_at             timestamptz,
    status                   text NOT NULL
                             CHECK (status IN
                             ('BUILDING','SHADOW','ACTIVE','RETIRED','FAILED')),
    source_collection_run_id bigint,
    source_parser_version    text,
    code_version             text NOT NULL,
    content_hash             text,
    metrics_json             jsonb,
    notes                    text
);
CREATE UNIQUE INDEX IF NOT EXISTS projection_run_one_active_idx
    ON registry_projection.projection_run ((1))
    WHERE status = 'ACTIVE';

-- 2) Consumer-ready company state: one row per company per projection run.
CREATE TABLE IF NOT EXISTS registry_projection.company_current_state (
    id                     bigserial PRIMARY KEY,
    projection_run_id      bigint NOT NULL
                           REFERENCES registry_projection.projection_run(projection_run_id),
    company_id             bigint NOT NULL,
    registration_number    text,
    snapshot_date          date,
    source_document_id     bigint,
    source_observation_id  bigint,
    source_parse_result_id bigint,
    created_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT company_current_state_unique
        UNIQUE (projection_run_id, company_id)
);
CREATE INDEX IF NOT EXISTS company_current_state_run_idx
    ON registry_projection.company_current_state (projection_run_id);
CREATE INDEX IF NOT EXISTS company_current_state_company_idx
    ON registry_projection.company_current_state (company_id);

-- 3) Field-level authority model: every projected field carries its own
--    authority state and provenance (task sections 9/10/39/41/45).
CREATE TABLE IF NOT EXISTS registry_projection.company_current_state_field (
    id                     bigserial PRIMARY KEY,
    projection_run_id      bigint NOT NULL
                           REFERENCES registry_projection.projection_run(projection_run_id),
    company_id             bigint NOT NULL,
    field_name             text NOT NULL,
    value_json             jsonb,
    authority_state        text NOT NULL
                           CHECK (authority_state IN
                           ('AUTHORITATIVE_SNAPSHOT','HISTORICAL_EVENT_ONLY',
                            'SOURCE_NOT_AUTHORITATIVE','SAME_DAY_UNORDERED',
                            'SOURCE_ANOMALY','NOT_AVAILABLE')),
    source_document_id     bigint,
    source_observation_id  bigint,
    source_parse_result_id bigint,
    source_snapshot_date   date,
    warning_code           text,
    created_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT company_current_state_field_unique
        UNIQUE (projection_run_id, company_id, field_name)
);
CREATE INDEX IF NOT EXISTS company_current_state_field_run_idx
    ON registry_projection.company_current_state_field (projection_run_id);
CREATE INDEX IF NOT EXISTS company_current_state_field_company_idx
    ON registry_projection.company_current_state_field (company_id);
CREATE INDEX IF NOT EXISTS company_current_state_field_authority_idx
    ON registry_projection.company_current_state_field (authority_state);

-- 4) Historical Lysing evidence rows (HISTORICAL_EVENT_ONLY authority).
CREATE TABLE IF NOT EXISTS registry_projection.company_history_event (
    id                     bigserial PRIMARY KEY,
    projection_run_id      bigint NOT NULL
                           REFERENCES registry_projection.projection_run(projection_run_id),
    company_id             bigint NOT NULL,
    registration_number    text,
    event_type             text NOT NULL,
    action                 text NOT NULL,
    entity_key             text NOT NULL,
    value_json             jsonb,
    effective_date         date,
    registration_date      date,
    notice_date            date,
    source_event_date      date,
    source_event_id        bigint REFERENCES pdf_source_event(id),
    source_document_id     bigint,
    source_observation_id  bigint,
    source_parse_result_id bigint,
    raw_text               text,
    authority_state        text NOT NULL
                           DEFAULT 'HISTORICAL_EVENT_ONLY'
                           CHECK (authority_state IN
                           ('HISTORICAL_EVENT_ONLY','SOURCE_ANOMALY')),
    created_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT company_history_event_unique
        UNIQUE (projection_run_id, source_event_id, event_type, action,
                entity_key, source_document_id)
);
CREATE INDEX IF NOT EXISTS company_history_event_run_idx
    ON registry_projection.company_history_event (projection_run_id);
CREATE INDEX IF NOT EXISTS company_history_event_company_idx
    ON registry_projection.company_history_event (company_id);

-- 5) Consumer convenience: person-role rows. Current rows are generated ONLY
--    from authoritative snapshot state (task sections 12/17).
CREATE TABLE IF NOT EXISTS registry_projection.company_person_role (
    id                     bigserial PRIMARY KEY,
    projection_run_id      bigint NOT NULL
                           REFERENCES registry_projection.projection_run(projection_run_id),
    company_id             bigint NOT NULL,
    role_type              text NOT NULL
                           CHECK (role_type IN ('BOARD','MANAGEMENT','AUDITOR')),
    person_name            text NOT NULL,
    role_title             text,
    entity_normalized      text NOT NULL,
    source_scope           text NOT NULL
                           CHECK (source_scope IN
                           ('CURRENT_AUTHORITATIVE','HISTORICAL_EVENT',
                            'NON_AUTHORITATIVE')),
    source_document_id     bigint,
    source_observation_id  bigint,
    source_parse_result_id bigint,
    source_event_id        bigint REFERENCES pdf_source_event(id),
    created_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT company_person_role_unique
        UNIQUE (projection_run_id, company_id, role_type, entity_normalized,
                source_scope)
);
CREATE INDEX IF NOT EXISTS company_person_role_run_idx
    ON registry_projection.company_person_role (projection_run_id);
CREATE INDEX IF NOT EXISTS company_person_role_company_idx
    ON registry_projection.company_person_role (company_id);

-- 6) Addresses: company address is kept strictly separate from person, board,
--    management, auditor, reporter and invoice addresses (task section 18).
CREATE TABLE IF NOT EXISTS registry_projection.company_address (
    id                     bigserial PRIMARY KEY,
    projection_run_id      bigint NOT NULL
                           REFERENCES registry_projection.projection_run(projection_run_id),
    company_id             bigint NOT NULL,
    address_role           text NOT NULL
                           CHECK (address_role IN
                           ('COMPANY','BOARD','MANAGEMENT','AUDITOR',
                            'REPORTER','INVOICE')),
    source_scope           text NOT NULL
                           CHECK (source_scope IN
                           ('CURRENT_AUTHORITATIVE','HISTORICAL_EVENT',
                            'NON_AUTHORITATIVE')),
    value_json             jsonb NOT NULL,
    source_document_id     bigint,
    source_observation_id  bigint,
    source_parse_result_id bigint,
    created_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT company_address_unique
        UNIQUE (projection_run_id, company_id, address_role, source_scope)
);
CREATE INDEX IF NOT EXISTS company_address_run_idx
    ON registry_projection.company_address (projection_run_id);

-- 5) Administrative settings. NOTE: the authoritative runtime feature-flag
--    source is the config layer (environment), NOT this table; this table is
--    the DB mirror used by the admin CLI and by deployments without env
--    overrides. Precedence is documented in
--    docs/HEIMDAL-REGISTRY-PROJECTION-ARCHITECTURE-v1.md section 13.
CREATE TABLE IF NOT EXISTS registry_projection.projection_setting (
    key        text PRIMARY KEY,
    value_json jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO registry_projection.projection_setting (key, value_json)
VALUES ('integration_enabled', 'false'::jsonb),
       ('ui_enabled', 'false'::jsonb),
       ('active_projection_run_id', 'null'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- 7) Deterministic warnings (task sections 19/42/43/44).
CREATE TABLE IF NOT EXISTS registry_projection.projection_warning (
    id                bigserial PRIMARY KEY,
    projection_run_id bigint NOT NULL
                      REFERENCES registry_projection.projection_run(projection_run_id),
    company_id        bigint,
    warning_code      text NOT NULL,
    severity          text NOT NULL
                      CHECK (severity IN ('INFO','WARN','ERROR')),
    details_json      jsonb,
    source_document_id bigint,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT projection_warning_unique
        UNIQUE (projection_run_id, company_id, warning_code)
);
CREATE INDEX IF NOT EXISTS projection_warning_run_idx
    ON registry_projection.projection_warning (projection_run_id);
CREATE INDEX IF NOT EXISTS projection_warning_company_idx
    ON registry_projection.projection_warning (company_id);

-- 8) Derived views: convenience readers bound to the ACTIVE run only. They
--    return zero rows unless a projection has been explicitly activated;
--    runtime exposure is additionally gated by the kill switch (task
--    sections 20/36/50/66/67).
CREATE OR REPLACE VIEW registry_projection.v_active_run AS
    SELECT * FROM registry_projection.projection_run
    WHERE status = 'ACTIVE';

CREATE OR REPLACE VIEW registry_projection.v_active_company_current_state AS
    SELECT s.* FROM registry_projection.company_current_state s
    JOIN registry_projection.v_active_run r
      ON r.projection_run_id = s.projection_run_id;

CREATE OR REPLACE VIEW registry_projection.v_active_company_current_state_field AS
    SELECT f.* FROM registry_projection.company_current_state_field f
    JOIN registry_projection.v_active_run r
      ON r.projection_run_id = f.projection_run_id;
-- 9) Entity linking layer (task: KNYT PDF-DATA TIL EKSISTERENDE
--    VIRKSOMHEDER OG PERSONER). Derived, disposable links between the
--    ACTIVE projection generation and EXISTING canonical Heimdal entities.
--    This layer links only; it never creates or mutates canonical data.
--    canonical_entity_id references the SEPARATE heimdal QA/production
--    database (cross-database, so no SQL FK is possible); the value is
--    provenance-audited in match_metadata instead.
CREATE TABLE IF NOT EXISTS registry_projection.entity_link (
    entity_link_id      bigserial PRIMARY KEY,
    projection_run_id   bigint NOT NULL
                        REFERENCES registry_projection.projection_run(projection_run_id),
    entity_type         text NOT NULL
                        CHECK (entity_type IN ('COMPANY','PERSON')),
    source_entity_key   text NOT NULL,
    -- COMPANY: normalized registration number (digits).
    -- PERSON: 'ROLE:<company_person_role.id>' — role-occurrence scope
    -- (section 14: no global source person identity exists, so linking is
    -- scoped to the concrete person-role occurrence).
    canonical_entity_id bigint,
    registration_number text,
    person_role_id      bigint,
    link_status         text NOT NULL
                        CHECK (link_status IN ('LINKED','UNRESOLVED','AMBIGUOUS')),
    link_method         text NOT NULL
                        CHECK (link_method IN
                        ('REGISTRATION_NUMBER_EXACT','EXACT_MULTI_ATTRIBUTE')),
    match_metadata      jsonb,
    link_code_version   text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    -- One link row per source identity per run: a second link to a different
    -- canonical entity within the same run is impossible without the
    -- AMBIGUOUS status (single row, canonical_entity_id NULL, candidates in
    -- match_metadata).
    CONSTRAINT entity_link_run_type_source_unique
        UNIQUE (projection_run_id, entity_type, source_entity_key),
    -- LINKED rows must carry a canonical entity; the others must not.
    CONSTRAINT entity_link_canonical_presence CHECK (
        (link_status = 'LINKED' AND canonical_entity_id IS NOT NULL) OR
        (link_status IN ('UNRESOLVED','AMBIGUOUS') AND canonical_entity_id IS NULL)),
    -- Company links are keyed by their normalized registration number.
    CONSTRAINT entity_link_company_source_key CHECK (
        entity_type <> 'COMPANY' OR source_entity_key = registration_number),
    -- Person links always point at the concrete source role occurrence.
    CONSTRAINT entity_link_person_role_required CHECK (
        entity_type <> 'PERSON' OR person_role_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS entity_link_run_status_idx
    ON registry_projection.entity_link (projection_run_id, entity_type, link_status);
CREATE INDEX IF NOT EXISTS entity_link_canonical_idx
    ON registry_projection.entity_link (canonical_entity_id)
    WHERE canonical_entity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS entity_link_person_role_idx
    ON registry_projection.entity_link (person_role_id)
    WHERE person_role_id IS NOT NULL;

CREATE OR REPLACE VIEW registry_projection.v_active_entity_link AS
    SELECT l.* FROM registry_projection.entity_link l
    JOIN registry_projection.v_active_run r
      ON r.projection_run_id = l.projection_run_id;
