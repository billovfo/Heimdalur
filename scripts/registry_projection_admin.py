"""Admin CLI for the registry projection layer (task section 60).

Commands cover the full mechanical lifecycle:

    apply-migration     create/upgrade the registry_projection schema
    build-shadow        build a NEW SHADOW generation from evidence
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
import json

import psycopg

from heimdal.pdfhist import flags, projection


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
            projection_version=args.version, notes=args.notes or "")
        conn.commit()
        v = projection.validate(conn, run_id)
        counts = projection.counts(conn, run_id)
    print(json.dumps({"projection_run_id": run_id, "status": "SHADOW",
                      "validation": v, "counts": counts},
                     indent=1, ensure_ascii=False))


def cmd_validate(args) -> None:
    with _connect(args) as conn:
        print(json.dumps(projection.validate(conn, args.run_id), indent=1,
                         ensure_ascii=False))


def cmd_activate(args) -> None:
    with _connect(args) as conn:
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
    p.set_defaults(func=cmd_build_shadow)
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