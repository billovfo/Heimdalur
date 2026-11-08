"""Read-only comparison: canonical Heimdal vs registry_projection (§37/§38).

Opens TWO read-only connections:
    heimdal (QA/canonical)  - canonical_entity / company_inventory
    pdfhist_pilot           - registry_projection ACTIVE run (or explicit run)

Reports differences ONLY. No writes anywhere (section 38: no auto-correction,
no overwrite, no projection fix). Fields compared where both sides have
comparable data (section 37):
    registration_number (company_inventory by source_entity_id)
    company_name        (canonical_entity.display_name, entity_kind company)

Canonical has no PDF-domain fields (capital/board/management/purpose/
signing_rules), so those are reported as NOT_COMPARABLE (absent on the
canonical side), never as mismatches.
"""
from __future__ import annotations

import os  # DSNs from tools/dev_env.sh, auth via PGPASSFILE (I-4)
import argparse
import json
import re
import unicodedata

import psycopg

QA_DSN = os.environ["HEIMDAL_QA_DB"]
PILOT_DSN = os.environ["HEIMDAL_PDFHIST_DB"]


_LEGAL_FORMS = ("p/f", "sp/f", "t/f", "i/f", "a/s", "hf", "v/s")


def _squash(s) -> str:
    return re.sub(r"\s+", " ",
                  unicodedata.normalize("NFC", str(s or ""))).strip().lower()


def _name_core(s) -> str:
    """Name without legal-form tokens and address tail (canonical
    raw_display_name is padded with form + address in parentheses)."""
    core = _squash(s).split(" (")[0]
    tokens = [t for t in core.replace(",", " ").split()
              if t not in _LEGAL_FORMS]
    return " ".join(tokens)


def compare(qa_dsn: str = QA_DSN, pilot_dsn: str = PILOT_DSN,
            run_id: int | None = None) -> dict:
    """Report-only comparison (section 37). Identity = digit-normalized
    registration_number (canonical tokens carry an 'S' prefix; report format
    differences separately from real mismatches)."""
    with psycopg.connect(pilot_dsn, autocommit=True) as p:
        p.execute("SET default_transaction_read_only = on")
        if run_id is None:
            row = p.execute(
                "SELECT projection_run_id FROM "
                "registry_projection.projection_run WHERE status='ACTIVE'"
            ).fetchone()
            if row is None:
                return {"compared": 0, "note": "no ACTIVE projection"}
            run_id = row[0]
        proj = dict(((_norm(r[1]), r[0]) for r in p.execute(
            "SELECT company_id, registration_number FROM "
            "registry_projection.company_current_state WHERE "
            "projection_run_id=%s", (run_id,)).fetchall()))
        names = dict(((_norm(cr), (vj.get("value") if isinstance(vj, dict) else vj)) for cr, vj in p.execute(
            "SELECT cs.registration_number, f.value_json FROM "
            "registry_projection.company_current_state cs "
            "JOIN registry_projection.company_current_state_field f "
            " ON f.projection_run_id=cs.projection_run_id"
            " AND f.company_id=cs.company_id WHERE cs.projection_run_id=%s "
            "AND f.field_name='company_name'",
            (run_id,)).fetchall()))
    with psycopg.connect(qa_dsn, autocommit=True) as q:
        q.execute("SET default_transaction_read_only = on")
        inv = dict(((_norm(r[0]), (r[0], r[1])) for r in q.execute(
            "SELECT registration_number, raw_display_name FROM "
            "company_inventory").fetchall()))
    format_only, real, same, missing = [], [], 0, 0
    for dig, company_id in proj.items():
        inv_row = inv.get(dig)
        if inv_row is None:
            missing += 1
            continue
        canon_regnr, canon_name = inv_row
        proj_name = names.get(dig)
        if proj_name and canon_name:
            if _name_core(proj_name) == _name_core(canon_name):
                same += 1
            elif _name_core(proj_name) and (
                    _name_core(proj_name) in _squash(canon_name)
                    or _name_core(canon_name) in _squash(proj_name)):
                same += 1  # canonical raw_display_name is padded with c/o etc.
            else:
                real.append({"projection_company_id": company_id,
                             "registration_number":
                                 {"projection": proj_name and dig,
                                  "canonical": canon_regnr},
                             "company_name": {"projection": proj_name,
                                              "canonical": canon_name}})
        if canon_regnr != dig:
            format_only.append({"canonical": canon_regnr, "digits": dig})
    return {"projection_run_id": run_id, "compared": len(proj),
            "identical_or_padded_name": same,
            "real_name_differences": real,
            "canonical_missing": missing,
            "registration_number_format_only_differences":
                len(format_only),
            "format_examples": format_only[:5],
            "identity_rule": "digit-normalized registration_number",
            "not_comparable_fields": ["company_address", "capital", "board",
                                      "management", "purpose",
                                      "signing_rules", "accounts_dates",
                                      "snapshot_date",
                                      "(no canonical current-state source "
                                      "exists in Heimdal yet)"]}


def _norm(x) -> str:
    return re.sub(r"\D", "", str(x or ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = compare(run_id=args.run_id)
    text = json.dumps(result, indent=1, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text[:4000])


if __name__ == "__main__":
    main()