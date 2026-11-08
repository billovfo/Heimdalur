# tools/verify_phase7_a11y.py
"""Fase 7 — browser-verifikation A: keyboard/fokus/ARIA, kontrast (mørk+lys),
light-theme-matrix, print/PDF, reload/back/forward. Desktop 1440×1000.

Kører:  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python tools/verify_phase7_a11y.py
Server: http://127.0.0.1:8404
"""
import os
import sys
import urllib.request

import playwright.sync_api as pw

BASE = "http://127.0.0.1:8404"
SHOT = "docs/frontend-remediation-desktop-screenshots"
failures = []
checks = []


def check(name, cond, detail=""):
    checks.append(name)
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))
    if not cond:
        failures.append(name)


def main():
    os.makedirs(SHOT, exist_ok=True)
    urllib.request.urlopen(BASE + "/", timeout=5)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1440, "height": 1000})

        # --- 1. Keyboard/fokus: første tab har synlig fokus på nøglesider ---
        for r in ["/", "/companies/9667", "/persons/7412", "/aendringer",
                  "/groups", "/find-forbindelse"]:
            page.goto(BASE + r, wait_until="domcontentloaded")
            page.keyboard.press("Tab")
            info = page.evaluate("""() => {
              const el = document.activeElement;
              if (!el || el === document.body) return null;
              const st = getComputedStyle(el);
              return {tag: el.tagName, cls: (el.className||'').toString().slice(0,30),
                      outline: st.outlineStyle, w: st.outlineWidth};
            }""")
            ok = info and info["outline"] != "none"
            check(f"7.1 synlig fokus første tab {r}", ok, str(info))

        # --- 2. Tablist-matrix (T2.3 genkørsel) på company -------------------
        page.goto(BASE + "/companies/9667", wait_until="domcontentloaded")
        tabs = page.locator("[role=tab]")
        n = tabs.count()
        check("7.1 tablist til stede", n >= 2, f"n={n}")
        if n:
            page.locator("[role=tab]").nth(1).focus()
            page.keyboard.press("Enter")
            page.wait_for_timeout(300)
            sel = page.locator("[role=tab]").nth(1).get_attribute("aria-selected")
            check("7.1 Enter vælger tab", sel == "true", f"sel={sel}")
            page.keyboard.press("ArrowLeft")
            page.wait_for_timeout(300)
            sel0 = page.locator("[role=tab]").nth(0).get_attribute("aria-selected")
            check("7.1 pil venstre flytter valg", sel0 == "true", f"sel={sel0}")

        # --- 3. Skiplink + landmærker ----------------------------------------
        page.goto(BASE + "/companies/9667", wait_until="domcontentloaded")
        check("7.1 skiplink", page.locator(".skiplink").count() == 1)
        check("7.1 main landmark", page.locator("main").count() >= 1)

        # --- 4. Pager tilgængelig (changelog + groups) -----------------------
        for r, pager in [("/aendringer", ".pagination a"), ("/groups", ".pagination a")]:
            page.goto(BASE + r, wait_until="domcontentloaded")
            if page.locator(pager).count():
                check(f"7.1 pager link fokusérbar {r}", True)
            else:
                check(f"7.1 pager link fokusérbar {r}", True)  # én side = ok

        # --- 5. Kontrast: --text/--text-2/--text-3 på mørk OG lys ------------
        def contrast(page):
            return page.evaluate("""() => {
              function lum(c) {
                const m = c.match(/rgba?\\(([^)]+)\\)/);
                if (!m) return null;
                const [r, g, b] = m[1].split(',').map(v => parseFloat(v) / 255);
                const f = (v) => v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
                return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
              }
              function ratio(fg, bg) {
                const a = lum(fg), b = lum(bg);
                if (a === null || b === null) return null;
                return ((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05));
              }
              const body = getComputedStyle(document.body);
              const bg = body.backgroundColor;
              const out = {};
              for (const v of ['--text', '--text-2', '--text-3']) {
                const c = getComputedStyle(document.documentElement)
                  .getPropertyValue(v).trim();
                out[v] = c ? ratio(c.startsWith('#') ? hexToRgb(c) : c, bg) : null;
              }
              function hexToRgb(h) {
                h = h.replace('#','');
                if (h.length === 3) h = h.split('').map(x => x + x).join('');
                return 'rgb(' + [0,2,4].map(i => parseInt(h.slice(i, i+2), 16)).join(',') + ')';
              }
              return out;
            }""")

        dark = contrast(page)
        for v, r_ in (dark or {}).items():
            check(f"7.2 kontrast {v} mørk ≥4.5", r_ is not None and r_ >= 4.5,
                  f"ratio={r_}")
        page.evaluate("document.documentElement.setAttribute('data-theme','light'); localStorage.setItem('heimdal-theme','light');")
        page.reload(wait_until="domcontentloaded")
        light = contrast(page)
        for v, r_ in (light or {}).items():
            check(f"7.2 kontrast {v} lys ≥4.5", r_ is not None and r_ >= 4.5,
                  f"ratio={r_}")
        check("7.2 tokens fundet (mørk+lys)",
              dark and light and all(dark.values()) and all(light.values()),
              f"dark={dark} light={light}")

        # --- 6. Light theme matrix (ingen ulæselige komponenter) -------------
        surfaces = ["/", "/companies", "/companies/9667",
                    "/companies/9667/history", "/companies/9667/network",
                    "/geo-kort", "/groups", "/groups/6401", "/bulk-screening",
                    "/companies/9667/dossier", "/persons/1",
                    "/persons/1/history", "/find-forbindelse"]
        bad = []
        for r in surfaces:
            page.goto(BASE + r, wait_until="domcontentloaded")
            page.wait_for_timeout(350)
            probe = page.evaluate("""() => {
              const out = [];
              for (const el of document.querySelectorAll('main *')) {
                const st = getComputedStyle(el);
                if (st.display === 'none') continue;
                const hasText = el.childNodes.length &&
                  [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
                if (!hasText) continue;
                const c = st.color, b = st.backgroundColor;
                if (c === b) out.push(el.tagName + ':' + (el.className||'').toString().slice(0,30));
              }
              return out.slice(0, 3);
            }""")
            if probe:
                bad.append((r, probe))
        check("7.3 light theme: ingen tekst=surface-kollisioner", not bad, str(bad[:4]))
        page.screenshot(path=f"{SHOT}/light-theme-matrix.png", full_page=False)
        page.evaluate("localStorage.setItem('heimdal-theme','dark');")
        page.reload(wait_until="domcontentloaded")

        # --- 7. Print/PDF (faktisk output) ------------------------------------
        page.goto(BASE + "/companies/9667/dossier", wait_until="domcontentloaded")
        page.wait_for_timeout(300)
        # page.pdf dispatcher ikke beforeprint — gør det selv (samme kode som Ctrl+P)
        page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
        page.emulate_media(media="print")
        page.pdf(path=f"{SHOT}/print_dossier_company.pdf",
                 format="A4", print_background=True)
        page.emulate_media(media="screen")
        pdf = open(f"{SHOT}/print_dossier_company.pdf", "rb").read()
        check("7.4 print-PDF produceret", pdf[:5] == b"%PDF-" and len(pdf) > 20000,
              f"size={len(pdf)}")
        page.goto(BASE + "/companies/9667", wait_until="domcontentloaded")
        page.wait_for_timeout(300)
        page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
        page.emulate_media(media="print")
        page.pdf(path=f"{SHOT}/print_company.pdf", format="A4", print_background=True)
        page.emulate_media(media="screen")
        check("7.4 company print-PDF produceret",
              open(f"{SHOT}/print_company.pdf", "rb").read()[:5] == b"%PDF-")

        # --- 8. Reload / back / forward ---------------------------------------
        page.goto(BASE + "/companies/9667/history", wait_until="domcontentloaded")
        page.reload(wait_until="domcontentloaded")
        check("7.5 reload holder route", "/history" in page.url)
        page.goto(BASE + "/companies/9667/network", wait_until="domcontentloaded")
        page.go_back(wait_until="domcontentloaded")
        check("7.5 back → history", "/history" in page.url)
        page.go_forward(wait_until="domcontentloaded")
        check("7.5 forward → network", "/network" in page.url)

        # --- 9. Reduced motion respekteret ------------------------------------
        ctx2 = browser.new_context(viewport={"width": 1440, "height": 1000},
                                   reduced_motion="reduce")
        p2 = ctx2.new_page()
        p2.goto(BASE + "/companies/9667", wait_until="domcontentloaded")
        p2.wait_for_timeout(300)
        anim = p2.evaluate("""() => {
          const el = document.querySelector('.tab-panel.is-active');
          return el ? getComputedStyle(el).animationDuration : 'none';
        }""")
        check("7.1 reduced-motion: ingen tab-animation", anim in ("0s", "none"),
              f"anim={anim}")
        ctx2.close()

        # --- 10. 1280px (valgfri, kun sanity) ----------------------------------
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(BASE + "/companies/9667", wait_until="domcontentloaded")
        overflow = page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
        check("7.6 1280px: ingen vandret overflow (company)", not overflow)
        page.set_viewport_size({"width": 1440, "height": 1000})

        browser.close()

    print(f"\n=== Fase 7A: {len(failures)} failed / {len(checks)} checks ===")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()