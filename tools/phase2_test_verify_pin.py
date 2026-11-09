#!/usr/bin/env python3
"""Offline tests for the analytics-verifier baselines and the F27 run pin.

Imports the two verifier modules (no DB connection at import) and exercises the
same pure helpers their live checks use, so a missing run-34 baseline or a build
pinned to an older projection run fails here as well as in the live verifier.
Run: python3 tools/phase2_test_verify_pin.py
"""
import inspect
import sys

sys.path.insert(0, "tools")
import phase2_verify as p2
import phase3_verify as p3

fails = []


def check(name, cond, detail=""):
    print(("  ok  " if cond else "  FAIL ") + name + (f" :: {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


# ---- recorded baselines (lane X4: build 14/15 on projection run 34, 2026-11-08)
check("run 34 identity baseline recorded",
      p2.identity_baseline(34) == {"che_total": 14822, "direct": 7763, "propagation": 2157},
      str(p2.identity_baseline(34)))
check("run 34 coverage baseline recorded",
      p3.coverage_baseline(34) == {"unresolved": 4902, "web_unresolved_canonical": 7770},
      str(p3.coverage_baseline(34)))
check("run 33 identity baseline still recorded",
      p2.identity_baseline(33) == {"che_total": 10609, "direct": 3291, "propagation": 931})
check("run 33 coverage baseline still recorded",
      p3.coverage_baseline(33) == {"unresolved": 6387, "web_unresolved_canonical": 7770})
check("unknown run has no identity baseline (fail closed)", p2.identity_baseline(999) is None)
check("unknown run has no coverage baseline (fail closed)", p3.coverage_baseline(999) is None)
check("non-numeric pin has no baseline", p2.identity_baseline(None) is None and p3.coverage_baseline(None) is None)

# ---- F27 is emitted by the live main() of both verifiers, not only by the helper
p2_main, p3_main = inspect.getsource(p2.main), inspect.getsource(p3.main)
for name, src in (("phase2_verify", p2_main), ("phase3_verify", p3_main)):
    check(f"{name}.main emits the F27 projection-run check",
          "is pinned to the ACTIVE projection run" in src
          and "projection_pin_matches(" in src
          and "FROM registry_projection.projection_run" in src)
    check(f"{name}.main opens both connections read-only",
          src.count('options="-c default_transaction_read_only=on"') == 2)
    check(f"{name}.main closes both connections (contextlib.closing)",
          src.count("contextlib.closing(") == 2)

# ---- F27: ACTIVE analytics build pinned to the ACTIVE projection run
check("matching build/projection pin passes",
      p2.projection_pin_matches(34, 34) and p3.projection_pin_matches(34, 34))
check("string and int pins compare numerically",
      p2.projection_pin_matches("34", 34) and p3.projection_pin_matches(34, "34"))
check("build 13 on run 4 fails against ACTIVE run 34",
      not p2.projection_pin_matches(4, 34) and not p3.projection_pin_matches(4, 34))
check("missing pin fails closed",
      not p2.projection_pin_matches(34, None) and not p3.projection_pin_matches(None, 34))

print("FAILS:", fails or "none")
sys.exit(1 if fails else 0)
