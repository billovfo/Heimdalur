-- HEIMDAL PERSON HUMAN-REVIEW: decision payload immutability in the DB (audit 2026-11 E-14).
-- review_migration.sql is in the ledger, so this ships as a NEW file.
-- Deviation from the review migration convention: a trigger needs a function.
-- Its body is a single-quoted string (the statement splitter keeps it whole).
-- The payload columns of person_review_decision are INSERT-only for every role,
-- the owner included. Lifecycle and QA columns stay updatable through the named
-- repository functions. Idempotent. DEV/LOCAL ONLY.

CREATE OR REPLACE FUNCTION registry_projection.person_review_decision_payload_guard()
RETURNS trigger LANGUAGE plpgsql AS
'BEGIN
  IF (OLD.decision_id, OLD.case_key, OLD.reviewer, OLD.decided_at, OLD.decision,
      OLD.chosen_canonical_id, OLD.owner_note, OLD.reason_code, OLD.shown_packet_hash,
      OLD.shown_alternatives, OLD.shown_score_snapshot_ref, OLD.evidence_pin,
      OLD.supersedes_decision_id, OLD.created_at)
     IS DISTINCT FROM
     (NEW.decision_id, NEW.case_key, NEW.reviewer, NEW.decided_at, NEW.decision,
      NEW.chosen_canonical_id, NEW.owner_note, NEW.reason_code, NEW.shown_packet_hash,
      NEW.shown_alternatives, NEW.shown_score_snapshot_ref, NEW.evidence_pin,
      NEW.supersedes_decision_id, NEW.created_at) THEN
    RAISE EXCEPTION ''person_review_decision % payload is immutable'', OLD.decision_id;
  END IF;
  RETURN NEW;
END';

DROP TRIGGER IF EXISTS person_review_decision_payload_immutable
    ON registry_projection.person_review_decision;

CREATE TRIGGER person_review_decision_payload_immutable
    BEFORE UPDATE ON registry_projection.person_review_decision
    FOR EACH ROW EXECUTE FUNCTION registry_projection.person_review_decision_payload_guard();
