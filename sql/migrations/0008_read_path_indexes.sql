-- 0008 (W5b): index-friendly hot read paths (audit B-12, B-16). Idempotent.
--
-- B-16: a leading index on every FK column of registry_ownership and on
-- request_attempt.response_sha256 (parent deletes and joins on them).
CREATE INDEX IF NOT EXISTS relation_source_attempt_id_idx
    ON registry_ownership.relation (source_attempt_id);
CREATE INDEX IF NOT EXISTS person_relation_context_person_a_relation_id_idx
    ON registry_ownership.person_relation_context (person_a_relation_id);
CREATE INDEX IF NOT EXISTS person_relation_context_person_b_relation_id_idx
    ON registry_ownership.person_relation_context (person_b_relation_id);
CREATE INDEX IF NOT EXISTS company_relation_context_source_relation_id_idx
    ON registry_ownership.company_relation_context (source_relation_id);
CREATE INDEX IF NOT EXISTS relation_evidence_pointer_source_attempt_id_idx
    ON registry_ownership.relation_evidence_pointer (source_attempt_id);
CREATE INDEX IF NOT EXISTS relation_evidence_pointer_response_sha256_idx
    ON registry_ownership.relation_evidence_pointer (response_sha256);
CREATE INDEX IF NOT EXISTS relation_evidence_pointer_collection_run_id_idx
    ON registry_ownership.relation_evidence_pointer (collection_run_id);
CREATE INDEX IF NOT EXISTS request_attempt_response_sha256_idx
    ON public.request_attempt (response_sha256);

-- B-12b: v_role_relation derived person/company with CASE, so no lookup could use
-- an index (80-170 ms per entity, once per visited node in the pathfinder). Split
-- by direction: each arm exposes a plain column, and the raw endpoint indexes
-- below serve the join. Same rows, same columns (proven on the 2026-09-04 data:
-- 7,775 rows, EXCEPT ALL empty both ways).
CREATE INDEX IF NOT EXISTS relation_raw_from_source_entity_id_idx
    ON registry_ownership.relation (raw_from_source_entity_id);
CREATE INDEX IF NOT EXISTS relation_raw_to_source_entity_id_idx
    ON registry_ownership.relation (raw_to_source_entity_id);
CREATE OR REPLACE VIEW public.v_role_relation AS
 SELECT cr.relation_id,
    cef.canonical_entity_id AS person_id,
    cef.display_name AS person_name,
    cet.canonical_entity_id AS company_id,
    cet.display_name AS company_name,
    cr.inferred_group, cr.inferred_group_confidence, cr.canonical_role_label,
    cr.raw_source_relation_label, cr.raw_source_relation_type_id,
    cr.semantic_direction_confidence, cr.semantic_status, cr.source_attempt_id
   FROM registry_ownership.relation cr
     JOIN public.canonical_entity cef ON cef.source_entity_id = cr.raw_from_source_entity_id
     JOIN public.canonical_entity cet ON cet.source_entity_id = cr.raw_to_source_entity_id
  WHERE cr.canonical_relation_type = 'OTHER_SOURCE_RELATION'
    AND cef.entity_kind = 'PERSON' AND cet.entity_kind <> 'PERSON'
UNION ALL
 SELECT cr.relation_id,
    cet.canonical_entity_id, cet.display_name,
    cef.canonical_entity_id, cef.display_name,
    cr.inferred_group, cr.inferred_group_confidence, cr.canonical_role_label,
    cr.raw_source_relation_label, cr.raw_source_relation_type_id,
    cr.semantic_direction_confidence, cr.semantic_status, cr.source_attempt_id
   FROM registry_ownership.relation cr
     JOIN public.canonical_entity cef ON cef.source_entity_id = cr.raw_from_source_entity_id
     JOIN public.canonical_entity cet ON cet.source_entity_id = cr.raw_to_source_entity_id
  WHERE cr.canonical_relation_type = 'OTHER_SOURCE_RELATION'
    AND cef.entity_kind <> 'PERSON' AND cet.entity_kind = 'PERSON';
-- B-12a: `x = id OR y = id` on the read views is answered as a UNION ALL in
-- heimdal/modules/shared/db.py either_side(), not here.
