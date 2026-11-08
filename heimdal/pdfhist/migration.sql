-- HEIMDAL PDF-HISTORIK v3.1.2 - migration (7 persistent tables + 2 derived views).
-- Normative source: docs/HEIMDAL-PDF-HISTORIK-MASTERPLAN-v3.1.2.md section 4/11/13.
-- CREATE ORDER IS BINDING (v3.1.2 patch 1): every FK target is created BEFORE the
-- table referencing it. Idempotent (IF NOT EXISTS / CREATE OR REPLACE) matching the
-- repo-wide schema.sql convention. Touches NO existing Heimdal state: no
-- company_location, no inventory, no collection_run. source_system identity is
-- 'sg.vs.fo' (the source system, never the HTTP host name).

-- 1) Run log (incl. fetch outcome + review signals; retries are local to the
--    fetch operation and only final outcomes are persisted).
CREATE TABLE IF NOT EXISTS pdf_run_log (
    id           bigserial PRIMARY KEY,
    run_id       bigint NOT NULL,
    company_id   bigint,
    document_id  bigint,
    step         text NOT NULL,
    status       text NOT NULL,
    error_type   text,
    detail       text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pdf_run_log_step_check CHECK (step IN
        ('SEARCH','PAGINATION','EVENT','FETCH','PARSE','REVIEW','ACCOUNT')),
    CONSTRAINT pdf_run_log_status_check CHECK (status IN
        ('OK','SKIPPED','WARN','REVIEW','STOPPED','FAILED'))
);
CREATE INDEX IF NOT EXISTS pdf_run_log_run_idx ON pdf_run_log (run_id);
CREATE INDEX IF NOT EXISTS pdf_run_log_doc_idx ON pdf_run_log (document_id);
CREATE INDEX IF NOT EXISTS pdf_run_log_status_idx ON pdf_run_log (status);

-- 2) Verified source event rows (exact skrasnr-validated).
CREATE TABLE IF NOT EXISTS pdf_source_event (
    id                bigserial PRIMARY KEY,
    company_id        bigint NOT NULL,
    requested_regnr   text NOT NULL,
    row_skrasnr       text NOT NULL,
    source_row_key    text NOT NULL UNIQUE,
    source_event_date date,
    raw_text          text NOT NULL,
    page_no           integer NOT NULL CHECK (page_no >= 0),
    row_ordinal       integer NOT NULL CHECK (row_ordinal >= 1),
    discovered_at     timestamptz NOT NULL DEFAULT now()
);

-- 3) Logical source reference: identifies the source reference, NOT one byte set.
CREATE TABLE IF NOT EXISTS pdf_document (
    id                 bigserial PRIMARY KEY,
    source_system      text NOT NULL DEFAULT 'sg.vs.fo',
    pdf_type           text NOT NULL,
    source_document_id text NOT NULL,
    pdf_kind_hint      text CHECK (pdf_kind_hint IN
                           ('FULL_STATE_SNAPSHOT','REGISTRATION_NOTICE')),
    fetch_policy       text NOT NULL DEFAULT 'RECHECK_ON_ENCOUNTER'
                       CHECK (fetch_policy IN ('RECHECK_ON_ENCOUNTER')),
    UNIQUE (source_system, pdf_type, source_document_id)
);

-- 4) Content store, deduplicated on exact bytes. MUST exist before the
--    observation table (patch 1 FK order).
CREATE TABLE IF NOT EXISTS pdf_document_content (
    sha256      text PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    byte_length integer NOT NULL,
    content     bytea NOT NULL
);

-- 5) Observation: one concrete fetch of one logical document.
--    UNIQUE(document_id, collection_run_id) - at most one fetch-observation per
--    document per collection run. fetched_at is the observation timestamp
--    (provenance), never its identity. FK on sha256 (v3.1.1 patch 1): insert
--    order is ALWAYS content first, then observation. A new hash creates a new
--    observation and never overwrites an old one.
CREATE TABLE IF NOT EXISTS pdf_document_observation (
    id                      bigserial PRIMARY KEY,
    document_id             bigint NOT NULL REFERENCES pdf_document(id),
    collection_run_id       bigint NOT NULL,
    fetched_at              timestamptz NOT NULL,
    document_snapshot_date  date,
    sha256                  text NOT NULL
                            REFERENCES pdf_document_content(sha256),
    byte_length             integer NOT NULL,
    extracted_text          text,
    UNIQUE (document_id, collection_run_id)
);
CREATE INDEX IF NOT EXISTS pdf_document_observation_doc_idx
    ON pdf_document_observation (document_id);

-- 6) Row-to-document N:M junction (link is written first, regardless of fetch).
CREATE TABLE IF NOT EXISTS pdf_source_event_document (
    source_event_id bigint NOT NULL REFERENCES pdf_source_event(id),
    document_id     bigint NOT NULL REFERENCES pdf_document(id),
    cell_role       text NOT NULL
                    CHECK (cell_role IN ('FELAG_CELL','LYSING_CELL','UNCLASSIFIED_CELL')),
    PRIMARY KEY (source_event_id, document_id)
);
CREATE INDEX IF NOT EXISTS pdf_source_event_document_doc_idx
    ON pdf_source_event_document (document_id);

-- 7) Parse result, always scoped to one observation and one parser_version.
--    A reparse with a new version is a NEW row; old parses are never updated.
CREATE TABLE IF NOT EXISTS pdf_parse_result (
    id                      bigserial PRIMARY KEY,
    document_observation_id bigint NOT NULL REFERENCES pdf_document_observation(id),
    parser_version          text NOT NULL DEFAULT '1',
    document_kind           text CHECK (document_kind IN
                                ('COMPANY_SNAPSHOT','REGISTRATION_NOTICE',
                                 'INVOICE','OTHER')),
    raw_text                text NOT NULL,
    structured_data         jsonb NOT NULL,
    parse_status            text NOT NULL
                            CHECK (parse_status IN ('PARSED','REVIEW','FAILED')),
    needs_review            boolean NOT NULL DEFAULT false,
    review_reason           text,
    parsed_at               timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_observation_id, parser_version)
);
CREATE INDEX IF NOT EXISTS pdf_parse_result_obs_idx
    ON pdf_parse_result (document_observation_id);

-- Derived view A: company_pdf_snapshot_history (v3.1.2 patches 3+4).
-- DISTINCT before the window (the N:M join must not duplicate one observation),
-- partitioned by (company_id, parser_version) so parser versions never mix, and
-- a fail-closed verification filter: only relations whose parsed registration
-- number matches the source row skrasnr enter the timeline. Mismatched or
-- missing registration numbers are retained as evidence but excluded here and
-- surfaced by pdf_review_queue instead.
CREATE OR REPLACE VIEW company_pdf_snapshot_history AS
WITH verified_company_observation AS (
    SELECT DISTINCT
           e.company_id,
           p.parser_version,
           o.id             AS document_observation_id,
           o.document_snapshot_date,
           o.sha256,
           o.fetched_at,
           p.structured_data
    FROM pdf_parse_result p
    JOIN pdf_document_observation o ON o.id = p.document_observation_id
    JOIN pdf_document d             ON d.id = o.document_id
    JOIN pdf_source_event_document sed ON sed.document_id = d.id
    JOIN pdf_source_event e         ON e.id = sed.source_event_id
    WHERE d.pdf_type = 'FelagPdf'
      AND p.parse_status = 'PARSED'
      AND o.document_snapshot_date IS NOT NULL
      AND lower(btrim(p.structured_data ->> 'registration_number'))
        = lower(btrim(e.row_skrasnr))
)
SELECT company_id,
       parser_version,
       document_observation_id,
       document_snapshot_date AS valid_from,
       LEAD(document_snapshot_date) OVER (
           PARTITION BY company_id, parser_version
           ORDER BY document_snapshot_date, document_observation_id
       ) AS valid_to,
       sha256,
       fetched_at,
       structured_data,
       EXISTS (
           SELECT 1
           FROM verified_company_observation v2
           WHERE v2.company_id = verified_company_observation.company_id
             AND v2.parser_version = verified_company_observation.parser_version
             AND v2.document_snapshot_date = verified_company_observation.document_snapshot_date
             AND v2.sha256 <> verified_company_observation.sha256
       ) AS snapshot_date_conflict
FROM verified_company_observation;

-- Derived view B: pdf_review_queue (simple UNION ALL operator list - no workflow,
-- no approvals, no review table). Warnings cannot be lost: parse reviews, failed
-- fetches, verification mismatches, snapshot conflicts, name mismatches and run
-- warnings all land here. The two verification-mismatch branches recompute the
-- relation-level gate live from parsed data, so mismatched evidence stays visible
-- even though it is excluded from the derived history views.
CREATE OR REPLACE VIEW pdf_review_queue AS
SELECT DISTINCT
       'PARSE_REVIEW'             AS issue,
       p.id                       AS parse_result_id,
       o.id                       AS document_observation_id,
       d.id                       AS document_id,
       e.company_id               AS company_id,
       e.id                       AS source_event_id,
       p.review_reason            AS detail
FROM pdf_parse_result p
JOIN pdf_document_observation o ON o.id = p.document_observation_id
JOIN pdf_document d             ON d.id = o.document_id
LEFT JOIN pdf_source_event_document sed ON sed.document_id = d.id
LEFT JOIN pdf_source_event e            ON e.id = sed.source_event_id
WHERE p.needs_review
UNION ALL
SELECT 'FETCH_FAILED', NULL, NULL, l.document_id, l.company_id, NULL, l.error_type
FROM pdf_run_log l
WHERE l.step = 'FETCH' AND l.status = 'FAILED'
UNION ALL
SELECT DISTINCT
       'PDF_COMPANY_MISMATCH', p.id, o.id, d.id, e.company_id, e.id,
       'parsed regnr ' || coalesce(btrim(p.structured_data ->> 'registration_number'), '?')
       || ' != source row skrasnr ' || e.row_skrasnr
FROM pdf_parse_result p
JOIN pdf_document_observation o ON o.id = p.document_observation_id
JOIN pdf_document d             ON d.id = o.document_id
JOIN pdf_source_event_document sed ON sed.document_id = d.id
JOIN pdf_source_event e         ON e.id = sed.source_event_id
WHERE btrim(coalesce(p.structured_data ->> 'registration_number','')) <> ''
  AND lower(btrim(p.structured_data ->> 'registration_number'))
    <> lower(btrim(e.row_skrasnr))
UNION ALL
SELECT DISTINCT
       'PDF_COMPANY_MISMATCH_MISSING_REGNR', p.id, o.id, d.id, e.company_id, e.id,
       'parsed registration_number missing'
FROM pdf_parse_result p
JOIN pdf_document_observation o ON o.id = p.document_observation_id
JOIN pdf_document d             ON d.id = o.document_id
JOIN pdf_source_event_document sed ON sed.document_id = d.id
JOIN pdf_source_event e         ON e.id = sed.source_event_id
WHERE btrim(coalesce(p.structured_data ->> 'registration_number','')) = ''
UNION ALL
SELECT 'SOURCE_NAME_MISMATCH', NULL, NULL, NULL, l.company_id, NULL, l.error_type
FROM pdf_run_log l
WHERE l.error_type = 'SOURCE_NAME_MISMATCH'
UNION ALL
SELECT 'SNAPSHOT_DATE_CONFLICT', NULL, h.document_observation_id, NULL,
       h.company_id, NULL, 'same snapshot_date with different sha256'
FROM company_pdf_snapshot_history h
WHERE h.snapshot_date_conflict
UNION ALL
SELECT 'RUN_WARNING', NULL, NULL, l.document_id, l.company_id, NULL, l.error_type
FROM pdf_run_log l
WHERE l.status IN ('WARN','STOPPED','REVIEW')
  AND l.error_type IS DISTINCT FROM 'SOURCE_NAME_MISMATCH';
