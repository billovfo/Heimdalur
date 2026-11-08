#!/usr/bin/env python3
"""Audit lane J+I check (2026-11): secrets, personal data and binding.

Static + one in-process Flask request. Touches no database and no network.
Asserts:
  J-1  no tracked file holds a DSN with an inline password (scheme://user:pw@).
       Prints file:line only, never the value.
  I-5  (W6c) the real-data files moved to the evidence store stay untracked, and
       no tracked file names a person from the store's reference set (skipped
       when the store is absent). Prints file names only.
  J-2  `git add -A` cannot sweep in evidence/backups: nothing under archive/,
       backups/, docs/*.json or docs/<subdir>/ (except docs/audit-2026-11/) is
       untracked-and-not-ignored. Those paths hold personal data and old DSNs.
  J-3  the portal refuses non-loopback clients (I6): GET /favicon.ico with
       REMOTE_ADDR=192.0.2.10 must return 403.
Docs governance (W6b; report finding ids):
  SOT     (J-1) the SoT is v2.4+, PENDING_OWNER_APPROVAL or APPROVED, and its
          summary.json carries the same version and the file's sha256.
  BANNERS (J-3, G-14) every tracked .md that states an ACTIVE run/build or
          decision count (or "unrecoverable"/"entirely lost"), and every
          September frontend/UX doc, opens with a HISTORICAL or SUPERSEDED-PARTS
          banner. STATUS.md, the SoT, README and the audit reports are exempt.
  CHAINS  (J-5) no two tracked docs/*.md differ only in their -vN.N suffix.
  ROOT    (J-6) the root temp artefacts stay untracked, and the only tracked
          files that match an ignore pattern are the kept evidence.
  LINKS   every relative link in README.md and tracked docs/**/*.md resolves to
          a tracked file or directory.
Run: python3 tools/audit/check_j.py   (exit 1 while a finding is present)
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
DSN_PW = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@'\"`]+:[^\s@/'\"`]+@")


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                          capture_output=True, text=True).stdout.splitlines()


fails = []

# J-1
def real_pw(line):
    """A DSN password that is a value, not a placeholder: G3's rule (a value
    starting with $ { < * % is a variable/placeholder) plus the notation user:pw."""
    for m in DSN_PW.finditer(line):
        user, pw = m.group(0)[:-1].split("://", 1)[1].rsplit(":", 1)
        if pw[0] not in "${<*%" and (user, pw) != ("user", "pw"):
            return True
    return False


assert real_pw("postgresql://u:s3cret" + "@h/db") and not any(map(real_pw, (
    "x://u:<PW>" + "@h", "x://u:${PW}" + "@h", "scheme://user:pw" + "@")))
hits = []
for rel in git("ls-files"):
    try:
        text = (ROOT / rel).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    hits += [f"{rel}:{i}" for i, line in enumerate(text.splitlines(), 1)
             if real_pw(line)]
if hits:
    fails.append(f"J-1 {len(hits)} password-bearing DSN lines in tracked files: "
                 + " ".join(hits))

# I-5 (W6c): real-data evidence stays outside git (J §3 retention rule). The
# moved files must not come back, and no tracked file may name a person from
# the reference set that lives in the evidence store (company names may stay).
EVIDENCE = Path(os.environ.get("HEIMDAL_EVIDENCE_DIR")
                or Path.home() / ".local/share/heimdal/evidence/2026-11-audit")
REAL_DATA = re.compile(
    r"^(fixtures/pdfhist/|docs/.*\.png$|docs/HEIMDAL-PDF-HISTORIK-10-RANDOM-DATA-AUDIT"
    r"|validation_(250_results|results|250_temporal_sample|250_manual_review"
    r"|changed_parses_gatefix)\.json$|expansion_reparse_|manual_geocode_"
    r"|tools/phase4_real_data_audit\.json$)")
back = [p for p in git("ls-files") if REAL_DATA.match(p)]
if back:
    fails.append(f"I-5 {len(back)} real-data files tracked again: " + " ".join(back))
if (EVIDENCE / "person_reference_set.txt").is_file():
    def _lines(name):
        p = EVIDENCE / name
        return [n for n in p.read_text(encoding="utf-8").splitlines() if n] if p.is_file() else []

    by_first = {}
    for n in _lines("person_reference_set.txt"):
        by_first.setdefault(n.split()[0], []).append(n)

    def _word(c):
        return c.isalnum() or c == "_"

    def names_a_person(text):
        """Whole-word match of a reference name (a genitive -s is allowed)."""
        for m in re.finditer(r"\w+(?:-\w+)*", text):
            for n in by_first.get(m.group(0), ()):
                end = m.start() + len(n)
                a, b = text[end:end + 1], text[end + 1:end + 2]
                if text.startswith(n, m.start()) and (not _word(a) or a == "s" and not _word(b)):
                    return True
        return False

    sample = next(iter(by_first.values()))[0]
    assert names_a_person(f"a {sample}, b") and not names_a_person(f"a{sample}b")
    mask = [n for n in _lines("company_name_mask.txt") if len(n) > 5]
    company = re.compile("|".join(map(re.escape, sorted(mask, key=len, reverse=True)))) if mask else None
    named = []
    for rel in git("ls-files"):
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if company:
            text = company.sub("#", text)
        if names_a_person(text):
            named.append(rel)  # file only, never the name
    if named:
        fails.append(f"I-5 {len(named)} tracked files name a person from the reference "
                     f"set in {EVIDENCE}: " + " ".join(named))
else:
    print(f"SKIP I-5 person-name scan: no reference set in {EVIDENCE}")

# J-2
def exposed(p):
    if p.startswith(("archive/", "backups/")):
        return True
    if p.startswith("docs/") and not p.startswith("docs/audit-2026-11/"):
        rest = p[len("docs/"):]
        return "/" in rest or rest.endswith(".json")
    return False


swept = [p for p in git("ls-files", "-o", "--exclude-standard") if exposed(p)]
if swept:
    tops = sorted({p.split("/")[0] if not p.startswith("docs/") else
                   "docs/" + (p.split("/")[1] if p.count("/") > 1 else "*.json")
                   for p in swept})
    fails.append(f"J-2 {len(swept)} untracked, not-ignored evidence/backup files "
                 f"(git add -A would commit them) under: " + " ".join(tops))

# --- docs governance (W6b) ---
tracked = git("ls-files")
mds = [p for p in tracked if p.endswith(".md")]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# SOT (report J-1)
SOT = "docs/HEIMDAL-PRODUCT-SOURCE-OF-TRUTH.md"
meta = dict(re.findall(r"^(\w+): (.*)$", read(SOT).split("\n---\n", 1)[0], re.M))
summary = json.loads(read(SOT.replace(".md", ".summary.json")))
version = tuple(int(x) for x in re.findall(r"\d+", meta.get("document_version", "0")))
if (version < (2, 4) or meta.get("status") not in ("PENDING_OWNER_APPROVAL", "APPROVED")
        or summary.get("document_version") != meta.get("document_version")
        or summary.get("document_sha256") != hashlib.sha256((ROOT / SOT).read_bytes()).hexdigest()):
    fails.append(f"SOT (report J-1): want v2.4+, an owner-approval status and a summary.json "
                 f"bound to the file; got v{meta.get('document_version')} {meta.get('status')}")

# BANNERS (report J-3, G-14)
STATE = re.compile(r"(?i)ACTIVE PDF PROJECTION"
                   r"|\b(?:projection(?:[ _]run)?|run)[ *`]*\d+[ *`]*\(?(?:continuously )?ACTIVE\b"
                   r"|\bACTIVE(?: projection)?:?[ *`]*\d+"
                   r"|\banalytics(?: build)?[ *`]*1[0-9]\b"
                   r"|\bbuild[ *`]*\d+[ *`]*\(?ACTIVE\b"
                   r"|\b0 (?:live )?decisions"
                   r"|unrecoverable|entirely lost")
FRONTEND = re.compile(r"(?i)^docs/[^/]*(FRONTEND|UX|SCREENSHOT|INTERACTION|OWNER-ACCEPTANCE|AGENT-OWNER"
                      r"|FINAL-(DESKTOP|EVIDENCE|MECHANICAL)|FRESH-INDEPENDENT-FINAL|design-audit"
                      r"|redesign-plan)[^/]*\.md$")
BANNER = re.compile(r"^> (HISTORICAL \(audit 2026-11\)|SUPERSEDED-PARTS:)", re.M)
unbannered = [p for p in mds
              if not p.startswith("docs/audit-2026-11/")
              and p not in ("docs/STATUS.md", SOT, "README.md")
              and (FRONTEND.match(p) or STATE.search(read(p)))
              and not BANNER.search("".join(read(p).splitlines(keepends=True)[:12]))]
if unbannered:
    fails.append(f"BANNERS (report J-3, G-14) {len(unbannered)} stale docs without a "
                 "HISTORICAL banner: " + " ".join(unbannered))

# CHAINS (report J-5)
stems = {}
for p in mds:
    if p.count("/") == 1 and p.startswith("docs/"):
        stems.setdefault(re.sub(r"(-v\d+(?:\.\d+)*)?\.md$", "", p), []).append(p)
chains = [" + ".join(v) for v in stems.values() if len(v) > 1]
if chains:
    fails.append("CHAINS (report J-5) older versions next to the latest: " + "; ".join(chains))

# ROOT (report J-6)
ROOT_TEMP = ("validation_metrics_run.json", "validation_preflight.json", "sweep.log",
             "expansion_run_424244_console.log", "skraseting_activation_regression_junit.xml",
             "run_batch.sh")
KEEP_IGNORED = {"acquire_100.log", "acquire_rest.log", "audit_selection_250.json"}
temp = [p for p in tracked if p.startswith(ROOT_TEMP)]
odd = sorted(set(git("ls-files", "-i", "-c", "--exclude-standard")) - KEEP_IGNORED)
if temp or odd:
    fails.append("ROOT (report J-6) tracked temp artefacts: " + " ".join(sorted({*temp, *odd})))

# LINKS
dirs = {str(Path(p).parent) for p in tracked} | {str(q) for p in tracked for q in Path(p).parents}
LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
broken, nlinks = [], 0
for p in [m for m in mds if m == "README.md" or m.startswith("docs/")]:
    for m in LINK.finditer(re.sub(r"```.*?```", "", read(p), flags=re.S)):
        target = unquote(m.group(1).split("#")[0])
        if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("<"):
            continue
        nlinks += 1
        rel = os.path.normpath(os.path.join(os.path.dirname(p), target))
        if rel not in tracked and rel not in dirs:
            broken.append(f"{p} -> {m.group(1)}")
if broken:
    fails.append(f"LINKS {len(broken)}/{nlinks} relative links do not resolve: " + " ".join(broken))

# J-3 (env pointed at a dead port before import: no DB can be reached)
for var in ("HEIMDAL_READ_DB", "HEIMDAL_PG_DSN", "HEIMDAL_PDFHIST_PROJECTION_DSN",
            "HEIMDAL_PDFHIST_DB", "HEIMDAL_QA_DB"):
    os.environ[var] = "postgresql://audit@127.0.0.1:1/none"
sys.path.insert(0, str(ROOT))
from heimdal.app import create_app  # noqa: E402

status = create_app().test_client().get(
    "/favicon.ico", environ_base={"REMOTE_ADDR": "192.0.2.10"}).status_code
if status != 403:
    fails.append(f"J-3 non-loopback client got HTTP {status} (want 403): "
                 "no loopback guard in heimdal/app.py create_app()")

for f in fails:
    print("FAIL", f)
print("check_j:", "FAIL" if fails else "PASS")
sys.exit(1 if fails else 0)
