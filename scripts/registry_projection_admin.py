"""Admin CLI for the registry projection layer (task section 60).

Commands cover the full mechanical lifecycle:

    apply-migration     create/upgrade the registry_projection schema
    build-shadow        build a NEW SHADOW generation from evidence
    reparse             re-parse stored text with the current parser version
    build-links         build entity links for one SHADOW run
    validate            run the section-47 validation for a run id
    activate            SHADOW -> ACTIVE (atomic; refuses non-SHADOW and runs
                        that fail validate(), D-07)
    retire              ACTIVE/SHADOW -> RETIRED
    rollback            RETIRED generation -> ACTIVE (current ACTIVE retires)
    delete-non-active   delete SHADOW/FAILED/RETIRED (never ACTIVE, never evidence)
    status              list runs + settings + active run
    enable-integration / disable-integration    (DB mirror; env overrides)
    enable-ui / disable-ui

No admin UI in this phase - CLI is sufficient (task section 60). No command
auto-activates a projection (section 65). LIVE SOURCE REQUESTS = 0 (section 57).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import time

import psycopg
from psycopg.types.json import Json

from heimdal.pdfhist import consumer, entity_links, flags, ingest, projection


def _connect(args) -> psycopg.Connection:
    return psycopg.connect(args.dsn or projection.dsn())


def cmd_apply_migration(args) -> None:
    with _connect(args) as conn:
        stmts = projection.apply_migration(conn)
        conn.commit()
    print(json.dumps({"statements_executed": len(stmts)}))


def cmd_build_shadow(args) -> None:
    if args.cross_run and args.source_run is not None:
        raise SystemExit("--cross-run excludes --source-run")
    if not args.cross_run and args.source_run is None:
        raise SystemExit("either --source-run or --cross-run is required")
    with _connect(args) as conn:
        run_id = projection.build_shadow(
            conn, source_collection_run_id=args.source_run,
            cross_run=args.cross_run,
            projection_version=args.version, notes=args.notes or "",
            parser_version=args.parser_version)
        conn.commit()
        v = projection.validate(conn, run_id)
        counts = projection.counts(conn, run_id)
    print(json.dumps({"projection_run_id": run_id, "status": "SHADOW",
                      "validation": v, "counts": counts},
                     indent=1, ensure_ascii=False))


def _parser_version_guard(parser_version: str) -> None:
    if parser_version != ingest.PARSER_VERSION_DEFAULT:
        raise SystemExit(
            f"reparse refused: parser version {parser_version!r} is not the "
            f"code default {ingest.PARSER_VERSION_DEFAULT!r}")


def _reparse(conn, parser_version: str, batch_size: int = 500) -> dict:
    """Re-parse stored text rows that lack the current parser version.

    Only INSERTS pdf_parse_result rows (ON CONFLICT DO NOTHING); never touches
    an existing row, the observation, PDF bytes or the network. One
    transaction per batch. Observations that already carry the target version
    are excluded up front (NOT EXISTS), so a stopped run resumes without
    redoing committed batches.
    """
    _parser_version_guard(parser_version)
    started = time.monotonic()
    summary = {"seen": 0, "inserted": 0, "already_present": 0,
               "skipped_empty": 0, "skipped_error": []}
    last_id = 0
    while True:
        rows = conn.execute(
            "SELECT DISTINCT ON (o.id) o.id, d.pdf_type, p.raw_text "
            "FROM pdf_document_observation o "
            "JOIN pdf_document d ON d.id = o.document_id "
            "JOIN pdf_parse_result p ON p.document_observation_id = o.id "
            "WHERE p.parser_version <> %s AND o.id > %s "
            "AND NOT EXISTS (SELECT 1 FROM pdf_parse_result q "
            "WHERE q.document_observation_id = o.id "
            "AND q.parser_version = %s) "
            "ORDER BY o.id, p.id DESC LIMIT %s",
            (parser_version, last_id, parser_version, batch_size)).fetchall()
        if not rows:
            conn.commit()
            break
        batch_inserted = batch_present = batch_skipped = 0
        try:
            for observation_id, pdf_type, raw_text in rows:
                summary["seen"] += 1
                if not raw_text:
                    summary["skipped_empty"] += 1
                    batch_skipped += 1
                    continue
                try:
                    parser = ingest._parser_for(pdf_type, None)
                    struct, parse_status, needs_review, review_reason = \
                        ingest.parse_extracted_text(parser, raw_text)
                except Exception as exc:  # noqa: BLE001 - recorded per row
                    summary["skipped_error"].append(
                        {"observation_id": observation_id,
                         "error_type": type(exc).__name__,
                         "error": str(exc)})
                    batch_skipped += 1
                    continue
                cursor = conn.execute(
                    "INSERT INTO pdf_parse_result (document_observation_id,"
                    " parser_version, document_kind, raw_text, structured_data,"
                    " parse_status, needs_review, review_reason)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
                    " ON CONFLICT (document_observation_id, parser_version)"
                    " DO NOTHING",
                    (observation_id, parser_version,
                     struct.get("document_kind"), raw_text, Json(struct),
                     parse_status, needs_review, review_reason))
                if cursor.rowcount == 1:
                    summary["inserted"] += 1
                    batch_inserted += 1
                elif cursor.rowcount == 0:
                    summary["already_present"] += 1
                    batch_present += 1
                else:
                    raise RuntimeError(
                        "reparse insert returned unexpected rowcount "
                        f"{cursor.rowcount} for observation {observation_id}")
            if batch_inserted + batch_present != len(rows) - batch_skipped:
                raise RuntimeError(
                    "reparse batch rowcount mismatch: "
                    f"inserted={batch_inserted} present={batch_present} "
                    f"skipped={batch_skipped} rows={len(rows)}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        last_id = rows[-1][0]
    summary["duration_seconds"] = round(time.monotonic() - started, 3)
    return summary


def cmd_reparse(args) -> None:
    _parser_version_guard(args.parser_version)
    with _connect(args) as conn:
        summary = _reparse(conn, args.parser_version)
    print(json.dumps(summary, indent=1, ensure_ascii=False))


def cmd_build_links(args) -> None:
    # Review #3: HEIMDAL_QA_DB is required explicitly. Falling back to
    # HEIMDAL_READ_DB (or the old localhost/heimdal default) could build links
    # against another database than this repo's canonical state.
    canonical_dsn = os.environ.get("HEIMDAL_QA_DB", "")
    if not canonical_dsn:
        raise SystemExit(
            "build-links refused: HEIMDAL_QA_DB is not set; refusing implicit "
            "DSN")
    with contextlib.ExitStack() as stack:
        projection_conn = stack.enter_context(
            psycopg.connect(consumer.projection_dsn()))
        # Review #3: the canonical side is read-only even when the DSN carries
        # a superuser.
        canonical_conn = stack.enter_context(psycopg.connect(
            canonical_dsn,
            options="-c default_transaction_read_only=on"))
        try:
            stats = entity_links.build_entity_links(
                args.run_id, projection_conn=projection_conn,
                canonical_conn=canonical_conn)
        except (RuntimeError, ValueError) as exc:
            raise SystemExit(f"build-links refused: {exc}") from None
        databases = {
            "projection": projection_conn.execute(
                "SELECT current_database()").fetchone()[0],
            "canonical": canonical_conn.execute(
                "SELECT current_database()").fetchone()[0],
        }
        projection_conn.commit()
        canonical_conn.rollback()
    print(json.dumps({
        "run_id": args.run_id,
        "databases": databases,
        "link_count_by_tier": {
            tier: sum(stats[tier].values())
            for tier in ("COMPANY", "PERSON") if tier in stats},
        "stats": stats,
    }, indent=1, ensure_ascii=False, default=str))


def cmd_validate(args) -> None:
    with _connect(args) as conn:
        print(json.dumps(projection.validate(conn, args.run_id), indent=1,
                         ensure_ascii=False))


def cmd_activate(args) -> None:
    with _connect(args) as conn:
        run = conn.execute(
            "SELECT status FROM registry_projection.projection_run "
            "WHERE projection_run_id = %s", (args.run_id,)).fetchone()
        if run is None:
            raise SystemExit(f"activate refused: unknown run {args.run_id}")
        links = conn.execute(
            "SELECT count(*) FROM registry_projection.entity_link "
            "WHERE projection_run_id = %s", (args.run_id,)).fetchone()[0]
        if links == 0:
            raise SystemExit(
                f"activate refused: run {args.run_id} has 0 entity_link "
                f"rows; run build-links --run-id {args.run_id} first")
        try:  # activate() runs validate() and refuses a failing run (D-07)
            projection.activate(conn, args.run_id)
        except ValueError as exc:
            raise SystemExit(f"activate refused: {exc}") from None
        conn.commit()
    print(json.dumps({"activated": args.run_id}))


def cmd_retire(args) -> None:
    with _connect(args) as conn:
        projection.retire(conn, args.run_id)
        conn.commit()
    print(json.dumps({"retired": args.run_id}))


def cmd_rollback(args) -> None:
    with _connect(args) as conn:
        projection.rollback_to(conn, args.run_id)
        conn.commit()
    print(json.dumps({"rolled_back_to": args.run_id}))


def cmd_delete_non_active(args) -> None:
    with _connect(args) as conn:
        projection.delete_non_active(conn, args.run_id)
        conn.commit()
    print(json.dumps({"deleted": args.run_id}))


def cmd_status(args) -> None:
    with _connect(args) as conn:
        runs = conn.execute(
            "SELECT projection_run_id, projection_version, status, "
            "created_at, completed_at, source_collection_run_id, "
            "code_version, content_hash FROM "
            "registry_projection.projection_run ORDER BY projection_run_id"
        ).fetchall()
        settings = dict(conn.execute(
            "SELECT key, value_json FROM "
            "registry_projection.projection_setting").fetchall())
    print(json.dumps({
        "runs": [{"projection_run_id": r[0], "projection_version": r[1],
                  "status": r[2], "created_at": str(r[3]),
                  "completed_at": str(r[4]), "source_run": r[5],
                  "code_version": r[6], "content_hash": r[7]} for r in runs],
        "settings": {k: v for k, v in settings.items()},
        "env_overrides": {
            "PDF_HISTORY_INTEGRATION_ENABLED":
                __import__("os").environ.get(
                    "PDF_HISTORY_INTEGRATION_ENABLED"),
            "PDF_HISTORY_UI_ENABLED":
                __import__("os").environ.get("PDF_HISTORY_UI_ENABLED")},
    }, indent=1, ensure_ascii=False, default=str))


def cmd_flag(args, key: str, enabled: bool) -> None:
    with _connect(args) as conn:
        if key == "integration_enabled":
            projection.set_integration_enabled(conn, enabled)
        else:
            projection.set_ui_enabled(conn, enabled)
        conn.commit()
    print(json.dumps({"setting": key, "value": enabled,
                      "note": "DB mirror; environment variable overrides"}))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", default=None,
                    help="PDF-history DB DSN (default HEIMDAL_PDFHIST_DB)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("apply-migration").set_defaults(func=cmd_apply_migration)
    p = sub.add_parser("build-shadow")
    p.add_argument("--source-run", type=int, default=None)
    p.add_argument("--cross-run", action="store_true",
                   help="latest VERIFIED snapshot per company across ALL "
                        "approved runs (v2)")
    p.add_argument("--version", required=True)
    p.add_argument("--notes", default="")
    p.add_argument("--parser-version", default=None,
                   help="pin every pdf_parse_result join to this parser "
                        "version (default: unpinned, legacy behaviour)")
    p.set_defaults(func=cmd_build_shadow)
    p = sub.add_parser("reparse")
    p.add_argument("--parser-version", required=True,
                   help="target parser version; must equal "
                        "PARSER_VERSION_DEFAULT")
    p.set_defaults(func=cmd_reparse)
    p = sub.add_parser(
        "build-links",
        help="build entity links for one SHADOW run",
        description="Build entity links for one SHADOW run. The projection "
                    "connection comes from HEIMDAL_PDFHIST_PROJECTION_DSN and "
                    "the canonical read-only connection from HEIMDAL_QA_DB; "
                    "the global --dsn flag is not used.")
    p.add_argument("--run-id", type=int, required=True)
    p.set_defaults(func=cmd_build_links)
    p = sub.add_parser("validate")
    p.add_argument("--run-id", type=int, required=True)
    p.set_defaults(func=cmd_validate)
    p = sub.add_parser("activate")
    p.add_argument("--run-id", type=int, required=True)
    p.set_defaults(func=cmd_activate)
    p = sub.add_parser("retire")
    p.add_argument("--run-id", type=int, required=True)
    p.set_defaults(func=cmd_retire)
    p = sub.add_parser("rollback")
    p.add_argument("--run-id", type=int, required=True)
    p.set_defaults(func=cmd_rollback)
    p = sub.add_parser("delete-non-active")
    p.add_argument("--run-id", type=int, required=True)
    p.set_defaults(func=cmd_delete_non_active)
    sub.add_parser("status").set_defaults(func=cmd_status)
    p = sub.add_parser("enable-integration")
    p.set_defaults(func=lambda a: cmd_flag(a, "integration_enabled", True))
    p = sub.add_parser("disable-integration")
    p.set_defaults(func=lambda a: cmd_flag(a, "integration_enabled", False))
    p = sub.add_parser("enable-ui")
    p.set_defaults(func=lambda a: cmd_flag(a, "ui_enabled", True))
    p = sub.add_parser("disable-ui")
    p.set_defaults(func=lambda a: cmd_flag(a, "ui_enabled", False))

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()