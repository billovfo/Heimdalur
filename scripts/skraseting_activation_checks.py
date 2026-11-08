#!/usr/bin/env python3
"""Evidence/projection/canonical checksums for the Skráseting UI activation.

Checksum methods (identical for BEFORE and AFTER -> UNCHANGED proof):

- evidence tables: streaming sha256 row-chain
      h_0 = sha256(b'')
      h_i = sha256(h_{i-1}.digest() + row_text_i)
      md5 = md5(h_final.digest())
  rows ordered by primary key; row_text = str(tuple(row)) as returned by
  psycopg. Used because pdf_document_content is far too large for the
  server-side string_agg recipe (OOM) and streaming is deterministic.

- canonical tables: the BINDING documented recipe
      md5(string_agg(t::text, '|' ORDER BY t::text))
  (docs/HEIMDAL-PDF-HISTORIK-FINAL-CLOSURE-AUDIT-v1.md §2) which reproduces
  the known references (a927fde…, 008170ae…, e84821d6…).

Usage: python3 scripts/skraseting_activation_checks.py evidence|projection|canonical <out.json>
"""
import hashlib
import json
import sys

import psycopg

sys.path.insert(0, ".")

from heimdal.pdfhist import consumer, db  # noqa: E402

EVIDENCE_TABLES = [
    "pdf_run_log",
    "pdf_source_event",
    "pdf_document",
    "pdf_document_content",
    "pdf_document_observation",
    "pdf_source_event_document",
    "pdf_parse_result",
    "company_pdf_snapshot_history",
]
EVIDENCE_PKS = {
    "pdf_run_log": "id",
    "pdf_source_event": "id",
    "pdf_document": "id",
    "pdf_document_content": "sha256",
    "pdf_document_observation": "id",
    "pdf_source_event_document": "id",
    "pdf_parse_result": "id",
    "company_pdf_snapshot_history": "id",
}
CANONICAL_TABLES = ["company_inventory", "company_location", "canonical_entity"]
V2_HASH = "542b763ec8c8740625d881a55eccf4cec74e6e9348d7856c70915f64a3e96712"


def _pk(table: str) -> str:
    conn = psycopg.connect(consumer.projection_dsn())
    cols = [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name=%s ORDER BY ordinal_position", (table,)).fetchall()]
    for candidate in ("id", "sha256"):
        if candidate in cols:
            return candidate
    return cols[0]


def evidence_checksums() -> dict:
    conn = psycopg.connect(consumer.projection_dsn())
    out = {"method": ("streaming sha256 row-chain h_i=sha256(h_prev+row_text), "
                      "PK order, md5(final digest); row_text=str(tuple(row))")}
    for table in EVIDENCE_TABLES:
        pk = _pk(table)
        cur = conn.cursor(name=f"ck_{table}")
        cur.itersize = 5000
        cur.execute(f"SELECT * FROM {table} ORDER BY {pk}")
        h = hashlib.sha256()
        n = 0
        for row in cur:
            h = hashlib.sha256(h.digest() + str(tuple(row)).encode())
            n += 1
        cur.close()
        out[table] = {"rows": n, "md5": hashlib.md5(h.digest()).hexdigest()}
        print(f"  {table}: {n} rows")
    return out


def projection_state() -> dict:
    conn = psycopg.connect(consumer.projection_dsn())
    setting = dict(conn.execute(
        "SELECT key, value_json FROM registry_projection.projection_setting"
    ).fetchall())
    run = conn.execute(
        "SELECT projection_run_id, status, projection_version, content_hash "
        "FROM registry_projection.projection_run WHERE projection_run_id=4"
    ).fetchone()
    counts = {}
    for label, q in [
        ("company_current_state",
         "SELECT count(*) FROM registry_projection.company_current_state "
         "WHERE projection_run_id=4"),
        ("company_current_state_field",
         "SELECT count(*) FROM registry_projection.company_current_state_field "
         "WHERE projection_run_id=4"),
        ("company_history_event",
         "SELECT count(*) FROM registry_projection.company_history_event "
         "WHERE projection_run_id=4"),
        ("company_person_role",
         "SELECT count(*) FROM registry_projection.company_person_role "
         "WHERE projection_run_id=4"),
        ("projection_warning",
         "SELECT count(*) FROM registry_projection.projection_warning "
         "WHERE projection_run_id=4"),
        ("projection_runs_total",
         "SELECT count(*) FROM registry_projection.projection_run"),
        ("active_runs",
         "SELECT count(*) FROM registry_projection.projection_run "
         "WHERE status='ACTIVE'"),
    ]:
        counts[label] = conn.execute(q).fetchone()[0]
    return {
        "method": "count(*) + projection_run row for run 4",
        "settings": setting,
        "run_4": {"projection_run_id": run[0], "status": run[1],
                  "projection_version": run[2], "content_hash": run[3]},
        "counts": counts,
    }


def canonical_checksums() -> dict:
    conn = psycopg.connect(db.qa_dsn())
    out = {"method": "binding recipe md5(string_agg(t::text,'|' ORDER BY t::text))"}
    for table in CANONICAL_TABLES:
        n = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        md5 = conn.execute(
            f"SELECT md5(string_agg(t::text, '|' ORDER BY t::text)) "
            f"FROM (SELECT * FROM {table}) AS t").fetchone()[0]
        out[table] = {"rows": n, "md5": md5}
        print(f"  {table}: {n} rows {md5[:12]}…")
    return out


def main() -> None:
    mode, out_path = sys.argv[1], sys.argv[2]
    if mode == "evidence":
        data = evidence_checksums()
    elif mode == "projection":
        data = projection_state()
    elif mode == "canonical":
        data = canonical_checksums()
    else:
        raise SystemExit(f"unknown mode {mode}")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True, default=str)
    print(f"-> {out_path}")


if __name__ == "__main__":
    main()