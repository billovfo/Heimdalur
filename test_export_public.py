"""tools/export_public.sh: a denylisted string refuses the snapshot; a clean one passes
with only the keep-listed docs (docs/ is export-ignored by default). Offline (git archive only)."""
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent


def _export(tmp_path, pattern):
    deny = tmp_path / "deny"
    deny.write_text(pattern + "\n")
    env = dict(os.environ, HEIMDAL_PUBLIC_DENYLIST=str(deny))
    return subprocess.run(["bash", str(ROOT / "tools/export_public.sh"), str(tmp_path / "out")],
                          cwd=ROOT, env=env, capture_output=True, text=True)


def test_denylisted_text_refuses(tmp_path):
    r = _export(tmp_path, "heimdal")
    assert r.returncode == 1 and "REFUSED" in r.stderr


def test_clean_snapshot_passes_without_internal_notes(tmp_path):
    r = _export(tmp_path, "zz-no-such-" + "text-zz")  # split: this file is exported too
    assert r.returncode == 0, r.stderr
    out = tmp_path / "out"
    assert (out / "README.md").is_file()
    assert (out / "docs/STATUS.md").is_file()             # keep-listed
    assert not (out / "docs/audit-2026-11").exists()      # private by default
    assert not (out / "docs/HEIMDAL-AUDIT-v1.md").exists()
