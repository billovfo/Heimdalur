-- HISTORICAL (audit B-17, 2026-11-06): the canonical DB's schema is the ledger in
-- sql/migrations/ (python3 -m heimdal.canonical_migrations). Change it there, not here.
-- HEIMDAL — registry_analytics Phase 2 schema (plan v1.3.1 §5.1-5.3, §5.7).
-- Phase 2 minimal surface ONLY: analytics_build, person_company_observation,
-- person_company_period. Phase 3 tables (person_person_overlap, person_group*)
-- are deliberately NOT created here (task B1: minimal schema surface; they can
-- be added by a later checked-in migration without touching Phase 2 tables).
-- Idempotent: identical outcome on every run.

CREATE SCHEMA IF NOT EXISTS registry_analytics;

CREATE TABLE IF NOT EXISTS registry_analytics.analytics_build (
    build_run_id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_projection_run_id bigint NOT NULL,
    pdf_link_code_version    text NOT NULL,
    ownership_source_meta    jsonb NOT NULL,
    canonical_identity_pin   jsonb NOT NULL,
    temporal_conventions     jsonb NOT NULL,
    group_support_policy     jsonb NOT NULL,
    group_size_cap           integer NOT NULL,
    phase0_measurement_id    text NOT NULL,
    phase0_measured_at       text NOT NULL,
    build_warnings           jsonb NOT NULL,
    coverage                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    content_hash             text,
    status                   text NOT NULL CHECK (status IN ('ACTIVE','FAILED','SUPERSEDED')),
    built_at                 timestamptz NOT NULL DEFAULT now(),
    CHECK (phase0_measurement_id <> '' AND phase0_measurement_id <> 'N/A'),
    CHECK (phase0_measured_at <> '' AND phase0_measured_at <> 'N/A')
);

CREATE TABLE IF NOT EXISTS registry_analytics.person_company_observation (
    observation_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    build_run_id            bigint NOT NULL REFERENCES registry_analytics.analytics_build (build_run_id),
    source_observation_key  text NOT NULL,

    canonical_person_id     bigint REFERENCES public.canonical_entity (canonical_entity_id),  -- NULL = company-level unresolved evidence
    canonical_company_id    bigint NOT NULL REFERENCES public.canonical_entity (canonical_entity_id),

    role_context            text NOT NULL CHECK (role_context IN ('BOARD','MANAGEMENT','OWNERSHIP','CONTROL_ACTOR')),
    source_family           text NOT NULL CHECK (source_family IN
                              ('PDF_HISTORY','OWNERSHIP_GRAPH','OWNERSHIP_WEB_GOVERNANCE')),
    authority_class         text NOT NULL CHECK (authority_class IN
                               ('AUTHORITATIVE_CURRENT_GOVERNANCE',
                                'SUPPLEMENTARY_WEB_GOVERNANCE_OBSERVATION',
                                'OWNERSHIP_POINT_OBSERVATION',
                                'HISTORICAL_PDF_OBSERVATION')),
    identity_resolution     text CHECK (identity_resolution IN
                               ('DIRECT_LINK','SOURCE_LOCAL_PROPAGATION')),
    observation_kind        text NOT NULL CHECK (observation_kind IN
                               ('CURRENT_STATE_SNAPSHOT','APPOINTED_EVENT','REMOVED_EVENT',
                                'OWNERSHIP_SNAPSHOT_POINT','WEB_GOVERNANCE_SNAPSHOT_POINT')),

    observed_at             date,
    observed_state_at       date,

    action                  text,
    state                   text,
    ownership_percentage    numeric(6,3),

    source_projection_run_id  bigint,
    source_person_role_id     bigint,   -- CPR id: identity-bridge metadata only, never counted (plan §8)
    source_history_event_id   bigint,   -- CHE id: the ONLY counted event observation key family
    source_event_id           bigint,
    source_document_id        bigint,
    source_observation_id     bigint,
    source_relation_id        bigint,
    source_attempt_id         bigint,

    provenance              jsonb NOT NULL DEFAULT '{}'::jsonb,

    UNIQUE (build_run_id, source_observation_key),

    -- authority-class linkage (plan §5.2)
    CONSTRAINT pco_authoritative_governance_shape CHECK (
        authority_class <> 'AUTHORITATIVE_CURRENT_GOVERNANCE'
        OR (source_family = 'PDF_HISTORY' AND observation_kind = 'CURRENT_STATE_SNAPSHOT')),
    CONSTRAINT pco_ownership_point_family CHECK (
        authority_class <> 'OWNERSHIP_POINT_OBSERVATION' OR source_family = 'OWNERSHIP_GRAPH'),
    CONSTRAINT pco_webgov_supplementary_shape CHECK (
        authority_class <> 'SUPPLEMENTARY_WEB_GOVERNANCE_OBSERVATION'
        OR source_family = 'OWNERSHIP_WEB_GOVERNANCE'),
    CONSTRAINT pco_control_actor_no_percentage CHECK (
        role_context <> 'CONTROL_ACTOR' OR ownership_percentage IS NULL),
    CONSTRAINT pco_propagation_only_events CHECK (
        identity_resolution IS DISTINCT FROM 'SOURCE_LOCAL_PROPAGATION'
        OR observation_kind IN ('APPOINTED_EVENT','REMOVED_EVENT')),
    CONSTRAINT pco_ownership_percentage_only_on_ownership CHECK (
        ownership_percentage IS NULL OR (role_context = 'OWNERSHIP'
        AND authority_class = 'OWNERSHIP_POINT_OBSERVATION'))
);

CREATE INDEX IF NOT EXISTS pco_build_person_idx ON registry_analytics.person_company_observation (build_run_id, canonical_person_id);
CREATE INDEX IF NOT EXISTS pco_build_company_idx ON registry_analytics.person_company_observation (build_run_id, canonical_company_id);
CREATE INDEX IF NOT EXISTS pco_build_context_idx ON registry_analytics.person_company_observation (build_run_id, role_context);

CREATE TABLE IF NOT EXISTS registry_analytics.person_company_period (
    period_id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    build_run_id             bigint NOT NULL REFERENCES registry_analytics.analytics_build (build_run_id),
    canonical_person_id      bigint NOT NULL,
    canonical_company_id     bigint NOT NULL,
    role_context             text NOT NULL CHECK (role_context IN ('BOARD','MANAGEMENT','OWNERSHIP','CONTROL_ACTOR')),

    period_start             date,
    period_end               date,        -- half-open [start, end)
    start_status             text NOT NULL CHECK (start_status IN ('KNOWN','LEFT_CENSORED','UNKNOWN')),
    end_status               text NOT NULL CHECK (end_status IN ('KNOWN','LEFT_CENSORED','UNKNOWN')),
    left_censored_start      boolean NOT NULL DEFAULT false,
    left_censored_end        boolean NOT NULL DEFAULT false,
    period_status            text NOT NULL CHECK (period_status IN ('BOUNDED','OPEN_LEFT_CENSORED','CLOSED_ONLY')),
    opening_observation_key  text,
    closing_observation_key  text,
    source_family            text NOT NULL,

    UNIQUE (build_run_id, canonical_person_id, canonical_company_id, role_context, opening_observation_key),

    -- periods resolve to observations of the SAME build
    CONSTRAINT pp_opening_resolves FOREIGN KEY (build_run_id, opening_observation_key)
        REFERENCES registry_analytics.person_company_observation (build_run_id, source_observation_key),
    CONSTRAINT pp_closing_resolves FOREIGN KEY (build_run_id, closing_observation_key)
        REFERENCES registry_analytics.person_company_observation (build_run_id, source_observation_key),
    CONSTRAINT pcp_status_shape CHECK (
        (period_status = 'BOUNDED'             AND period_start IS NOT NULL AND period_end IS NOT NULL AND left_censored_start = false)
     OR (period_status = 'OPEN_LEFT_CENSORED'  AND period_start IS NOT NULL AND period_end IS NULL AND left_censored_end = true)
     OR (period_status = 'CLOSED_ONLY'         AND period_start IS NULL AND period_end IS NOT NULL AND left_censored_start = true))
);

CREATE INDEX IF NOT EXISTS pcp_build_person_idx ON registry_analytics.person_company_period (build_run_id, canonical_person_id);
CREATE INDEX IF NOT EXISTS pcp_build_company_ctx_idx ON registry_analytics.person_company_period (build_run_id, canonical_company_id, role_context);

-- Phase 2 build must reference the PASSED Phase 0 artifact (N-10 gate).
ALTER TABLE registry_analytics.analytics_build
    DROP CONSTRAINT IF EXISTS analytics_build_phase0_gate;
ALTER TABLE registry_analytics.analytics_build
    ADD CONSTRAINT analytics_build_phase0_gate CHECK (
        phase0_measurement_id IS NOT NULL AND phase0_measurement_id NOT IN ('', 'N/A')
        AND phase0_measured_at IS NOT NULL AND phase0_measured_at NOT IN ('', 'N/A'));

-- Phase 2 closure: the Phase 0 pin is enforced at APPLICATION level
-- (tools/phase2_build.py persist(): pin re-derived from artifact bytes and
-- format-validated BEFORE any DB write; a truncated/empty '#sha256:' can never
-- become ACTIVE). A DB regex-CHECK was evaluated and deliberately NOT added:
-- a NOT VALID CHECK is re-enforced on UPDATE and would freeze historical
-- SUPERSEDED rows, breaking the normal rebuild lifecycle (plan §15).