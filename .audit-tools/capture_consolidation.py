"""Product UX consolidation — §69 comparison screenshot pack (tooling only).

Captures BEFORE-style evidence of the NEW IA (after-state) for the same real
cases used in the product audit: company 9667 / 9287, person 7412 / 2321,
plus 390x844 mobile. Tab evidence uses data-tab activation (same JS path as
a user click).
"""
import json, os
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8002"
OUT = "docs/frontend-audit-screenshots/consolidation"
os.makedirs(OUT, exist_ok=True)
NOW = datetime.now(timezone.utc).isoformat()
manifest = []


def add(file, route, tab, status, viewport="1440x1000", note=""):
    manifest.append({"file": f"{OUT}/{file}".replace("docs/", "docs/", 1) if False else f"docs/frontend-audit-screenshots/consolidation/{file}",
                     "route": route, "tab_or_section": tab, "viewport": viewport,
                     "captured_at": NOW, "http_status": status, "notes": note})


def settle(page, extra=350):
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.evaluate("() => document.fonts.ready")
    page.wait_for_timeout(extra)


def capture(page, file, route, tab, viewport="1440x1000", note="", full=False):
    r = page.goto(BASE + route, wait_until="domcontentloaded", timeout=60000)
    settle(page)
    if tab:
        loc = page.locator(f'.tablist [data-tab="{tab}"]').first
        if loc.count():
            loc.click()
            page.wait_for_timeout(500)
    page.screenshot(path=f"{OUT}/{file}", full_page=full)
    add(file, route, tab or "(default)", r.status if r else 0, viewport, note)
    print("  ", file, flush=True)


with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="chrome")
    page = browser.new_page(viewport={"width": 1440, "height": 1000})

    print("— company 9667 —")
    for tab in ("overblik", "personer", "ejerskab", "relationer", "analyse", "netvaerk", "kilder"):
        capture(page, f"after-company-9667-{tab}.png", "/companies/9667", tab)
    capture(page, "after-company-9667-historik-page.png", "/companies/9667/history", None)
    capture(page, "after-company-9667-skraseting.png", "/companies/9667/skraseting", None)
    capture(page, "after-company-9667-network-full.png", "/companies/9667/network", None)

    print("— company 9287 (families) —")
    capture(page, "after-company-9287-analyse-families.png", "/companies/9287", "analyse")

    print("— person 7412 —")
    for tab in ("overblik", "roller", "ejerskab", "relationer", "analyse", "netvaerk", "kilder"):
        capture(page, f"after-person-7412-{tab}.png", "/persons/7412", tab)
    capture(page, "after-person-7412-historik-page.png", "/persons/7412/history", None)
    capture(page, "after-person-7412-network-full.png", "/persons/7412/network", None)

    print("— person 2321 (plain) —")
    capture(page, "after-person-2321-overblik.png", "/persons/2321", "overblik")

    print("— mobile 390x844 —")
    m = browser.new_page(viewport={"width": 390, "height": 844})
    capture(m, "mobile-after-company-9667-overblik.png", "/companies/9667", "overblik", "390x844")
    capture(m, "mobile-after-company-9667-personer.png", "/companies/9667", "personer", "390x844")
    capture(m, "mobile-after-company-9667-analyse.png", "/companies/9667", "analyse", "390x844")
    capture(m, "mobile-after-person-7412-overblik.png", "/persons/7412", "overblik", "390x844")
    capture(m, "mobile-after-person-7412-analyse.png", "/persons/7412", "analyse", "390x844")
    capture(m, "mobile-after-person-7412-netvaerk.png", "/persons/7412", "netvaerk", "390x844")

    browser.close()

with open(f"{OUT}/manifest.json", "w") as fh:
    json.dump({"captured_at": NOW, "base": BASE, "count": len(manifest),
               "shots": manifest}, fh, indent=2, ensure_ascii=False)
print(f"manifest: {len(manifest)} shots")