-- HISTORICAL (audit B-17, 2026-11-06): the canonical DB's schema is the ledger in
-- sql/migrations/ (python3 -m heimdal.canonical_migrations). Change it there, not here.
-- HEIMDAL — registry_analytics Phase 3 schema (plan v1.3.1 §5.4-5.7, §13-§15).
-- Pair co-participation + supported 2..N recurring groups. Checked-in DDL,
-- idempotent. Pairs/groups are DERIVED artifacts built only from
-- person_company_observation / person_company_period of the SAME build_run_id.

-- ============================ person_person_overlap ============================
-- ONE grain: (build_run_id, person_a_id, person_b_id, canonical_company_id,
-- role_context, co_participation_class). CHECK person_a_id < person_b_id.
-- No pair-global aggregates (plan §5.5: query-time only).
CREATE TABLE IF NOT EXISTS registry_analytics.person_person_overlap (
    pair_id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    build_run_id             bigint NOT NULL REFERENCES registry_analytics.analytics_build (build_run_id),
    person_a_id              bigint NOT NULL REFERENCES public.canonical_entity (canonical_entity_id),
    person_b_id              bigint NOT NULL REFERENCES public.canonical_entity (canonical_entity_id),
    canonical_company_id     bigint NOT NULL REFERENCES public.canonical_entity (canonical_entity_id),
    role_context             text NOT NULL CHECK (role_context IN ('BOARD','MANAGEMENT','OWNERSHIP','CONTROL_ACTOR')),
    co_participation_class   text NOT NULL CHECK (co_participation_class IN
                               ('SAME_COMPANY_EVER','SAME_OBSERVATION','SAME_SNAPSHOT',
                                'KNOWN_TEMPORAL_OVERLAP','UNKNOWN_TEMPORAL_RELATION')),
    observation_count        integer NOT NULL,
    episode_count            integer NOT NULL,
    known_overlap_days       integer,          -- NULL unless truth-table case 1 (bounded x bounded DAY)
    first_observed           date,
    last_observed            date,
    same_source_event_count  integer NOT NULL DEFAULT 0,
    same_snapshot_count      integer NOT NULL DEFAULT 0,
    -- M-01 closure: NULL source_family = pair fact supported by more than one
    -- source family; NULL authority_class = pair fact supported by more than one
    -- authority class (heterogeneous fallback evidence; metadata incompleteness-
    -- by-design, reconstructible from same-build observations/periods).
    source_family            text,
    authority_class          text,

    CONSTRAINT ppo_ordering CHECK (person_a_id < person_b_id),
    CONSTRAINT ppo_grain UNIQUE (build_run_id, person_a_id, person_b_id,
                                 canonical_company_id, role_context, co_participation_class),
    CONSTRAINT ppo_overlap_days_only_known CHECK (
        known_overlap_days IS NULL OR co_participation_class = 'KNOWN_TEMPORAL_OVERLAP'),
    CONSTRAINT ppo_point_semantics CHECK (
        co_participation_class NOT IN ('SAME_COMPANY_EVER','UNKNOWN_TEMPORAL_RELATION')
        OR known_overlap_days IS NULL),
    -- direct shared-source evidence always carries precise single-valued metadata;
    -- NULL metadata is reserved for heterogeneous fallback rows (M-01 closure)
    CONSTRAINT ppo_direct_metadata_present CHECK (
        co_participation_class NOT IN ('SAME_SNAPSHOT','SAME_OBSERVATION','KNOWN_TEMPORAL_OVERLAP')
        OR (source_family IS NOT NULL AND authority_class IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ppo_build_pair_idx ON registry_analytics.person_person_overlap (build_run_id, person_a_id, person_b_id);
CREATE INDEX IF NOT EXISTS ppo_build_company_idx ON registry_analytics.person_person_overlap (build_run_id, canonical_company_id, role_context);

-- ================================= person_group ================================
CREATE TABLE IF NOT EXISTS registry_analytics.person_group (
    group_id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    build_run_id             bigint NOT NULL REFERENCES registry_analytics.analytics_build (build_run_id),
    member_set_hash          text NOT NULL,
    role_context             text NOT NULL CHECK (role_context IN ('BOARD','MANAGEMENT','OWNERSHIP','CONTROL_ACTOR')),
    group_kind               text NOT NULL CHECK (group_kind IN ('RECURRING_GROUP','CORE_GROUP')),
    group_size               integer NOT NULL CHECK (group_size >= 2),
    support                  jsonb NOT NULL,
    first_observed           date,
    last_observed            date,

    CONSTRAINT pg_grain UNIQUE (build_run_id, member_set_hash, role_context, group_kind)
);

CREATE INDEX IF NOT EXISTS pg_build_hash_idx ON registry_analytics.person_group (build_run_id, member_set_hash);
CREATE INDEX IF NOT EXISTS pg_ctx_idx ON registry_analytics.person_group (role_context, group_kind);

-- ============================= person_group_member =============================
CREATE TABLE IF NOT EXISTS registry_analytics.person_group_member (
    group_id                 bigint NOT NULL REFERENCES registry_analytics.person_group (group_id) ON DELETE CASCADE,
    canonical_person_id      bigint NOT NULL REFERENCES public.canonical_entity (canonical_entity_id),
    CONSTRAINT pgm_unique UNIQUE (group_id, canonical_person_id)
);

CREATE INDEX IF NOT EXISTS pgm_person_idx ON registry_analytics.person_group_member (canonical_person_id);

-- =========================== person_group_observation ==========================
CREATE TABLE IF NOT EXISTS registry_analytics.person_group_observation (
    group_observation_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    build_run_id             bigint NOT NULL REFERENCES registry_analytics.analytics_build (build_run_id),
    group_id                 bigint NOT NULL REFERENCES registry_analytics.person_group (group_id) ON DELETE CASCADE,
    episode_key              text NOT NULL,
    canonical_company_id     bigint NOT NULL REFERENCES public.canonical_entity (canonical_entity_id),
    role_context             text NOT NULL CHECK (role_context IN ('BOARD','MANAGEMENT','OWNERSHIP','CONTROL_ACTOR')),
    episode_start            date,
    episode_end              date,
    source_family            text NOT NULL,
    co_observation_kind      text NOT NULL CHECK (co_observation_kind IN
                               ('SAME_EVENT','SAME_SNAPSHOT','SAFE_PERIOD_OVERLAP')),

    CONSTRAINT pgo_unique UNIQUE (group_id, episode_key, canonical_company_id, role_context)
);

CREATE INDEX IF NOT EXISTS pgo_group_idx ON registry_analytics.person_group_observation (group_id, canonical_company_id, role_context);
CREATE INDEX IF NOT EXISTS pgo_episode_idx ON registry_analytics.person_group_observation (group_id, episode_key);

-- ======================= person_group_observation_member =======================
-- Junction-FK provenance (plan §5.6 / N-14): no text[] pseudo-FK.
CREATE TABLE IF NOT EXISTS registry_analytics.person_group_observation_member (
    group_observation_id     bigint NOT NULL REFERENCES registry_analytics.person_group_observation (group_observation_id) ON DELETE CASCADE,
    observation_id           bigint NOT NULL REFERENCES registry_analytics.person_company_observation (observation_id) ON DELETE CASCADE,
    role_in_episode          text,   -- 'MEMBER' | 'PERIOD_OPENING' | 'PERIOD_CLOSING' (SAFE_PERIOD_OVERLAP proof)
    CONSTRAINT pgom_unique UNIQUE (group_observation_id, observation_id)
);

CREATE INDEX IF NOT EXISTS pgom_observation_idx ON registry_analytics.person_group_observation_member (observation_id);