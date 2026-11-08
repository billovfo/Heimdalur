"""Heimdal screenshot capture (audit evidence phase §116-§147).
Tooling only — captures the running local server; no product code changes.
"""
import json, os, subprocess, time
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8001"
OUT = "docs/frontend-audit-screenshots"
os.makedirs(OUT, exist_ok=True)
NOW = datetime.now(timezone.utc).isoformat()
FLAGS_ON = {"PDF_HISTORY_UI_ENABLED": "ON", "REGISTRY_ANALYTICS_ENABLED": "1", "REGISTRY_ANALYTICS_GRAPH_ENABLED": "1"}
FLAGS_OFF = {"PDF_HISTORY_UI_ENABLED": "true", "REGISTRY_ANALYTICS_ENABLED": "0", "REGISTRY_ANALYTICS_GRAPH_ENABLED": "0"}
manifest = []

def add(file, route, page_or_tab, capture_type, status, notes="", entity_id=None, entity_type=None, viewport="1440x1000", flags=FLAGS_ON):
    manifest.append({"file": f"docs/frontend-audit-screenshots/{file}", "route": route,
        "entity_id": entity_id, "entity_type": entity_type, "page_or_tab": page_or_tab,
        "viewport": viewport, "feature_flags": dict(flags), "captured_at": NOW,
        "http_status": status, "capture_type": capture_type, "notes": notes})

def settle(page, extra=350):
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.evaluate("() => document.fonts.ready")
    page.wait_for_timeout(extra)

def shot(page, file, route, page_or_tab, full=False, entity_id=None, entity_type=None, notes="", status=200, viewport="1440x1000", flags=FLAGS_ON, capture_type=None):
    page.screenshot(path=f"{OUT}/{file}", full_page=full)
    add(file, route, page_or_tab, capture_type or ("full_page" if full else "viewport"), status, notes, entity_id, entity_type, viewport, flags)
    print("  ", file, flush=True)

def scroll_to(page, selector):
    loc = page.locator(selector).first
    try:
        loc.scroll_into_view_if_needed(timeout=8000)
    except Exception:
        loc.evaluate("el => { el.removeAttribute('hidden'); el.classList.add('is-active'); el.scrollIntoView({block:'start'}); }")
    page.wait_for_timeout(450)
    return loc

def goto(page, route):
    r = page.goto(BASE + route, wait_until="domcontentloaded", timeout=60000)
    settle(page)
    return r.status if r else 0

def sec(page, route, file, page_or_tab, selector, status=None, notes="", entity_id=None, entity_type=None):
    st = goto(page, route) if status is None else status
    scroll_to(page, selector)
    shot(page, file, route, page_or_tab, entity_id=entity_id, entity_type=entity_type,
         notes=notes or f"section close-up ({selector})", status=st, capture_type="detail")
    return st

def show_panel(page, panel_id):
    page.evaluate("id => { const p = document.querySelector(id); if (p) { p.classList.add('is-active'); p.removeAttribute('hidden'); } }", panel_id)
    page.wait_for_timeout(300)

def tab(page, name):
    """Activate a tab panel (button[data-tab])."""
    btn = page.locator(f'button[role="tab"][data-tab="{name}"]')
    if btn.count():
        btn.click()
        page.wait_for_timeout(300)

def tabsec(page, route, tabname, file, page_or_tab, selector, status=None, notes="", entity_id=None, entity_type=None):
    st = goto(page, route) if status is None else status
    tab(page, tabname)
    loc = page.locator(selector).first
    loc.scroll_into_view_if_needed(timeout=15000)
    page.wait_for_timeout(450)
    shot(page, file, route, page_or_tab, entity_id=entity_id, entity_type=entity_type,
         notes=notes or f"tab '{tabname}' aktiv; section close-up ({selector})", status=st, capture_type="detail")
    return st

def fullpage_all_tabs(page, file, route, page_or_tab, status, entity_id=None, entity_type=None):
    """Full-page capture with ALL tab panels opened (browser-side test state; marked)."""
    page.evaluate("document.querySelectorAll('.tab-panel').forEach(p => p.classList.add('is-active'))")
    page.wait_for_timeout(400)
    shot(page, file, route, page_or_tab, full=True, entity_id=entity_id, entity_type=entity_type,
         status=status, capture_type="full_page",
         notes="FULL-PAGE: alle tab-panels åbnet via browser-side test state (is-active på alle .tab-panel) — viser hele informationsarkitekturen; ikke standard-UI-tilstand")

with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="chrome", args=["--no-sandbox", "--disable-dev-shm-usage", "--hide-scrollbars"])
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = ctx.new_page()
    page.set_default_timeout(30000)

    # ---------- major pages ----------
    st = goto(page, "/")
    shot(page, "001-dashboard.png", "/", "Dashboard", full=True, status=st)
    page.evaluate("window.scrollTo(0,0)"); page.wait_for_timeout(250)
    shot(page, "500-navigation-desktop.png", "/", "Global navigation (spine + topbar)", status=st, notes="navigation synlig i viewport-top")
    st = goto(page, "/companies"); shot(page, "010-companies-list.png", "/companies", "Virksomhedsliste", full=True, status=st)
    st = goto(page, "/persons"); shot(page, "011-persons-list.png", "/persons", "Personliste", full=True, status=st)
    st = goto(page, "/groups"); shot(page, "012-groups.png", "/groups", "Koncerner", full=True, status=st)
    st = goto(page, "/groups/9667"); shot(page, "013-group-detail-9667.png", "/groups/9667", "Koncerndetail", full=True, status=st)
    st = goto(page, "/bestand"); shot(page, "014-bestand.png", "/bestand", "Bestand", full=True, status=st)
    st = goto(page, "/persons/relationer"); shot(page, "015-person-relationer.png", "/persons/relationer", "Relationer mellem personer", full=True, status=st)
    st = goto(page, "/geo-kort")
    try:
        page.wait_for_selector(".leaflet-container", timeout=8000)
        page.wait_for_timeout(1200)
    except Exception:
        pass
    shot(page, "016-geo-kort.png", "/geo-kort", "Geo-Kort", status=st, notes="Leaflet vendet lokalt; tile/layer-tilstand captured som rendret")
    st = goto(page, "/compliance"); shot(page, "017-compliance.png", "/compliance", "Registerobservationer", full=True, status=st)
    st = goto(page, "/bulk-screening"); shot(page, "018-bulk-screening.png", "/bulk-screening", "Bulk Screening", full=True, status=st)
    st = goto(page, "/overvaagning"); shot(page, "019-overvaagning.png", "/overvaagning", "Overvågning", full=True, status=st)
    st = goto(page, "/aendringer"); shot(page, "020-aendringer.png", "/aendringer", "Ændringslog", full=True, status=st)
    st = goto(page, "/find-forbindelse"); shot(page, "021-find-forbindelse.png", "/find-forbindelse", "Find forbindelse (pathfinder)", status=st)
    st = goto(page, "/kildeinformation/9667"); shot(page, "022-kildeinformation-9667.png", "/kildeinformation/9667", "Kildeinformation", full=True, status=st, entity_id=9667, entity_type="COMPANY")

    # ---------- reference company 9667 ----------
    R = "/companies/9667"
    st = goto(page, R)
    shot(page, "100-company-9667-overview.png", R, "Company header + tabstrip (top, ejerskab-tab aktiv)", status=st, entity_id=9667, entity_type="COMPANY")
    sec(page, R, "101-company-9667-registrerede-oplysninger.png", "Registrerede oplysninger (§16.0)", "#registrerede-oplysninger", status=st, entity_id=9667, entity_type="COMPANY")
    sec(page, R, "102-company-9667-ejerskab.png", "Ejerskab — Hvem ejer virksomheden? (ownership observation)", "#ejere", status=st, entity_id=9667, entity_type="COMPANY")
    sec(page, R, "103-company-9667-besiddelser.png", "Besiddelser — Hvad ejer virksomheden?", "#besiddelser", status=st, entity_id=9667, entity_type="COMPANY")
    tabsec(page, R, "personer", "104-company-9667-personer.png", "Personer — aktuel bestyrelse/ledelse", "#roller", status=st, entity_id=9667, entity_type="COMPANY")
    tabsec(page, R, "personer", "105-company-9667-analyse-collapsed.png", "Konstellationer / Analyse (collapsed)", "#analytics-company", status=st, entity_id=9667, entity_type="COMPANY")
    tab(page, "personer")
    d = page.locator("#analytics-company details", has_text="Vis flere").first
    if d.count() == 0:
        d = page.locator("#analytics-company details").first
    d.evaluate("el => { el.open = true; }")
    d.scroll_into_view_if_needed(); page.wait_for_timeout(400)
    shot(page, "106-company-9667-analyse-vis-flere-expanded.png", R, "Konstellationer — 'Vis flere konstellationer' expanded", entity_id=9667, entity_type="COMPANY", capture_type="expanded_state", status=st)
    d2 = page.locator("#analytics-company details", has_text="Dækning og metode").first
    d2.evaluate("el => { el.open = true; }")
    d2.scroll_into_view_if_needed(); page.wait_for_timeout(400)
    shot(page, "120-company-9667-dakning-og-metode-expanded.png", R, "Dækning og metode expanded", entity_id=9667, entity_type="COMPANY", capture_type="expanded_state", status=st)
    tabsec(page, R, "relationer", "107-company-9667-relationer.png", "Relationer", "#virksomhedsrelationer", status=st, entity_id=9667, entity_type="COMPANY")
    tabsec(page, R, "kontrol", "108-company-9667-kontrol.png", "Kontrolregistrering", "#kontrol", status=st, entity_id=9667, entity_type="COMPANY")
    goto(page, R)
    tab(page, "netvaerk")
    try:
        page.wait_for_function("() => window.heimdalGraph && window.heimdalGraph.edges && window.heimdalGraph.edges.length > 0", timeout=15000)
        note = "embedded canvas graph hydrated (window.heimdalGraph.edges > 0)"
    except Exception:
        note = "embedded graph container captured; canvas hydration state uncertain (documented)"
    page.wait_for_timeout(600)
    shot(page, "109-company-9667-network-embedded.png", R, "Embedded interaktiv netværksgraf (§21, Netværk-tab)", entity_id=9667, entity_type="COMPANY", notes=note, status=st)
    st = goto(page, R)
    show_panel(page, "#panel-historik")
    scroll_to(page, "#aendringer")
    shot(page, "111-company-9667-historik.png", R, "Ændringslog & Tidslinje (company-main)", entity_id=9667, entity_type="COMPANY", status=st, capture_type="detail", notes="panel-historik aktiveret via browser-side test state (tab er en <a>-route, ikke data-tab-knap)")
    stn = goto(page, "/companies/9667/network")
    page.wait_for_selector(".network-svg", timeout=15000)
    page.wait_for_timeout(500)
    shot(page, "110-company-9667-network-page.png", "/companies/9667/network", "Separat network-side (SVG-graf)", full=True, entity_id=9667, entity_type="COMPANY", status=stn)
    sec(page, "/companies/9667/network", "311-company-9667-network-constellation-layer.png", "Network constellation layer (analytics overlay)", "#konstellationer", status=stn, entity_id=9667, entity_type="COMPANY")
    st = goto(page, "/companies/9667/history"); shot(page, "112-company-9667-history-page.png", "/companies/9667/history", "Separat historik-side", full=True, entity_id=9667, entity_type="COMPANY", status=st)
    sts = goto(page, "/companies/9667/skraseting")
    shot(page, "113-company-9667-skraseting-current.png", "/companies/9667/skraseting", "Skráseting — Registreret status (current)", entity_id=9667, entity_type="COMPANY", status=sts)
    sec(page, "/companies/9667/skraseting", "114-company-9667-skraseting-historik.png", "Skráseting — Historik (collapsed Vis kilde)", "#skr-historik", status=sts, entity_id=9667, entity_type="COMPANY")
    d3 = page.locator("details", has_text="Vis kilde").first
    d3.evaluate("el => { el.open = true; }")
    d3.scroll_into_view_if_needed(); page.wait_for_timeout(400)
    shot(page, "115-company-9667-skraseting-vis-kilde-expanded.png", "/companies/9667/skraseting", "Skráseting — Vis kilde disclosure expanded", entity_id=9667, entity_type="COMPANY", capture_type="expanded_state", status=sts)
    sec(page, "/companies/9667/skraseting", "302-unresolved-history-person-9667.png", "Skráseting — historik rækker med unresolved person (edge case)", "#skr-historik", status=sts, entity_id=9667, entity_type="COMPANY", notes="linked (<a>) + unresolved (tekst) rækker i samme visning")
    st = goto(page, "/companies/9667/eigarayvirlit"); shot(page, "116-company-9667-eigarayvirlit.png", "/companies/9667/eigarayvirlit", "Eigarayvirlit", full=True, entity_id=9667, entity_type="COMPANY", status=st)
    st = goto(page, "/companies/9667/dossier"); shot(page, "117-company-9667-dossier.png", "/companies/9667/dossier", "Dossier", full=True, entity_id=9667, entity_type="COMPANY", status=st)
    st = goto(page, "/companies/9667/kyc")
    if st == 200:
        shot(page, "118-company-9667-kyc.png", "/companies/9667/kyc", "KYC-dossier", full=True, entity_id=9667, entity_type="COMPANY", status=st)
    else:
        add("118-company-9667-kyc.png", "/companies/9667/kyc", "KYC-dossier", "detail", st, notes="NOT CAPTURED: route returned %s" % st)
    st = goto(page, R); page.wait_for_timeout(600)
    fullpage_all_tabs(page, "119-company-9667-full-page.png", R, "Company 9667 — FULL PAGE", status=st, entity_id=9667, entity_type="COMPANY")
    goto(page, R)
    ts = page.locator("nav.tablist").first
    ts.scroll_into_view_if_needed(); page.wait_for_timeout(250)
    shot(page, "501-company-tabs-strip.png", R, "Company tab-navigation strip", entity_id=9667, entity_type="COMPANY", status=st)
    stt = goto(page, "/companies/9063")
    tab(page, "personer")
    twin = page.locator(".constellation-card", has_text="Tilbagevendende").filter(has_text="Kernegruppe").first
    if twin.count() == 0:
        twin = page.locator("#analytics-company .card", has_text="Tilbagevendende").filter(has_text="Kernegruppe").first
    if twin.count():
        twin.scroll_into_view_if_needed(); page.wait_for_timeout(350)
        shot(page, "303-twin-core-recurring-9063.png", "/companies/9063", "Twin-badge kort (CORE + RECURRING, samme member-set)", entity_id=9063, entity_type="COMPANY", capture_type="detail", status=stt)
    else:
        add("303-twin-core-recurring-9063.png", "/companies/9063", "Twin-badge kort", "detail", stt, notes="NOT CAPTURED: twin card selector ikke fundet")
    goto(page, R)
    tab(page, "personer")
    card = page.locator("#analytics-company .constellation-card").filter(has_text="Observeret").filter(has_not_text="funktionstid").first
    try:
        card.scroll_into_view_if_needed(); page.wait_for_timeout(350)
        shot(page, "304-point-observation-same-snapshot-9667.png", R, "SAME_SNAPSHOT point observation ('Observeret DD.MM.YYYY')", entity_id=9667, entity_type="COMPANY", capture_type="detail", notes="live data", status=200)
    except Exception:
        add("304-point-observation.png", R, "point observation", "detail", 200, notes="NOT CAPTURED: selector ikke fundet")
    sth = goto(page, "/companies/9287")
    tabsec(page, "/companies/9287", "personer", "300-company-9287-high-group-count.png", "Høj group count (Viser 60 af 120)", "#analytics-company", status=sth, entity_id=9287, entity_type="COMPANY")
    st7 = goto(page, "/companies/7")
    tabsec(page, "/companies/7", "personer", "301-company-7-no-analytics.png", "Company uden analytics-data (empty/degraded state)", "#analytics-company", status=st7, entity_id=7, entity_type="COMPANY")
    ste = goto(page, "/companies?q=zzzzzz")
    shot(page, "307-empty-state-search.png", "/companies?q=zzzzzz", "Empty state (søgning uden resultater)", status=ste)

    # ---------- reference person 7412 ----------
    P = "/persons/7412"
    st = goto(page, P)
    shot(page, "200-person-7412-overview.png", P, "Person overview (top, ejerskab-tab aktiv)", status=st, entity_id=7412, entity_type="PERSON")
    sec(page, P, "216-person-7412-ejerskab.png", "Direkte + indirekte ejerskab", "#besiddelser", status=st, entity_id=7412, entity_type="PERSON")
    tabsec(page, P, "roller", "201-person-7412-roller.png", "Roller i virksomheder (aktuelle)", "#roller", status=st, entity_id=7412, entity_type="PERSON")
    tabsec(page, P, "relationer", "202-person-7412-relationer.png", "Relation til andre personer", "#personrelationer", status=st, entity_id=7412, entity_type="PERSON")
    tabsec(page, P, "analyse", "203-person-7412-analyse.png", "Analyse-sektion (overblik)", "#analytics", status=st, entity_id=7412, entity_type="PERSON")
    tab(page, "analyse")
    sec(page, P, "204-person-7412-analyse-samarbejdspartnere.png", "Top samarbejdspartnere (tabel close-up)", "#analytics table", status=st, entity_id=7412, entity_type="PERSON", notes="analyse-tab aktiv; Top samarbejdspartner tabel med Bevisklasser/Første/Senest observeret")
    sec(page, P, "205-person-7412-analyse-konstellationer.png", "Tilbagevendende konstellationer (kort close-up)", "#analytics", status=st, entity_id=7412, entity_type="PERSON", notes="analyse-tab aktiv")
    sec(page, P, "206-person-7412-perioder.png", "Observeret funktionstid (perioder)", "#analytics", status=st, entity_id=7412, entity_type="PERSON", notes="analyse-tab aktiv; perioder-tabel")
    d4 = page.locator("#analytics details", has_text="Dækning").first
    if d4.count():
        d4.evaluate("el => { el.open = true; }")
        d4.scroll_into_view_if_needed(); page.wait_for_timeout(350)
        shot(page, "213-person-7412-dakning-expanded.png", P, "Coverage disclosure expanded", entity_id=7412, entity_type="PERSON", capture_type="expanded_state", status=st)
    kg = page.locator("#analytics .constellation-card", has_text="Kernegruppe").first
    if kg.count():
        kg.scroll_into_view_if_needed(); page.wait_for_timeout(350)
        shot(page, "214-person-7412-kernegruppe.png", P, "Kernegruppe-konstellation (detail)", entity_id=7412, entity_type="PERSON", capture_type="detail", status=st)
    goto(page, P)
    show_panel(page, "#panel-historik")
    scroll_to(page, "#aendringer")
    shot(page, "207-person-7412-historik.png", P, "Ændringslog & Tidslinje (Historik-tab)", entity_id=7412, entity_type="PERSON", status=st, capture_type="detail", notes="panel-historik aktiveret via browser-side test state")
    sec(page, P, "208-person-7412-skraseting.png", "Skráseting-sektion (person; uden for tabpanels)", "#skraseting-person", status=st, entity_id=7412, entity_type="PERSON")
    goto(page, P)
    tsp = page.locator("nav.tablist").first
    tsp.scroll_into_view_if_needed(); page.wait_for_timeout(250)
    shot(page, "502-person-tabs-strip.png", P, "Person tab-navigation strip", entity_id=7412, entity_type="PERSON", status=st)
    stn = goto(page, "/persons/7412/network")
    page.wait_for_selector(".network-svg", timeout=15000)
    page.wait_for_timeout(500)
    shot(page, "209-person-7412-network-page.png", "/persons/7412/network", "Person network-side (SVG + konstellationer)", full=True, entity_id=7412, entity_type="PERSON", status=stn)
    st = goto(page, "/persons/7412/history"); shot(page, "210-person-7412-history-page.png", "/persons/7412/history", "Person historik-side", full=True, entity_id=7412, entity_type="PERSON", status=st)
    st = goto(page, "/persons/7412/dossier")
    if st == 200:
        shot(page, "211-person-7412-dossier.png", "/persons/7412/dossier", "Person dossier", full=True, entity_id=7412, entity_type="PERSON", status=st)
    else:
        add("211-person-7412-dossier.png", "/persons/7412/dossier", "Person dossier", "detail", st, notes=f"NOT CAPTURED: status {st}")
    st = goto(page, P); page.wait_for_timeout(600)
    fullpage_all_tabs(page, "212-person-7412-full-page.png", P, "Person 7412 FULL PAGE", status=st, entity_id=7412, entity_type="PERSON")
    stb = goto(page, "/persons/2321")
    tabsec(page, "/persons/2321", "analyse", "305-bounded-interval-2321.png", "SAFE_PERIOD_OVERLAP bounded interval ('Observeret funktionstid 09.03.2012 – 14.04.2025')", "#analytics", status=stb, entity_id=2321, entity_type="PERSON")

    def spawn(port, env_extra, code):
        env = dict(os.environ)
        env.update({"PDF_HISTORY_UI_ENABLED": "true", "HEIMDAL_READ_DB": "postgresql://postgres@localhost:5432/heimdal"})
        env.update(env_extra)
        proc = subprocess.Popen(["python3", "-c", code], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import urllib.request
        for _ in range(40):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/companies/9667", timeout=2); return proc
            except Exception:
                time.sleep(0.5)
        return proc

    err_code = (
        "import heimdal.modules.analytics.views as v\n"
        "def boom(*a, **k):\n"
        "    raise RuntimeError('patched: simuleret reader-fejl (NOT-LIVE)')\n"
        "v.get_company_groups = boom\n"
        "try:\n"
        "    if hasattr(v, 'get_person_groups'): v.get_person_groups = boom\n"
        "except Exception: pass\n"
        "from heimdal.app import app\n"
        "app.run(host='127.0.0.1', port=8002, debug=False, use_reloader=False)"
    )
    p_err = spawn(8002, {"REGISTRY_ANALYTICS_ENABLED": "1", "REGISTRY_ANALYTICS_GRAPH_ENABLED": "1"}, err_code)
    off_code = "from heimdal.app import app\napp.run(host='127.0.0.1', port=8003, debug=False, use_reloader=False)"
    p_off = spawn(8003, {}, off_code)
    try:
        ectx = browser.new_context(viewport={"width": 1440, "height": 1000})
        err_page = ectx.new_page()
        r = err_page.goto("http://127.0.0.1:8002/companies/9667", wait_until="domcontentloaded", timeout=60000)
        settle(err_page)
        try:
            err_page.locator("#analytics-company").first.evaluate("el => { el.scrollIntoView({block:'start'}); }")
        except Exception:
            pass
        err_page.wait_for_timeout(400)
        shot(err_page, "308-analytics-error-state-9667-NOT-LIVE.png", "http://127.0.0.1:8002/companies/9667", "Analytics-reader degraded/error state (monkeypatched reader)", status=r.status if r else 0,
             notes="NOT-LIVE: get_company_groups monkeypatched to raise i separat server-instans (port 8002). Ingen dataændring; dokumenterer fail-closed/error-tilstanden som faktisk renderer.")
        off_page = ectx.new_page()
        r2 = off_page.goto("http://127.0.0.1:8003/persons/7412", wait_until="domcontentloaded", timeout=60000)
        settle(off_page)
        shot(off_page, "309-feature-flag-off-person-7412-NOT-LIVE.png", "http://127.0.0.1:8003/persons/7412", "Feature-flag OFF state (analytics sektioner væk)", status=r2.status if r2 else 0, flags=FLAGS_OFF,
             notes="NOT-LIVE: separat server-instans med REGISTRY_ANALYTICS_ENABLED/GRAPH_ENABLED unset. Sektionen er fraværende.")
    finally:
        p_err.terminate(); p_off.terminate()

    mctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1, is_mobile=True)
    mp = mctx.new_page()
    st = goto(mp, "/")
    shot(mp, "400-mobile-dashboard.png", "/", "Mobile — Dashboard", viewport="390x844", status=st)
    st = goto(mp, "/companies/9667")
    shot(mp, "401-mobile-company-9667-overview.png", "/companies/9667", "Mobile — Company overview", entity_id=9667, entity_type="COMPANY", viewport="390x844", status=st)
    tabsec(mp, "/companies/9667", "personer", "402-mobile-company-9667-personer.png", "Mobile — Company Personer", "#roller", status=st, entity_id=9667, entity_type="COMPANY")
    tabsec(mp, "/companies/9667", "personer", "403-mobile-company-9667-analyse.png", "Mobile — Company Analyse/konstellationer", "#analytics-company", status=st, entity_id=9667, entity_type="COMPANY")
    goto(mp, "/companies/9667")
    show_panel(mp, "#panel-historik")
    scroll_to(mp, "#aendringer")
    shot(mp, "404-mobile-company-9667-historik.png", "/companies/9667", "Mobile — Company Historik", entity_id=9667, entity_type="COMPANY", viewport="390x844", status=st)
    st = goto(mp, "/persons/7412")
    shot(mp, "405-mobile-person-7412-overview.png", "/persons/7412", "Mobile — Person overview", entity_id=7412, entity_type="PERSON", viewport="390x844", status=st)
    tabsec(mp, "/persons/7412", "analyse", "406-mobile-person-7412-analyse.png", "Mobile — Person Analyse", "#analytics", status=st, entity_id=7412, entity_type="PERSON")
    st = goto(mp, "/persons/7412/network")
    try: mp.wait_for_selector(".network-svg", timeout=15000)
    except Exception: pass
    shot(mp, "407-mobile-person-7412-network.png", "/persons/7412/network", "Mobile — Person Network", entity_id=7412, entity_type="PERSON", viewport="390x844", status=st)

    browser.close()

with open(f"{OUT}/manifest.json", "w") as f:
    json.dump(manifest, f, indent=1, ensure_ascii=False)
print("TOTAL:", len(manifest), "manifest entries")
