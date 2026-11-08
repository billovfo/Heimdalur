#!/usr/bin/env python3
"""Audit lane G check (frontend). Offline: reads repo files only. No DB, no
network, no browser. Exits 1 while a finding is open.

  G-01 API/registry text must not reach innerHTML unescaped
       (static/graph_network.js:257 renders node names as HTML -> stored DOM-XSS)
  G-02 the CSP must not need script-src 'unsafe-inline': no inline <script>
       blocks (JSON data blocks are fine) and no on*= handlers in templates
  G-01(geo)  report G-01: Geo-Kort has no demo fallback (no hard-coded cities or counts)
  G-07  report G-07: dashboard/Ændringslog/watchlist print change summaries
        through L.change_summary_label, and no raw change_category in visible text
  G-11  report G-11: every client-side CSV export uses window.heimdalDownloadCsv
        (static/app.js); no hand-built CSV, no data:text/csv URI
  G-03  print CSS makes every surface white (both themes); tools/verify_print_pdf.py
        takes --port/--out, writes nothing into the repo, needs no Chrome channel
  G-04  colour tokens meet WCAG AA 4.5:1: dark --text-3 on surface/bg, light
        --text-3/--calc/--accent/--info/--success/--warning/--danger, white on
        .btn-primary (and its hover)
  G-06  selects have an accessible name; a role=tablist holds only tabs; the
        pager nav is not named like the spine nav; no role=img svg wraps links;
        the scrollable network edge list is focusable
  H-7   test_heimdal_geo_ux.py asserts what static/geo_map.js does

Run from the repo root:  python3 tools/audit/check_g.py
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
FAIL = []

# `${expr}` inside a template literal assigned to innerHTML / insertAdjacentHTML,
# where expr carries registry text (name/label/title) and is not escaped.
SINK = re.compile(r"(?:innerHTML\s*[+]?=|insertAdjacentHTML\([^,]+,)\s*`(.*?)`", re.S)
TEXTY = re.compile(r"(\.name\b|\.label\b|\bdispLabel\b|display_name|\btitle\b)")
SAFE = re.compile(r"^\s*(escapeHtml|esc|escHtml)\(")


def check_g01():
    sources = list((ROOT / "static").glob("*.js")) + list((ROOT / "templates").glob("*.html"))
    for f in sources:
        text = f.read_text(encoding="utf-8")
        for m in SINK.finditer(text):
            for e in re.findall(r"\$\{(.*?)\}", m.group(1)):
                if TEXTY.search(e) and not SAFE.search(e):
                    ln = text.count("\n", 0, m.start()) + 1
                    FAIL.append(f"G-01 {f.relative_to(ROOT)}:{ln} unescaped ${{{e}}} in innerHTML")


def check_g02():
    web = (ROOT / "heimdal/modules/shared/web.py").read_text(encoding="utf-8")
    script_src = re.search(r"script-src([^;]*);", web)
    if script_src and "'unsafe-inline'" in script_src.group(1):
        FAIL.append("G-02 heimdal/modules/shared/web.py: CSP script-src still allows 'unsafe-inline'")
    for f in sorted((ROOT / "templates").glob("*.html")):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"<script(?![^>]*\bsrc=)(?![^>]*application/(ld\+)?json)[^>]*>", text):
            FAIL.append(f"G-02 {f.relative_to(ROOT)}:{text.count(chr(10), 0, m.start()) + 1} inline <script>")
        n = len(re.findall(r"\son[a-z]+\s*=\s*\"", text))
        if n:
            FAIL.append(f"G-02 {f.relative_to(ROOT)}: {n} inline on*= handler(s)")


def check_geo_demo():
    src = (ROOT / "heimdal/modules/geo/repository.py").read_text(encoding="utf-8")
    if re.search(r"_DEMO|company_count\"?\s*:\s*\d", src):
        FAIL.append("G-01(geo) heimdal/modules/geo/repository.py: demo cities/counts fallback")


def check_g07():
    for name in ("home.html", "changelog.html", "watchlist.html"):
        text = (ROOT / "templates" / name).read_text(encoding="utf-8")
        for m in re.finditer(r"\{\{\s*(?:ev|event)\.change_(?:summary|category)\b[^}]*\}\}", text):
            if "L." not in m.group(0):
                ln = text.count("\n", 0, m.start()) + 1
                FAIL.append(f"G-07 templates/{name}:{ln} raw {m.group(0)}")


def check_g11():
    for f in list((ROOT / "static").glob("*.js")) + list((ROOT / "templates").glob("*.html")):
        text = f.read_text(encoding="utf-8")
        if f.name != "app.js" and re.search(r"text/csv|new Blob\(\[csv", text):
            FAIL.append(f"G-11 {f.relative_to(ROOT)}: builds its own CSV (use window.heimdalDownloadCsv)")


def _lum(hex_):
    c = [int(hex_.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _block(css, head):
    """Body of the first `head {` ... `}` block (no nested braces)."""
    i = css.index(head + " {") + len(head) + 2
    return css[i:css.index("}", i)]


def _vars(body):
    return {k: v if len(v) == 7 else "#" + "".join(ch * 2 for ch in v[1:])
            for k, v in re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3})?)\b", body)}


def check_g03():
    css = (ROOT / "static/heimdal.css").read_text(encoding="utf-8")
    pr = css[css.index("@media print {"):]
    pvars = _vars(_block(pr, ':root, :root[data-theme="light"]')) if ':root, :root[data-theme="light"] {' in pr else {}
    for v in ("bg", "bg-subtle", "surface", "surface-elevated"):
        if pvars.get(v, "").lower() != "#ffffff":
            FAIL.append(f"G-03 static/heimdal.css @media print: --{v} is not white in both themes")
    if not re.search(r"\.card, \.section, \.dossier-header[^{]*\{ background: #fff; \}", pr):
        FAIL.append("G-03 static/heimdal.css @media print: cards/header keep their screen background")
    tool = (ROOT / "tools/verify_print_pdf.py").read_text(encoding="utf-8")
    if 'channel="chrome"' in tool or "docs/print-pdf-validation" in tool or "--port" not in tool:
        FAIL.append("G-03 tools/verify_print_pdf.py: hard-coded port/Chrome channel or writes into the repo")
    if "contrast" not in tool.lower():
        FAIL.append("G-03 tools/verify_print_pdf.py: does not assert contrast")


def check_g04():
    css = (ROOT / "static/heimdal.css").read_text(encoding="utf-8")
    dark, light = _vars(_block(css, ":root")), _vars(_block(css, ':root[data-theme="light"]'))
    pairs = [("dark --text-3 on --surface", dark["text-3"], dark["surface"]),
             ("dark --text-3 on --bg", dark["text-3"], dark["bg"]),
             ("light --text-3 on --bg-subtle", light["text-3"], light["bg-subtle"])]
    pairs += [(f"light --{v} on white", light[v], "#FFFFFF")
              for v in ("calc", "accent", "info", "success", "warning", "danger")]
    for sel in (".btn-primary", ".btn-primary:hover"):
        bg = re.findall(re.escape(sel) + r" \{ background: (#[0-9A-Fa-f]{6})", css)
        pairs.append((f"white on {sel}", "#FFFFFF", bg[-1] if bg else "#FFFFFF"))
    for name, fg, bg in pairs:
        if contrast(fg, bg) < 4.5:
            FAIL.append(f"G-04 static/heimdal.css: {name} is {contrast(fg, bg):.2f}:1 (< 4.5)")


def check_g06():
    t = {n: (ROOT / "templates" / n).read_text(encoding="utf-8")
         for n in ("company.html", "person.html", "entities.html", "changelog.html", "home.html",
                   "ejarayvirlit.html", "person_relations.html", "macros.html")}
    for n in ("company.html", "person.html"):
        m = re.search(r'role="tablist"[^>]*>(.*?)</(div|nav)>', t[n], re.S)
        if not m or "<a " in m.group(1):
            FAIL.append(f"G-06 templates/{n}: the role=tablist element holds a link (aria-required-children)")
    for n in ("entities.html", "changelog.html"):
        for s in re.findall(r"<select\b[^>]*>", t[n]):
            if "aria-label" not in s and "id=" not in s:
                FAIL.append(f"G-06 templates/{n}: {s[:50]} has no accessible name")
    for n in ("entities.html", "person_relations.html"):
        if 'class="pager" aria-label="Sider"' in t[n]:
            FAIL.append(f"G-06 templates/{n}: pager nav shares the spine's name 'Sider'")
    for n in ("home.html", "ejarayvirlit.html"):
        for svg in re.findall(r"<svg\b[^>]*>.*?</svg>", t[n], re.S):
            if 'role="img"' in svg.split(">", 1)[0] and ("<a " in svg or "<a\n" in svg):
                FAIL.append(f"G-06 templates/{n}: svg role=img wraps links (nested-interactive)")
    if not re.search(r'id="network-ledger-[^"]*"[^>]*tabindex="0"', t["macros.html"]):
        FAIL.append("G-06 templates/macros.html: scrollable network edge list is not focusable")


def check_h7():
    test = (ROOT / "test_heimdal_geo_ux.py").read_text(encoding="utf-8")
    js = (ROOT / "static/geo_map.js").read_text(encoding="utf-8")
    for lit in re.findall(r'assert "([^"]+)" in source', test):
        if lit not in js:
            FAIL.append(f"H-7 test_heimdal_geo_ux.py asserts {lit!r}, static/geo_map.js has no such text")


if __name__ == "__main__":
    check_g01()
    check_g02()
    check_geo_demo()
    check_g07()
    check_g11()
    check_g03()
    check_g04()
    check_g06()
    check_h7()
    for line in FAIL:
        print("FAIL", line)
    print("OK" if not FAIL else f"{len(FAIL)} open")
    sys.exit(1 if FAIL else 0)
