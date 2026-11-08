# tools/verify_phase4_graph.py
"""Browser-verifikation af Fase 4 (T4.3/T4.4/T4.6): GraphShell-states,
graph-kontrol-mål/fokus og Pathfinder-navne-resolver — rigtig browser-DOM.

Kører:  python tools/verify_phase4_graph.py
Server: http://127.0.0.1:8404 (startes af kalderen).
"""
import os
import sys
import urllib.request

import playwright.sync_api as pw
import psycopg

BASE = "http://127.0.0.1:8404"
failures = []
checks = []


def display_name(entity_id):
    """Register name read from HEIMDAL_READ_DB, never hard-coded (I-5)."""
    with psycopg.connect(os.environ["HEIMDAL_READ_DB"]) as c:
        return c.execute("SELECT display_name FROM canonical_entity"
                         " WHERE canonical_entity_id = %s", (entity_id,)).fetchone()[0]


def check(name, cond, detail=""):
    checks.append(name)
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))
    if not cond:
        failures.append(name)


def main():
    ur = urllib.request.urlopen(BASE + "/", timeout=5)
    assert ur.status == 200, "server ikke oppe"
    person = display_name(7412)  # unique person name (the resolver's "unik" case)

    with pw.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1440, "height": 1000})

        # --- T4.6: navneresolver — tvetydigt navn → eksplisit valg -----------
        page.goto(BASE + "/find-forbindelse", wait_until="domcontentloaded")
        page.fill("#pf-from", "Betri")          # 9667 + flere → tvetydigt
        page.fill("#pf-to", person)            # unik → resolver direkte
        page.click("button[type=submit]")
        try:
            page.wait_for_selector("#pf-choices:not([hidden])", timeout=6000)
            n = page.locator("#pf-choices .pathfinder-choice").count()
            check("T4.6 tvetydigt navn viser valg (≥2)", n >= 2, f"n={n}")
            check("T4.6 status forklarer valg",
                  "vælg" in page.locator("#pf-resolver-status").inner_text().lower())
            # vælg eksplicit → derefter submit
            page.locator("#pf-choices .pathfinder-choice").first.click()
            page.fill("#pf-to", person)
            page.click("button[type=submit]")
            page.wait_for_url("**/find-forbindelse?from=9667*", timeout=8000)
            check("T4.6 valg → canonical-ID i URL",
                  "from=9667" in page.url and "to=7412" in page.url)
        except pw.TimeoutError as e:
            check("T4.6 tvetydigt navn viser valg", False, f"timeout {e}")

        # --- T4.6: unikt navn resolver + kører sti --------------------------
        page.goto(BASE + "/find-forbindelse", wait_until="domcontentloaded")
        page.fill("#pf-from", person)
        page.fill("#pf-to", "9667")
        page.click("button[type=submit]")
        page.wait_for_url("**/find-forbindelse?from=7412*", timeout=8000)
        check("T4.6 unikt navn resolver + int-path kører",
              "from=7412" in page.url and "to=9667" in page.url)

        # --- T4.6: ukendt navn → tydelig hjælp ------------------------------
        page.goto(BASE + "/find-forbindelse", wait_until="domcontentloaded")
        page.fill("#pf-from", "XYZIKKEFINDES")
        page.fill("#pf-to", "9667")
        page.click("button[type=submit]")
        page.wait_for_timeout(1200)
        status = page.locator("#pf-resolver-status").inner_text()
        check("T4.6 ukendt navn → tydelig hjælp",
              "Ingen entitet matcher" in status, f"status={status!r}")
        check("T4.6 ukendt navn → intet gæt (bliver på siden)",
              page.url.endswith("/find-forbindelse"))

        # --- T4.3: GraphShell LOADED på netværk -----------------------------
        page.goto(BASE + f"/companies/9667/network", wait_until="domcontentloaded")
        try:
            page.wait_for_selector('.graph-shell-state[data-state="LOADED"][hidden]',
                                   timeout=9000, state='attached')
            check("T4.3 netværk: LOADED-state nået (overlay fjernet)", True)
        except pw.TimeoutError:
            state = page.evaluate(
                "() => { const s = document.querySelector('.graph-shell-state');"
                " return s ? s.dataset.state : 'none'; }")
            check("T4.3 netværk: LOADED-state nået (overlay fjernet)",
                  False, f"state={state}")
        check("T4.3 netværk: ingen ERROR-overlay",
              page.locator('.graph-shell-state[data-state="ERROR"]:not([hidden])').count() == 0)

        # --- T4.4: graph-btn ≥44px + aria-label + fokus ----------------------
        page.evaluate("document.getElementById('netvaerk-fallback')"
                      " && document.getElementById('netvaerk-fallback')"
                      ".setAttribute('open','')")
        btns = page.locator(".graph-btn")
        btns = page.locator(".graph-btn")
        cnt = btns.count()
        check("T4.4 graph-controls injiceret", cnt >= 3, f"count={cnt}")
        for i in range(min(cnt, 4)):
            bb = page.locator(".graph-btn").nth(i).bounding_box()
            ok = bb and bb["width"] >= 43 and bb["height"] >= 43
            check(f"T4.4 graph-btn[{i}] ≥44px", ok, f"{bb}")
            aria = page.locator(".graph-btn").nth(i).get_attribute("aria-label")
            check(f"T4.4 graph-btn[{i}] aria-label", bool(aria), f"{aria!r}")
        if cnt:
            page.locator(".graph-btn").nth(0).focus()
            outlined = page.evaluate(
                "() => { const el = document.activeElement;"
                " if (!el.classList.contains('graph-btn')) return false;"
                " const st = getComputedStyle(el); return st.outlineStyle !== 'none'; }")
            check("T4.4 synlig fokus ved keyboard", outlined)

        browser.close()

    print(f"\n=== Fase 4 browser-verifikation: {len(failures)} failed / "
          f"{len(checks)} checks ===")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()