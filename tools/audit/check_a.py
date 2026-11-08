#!/usr/bin/env python3
"""Lane A (collector) audit check, 2026-11. Offline: no DB, no network.

A-1  process-relations must be identity-reproducible from raw: the attempts
     query is ORDER BY ra.attempt_id and nothing commits inside the attempt
     loop (otherwise relation_id depends on the query plan and on crash/resume).
A-5  a network error must not be stored as a raw response body.
A-6  init (schema.sql) and the pre-cutover companions schema_read.sql and
     schema_compliance.sql open with BEGIN + the post-cutover guard, before any
     other statement (B-3: re-applying them empties the portal views).
A-7  Retry-After must be honoured whatever its header case.
A-15 HEIMDAL_DB is required: unset, the module still imports, but db() exits
     naming HEIMDAL_DB instead of connecting to a default (live) DSN.
W5a (fix wave, same file; DB behaviour is in test_collector_db.py):
A-1  --rebuild runs TRUNCATE … RESTART IDENTITY inside the body (one transaction).
A-2  one input set (latest 200 per entity) and one key rule for every reader.
A-3  canonical ids are assigned in source_entity_id order.
A-4  the PDF backfill goes through fetch(), which enforces the host allowlist.
A-8  a crash is recorded as STOPPED and re-raised; report lists RUNNING runs.
A-9  process-relations writes the evidence pointer.
A-10 requested_at is taken after the rate-limit wait.
A-13 oversize bodies are refused unstored; off-host redirects are refused.
A-14 schema.sql makes request_attempt / response_content append-only.
C-6  compute-ownership skips (never zeroes) an unparsed percentage.

Run: python3 tools/audit/check_a.py [path/to/heimdal_collect.py]   (exit 1 on failure)
"""
import ast
import http.client
import importlib.util
import inspect
import io
import os
import re
import sys
import textwrap
import urllib.error
import urllib.request

sys.dont_write_bytecode = True
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "heimdal_collect.py")
os.environ.setdefault("HEIMDAL_DB", "postgresql://invalid/never-connected")
spec = importlib.util.spec_from_file_location("hc_under_check", path)
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)
hc.MIN_SECONDS_BETWEEN_REQUESTS = 0
failures = []

# --- A-1 (static) ------------------------------------------------------------
src = textwrap.dedent(inspect.getsource(hc.cmd_process_relations))
tree = ast.parse(src)
query = next(" ".join(n.value for n in ast.walk(c) if isinstance(n, ast.Constant) and isinstance(n.value, str))
             for c in ast.walk(tree) if isinstance(c, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "attempts" for t in c.targets))
if "ORDER BY RA.ATTEMPT_ID" not in " ".join(query.upper().split()):
    failures.append("A-1 process-relations attempts query has no ORDER BY ra.attempt_id")
loop = next(n for n in ast.walk(tree) if isinstance(n, ast.For)
            and isinstance(n.iter, ast.Name) and n.iter.id == "attempts")
if any(isinstance(n, ast.Attribute) and n.attr == "commit" for n in ast.walk(loop)):
    failures.append("A-1 process-relations commits inside the attempt loop (crash+resume renumbers relation_id)")


# --- offline HTTP fakes --------------------------------------------------------
class Cur:
    rowcount = 1

    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class Conn:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        return Cur((1,)) if "RETURNING" in sql else Cur()

    def commit(self):
        pass

    def rollback(self):
        pass


def scripted(*steps):
    it = iter(steps)

    def fake(req, timeout=None):
        s = next(it)
        if isinstance(s, BaseException):
            raise s
        raise urllib.error.HTTPError(req.full_url, s[0], "x", s[1], io.BytesIO(b"busy"))
    urllib.request.urlopen = fake


def hdrs(raw):
    return http.client.parse_headers(io.BytesIO(raw + b"\r\n"))


def fetch(conn):
    hc._consecutive_5xx = 0
    try:
        hc.fetch(conn, 1, "get-table", "/api/get-table/1", {"level": 1}, entity_id=1)
    except hc.HardStop as e:
        return e
    return None


# --- A-5 ---------------------------------------------------------------------------
scripted(TimeoutError("The read operation timed out"), TimeoutError("again"))
c = Conn()
fetch(c)
if any("INSERT INTO response_content" in s for s in c.sql):
    failures.append("A-5 a network error was stored as raw response_content")

# --- A-7 ---------------------------------------------------------------------------
scripted((503, hdrs(b"retry-after: 3600")), (503, hdrs(b"retry-after: 3600")))
e = fetch(Conn())
if type(e) is not hc.HardStop or "Retry-After" not in str(e):
    failures.append("A-7 a lowercase 'retry-after' header was not honoured as a hard stop")

# --- A-6 / B-3 (static) ----------------------------------------------------------
if "schema.sql" not in inspect.getsource(hc.cmd_init):
    failures.append("A-6 init no longer applies schema.sql (update this check)")
for name in ("schema.sql", "schema_read.sql", "schema_compliance.sql"):
    body = re.sub(r"--[^\n]*", "", open(os.path.join(os.path.dirname(path), name)).read()).lstrip()
    m = re.match(r"BEGIN;\s*DO \$\$(.*?)\$\$;", body, re.S)
    if not m or not all(k in m.group(1) for k in ("registry_ownership", "_legacy", "RAISE EXCEPTION")):
        failures.append(f"A-6 {name} does not open with BEGIN + the post-cutover guard (B-3)")

# --- W5a ----------------------------------------------------------------------------
def source(name):
    obj = getattr(hc, name, None)
    return " ".join(inspect.getsource(obj).split()) if callable(obj) else ""


def flat(text):
    return " ".join(str(text).upper().split())


pr = source("cmd_process_relations")
if not re.search(r"RESTART IDENTITY.*attempts = conn\.execute", pr, re.S | re.I):
    failures.append("A-1 process-relations has no in-transaction TRUNCATE … RESTART IDENTITY rebuild")
input_set = flat(getattr(hc, "INPUT_SET_SQL", ""))
if "DISTINCT ON" not in input_set or "ATTEMPT_ID DESC" not in input_set:
    failures.append("A-2 the input set is not the latest HTTP-200 attempt per entity")
row = {"fromId": 1, "fromName": "a", "cbTypeId": 1, "cbTypeName": "x", "toId": 2, "toName": "b"}
if hc.SHAPES["get-table"]([dict(row, extra=1)]):
    failures.append("A-2 acquisition accepts a get-table row the parse rule rejects (extra key)")
for reader in ("cmd_process_relations", "cmd_taxonomy_audit", "cmd_report"):
    s = source(reader)
    if "INPUT_BODIES_SQL" not in s or "input_edges(" not in s or "::json" in s:
        failures.append(f"A-2 {reader} does not read the input set through the one key rule")
if "sorted(labels" not in source("assign_canonical_ids"):
    failures.append("A-3 canonical ids are not assigned in source_entity_id order")
if "fetch(" not in source("_process_pdf_candidate") or "_http_get(" in source("_process_pdf_candidate"):
    failures.append("A-4 the PDF backfill bypasses fetch()")
asked = []


def recorder(req, timeout=None):
    asked.append(req.full_url)
    raise OSError("check_a: no network")


urllib.request.urlopen = recorder
try:
    hc.fetch(Conn(), 1, "felag-pdf", "https://evil.example/x.pdf", None)
except Exception:  # noqa: BLE001 - only "was a request attempted" matters here
    pass
if asked:
    failures.append("A-4 fetch() sent a request to a host outside the allowlist")
c = Conn()
try:
    hc.run_guard(c, 9, lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    failures.append("A-8 run_guard swallowed a crash")
except RuntimeError:
    if not any("UPDATE collection_run" in s for s in c.sql):
        failures.append("A-8 a crashed run is not recorded as STOPPED")
if "run_status='RUNNING'" not in source("cmd_report"):
    failures.append("A-8 report does not list stale RUNNING runs")
if "relation_evidence_pointer" not in pr:
    failures.append("A-9 process-relations does not write the evidence pointer")
fs = source("fetch")
if not (0 <= fs.find("_sleep_for_rate_limit()") < fs.find("requested_at =")) \
        or "_sleep_for_rate_limit" in source("_http_get"):
    failures.append("A-10 requested_at is taken before the rate-limit wait")
hc.MAX_BODY_BYTES, max_body = 8, getattr(hc, "MAX_BODY_BYTES", None)


class Big(io.BytesIO):
    status, headers = 200, hdrs(b"")


urllib.request.urlopen = lambda req, timeout=None: Big(b"[" + b" " * 64 + b"]")
c = Conn()
fetch(c)
hc.MAX_BODY_BYTES = max_body
if any("INSERT INTO response_content" in s for s in c.sql):
    failures.append("A-13 an oversize body was read whole and stored")
redirect = getattr(hc, "_SameHostRedirect", None)
try:
    redirect and redirect().redirect_request(urllib.request.Request("https://sg.vs.fo/a"),
                                             io.BytesIO(), 302, "Found", hdrs(b""), "https://x.example/")
    failures.append("A-13 an off-host redirect is followed")
except urllib.error.HTTPError:
    pass
schema = flat(open(os.path.join(os.path.dirname(path), "schema.sql")).read())
for table in ("REQUEST_ATTEMPT", "RESPONSE_CONTENT"):
    if not re.search(rf"BEFORE UPDATE OR DELETE\S* (OR TRUNCATE )?ON {table} ", schema):
        failures.append(f"A-14 schema.sql has no append-only trigger on {table.lower()}")
if "is not None" not in source("cmd_compute_ownership"):
    failures.append("C-6 compute-ownership does not skip an unparsed percentage")

# --- A-15 ---------------------------------------------------------------------------
os.environ.pop("HEIMDAL_DB", None)
hc15 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc15)
hc15.psycopg = type("NoConnect", (), {"connect": staticmethod(lambda *a, **k: None)})  # never a real connect
try:
    hc15.db()
    failures.append("A-15 HEIMDAL_DB unset, but db() went on to connect to a default DSN")
except SystemExit as e:
    if "HEIMDAL_DB" not in str(e.code):
        failures.append("A-15 db() exits without naming HEIMDAL_DB")

for f in failures:
    print("FAIL", f)
print("check_a:", "OK" if not failures else f"{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
