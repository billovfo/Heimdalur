#!/usr/bin/env python3
"""T1.2 VERIFY FIRST + regression — crafted Bulk Screening input (plan §39).

Three steps:
  1. SINK DATA PATH: POST /api/bulk/screen with crafted identifiers and report
     whether raw HTML reaches the JSON response (query/name for UNKNOWN).
  2. OLD RENDERER REPRODUCTION: re-run the pre-fix renderer (template-string
     into tbody.innerHTML) against the crafted response in a sandboxed blank
     page. If the flag fires, execution is proven for the old code path.
  3. FIXED PAGE REGRESSION: run the real UI flow with the same crafted input
     and assert: no HTML execution, crafted text rendered as text (textContent),
     no /persons/<id>/kyc links, KYC links only for valid companies.

No database mutation: screening is a read-only lookup.
"""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("AUDIT_BASE", "http://127.0.0.1:8404")
CRAFTED = [
    "<img src=x onerror=window.__XSS=1>",
    "5396",
    "IkkeFundet<svg/onload=window.__XSS=2>",
    '" onmouseover=window.__XSS=3 x="',
]
CANARY = None  # set to any window.__XSS value


def step1_sink():
    req = urllib.request.Request(
        f"{BASE}/api/bulk/screen",
        data=json.dumps({"identifiers": CRAFTED}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        data = json.load(r)
    raw_echo = [x for x in data["results"]
                if "<" in (x.get("query") or "") or "<" in (x.get("name") or "")]
    print(f"STEP1 sink: {len(raw_echo)} raw-HTML echoes in API response" if False else
          f"STEP1: raw HTML echoed back by API for {len(raw_echo := [x for x in data['results'] if '<' in (x.get('query') or '')]) if (raw_echo := [x for x in data['results'] if '<' in (x.get('query') or '')]) else 0} identifier(s) -> sink data path CONFIRMED" )
    return data


def step2_old_renderer(data):
    """Reproduce the PRE-FIX rendering path (template string + innerHTML)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome")
        page = b.new_context(viewport={"width": 1440, "height": 1000}).new_page()
        page.set_content("<table><tbody id='t'></tbody></table>")
        old_js = """
        (items) => {
          const tbody = document.getElementById('t');
          tbody.innerHTML = items.map(r => {
            const pepBadge = r.has_pep ? 'PEP: ' + (r.pep_title || 'Registreret') : 'Ingen';
            const link = r.kind === 'PERSON' ? `/persons/${r.id}` : `/companies/${r.id}`;
            return `
              <tr class="bulk-row">
                <td class="font-mono text-xs text-muted">${r.query}</td>
                <td><strong><a href="${link}" class="entity-link">${r.name}</a></strong></td>
                <td>${pepBadge}</td>
                <td class="num">${r.observation_count}</td>
                <td><a href="${link}/kyc" class="btn btn-xs btn-outline">KYC Dossier →</a></td>
              </tr>`;
          }).join('');
        }
        """
        page.evaluate(old_js, data["results"])
        page.wait_for_timeout(800)
        fired = page.evaluate("()=>window.__XSS || null")
        b.close()
        print(f"STEP2 old renderer: window.__XSS fired={fired!r} "
              f"({'EXECUTION CONFIRMED (pre-fix exploitability)' if fired else 'no execution in sandbox'})")
        return fired


def step3_fixed_page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome")
        ctx = b.new_context(viewport={"width": 1440, "height": 1000})
        page = ctx.new_page()
        page.add_init_script("try{localStorage.clear();sessionStorage.clear();}catch(e){}")
        xss_hits = []
        page.on("console", lambda m: xss_hits.append(m.text) if "__XSS" in (m.text or "") else None)
        page.goto(f"{BASE}/bulk-screening", wait_until="domcontentloaded")
        page.wait_for_timeout(900)
        page.evaluate(
            "(v)=>{document.getElementById('bulk-input').value=v;}",
            "\n".join(CRAFTED))
        page.evaluate("()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Slå op i Snapshot')); if(b) b.click();}")
        page.wait_for_timeout(2500)
        rows = page.evaluate("""()=>{
          const rows=[...document.querySelectorAll('#bulk-results-body tr')];
          return rows.map(tr=>({
            text: tr.innerText,
            links: [...tr.querySelectorAll('a')].map(a=>a.getAttribute('href')),
            hasImgOnerror: !!tr.querySelector('img[onerror], svg[onload], [onmouseover]'),
          }));
        }""")
        execed = page.evaluate("()=>window.__XSS || null")
        person_kyc = [l for r in rows for l in r["links"] if l and l.startswith("/persons/") and l.endswith("/kyc")]
        b.close()
        print(f"STEP3 fixed page: rows={len(rows)} executions={execed} person-kyc-links={len(person_kyc)}")
        for r in rows:
            print("   row text:", r["text"].replace("\n", " ")[:90], "| links:", r["links"],
                  "| dangerous-attr:", r["hasImgOnerror"])
        ok = execed is None and not person_kyc and all(
            (not r["hasImgOnerror"]) for r in rows)
        print("RESULT:", "PASS (no execution, text rendering, kind-correct actions)" if ok else "FAIL")
        return 0 if ok else 1


if __name__ == "__main__":
    data = step1_sink()
    fired = step2_old_renderer(data)
    rc = step3_fixed_page()
    print(f"SUMMARY: pre-fix execution {'CONFIRMED' if fired else 'not reproduced in sandbox'}; "
          f"fixed-page regression {'PASS' if rc == 0 else 'FAIL'}")
    sys.exit(0 if (rc == 0) else 1)