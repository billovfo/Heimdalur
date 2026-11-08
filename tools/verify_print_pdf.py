#!/usr/bin/env python3
"""Print/PDF verification of the dossier reports (audit 2026-11 G-03).

    python3 tools/verify_print_pdf.py --port 8405 [--out DIR] [--company 9667] [--person 7412]
                                      [--chromium PATH] [--route /path ...]

For the company dossier, the person dossier and the KYC report, in the dark
(default) and the light theme, with print media and background graphics on:

- legibility: every visible text element has WCAG contrast >= 4.5:1 (3:1 for
  large text) against its effective background; the page, the report header and
  the cards print on a light background; no text sits on a background image;
- layout: at A4 width (718 px) nothing is wider than the page;
- report structure: header with the entity name (read from the page), disclaimer,
  summary, source caveat and provenance appendix are visible; navigation,
  buttons and the action bar are hidden; every <details> is unfolded;
- the PDF: 1-6 pages, the entity name and "Proveniens" in its text, no button text.

Only 127.0.0.1 is reached (every other request is aborted). PDFs go to --out
(default: a fresh temp dir), never into the repo. --route checks other pages
(a negative control: a plain profile page must fail). Exit 1 on any failure.
"""
import argparse
import io
import pathlib
import sys
import tempfile

A4_CSS_WIDTH = 718  # 210 mm - 2 x 10 mm margin, at 96 dpi

AUDIT_JS = r"""
() => {
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return [0, 0, 0, 0];
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const over = (top, under) => { const a = top[3]; return [0, 1, 2].map(i => top[i] * a + under[i] * (1 - a)).concat(1); };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const hasImage = (el) => { const s = getComputedStyle(el);
    return s.backgroundImage !== 'none' || getComputedStyle(el, '::before').backgroundImage !== 'none'; };
  const bgOf = (el) => {  // composite background-colors from the element up to the page
    const stack = []; let image = false;
    for (let e = el; e; e = e.parentElement) {
      const c = parse(getComputedStyle(e).backgroundColor);
      if (hasImage(e)) image = true;
      if (c[3] > 0) stack.push(c);
      if (c[3] >= 1) break;
    }
    let bg = [255, 255, 255, 1];
    for (let i = stack.length - 1; i >= 0; i--) bg = over(stack[i], bg);
    return { bg, image };
  };
  const visible = (el) => { const s = getComputedStyle(el); const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && Number(s.opacity) > 0; };
  const fails = [];
  let n = 0;
  for (const el of document.body.querySelectorAll('*')) {
    if (el.closest('svg, canvas, .sr-only') || !visible(el)) continue;
    const own = [...el.childNodes].some(t => t.nodeType === 3 && t.textContent.trim());
    if (!own) continue;
    const s = getComputedStyle(el);
    const { bg, image } = bgOf(el);
    const fg = over(parse(s.color), bg);
    const size = parseFloat(s.fontSize), bold = Number(s.fontWeight) >= 700;
    const need = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
    const r = ratio(fg, bg);
    n++;
    if (r < need || image) fails.push(`${el.tagName.toLowerCase()}.${[...el.classList].join('.')} "${el.textContent.trim().slice(0, 40)}" ${r.toFixed(2)}:1${image ? ' on a background image' : ''}`);
  }
  const dark = [];
  for (const el of [document.body, ...document.querySelectorAll('.dossier-header, .card, .section, .stat-card')]) {
    if (!visible(el)) continue;
    const l = lum(bgOf(el).bg);
    if (l < 0.85) dark.push(`${el.tagName.toLowerCase()}.${[...el.classList].join('.')} background luminance ${l.toFixed(2)}`);
  }
  const shown = (sel) => [...document.querySelectorAll(sel)].filter(visible).length;
  const header = document.querySelector('.dossier-header h1');
  return {
    textElements: n, contrastFails: fails, darkBackgrounds: dark,
    entityName: header && visible(header) ? header.textContent.trim() : null,
    structure: {
      disclaimer: shown('.report-disclaimer') > 0,
      summary: shown('.report-summary') > 0,
      caveat: document.body.innerText.includes('Kildeforbehold'),
      appendix: shown('.dossier-footer') > 0,
    },
    // sort.js turns column headers into buttons: they print as headers, their .arrow is hidden
    visibleControls: [...document.querySelectorAll('button:not(th > button), .spine, .topbar, .dossier-actions, .tablist, .skiplink, .arrow')]
      .filter(visible).map(e => `${e.tagName.toLowerCase()}.${[...e.classList].join('.')} "${e.textContent.trim().slice(0, 30)}"`),
    closedDetails: [...document.querySelectorAll('details')].filter(d => !d.open).length,
    overflow: Math.max(document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ...[...document.body.querySelectorAll('*')].filter(visible).map(e => Math.round(e.getBoundingClientRect().right - document.documentElement.clientWidth))),
  };
}
"""


def check_page(page, url, theme, out_dir, slug):
    """Return a list of failure strings for one route in one theme."""
    from pypdf import PdfReader

    fails = []
    page.add_init_script(f"try {{ localStorage.setItem('heimdal_theme', '{theme}'); }} catch (e) {{}}")
    resp = page.goto(url, wait_until="load")
    if not resp or resp.status != 200:
        return [f"HTTP {resp.status if resp else 'no response'}"]
    page.wait_for_timeout(800)
    page.emulate_media(media="print")
    page.evaluate("() => window.dispatchEvent(new Event('beforeprint'))")
    page.set_viewport_size({"width": A4_CSS_WIDTH, "height": 1000})
    page.wait_for_timeout(300)
    a = page.evaluate(AUDIT_JS)
    if a["textElements"] < 20:
        fails.append(f"only {a['textElements']} visible text elements")
    fails += [f"contrast: {f}" for f in a["contrastFails"][:15]]
    if len(a["contrastFails"]) > 15:
        fails.append(f"contrast: ... {len(a['contrastFails'])} in total")
    fails += [f"dark background: {d}" for d in a["darkBackgrounds"][:10]]
    if not a["entityName"]:
        fails.append("no visible report header (.dossier-header h1)")
    fails += [f"structure: no visible {k}" for k, v in a["structure"].items() if not v]
    fails += [f"visible in print: {c}" for c in a["visibleControls"]]
    if a["closedDetails"]:
        fails.append(f"{a['closedDetails']} <details> not unfolded")
    if a["overflow"] > 1:
        fails.append(f"content {a['overflow']} px wider than the A4 page")
    pdf_path = out_dir / f"{slug}-{theme}.pdf"
    pdf = page.pdf(format="A4", print_background=True,
                   margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"})
    pdf_path.write_bytes(pdf)
    reader = PdfReader(io.BytesIO(pdf))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    pages = len(reader.pages)
    if not 1 <= pages <= 6:
        fails.append(f"PDF has {pages} pages")
    if a["entityName"] and a["entityName"].split()[0] not in text:
        fails.append("entity name missing from the PDF text")
    if "Proveniens" not in text:
        fails.append("provenance appendix missing from the PDF text")
    if "Udskriv" in text:
        fails.append("button text ('Udskriv') printed")
    print(f"  {slug} [{theme}] pages={pages} text_elements={a['textElements']} -> {pdf_path}")
    return fails


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8405)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--out", help="directory for the PDFs (default: a new temp dir)")
    ap.add_argument("--company", type=int, default=9667)
    ap.add_argument("--person", type=int, default=7412)
    ap.add_argument("--route", action="append", help="check this path instead of the three reports")
    ap.add_argument("--chromium", help="Chromium executable (default: Playwright's bundled one)")
    a = ap.parse_args()
    base = f"http://{a.host}:{a.port}"
    out_dir = pathlib.Path(a.out) if a.out else pathlib.Path(tempfile.mkdtemp(prefix="heimdal-print-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    routes = a.route or [f"/companies/{a.company}/dossier", f"/persons/{a.person}/dossier",
                         f"/companies/{a.company}/kyc"]

    from playwright.sync_api import sync_playwright

    failed = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=a.chromium)
        for route in routes:
            for theme in ("dark", "light"):
                ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
                ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(base + "/") else r.abort())
                slug = route.strip("/").replace("/", "_")
                fails = check_page(ctx.new_page(), base + route, theme, out_dir, slug)
                ctx.close()
                print(f"{'PASS' if not fails else 'FAIL'} {route} [{theme}]")
                for f in fails:
                    print(f"    {f}")
                failed += bool(fails)
        browser.close()
    total = 2 * len(routes)
    print(f"PRINT/PDF: {total - failed}/{total} passed (PDFs in {out_dir})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
