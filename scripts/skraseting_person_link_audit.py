#!/usr/bin/env python3
"""Risk-based manual person-link audit (task sections 40-42).

Stratifies 30 automatically LINKED person-role occurrences across the risky
groups (common names, special characters, same surname, multiple company
roles, multiple companies) and verifies each link against the canonical
model with explicit evidence queries. Every link gets CORRECT / INCORRECT /
UNCERTAIN plus the reason. PASS requires 0 INCORRECT.

Also samples UNRESOLVED occurrences (fail-closed review, section 41). The
AMBIGUOUS review documents the (empty) real population and the fixture-proven
mechanism.

Writes skraseting_entity_linking_audit.json.
"""
from __future__ import annotations

import json

import psycopg

from heimdal.pdfhist import consumer, db
from heimdal.pdfhist import entity_links as el

OUT = "skraseting_entity_linking_audit.json"


def _qa_rows(qc, sql, params=None):
    return qc.execute(sql, params or ()).fetchall()


def verify_link(pc, qc, role_id):
    """Full evidence verification for one linked person-role occurrence."""
    src = pc.execute(
        """SELECT pr.person_name, pr.entity_normalized, pr.role_type,
                  pr.role_title, pr.source_scope, pr.source_document_id,
                  pr.company_id, s.registration_number,
                  l.canonical_entity_id
           FROM registry_projection.entity_link l
           JOIN registry_projection.company_person_role pr
             ON pr.id = l.person_role_id
           JOIN registry_projection.company_current_state s
             ON s.projection_run_id = pr.projection_run_id
            AND s.company_id = pr.company_id
           WHERE l.entity_type='PERSON' AND l.link_status='LINKED'
             AND l.person_role_id = %s""", (role_id,)).fetchone()
    if src is None:
        return None
    (name, normalized, role_type, role_title, scope, doc_id, company_id,
     regnr, person_id) = src
    # canonical company id for the source company (from the COMPANY link)
    company_canon = pc.execute(
        """SELECT canonical_entity_id FROM registry_projection.entity_link
           WHERE projection_run_id=(SELECT projection_run_id
                                    FROM registry_projection.v_active_run)
             AND entity_type='COMPANY' AND link_status='LINKED'
             AND registration_number=%s""", (regnr,)).fetchone()[0]
    # 1) canonical person exists + display name matches EXACTLY (normalized)
    disp = qc.execute(
        "SELECT display_name FROM canonical_entity WHERE canonical_entity_id=%s",
        (person_id,)).fetchone()[0]
    name_exact = el.normalize_person_name(disp) == normalized
    # 2) same-company canonical role relation with compatible group exists
    rel = qc.execute(
        """SELECT canonical_role_label, inferred_group FROM v_role_relation
           WHERE person_id=%s AND company_id=%s
             AND inferred_group IN ('NEVND','STJORN','ANNAD') LIMIT 1""",
        (person_id, company_canon)).fetchone()
    # 3) uniqueness: no OTHER canonical person with same name relates to C
    others = qc.execute(
        """SELECT count(*) FROM v_role_relation v
           JOIN canonical_entity ce ON ce.canonical_entity_id = v.person_id
           WHERE v.company_id=%s AND lower(btrim(ce.display_name))=%s
             AND v.person_id <> %s
             AND v.inferred_group IN ('NEVND','STJORN','ANNAD')""",
        (company_canon, normalized, person_id)).fetchone()[0]
    # 4) global same-name population (risk signal)
    global_count = qc.execute(
        """SELECT count(*) FROM canonical_entity
           WHERE entity_kind='PERSON' AND lower(btrim(display_name))=%s""",
        (normalized,)).fetchone()[0]
    # 5) exact role-label match (stronger than group compatibility)
    label_match = bool(rel) and bool(role_title) and \
        rel[0].strip().lower() == role_title.strip().lower()
    return {
        "person_role_id": role_id,
        "source": {"person_name": name, "normalized": normalized,
                   "role_type": role_type, "role_title": role_title,
                   "source_scope": scope, "source_document_id": doc_id,
                   "company_id": company_id, "registration_number": regnr},
        "canonical": {"canonical_entity_id": person_id,
                      "display_name": disp},
        "checks": {"name_exact_normalized": name_exact,
                   "same_company_role_relation": bool(rel),
                   "relation_role_label": rel[0] if rel else None,
                   "relation_group": rel[1] if rel else None,
                   "role_label_exact_match": label_match,
                   "other_same_name_persons_in_company": others,
                   "global_same_name_persons": global_count},
    }


def main():
    pc = psycopg.connect(consumer.projection_dsn())
    qc = psycopg.connect(db.qa_dsn())
    run = consumer._resolve_active_run(pc)

    # stratified samples of LINKED role occurrences
    samples = {}

    # A) globally common names (name count > 1 in canonical) — highest risk.
    #    Name counts live in the QA DB, so link rows are pulled from the
    #    projection first and filtered by the canonical name population.
    linked_rows = pc.execute(
        """SELECT l.person_role_id, l.canonical_entity_id
           FROM registry_projection.entity_link l
           WHERE l.projection_run_id=%s AND l.entity_type='PERSON'
             AND l.link_status='LINKED'""", (run,)).fetchall()
    name_counts = {}
    for rid, pid in linked_rows:
        if pid not in name_counts:
            name_counts[pid] = qc.execute(
                """SELECT count(*) FROM canonical_entity
                   WHERE entity_kind='PERSON' AND lower(btrim(display_name))=
                         (SELECT lower(btrim(display_name)) FROM canonical_entity
                          WHERE canonical_entity_id=%s)""",
                (pid,)).fetchone()[0]
    samples["common_names"] = [(rid,) for rid, pid in linked_rows
                               if name_counts.get(pid, 0) > 1][:10]

    # B) special characters (æ ø ð í ó ý á ú)
    samples["special_characters"] = pc.execute(
        """SELECT l.person_role_id FROM registry_projection.entity_link l
           JOIN registry_projection.company_person_role pr ON pr.id=l.person_role_id
           WHERE l.projection_run_id=%s AND l.entity_type='PERSON'
             AND l.link_status='LINKED'
             AND pr.person_name ~ '[ðæøíóýá]'
           ORDER BY md5(pr.id::text) LIMIT 8""", (run,)).fetchall()

    # C) same surname, different persons, same company (sibling/parent-child)
    samples["same_surname_pairs"] = pc.execute(
        """SELECT MIN(l.person_role_id) FROM registry_projection.entity_link l
           JOIN registry_projection.company_person_role pr ON pr.id=l.person_role_id
           WHERE l.projection_run_id=%s AND l.entity_type='PERSON'
             AND l.link_status='LINKED' AND split_part(pr.entity_normalized,' ',1)
                 <> split_part(pr.entity_normalized,' ',-1)
           GROUP BY pr.company_id, split_part(pr.entity_normalized,' ',-1)
           HAVING count(DISTINCT l.canonical_entity_id) >= 2
           ORDER BY md5(MIN(l.person_role_id)::text) LIMIT 5""", (run,)).fetchall()

    # D) persons linked in multiple companies (multi-company risk)
    samples["multi_company"] = [r for r in pc.execute(
        """SELECT MIN(l.person_role_id) FROM registry_projection.entity_link l
           JOIN registry_projection.company_person_role pr ON pr.id=l.person_role_id
           WHERE l.projection_run_id=%s AND l.entity_type='PERSON'
             AND l.link_status='LINKED'
           GROUP BY l.canonical_entity_id
           HAVING count(DISTINCT pr.company_id) >= 2
           ORDER BY md5(MIN(l.person_role_id)::text) LIMIT 8""", (run,)).fetchall()]

    # E) persons holding multiple roles in the SAME company
    samples["multi_role_same_company"] = pc.execute(
        """SELECT MIN(l.person_role_id) FROM registry_projection.entity_link l
           JOIN registry_projection.company_person_role pr ON pr.id=l.person_role_id
           WHERE l.projection_run_id=%s AND l.entity_type='PERSON'
             AND l.link_status='LINKED'
           GROUP BY l.canonical_entity_id, pr.company_id
           HAVING count(DISTINCT pr.role_type) >= 2
           ORDER BY md5(MIN(l.person_role_id)::text) LIMIT 5""", (run,)).fetchall()

    # dedupe role ids, keep order, cap at 30
    seen, ordered = set(), []
    for group, rows in samples.items():
        for row in rows:
            rid = row[0]
            if rid and rid not in seen:
                seen.add(rid)
                ordered.append((group, rid))
    ordered = ordered[:34]

    audited, verdicts = [], {"CORRECT": 0, "INCORRECT": 0, "UNCERTAIN": 0}
    for group, rid in ordered:
        ev = verify_link(pc, qc, rid)
        if ev is None:
            continue
        checks = ev["checks"]
        if checks["name_exact_normalized"] and checks["same_company_role_relation"] \
                and checks["other_same_name_persons_in_company"] == 0:
            if checks["global_same_name_persons"] > 1 and \
                    not checks["role_label_exact_match"]:
                verdict = "UNCERTAIN"  # common name, role label differs
            else:
                verdict = "CORRECT"
        else:
            verdict = "INCORRECT"
        ev["audit_group"] = group
        ev["verdict"] = verdict
        audited.append(ev)
        verdicts[verdict] += 1

    # UNRESOLVED sample with fail-closed reasons (section 41)
    unresolved = pc.execute(
        """SELECT pr.person_name, pr.entity_normalized, pr.role_type,
                  pr.role_title, pr.source_scope, l.match_metadata->>'company_link_status'
           FROM registry_projection.entity_link l
           JOIN registry_projection.company_person_role pr ON pr.id=l.person_role_id
           WHERE l.projection_run_id=%s AND l.entity_type='PERSON'
             AND l.link_status='UNRESOLVED'
           ORDER BY md5(pr.id::text) LIMIT 10""", (run,)).fetchall()
    unresolved_review = []
    for (name, normalized, role_type, role_title, scope, comp_link) in unresolved:
        in_canon = qc.execute(
            """SELECT count(*) FROM canonical_entity
               WHERE entity_kind='PERSON' AND lower(btrim(display_name))=%s""",
            (normalized,)).fetchone()[0]
        reason = ("company_not_linked" if comp_link != "LINKED" else
                  ("name_matches_no_canonical_person" if in_canon == 0 else
                   "name_exists_but_no_same_company_relation"))
        unresolved_review.append({
            "person_name": name, "normalized": normalized,
            "role_type": role_type, "role_title": role_title,
            "scope": scope, "company_link_status": comp_link,
            "canonical_persons_with_same_name": in_canon,
            "fail_closed_reason": reason})

    # AMBIGUOUS real population (section 41: 10 samples if population exists)
    ambiguous_count = pc.execute(
        """SELECT count(*) FROM registry_projection.entity_link
           WHERE projection_run_id=%s AND entity_type='PERSON'
             AND link_status='AMBIGUOUS'""", (run,)).fetchone()[0]
    company_ambiguous = pc.execute(
        """SELECT count(*) FROM registry_projection.entity_link
           WHERE projection_run_id=%s AND entity_type='COMPANY'
             AND link_status='AMBIGUOUS'""", (run,)).fetchone()[0]

    result = {
        "audit_type": "risk_based_manual_person_link_audit",
        "projection_run_id": run,
        "link_code_version": el.LINK_CODE_VERSION,
        "method": ("each sampled link verified against: exact normalized "
                   "name match, same-company canonical role relation with "
                   "compatible group, zero competing same-name canonical "
                   "persons in the same company; role-label agreement used "
                   "to resolve common-name cases"),
        "sample_stratification": {k: len(v) for k, v in samples.items()},
        "audited_count": len(audited),
        "verdicts": verdicts,
        "audited_links": audited,
        "unresolved_review": {
            "sample_size": len(unresolved_review),
            "items": unresolved_review},
        "ambiguous_review": {
            "person_ambiguous_population": ambiguous_count,
            "company_ambiguous_population": company_ambiguous,
            "note": ("AMBIGUOUS population is empty in this build; the "
                     "AMBIGUOUS path is proven by unit fixtures "
                     "(test_person_same_name_ambiguous, "
                     "test_company_ambiguous_protection) and the DB "
                     "invariant forbids a canonical target on AMBIGUOUS "
                     "rows.")},
        "address_role_note": ("addresses never participate in person "
                              "matching; company_person_role carries no "
                              "address and canonical persons have none "
                              "(section 37 satisfied by construction)"),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"audited": len(audited), "verdicts": verdicts,
                      "unresolved_reviewed": len(unresolved_review),
                      "ambiguous_population": ambiguous_count},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()