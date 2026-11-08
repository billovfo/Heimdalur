#!/usr/bin/env python3
"""T1.3 VERIFY FIRST — count POSTs to /api/watchlist/toggle per single click.

QA tool (remediation plan v1 T1.3, plan §48 write-path gate): intercepts
/api/watchlist/toggle and fulfils with a mocked 200 so NO database mutation
happens during verification. Acceptance after fix: exactly 1 request per click.

Run: PDF_HISTORY_UI_ENABLED=true ... python3 tools/verify_watchlist_requests.py
"""
import os
import sys

BASE = os.environ.get("AUDIT_BASE", "http://127.0.0.1:8404")

from playwright.sync_api import sync_playwright


def main():
    hits = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = ctx.new_page()
        page.add_init_script("try{localStorage.clear();sessionStorage.clear();}catch(e){}")

        def on_request(req):
            if "/api/watchlist/toggle" in req.url:
                hits.append(req.url)

        page.on("request", on_request)

        # Mock the toggle endpoint: no DB mutation during verification.
        def route_handler(route, request):
            route.fulfill(status=200, content_type="application/json",
                          body='{"ok": true, "mocked": true}')

        page.route("**/api/watchlist/toggle", route_handler)

        page.goto(f"{BASE}/overvaagning", wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        btn = page.query_selector("button[data-unwatch-id]")  # G-05: no inline onclick
        if btn is None:
            print("NO-CLICKABLE-WATCHLIST-BUTTON (nothing watched on this page)")
            return 2
        btn.click()
        page.wait_for_timeout(1500)
        browser.close()

    print(f"POSTs captured for ONE click: {len(hits)}")
    for h in hits:
        print("  ->", h)
    if len(hits) == 1:
        print("RESULT: PASS (exactly one POST per click)")
        return 0
    if len(hits) == 2:
        print("RESULT: BUG CONFIRMED (double handler — two POSTs for one click)")
        return 1
    print(f"RESULT: UNEXPECTED ({len(hits)} requests)")
    return 1


if __name__ == "__main__":
    sys.exit(main())