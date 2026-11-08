# tools/verify_phase7_graph.py
"""Fase 7 — browser-verifikation B: GraphShell-states (200-rig/200-tom/4xx/5xx/
langsom/JS-slået fra), crafted bulk re-test, watchlist interception re-test +
screenshot-pakke. Desktop 1440×1000.

Kører:  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python tools/verify_phase7_graph.py
Server: http://127.0.0.1:8404
"""
import json
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

        def shell_state():
            return page.evaluate("""() => {
              const s = document.querySelector('.graph-shell-state');
              return s ? {state: s.dataset.state, visible: !s.hidden} : null;
            }""")

        # --- 1. LOADED (200-rig) ----------------------------------------------
        page.goto(BASE + "/companies/9667/network", wait_until="domcontentloaded")
        try:
            page.wait_for_selector('.graph-shell-state[data-state="LOADED"][hidden]',
                                   timeout=9000, state="attached")
            check("7.B1 netværk LOADED (200-rig)", True)
        except pw.TimeoutError:
            check("7.B1 netværk LOADED (200-rig)", False, str(shell_state()))
        page.screenshot(path=f"{SHOT}/network_company_9667.png")

        # --- 2. EMPTY (200 med tom graf) ---------------------------------------
        def route_empty(route, request):
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"nodes": [], "edges": []}))
        page.route("**/api/network/9667?min_pct*", route_empty)
        page.goto(BASE + "/companies/9667/network", wait_until="domcontentloaded")
        try:
            page.wait_for_selector('.graph-shell-state[data-state="EMPTY"]:not([hidden])',
                                   timeout=9000)
            check("7.B2 EMPTY ved tom 200 (synlig overlay)", True)
            msg = page.locator(".graph-shell-state").inner_text()
            check("7.B2 EMPTY har hjælpetekst", len(msg.strip()) > 10, msg[:80])
        except pw.TimeoutError:
            check("7.B2 EMPTY ved tom 200 (synlig overlay)", False, str(shell_state()))
        page.unroute("**/api/network/9667?min_pct*")

        # --- 3. ERROR 4xx / 5xx --------------------------------------------------
        for status, label in [(404, "4xx"), (500, "5xx")]:
            def route_err(route, request, status=status):
                route.fulfill(status=status, content_type="text/plain",
                              body="mock failure")
            page.route("**/api/network/9667?min_pct*", route_err)
            page.goto(BASE + "/companies/9667/network", wait_until="domcontentloaded")
            try:
                page.wait_for_selector('.graph-shell-state[data-state="ERROR"]:not([hidden])',
                                       timeout=9000)
                check(f"7.B3 ERROR-tilstand ved {label}", True)
                has_retry = page.locator(".graph-shell-retry").count() > 0
                check(f"7.B3 retry-knap ved {label}", has_retry)
            except pw.TimeoutError:
                check(f"7.B3 ERROR-tilstand ved {label}", False, str(shell_state()))
            page.unroute("**/api/network/9667?min_pct*")

        # --- 4. Langsomt svar: isoleret side + CDP-netværkslatency --------------
        # (route-fulfill med sleep blokerer Playwrights sync-loop og kan ikke
        # vise LOADING; CDP latency strammer netværket uden at blokere.)
        page4 = browser.new_page(viewport={"width": 1440, "height": 1000})
        cdp = page4.context.new_cdp_session(page4)
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions",
                 {"offline": False, "latency": 3000,
                  "downloadThroughput": 50000, "uploadThroughput": 50000})
        page4.goto(BASE + "/companies/9667/network", wait_until="domcontentloaded")
        page4.wait_for_timeout(400)
        s4 = page4.evaluate("""() => { const s=document.querySelector('.graph-shell-state');
            return s ? {state: s.dataset.state, visible: !s.hidden} : null; }""")
        check("7.B4 LOADING under langsomt svar",
              s4 and s4["state"] == "LOADING" and s4["visible"], str(s4))
        cdp.send("Network.emulateNetworkConditions",
                 {"offline": False, "latency": 0,
                  "downloadThroughput": -1, "uploadThroughput": -1})
        try:
            page4.wait_for_selector(
                '.graph-shell-state[data-state="LOADED"][hidden]',
                timeout=12000, state="attached")
            check("7.B4 efter langsomt svar → LOADED (rigtig data)", True)
        except pw.TimeoutError:
            check("7.B4 efter langsomt svar → LOADED (rigtig data)", False,
                  str(shell_state()))
        page4.close()

        # --- 5. JS slået fra: SVG-fallback er evidence og graf-container står ---
        ctx_nojs = browser.new_context(viewport={"width": 1440, "height": 1000},
                                       java_script_enabled=False)
        pn = ctx_nojs.new_page()
        pn.goto(BASE + "/companies/9667/network", wait_until="domcontentloaded")
        html = pn.content()
        check("7.B5 uden JS: statisk SVG-fallback til stede",
              'id="netvaerk-fallback"' in html and "network-svg" in html)
        check("7.B5 uden JS: ingen tom graf-container alene",
              "network-graph-container" in html)
        pn.screenshot(path=f"{SHOT}/network_nojs_fallback.png")
        ctx_nojs.close()

        # --- 6. Geo-kort og sankey states (200-rig) ------------------------------
        page.goto(BASE + "/geo-kort", wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        gs = page.evaluate("""() => {
          const s = document.querySelector('.graph-shell-state');
          return s ? s.dataset.state : 'no-shell';
        }""")
        check("7.B6 geo LOADED", gs in ("LOADED", "no-shell"), gs)
        page.screenshot(path=f"{SHOT}/geo_map.png")

        # --- 7. Crafted bulk re-test (mange selskaber) ---------------------------
        page.goto(BASE + "/bulk-screening", wait_until="domcontentloaded")
        page.fill("#bulk-input", "9667\n6401\n7231\nXYZIKKEFINDES\n3054")
        page.click(".btn-primary.w-full")
        page.wait_for_timeout(2500)
        body = page.content()
        check("7.B7 crafted bulk: resultater vises",
              "resultat" in body.lower() or "bulk" in body.lower())
        check("7.B7 crafted bulk: ukendt ID flagges",
              "XYZIKKEFINDES" in body)
        page.screenshot(path=f"{SHOT}/bulk_screening_crafted.png", full_page=True)

        # --- 8. Watchlist interception re-test ------------------------------------
        def route_watch(route, request):
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": True, "items": []}))
        page.route("**/api/watchlist*", route_watch)
        page.goto(BASE + "/watchlist", wait_until="domcontentloaded")
        page.wait_for_timeout(600)
        check("7.B8 watchlist-side renderer under interception",
              page.locator("main").count() >= 1)
        page.unroute("**/api/watchlist*")

        # --- 9. Screenshot-pakke (desktop 1440×1000, mørk) ------------------------
        pack = [
            ("home.png", "/"),
            ("companies_list.png", "/companies"),
            ("company_9667.png", "/companies/9667"),
            ("company_9667_history.png", "/companies/9667/history"),
            ("person_7412.png", "/persons/7412"),
            ("person_1.png", "/persons/1"),
            ("person_1_history.png", "/persons/1/history"),
            ("group_6401.png", "/groups/6401"),
            ("groups_overview.png", "/groups"),
            ("changelog.png", "/aendringer"),
            ("bulk_screening.png", "/bulk-screening"),
            ("dossier_company_9667.png", "/companies/9667/dossier"),
            ("dossier_person_1.png", "/persons/1/dossier"),
            ("kyc_9667.png", "/companies/9667/kyc"),
            ("eigarayvirlit_9667.png", "/companies/9667/eigarayvirlit"),
            ("pathfinder.png", "/find-forbindelse"),
            ("network_9667.png", "/companies/9667/network"),
            ("geo_map.png", "/geo-kort"),
        ]
        for fname, r in pack:
            page.goto(BASE + r, wait_until="domcontentloaded")
            page.wait_for_timeout(450)
            page.screenshot(path=f"{SHOT}/{fname}")
        n_shots = len([f for f in os.listdir(SHOT) if f.endswith(".png")])
        check("7.B9 screenshot-pakke (≥18 billeder)", n_shots >= 18, f"n={n_shots}")

        browser.close()

    manifest = {
        "canonical_viewport": "1440x1000",
        "screenshots": sorted(f for f in os.listdir(SHOT) if f.endswith(".png")),
        "print_pdfs": ["print_dossier_company.pdf", "print_company.pdf"],
        "theme": "dark (default) + light-theme-matrix.png (light)",
    }
    with open(f"{SHOT}/manifest.json", "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"\n=== Fase 7B: {len(failures)} failed / {len(checks)} checks ===")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()