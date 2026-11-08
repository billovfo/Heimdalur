-- HEIMDAL PERSON HUMAN-REVIEW persistence v1 - additive review-layer migration.
-- Conventions follow heimdal/pdfhist/projection_migration.sql: plain SQL,
-- idempotent (IF NOT EXISTS), new tables only, no stored procedures,
-- no semicolons inside string/comment text, no mutation of pre-existing tables.
-- Runs in the PDF-history database (pdfhist_pilot). DEV/LOCAL ONLY.

-- 0) Schema (safe if projection migration already created it).
CREATE SCHEMA IF NOT EXISTS registry_projection;

-- 1) Review case: one row per stable source identity (REVIEW-CASE-KEY-v1).
-- Sealed packet versions are appended to packet_versions, never mutated.
CREATE TABLE IF NOT EXISTS registry_projection.person_review_case (
    case_key                 text PRIMARY KEY,
    source_kind              text NOT NULL
                             CHECK (source_kind IN ('PDF-HIST', 'PDF-CURRENT')),
    norm_name                text NOT NULL,
    source_name_exact        text NOT NULL,
    company_anchor           text,
    role_type                text,
    source_context           text,
    bucket                   text,
    occurrence_refs          jsonb NOT NULL DEFAULT '[]'::jsonb,
    packet_versions          jsonb NOT NULL DEFAULT '[]'::jsonb,
    current_packet_version   integer NOT NULL DEFAULT 0,
    status                   text NOT NULL DEFAULT 'QUEUED'
                             CHECK (status IN
                             ('QUEUED','IN_REVIEW','APPROVED','REJECTED',
                              'AMBIGUOUS','DEFERRED','STALE','APPLIED')),
    queue_rank               double precision NOT NULL DEFAULT 0,
    h_class_estimate         text,
    candidate_count          integer NOT NULL DEFAULT 0,
    superseded_by            text,
    status_updated_at        timestamptz NOT NULL DEFAULT now(),
    created_at               timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS person_review_case_status_rank_idx
    ON registry_projection.person_review_case (status, queue_rank);
CREATE INDEX IF NOT EXISTS person_review_case_bucket_idx
    ON registry_projection.person_review_case (bucket);
CREATE INDEX IF NOT EXISTS person_review_case_norm_idx
    ON registry_projection.person_review_case (norm_name);

-- 2) Review decision: append-only record, immutable payload columns,
-- controlled-mutable lifecycle columns (named functions only, see repository).
CREATE TABLE IF NOT EXISTS registry_projection.person_review_decision (
    decision_id              bigserial PRIMARY KEY,
    case_key                 text NOT NULL
                             REFERENCES registry_projection.person_review_case(case_key),
    reviewer                 text NOT NULL,
    decided_at               timestamptz NOT NULL DEFAULT now(),
    decision                 text NOT NULL
                             CHECK (decision IN
                             ('APPROVE_LINK','REJECT','AMBIGUOUS','DEFER')),
    chosen_canonical_id      bigint,
    owner_note               text,
    reason_code              text,
    shown_packet_hash        text NOT NULL,
    shown_alternatives       jsonb NOT NULL DEFAULT '[]'::jsonb,
    shown_score_snapshot_ref text NOT NULL,
    evidence_pin             text NOT NULL,
    supersedes_decision_id   bigint
                             REFERENCES registry_projection.person_review_decision(decision_id),
    applied_projection_run_id bigint,
    stale_reason             text,
    stale_at                 timestamptz,
    application_status       text NOT NULL DEFAULT 'PENDING'
                             CHECK (application_status IN
                             ('PENDING','FROZEN','APPLIED','STALE','CONFLICT')),
    frozen_batch_id          text,
    created_at               timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS person_review_decision_case_idx
    ON registry_projection.person_review_decision (case_key, decided_at);
CREATE INDEX IF NOT EXISTS person_review_decision_pending_idx
    ON registry_projection.person_review_decision (decision, application_status)
    WHERE decision = 'APPROVE_LINK' AND application_status = 'PENDING';

-- 3) Human-review link method (documented P1 deviation, additive-compatible).
-- Rationale: entity_link.link_method CHECK enumerates only automatic methods,
-- so a real HUMAN-REVIEW-PERSON-v1 application is a hard repository blocker
-- (proven by constraint inspection + P4 fixture tests). This idempotent block
-- extends the CHECK value set without touching rows, columns, or other
-- constraints. No data migration, no downtime-relevant lock beyond catalog.
ALTER TABLE registry_projection.entity_link
    DROP CONSTRAINT IF EXISTS entity_link_link_method_check;
ALTER TABLE registry_projection.entity_link
    ADD CONSTRAINT entity_link_link_method_check
    CHECK (link_method IN
           ('REGISTRATION_NUMBER_EXACT','EXACT_MULTI_ATTRIBUTE',
            'HUMAN-REVIEW-PERSON-v1'));

-- 4) Post-MVP QA marks (E4): controlled lifecycle columns only, no new table.
-- qa_status is lifecycle metadata (UNREVIEWED default); owner decision payload
-- columns above are untouched. All statements idempotent, dev/local only.
ALTER TABLE registry_projection.person_review_decision
    ADD COLUMN IF NOT EXISTS qa_status text NOT NULL DEFAULT 'UNREVIEWED';
ALTER TABLE registry_projection.person_review_decision
    ADD COLUMN IF NOT EXISTS qa_reviewer text;
ALTER TABLE registry_projection.person_review_decision
    ADD COLUMN IF NOT EXISTS qa_note text;
ALTER TABLE registry_projection.person_review_decision
    ADD COLUMN IF NOT EXISTS qa_decided_at timestamptz;
ALTER TABLE registry_projection.person_review_decision
    DROP CONSTRAINT IF EXISTS person_review_decision_qa_check;
ALTER TABLE registry_projection.person_review_decision
    ADD CONSTRAINT person_review_decision_qa_check
    CHECK (qa_status IN ('UNREVIEWED','QA_OK','QA_NEEDS_REVIEW'));
