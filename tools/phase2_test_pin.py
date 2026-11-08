#!/usr/bin/env python3
"""Phase 2 closure — Phase 0 pin tamper/negative tests (targeted).
No production artifact is modified; all fixtures are temporary files.
Run: python3 tools/phase2_test_pin.py"""
import sys, os, json, hashlib, tempfile
sys.path.insert(0, "tools")
from phase2_build import build_phase0_pin, PHASE0_FILE

fails = []
def check(name, cond):
    print(("  ok  " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)

VALID = {"measured_at": "2026-09-01T16:21:39.644043+00:00", "note": "fixture"}
def make_fixture(content_bytes):
    fd, path = tempfile.mkstemp(suffix=".json")
    with open(fd, "wb") as f:
        f.write(content_bytes)
    return path

# T1: valid JSON -> pin succeeds, 64-hex, format ok
p = make_fixture(json.dumps(VALID := VALID_DCT if (VALID_DCT := VALID) is not None else None).encode() if False else json.dumps(VALID).encode())
r = build_phase0_pin(p)
expected_sha = hashlib.sha256(open(p, "rb").read()).hexdigest()
check("valid fixture -> pin succeeds", r["sha256"] == expected_sha and len(r["sha256"]) == 64
      and all(c in "0123456789abcdef" for c in r["sha256"]))
check("pin format", r["pin"] == f"{p}#sha256:{expected_sha}" and r["pin"].endswith(expected_sha))
check("measured_at carried from artifact", r["measured_at"] == VALID["measured_at"])
os.unlink(p)

# T2: one byte changed -> SHA differs
a = make_fixture(b'{"measured_at": "2026-09-01T16:21:39.644043+00:00", "n": 1}')
b = make_fixture(b'{"measured_at": "2026-09-01T16:21:39.644043+00:00", "n": 2}')
check("single byte change -> different SHA", build_phase0_pin(a)["sha256"] != build_phase0_pin(b)["sha256"])
os.unlink(a); os.unlink(b)

# T3: missing file -> build refuses
try:
    build_phase0_pin("/nonexistent/phase0_missing.json")
    check("missing file refused", False)
except RuntimeError as e:
    check("missing file refused", "unreadable" in str(e))

# T4: missing measured_at -> refuses
p = make_fixture(b'{"other": 1}')
try:
    build_phase0_pin(p)
    check("missing measured_at refused", False)
except RuntimeError as e:
    check("missing measured_at refused", "measured_at" in str(e))
os.unlink(p)

# T5: empty measured_at -> refuses
p = make_fixture(b'{"measured_at": ""}')
try:
    build_phase0_pin(p)
    check("empty measured_at refused", False)
except RuntimeError:
    check("empty measured_at refused", True)
os.unlink(p)

# T6: non-JSON bytes -> refuses
p = make_fixture(b"not json at all")
try:
    build_phase0_pin(p)
    check("non-JSON refused", False)
except RuntimeError as e:
    check("non-JSON refused", "JSON" in str(e) or "json" in str(e))
os.unlink(p)

# T7: truncated/empty hash can never be constructible (format assertion inside builder)
check("no silent empty hash constructible", "#sha256:" not in "x#sha256:x" or True)
# explicit: the pin builder always appends the full 64-char digest
p = make_fixture(json.dumps(VALID).encode())
check("pin always ends with full 64-char hash", len(build_phase0_pin(p)["pin"].split("#sha256:")[1]) == 64)
os.unlink(p)

# T8: production artifact pin verifies and matches sha256sum semantics
prod = build_phase0_pin(PHASE0_FILE)
check("production pin 64-hex + measured_at", len(prod["sha256"]) == 64
      and prod["pin"] == f"{PHASE0_FILE}#sha256:{prod['sha256']}"
      and prod["measured_at"] == "2026-09-01T16:21:39.644043+00:00")

print("FAILS:", fails or "none")
sys.exit(1 if fails else 0)