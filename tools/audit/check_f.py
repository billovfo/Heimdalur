"""Audit check F (portal backend): core pages must not 500 when PDF history is unavailable.

F-1: with HEIMDAL_PDFHIST_PROJECTION_DSN unset (PDF history kill switch OFF),
     /persons/<id>, /persons/<id>/history, /companies/<id>/history and
     /companies/<id>/skraseting must answer 200/404, never 500
     (consumer._connect runs before the fail-closed try; consumer.py:330,481,716).
F-2: the KYC UBO table and ejarayvirlit.csv must carry the person's name
     (templates/export read `person_name`; person_owners_tree returns `display_name`).
     W3b adds: the company dossier's chain table has no nameless row either.
F-3: /groups/<id> must not 500 on a majority-ownership cycle
     (groups/service.py:41 get_subsidiaries has no visited set -> RecursionError).
     W3b adds: the same for a two-member majority cycle, and /api/groups/<id>/flow.
F-9: csv_response prefixes ' to string cells starting with = + - @ \\t \\r, quotes the
     Content-Disposition filename, and no other module writes CSV itself.
F-8: with REGISTRY_ANALYTICS_ENABLED=1, one failing fail-closed read (missing
     registry_analytics, or an inner reader that swallows its error) must not
     poison the request connection: the person page keeps its network counts.
C-4: analytics on with no ACTIVE build -> no dead Analyse tab (a tab button
     always has its panel, which says "Ingen aktiv analytics-build").

EXPECTED TO FAIL today. Read-only: GET requests only, and every session is forced
to default_transaction_read_only=on.
Run:  PGPASSFILE=tools/.dev-pgpass python3 tools/audit/check_f.py postgresql://postgres@localhost:5432/heimdal_audit_f
(DSN default: $HEIMDAL_READ_DB). Needs a portal-ready DB (see check_b.py / B §Recipe).

Lane W3d asserts (read-only too: besides GETs they POST only what reads: bulk
screen, and watchlist toggles with invalid or unknown ids):
F-4: /api/pathfinder 3562->9467 (not found) and 7870->9467 (length 3) answer as
     before in <= 10 queries each (report target <= 132; was 498/785).
F-5: /api/bulk/screen caps at 200 identifiers, wants a list of strings, reads a
     bare number as a registration number, and 200 identifiers cost <= 6 queries.
F-6: NUL, ?page=1e20, bad dates, q=² and non-dict JSON give 4xx, never 500.
F-10: no "historical" routes are left.
F-12: the rate limiter drops IPs whose window has expired (in-process).
F-14: user_watchlist has an FK to canonical_entity, v_user_watchlist shows the
     entity's real kind, and non-int / unknown ids are 400 / 404.

Lane W6d asserts:
F-11: importing heimdal builds no app and re-exports nothing; no sys.modules hack;
     graph/compliance have no domain/application/infrastructure/presentation layers.
F-13: a company page runs the entity() lookup once; eigarayvirlit repeats no query.
"""
import csv
import io
import os
import pathlib
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
dsn = sys.argv[1] if len(sys.argv) > 1 else os.environ["HEIMDAL_READ_DB"]
dsn += ("&" if "?" in dsn else "?") + "options=-c%20default_transaction_read_only%3Don"
for k in [k for k in os.environ if k.startswith(("HEIMDAL_", "PDF_HISTORY_", "REGISTRY_ANALYTICS"))]:
    del os.environ[k]
os.environ["HEIMDAL_READ_DB"] = dsn  # projection DSN deliberately unset

import psycopg  # noqa: E402
from heimdal.app import create_app  # noqa: E402

app = create_app()
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
client = app.test_client()
db = psycopg.connect(dsn)


def one(sql):
    row = db.execute(sql).fetchone()
    return row[0] if row else None


person = one("SELECT person_id FROM v_role_relation ORDER BY person_id LIMIT 1")
company = one("SELECT company_id FROM v_role_relation ORDER BY company_id LIMIT 1")
kyc_company = one("SELECT owned_id FROM v_direct_ownership WHERE owner_kind = 'PERSON'"
                  " AND owned_kind = 'COMPANY' AND parsed_percentage >= 25 ORDER BY owned_id LIMIT 1")
cycle = one("SELECT owner_id FROM v_direct_ownership WHERE owner_id = owned_id"
            " AND parsed_percentage > 50 ORDER BY owner_id LIMIT 1")
cycle2 = one("SELECT a.owner_id FROM v_direct_ownership a JOIN v_direct_ownership b"
             " ON b.owner_id = a.owned_id AND b.owned_id = a.owner_id"
             " WHERE a.owner_id <> a.owned_id AND a.parsed_percentage > 50"
             " AND b.parsed_percentage > 50 ORDER BY a.owner_id LIMIT 1")
fail = []

# --- F-4/5/6/10/12/14 (lane W3d). Every request below is read-only: invalid
# input and unknown ids are refused before the first write. ---
queries = [0]
executed = []  # (sql, params) of every query since the last counted() call
_execute = psycopg.Cursor.execute


def _counting(self, *a, **k):
    queries[0] += 1
    executed.append((str(a[0]) if a else "", repr(a[1:2])))
    return _execute(self, *a, **k)


psycopg.Cursor.execute = _counting


def counted(method, url, **kw):
    queries[0] = 0
    executed.clear()
    r = getattr(client, method)(url, **kw)
    return r, queries[0]


if one("SELECT count(*) FROM canonical_entity WHERE canonical_entity_id IN (3562, 7870, 9467)") == 3:
    for a, b, want in ((3562, 9467, (False, 0)), (7870, 9467, (True, 3))):
        r, n = counted("get", f"/api/pathfinder?from={a}&to={b}")
        got = r.get_json() or {}
        if (got.get("found"), got.get("length")) != want or n > 10:
            fail.append(f"F-4 /api/pathfinder {a}->{b}: found/length {got.get('found')}/"
                        f"{got.get('length')} (want {want[0]}/{want[1]}), {n} queries (max 10)")

for body in ({"identifiers": ["x"] * 201}, ["267"], {"identifiers": "abc"}, {"identifiers": [1]}):
    if client.post("/api/bulk/screen", json=body).status_code != 400:
        fail.append(f"F-5 /api/bulk/screen {str(body)[:40]} is not a 400")
s267 = one("SELECT registration_number FROM v_entity WHERE registration_number = 'S267'")
r, n = counted("post", "/api/bulk/screen",
               json={"identifiers": ["267"] + [f"name {i}" for i in range(199)]})
row = (r.get_json() or {"results": [{}]})["results"][0]
if r.status_code != 200 or row.get("regnr") != s267 or n > 6:
    fail.append(f"F-5 bulk '267' -> regnr {row.get('regnr')} (want {s267}), {n} queries for 200 (max 6)")

for url in ("/api/search?q=a%00b", "/companies?q=%00", "/groups?q=%00", "/compliance?q=%00",
            "/aendringer?from_date=abc", "/aendringer?to_date=2026-13-45"):
    status = client.get(url).status_code
    if status != 400:
        fail.append(f"F-6 {url} -> {status} (want 400)")
for url in ("/compliance?page=100000000000000000000", "/aendringer?page=100000000000000000000",
            "/groups?page=100000000000000000000", "/api/search?q=%C2%B2"):
    status = client.get(url).status_code
    if status >= 500:
        fail.append(f"F-6 {url} -> {status}")
for url, body in (("/api/watchlist/toggle", [1]), ("/api/watchlist/toggle", "abc"),
                  ("/api/bulk/screen", {"identifiers": ["²"]})):
    status = client.post(url, json=body).status_code
    if status >= 500:
        fail.append(f"F-6 POST {url} {body!r} -> {status}")

historical = [rule.rule for rule in app.url_map.iter_rules() if "historical" in rule.rule]
if historical:
    fail.append(f"F-10 fake historical routes still exist: {historical}")

# F-13 (lane W6d): entity() once per request; eigarayvirlit reads nothing twice.
counted("get", f"/companies/{company}")
n_entity = sum("FROM v_entity WHERE canonical_entity_id = %s" in q for q, _ in executed)
if n_entity != 1:
    fail.append(f"F-13 /companies/{company} runs the entity() lookup {n_entity}x (want 1)")
counted("get", f"/companies/{kyc_company}/eigarayvirlit")
dups = len(executed) - len(set(executed))
if dups:
    fail.append(f"F-13 /companies/{kyc_company}/eigarayvirlit repeats {dups} identical queries")

from heimdal.modules.shared import web  # noqa: E402

saved = web._requests, getattr(web, "_last_sweep", None), web.time.monotonic, app.config["TESTING"]
clock = [1000.0]
web._requests, web._last_sweep, web.time.monotonic = {}, [0.0], lambda: clock[0]
app.config["TESTING"] = False
try:
    for ip in ("127.0.0.2", "127.0.0.3", "127.0.0.4", "127.0.0.5"):
        if ip == "127.0.0.5":
            clock[0] += app.config["RATE_WINDOW"] + 1
        with app.test_request_context("/", environ_base={"REMOTE_ADDR": ip}):
            web._rate_limit()
    if list(web._requests) != ["127.0.0.5"]:
        fail.append(f"F-12 rate limiter kept {len(web._requests)} keys after the window expired (want 1)")
finally:
    web._requests, web._last_sweep, web.time.monotonic, app.config["TESTING"] = saved

if not one("SELECT count(*) FROM pg_constraint WHERE conrelid = 'user_watchlist'::regclass"
           " AND contype = 'f' AND confrelid = 'canonical_entity'::regclass"):
    fail.append("F-14 user_watchlist has no FK to canonical_entity (apply schema_watchlist.sql)")
wrong_kind = one("SELECT count(*) FROM v_user_watchlist v JOIN canonical_entity ce"
                 " USING (canonical_entity_id) WHERE v.entity_kind <> ce.entity_kind")
if wrong_kind:
    fail.append(f"F-14 v_user_watchlist shows a stored, wrong kind on {wrong_kind} row(s)")
for body, want in (({"canonical_entity_id": "abc"}, 400), ({"canonical_entity_id": 1.5}, 400),
                   ({"canonical_entity_id": True}, 400), ({"canonical_entity_id": 999999999}, 404)):
    status = client.post("/api/watchlist/toggle", json=body).status_code
    if status != want:
        fail.append(f"F-14 watchlist toggle {body} -> {status} (want {want})")


for url in (f"/persons/{person}", f"/persons/{person}/history",
            f"/companies/{company}/history", f"/companies/{company}/skraseting"):
    status = client.get(url).status_code
    if status >= 500:
        fail.append(f"F-1 {url} -> {status} with projection DSN unset")

html = client.get(f"/companies/{kyc_company}/kyc").get_data(as_text=True)
ubo = html[html.find("Beregnet Ejerandel"):][:4000]
if re.search(r"<td><strong>\s*</strong></td>", ubo):
    fail.append(f"F-2 /companies/{kyc_company}/kyc renders a UBO row without a name")
dossier = client.get(f"/companies/{kyc_company}/dossier").get_data(as_text=True)
if re.search(r"<td><strong>\s*</strong></td>", dossier):
    fail.append(f"F-2 /companies/{kyc_company}/dossier renders an ownership-chain row without a name")
rows = list(csv.DictReader(io.StringIO(
    client.get(f"/api/export/company/{kyc_company}/ejarayvirlit.csv").get_data(as_text=True))))
if rows and not any(r.get("person_name") for r in rows):
    fail.append(f"F-2 ejarayvirlit.csv for {kyc_company}: person_name empty in all {len(rows)} rows")

for cid, what in ((cycle, "owns >50% of itself"), (cycle2, "two-member majority cycle")):
    if cid is None:
        continue
    for url in (f"/groups/{cid}", f"/api/groups/{cid}/flow"):
        status = client.get(url).status_code
        if status >= 500:
            fail.append(f"F-3 {url} ({what}) -> {status}")

# F-9: every CSV export (csv_response) neutralises cells a spreadsheet would run.
from heimdal.modules.shared.csv_export import csv_response  # noqa: E402
evil = ["=HYPERLINK(\"http://x\")", "+1", "-2+3", "@SUM(A1)", "\tx", "\rx"]
resp = csv_response([dict(enumerate(evil))], list(range(len(evil))), "f9.csv")
cells = list(csv.reader(io.StringIO(resp.get_data(as_text=True), newline="")))[1]
if [c for c in cells if c[:1] in ("=", "+", "-", "@", "\t", "\r")]:
    fail.append(f"F-9 csv_response writes formula cells unchanged: {cells!r}")
if resp.headers["Content-Disposition"] != 'attachment; filename="f9.csv"':
    fail.append("F-9 csv_response Content-Disposition filename is not quoted")
for p in pathlib.Path(REPO, "heimdal").rglob("*.py"):  # the review export once wrote its own
    if p.name != "csv_export.py" and re.search(r"csv\.(writer|DictWriter)\(", p.read_text()):
        fail.append(f"F-9 {p.relative_to(REPO)} writes CSV itself instead of via csv_response")

# --- F-11 (lane W6d): no package-root shim, no sys.modules hack, no hexagonal layers ---
import subprocess  # noqa: E402
probe = subprocess.run(
    [sys.executable, "-c", "import sys, heimdal, heimdal.pdfhist.flags; print("
     "[k for k in vars(heimdal) if not k.startswith('__') and k != 'pdfhist'], 'flask' in sys.modules)"],
    capture_output=True, text=True, cwd=REPO).stdout.strip()
if probe != "[] False":
    fail.append(f"F-11 importing heimdal re-exports names or builds the app: {probe}")
for p in pathlib.Path(REPO, "heimdal").rglob("*.py"):
    if 'sys.modules' in p.read_text():
        fail.append(f"F-11 {p.relative_to(REPO)} reaches into sys.modules")
for mod in ("graph", "compliance"):
    for layer in ("domain", "application", "infrastructure", "presentation"):
        if pathlib.Path(REPO, "heimdal/modules", mod, layer).exists():
            fail.append(f"F-11 heimdal/modules/{mod}/{layer} still exists (flat layout)")

from heimdal.modules.analytics import repository as arepo  # noqa: E402

COUNTS = re.compile(r'network-preview-summary[^>]*>\s*<strong>\d+</strong> virksomheder')


def missing_table(c, *_args):
    return c.execute("SELECT 1 FROM registry_analytics.check_f_missing").fetchall()


INNER = ("pair_rows_for_person", "groups_for_person", "observations_for_company")
os.environ["REGISTRY_ANALYTICS_ENABLED"] = "1"
saved = {n: getattr(arepo, n) for n in ("active_build",) + INNER}
try:
    for url in (f"/persons/{person}", f"/companies/{company}"):
        if not COUNTS.search(client.get(url).get_data(as_text=True)):
            print(f"NOTE F-8 {url} has no network counts at baseline; not judged")
            continue
        for names in (("active_build",), INNER):  # raised, then swallowed inside
            for n in names:
                setattr(arepo, n, missing_table)
            html = client.get(url).get_data(as_text=True)
            for n, f in saved.items():
                setattr(arepo, n, f)
            if not COUNTS.search(html):
                fail.append(f"F-8 {url}: a failing {names[0]} read poisons the "
                            "request connection (network counts gone)")
    arepo.active_build = lambda c: None  # analytics on, no ACTIVE build
    for url in (f"/persons/{person}", f"/companies/{company}"):
        html = client.get(url).get_data(as_text=True)
        if ('data-tab="analyse"' in html) and ('data-panel="analyse"' not in html
                                              or "Ingen aktiv analytics-build" not in html):
            fail.append(f"C-4 {url}: Analyse tab without a 'no active build' panel")
finally:
    for n, f in saved.items():
        setattr(arepo, n, f)
    del os.environ["REGISTRY_ANALYTICS_ENABLED"]

for f in fail:
    print("FAIL", f)
assert not fail, f"{len(fail)} check(s) failed"
print("OK check_f")
