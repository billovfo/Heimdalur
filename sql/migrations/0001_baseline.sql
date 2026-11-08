-- 0001 baseline: the canonical (heimdal) DB schema after the 2026-09-01 cutover,
-- exactly as recovered in backups/recovered-20260904/heimdal-qa_heimdal.dump
-- (sha256 0a08b59b15ecc1a35d6e43dc7fb5a45a23f499300cbdbfae5de2f0b48562bd87).
-- Generated 2026-11-06 with `pg_dump -s --no-owner --no-acl` from a fresh restore;
-- psql meta-commands and session SETs removed. Every name is schema-qualified.
-- Ledger: heimdal/canonical_migrations.py. A DB restored from that dump (or any
-- copy of it) adopts this entry without running it; an empty DB runs it.
-- Grants are not here: 0007 owns heimdal_reader's. Never edit (checksummed).

--
-- PostgreSQL database dump
--

-- Dumped from database version 18.4
-- Dumped by pg_dump version 18.4

--
-- Name: registry_analytics; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA registry_analytics;

--
-- Name: registry_ownership; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA registry_ownership;

--
-- Name: canonical_entity; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.canonical_entity (
    canonical_entity_id bigint NOT NULL,
    source_system text NOT NULL,
    source_entity_id bigint NOT NULL,
    entity_kind text NOT NULL,
    entity_kind_confidence text NOT NULL,
    raw_type_token text,
    display_name text NOT NULL,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT canonical_entity_entity_kind_check CHECK ((entity_kind = ANY (ARRAY['PERSON'::text, 'COMPANY'::text, 'ORGANIZATION'::text, 'PUBLIC_AUTHORITY'::text, 'UNKNOWN_ENTITY'::text]))),
    CONSTRAINT canonical_entity_entity_kind_confidence_check CHECK ((entity_kind_confidence = 'INFERRED'::text))
);

--
-- Name: canonical_entity_canonical_entity_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.canonical_entity ALTER COLUMN canonical_entity_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.canonical_entity_canonical_entity_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: canonical_relation_legacy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.canonical_relation_legacy (
    relation_id bigint NOT NULL,
    raw_from_source_entity_id bigint NOT NULL,
    raw_from_source_entity_label text NOT NULL,
    raw_to_source_entity_id bigint NOT NULL,
    raw_to_source_entity_label text NOT NULL,
    raw_source_relation_type_id integer NOT NULL,
    raw_source_relation_label text NOT NULL,
    canonical_relation_type text NOT NULL,
    canonical_subject_entity_id bigint,
    canonical_object_entity_id bigint,
    semantic_direction_confidence text CONSTRAINT canonical_relation_legacy_semantic_direction_confidenc_not_null NOT NULL,
    raw_percentage_label text,
    parsed_percentage numeric(6,3),
    percentage_parse_status text,
    source_attempt_id bigint NOT NULL,
    first_stored_at timestamp with time zone DEFAULT now() NOT NULL,
    inferred_group text,
    inferred_group_confidence text,
    canonical_role_label text,
    semantic_status text,
    CONSTRAINT canonical_relation_canonical_relation_type_check CHECK ((canonical_relation_type = ANY (ARRAY['DIRECT_OWNERSHIP'::text, 'REGISTERED_CONTROL_ACTOR'::text, 'OTHER_SOURCE_RELATION'::text]))),
    CONSTRAINT canonical_relation_inferred_group_check CHECK ((inferred_group = ANY (ARRAY['EIGARAR'::text, 'NEVND'::text, 'STJORN'::text, 'ANNAD'::text]))),
    CONSTRAINT canonical_relation_inferred_group_confidence_check CHECK ((inferred_group_confidence = 'INFERRED'::text)),
    CONSTRAINT canonical_relation_percentage_invariant CHECK ((((canonical_relation_type = 'DIRECT_OWNERSHIP'::text) = (raw_percentage_label IS NOT NULL)) AND ((percentage_parse_status = 'PARSED'::text) = (parsed_percentage IS NOT NULL)))),
    CONSTRAINT canonical_relation_percentage_parse_status_check CHECK ((percentage_parse_status = ANY (ARRAY['PARSED'::text, 'FAILED'::text]))),
    CONSTRAINT canonical_relation_semantic_direction_confidence_check CHECK ((semantic_direction_confidence = ANY (ARRAY['OBSERVED_AND_OWNER_ACCEPTED'::text, 'OBSERVED_FOR_SUPPLIED_EXAMPLES'::text, 'UNCONFIRMED'::text]))),
    CONSTRAINT canonical_relation_semantic_status_check CHECK ((semantic_status = 'UNRESOLVED_SOURCE_ANOMALY'::text))
);

--
-- Name: TABLE canonical_relation_legacy; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.canonical_relation_legacy IS 'LEGACY_READ_ONLY - pre-Phase-1 ownership source table; writer target is now registry_ownership.relation; rollback/reconciliation use only';

--
-- Name: canonical_relation_relation_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.canonical_relation_legacy ALTER COLUMN relation_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.canonical_relation_relation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: collection_run; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collection_run (
    collection_run_id bigint NOT NULL,
    run_type text NOT NULL,
    run_status text DEFAULT 'RUNNING'::text NOT NULL,
    stop_reason text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    ended_at timestamp with time zone,
    collector_version text NOT NULL,
    config jsonb NOT NULL,
    permission_status text NOT NULL,
    permission_scope text NOT NULL,
    permission_granted_by text DEFAULT 'NOT_RECORDED'::text NOT NULL,
    permission_granted_at text DEFAULT 'NOT_RECORDED'::text NOT NULL,
    permission_channel text DEFAULT 'NOT_RECORDED'::text NOT NULL,
    owner_attestation_recorded_at date NOT NULL,
    source_owner_contact_required_before_collection boolean CONSTRAINT collection_run_source_owner_contact_required_before_co_not_null NOT NULL,
    source_support_availability text NOT NULL,
    CONSTRAINT collection_run_run_status_check CHECK ((run_status = ANY (ARRAY['RUNNING'::text, 'COMPLETED'::text, 'STOPPED'::text]))),
    CONSTRAINT collection_run_run_type_check CHECK ((run_type = ANY (ARRAY['INVENTORY'::text, 'ACQUISITION'::text, 'TYPE_VALIDATION'::text, 'RELATION_PROCESSING'::text, 'INDIRECT_OWNERSHIP'::text, 'ROLE_NORMALIZATION'::text, 'RELATION_CONTEXTS'::text, 'CANONICAL_ENTITY_ASSIGNMENT'::text, 'PDF_LOCATION_ENRICHMENT'::text])))
);

--
-- Name: collection_run_collection_run_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.collection_run ALTER COLUMN collection_run_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.collection_run_collection_run_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: company_inventory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.company_inventory (
    source_system text NOT NULL,
    source_entity_id bigint NOT NULL,
    registration_number text,
    raw_display_name text NOT NULL,
    normalized_search_name text NOT NULL,
    raw_entity_type_text text,
    parsed_entity_type text,
    typing_status text NOT NULL,
    discovery_method text NOT NULL,
    first_discovered_at timestamp with time zone DEFAULT now() NOT NULL,
    last_confirmed_at timestamp with time zone DEFAULT now() NOT NULL,
    inventory_run_id bigint NOT NULL,
    raw_response_reference character(64) NOT NULL,
    inventory_status text DEFAULT 'DISCOVERED'::text NOT NULL,
    CONSTRAINT company_inventory_inventory_status_check CHECK ((inventory_status = ANY (ARRAY['DISCOVERED'::text, 'QUEUED'::text, 'FETCHED'::text, 'NOT_FOUND'::text, 'RETRYABLE_ERROR'::text, 'TERMINAL_ERROR'::text]))),
    CONSTRAINT company_inventory_typing_status_check CHECK ((typing_status = ANY (ARRAY['PARSED'::text, 'UNKNOWN_ENTITY'::text, 'CONFLICT'::text])))
);

--
-- Name: company_location; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.company_location (
    source_system text NOT NULL,
    source_entity_id bigint NOT NULL,
    postal_code text,
    country_code text,
    location_status text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT company_location_check CHECK ((((location_status = 'FAROESE_CITY'::text) AND (postal_code IS NOT NULL) AND (country_code = 'FO'::text)) OR ((location_status = 'FOREIGN'::text) AND (postal_code IS NULL) AND (country_code IS NOT NULL)) OR ((location_status = 'UNKNOWN'::text) AND (postal_code IS NULL) AND (country_code IS NULL)))),
    CONSTRAINT company_location_country_code_check CHECK ((country_code ~ '^[A-Z]{2}$'::text)),
    CONSTRAINT company_location_location_status_check CHECK ((location_status = ANY (ARRAY['FAROESE_CITY'::text, 'FOREIGN'::text, 'UNKNOWN'::text])))
);

--
-- Name: company_relation_context_legacy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.company_relation_context_legacy (
    context_id bigint NOT NULL,
    company_a_entity_id bigint NOT NULL,
    company_b_entity_id bigint NOT NULL,
    relation_basis text NOT NULL,
    related_entity_id bigint,
    company_a_relation text,
    company_b_relation text,
    source_relation_id bigint,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT company_relation_context_check CHECK ((company_a_entity_id <> company_b_entity_id)),
    CONSTRAINT company_relation_context_relation_basis_check CHECK ((relation_basis = ANY (ARRAY['DIRECT_OWNERSHIP'::text, 'INDIRECT_OWNERSHIP'::text, 'SHARED_OWNER'::text, 'SHARED_BENEFICIAL_OWNER'::text, 'SHARED_BOARD_PERSON'::text, 'SHARED_MANAGEMENT_PERSON'::text, 'CROSS_ROLE_PERSON'::text, 'SHARED_OTHER_RELATION'::text, 'DIRECT_CONTROL_ACTOR'::text])))
);

--
-- Name: TABLE company_relation_context_legacy; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.company_relation_context_legacy IS 'LEGACY_READ_ONLY - pre-Phase-1 ownership source table; writer target is now registry_ownership.company_relation_context; rollback/reconciliation use only';

--
-- Name: company_relation_context_context_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.company_relation_context_legacy ALTER COLUMN context_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.company_relation_context_context_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: entity_change_event; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entity_change_event (
    event_id bigint NOT NULL,
    canonical_entity_id bigint NOT NULL,
    event_timestamp timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    change_category text NOT NULL,
    field_name text NOT NULL,
    old_value text,
    new_value text,
    related_entity_id bigint,
    related_entity_name text,
    change_summary text NOT NULL,
    CONSTRAINT entity_change_event_change_category_check CHECK ((change_category = ANY (ARRAY['OWNERSHIP_ADDED'::text, 'OWNERSHIP_REMOVED'::text, 'OWNERSHIP_PERCENTAGE_CHANGED'::text, 'ROLE_APPOINTED'::text, 'ROLE_RESIGNED'::text, 'CONTROL_ACTOR_CHANGED'::text, 'STATUS_CHANGED'::text])))
);

--
-- Name: entity_change_event_event_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.entity_change_event ALTER COLUMN event_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.entity_change_event_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: entity_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entity_status (
    entity_status_id bigint NOT NULL,
    source_entity_id bigint NOT NULL,
    canonical_status_type text NOT NULL,
    raw_source_label text NOT NULL,
    source_attempt_id bigint NOT NULL,
    first_stored_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT entity_status_canonical_status_type_check CHECK ((canonical_status_type = 'NO_REGISTERED_CONTROL_ACTOR'::text))
);

--
-- Name: entity_status_entity_status_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.entity_status ALTER COLUMN entity_status_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.entity_status_entity_status_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: enumeration_key; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.enumeration_key (
    search_key text NOT NULL,
    key_number integer NOT NULL,
    status text DEFAULT 'PENDING'::text NOT NULL,
    row_count integer,
    attempt_id bigint,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT enumeration_key_status_check CHECK ((status = ANY (ARRAY['PENDING'::text, 'HIT'::text, 'MISS'::text, 'ERROR'::text])))
);

--
-- Name: geo_postcode; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.geo_postcode (
    postal_code text NOT NULL,
    city text NOT NULL,
    region text NOT NULL,
    latitude numeric(8,5) NOT NULL,
    longitude numeric(8,5) NOT NULL,
    CONSTRAINT geo_postcode_city_check CHECK ((city <> ''::text)),
    CONSTRAINT geo_postcode_latitude_check CHECK (((latitude > (61)::numeric) AND (latitude < (63)::numeric))),
    CONSTRAINT geo_postcode_longitude_check CHECK (((longitude > ('-8'::integer)::numeric) AND (longitude < ('-6'::integer)::numeric))),
    CONSTRAINT geo_postcode_postal_code_check CHECK ((postal_code ~ '^[0-9]{3}$'::text)),
    CONSTRAINT geo_postcode_region_check CHECK ((region = ANY (ARRAY['SUDUROY'::text, 'SANDOY'::text, 'SUDURSTREYMOY'::text, 'NORDURSTREYMOY'::text, 'VAGAR'::text, 'EYSTUROY'::text, 'NORDOYGGJAR'::text])))
);

--
-- Name: indirect_ownership_legacy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.indirect_ownership_legacy (
    indirect_ownership_id bigint NOT NULL,
    owner_entity_id bigint NOT NULL,
    owned_entity_id bigint NOT NULL,
    computed_percentage numeric(9,4) NOT NULL,
    path_count integer NOT NULL,
    contributing_paths jsonb NOT NULL,
    paths_truncated boolean DEFAULT false NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: TABLE indirect_ownership_legacy; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.indirect_ownership_legacy IS 'LEGACY_READ_ONLY - pre-Phase-1 ownership source table; writer target is now registry_ownership.indirect_ownership; rollback/reconciliation use only';

--
-- Name: indirect_ownership_indirect_ownership_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.indirect_ownership_legacy ALTER COLUMN indirect_ownership_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.indirect_ownership_indirect_ownership_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: input_parse_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.input_parse_status (
    response_sha256 character(64) NOT NULL,
    parse_status text NOT NULL,
    failure_reason text,
    row_count integer,
    company_count integer NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT input_parse_status_failure_reason_check CHECK ((failure_reason = ANY (ARRAY['NON_JSON'::text, 'EMPTY_BODY'::text, 'NOT_ARRAY'::text, 'BAD_ROW_KEYS'::text]))),
    CONSTRAINT input_parse_status_parse_status_check CHECK ((parse_status = ANY (ARRAY['OK'::text, 'FAILED'::text])))
);

--
-- Name: ownership_cycle_member_legacy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ownership_cycle_member_legacy (
    cycle_group_id integer NOT NULL,
    entity_id bigint NOT NULL
);

--
-- Name: TABLE ownership_cycle_member_legacy; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.ownership_cycle_member_legacy IS 'LEGACY_READ_ONLY - pre-Phase-1 ownership source table; writer target is now registry_ownership.ownership_cycle_member; rollback/reconciliation use only';

--
-- Name: pep_record; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pep_record (
    pep_id integer NOT NULL,
    person_name text NOT NULL,
    office_title text NOT NULL,
    pep_category text NOT NULL,
    period_start text,
    period_end text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: pep_record_pep_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pep_record_pep_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: pep_record_pep_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pep_record_pep_id_seq OWNED BY public.pep_record.pep_id;

--
-- Name: person_relation_context_legacy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.person_relation_context_legacy (
    context_id bigint NOT NULL,
    person_a_entity_id bigint NOT NULL,
    person_b_entity_id bigint NOT NULL,
    company_entity_id bigint NOT NULL,
    person_a_relation_id bigint NOT NULL,
    person_a_group text,
    person_a_role_label text,
    person_b_relation_id bigint NOT NULL,
    person_b_group text,
    person_b_role_label text,
    is_cross_role boolean NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT person_relation_context_check CHECK ((person_a_entity_id < person_b_entity_id))
);

--
-- Name: TABLE person_relation_context_legacy; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.person_relation_context_legacy IS 'LEGACY_READ_ONLY - pre-Phase-1 ownership source table; writer target is now registry_ownership.person_relation_context; rollback/reconciliation use only';

--
-- Name: person_relation_context_context_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.person_relation_context_legacy ALTER COLUMN context_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.person_relation_context_context_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: request_attempt; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.request_attempt (
    attempt_id bigint NOT NULL,
    collection_run_id bigint NOT NULL,
    endpoint text NOT NULL,
    parameters jsonb NOT NULL,
    requested_source_entity_id bigint,
    requested_at timestamp with time zone NOT NULL,
    response_received_at timestamp with time zone,
    http_status integer,
    error_category text NOT NULL,
    retry_number smallint DEFAULT 0 NOT NULL,
    collector_version text NOT NULL,
    response_sha256 character(64),
    CONSTRAINT request_attempt_retry_number_check CHECK (((retry_number >= 0) AND (retry_number <= 1)))
);

--
-- Name: request_attempt_attempt_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.request_attempt ALTER COLUMN attempt_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.request_attempt_attempt_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: response_content; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.response_content (
    response_sha256 character(64) NOT NULL,
    response_bytes bytea NOT NULL,
    content_type text,
    byte_length integer GENERATED ALWAYS AS (octet_length(response_bytes)) STORED,
    first_stored_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: user_watchlist; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_watchlist (
    id integer NOT NULL,
    canonical_entity_id bigint NOT NULL,
    entity_kind text NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: user_watchlist_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_watchlist_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: user_watchlist_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_watchlist_id_seq OWNED BY public.user_watchlist.id;

--
-- Name: relation; Type: TABLE; Schema: registry_ownership; Owner: -
--

CREATE TABLE registry_ownership.relation (
    relation_id bigint NOT NULL,
    raw_from_source_entity_id bigint NOT NULL,
    raw_from_source_entity_label text NOT NULL,
    raw_to_source_entity_id bigint NOT NULL,
    raw_to_source_entity_label text NOT NULL,
    raw_source_relation_type_id integer NOT NULL,
    raw_source_relation_label text NOT NULL,
    canonical_relation_type text NOT NULL,
    canonical_subject_entity_id bigint,
    canonical_object_entity_id bigint,
    semantic_direction_confidence text NOT NULL,
    raw_percentage_label text,
    parsed_percentage numeric(6,3),
    percentage_parse_status text,
    source_attempt_id bigint NOT NULL,
    first_stored_at timestamp with time zone DEFAULT now() NOT NULL,
    inferred_group text,
    inferred_group_confidence text,
    canonical_role_label text,
    semantic_status text,
    CONSTRAINT canonical_relation_canonical_relation_type_check CHECK ((canonical_relation_type = ANY (ARRAY['DIRECT_OWNERSHIP'::text, 'REGISTERED_CONTROL_ACTOR'::text, 'OTHER_SOURCE_RELATION'::text]))),
    CONSTRAINT canonical_relation_inferred_group_check CHECK ((inferred_group = ANY (ARRAY['EIGARAR'::text, 'NEVND'::text, 'STJORN'::text, 'ANNAD'::text]))),
    CONSTRAINT canonical_relation_inferred_group_confidence_check CHECK ((inferred_group_confidence = 'INFERRED'::text)),
    CONSTRAINT canonical_relation_percentage_invariant CHECK ((((canonical_relation_type = 'DIRECT_OWNERSHIP'::text) = (raw_percentage_label IS NOT NULL)) AND ((percentage_parse_status = 'PARSED'::text) = (parsed_percentage IS NOT NULL)))),
    CONSTRAINT canonical_relation_percentage_parse_status_check CHECK ((percentage_parse_status = ANY (ARRAY['PARSED'::text, 'FAILED'::text]))),
    CONSTRAINT canonical_relation_semantic_direction_confidence_check CHECK ((semantic_direction_confidence = ANY (ARRAY['OBSERVED_AND_OWNER_ACCEPTED'::text, 'OBSERVED_FOR_SUPPLIED_EXAMPLES'::text, 'UNCONFIRMED'::text]))),
    CONSTRAINT canonical_relation_semantic_status_check CHECK ((semantic_status = 'UNRESOLVED_SOURCE_ANOMALY'::text))
);

--
-- Name: v_control_registration; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_control_registration AS
 SELECT cr.relation_id,
    cea.canonical_entity_id AS actor_id,
    cea.display_name AS actor_name,
    cea.entity_kind AS actor_kind,
    cee.canonical_entity_id AS registered_id,
    cee.display_name AS registered_name,
    cr.raw_source_relation_label,
    cr.semantic_direction_confidence,
    cr.source_attempt_id
   FROM ((registry_ownership.relation cr
     JOIN public.canonical_entity cea ON ((cea.source_entity_id = cr.canonical_subject_entity_id)))
     JOIN public.canonical_entity cee ON ((cee.source_entity_id = cr.canonical_object_entity_id)))
  WHERE (cr.canonical_relation_type = 'REGISTERED_CONTROL_ACTOR'::text);

--
-- Name: v_direct_ownership; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_direct_ownership AS
 SELECT cr.relation_id,
    ceo.canonical_entity_id AS owner_id,
    ceo.display_name AS owner_name,
    ceo.entity_kind AS owner_kind,
    cew.canonical_entity_id AS owned_id,
    cew.display_name AS owned_name,
    cew.entity_kind AS owned_kind,
    cr.parsed_percentage,
    cr.raw_percentage_label,
    cr.percentage_parse_status,
    cr.semantic_direction_confidence,
    cr.source_attempt_id
   FROM ((registry_ownership.relation cr
     JOIN public.canonical_entity ceo ON ((ceo.source_entity_id = cr.canonical_subject_entity_id)))
     JOIN public.canonical_entity cew ON ((cew.source_entity_id = cr.canonical_object_entity_id)))
  WHERE (cr.canonical_relation_type = 'DIRECT_OWNERSHIP'::text);

--
-- Name: v_entity; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_entity AS
 SELECT ce.canonical_entity_id,
    ce.source_system,
    ce.source_entity_id,
    ce.entity_kind,
    ce.entity_kind_confidence,
    ce.raw_type_token,
    ce.display_name,
    ci.registration_number,
    ci.inventory_status,
    ci.typing_status,
    ci.raw_display_name AS raw_inventory_label
   FROM (public.canonical_entity ce
     LEFT JOIN public.company_inventory ci ON (((ci.source_system = ce.source_system) AND (ci.source_entity_id = ce.source_entity_id))));

--
-- Name: v_entity_change_feed; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_entity_change_feed AS
 SELECT e.event_id,
    e.canonical_entity_id,
    ce.display_name AS entity_name,
    ce.entity_kind,
    ce.registration_number,
    e.event_timestamp,
    e.change_category,
    e.field_name,
    e.old_value,
    e.new_value,
    e.related_entity_id,
    e.related_entity_name,
    e.change_summary
   FROM (public.entity_change_event e
     JOIN public.v_entity ce ON ((ce.canonical_entity_id = e.canonical_entity_id)));

--
-- Name: ownership_cycle_member; Type: TABLE; Schema: registry_ownership; Owner: -
--

CREATE TABLE registry_ownership.ownership_cycle_member (
    cycle_group_id integer NOT NULL,
    entity_id bigint NOT NULL
);

--
-- Name: v_ownership_cycle_observations; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_ownership_cycle_observations AS
 SELECT ce.canonical_entity_id AS company_id,
    ce.display_name AS company_name,
    ce.registration_number AS company_regnr,
    ocm.cycle_group_id
   FROM (registry_ownership.ownership_cycle_member ocm
     JOIN public.v_entity ce ON ((ce.source_entity_id = ocm.entity_id)));

--
-- Name: v_pep_actors; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_pep_actors AS
 SELECT ce.canonical_entity_id AS canonical_person_id,
    ce.display_name AS person_name,
    pr.pep_id,
    pr.office_title,
    pr.pep_category,
    pr.period_start,
    pr.period_end
   FROM (public.pep_record pr
     JOIN public.canonical_entity ce ON (((ce.display_name ~~* pr.person_name) AND (ce.entity_kind = 'PERSON'::text))));

--
-- Name: v_provenance; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_provenance AS
 SELECT ce.canonical_entity_id,
    ce.source_entity_id,
    max(ra.requested_at) AS last_fetched_at,
    (array_agg(ra.response_sha256 ORDER BY ra.requested_at DESC))[1] AS snapshot_sha256,
    (array_agg(ra.http_status ORDER BY ra.requested_at DESC))[1] AS last_http_status,
    (array_agg(ra.error_category ORDER BY ra.requested_at DESC))[1] AS last_error_category
   FROM (public.canonical_entity ce
     JOIN public.request_attempt ra ON ((ra.requested_source_entity_id = ce.source_entity_id)))
  WHERE (ra.endpoint = 'get-table'::text)
  GROUP BY ce.canonical_entity_id, ce.source_entity_id;

--
-- Name: v_role_relation; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_role_relation AS
 SELECT cr.relation_id,
        CASE
            WHEN (cef.entity_kind = 'PERSON'::text) THEN cef.canonical_entity_id
            ELSE cet.canonical_entity_id
        END AS person_id,
        CASE
            WHEN (cef.entity_kind = 'PERSON'::text) THEN cef.display_name
            ELSE cet.display_name
        END AS person_name,
        CASE
            WHEN (cef.entity_kind = 'PERSON'::text) THEN cet.canonical_entity_id
            ELSE cef.canonical_entity_id
        END AS company_id,
        CASE
            WHEN (cef.entity_kind = 'PERSON'::text) THEN cet.display_name
            ELSE cef.display_name
        END AS company_name,
    cr.inferred_group,
    cr.inferred_group_confidence,
    cr.canonical_role_label,
    cr.raw_source_relation_label,
    cr.raw_source_relation_type_id,
    cr.semantic_direction_confidence,
    cr.semantic_status,
    cr.source_attempt_id
   FROM ((registry_ownership.relation cr
     JOIN public.canonical_entity cef ON ((cef.source_entity_id = cr.raw_from_source_entity_id)))
     JOIN public.canonical_entity cet ON ((cet.source_entity_id = cr.raw_to_source_entity_id)))
  WHERE ((cr.canonical_relation_type = 'OTHER_SOURCE_RELATION'::text) AND ((cef.entity_kind = 'PERSON'::text) <> (cet.entity_kind = 'PERSON'::text)));

--
-- Name: v_registration_patterns; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_registration_patterns AS
 SELECT ce.canonical_entity_id AS entity_id,
    ce.display_name AS entity_name,
    ce.entity_kind,
    'MANY_REGISTERED_ROLES'::text AS pattern_type,
    (('Registreret med roller i '::text || (count(DISTINCT vrr.company_id))::text) || ' selskaber'::text) AS pattern_label,
    (count(DISTINCT vrr.company_id))::numeric AS observed_value
   FROM (public.canonical_entity ce
     JOIN public.v_role_relation vrr ON ((vrr.person_id = ce.canonical_entity_id)))
  WHERE (ce.entity_kind = 'PERSON'::text)
  GROUP BY ce.canonical_entity_id, ce.display_name, ce.entity_kind
 HAVING (count(DISTINCT vrr.company_id) >= 5)
UNION ALL
 SELECT ce.canonical_entity_id AS entity_id,
    ce.display_name AS entity_name,
    ce.entity_kind,
    'DIRECT_SHARE_SUM_OVER_100'::text AS pattern_type,
    (('Registrerede direkte ejerandele summerer til '::text || (round(sum(vdo.parsed_percentage), 1))::text) || '%'::text) AS pattern_label,
    round(sum(vdo.parsed_percentage), 1) AS observed_value
   FROM (public.canonical_entity ce
     JOIN public.v_direct_ownership vdo ON ((vdo.owned_id = ce.canonical_entity_id)))
  WHERE (ce.entity_kind = 'COMPANY'::text)
  GROUP BY ce.canonical_entity_id, ce.display_name, ce.entity_kind
 HAVING (sum(vdo.parsed_percentage) > 100.0);

--
-- Name: v_same_surname_share_observations; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_same_surname_share_observations AS
 WITH surname_shares AS (
         SELECT vdo.owned_id AS company_id,
            ce_comp.display_name AS company_name,
            ce_comp.registration_number AS company_regnr,
            split_part(vdo.owner_name, ' '::text, array_length(string_to_array(vdo.owner_name, ' '::text), 1)) AS surname,
            count(DISTINCT vdo.owner_id) AS person_count,
            sum(vdo.parsed_percentage) AS combined_percentage
           FROM (public.v_direct_ownership vdo
             JOIN public.v_entity ce_comp ON ((ce_comp.canonical_entity_id = vdo.owned_id)))
          WHERE ((vdo.owner_kind = 'PERSON'::text) AND (vdo.parsed_percentage < 25.0) AND (vdo.parsed_percentage > (0)::numeric))
          GROUP BY vdo.owned_id, ce_comp.display_name, ce_comp.registration_number, (split_part(vdo.owner_name, ' '::text, array_length(string_to_array(vdo.owner_name, ' '::text), 1)))
         HAVING ((sum(vdo.parsed_percentage) >= 25.0) AND (count(DISTINCT vdo.owner_id) > 1))
        )
 SELECT company_id,
    company_name,
    company_regnr,
    'SAME_SURNAME_SHARE'::text AS observation_type,
    'SAME_SURNAME_SHARE'::text AS observation_code,
    (((((('Registrerede personer med efternavnet '::text || surname) || ' ejer samlet '::text) || (round(combined_percentage, 1))::text) || '% ('::text) || (person_count)::text) || ' personer)'::text) AS observation_label,
    combined_percentage AS observed_pct,
    NULL::bigint AS related_entity_id,
    (((surname || ' ('::text) || (person_count)::text) || ' personer)'::text) AS related_entity_name
   FROM surname_shares;

--
-- Name: v_source_anomaly; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_source_anomaly AS
 SELECT cr.relation_id,
    cef.canonical_entity_id AS from_id,
    cef.display_name AS from_name,
    cet.canonical_entity_id AS to_id,
    cet.display_name AS to_name,
    cr.raw_source_relation_type_id,
    cr.raw_source_relation_label,
    cr.semantic_status,
    cr.semantic_direction_confidence,
    cr.source_attempt_id
   FROM ((registry_ownership.relation cr
     JOIN public.canonical_entity cef ON ((cef.source_entity_id = cr.raw_from_source_entity_id)))
     JOIN public.canonical_entity cet ON ((cet.source_entity_id = cr.raw_to_source_entity_id)))
  WHERE (cr.semantic_status = 'UNRESOLVED_SOURCE_ANOMALY'::text);

--
-- Name: v_structure_observations; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_structure_observations AS
 SELECT v_ownership_cycle_observations.company_id,
    v_ownership_cycle_observations.company_name,
    v_ownership_cycle_observations.company_regnr,
    'OWNERSHIP_CYCLE'::text AS observation_type,
    'OWNERSHIP_CYCLE'::text AS observation_code,
    (('Lukket ejerrelation registreret (gruppe #'::text || (v_ownership_cycle_observations.cycle_group_id)::text) || ')'::text) AS observation_label,
    NULL::numeric AS observed_pct,
    NULL::bigint AS related_entity_id,
    NULL::text AS related_entity_name
   FROM public.v_ownership_cycle_observations
UNION ALL
 SELECT v_same_surname_share_observations.company_id,
    v_same_surname_share_observations.company_name,
    v_same_surname_share_observations.company_regnr,
    v_same_surname_share_observations.observation_type,
    v_same_surname_share_observations.observation_code,
    v_same_surname_share_observations.observation_label,
    v_same_surname_share_observations.observed_pct,
    v_same_surname_share_observations.related_entity_id,
    v_same_surname_share_observations.related_entity_name
   FROM public.v_same_surname_share_observations;

--
-- Name: v_user_watchlist; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_user_watchlist AS
 SELECT w.id AS watchlist_id,
    w.canonical_entity_id,
    w.entity_kind,
    w.notes,
    w.created_at AS added_at,
    ce.display_name,
    ci.registration_number,
    ci.inventory_status
   FROM ((public.user_watchlist w
     JOIN public.canonical_entity ce ON ((ce.canonical_entity_id = w.canonical_entity_id)))
     LEFT JOIN public.company_inventory ci ON (((ci.source_system = ce.source_system) AND (ci.source_entity_id = ce.source_entity_id))));

--
-- Name: analytics_build; Type: TABLE; Schema: registry_analytics; Owner: -
--

CREATE TABLE registry_analytics.analytics_build (
    build_run_id bigint NOT NULL,
    source_projection_run_id bigint NOT NULL,
    pdf_link_code_version text NOT NULL,
    ownership_source_meta jsonb NOT NULL,
    canonical_identity_pin jsonb NOT NULL,
    temporal_conventions jsonb NOT NULL,
    group_support_policy jsonb NOT NULL,
    group_size_cap integer NOT NULL,
    phase0_measurement_id text NOT NULL,
    phase0_measured_at text NOT NULL,
    build_warnings jsonb NOT NULL,
    coverage jsonb DEFAULT '{}'::jsonb NOT NULL,
    content_hash text,
    status text NOT NULL,
    built_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT analytics_build_phase0_gate CHECK (((phase0_measurement_id IS NOT NULL) AND (phase0_measurement_id <> ALL (ARRAY[''::text, 'N/A'::text])) AND (phase0_measured_at IS NOT NULL) AND (phase0_measured_at <> ALL (ARRAY[''::text, 'N/A'::text])))),
    CONSTRAINT analytics_build_phase0_measured_at_check CHECK (((phase0_measured_at <> ''::text) AND (phase0_measured_at <> 'N/A'::text))),
    CONSTRAINT analytics_build_phase0_measurement_id_check CHECK (((phase0_measurement_id <> ''::text) AND (phase0_measurement_id <> 'N/A'::text))),
    CONSTRAINT analytics_build_status_check CHECK ((status = ANY (ARRAY['ACTIVE'::text, 'FAILED'::text, 'SUPERSEDED'::text])))
);

--
-- Name: analytics_build_build_run_id_seq; Type: SEQUENCE; Schema: registry_analytics; Owner: -
--

ALTER TABLE registry_analytics.analytics_build ALTER COLUMN build_run_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME registry_analytics.analytics_build_build_run_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: person_company_observation; Type: TABLE; Schema: registry_analytics; Owner: -
--

CREATE TABLE registry_analytics.person_company_observation (
    observation_id bigint NOT NULL,
    build_run_id bigint NOT NULL,
    source_observation_key text NOT NULL,
    canonical_person_id bigint,
    canonical_company_id bigint NOT NULL,
    role_context text NOT NULL,
    source_family text NOT NULL,
    authority_class text NOT NULL,
    identity_resolution text,
    observation_kind text NOT NULL,
    observed_at date,
    observed_state_at date,
    action text,
    state text,
    ownership_percentage numeric(6,3),
    source_projection_run_id bigint,
    source_person_role_id bigint,
    source_history_event_id bigint,
    source_event_id bigint,
    source_document_id bigint,
    source_observation_id bigint,
    source_relation_id bigint,
    source_attempt_id bigint,
    provenance jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT pco_authoritative_governance_shape CHECK (((authority_class <> 'AUTHORITATIVE_CURRENT_GOVERNANCE'::text) OR ((source_family = 'PDF_HISTORY'::text) AND (observation_kind = 'CURRENT_STATE_SNAPSHOT'::text)))),
    CONSTRAINT pco_control_actor_no_percentage CHECK (((role_context <> 'CONTROL_ACTOR'::text) OR (ownership_percentage IS NULL))),
    CONSTRAINT pco_ownership_percentage_only_on_ownership CHECK (((ownership_percentage IS NULL) OR ((role_context = 'OWNERSHIP'::text) AND (authority_class = 'OWNERSHIP_POINT_OBSERVATION'::text)))),
    CONSTRAINT pco_ownership_point_family CHECK (((authority_class <> 'OWNERSHIP_POINT_OBSERVATION'::text) OR (source_family = 'OWNERSHIP_GRAPH'::text))),
    CONSTRAINT pco_propagation_only_events CHECK (((identity_resolution IS DISTINCT FROM 'SOURCE_LOCAL_PROPAGATION'::text) OR (observation_kind = ANY (ARRAY['APPOINTED_EVENT'::text, 'REMOVED_EVENT'::text])))),
    CONSTRAINT pco_webgov_supplementary_shape CHECK (((authority_class <> 'SUPPLEMENTARY_WEB_GOVERNANCE_OBSERVATION'::text) OR (source_family = 'OWNERSHIP_WEB_GOVERNANCE'::text))),
    CONSTRAINT person_company_observation_authority_class_check CHECK ((authority_class = ANY (ARRAY['AUTHORITATIVE_CURRENT_GOVERNANCE'::text, 'SUPPLEMENTARY_WEB_GOVERNANCE_OBSERVATION'::text, 'OWNERSHIP_POINT_OBSERVATION'::text, 'HISTORICAL_PDF_OBSERVATION'::text]))),
    CONSTRAINT person_company_observation_identity_resolution_check CHECK ((identity_resolution = ANY (ARRAY['DIRECT_LINK'::text, 'SOURCE_LOCAL_PROPAGATION'::text]))),
    CONSTRAINT person_company_observation_observation_kind_check CHECK ((observation_kind = ANY (ARRAY['CURRENT_STATE_SNAPSHOT'::text, 'APPOINTED_EVENT'::text, 'REMOVED_EVENT'::text, 'OWNERSHIP_SNAPSHOT_POINT'::text, 'WEB_GOVERNANCE_SNAPSHOT_POINT'::text]))),
    CONSTRAINT person_company_observation_role_context_check CHECK ((role_context = ANY (ARRAY['BOARD'::text, 'MANAGEMENT'::text, 'OWNERSHIP'::text, 'CONTROL_ACTOR'::text]))),
    CONSTRAINT person_company_observation_source_family_check CHECK ((source_family = ANY (ARRAY['PDF_HISTORY'::text, 'OWNERSHIP_GRAPH'::text, 'OWNERSHIP_WEB_GOVERNANCE'::text])))
);

--
-- Name: person_company_observation_observation_id_seq; Type: SEQUENCE; Schema: registry_analytics; Owner: -
--

ALTER TABLE registry_analytics.person_company_observation ALTER COLUMN observation_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME registry_analytics.person_company_observation_observation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: person_company_period; Type: TABLE; Schema: registry_analytics; Owner: -
--

CREATE TABLE registry_analytics.person_company_period (
    period_id bigint NOT NULL,
    build_run_id bigint NOT NULL,
    canonical_person_id bigint NOT NULL,
    canonical_company_id bigint NOT NULL,
    role_context text NOT NULL,
    period_start date,
    period_end date,
    start_status text NOT NULL,
    end_status text NOT NULL,
    left_censored_start boolean DEFAULT false NOT NULL,
    left_censored_end boolean DEFAULT false NOT NULL,
    period_status text NOT NULL,
    opening_observation_key text,
    closing_observation_key text,
    source_family text NOT NULL,
    CONSTRAINT pcp_status_shape CHECK ((((period_status = 'BOUNDED'::text) AND (period_start IS NOT NULL) AND (period_end IS NOT NULL) AND (left_censored_start = false)) OR ((period_status = 'OPEN_LEFT_CENSORED'::text) AND (period_start IS NOT NULL) AND (period_end IS NULL) AND (left_censored_end = true)) OR ((period_status = 'CLOSED_ONLY'::text) AND (period_start IS NULL) AND (period_end IS NOT NULL) AND (left_censored_start = true)))),
    CONSTRAINT person_company_period_end_status_check CHECK ((end_status = ANY (ARRAY['KNOWN'::text, 'LEFT_CENSORED'::text, 'UNKNOWN'::text]))),
    CONSTRAINT person_company_period_period_status_check CHECK ((period_status = ANY (ARRAY['BOUNDED'::text, 'OPEN_LEFT_CENSORED'::text, 'CLOSED_ONLY'::text]))),
    CONSTRAINT person_company_period_role_context_check CHECK ((role_context = ANY (ARRAY['BOARD'::text, 'MANAGEMENT'::text, 'OWNERSHIP'::text, 'CONTROL_ACTOR'::text]))),
    CONSTRAINT person_company_period_start_status_check CHECK ((start_status = ANY (ARRAY['KNOWN'::text, 'LEFT_CENSORED'::text, 'UNKNOWN'::text])))
);

--
-- Name: person_company_period_period_id_seq; Type: SEQUENCE; Schema: registry_analytics; Owner: -
--

ALTER TABLE registry_analytics.person_company_period ALTER COLUMN period_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME registry_analytics.person_company_period_period_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: person_group; Type: TABLE; Schema: registry_analytics; Owner: -
--

CREATE TABLE registry_analytics.person_group (
    group_id bigint NOT NULL,
    build_run_id bigint NOT NULL,
    member_set_hash text NOT NULL,
    role_context text NOT NULL,
    group_kind text NOT NULL,
    group_size integer NOT NULL,
    support jsonb NOT NULL,
    first_observed date,
    last_observed date,
    CONSTRAINT person_group_group_kind_check CHECK ((group_kind = ANY (ARRAY['RECURRING_GROUP'::text, 'CORE_GROUP'::text]))),
    CONSTRAINT person_group_group_size_check CHECK ((group_size >= 2)),
    CONSTRAINT person_group_role_context_check CHECK ((role_context = ANY (ARRAY['BOARD'::text, 'MANAGEMENT'::text, 'OWNERSHIP'::text, 'CONTROL_ACTOR'::text])))
);

--
-- Name: person_group_group_id_seq; Type: SEQUENCE; Schema: registry_analytics; Owner: -
--

ALTER TABLE registry_analytics.person_group ALTER COLUMN group_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME registry_analytics.person_group_group_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: person_group_member; Type: TABLE; Schema: registry_analytics; Owner: -
--

CREATE TABLE registry_analytics.person_group_member (
    group_id bigint NOT NULL,
    canonical_person_id bigint NOT NULL
);

--
-- Name: person_group_observation; Type: TABLE; Schema: registry_analytics; Owner: -
--

CREATE TABLE registry_analytics.person_group_observation (
    group_observation_id bigint NOT NULL,
    build_run_id bigint NOT NULL,
    group_id bigint NOT NULL,
    episode_key text NOT NULL,
    canonical_company_id bigint NOT NULL,
    role_context text NOT NULL,
    episode_start date,
    episode_end date,
    source_family text NOT NULL,
    co_observation_kind text NOT NULL,
    CONSTRAINT person_group_observation_co_observation_kind_check CHECK ((co_observation_kind = ANY (ARRAY['SAME_EVENT'::text, 'SAME_SNAPSHOT'::text, 'SAFE_PERIOD_OVERLAP'::text]))),
    CONSTRAINT person_group_observation_role_context_check CHECK ((role_context = ANY (ARRAY['BOARD'::text, 'MANAGEMENT'::text, 'OWNERSHIP'::text, 'CONTROL_ACTOR'::text])))
);

--
-- Name: person_group_observation_group_observation_id_seq; Type: SEQUENCE; Schema: registry_analytics; Owner: -
--

ALTER TABLE registry_analytics.person_group_observation ALTER COLUMN group_observation_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME registry_analytics.person_group_observation_group_observation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: person_group_observation_member; Type: TABLE; Schema: registry_analytics; Owner: -
--

CREATE TABLE registry_analytics.person_group_observation_member (
    group_observation_id bigint NOT NULL,
    observation_id bigint NOT NULL,
    role_in_episode text
);

--
-- Name: person_person_overlap; Type: TABLE; Schema: registry_analytics; Owner: -
--

CREATE TABLE registry_analytics.person_person_overlap (
    pair_id bigint NOT NULL,
    build_run_id bigint NOT NULL,
    person_a_id bigint NOT NULL,
    person_b_id bigint NOT NULL,
    canonical_company_id bigint NOT NULL,
    role_context text NOT NULL,
    co_participation_class text NOT NULL,
    observation_count integer NOT NULL,
    episode_count integer NOT NULL,
    known_overlap_days integer,
    first_observed date,
    last_observed date,
    same_source_event_count integer DEFAULT 0 NOT NULL,
    same_snapshot_count integer DEFAULT 0 NOT NULL,
    source_family text,
    authority_class text,
    CONSTRAINT person_person_overlap_co_participation_class_check CHECK ((co_participation_class = ANY (ARRAY['SAME_COMPANY_EVER'::text, 'SAME_OBSERVATION'::text, 'SAME_SNAPSHOT'::text, 'KNOWN_TEMPORAL_OVERLAP'::text, 'UNKNOWN_TEMPORAL_RELATION'::text]))),
    CONSTRAINT person_person_overlap_role_context_check CHECK ((role_context = ANY (ARRAY['BOARD'::text, 'MANAGEMENT'::text, 'OWNERSHIP'::text, 'CONTROL_ACTOR'::text]))),
    CONSTRAINT ppo_direct_metadata_present CHECK (((co_participation_class <> ALL (ARRAY['SAME_SNAPSHOT'::text, 'SAME_OBSERVATION'::text, 'KNOWN_TEMPORAL_OVERLAP'::text])) OR ((source_family IS NOT NULL) AND (authority_class IS NOT NULL)))),
    CONSTRAINT ppo_ordering CHECK ((person_a_id < person_b_id)),
    CONSTRAINT ppo_overlap_days_only_known CHECK (((known_overlap_days IS NULL) OR (co_participation_class = 'KNOWN_TEMPORAL_OVERLAP'::text))),
    CONSTRAINT ppo_point_semantics CHECK (((co_participation_class <> ALL (ARRAY['SAME_COMPANY_EVER'::text, 'UNKNOWN_TEMPORAL_RELATION'::text])) OR (known_overlap_days IS NULL)))
);

--
-- Name: COLUMN person_person_overlap.source_family; Type: COMMENT; Schema: registry_analytics; Owner: -
--

COMMENT ON COLUMN registry_analytics.person_person_overlap.source_family IS 'Single contributing source family; NULL when the pair fact is supported by more than one family (heterogeneous fallback evidence; closure M-01)';

--
-- Name: COLUMN person_person_overlap.authority_class; Type: COMMENT; Schema: registry_analytics; Owner: -
--

COMMENT ON COLUMN registry_analytics.person_person_overlap.authority_class IS 'Single contributing authority class; NULL when the pair fact is supported by more than one authority class (heterogeneous fallback evidence; closure M-01)';

--
-- Name: person_person_overlap_pair_id_seq; Type: SEQUENCE; Schema: registry_analytics; Owner: -
--

ALTER TABLE registry_analytics.person_person_overlap ALTER COLUMN pair_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME registry_analytics.person_person_overlap_pair_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: build_meta; Type: TABLE; Schema: registry_ownership; Owner: -
--

CREATE TABLE registry_ownership.build_meta (
    build_id bigint NOT NULL,
    schema_version text NOT NULL,
    source_relation_count bigint NOT NULL,
    source_relation_max_id bigint NOT NULL,
    max_source_attempt_id bigint,
    max_collection_run_id bigint,
    built_at timestamp with time zone DEFAULT now() NOT NULL,
    content_hash text NOT NULL,
    status text NOT NULL
);

--
-- Name: build_meta_build_id_seq; Type: SEQUENCE; Schema: registry_ownership; Owner: -
--

ALTER TABLE registry_ownership.build_meta ALTER COLUMN build_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME registry_ownership.build_meta_build_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: company_relation_context; Type: TABLE; Schema: registry_ownership; Owner: -
--

CREATE TABLE registry_ownership.company_relation_context (
    context_id bigint NOT NULL,
    company_a_entity_id bigint NOT NULL,
    company_b_entity_id bigint NOT NULL,
    relation_basis text NOT NULL,
    related_entity_id bigint,
    company_a_relation text,
    company_b_relation text,
    source_relation_id bigint,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT company_relation_context_check CHECK ((company_a_entity_id <> company_b_entity_id)),
    CONSTRAINT company_relation_context_relation_basis_check CHECK ((relation_basis = ANY (ARRAY['DIRECT_OWNERSHIP'::text, 'INDIRECT_OWNERSHIP'::text, 'SHARED_OWNER'::text, 'SHARED_BENEFICIAL_OWNER'::text, 'SHARED_BOARD_PERSON'::text, 'SHARED_MANAGEMENT_PERSON'::text, 'CROSS_ROLE_PERSON'::text, 'SHARED_OTHER_RELATION'::text, 'DIRECT_CONTROL_ACTOR'::text])))
);

--
-- Name: company_relation_context_context_id_seq; Type: SEQUENCE; Schema: registry_ownership; Owner: -
--

ALTER TABLE registry_ownership.company_relation_context ALTER COLUMN context_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME registry_ownership.company_relation_context_context_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: indirect_ownership; Type: TABLE; Schema: registry_ownership; Owner: -
--

CREATE TABLE registry_ownership.indirect_ownership (
    indirect_ownership_id bigint NOT NULL,
    owner_entity_id bigint NOT NULL,
    owned_entity_id bigint NOT NULL,
    computed_percentage numeric(9,4) NOT NULL,
    path_count integer NOT NULL,
    contributing_paths jsonb NOT NULL,
    paths_truncated boolean DEFAULT false NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: indirect_ownership_indirect_ownership_id_seq; Type: SEQUENCE; Schema: registry_ownership; Owner: -
--

ALTER TABLE registry_ownership.indirect_ownership ALTER COLUMN indirect_ownership_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME registry_ownership.indirect_ownership_indirect_ownership_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: person_relation_context; Type: TABLE; Schema: registry_ownership; Owner: -
--

CREATE TABLE registry_ownership.person_relation_context (
    context_id bigint NOT NULL,
    person_a_entity_id bigint NOT NULL,
    person_b_entity_id bigint NOT NULL,
    company_entity_id bigint NOT NULL,
    person_a_relation_id bigint NOT NULL,
    person_a_group text,
    person_a_role_label text,
    person_b_relation_id bigint NOT NULL,
    person_b_group text,
    person_b_role_label text,
    is_cross_role boolean NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT person_relation_context_check CHECK ((person_a_entity_id < person_b_entity_id))
);

--
-- Name: person_relation_context_context_id_seq; Type: SEQUENCE; Schema: registry_ownership; Owner: -
--

ALTER TABLE registry_ownership.person_relation_context ALTER COLUMN context_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME registry_ownership.person_relation_context_context_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: relation_evidence_pointer; Type: TABLE; Schema: registry_ownership; Owner: -
--

CREATE TABLE registry_ownership.relation_evidence_pointer (
    relation_id bigint NOT NULL,
    source_attempt_id bigint NOT NULL,
    response_sha256 character(64),
    collection_run_id bigint NOT NULL,
    materialized_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: relation_relation_id_seq; Type: SEQUENCE; Schema: registry_ownership; Owner: -
--

ALTER TABLE registry_ownership.relation ALTER COLUMN relation_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME registry_ownership.relation_relation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

--
-- Name: pep_record pep_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pep_record ALTER COLUMN pep_id SET DEFAULT nextval('public.pep_record_pep_id_seq'::regclass);

--
-- Name: user_watchlist id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_watchlist ALTER COLUMN id SET DEFAULT nextval('public.user_watchlist_id_seq'::regclass);

--
-- Name: canonical_entity canonical_entity_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.canonical_entity
    ADD CONSTRAINT canonical_entity_pkey PRIMARY KEY (canonical_entity_id);

--
-- Name: canonical_entity canonical_entity_source_system_source_entity_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.canonical_entity
    ADD CONSTRAINT canonical_entity_source_system_source_entity_id_key UNIQUE (source_system, source_entity_id);

--
-- Name: canonical_relation_legacy canonical_relation_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.canonical_relation_legacy
    ADD CONSTRAINT canonical_relation_pkey PRIMARY KEY (relation_id);

--
-- Name: canonical_relation_legacy canonical_relation_raw_from_source_entity_id_raw_to_source__key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.canonical_relation_legacy
    ADD CONSTRAINT canonical_relation_raw_from_source_entity_id_raw_to_source__key UNIQUE (raw_from_source_entity_id, raw_to_source_entity_id, raw_source_relation_type_id);

--
-- Name: collection_run collection_run_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_run
    ADD CONSTRAINT collection_run_pkey PRIMARY KEY (collection_run_id);

--
-- Name: company_inventory company_inventory_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_inventory
    ADD CONSTRAINT company_inventory_pkey PRIMARY KEY (source_system, source_entity_id);

--
-- Name: company_location company_location_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_location
    ADD CONSTRAINT company_location_pkey PRIMARY KEY (source_system, source_entity_id);

--
-- Name: company_relation_context_legacy company_relation_context_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_relation_context_legacy
    ADD CONSTRAINT company_relation_context_pkey PRIMARY KEY (context_id);

--
-- Name: entity_change_event entity_change_event_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_change_event
    ADD CONSTRAINT entity_change_event_pkey PRIMARY KEY (event_id);

--
-- Name: entity_status entity_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_status
    ADD CONSTRAINT entity_status_pkey PRIMARY KEY (entity_status_id);

--
-- Name: entity_status entity_status_source_entity_id_canonical_status_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_status
    ADD CONSTRAINT entity_status_source_entity_id_canonical_status_type_key UNIQUE (source_entity_id, canonical_status_type);

--
-- Name: enumeration_key enumeration_key_key_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.enumeration_key
    ADD CONSTRAINT enumeration_key_key_number_key UNIQUE (key_number);

--
-- Name: enumeration_key enumeration_key_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.enumeration_key
    ADD CONSTRAINT enumeration_key_pkey PRIMARY KEY (search_key);

--
-- Name: geo_postcode geo_postcode_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.geo_postcode
    ADD CONSTRAINT geo_postcode_pkey PRIMARY KEY (postal_code);

--
-- Name: indirect_ownership_legacy indirect_ownership_owner_entity_id_owned_entity_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indirect_ownership_legacy
    ADD CONSTRAINT indirect_ownership_owner_entity_id_owned_entity_id_key UNIQUE (owner_entity_id, owned_entity_id);

--
-- Name: indirect_ownership_legacy indirect_ownership_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indirect_ownership_legacy
    ADD CONSTRAINT indirect_ownership_pkey PRIMARY KEY (indirect_ownership_id);

--
-- Name: input_parse_status input_parse_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.input_parse_status
    ADD CONSTRAINT input_parse_status_pkey PRIMARY KEY (response_sha256);

--
-- Name: ownership_cycle_member_legacy ownership_cycle_member_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ownership_cycle_member_legacy
    ADD CONSTRAINT ownership_cycle_member_pkey PRIMARY KEY (cycle_group_id, entity_id);

--
-- Name: pep_record pep_record_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pep_record
    ADD CONSTRAINT pep_record_pkey PRIMARY KEY (pep_id);

--
-- Name: person_relation_context_legacy person_relation_context_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.person_relation_context_legacy
    ADD CONSTRAINT person_relation_context_pkey PRIMARY KEY (context_id);

--
-- Name: request_attempt request_attempt_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_attempt
    ADD CONSTRAINT request_attempt_pkey PRIMARY KEY (attempt_id);

--
-- Name: response_content response_content_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.response_content
    ADD CONSTRAINT response_content_pkey PRIMARY KEY (response_sha256);

--
-- Name: user_watchlist uq_watchlist_entity; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_watchlist
    ADD CONSTRAINT uq_watchlist_entity UNIQUE (canonical_entity_id);

--
-- Name: user_watchlist user_watchlist_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_watchlist
    ADD CONSTRAINT user_watchlist_pkey PRIMARY KEY (id);

--
-- Name: analytics_build analytics_build_pkey; Type: CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.analytics_build
    ADD CONSTRAINT analytics_build_pkey PRIMARY KEY (build_run_id);

--
-- Name: person_company_observation person_company_observation_build_run_id_source_observation__key; Type: CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_company_observation
    ADD CONSTRAINT person_company_observation_build_run_id_source_observation__key UNIQUE (build_run_id, source_observation_key);

--
-- Name: person_company_observation person_company_observation_pkey; Type: CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_company_observation
    ADD CONSTRAINT person_company_observation_pkey PRIMARY KEY (observation_id);

--
-- Name: person_company_period person_company_period_build_run_id_canonical_person_id_cano_key; Type: CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_company_period
    ADD CONSTRAINT person_company_period_build_run_id_canonical_person_id_cano_key UNIQUE (build_run_id, canonical_person_id, canonical_company_id, role_context, opening_observation_key);

--
-- Name: person_company_period person_company_period_pkey; Type: CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_company_period
    ADD CONSTRAINT person_company_period_pkey PRIMARY KEY (period_id);

--
-- Name: person_group_observation person_group_observation_pkey; Type: CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_group_observation
    ADD CONSTRAINT person_group_observation_pkey PRIMARY KEY (group_observation_id);

--
-- Name: person_group person_group_pkey; Type: CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_group
    ADD CONSTRAINT person_group_pkey PRIMARY KEY (group_id);

--
-- Name: person_person_overlap person_person_overlap_pkey; Type: CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_person_overlap
    ADD CONSTRAINT person_person_overlap_pkey PRIMARY KEY (pair_id);

--
-- Name: person_group pg_grain; Type: CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_group
    ADD CONSTRAINT pg_grain UNIQUE (build_run_id, member_set_hash, role_context, group_kind);

--
-- Name: person_group_member pgm_unique; Type: CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_group_member
    ADD CONSTRAINT pgm_unique UNIQUE (group_id, canonical_person_id);

--
-- Name: person_group_observation pgo_unique; Type: CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_group_observation
    ADD CONSTRAINT pgo_unique UNIQUE (group_id, episode_key, canonical_company_id, role_context);

--
-- Name: person_group_observation_member pgom_unique; Type: CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_group_observation_member
    ADD CONSTRAINT pgom_unique UNIQUE (group_observation_id, observation_id);

--
-- Name: person_person_overlap ppo_grain; Type: CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_person_overlap
    ADD CONSTRAINT ppo_grain UNIQUE (build_run_id, person_a_id, person_b_id, canonical_company_id, role_context, co_participation_class);

--
-- Name: build_meta build_meta_pkey; Type: CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.build_meta
    ADD CONSTRAINT build_meta_pkey PRIMARY KEY (build_id);

--
-- Name: company_relation_context company_relation_context_pkey; Type: CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.company_relation_context
    ADD CONSTRAINT company_relation_context_pkey PRIMARY KEY (context_id);

--
-- Name: indirect_ownership indirect_ownership_owner_entity_id_owned_entity_id_key; Type: CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.indirect_ownership
    ADD CONSTRAINT indirect_ownership_owner_entity_id_owned_entity_id_key UNIQUE (owner_entity_id, owned_entity_id);

--
-- Name: indirect_ownership indirect_ownership_pkey; Type: CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.indirect_ownership
    ADD CONSTRAINT indirect_ownership_pkey PRIMARY KEY (indirect_ownership_id);

--
-- Name: ownership_cycle_member ownership_cycle_member_pkey; Type: CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.ownership_cycle_member
    ADD CONSTRAINT ownership_cycle_member_pkey PRIMARY KEY (cycle_group_id, entity_id);

--
-- Name: person_relation_context person_relation_context_pkey; Type: CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.person_relation_context
    ADD CONSTRAINT person_relation_context_pkey PRIMARY KEY (context_id);

--
-- Name: relation_evidence_pointer relation_evidence_pointer_pkey; Type: CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.relation_evidence_pointer
    ADD CONSTRAINT relation_evidence_pointer_pkey PRIMARY KEY (relation_id);

--
-- Name: relation relation_pkey; Type: CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.relation
    ADD CONSTRAINT relation_pkey PRIMARY KEY (relation_id);

--
-- Name: relation relation_raw_from_source_entity_id_raw_to_source_entity_id__key; Type: CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.relation
    ADD CONSTRAINT relation_raw_from_source_entity_id_raw_to_source_entity_id__key UNIQUE (raw_from_source_entity_id, raw_to_source_entity_id, raw_source_relation_type_id);

--
-- Name: canonical_entity_kind_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX canonical_entity_kind_idx ON public.canonical_entity USING btree (entity_kind);

--
-- Name: canonical_entity_source_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX canonical_entity_source_idx ON public.canonical_entity USING btree (source_system, source_entity_id);

--
-- Name: canonical_relation_object_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX canonical_relation_object_idx ON public.canonical_relation_legacy USING btree (canonical_object_entity_id);

--
-- Name: canonical_relation_subject_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX canonical_relation_subject_idx ON public.canonical_relation_legacy USING btree (canonical_subject_entity_id);

--
-- Name: canonical_relation_type_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX canonical_relation_type_idx ON public.canonical_relation_legacy USING btree (canonical_relation_type);

--
-- Name: company_inventory_name_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX company_inventory_name_idx ON public.company_inventory USING btree (normalized_search_name);

--
-- Name: company_inventory_regnr_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX company_inventory_regnr_idx ON public.company_inventory USING btree (registration_number);

--
-- Name: company_inventory_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX company_inventory_status_idx ON public.company_inventory USING btree (inventory_status);

--
-- Name: company_relation_context_a_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX company_relation_context_a_idx ON public.company_relation_context_legacy USING btree (company_a_entity_id);

--
-- Name: company_relation_context_b_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX company_relation_context_b_idx ON public.company_relation_context_legacy USING btree (company_b_entity_id);

--
-- Name: company_relation_context_basis_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX company_relation_context_basis_idx ON public.company_relation_context_legacy USING btree (relation_basis);

--
-- Name: enumeration_key_number_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX enumeration_key_number_idx ON public.enumeration_key USING btree (key_number);

--
-- Name: enumeration_key_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX enumeration_key_status_idx ON public.enumeration_key USING btree (status);

--
-- Name: idx_entity_change_event_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_entity_change_event_category ON public.entity_change_event USING btree (change_category, event_timestamp DESC);

--
-- Name: idx_entity_change_event_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_entity_change_event_entity ON public.entity_change_event USING btree (canonical_entity_id, event_timestamp DESC);

--
-- Name: idx_pep_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pep_name ON public.pep_record USING btree (person_name);

--
-- Name: idx_watchlist_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_watchlist_entity ON public.user_watchlist USING btree (canonical_entity_id);

--
-- Name: indirect_ownership_owned_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX indirect_ownership_owned_idx ON public.indirect_ownership_legacy USING btree (owned_entity_id);

--
-- Name: indirect_ownership_owner_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX indirect_ownership_owner_idx ON public.indirect_ownership_legacy USING btree (owner_entity_id);

--
-- Name: person_relation_context_a_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX person_relation_context_a_idx ON public.person_relation_context_legacy USING btree (person_a_entity_id);

--
-- Name: person_relation_context_b_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX person_relation_context_b_idx ON public.person_relation_context_legacy USING btree (person_b_entity_id);

--
-- Name: person_relation_context_co_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX person_relation_context_co_idx ON public.person_relation_context_legacy USING btree (company_entity_id);

--
-- Name: request_attempt_endpoint_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX request_attempt_endpoint_idx ON public.request_attempt USING btree (endpoint);

--
-- Name: request_attempt_entity_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX request_attempt_entity_idx ON public.request_attempt USING btree (requested_source_entity_id);

--
-- Name: request_attempt_run_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX request_attempt_run_idx ON public.request_attempt USING btree (collection_run_id);

--
-- Name: pco_build_company_idx; Type: INDEX; Schema: registry_analytics; Owner: -
--

CREATE INDEX pco_build_company_idx ON registry_analytics.person_company_observation USING btree (build_run_id, canonical_company_id);

--
-- Name: pco_build_context_idx; Type: INDEX; Schema: registry_analytics; Owner: -
--

CREATE INDEX pco_build_context_idx ON registry_analytics.person_company_observation USING btree (build_run_id, role_context);

--
-- Name: pco_build_person_idx; Type: INDEX; Schema: registry_analytics; Owner: -
--

CREATE INDEX pco_build_person_idx ON registry_analytics.person_company_observation USING btree (build_run_id, canonical_person_id);

--
-- Name: pcp_build_company_ctx_idx; Type: INDEX; Schema: registry_analytics; Owner: -
--

CREATE INDEX pcp_build_company_ctx_idx ON registry_analytics.person_company_period USING btree (build_run_id, canonical_company_id, role_context);

--
-- Name: pcp_build_person_idx; Type: INDEX; Schema: registry_analytics; Owner: -
--

CREATE INDEX pcp_build_person_idx ON registry_analytics.person_company_period USING btree (build_run_id, canonical_person_id);

--
-- Name: pg_build_hash_idx; Type: INDEX; Schema: registry_analytics; Owner: -
--

CREATE INDEX pg_build_hash_idx ON registry_analytics.person_group USING btree (build_run_id, member_set_hash);

--
-- Name: pg_ctx_idx; Type: INDEX; Schema: registry_analytics; Owner: -
--

CREATE INDEX pg_ctx_idx ON registry_analytics.person_group USING btree (role_context, group_kind);

--
-- Name: pgm_person_idx; Type: INDEX; Schema: registry_analytics; Owner: -
--

CREATE INDEX pgm_person_idx ON registry_analytics.person_group_member USING btree (canonical_person_id);

--
-- Name: pgo_episode_idx; Type: INDEX; Schema: registry_analytics; Owner: -
--

CREATE INDEX pgo_episode_idx ON registry_analytics.person_group_observation USING btree (group_id, episode_key);

--
-- Name: pgo_group_idx; Type: INDEX; Schema: registry_analytics; Owner: -
--

CREATE INDEX pgo_group_idx ON registry_analytics.person_group_observation USING btree (group_id, canonical_company_id, role_context);

--
-- Name: pgom_observation_idx; Type: INDEX; Schema: registry_analytics; Owner: -
--

CREATE INDEX pgom_observation_idx ON registry_analytics.person_group_observation_member USING btree (observation_id);

--
-- Name: ppo_build_company_idx; Type: INDEX; Schema: registry_analytics; Owner: -
--

CREATE INDEX ppo_build_company_idx ON registry_analytics.person_person_overlap USING btree (build_run_id, canonical_company_id, role_context);

--
-- Name: ppo_build_pair_idx; Type: INDEX; Schema: registry_analytics; Owner: -
--

CREATE INDEX ppo_build_pair_idx ON registry_analytics.person_person_overlap USING btree (build_run_id, person_a_id, person_b_id);

--
-- Name: company_relation_context_company_a_entity_id_idx; Type: INDEX; Schema: registry_ownership; Owner: -
--

CREATE INDEX company_relation_context_company_a_entity_id_idx ON registry_ownership.company_relation_context USING btree (company_a_entity_id);

--
-- Name: company_relation_context_company_b_entity_id_idx; Type: INDEX; Schema: registry_ownership; Owner: -
--

CREATE INDEX company_relation_context_company_b_entity_id_idx ON registry_ownership.company_relation_context USING btree (company_b_entity_id);

--
-- Name: company_relation_context_relation_basis_idx; Type: INDEX; Schema: registry_ownership; Owner: -
--

CREATE INDEX company_relation_context_relation_basis_idx ON registry_ownership.company_relation_context USING btree (relation_basis);

--
-- Name: indirect_ownership_owned_entity_id_idx; Type: INDEX; Schema: registry_ownership; Owner: -
--

CREATE INDEX indirect_ownership_owned_entity_id_idx ON registry_ownership.indirect_ownership USING btree (owned_entity_id);

--
-- Name: indirect_ownership_owner_entity_id_idx; Type: INDEX; Schema: registry_ownership; Owner: -
--

CREATE INDEX indirect_ownership_owner_entity_id_idx ON registry_ownership.indirect_ownership USING btree (owner_entity_id);

--
-- Name: person_relation_context_company_entity_id_idx; Type: INDEX; Schema: registry_ownership; Owner: -
--

CREATE INDEX person_relation_context_company_entity_id_idx ON registry_ownership.person_relation_context USING btree (company_entity_id);

--
-- Name: person_relation_context_person_a_entity_id_idx; Type: INDEX; Schema: registry_ownership; Owner: -
--

CREATE INDEX person_relation_context_person_a_entity_id_idx ON registry_ownership.person_relation_context USING btree (person_a_entity_id);

--
-- Name: person_relation_context_person_b_entity_id_idx; Type: INDEX; Schema: registry_ownership; Owner: -
--

CREATE INDEX person_relation_context_person_b_entity_id_idx ON registry_ownership.person_relation_context USING btree (person_b_entity_id);

--
-- Name: relation_canonical_object_entity_id_idx; Type: INDEX; Schema: registry_ownership; Owner: -
--

CREATE INDEX relation_canonical_object_entity_id_idx ON registry_ownership.relation USING btree (canonical_object_entity_id);

--
-- Name: relation_canonical_relation_type_idx; Type: INDEX; Schema: registry_ownership; Owner: -
--

CREATE INDEX relation_canonical_relation_type_idx ON registry_ownership.relation USING btree (canonical_relation_type);

--
-- Name: relation_canonical_subject_entity_id_idx; Type: INDEX; Schema: registry_ownership; Owner: -
--

CREATE INDEX relation_canonical_subject_entity_id_idx ON registry_ownership.relation USING btree (canonical_subject_entity_id);

--
-- Name: canonical_relation_legacy canonical_relation_source_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.canonical_relation_legacy
    ADD CONSTRAINT canonical_relation_source_attempt_id_fkey FOREIGN KEY (source_attempt_id) REFERENCES public.request_attempt(attempt_id);

--
-- Name: company_inventory company_inventory_inventory_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_inventory
    ADD CONSTRAINT company_inventory_inventory_run_id_fkey FOREIGN KEY (inventory_run_id) REFERENCES public.collection_run(collection_run_id);

--
-- Name: company_inventory company_inventory_raw_response_reference_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_inventory
    ADD CONSTRAINT company_inventory_raw_response_reference_fkey FOREIGN KEY (raw_response_reference) REFERENCES public.response_content(response_sha256);

--
-- Name: company_location company_location_postal_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_location
    ADD CONSTRAINT company_location_postal_code_fkey FOREIGN KEY (postal_code) REFERENCES public.geo_postcode(postal_code);

--
-- Name: company_location company_location_source_system_source_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_location
    ADD CONSTRAINT company_location_source_system_source_entity_id_fkey FOREIGN KEY (source_system, source_entity_id) REFERENCES public.company_inventory(source_system, source_entity_id) ON DELETE CASCADE;

--
-- Name: company_relation_context_legacy company_relation_context_source_relation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_relation_context_legacy
    ADD CONSTRAINT company_relation_context_source_relation_id_fkey FOREIGN KEY (source_relation_id) REFERENCES public.canonical_relation_legacy(relation_id);

--
-- Name: entity_change_event entity_change_event_canonical_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_change_event
    ADD CONSTRAINT entity_change_event_canonical_entity_id_fkey FOREIGN KEY (canonical_entity_id) REFERENCES public.canonical_entity(canonical_entity_id);

--
-- Name: entity_change_event entity_change_event_related_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_change_event
    ADD CONSTRAINT entity_change_event_related_entity_id_fkey FOREIGN KEY (related_entity_id) REFERENCES public.canonical_entity(canonical_entity_id);

--
-- Name: entity_status entity_status_source_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_status
    ADD CONSTRAINT entity_status_source_attempt_id_fkey FOREIGN KEY (source_attempt_id) REFERENCES public.request_attempt(attempt_id);

--
-- Name: enumeration_key enumeration_key_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.enumeration_key
    ADD CONSTRAINT enumeration_key_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.request_attempt(attempt_id);

--
-- Name: input_parse_status input_parse_status_response_sha256_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.input_parse_status
    ADD CONSTRAINT input_parse_status_response_sha256_fkey FOREIGN KEY (response_sha256) REFERENCES public.response_content(response_sha256);

--
-- Name: person_relation_context_legacy person_relation_context_person_a_relation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.person_relation_context_legacy
    ADD CONSTRAINT person_relation_context_person_a_relation_id_fkey FOREIGN KEY (person_a_relation_id) REFERENCES public.canonical_relation_legacy(relation_id);

--
-- Name: person_relation_context_legacy person_relation_context_person_b_relation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.person_relation_context_legacy
    ADD CONSTRAINT person_relation_context_person_b_relation_id_fkey FOREIGN KEY (person_b_relation_id) REFERENCES public.canonical_relation_legacy(relation_id);

--
-- Name: request_attempt request_attempt_collection_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_attempt
    ADD CONSTRAINT request_attempt_collection_run_id_fkey FOREIGN KEY (collection_run_id) REFERENCES public.collection_run(collection_run_id);

--
-- Name: request_attempt request_attempt_response_sha256_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_attempt
    ADD CONSTRAINT request_attempt_response_sha256_fkey FOREIGN KEY (response_sha256) REFERENCES public.response_content(response_sha256);

--
-- Name: person_company_observation person_company_observation_build_run_id_fkey; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_company_observation
    ADD CONSTRAINT person_company_observation_build_run_id_fkey FOREIGN KEY (build_run_id) REFERENCES registry_analytics.analytics_build(build_run_id);

--
-- Name: person_company_observation person_company_observation_canonical_company_id_fkey; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_company_observation
    ADD CONSTRAINT person_company_observation_canonical_company_id_fkey FOREIGN KEY (canonical_company_id) REFERENCES public.canonical_entity(canonical_entity_id);

--
-- Name: person_company_observation person_company_observation_canonical_person_id_fkey; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_company_observation
    ADD CONSTRAINT person_company_observation_canonical_person_id_fkey FOREIGN KEY (canonical_person_id) REFERENCES public.canonical_entity(canonical_entity_id);

--
-- Name: person_company_period person_company_period_build_run_id_fkey; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_company_period
    ADD CONSTRAINT person_company_period_build_run_id_fkey FOREIGN KEY (build_run_id) REFERENCES registry_analytics.analytics_build(build_run_id);

--
-- Name: person_group person_group_build_run_id_fkey; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_group
    ADD CONSTRAINT person_group_build_run_id_fkey FOREIGN KEY (build_run_id) REFERENCES registry_analytics.analytics_build(build_run_id);

--
-- Name: person_group_member person_group_member_canonical_person_id_fkey; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_group_member
    ADD CONSTRAINT person_group_member_canonical_person_id_fkey FOREIGN KEY (canonical_person_id) REFERENCES public.canonical_entity(canonical_entity_id);

--
-- Name: person_group_member person_group_member_group_id_fkey; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_group_member
    ADD CONSTRAINT person_group_member_group_id_fkey FOREIGN KEY (group_id) REFERENCES registry_analytics.person_group(group_id) ON DELETE CASCADE;

--
-- Name: person_group_observation person_group_observation_build_run_id_fkey; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_group_observation
    ADD CONSTRAINT person_group_observation_build_run_id_fkey FOREIGN KEY (build_run_id) REFERENCES registry_analytics.analytics_build(build_run_id);

--
-- Name: person_group_observation person_group_observation_canonical_company_id_fkey; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_group_observation
    ADD CONSTRAINT person_group_observation_canonical_company_id_fkey FOREIGN KEY (canonical_company_id) REFERENCES public.canonical_entity(canonical_entity_id);

--
-- Name: person_group_observation person_group_observation_group_id_fkey; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_group_observation
    ADD CONSTRAINT person_group_observation_group_id_fkey FOREIGN KEY (group_id) REFERENCES registry_analytics.person_group(group_id) ON DELETE CASCADE;

--
-- Name: person_group_observation_member person_group_observation_member_group_observation_id_fkey; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_group_observation_member
    ADD CONSTRAINT person_group_observation_member_group_observation_id_fkey FOREIGN KEY (group_observation_id) REFERENCES registry_analytics.person_group_observation(group_observation_id) ON DELETE CASCADE;

--
-- Name: person_group_observation_member person_group_observation_member_observation_id_fkey; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_group_observation_member
    ADD CONSTRAINT person_group_observation_member_observation_id_fkey FOREIGN KEY (observation_id) REFERENCES registry_analytics.person_company_observation(observation_id) ON DELETE CASCADE;

--
-- Name: person_person_overlap person_person_overlap_build_run_id_fkey; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_person_overlap
    ADD CONSTRAINT person_person_overlap_build_run_id_fkey FOREIGN KEY (build_run_id) REFERENCES registry_analytics.analytics_build(build_run_id);

--
-- Name: person_person_overlap person_person_overlap_canonical_company_id_fkey; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_person_overlap
    ADD CONSTRAINT person_person_overlap_canonical_company_id_fkey FOREIGN KEY (canonical_company_id) REFERENCES public.canonical_entity(canonical_entity_id);

--
-- Name: person_person_overlap person_person_overlap_person_a_id_fkey; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_person_overlap
    ADD CONSTRAINT person_person_overlap_person_a_id_fkey FOREIGN KEY (person_a_id) REFERENCES public.canonical_entity(canonical_entity_id);

--
-- Name: person_person_overlap person_person_overlap_person_b_id_fkey; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_person_overlap
    ADD CONSTRAINT person_person_overlap_person_b_id_fkey FOREIGN KEY (person_b_id) REFERENCES public.canonical_entity(canonical_entity_id);

--
-- Name: person_company_period pp_closing_resolves; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_company_period
    ADD CONSTRAINT pp_closing_resolves FOREIGN KEY (build_run_id, closing_observation_key) REFERENCES registry_analytics.person_company_observation(build_run_id, source_observation_key);

--
-- Name: person_company_period pp_opening_resolves; Type: FK CONSTRAINT; Schema: registry_analytics; Owner: -
--

ALTER TABLE ONLY registry_analytics.person_company_period
    ADD CONSTRAINT pp_opening_resolves FOREIGN KEY (build_run_id, opening_observation_key) REFERENCES registry_analytics.person_company_observation(build_run_id, source_observation_key);

--
-- Name: company_relation_context company_relation_context_source_relation_id_fkey; Type: FK CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.company_relation_context
    ADD CONSTRAINT company_relation_context_source_relation_id_fkey FOREIGN KEY (source_relation_id) REFERENCES registry_ownership.relation(relation_id);

--
-- Name: person_relation_context person_relation_context_person_a_relation_id_fkey; Type: FK CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.person_relation_context
    ADD CONSTRAINT person_relation_context_person_a_relation_id_fkey FOREIGN KEY (person_a_relation_id) REFERENCES registry_ownership.relation(relation_id);

--
-- Name: person_relation_context person_relation_context_person_b_relation_id_fkey; Type: FK CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.person_relation_context
    ADD CONSTRAINT person_relation_context_person_b_relation_id_fkey FOREIGN KEY (person_b_relation_id) REFERENCES registry_ownership.relation(relation_id);

--
-- Name: relation_evidence_pointer relation_evidence_pointer_collection_run_id_fkey; Type: FK CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.relation_evidence_pointer
    ADD CONSTRAINT relation_evidence_pointer_collection_run_id_fkey FOREIGN KEY (collection_run_id) REFERENCES public.collection_run(collection_run_id);

--
-- Name: relation_evidence_pointer relation_evidence_pointer_relation_id_fkey; Type: FK CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.relation_evidence_pointer
    ADD CONSTRAINT relation_evidence_pointer_relation_id_fkey FOREIGN KEY (relation_id) REFERENCES registry_ownership.relation(relation_id) ON DELETE CASCADE;

--
-- Name: relation_evidence_pointer relation_evidence_pointer_response_sha256_fkey; Type: FK CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.relation_evidence_pointer
    ADD CONSTRAINT relation_evidence_pointer_response_sha256_fkey FOREIGN KEY (response_sha256) REFERENCES public.response_content(response_sha256);

--
-- Name: relation_evidence_pointer relation_evidence_pointer_source_attempt_id_fkey; Type: FK CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.relation_evidence_pointer
    ADD CONSTRAINT relation_evidence_pointer_source_attempt_id_fkey FOREIGN KEY (source_attempt_id) REFERENCES public.request_attempt(attempt_id);

--
-- Name: relation relation_source_attempt_id_fkey; Type: FK CONSTRAINT; Schema: registry_ownership; Owner: -
--

ALTER TABLE ONLY registry_ownership.relation
    ADD CONSTRAINT relation_source_attempt_id_fkey FOREIGN KEY (source_attempt_id) REFERENCES public.request_attempt(attempt_id);

--
-- PostgreSQL database dump complete
--
