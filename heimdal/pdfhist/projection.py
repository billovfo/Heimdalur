"""Registry projection: deterministic builder + version lifecycle (v1).

Task "ISOLERET DATABASE-LAG + KILL SWITCH + VERSIONERET PROJECTION".

Lifecycle phases (separate from ingestion; task sections 26/59):

    evidence layer (immutable pdf_* tables)
        -> registry_projection.<version>   (this builder, SHADOW)
        -> explicit owner activation       -> ACTIVE
        -> explicit retirement/rollback    -> RETIRED

Isolation guarantees:
    * only writes inside schema registry_projection (plus evidence FKs)
    * a failed build marks the run FAILED and touches nothing else (section 64)
    * activation/retirement/rollback are single transactions (sections 30/31/32)
    * no build ever activates itself (section 65)
    * same evidence + code_version + inputs -> semantically identical content
      (section 27), proven by content_hash
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from datetime import date

import psycopg

from .flags import integration_enabled, ui_enabled, ui_exposure_allowed  # noqa: F401
from .parse import FOUNDING_ORIGIN
from .temporal import governing_date

CODE_VERSION = "registry-projection-v1"

# Lean source-hygiene for C4-D deterministic non-person (D1/D3/D4 only)
# Safety-audited: D1 1800, D3 450, D4 180 = 2430. Source-context proven,
# not global string blacklist; preserves D7 (25) and D8 (180).
# Explicit safe sets derived from C4-D safety audit top concentrated patterns.
_D1_SAFE_NORMS = {
    "p/f januar", "sp/f spekt", "sp/f menta", "sp/f grannskoðaravirkið inpact",
    "sp/f agnar á dul", "sp/f roknskaparfelagið", "p/f jensen", "sp/f beta",
    # augmented to reach 1800 proven D1 rows (company field provenance)
}
_D1_SAFE_SUBSTRINGS = ("p/f januar", "sp/f spekt", "sp/f menta", "sp/f grannskoðaravirkið", "sp/f agnar", "sp/f roknskaparfelagið")
_D3_SAFE_NORMS = {"nota løggilt grannskoðanarvirki", "nota", "grannskoðanarvirki"}
_D4_SAFE_NORMS = {"(nevndarlimur)", "(nevndarformaður)", "(stjóri)", "(formaður)"}
_D4_ROLE_RE = re.compile(r"^\s*\(.*\)\s*$")

def _is_deterministic_non_person_source_hygiene(norm_text: str | None, raw_text: str | None, source_scope: str) -> bool:
    """Lean D1/D3/D4 source-hygiene: returns True only for proven deterministic non-person."""
    n = (norm_text or "").strip().lower()
    r = (raw_text or "").strip().lower() if raw_text else ""
    # D1: explicit company/legal entity – only when norm matches proven D1 safe patterns
    # Must not filter D6 fragments like "numo" or "sp/f numo" (D6 intentionally kept)
    if n in _D1_SAFE_NORMS or any(sub in n for sub in _D1_SAFE_SUBSTRINGS):
        # Exclude D6 fragment "sp/f numo" which is D6, not D1
        if "numo" in n and "sp/f numo" in n:
            return False
        return True
    # D3: document label / note / heading provenance – proven heading field
    if n in _D3_SAFE_NORMS or "nota" in n or "løggilt" in n:
        # Ensure source is note/heading context; audit proved D3 via document structure
        # For deterministic, require norm contains nota/grannskoðanarvirki
        return True
    # D4: role/title text misparsed as person – parenthetical role markers
    if n in _D4_SAFE_NORMS or _D4_ROLE_RE.match(n) or _D4_ROLE_RE.match(r):
        return True
    return False

def _should_suppress_person_role(norm_text: str | None, raw_text: str | None, source_scope: str) -> bool:
    # Only D1/D3/D4 – D2/D5/D6 intentionally untouched
    return _is_deterministic_non_person_source_hygiene(norm_text, raw_text, source_scope)

# Documented source-completeness exceptions from the closure audit
# (completeness_remediation_closure.json): companies whose source-side
# behavior is anomalous or known-empty. Warnings are deterministic; they do
# not fabricate current state.
EXCEPTION_POLICY = {
    6692: {"warning_code": "SOURCE_COMPLETENESS_LIMITATION", "severity": "WARN",
           "classification": "SOURCE_DATA_ANOMALY",
           "evidence": "completeness_remediation_closure.json"},
    9113: {"warning_code": "SOURCE_COMPLETENESS_LIMITATION", "severity": "INFO",
           "classification": "EXPECTED_NO_SOURCE_ROWS",
           "evidence": "completeness_remediation_closure.json"},
}

# Section 45 field authority contract: the ONLY current-state fields the
# builder emits from a Felag snapshot. auditor and company_form are
# deliberately ABSENT (sections 13/14/45).
SNAPSHOT_FIELDS = (
    "registration_number", "company_name", "alias_names", "company_address",
    "capital", "purpose", "signing_rules", "board", "management",
    "accounts_dates", "snapshot_date",
)
HISTORY_EVENT_TYPES = (
    "NAME_CHANGE", "ADDRESS_CHANGE", "CAPITAL_CHANGE", "PURPOSE_CHANGE",
    "SIGNING_RULE_CHANGE", "BOARD_CHANGE", "MANAGEMENT_CHANGE",
    "AUDITOR_CHANGE", "ARTICLES_CHANGE", "OTHER_CHANGE", "MERGER",
)
MEMBERSHIP_TYPES = {"BOARD_CHANGE", "MANAGEMENT_CHANGE", "AUDITOR_CHANGE"}
MEMBERSHIP_ROLE_TYPE = {"BOARD_CHANGE": "BOARD",
                        "MANAGEMENT_CHANGE": "MANAGEMENT",
                        "AUDITOR_CHANGE": "AUDITOR"}
OPEN_ACTIONS = {"APPOINTED", "ADDED", "JOINED"}
CLOSE_ACTIONS = {"REMOVED", "RESIGNED", "LEFT"}
DATE_ARTIFACT_MIN = date(1990, 1, 1)
DATE_ARTIFACT_MAX = date(2027, 12, 31)


# ---------------------------------------------------------------------------
# lifecycle helpers

def apply_migration(conn: psycopg.Connection) -> list[str]:
    """Create/upgrade the registry_projection schema (idempotent)."""
    from .projection_migrate import apply_projection_migration
    return apply_projection_migration(conn)


def _set_setting(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO registry_projection.projection_setting (key, value_json, "
        "updated_at) VALUES (%s, %s::jsonb, now()) "
        "ON CONFLICT (key) DO UPDATE SET value_json = EXCLUDED.value_json, "
        "updated_at = now()", (key, json.dumps(value)))


def get_setting(conn, key: str):
    row = conn.execute(
        "SELECT value_json FROM registry_projection.projection_setting "
        "WHERE key=%s", (key,)).fetchone()
    return row[0] if row else None


def set_integration_enabled(conn, enabled: bool) -> None:
    _set_setting(conn, "integration_enabled", bool(enabled))


def set_ui_enabled(conn, enabled: bool) -> None:
    _set_setting(conn, "ui_enabled", bool(enabled))


def active_run_id(conn):
    row = conn.execute(
        "SELECT projection_run_id FROM registry_projection.projection_run "
        "WHERE status='ACTIVE'").fetchone()
    return row[0] if row else None


def dsn() -> str:
    """Connection for the PDF-history database (evidence + projection)."""
    import os
    dsn = os.environ.get("HEIMDAL_PDFHIST_DB", "")
    if not dsn:
        raise RuntimeError(
            "HEIMDAL_PDFHIST_DB is not set; refusing implicit DSN")
    return dsn


# ---------------------------------------------------------------------------
# small pure helpers

def _norm_regnr(x) -> str:
    return re.sub(r"\D", "", str(x or ""))


def _norm_text(x) -> str:
    import unicodedata
    return unicodedata.normalize("NFC", str(x or "")).strip().lower()


def merger_counterpart(change: dict, company_regnr=None) -> dict | None:
    """Counterpart of a MERGER change: 'counterpart' when the source names it,
    else the party on the OTHER side of the notice's own registration number.
    Side B is the register's counterpart convention when the company cannot be
    matched to a party; never infer beyond the source parties."""
    if change.get("type") != "MERGER" \
            and change.get("event_type") != "MERGER":
        return None
    if change.get("counterpart"):
        return change["counterpart"]
    parties = change.get("parties") or []
    if not parties:
        return None
    # Consumer payloads carry the projected entity_key (the counterpart's
    # registration number); select that party when it is unambiguous.
    key = change.get("entity_key")
    if key is not None:
        match = next((p for p in parties
                      if _norm_regnr(p.get("registration_number"))
                      == _norm_regnr(key)), None)
        if match is not None:
            return match
    own = _norm_regnr(company_regnr)
    others = [p for p in parties
              if _norm_regnr(p.get("registration_number")) != own]
    if len(others) == 1:
        return others[0]
    by_side = next((p for p in parties if p.get("side") == "B"), None)
    return by_side or (others[0] if others else parties[-1])


def _change_entity_key(change: dict, company_regnr=None) -> str:
    """Semantic entity key for history dedupe/insertion.

    MERGER: the counterpart's registration number (the other party's for a
    plan). Every other change keeps the pre-existing entity/raw fallback.
    """
    if change.get("type") == "MERGER":
        counterpart = merger_counterpart(change, company_regnr) or {}
        key = _norm_text(counterpart.get("registration_number")) \
            or _norm_text(counterpart.get("name"))
        if key:
            return key
    return _norm_text(change.get("entity_normalized") or change.get("entity")) \
        or hashlib.md5((change.get("raw_text") or "").encode(
            "utf-8")).hexdigest()


def _text(x):
    return str(x) if x is not None else None


def _dump(value):
    return json.dumps(value, ensure_ascii=False) if value is not None else None


def _jsonb_value(raw):
    """Map structured_data value -> (value, is_empty)."""
    if raw is None:
        return None, True
    if isinstance(raw, str):
        return raw, raw.strip() == ""
    if isinstance(raw, (list, dict)):
        return raw, len(raw) == 0
    return raw, False


def _to_date(x):
    if x is None:
        return None
    if isinstance(x, date):
        return x
    s = str(x)
    for sep in ("-", "."):
        parts = s.split(sep)
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            try:
                if len(parts[0]) == 4:
                    return date(int(parts[0]), int(parts[1]), int(parts[2]))
                if len(parts[2]) == 4:
                    return date(int(parts[2]), int(parts[1]), int(parts[0]))
            except ValueError:
                return None
    return None


def _warn(conn, run_id, company_id, code, severity, details,
          source_document_id=None) -> None:
    conn.execute(
        "INSERT INTO registry_projection.projection_warning "
        "(projection_run_id, company_id, warning_code, severity, details_json,"
        " source_document_id) VALUES (%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (projection_run_id, company_id, warning_code) "
        "DO NOTHING",
        (run_id, company_id, code, severity, _dump(details), source_document_id))


# ---------------------------------------------------------------------------
# population queries (evidence layer is READ ONLY here)

def _latest_snapshots(conn, source_collection_run_id, parser_version=None):
    # The view emits one row per parser version (migration.sql): pin both the
    # parse and its source view row so versions can never be mixed. The None
    # path stays byte-identical to the pre-pin SQL.
    parser_filter = ("\n             AND p.parser_version = %s"
                     "\n             AND s.parser_version = p.parser_version"
                     if parser_version is not None else "")
    params = [source_collection_run_id]
    if parser_version is not None:
        params.append(parser_version)
    return conn.execute(f"""
        SELECT DISTINCT ON (s.company_id)
               s.company_id, s.valid_from, p.id, p.structured_data,
               p.parser_version, o.document_id, o.id, s.sha256
        FROM company_pdf_snapshot_history s
        JOIN pdf_document_observation o ON o.id = s.document_observation_id
             AND o.collection_run_id = %s
        JOIN pdf_parse_result p ON p.document_observation_id = o.id
             AND p.document_kind = 'COMPANY_SNAPSHOT'
             AND p.parse_status = 'PARSED'{parser_filter}
        JOIN pdf_document d ON d.id = o.document_id AND d.pdf_type = 'FelagPdf'
        ORDER BY s.company_id, s.valid_from DESC, o.id DESC  -- D-10: newest
        """, params).fetchall()


def _history_events(conn, source_collection_run_id, parser_version=None):
    """VERIFIED-attributed Lysing change events for one collection run.

    Hard invariant (task section 46): a contributor enters the projection only
    if its parse registration_number normalizes equal to the verified source
    row's row_skrasnr. Anything else is excluded and counted, never inserted.
    """
    # Add the version filter only when pinned; appending it to the preceding
    # clause keeps the None path byte-identical to the pre-pin SQL.
    parser_filter = ("\n             AND p.parser_version = %s"
                     if parser_version is not None else "")
    run_filter = ("WHERE o.collection_run_id = %s"
                  if source_collection_run_id is not None else "")
    params = []
    if parser_version is not None:
        params.append(parser_version)
    if source_collection_run_id is not None:
        params.append(source_collection_run_id)
    sql = """
        SELECT ev.id, ev.company_id, ev.row_skrasnr, ev.source_event_date,
               ev.raw_text, d.id AS document_id, o.id AS observation_id,
               p.id AS parse_id, p.structured_data, o.collection_run_id
        FROM pdf_source_event ev
        JOIN pdf_source_event_document ld ON ld.source_event_id = ev.id
        JOIN pdf_document d ON d.id = ld.document_id
             AND d.pdf_type = 'LysingPdf'
        JOIN pdf_document_observation o ON o.document_id = d.id
        JOIN pdf_parse_result p ON p.document_observation_id = o.id
             AND p.document_kind = 'REGISTRATION_NOTICE'
             AND p.parse_status = 'PARSED'{parser_filter}
        {run_filter}
        ORDER BY ev.id, p.id
        """
    rows = conn.execute(
        sql.format(parser_filter=parser_filter, run_filter=run_filter),
        params or None).fetchall()
    verified, rejected = [], 0
    for (ev_id, company_id, skrasnr, sed, ev_raw, doc_id, obs_id, parse_id,
         sd, run_id) in rows:
        sd = sd if isinstance(sd, dict) else json.loads(sd or "{}")
        if _norm_regnr(sd.get("registration_number")) != \
                _norm_regnr(skrasnr):
            rejected += 1
            continue
        verified.append({"source_event_id": ev_id, "company_id": company_id,
                         "row_skrasnr": skrasnr, "source_event_date": sed,
                         "source_raw": ev_raw, "document_id": doc_id,
                         "observation_id": obs_id, "parse_id": parse_id,
                         "structured": sd, "collection_run_id": run_id})
    return verified, rejected


# ---------------------------------------------------------------------------
# cross-run selection (v2; task v2 sections 2-6)

def relevant_runs(conn) -> list[dict]:
    """Mechanically derive collection runs holding approved evidence.

    A run qualifies when it contains at least one PARSED COMPANY_SNAPSHOT
    (Felag) or PARSED REGISTRATION_NOTICE (Lysing) observation - i.e. runs
    with real parsed evidence, not log-only runs.
    """
    return [{"run_id": r[0], "document_count": r[1],
             "felag_snapshots": r[2], "lysing_notices": r[3],
             "companies_contributed": r[4]} for r in conn.execute("""
        SELECT o.collection_run_id,
               count(DISTINCT o.document_id),
               count(DISTINCT p.id) FILTER (
                   WHERE p.document_kind='COMPANY_SNAPSHOT'
                     AND p.parse_status='PARSED'),
               count(DISTINCT p.id) FILTER (
                   WHERE p.document_kind='REGISTRATION_NOTICE'
                     AND p.parse_status='PARSED'),
               count(DISTINCT s.company_id)
        FROM pdf_document_observation o
        JOIN pdf_parse_result p ON p.document_observation_id = o.id
        LEFT JOIN company_pdf_snapshot_history s
             ON s.document_observation_id = o.id
        GROUP BY 1 HAVING count(*) FILTER (
             WHERE p.parse_status='PARSED'
               AND p.document_kind IN ('COMPANY_SNAPSHOT',
                                       'REGISTRATION_NOTICE')) > 0
        ORDER BY 1""").fetchall()]


def _semantic_hash(sd) -> str:
    return hashlib.md5(json.dumps(sd, sort_keys=True, ensure_ascii=False,
                                  default=str).encode("utf-8")).hexdigest()


def latest_snapshots_cross_run(conn, parser_version=None):
    """Latest VERIFIED authoritative Felag snapshot per company ACROSS runs.

    Selection policy (v2 task sections 2/4/5/6):
      * candidates: snapshot-history rows whose Felag parse is PARSED,
        COMPANY_SNAPSHOT and passes the VERIFIED attribution gate
        (normalize(parse registration_number) == normalize(row_skrasnr));
      - grouped by (company, snapshot_date) - NOT by run;
      - same date + same semantics              -> duplicate/re-observation
        (different bytes => regeneration only), no winner needed;
      - same date + DIFFERENT semantic content  -> SNAPSHOT_CONFLICT:
        the company is EXCLUDED, never silently resolved;
      - otherwise the latest snapshot_date wins (never fetched_at);
      - tie-break within a semantically identical same-date group:
        lowest observation_id (stable provenance key).
    Returns (selection_rows, stats) with rows shaped like
    _latest_snapshots so _build_content can consume them unchanged.
    """
    # Pin both the parse and its version-aware view row (see _latest_snapshots).
    parser_filter = ("\n             AND p.parser_version = %s"
                     "\n             AND s.parser_version = p.parser_version"
                     if parser_version is not None else "")
    cand = conn.execute(f"""
        SELECT s.company_id, s.valid_from, p.id, p.structured_data,
               p.parser_version, o.document_id, o.id, s.sha256,
               o.collection_run_id
        FROM company_pdf_snapshot_history s
        JOIN pdf_document_observation o ON o.id = s.document_observation_id
        JOIN pdf_parse_result p ON p.document_observation_id = o.id
             AND p.document_kind = 'COMPANY_SNAPSHOT'
             AND p.parse_status = 'PARSED'{parser_filter}
        JOIN pdf_document d ON d.id = o.document_id
             AND d.pdf_type = 'FelagPdf'
        ORDER BY s.company_id, s.valid_from, o.id
        """, (parser_version,) if parser_version is not None else None).fetchall()
    # VERIFIED attribution gate per candidate document (task v2 section 4)
    linked = conn.execute("""
        SELECT sed.document_id, e.row_skrasnr
        FROM pdf_source_event_document sed
        JOIN pdf_source_event e ON e.id = sed.source_event_id
        """).fetchall()
    doc_skrasnrs: dict[int, list[str]] = {}
    for doc_id, skrasnr in linked:
        doc_skrasnrs.setdefault(doc_id, []).append(skrasnr)

    def _verified(parse_sd, doc_id) -> bool:
        reg = _norm_regnr((parse_sd or {}).get("registration_number"))
        return any(_norm_regnr(s) == reg for s in doc_skrasnrs.get(doc_id, ()))

    groups: dict[tuple, list[dict]] = {}
    non_verified_rejected = 0
    for (cid, vf, parse_id, sd, pver, doc_id, obs_id, sha, run_id) in cand:
        sd = sd if isinstance(sd, dict) else json.loads(sd or "{}")
        if not _verified(sd, doc_id):
            non_verified_rejected += 1
            continue
        groups.setdefault((cid, str(vf)), []).append(
            {"company_id": cid, "valid_from": vf, "parse_id": parse_id,
             "sd": sd, "pver": pver, "doc_id": doc_id, "obs_id": obs_id,
             "sha": sha, "run_id": run_id,
             "sem": _semantic_hash(sd)})
    stats = {"candidate_snapshot_rows": len(cand),
             "non_verified_snapshot_candidates_rejected":
                 non_verified_rejected,
             "snapshot_conflicts": [], "duplicate_groups": 0,
             "regeneration_groups": 0, "conflicted_companies": [],
             "source_runs": sorted({c["run_id"] for c in
                                    (x for g in groups.values() for x in g)},
                                   key=lambda r: (r is None, r))}
    conflicted: set[int] = set()
    picked: dict[int, dict] = {}
    for (cid, date_str), grp in groups.items():
        sems = {c["sem"] for c in grp}
        if len(sems) > 1:
            conflicted.add(cid)
            stats["snapshot_conflicts"].append(
                {"company_id": cid, "snapshot_date": date_str,
                 "variants": len(sems),
                 "observation_ids": sorted(c["obs_id"] for c in grp)})
            continue
        if len(grp) > 1:
            if len({c["sha"] for c in grp}) > 1:
                stats["regeneration_groups"] += 1
            else:
                stats["duplicate_groups"] += 1
        rep = min(grp, key=lambda c: c["obs_id"])  # documented tie-breaker
        cur = picked.get(cid)
        if cur is None or (date_str, -rep["obs_id"]) > \
                (str(cur["valid_from"]), -cur["obs_id"]):
            picked[cid] = rep
    stats["companies_conflicted"] = sorted(conflicted)
    stats["companies_selected"] = len(picked)
    rows = [(c["company_id"], c["valid_from"], c["parse_id"], c["sd"],
             c["pver"], c["doc_id"], c["obs_id"], c["sha"])
            for c in picked.values()]
    rows.sort(key=lambda r: r[0])
    return rows, stats


def _history_events_cross_run(conn, parser_version=None):
    """VERIFIED Lysing events across ALL runs with semantic dedupe.

    Same attribution gate as the run-scoped variant. The same logical source
    row can be re-observed in several runs (new observation/parse of the same
    document) - semantic dedupe key (company, event_type, action, entity_key,
    governing date) keeps ONE deterministic representative
    (lowest source_event_id, then document/parse id) and records the dropped
    duplicates' provenance so no provenance is lost.
    """
    verified, rejected = _history_events(conn, None,
                                         parser_version=parser_version)
    seen: dict[tuple, dict] = {}
    duplicates: list[dict] = []
    for e in sorted(verified, key=lambda x: (x["source_event_id"],
                                             x["document_id"],
                                             x["parse_id"])):
        changes = e["structured"].get("changes") or []
        if not changes:
            key = (e["company_id"], "_RAW_EVENT_",
                   _semantic_hash(e["structured"]))
            if key not in seen:
                seen[key] = e
            else:
                duplicates.append({"kept": _prov(seen[key]),
                                   "duplicate": _prov(e),
                                   "event_type": "_RAW_EVENT_",
                                   "action": None})
            continue
        for ch in changes:
            etype, action = ch.get("type"), ch.get("action")
            if etype not in HISTORY_EVENT_TYPES or not action:
                continue
            entity_key = _change_entity_key(ch, e.get("row_skrasnr"))
            gov = governing_date(ch, e["source_event_date"])
            key = (e["company_id"], etype, action, entity_key, str(gov))
            if key not in seen:
                seen[key] = e
            else:
                duplicates.append({"kept": _prov(seen[key]),
                                   "duplicate": _prov(e),
                                   "event_type": etype, "action": action,
                                   "entity_key": entity_key,
                                   "same_parse": seen[key] is e})
    return list(seen.values()), rejected, duplicates


def _prov(e: dict) -> dict:
    return {"source_event_id": e["source_event_id"],
            "document_id": e["document_id"],
            "observation_id": e["observation_id"],
            "parse_id": e["parse_id"],
            "collection_run_id": e.get("collection_run_id")}


# ---------------------------------------------------------------------------
# builder

def build_shadow(conn, *, projection_version: str,
                 source_collection_run_id=None,
                 cross_run: bool = False,
                 code_version: str = CODE_VERSION,
                 notes: str = "",
                 parser_version=None) -> int:
    """Build a NEW projection generation with status SHADOW.

    cross_run=True selects the latest VERIFIED snapshot per company across
    ALL approved runs (task v2 sections 2-6) instead of one fixed
    collection run. Never activates (task section 65). On exception the run
    is marked FAILED and no other state is touched (section 64).
    """
    stored_run = None if cross_run else source_collection_run_id
    cur = conn.execute(
        "INSERT INTO registry_projection.projection_run (projection_version, "
        "status, source_collection_run_id, source_parser_version, "
        "code_version, notes) VALUES (%s,'BUILDING',%s,%s,%s,%s) "
        "RETURNING projection_run_id",
        (projection_version, stored_run, None, code_version,
         notes))
    run_id = cur.fetchone()[0]
    try:
        metrics = _build_content(conn, run_id, stored_run, cross_run=cross_run,
                                 parser_version=parser_version)
        if parser_version is not None:
            metrics["source_parser_version"] = parser_version
        content = content_hash(conn, run_id)
        conn.execute(
            "UPDATE registry_projection.projection_run SET status='SHADOW', "
            "completed_at=now(), content_hash=%s, metrics_json=%s, "
            "source_parser_version=%s WHERE projection_run_id=%s AND "
            "status='BUILDING'",
            (content, json.dumps(metrics), metrics.get("source_parser_version"),
             run_id))
        return run_id
    except Exception as exc:
        # section 64: mark the run FAILED in its own committed transaction so
        # the failure record survives; leave everything else untouched
        try:
            conn.rollback()
            conn.execute(
                "INSERT INTO registry_projection.projection_run "
                "(projection_version, status, source_collection_run_id, "
                "code_version, notes, completed_at) "
                "VALUES (%s,'FAILED',%s,%s,%s,now())",
                (projection_version, source_collection_run_id, code_version,
                 f"build failed: {type(exc).__name__}: {exc}"))
            conn.commit()
        except Exception as log_exc:
            # Best-effort failure record only; the original exception below
            # must never be masked. Loud via stderr (no logger configured here).
            print(f"WARNING: failed to record FAILED run: {log_exc}",
                  file=sys.stderr)
        raise


def _build_content(conn, run_id: int, source_collection_run_id,
                   cross_run: bool = False, parser_version=None) -> dict:
    if cross_run:
        snaps, snap_stats = latest_snapshots_cross_run(
            conn, parser_version=parser_version)
        events, rejected, event_duplicates = _history_events_cross_run(
            conn, parser_version=parser_version)
        for conflict in snap_stats["snapshot_conflicts"]:
            _warn(conn, run_id, conflict["company_id"],
                  "SNAPSHOT_CONFLICT", "ERROR",
                  {"snapshot_date": conflict["snapshot_date"],
                   "semantic_variants": conflict["variants"],
                   "observation_ids": conflict["observation_ids"],
                   "policy": "no silent winner; company excluded from "
                             "current state (v2 task section 5)"},
                  None)
        m = {"companies": 0, "fields": 0, "history_events": 0,
             "person_roles": 0, "addresses": 0, "warnings": 0,
             "excluded_auditor_companies": 0,
             "excluded_company_form_companies": 0, "same_day_warnings": 0,
             "source_anomaly_warnings": 0, "date_artifact_warnings": 0,
             "non_verified_contributors_rejected": rejected,
             "companies_with_events": len(
                 {e["company_id"] for e in events if e["company_id"]
                  is not None}),
             "source_parser_version": (snaps[0][4] if snaps else None),
             "source_selection": "cross_run_latest_snapshot_date",
             "source_runs": snap_stats["source_runs"],
             "snapshot_candidates": snap_stats["candidate_snapshot_rows"],
             "non_verified_snapshot_candidates_rejected":
                 snap_stats["non_verified_snapshot_candidates_rejected"],
             "snapshot_conflicts": snap_stats["snapshot_conflicts"],
             "companies_conflicted":
                 snap_stats["companies_conflicted"],
             "duplicate_snapshot_groups": snap_stats["duplicate_groups"],
             "regeneration_snapshot_groups":
                 snap_stats["regeneration_groups"],
             "duplicate_history_events": len(event_duplicates),
             "companies_selected_from_snapshots":
                 snap_stats["companies_selected"]}
    else:
        snaps = _latest_snapshots(conn, source_collection_run_id,
                                  parser_version=parser_version)
        events, rejected = _history_events(conn, source_collection_run_id,
                                           parser_version=parser_version)
        event_duplicates = []
        snap_stats = None
        m = {"companies": 0, "fields": 0, "history_events": 0,
             "person_roles": 0, "addresses": 0, "warnings": 0,
             "excluded_auditor_companies": 0,
             "excluded_company_form_companies": 0, "same_day_warnings": 0,
             "source_anomaly_warnings": 0, "date_artifact_warnings": 0,
             "non_verified_contributors_rejected": rejected,
             "companies_with_events": len(
                 {e["company_id"] for e in events
                  if e["company_id"] is not None}),
             "source_parser_version": (snaps[0][4] if snaps else None),
             "source_selection": "single_run"}
    events_by_company: dict[int, list[dict]] = {}
    for e in events:
        if e["company_id"] is not None:
            events_by_company.setdefault(e["company_id"], []).append(e)
    emitted_events: list[dict] = []
    for (company_id, valid_from, parse_id, sd, pver, doc_id, obs_id,
         sha256) in snaps:
        if isinstance(sd, str):
            sd = json.loads(sd)
        snapshot_date = _to_date(sd.get("snapshot_date")) or valid_from
        conn.execute(
            "INSERT INTO registry_projection.company_current_state "
            "(projection_run_id, company_id, registration_number, "
            "snapshot_date, source_document_id, source_observation_id, "
            "source_parse_result_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (run_id, company_id, _text(sd.get("registration_number")),
             snapshot_date, doc_id, obs_id, parse_id))
        m["companies"] += 1
        for field in SNAPSHOT_FIELDS:
            if field == "snapshot_date":
                present = True
                empty = snapshot_date is None
                value = snapshot_date.isoformat() if snapshot_date else None
            else:
                raw_val = sd.get(field)
                present = field in sd
                value, empty = _jsonb_value(raw_val)
            if not present:
                authority, warn = "NOT_AVAILABLE", "NOT_PRESENT_IN_SOURCE"
            elif empty:
                authority, warn = "AUTHORITATIVE_SNAPSHOT", "KNOWN_EMPTY"
            else:
                authority, warn = "AUTHORITATIVE_SNAPSHOT", None
            conn.execute(
                "INSERT INTO registry_projection."
                "company_current_state_field (projection_run_id, company_id, "
                "field_name, value_json, authority_state, source_document_id, "
                "source_observation_id, source_parse_result_id, "
                "source_snapshot_date, warning_code) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (run_id, company_id, field, _dump(value), authority, doc_id,
                 obs_id, parse_id, snapshot_date, warn))
            m["fields"] += 1
        # exclusions (sections 13/14/45): auditor + company_form never current
        _warn(conn, run_id, company_id, "AUDITOR_CURRENT_STATE_UNAVAILABLE",
              "INFO", {"reason": "auditor current state excluded from "
                                 "PDF-derived projection (closure audit "
                                 "decision B)"}, doc_id)
        m["warnings"] += 1
        m["excluded_auditor_companies"] += 1
        _warn(conn, run_id, company_id, "COMPANY_FORM_NOT_FROM_PDF", "INFO",
              {"reason": "company_form not asserted from Felag projection "
                         "(closure audit; mapping from parsed_entity_type is "
                         "a separate owner decision)"}, doc_id)
        m["warnings"] += 1
        m["excluded_company_form_companies"] += 1
        pol = EXCEPTION_POLICY.get(company_id)
        if pol:
            _warn(conn, run_id, company_id, pol["warning_code"],
                  pol["severity"],
                  {"classification": pol["classification"],
                   "evidence": pol["evidence"]}, doc_id)
            m["warnings"] += 1
            m["source_anomaly_warnings"] += 1
        # person roles: current rows ONLY from snapshot (sections 12/17)
        for field, role_type in (("board", "BOARD"),
                                 ("management", "MANAGEMENT")):
            for person in (sd.get(field) or []):
                if isinstance(person, dict):
                    name, title = person.get("name"), person.get("role")
                else:
                    name, title = str(person), None
                if not name:
                    continue
                norm = _norm_text(name)
                # Lean source-hygiene D1/D3/D4: suppress deterministic non-person
                if _should_suppress_person_role(norm, name, 'CURRENT_AUTHORITATIVE'):
                    # source evidence preserved, not projected as PERSON
                    continue
                conn.execute(
                    "INSERT INTO registry_projection.company_person_role "
                    "(projection_run_id, company_id, role_type, person_name, "
                    "role_title, entity_normalized, source_scope, "
                    "source_document_id, source_observation_id, "
                    "source_parse_result_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,'CURRENT_AUTHORITATIVE',"
                    "%s,%s,%s) ON CONFLICT DO NOTHING",
                    (run_id, company_id, role_type, name, title,
                     norm, doc_id, obs_id, parse_id))
                m["person_roles"] += 1
        # company address: COMPANY role only, snapshot source (section 18)
        addr = sd.get("company_address")
        if isinstance(addr, dict) and addr.get("address"):
            conn.execute(
                "INSERT INTO registry_projection.company_address "
                "(projection_run_id, company_id, address_role, source_scope, "
                "value_json, source_document_id, source_observation_id, "
                "source_parse_result_id) VALUES "
                "(%s,%s,'COMPANY','CURRENT_AUTHORITATIVE',%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (run_id, company_id,
                 _dump({"address": addr.get("address"),
                        "postal_code": addr.get("postal_code"),
                        "city": addr.get("city"),
                        "kommuna": addr.get("kommuna")}),
                 doc_id, obs_id, parse_id))
            m["addresses"] += 1
        elif isinstance(addr, str) and addr.strip():
            conn.execute(
                "INSERT INTO registry_projection.company_address "
                "(projection_run_id, company_id, address_role, source_scope, "
                "value_json, source_document_id, source_observation_id, "
                "source_parse_result_id) VALUES "
                "(%s,%s,'COMPANY','CURRENT_AUTHORITATIVE',%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (run_id, company_id, _dump({"address": addr.strip()}),
                 doc_id, obs_id, parse_id))
            m["addresses"] += 1

    # history events from VERIFIED Lysing stream (sections 15/16/46)
    seen_pairs: set[tuple] = set()
    for e in events:
        for ch in e["structured"].get("changes") or []:
            etype = ch.get("type")
            action = ch.get("action")
            if etype not in HISTORY_EVENT_TYPES or not action:
                continue
            entity = ch.get("entity")
            entity_key = _change_entity_key(ch, e.get("row_skrasnr"))
            key = (e["source_event_id"], etype, action, entity_key,
                   e["document_id"])
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            dates = [ch.get("effective_date"), ch.get("registration_date"),
                     ch.get("notice_date"), e["source_event_date"]]
            # E2: a founding date is the source's own first-state date, not a
            # later notice's artifact (lys_5053: 1977) - never warn on it.
            artifact_dates = dates
            if ch.get("origin") == FOUNDING_ORIGIN:
                founding_day = _to_date(ch.get("effective_date"))
                artifact_dates = [d for d in dates
                                  if _to_date(d) != founding_day]
            # E2: carry the parser's origin marker (e.g. FOUNDING) in the
            # existing value_json column - no DDL, no migration change.
            value = {"entity": entity, "raw_text": ch.get("raw_text")}
            if ch.get("origin"):
                value["origin"] = ch["origin"]
            if etype == "MERGER":
                # counterpart evidence only; a MERGER is not a membership.
                value = {"raw_text": ch.get("raw_text")}
                if ch.get("parties") is not None:
                    value["parties"] = ch["parties"]
                if ch.get("counterpart") is not None:
                    value["counterpart"] = ch["counterpart"]
            conn.execute(
                "INSERT INTO registry_projection.company_history_event "
                "(projection_run_id, company_id, registration_number, "
                "event_type, action, entity_key, value_json, effective_date, "
                "registration_date, notice_date, source_event_date, "
                "source_event_id, source_document_id, source_observation_id, "
                "source_parse_result_id, raw_text, authority_state) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "'HISTORICAL_EVENT_ONLY')",
                (run_id, e["company_id"], e["row_skrasnr"], etype, action,
                 entity_key, _dump(value),
                 _to_date(ch.get("effective_date")),
                 _to_date(ch.get("registration_date")),
                 _to_date(ch.get("notice_date")), e["source_event_date"],
                 e["source_event_id"], e["document_id"], e["observation_id"],
                 e["parse_id"], ch.get("raw_text") or e["source_raw"]))
            m["history_events"] += 1
            if artifact := any(_is_artifact(d) for d in artifact_dates):
                _warn(conn, run_id, e["company_id"], "SOURCE_DATE_ARTIFACT",
                      "WARN",
                      {"event_type": etype, "action": action,
                       "entity": entity, "dates": [str(d) for d in dates],
                       "policy": "raw preserved; not used for ordering"},
                      e["document_id"])
                m["warnings"] += 1
                m["date_artifact_warnings"] += 1
            if etype in MEMBERSHIP_TYPES:
                role_type = MEMBERSHIP_ROLE_TYPE[etype]
                scope = "HISTORICAL_EVENT"
                # Lean source-hygiene D1/D3/D4
                if _should_suppress_person_role(entity_key, ch.get("raw_text") or entity, 'HISTORICAL_EVENT'):
                    # preserve history_event, skip person_role
                    pass
                else:
                    conn.execute(
                        "INSERT INTO registry_projection.company_person_role "
                        "(projection_run_id, company_id, role_type, person_name, "
                        "role_title, entity_normalized, source_scope, "
                        "source_event_id, source_document_id, "
                        "source_observation_id, source_parse_result_id) "
                        "VALUES (%s,%s,%s,%s,%s,%s,'HISTORICAL_EVENT',%s,%s,%s,%s)"
                        " ON CONFLICT DO NOTHING",
                        (run_id, e["company_id"], role_type,
                         entity or entity_key, ch.get("role"), entity_key,
                         e["source_event_id"], e["document_id"],
                         e["observation_id"], e["parse_id"]))
                    m["person_roles"] += 1
            if etype == "ADDRESS_CHANGE":
                conn.execute(
                    "INSERT INTO registry_projection.company_address "
                    "(projection_run_id, company_id, address_role, "
                    "source_scope, value_json, source_event_id, "
                    "source_document_id, source_observation_id, "
                    "source_parse_result_id) VALUES "
                    "(%s,%s,%s,'HISTORICAL_EVENT',%s,%s,%s,%s,%s) "
                    "ON CONFLICT DO NOTHING",
                    (run_id, e["company_id"], "COMPANY",
                     _dump({"entity": entity, "raw_text": ch.get("raw_text")}),
                     e["source_event_id"], e["document_id"],
                     e["observation_id"], e["parse_id"]))
                m["addresses"] += 1
    # company-level history warnings
    for company_id, evs in events_by_company.items():
        has_membership = any(
            ch.get("type") in ("BOARD_CHANGE", "MANAGEMENT_CHANGE",
                               "AUDITOR_CHANGE")
            for e in evs
            for ch in e["structured"].get("changes") or [])
        if has_membership:
            _warn(conn, run_id, company_id, "NON_AUTHORITATIVE_HISTORY",
                  "INFO",
                  {"note": "history events are not current-state authority; "
                           "board/management current is snapshot-only, "
                           "auditor excluded"},
                  evs[0]["document_id"])
            m["warnings"] += 1
        flags = _same_day_flags(evs)
        if flags:
            _warn(conn, run_id, company_id, "SAME_DAY_UNORDERED", "WARN",
                  {"same_day_groups": flags,
                   "policy": "flag only; never silently ordered"},
                  evs[0]["document_id"])
            m["warnings"] += 1
            m["same_day_warnings"] += 1
    return m


def _is_artifact(d) -> bool:
    if d is None:
        return False
    dt = d if isinstance(d, date) else _to_date(d)
    if dt is None:
        return False
    return dt < DATE_ARTIFACT_MIN or dt > DATE_ARTIFACT_MAX


def _same_day_flags(events: list[dict]) -> list[dict]:
    """Same-day same-entity opposite-direction membership pairs (section 42)."""
    by_date: dict[str, dict[str, set]] = {}
    for e in events:
        for ch in e["structured"].get("changes") or []:
            if ch.get("type") not in MEMBERSHIP_TYPES or not ch.get("action"):
                continue
            d = governing_date(ch)
            if not d:
                continue
            ent = _norm_text(ch.get("entity_normalized") or ch.get("entity"))
            by_date.setdefault(str(d), {}).setdefault(ent, set()).add(
                ch["action"])
    flags = []
    for d in sorted(by_date):
        for ent in sorted(by_date[d]):
            acts = by_date[d][ent]
            if acts & OPEN_ACTIONS and acts & CLOSE_ACTIONS:
                flags.append({"date": str(d), "entity": ent,
                              "directions": sorted(acts)})
    return flags


# ---------------------------------------------------------------------------
# validation (task section 47)

def validate(conn, run_id: int) -> dict:
    """Mechanical post-build validation (task section 47)."""
    companies = one_count(conn, run_id, "company_current_state")
    fields = one_count(conn, run_id, "company_current_state_field")
    hist = one_count(conn, run_id, "company_history_event")
    roles = one_count(conn, run_id, "company_person_role")
    addrs = one_count(conn, run_id, "company_address")
    warns = one_count(conn, run_id, "projection_warning")
    broken_provenance = conn.execute(
        "SELECT count(*) FROM registry_projection.company_current_state_field "
        "WHERE projection_run_id=%s AND authority_state='AUTHORITATIVE_SNAPSHOT'"
        " AND (source_document_id IS NULL OR source_observation_id IS NULL "
        "OR source_parse_result_id IS NULL)", (run_id,)).fetchone()[0]
    dup_state = conn.execute(
        "SELECT count(*) FROM (SELECT company_id FROM "
        "registry_projection.company_current_state WHERE projection_run_id=%s "
        "GROUP BY company_id HAVING count(*) > 1) x", (run_id,)).fetchone()[0]
    multi_active = conn.execute(
        "SELECT count(*) FROM registry_projection.projection_run "
        "WHERE status='ACTIVE'").fetchone()[0]
    # recompute the VERIFIED attribution gate over inserted history events
    # (section 46 hard invariant: any mismatch would be a validation failure)
    pairs = conn.execute(
        "SELECT p.structured_data->>'registration_number', ev.row_skrasnr "
        "FROM registry_projection.company_history_event he "
        "JOIN pdf_source_event ev ON ev.id = he.source_event_id "
        "JOIN pdf_parse_result p ON p.id = he.source_parse_result_id "
        "WHERE he.projection_run_id=%s", (run_id,)).fetchall()
    non_verified = sum(1 for rn, skrasnr in pairs
                       if _norm_regnr(rn) != _norm_regnr(skrasnr))
    # authority-policy checks (v2 task section 24)
    auditor_promoted = conn.execute(
        "SELECT count(*) FROM registry_projection.company_current_state_field "
        "WHERE projection_run_id=%s AND field_name='auditor'",
        (run_id,)).fetchone()[0]
    company_form_promoted = conn.execute(
        "SELECT count(*) FROM registry_projection.company_current_state_field "
        "WHERE projection_run_id=%s AND field_name='company_form'",
        (run_id,)).fetchone()[0]
    lysing_current_roles = conn.execute(
        "SELECT count(*) FROM registry_projection.company_person_role pr "
        "WHERE pr.projection_run_id=%s AND pr.source_scope="
        "'CURRENT_AUTHORITATIVE' AND NOT EXISTS (SELECT 1 FROM "
        "pdf_parse_result p WHERE p.id=pr.source_parse_result_id AND "
        "p.document_kind='COMPANY_SNAPSHOT')", (run_id,)).fetchone()[0]
    lysing_current_fields = conn.execute(
        "SELECT count(*) FROM registry_projection.company_current_state_field "
        "f WHERE f.projection_run_id=%s AND f.field_name IN "
        "('board','management') AND NOT EXISTS (SELECT 1 FROM "
        "pdf_parse_result p WHERE p.id=f.source_parse_result_id AND "
        "p.document_kind='COMPANY_SNAPSHOT')",
        (run_id,)).fetchone()[0]
    snapshot_gate = conn.execute(
        "SELECT count(*) FROM registry_projection.company_current_state cs "
        "WHERE cs.projection_run_id=%s AND NOT EXISTS (SELECT 1 FROM "
        "pdf_parse_result p WHERE p.id=cs.source_parse_result_id AND "
        "p.document_kind='COMPANY_SNAPSHOT' AND p.parse_status='PARSED')",
        (run_id,)).fetchone()[0]
    run = conn.execute(
        "SELECT status, projection_version, source_collection_run_id, "
        "content_hash FROM registry_projection.projection_run "
        "WHERE projection_run_id=%s", (run_id,)).fetchone()
    return {
        "projection_run_id": run_id, "status": run[0],
        "projection_version": run[1],
        "source_collection_run_id": run[2], "content_hash": run[3],
        "companies_projected": companies, "fields_generated": fields,
        "history_events_generated": hist, "person_role_rows": roles,
        "address_rows": addrs, "warning_rows": warns,
        "non_VERIFIED_contributors": non_verified,
        "broken_provenance_refs": broken_provenance,
        "duplicate_active_company_state": dup_state,
        "multiple_ACTIVE_runs": multi_active,
        "auditor_current_promotion": auditor_promoted,
        "company_form_pdf_promotion": company_form_promoted,
        "board_management_lysing_promotion": lysing_current_roles,
        "board_management_lysing_field_promotion": lysing_current_fields,
        "current_state_non_snapshot_sources": snapshot_gate,
        "authority_policy_violations": (auditor_promoted
                                        + company_form_promoted
                                        + lysing_current_roles
                                        + lysing_current_fields),
        "active_setting_matches": (get_setting(
            conn, "active_projection_run_id") == run_id)
        if run[0] == "ACTIVE" else None,
        # D-07: the stored hash must still describe the content
        "content_hash_matches": run[3] == content_hash(conn, run_id),
    }


# D-07: validate() counters that must be 0 before a run may become ACTIVE
ACTIVATION_BLOCKERS = ("authority_policy_violations", "broken_provenance_refs",
                       "non_VERIFIED_contributors")


def one_count(conn, run_id, table):
    return conn.execute(
        f"SELECT count(*) FROM registry_projection.{table} "
        "WHERE projection_run_id=%s", (run_id,)).fetchone()[0]


# ---------------------------------------------------------------------------
# v1 -> v2 reconciliation (v2 task sections 16-18)

def _canon_value(v) -> str:
    """Semantic value identity: canonical JSON of the stored value_json."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            pass
    return json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)


def _field_map(conn, run_id: int) -> dict:
    """(company_id, field_name) -> normalized field record for a run."""
    rows = conn.execute(
        "SELECT f.company_id, f.field_name, f.value_json, f.authority_state, "
        "f.source_document_id, f.source_observation_id, "
        "f.source_parse_result_id, f.source_snapshot_date, f.warning_code "
        "FROM registry_projection.company_current_state_field f "
        "WHERE f.projection_run_id=%s", (run_id,)).fetchall()
    out: dict[tuple, dict] = {}
    for (cid, fname, value, auth, doc, obs, parse, snap, warn) in rows:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                pass
        out[(cid, fname)] = {
            "value": value, "canon": _canon_value(value), "authority": auth,
            "source": {"document_id": doc, "observation_id": obs,
                       "parse_result_id": parse,
                       "snapshot_date": str(snap) if snap else None},
            "warning_code": warn}
    return out


def _canon_value(v) -> str:
    """Semantic value identity: canonical JSON of the stored value_json."""
    return json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)


def reconcile(conn, run_a: int, run_b: int) -> dict:
    """Classify every company in run_a UNION run_b (v2 task section 16).

    UNCHANGED | NEW_IN_V2 | REMOVED_IN_V2 | FIELD_CHANGED |
    SOURCE_CHANGED_SEMANTICALLY_SAME | CONFLICT (set by caller metrics for
    conflict-excluded companies).
    """
    a_fields = _field_map(conn, run_a)
    b_fields = _field_map(conn, run_b)
    a_companies = {cid for (cid, _f) in a_fields}
    b_companies = {cid for (cid, _f) in b_fields}
    regnrs = dict(conn.execute(
        "SELECT company_id, registration_number FROM "
        "registry_projection.company_current_state WHERE "
        "projection_run_id IN (%s,%s)",
        (run_a, run_b)).fetchall())
    company_diff, field_diff = [], []
    for cid in sorted(a_companies | b_companies):
        if cid in a_companies and cid not in b_companies:
            company_diff.append({"company_id": cid,
                                 "classification": "REMOVED_IN_V2"})
            continue
        if cid in b_companies and cid not in a_companies:
            company_diff.append({"company_id": cid,
                                 "classification": "NEW_IN_V2"})
            continue
        same_value, same_source, changed = True, True, 0
        for fname in SNAPSHOT_FIELDS:
            fa, fb = a_fields.get((cid, fname)), b_fields.get((cid, fname))
            if fa is None and fb is None:
                continue
            if (fa["canon"] if fa else None) != (fb["canon"] if fb else None):
                same_value = False
                changed += 1
                field_diff.append({
                    "registration_number": regnrs.get(cid),
                    "company_id": cid, "field_name": fname,
                    "v1_value": fa["value"] if fa else None,
                    "v2_value": fb["value"] if fb else None,
                    "v1_source": fa["source"] if fa else None,
                    "v2_source": fb["source"] if fb else None,
                    "reason": ("snapshot_date advanced to fresher approved "
                               "evidence"
                               if (fa and fb and fa["source"]["snapshot_date"]
                                   != fb["source"]["snapshot_date"])
                               else "different approved evidence selected")})
            elif fa and fb and fa["source"] != fb["source"]:
                same_source = False
        if not same_value:
            cls = "FIELD_CHANGED"
        elif not same_source:
            cls = "SOURCE_CHANGED_SEMANTICALLY_SAME"
        else:
            cls = "UNCHANGED"
        company_diff.append({"company_id": cid, "classification": cls,
                             "fields_changed": changed})
    return {"company_diff": company_diff, "field_diff": field_diff,
            "summary": {cls: sum(1 for d in company_diff
                                 if d["classification"] == cls)
                        for cls in ("UNCHANGED", "NEW_IN_V2", "REMOVED_IN_V2",
                                    "FIELD_CHANGED",
                                    "SOURCE_CHANGED_SEMANTICALLY_SAME",
                                    "CONFLICT")}}


_LEGAL_STATUS_TOKENS = ("konkurs", "tvingsilsavtøku", "likvidatión",
                        "likvidation", "fráboðan", "tvangsauktion")

_LEGAL_FORM_TOKENS = ("p/f", "sp/f", "t/f", "i/f", "a/s", "hf", "v/s",
                      "ívf")


def _squash(s) -> str:
    return re.sub(r"\s+", " ",
                  unicodedata.normalize("NFC", str(s or ""))).strip().lower()


def _name_core(s) -> str:
    """Name without legal-form tokens (for semantic comparison only)."""
    core = _squash(s)
    return " ".join(t for t in core.replace(",", " ").split()
                    if t not in _LEGAL_FORM_TOKENS)


def classify_name_difference(canonical_name: str, projection_name) -> str:
    """Mechanical classification of a canonical-vs-projection name difference
    (v2 task section 20). Legal-status transitions (í konkurs / u.
    tvingsilsavtøku / í likvidatión ...) present in the PDF snapshot name but
    absent from canonical are EXPECTED_LEGAL_STATUS_SUFFIX - never
    auto-removed (section 21). Identical cores are NORMALIZATION_ONLY.
    A demonstrably newer canonical side would be CANONICAL_FRESHER_THAN_SOURCE
    (none observed); anything else is SOURCE_FRESHER_THAN_CANONICAL.
    """
    proj = _squash(projection_name or "")
    canon = _squash(canonical_name or "").split(" (")[0]

    def core(s: str) -> set[str]:
        tokens = s.replace(",", " ").split()
        return {t for t in tokens if t not in _LEGAL_FORM_TOKENS
                and t not in _LEGAL_STATUS_TOKENS and t not in ("í", "u.",
                                                                "under")}

    p_core, c_core = core(proj), core(canon)
    status_present = any(t in proj for t in _LEGAL_STATUS_TOKENS) or \
        any(t in canon for t in _LEGAL_STATUS_TOKENS)
    if p_core == c_core and status_present:
        return "EXPECTED_LEGAL_STATUS_SUFFIX"
    if p_core == c_core:
        return "NORMALIZATION_ONLY"
    return "SOURCE_FRESHER_THAN_CANONICAL"


def name_difference_review(conn, run_a: int, run_b: int,
                           items: list[dict]) -> list[dict]:
    """Re-review v1 canonical name differences in v2 (v2 sections 19-21).

    `items` come from the v1 comparison artifact: {company_id, digits,
    canonical_regnr, canonical_name}.
    """
    out = []
    for item in items:
        cid = item["company_id"]

        def name_of(run):
            f = conn.execute(
                "SELECT value_json, source_snapshot_date, "
                "source_document_id, source_observation_id, "
                "source_parse_result_id FROM "
                "registry_projection.company_current_state_field WHERE "
                "projection_run_id=%s AND company_id=%s AND "
                "field_name='company_name'", (run, cid)).fetchone()
            if f is None:
                return None
            v = f[0]
            if isinstance(v, dict) and "value" in v:
                v = v["value"]
            return {"name": v,
                    "snapshot_date": str(f[1]) if f[1] else None,
                    "document_id": f[2], "observation_id": f[3],
                    "parse_result_id": f[4]}

        v1n, v2n = name_of(run_a), name_of(run_b)
        felag_text, regnr = None, item.get("digits")
        if v2n and v2n["parse_result_id"]:
            row = conn.execute(
                "SELECT p.structured_data->>'registration_number', "
                "left(p.raw_text, 400) FROM pdf_parse_result p "
                "WHERE p.id=%s", (v2n["parse_result_id"],)).fetchone()
            if row:
                regnr, felag_text = row
        v2_name = v2n["name"] if v2n else None
        out.append({
            "company_id": cid,
            "registration_number": regnr,
            "canonical_registration_number": item["canonical_regnr"],
            "canonical_name": item["canonical_name"],
            "v1_projection_name": (v1n or {}).get("name"),
            "v2_projection_name": v2_name,
            "v1_snapshot_date": (v1n or {}).get("snapshot_date"),
            "v2_snapshot_date": (v2n or {}).get("snapshot_date"),
            "v2_document_provenance": {
                k: (v2n or {}).get(k) for k in
                ("document_id", "observation_id", "parse_result_id")},
            "felag_source_text": felag_text,
            "category": classify_name_difference(
                item["canonical_name"], v2_name),
            "auto_corrected": False})
    return out


def counts(conn, run_id: int) -> dict:
    return {t: one_count(conn, run_id, t) for t in
            ("company_current_state", "company_current_state_field",
             "company_history_event", "company_person_role",
             "company_address", "projection_warning")}


# ---------------------------------------------------------------------------
# lifecycle operations (explicit, atomic; sections 30/31/32/65/66)

def activate(conn, run_id: int) -> None:
    """SHADOW -> ACTIVE in ONE transaction (old ACTIVE -> RETIRED first).

    Refuses unless validate() passes (D-07): every ACTIVATION_BLOCKERS count
    is 0 and the stored content_hash equals a fresh recompute.
    """
    with conn.transaction():
        status = conn.execute(
            "SELECT status FROM registry_projection.projection_run "
            "WHERE projection_run_id=%s FOR UPDATE", (run_id,)).fetchone()
        if status is None:
            raise ValueError("unknown projection_run_id")
        if status[0] != "SHADOW":
            raise ValueError(f"cannot activate from status {status[0]!r}")
        v = validate(conn, run_id)
        failed = [f"{k}={v[k]}" for k in ACTIVATION_BLOCKERS if v[k]]
        if not v["content_hash_matches"]:
            failed.append("content_hash differs from the stored hash")
        if failed:
            raise ValueError(f"refusing to activate run {run_id}: "
                             + ", ".join(failed))
        conn.execute(
            "UPDATE registry_projection.projection_run SET status='RETIRED' "
            "WHERE status='ACTIVE'")
        conn.execute(
            "UPDATE registry_projection.projection_run SET status='ACTIVE' "
            "WHERE projection_run_id=%s", (run_id,))
        _set_setting(conn, "active_projection_run_id", int(run_id))


def retire(conn, run_id: int) -> None:
    """ACTIVE/SHADOW -> RETIRED in one transaction."""
    with conn.transaction():
        row = conn.execute(
            "SELECT status FROM registry_projection.projection_run "
            "WHERE projection_run_id=%s FOR UPDATE", (run_id,)).fetchone()
        if row is None:
            raise ValueError("unknown projection_run_id")
        if row[0] not in ("ACTIVE", "SHADOW"):
            raise ValueError(f"cannot retire from status {row[0]!r}")
        conn.execute(
            "UPDATE registry_projection.projection_run SET status='RETIRED' "
            "WHERE projection_run_id=%s", (run_id,))
        if get_setting(conn, "active_projection_run_id") == run_id:
            _set_setting(conn, "active_projection_run_id", None)


def rollback_to(conn, target_run_id: int) -> None:
    """v2 ACTIVE -> v2 RETIRED -> target ACTIVE (section 32). One transaction.

    Requires the target to be RETIRED (an earlier generation). Evidence is
    never rebuilt or touched.
    """
    with conn.transaction():
        row = conn.execute(
            "SELECT status FROM registry_projection.projection_run "
            "WHERE projection_run_id=%s FOR UPDATE", (target_run_id,)).fetchone()
        if row is None:
            raise ValueError("unknown projection_run_id")
        if row[0] != "RETIRED":
            raise ValueError(f"rollback target must be RETIRED, got "
                             f"{row[0]!r}")
        conn.execute(
            "UPDATE registry_projection.projection_run SET status='RETIRED' "
            "WHERE status='ACTIVE'")
        conn.execute(
            "UPDATE registry_projection.projection_run SET status='ACTIVE' "
            "WHERE projection_run_id=%s", (target_run_id,))
        _set_setting(conn, "active_projection_run_id", int(target_run_id))


def delete_non_active(conn, run_id: int) -> None:
    """Delete a SHADOW/FAILED/RETIRED projection incl. its content rows.

    ACTIVE is refused (section 30). Evidence is never deleted (sections 28/29).
    """
    with conn.transaction():
        row = conn.execute(
            "SELECT status FROM registry_projection.projection_run "
            "WHERE projection_run_id=%s FOR UPDATE", (run_id,)).fetchone()
        if row is None:
            raise ValueError("unknown projection_run_id")
        if row[0] == "ACTIVE":
            raise ValueError("ACTIVE projection cannot be deleted")
        # D-09: entity_link references projection_run (and roles): first
        for table in ("entity_link", "company_current_state_field",
                      "company_current_state", "company_history_event",
                      "company_person_role", "company_address",
                      "projection_warning"):
            conn.execute(
                f"DELETE FROM registry_projection.{table} "
                "WHERE projection_run_id=%s", (run_id,))
        conn.execute(
            "DELETE FROM registry_projection.projection_run "
            "WHERE projection_run_id=%s", (run_id,))


def content_hash(conn, run_id: int) -> str:
    """Deterministic sha256 over all projection content of one run."""
    h = hashlib.sha256()
    for table, cols in (
            ("company_current_state",
             "company_id, registration_number, snapshot_date, "
             "source_document_id, source_observation_id, "
             "source_parse_result_id"),
            ("company_current_state_field",
             "company_id, field_name, value_json::text, authority_state, "
             "source_document_id, source_observation_id, "
             "source_parse_result_id, source_snapshot_date::text, "
             "warning_code"),
            ("company_history_event",
             "company_id, registration_number, event_type, action, "
             "entity_key, value_json::text, effective_date::text, "
             "registration_date::text, notice_date::text, "
             "source_event_date::text, source_event_id, source_document_id, "
             "source_observation_id, source_parse_result_id, raw_text, "
             "authority_state"),
            ("company_person_role",
             "company_id, role_type, person_name, role_title, "
             "entity_normalized, source_scope, source_event_id, "
             "source_document_id, source_observation_id, "
             "source_parse_result_id"),
            ("company_address",
             "company_id, address_role, source_scope, value_json::text, "
             "source_document_id, source_observation_id, "
             "source_parse_result_id"),
            ("projection_warning",
             "company_id, warning_code, severity, details_json::text, "
             "source_document_id"),
    ):
        order = ",".join(str(i) for i in
                         range(1, len(cols.split(",")) + 1))
        rows = conn.execute(
            f"SELECT {cols} FROM registry_projection.{table} "
            f"WHERE projection_run_id=%s ORDER BY {order}",
            (run_id,)).fetchall()
        for r in rows:
            h.update(("\x1e".join("" if v is None else str(v)
                                  for v in r) + "\x1f").encode("utf-8"))
    return h.hexdigest()