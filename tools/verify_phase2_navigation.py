#!/usr/bin/env python3
"""Phase 2 (T2.1–T2.3) browser verification — remediation plan v1.

Run:  python3 tools/verify_phase2_navigation.py

T2.1 Pathfinder discoverability: nav entry + palette shortcut + click → 200.
T2.2 Hero-search contract: person name → person results; reg.nr/ID → company.
T2.3 Tab matrix: click/arrow/Home/End, reload (default Overblik), deep link,
     entity A → B isolation, back/forward, hash; keyboard never lands on the
     History route-link; no heimdal_tab_v2 written.
"""
import os
import sys
import urllib.request

BASE = os.environ.get("AUDIT_BASE", "http://127.0.0.1:8404")
A_ID, A_NAME = 9667, "Betri"
B_ID = 7412  # a person: the name is read from the DB, never hard-coded (I-5)


def display_name(entity_id):
    import psycopg
    with psycopg.connect(os.environ["HEIMDAL_READ_DB"]) as c:
        return c.execute("SELECT display_name FROM canonical_entity"
                         " WHERE canonical_entity_id = %s", (entity_id,)).fetchone()[0]


from playwright.sync_api import sync_playwright

FAILURES = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def t21_nav_and_palette(page):
    page.goto(f"{BASE}/", wait_until="domcontentloaded")
    page.wait_for_timeout(700)
    nav_link = page.query_selector('nav.spine a[href="/find-forbindelse"]')
    check("T2.1 nav-entry for Find forbindelse", nav_link is not None)
    if nav_link:
        nav_link.click()
        page.wait_for_timeout(900)
        check("T2.1 /find-forbindelse klik → 200 (side vises)",
              page.url.startswith(BASE + "/find-forbindelse"))
    # palette shortcut
    page.goto(f"{BASE}/", wait_until="domcontentloaded")
    page.wait_for_timeout(700)
    page.keyboard.press("Control+k")
    page.wait_for_timeout(500)
    item = page.query_selector('#cmd-palette-results a[href="/find-forbindelse"]')
    check("T2.1 palette-genvej Find forbindelse", item is not None)


def t22_hero_search(page):
    page.goto(f"{BASE}/", wait_until="domcontentloaded")
    page.wait_for_timeout(700)
    hero = page.query_selector('form[role="search"][aria-label="Søg i registeret"]')
    check("T2.2 hero-search form til stede", hero is not None)
    if not hero:
        return
    inp = hero.query_selector('input[name="q"]')
    b_name = display_name(B_ID)
    inp.fill(b_name)
    inp.press("Enter")
    page.wait_for_timeout(2200)
    modal_open = page.evaluate(
        "()=>document.getElementById('cmd-palette-modal').classList.contains('open')")
    body = page.evaluate(
        "()=>document.getElementById('cmd-palette-results').innerText")
    check("T2.2 hero-søgning på personnavn → palette åben med person-resultat",
          modal_open and b_name in body and ("Person" in body),
          body[:120].replace("\n", " | "))
    # reg.nr → company
    inp.fill("3690")
    page.wait_for_timeout(400)
    inp.press("Enter")
    page.wait_for_timeout(2200)
    body = page.evaluate(
        "()=>document.getElementById('cmd-palette-results').innerText")
    check("T2.2 hero-søgning på reg.nr. → virksomhed",
          ("3690" in body or "Betri" in body) and "Virksomhed" in body,
          body[:120].replace("\n", " "))


def active_tab(page):
    return page.evaluate(
        "()=>{const t=document.querySelector('.tablist button[aria-selected=\"true\"]');"
        "return t?t.getAttribute('data-tab'):null;}")


def panel_visible(page, name):
    return page.evaluate(
        "(n)=>{const p=document.querySelector('.tab-panel[data-panel=\"'+n+'\"]');"
        "return p? !p.hidden : null;}", name)


def t23_tab_matrix(page):
    route = f"{BASE}/companies/{A_ID}"
    page.goto(route, wait_until="domcontentloaded")
    page.wait_for_timeout(800)

    # click matrix
    for name in ["personer", "ejerskab", "relationer", "netvaerk", "overblik"]:
        page.click(f'.tablist button[data-tab="{name}"]')
        page.wait_for_timeout(300)
        check(f"T2.3 klik {name} → aktiv + panel synlig",
              active_tab(page) == name and panel_visible(page, name) is True)

    # roving keyboard: focus first tab, ArrowRight moves within buttons only
    page.focus('.tablist button[data-tab="overblik"]')
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(250)
    focused = page.evaluate("()=>document.activeElement.getAttribute('data-tab')")
    check("T2.3 ArrowRight fra Overblik → Personer (button, ikke rute-link)",
          focused == "personer", f"focus={focused}")
    page.keyboard.press("End")
    page.wait_for_timeout(250)
    focused = page.evaluate("()=>document.activeElement.getAttribute('data-tab')")
    check("T2.3 End → sidste BUTTON-tab (kilder), aldrig Historik-link",
          focused == "kilder", f"focus={focused}")

    # history route link is a LINK, not a tab
    link = page.query_selector('.tablist a.tab-route-link[data-route-tab="historik"]')
    check("T2.3 Historik er rute-link (class tab-route-link, uden role=tab)",
          link is not None and
          link.evaluate("el=>el.getAttribute('role')") is None)
    if link:
        link.click()
        page.wait_for_timeout(1000)
        check("T2.3 Historik-klik → /history route",
              page.url.endswith(f"/companies/{A_ID}/history"), page.url)

    # reload → default Overblik (heimdal_tab_v2 removed). NOTE: clicking a tab
    # pushes a hash (#ejere) — hash wins on reload by design, so the reload
    # check runs from a hash-less URL.
    page.goto(f"{BASE}/companies/{A_ID}", wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    page.click('.tablist button[data-tab="ejerskab"]')
    page.wait_for_timeout(400)
    check("T2.3 klik Ejerskab → hash #ejere sat",
          "#ejere" in page.url, page.url)
    page.goto(f"{BASE}/companies/{A_ID}", wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    check("T2.3 hash-løs URL → default Overblik", active_tab(page) == "overblik")
    stored = page.evaluate("()=>localStorage.getItem('heimdal_tab_v2')")
    check("T2.3 localStorage heimdal_tab_v2 SKRIVES IKKE", stored is None, repr(stored))

    # deep link hash wins
    page.goto(f"{BASE}/companies/{A_ID}#ejere", wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    check("T2.3 deep-link #ejere → Ejerskab", active_tab(page) == "ejerskab")

    # entity A → entity B isolation: B defaults to Overblik
    page.goto(f"{BASE}/companies/{A_ID}", wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    page.click('.tablist button[data-tab="relationer"]')
    page.wait_for_timeout(400)
    page.goto(f"{BASE}/persons/{B_ID}", wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    check("T2.3 entity A's tabvalg påvirker ikke B (B → Overblik)",
          active_tab(page) == "overblik")

    # back/forward: hash navigation activates matching tab
    page.goto(f"{BASE}/persons/{B_ID}#ejerskab-hashless", wait_until="domcontentloaded")
    page.goto(f"{BASE}/persons/{B_ID}#besiddelser", wait_until="domcontentloaded")
    page.wait_for_timeout(700)
    check("T2.3 hash #besiddelser → Ejerskab (hashchange-mapping)",
          active_tab(page) == "ejerskab")
    page.go_back()
    page.wait_for_timeout(700)
    # hashless custom hash does not map to a tab → tab state may stay; assert no crash
    check("T2.3 browser-back uden crash (side stadig interaktiv)",
          active_tab(page) is not None)


def main():
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome")
        ctx = b.new_context(viewport={"width": 1440, "height": 1000})
        page = ctx.new_page()
        page.add_init_script("try{localStorage.clear();sessionStorage.clear();}catch(e){}")
        t21_nav_and_palette(page)
        t22_hero_search(page)
        t23_tab_matrix(page)
        b.close()
    print()
    if FAILURES:
        print(f"PHASE 2 BROWSER VERIFICATION: {len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("PHASE 2 BROWSER VERIFICATION: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())