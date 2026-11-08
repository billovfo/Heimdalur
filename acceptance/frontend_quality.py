from heimdal.pdfhist.db import qa_dsn
#!/usr/bin/env python3
"""HEIMDAL FRONTEND QUALITY detectors (addendum §110–§114).

Run from the repo root under a test env (tools/gate.sh runs it report-only):
      python3 -m acceptance.frontend_quality
  Layer B needs a running portal: HEIMDAL_ACCEPTANCE_URL=http://127.0.0.1:<port>
  (start it on the same env); without it layer B is SKIPPED. Requests to any
  other host are blocked. Not a pytest module (H-2): it is a __main__ runner.

Two layers:
  A) Flask test client (no browser): raw enum leaks, dev labels, § labels,
     default tabs, history tab parity, family collapse, reading width,
     same-day disclaimer, one H1 per page, ISO date leaks.
  B) Playwright (real browser, channel="chrome"): whole-page mobile overflow
     at 390px (§111), accessible names for buttons/inputs/selects (§112),
     visible focus ring sample (§66), tablet 768 spot (§108).
Playwright layer skips with an explicit SKIP marker if no portal URL is given or
Chrome is unavailable — the owner acceptance flow still requires it.
"""
import os
import re
import sys

os.environ.setdefault("PDF_HISTORY_UI_ENABLED", "true")
os.environ.setdefault("REGISTRY_ANALYTICS_ENABLED", "1")
os.environ.setdefault("REGISTRY_ANALYTICS_GRAPH_ENABLED", "1")
os.environ.setdefault("HEIMDAL_READ_DB", qa_dsn())

from heimdal.app import app  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))


# --- visible-text helpers (exclude scripts, styles, option VALUES, sr-only) --
def visible(html):
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    html = re.sub(r"<span class=\"sr-only\".*?</span>", " ", html, flags=re.S)
    html = re.sub(r"<span class=\"sr-only\".*?</span>", " ", html, flags=re.S)
    html = re.sub(r"<option[^>]*>", "<option> ", html)          # keep label, drop value attr
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"\s+", " ", html)
    return html


RAW_ENUM = re.compile(
    r"(CONTROL_ACTOR_CHANGED|OWNERSHIP_ADDED|OWNERSHIP_REMOVED|"
    r"OWNERSHIP_PERCENTAGE_CHANGED|ROLE_APPOINTED|ROLE_RESIGNED|STATUS_CHANGED|"
    r"FETCHED_OK|FETCH_FAILED|OVER_100|EXACTLY_100|UNDER_100|"
    r"member_set_hash|support_episode_count|role_context|DISCOVERED|RETRYABLE_ERROR)")
DEV_LABEL = re.compile(r"(GRAPH PATHFINDER|HEIR-SOT|CORPORATE GROUP DETECTOR|Ultimate Parent|Netværksalliance|Magtgruppe)")
SECTION_NUM = re.compile(r"§\d+(\.\d+)?")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

PRODUCT_ROUTES = ["/", "/companies", "/persons", "/companies/9667", "/persons/7412",
                  "/overvaagning", "/aendringer", "/bulk-screening", "/find-forbindelse",
                  "/groups", "/bestand", "/compliance", "/geo-kort", "/persons/relationer",
                  "/companies/9667/history", "/persons/7412/history",
                  "/companies/9667/dossier", "/persons/7412/dossier"]

# Source/technical surfaces where raw tokens may appear in visible text
TECHNICAL_ROUTES = {"/companies/9667/kilder": True}


def layer_a():
    print("=== LAYER A — Flask test client (DOM/text detectors) ===")
    app.config["TESTING"] = True
    pages = {}
    with app.test_client() as cl:
        for r in PRODUCT_ROUTES:
            resp = cl.get(r)
            check(f"route 200: {r}", resp.status_code == 200, str(resp.status_code))
            pages[r] = resp.get_data(as_text=True)

        # §110 raw enum tokens in visible text
        for r, html in pages.items():
            if r.startswith("/companies/9667/kilder") or r.startswith("/companies/9667/skraseting"):
                continue  # source-evidence surface: raw tokens allowed there
            leaks = RAW_ENUM.findall(visible(html))
            check(f"no raw enum tokens (visible): {r}", not leaks, ",".join(sorted(set(leaks))[:4]))

        # §110 developer labels in visible text
        for r, html in pages.items():
            bad = DEV_LABEL.findall(visible(html))
            check(f"no developer labels: {r}", not bad, ",".join(sorted(set(bad))[:4]))

        # §110 no § section numbers on primary product pages
        for r in ["/", "/companies", "/persons", "/companies/9667", "/persons/7412",
                  "/overvaagning", "/aendringer", "/bulk-screening", "/find-forbindelse",
                  "/groups", "/bestand", "/companies/9667/history", "/persons/7412/history",
                  "/companies/9667/dossier", "/persons/7412/dossier"]:
            if r not in pages:
                continue
            bad = SECTION_NUM.findall(visible(pages[r]))
            check(f"no § labels: {r}", not bad, ",".join(bad[:5]))

        # §114/§71 no raw ISO dates in product visible text (source views allowed)
        for r in ["/aendringer", "/companies/9667/history", "/persons/7412/history",
                  "/", "/overvaagning", "/persons/7412", "/companies/9667"]:
            bad = ISO_DATE.findall(visible(pages[r]))
            check(f"no raw ISO dates: {r}", not bad, ",".join(bad[:4]))

        # §126 default tab = Overblik on entity pages
        for r, tab in (("/companies/9667", "overblik"), ("/persons/7412", "overblik")):
            html = pages[r]
            active = re.search(r'class="tab-panel is-active" data-panel="(\w+)"', html)
            check(f"default tab is Overblik: {r}",
                  bool(active) and active.group(1) == tab,
                  active.group(1) if active else "no active panel")
            check(f"no kontrol tab: {r}", 'data-tab="kontrol"' not in html)

        # §17 history pages mirror the entity tab IA
        for r, tabs in (("/companies/9667/history", ["Overblik", "Personer", "Ejerskab",
                                                    "Historik", "Relationer", "Netværk", "Kilder"]),
                        ("/persons/7412/history", ["Overblik", "Roller", "Ejerskab",
                                                   "Historik", "Relationer", "Netværk", "Kilder"])):
            html = pages[r]
            for label in tabs:
                check(f"history tab parity {r}: {label}", f">{label}</a>" in html)
            check(f"history page: no 'Kontrol' tab {r}", ">Kontrol</a>" not in html)
            check(f"history page: no 'Oversigt' label {r}", ">Oversigt</a>" not in html)

        # §33 same-day disclaimer exists when ≥2 events share a date
        html = pages["/companies/9667/history"]
        dates = re.findall(r"<time datetime=\"([^\"]+)\"", html)
        dup = len(dates) != len(set(dates))
        if dup:
            check("same-day disclaimer rendered when same-day events exist",
                  "samme dag kan ikke fastslås" in html)
        else:
            check("same-day disclaimer logic present in template",
                  "samme dag kan ikke fastslås" in open("templates/company_history.html").read())

        # §87/§124 constellation DOM noise: families collapsed, capped headlines
        html = pages["/companies/9667"]
        fam_cards = html.count("constellation-family")
        sub_details = html.count("family-subgroups")
        check("constellation families collapse (subgroups inside <details>)",
              sub_details > 0, f"families={fam_cards} subgroup-details={sub_details}")
        check("constellation headlines capped at 8",
              fam_cards <= 8, str(fam_cards))

        # §5 reading width classes present on descriptive blocks
        check("reading-width applied (css + template)",
              ".reading" in open("static/heimdal.css").read()
              and ("section-lede" in pages["/companies/9667"] or 'class="reading"' in pages["/companies/9667"]))

        # §113 one H1 per page
        for r, html in pages.items():
            n = len(re.findall(r"<h1[\s>]", html))
            check(f"exactly one H1: {r}", n == 1, str(n))

        # §97 browser title reflects entity
        t = re.search(r"<title>([^<]+)</title>", pages["/companies/9667"])
        check("company title contains entity name",
              bool(t) and "9667" not in t.group(1) and "Betri" in t.group(1),
              t.group(1) if t else "")


def layer_b():
    print("=== LAYER B — Playwright (mobile overflow, accessible names, focus) ===")
    base = os.environ.get("HEIMDAL_ACCEPTANCE_URL", "").rstrip("/")
    if not base:
        print("SKIP layer B: no HEIMDAL_ACCEPTANCE_URL (a running portal) given")
        return
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        check("playwright available", False, f"import failed: {exc}")
        return

    def local_only(route):  # no request leaves the portal under test
        if route.request.url.startswith(base + "/"):
            route.continue_()
        else:
            route.abort()

    MOBILE = ["/", "/companies/9667", "/persons/7412", "/overvaagning",
              "/bulk-screening", "/find-forbindelse", "/aendringer", "/groups",
              "/companies/9667/history", "/persons/7412/history",
              "/companies/9667/network", "/persons/7412/network",
              # review fund 2 / observation 7: parameterized routes too
              "/companies/9667/dossier", "/persons/7412/dossier",
              "/companies/9667/kyc", "/companies/9667/skraseting",
              "/companies/9667/eigarayvirlit", "/groups/6401"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        # §111 whole-page overflow at 390px
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        ctx.route("**/*", local_only)
        page = ctx.new_page()
        overflow = []
        for r in MOBILE:
            page.goto(base + r, wait_until="domcontentloaded")
            page.wait_for_timeout(250)
            sw = page.evaluate("document.documentElement.scrollWidth")
            if sw > 391:
                overflow.append((r, sw))
        check("mobile 390px: no whole-page horizontal overflow (all required routes)",
              not overflow, str(overflow))

        # §112 accessible names for controls on key routes
        unlabeled = []
        for r in ["/", "/companies/9667", "/persons/7412", "/bulk-screening",
                  "/find-forbindelse", "/aendringer", "/companies/9667/network"]:
            page.goto(base + r, wait_until="domcontentloaded")
            page.wait_for_timeout(250)
            bad = page.evaluate("""() => {
              const out = [];
              const inClosedDetails = (el) => {
                let n = el.closest('details');
                return !!n && !n.open;
              };
              for (const el of document.querySelectorAll('button, input, select, textarea')) {
                if (el.closest('[aria-hidden="true"]')) continue;
                if (inClosedDetails(el)) continue; // disclosure-indhold er ikke eksponeret
                const st = getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden') continue;
                const name = el.getAttribute('aria-label') || el.getAttribute('title')
                  || (el.labels && el.labels.length ? el.labels[0].textContent.trim() : '')
                  || (el.id && document.querySelector(`label[for="${el.id}"]`) ? document.querySelector(`label[for="${el.id}"]`).textContent.trim() : '')
                  || el.innerText?.trim() || el.value || '';
                if (!name) out.push(el.tagName + ':' + (el.className || '').toString().slice(0, 40));
              }
              return out;
            }""")
            if bad:
                unlabeled.append((r, bad[:4]))
        check("accessible names for all controls (buttons/inputs/selects)",
              not unlabeled, str(unlabeled))

        # §66 focus ring visible (sample: first tab button on company page)
        page.goto(base + "/companies/9667", wait_until="domcontentloaded")
        page.keyboard.press("Tab")
        outline = page.evaluate("""() => {
          const el = document.activeElement;
          if (!el) return null;
          const st = getComputedStyle(el);
          return st.outlineStyle !== 'none' && st.outlineWidth !== '0px';
        }""")
        check("visible focus state on first stop", bool(outline), str(outline))
        ctx.close()

        # §108 tablet spot check at 768px
        ctx768 = browser.new_context(viewport={"width": 768, "height": 1024})
        ctx768.route("**/*", local_only)
        p768 = ctx768.new_page()
        tablet_overflow = []
        for r in ["/companies/9667", "/persons/7412", "/companies/9667/network", "/"]:
            p768.goto(base + r, wait_until="domcontentloaded")
            p768.wait_for_timeout(250)
            sw = p768.evaluate("document.documentElement.scrollWidth")
            if sw > 769:
                tablet_overflow.append((r, sw))
        check("tablet 768px: no whole-page overflow (spot check)",
              not tablet_overflow, str(tablet_overflow))
        ctx768.close()
        browser.close()


def main():
    layer_a()
    layer_b()
    print(f"\nFRONTEND QUALITY DETECTORS: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for name, detail in FAIL:
            print(f"  FAILED: {name} — {detail}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()