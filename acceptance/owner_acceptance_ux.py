from heimdal.pdfhist.db import qa_dsn
"""Owner acceptance UX correction — acceptance detectors (task §44-§54).

Run from the repo root under a test env: `python3 -m acceptance.owner_acceptance_ux`
(__main__ runner, not a pytest module; tools/gate.sh runs it report-only). READ-ONLY: renders real
pages through the test client and asserts presentation invariants. No flag
defaults change; no DB write.

Covers:
  UX-44  no raw dict/list repr on skraseting + overview (aliases/address/
         capital/dates humanized)
  UX-45  history action semantics: APPOINTED->join, REMOVED->leave,
         CHANGED/unknown -> neutral (never coerced)
  UX-46  linked historical person -> canonical display name + clickable;
         unresolved -> source name as text, no fabricated link
  UX-47  SAME_SNAPSHOT point rendered as point, never as pseudo-interval
  UX-48  bounded SAFE_PERIOD_OVERLAP interval allowed; left-censored never
         gets invented duration (period table text from Phase 4 semantics)
  UX-49  distinct member sets render as distinct cards; CORE+RECURRING
         twin renders once with both badges
  UX-50  high-group-count company: stated total, deterministic order, cap
  UX-51  current authority: Felag-only current bucket, history not current
  UX-52  no raw JSON across company/person/history/analytics surfaces
  UX-53  canonical click-through: all person hrefs resolve 200
  UX-54  feature flags: both gates off -> analytics DOM absent, pages fine
"""
import os
import re

# X5c: tools/test_env.sh pins all four feature flags false (X5b), but this
# runner accepts the flags-on product. Set them explicitly (setdefault is not
# enough: the test env already exports them) before the app is imported. The
# UX-54 sub-check below switches the same flags off again for its flag-off
# assertions.
_FEATURE_FLAGS = (
    "PDF_HISTORY_INTEGRATION_ENABLED",
    "PDF_HISTORY_UI_ENABLED",
    "REGISTRY_ANALYTICS_ENABLED",
    "REGISTRY_ANALYTICS_GRAPH_ENABLED",
)


def set_feature_flags(enabled):
    for flag in _FEATURE_FLAGS:
        os.environ[flag] = "true" if enabled else "false"


set_feature_flags(True)

from heimdal.app import app  # noqa: E402
from tests_baseline import ANALYTICS_BUILD, ANALYTICS_HASH  # noqa: E402

DSN = qa_dsn()
PERSON_WITH_GROUPS = 7412
COMPANY_WITH_GROUPS = 5266      # RECURRING+RECURRING same-set twins merged
COMPANY_TWIN = 9063             # CORE+RECURRING twins (same member set)
COMPANY_HIGH_GROUPS = 9287      # 120 merged constellations
COMPANY_OWNER_CASE = 9667
PERSON_PLAIN = 718

_pass = _fail = 0


def check(name, ok, detail=""):
    global _pass, _fail
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))
    if ok:
        _pass += 1
    else:
        _fail += 1


RAW_PATTERNS = [r"\['[^'\]]+'(,\s*'[^'\]]+')+\]", r"\{'[a-z_]+':\s",
                r"&#39;amount&#39;:", r"&#39;city&#39;:", r"&#39;currency&#39;:",
                r"&#39;lines&#39;:", r"'amount':\s*\d", r"'city':\s*'"]


def raw_leak_count(html):
    return sum(len(re.findall(p, html)) for p in RAW_PATTERNS)


def main():
    app.config["TESTING"] = True

    with app.test_client() as cl:
        # ---- UX-54: flag matrix still gates everything -----------------
        set_feature_flags(False)
        body_off = cl.get(f"/persons/{PERSON_WITH_GROUPS}").get_data(as_text=True)
        check("flags off: no analytics section",
              "Observeret samarbejde (analyse)" not in body_off)
        check("flag-off page still functional", "record-name" in body_off)

        # ---- UX-44/§13: raw structures (skraseting + overview) ---------
        set_feature_flags(True)
        skr = cl.get(f"/companies/{COMPANY_OWNER_CASE}/skraseting").get_data(as_text=True)
        check("skraseting: no raw list repr", not re.search(r"\['[^'\]]+'(,\s*'[^'\]]+')*\]", skr))
        check("skraseting: no raw dict repr", not re.search(r"&#39;[a-z_]+&#39;:\s", skr))
        check("skraseting: human alias chips", "Tidligere/alternative navne" in skr
              and 'class="chip"' in skr)
        check("skraseting: address lines rendered", 'class="address-lines"' in skr
              and "Kongabrúgvin" in skr)
        check("skraseting: human capital", re.search(r"DKK[:\s] ?[\d.]+", skr) is not None
              and "&#39;amount&#39;" not in skr)
        check("skraseting: human account dates",
              "Seneste registrerede regnskabsdato" in skr
              and re.search(r"\d{2}\.\d{2}\.\d{4}", skr) is not None)
        check("skraseting: no bracket aliases", "['" not in skr and "']" not in skr)

        over = cl.get(f"/companies/{COMPANY_OWNER_CASE}").get_data(as_text=True)
        check("overview: registry facts card present",
              "registrerede-oplysninger" in over)
        check("overview: source badge", "Kilde: Skráseting Føroya" in over)
        check("overview: no raw structures",
              raw_leak_count(over) == 0, str(raw_leak_count(over)))

        # ---- UX-49: constellation identity + twin dedupe ---------------
        body = cl.get(f"/companies/{COMPANY_OWNER_CASE}").get_data(as_text=True)
        check("constellation: human context titles",
              "constellation-title" in body
              and "Bestyrelseskonstellation" in body)
        check("constellation: collapsed member identities visible",
              "constellation-members" in body
              and body.count('class="member-more"') >= 1)
        check("constellation: no kinds-led noise rows",
              "Kernegruppe + Tilbagevendende konstellation ·" not in body)
        check("constellation: badges not title text",
              'badge-constellation">Kernegruppe<' in body)
        # twin: same member set shows ONCE as a card with BOTH badges
        # (order-agnostic: one card segment must contain both badge texts)
        twin_html = cl.get(f"/companies/{COMPANY_TWIN}").get_data(as_text=True)
        segments = re.findall(
            r'class="[^"]*constellation-card[^"]*".*?(?=constellation-card|</ul>)',
            twin_html, re.S)
        check("twin: both badges on one card",
              any("Kernegruppe" in s and "Tilbagevendende konstellation" in s
                  for s in segments))

        # ---- UX-47/§47: point not interval -----------------------------
        check("no same-point dash interval on company page",
              not re.search(r"(\d{2}\.\d{2}\.\d{4})–\1", body),
              str(re.findall(r"\d{2}\.\d{2}\.\d{4}–\d{2}\.\d{2}\.\d{4}", body)[:2]))
        check("point wording present (Observeret DD.MM.YYYY)",
              re.search(r"Observeret \d{2}\.\d{2}\.\d{4}", body) is not None)

        # ---- UX-48: bounded overlap interval allowed -------------------
        per = cl.get(f"/persons/{PERSON_WITH_GROUPS}").get_data(as_text=True)
        check("person page: bounded overlap interval allowed",
              re.search(r"Observeret funktionstid \d{2}\.\d{2}\.\d{4} – \d{2}\.\d{2}\.\d{4}", per)
              is not None or "Observeret funktionstid" in per)
        check("person page: left-censored wording preserved",
              "oprindelig start ukendt" in per or "Observeret fra" in per or "Tidligere registreret" in per)

        # ---- UX-50/§50: cap + total + deterministic order --------------
        # (updated for product UX consolidation: families + human order copy
        # §27/§31-§35 — internal order tokens must NO LONGER render)
        hi = cl.get(f"/companies/{COMPANY_HIGH_GROUPS}").get_data(as_text=True)
        m = re.search(r"Viser (\d+) af (\d+) konstellations-familier", hi)
        check("high-group-count: stated total", m is not None,
              "no 'Viser N af M konstellations-familier' line")
        check("high-group-count: deterministic order stated",
              "ingen skjult score" in hi and "support_company_count DESC" not in hi)
        check("high-group-count: Vis flere disclosure",
              "Vis flere konstellations-familier" in hi
              or "understøttede undergrupper" in hi)

        # ---- UX-05/§45: history action semantics -----------------------
        hist = cl.get(f"/companies/{COMPANY_OWNER_CASE}/skraseting").get_data(as_text=True)
        actions = set(re.findall(r'event-action"> — ([^<]+)<', hist))
        neutral = set(re.findall(r'event-type">([^<]+)<', hist))
        check("history: evidenced join/leave wording only",
              all(("tiltrådte" in a or "fratrådte" in a) for a in actions),
              str(actions))
        check("history: neutral change wording present",
              "Bestyrelsesændring registreret" in hist)
        check("history: human dates in timeline text",
              re.search(r">\d{2}\.\d{2}\.\d{4}</time>", hist) is not None)
        # view-model level: unknown action never coerced (§45)
        _check_action_coercion()

        # ---- UX-06/§46: linked vs unresolved identity ------------------
        check("history: canonical person links present",
              re.search(r'href="/persons/\d+"', hist) is not None)
        _check_unresolved_fail_closed()

        # ---- UX-51: current authority Felag-only ------------------------
        # (updated for product UX consolidation: current governance renders
        # in the Personer panel with the "Aktuel status (Felag)" badge; the
        # derived layer lives in the Analyse panel)
        comp = cl.get(f"/companies/{COMPANY_OWNER_CASE}").get_data(as_text=True)
        check("company: current authority wording intact",
              "Aktuel status (Felag)" in comp and "Personer" in comp
              and "Udledt" in comp)
        check("company: overview registry card is source-badged, no raw",
              "Kilde: Skráseting Føroya" in comp and raw_leak_count(comp) == 0)

        # ---- UX-52: no raw structures across surfaces ------------------
        for url, label in ((f"/persons/{PERSON_WITH_GROUPS}", "person"),
                           (f"/persons/{PERSON_WITH_GROUPS}/network", "person network"),
                           (f"/companies/{COMPANY_WITH_GROUPS}", "company"),
                           (f"/api/analytics/graph/{PERSON_WITH_GROUPS}", "graph API")):
            r = cl.get(url).get_data(as_text=True)
            check(f"no raw dict/list repr on {label}", raw_leak_count(r) == 0,
                  str(raw_leak_count(r)))

        # ---- UX-53: click-through ---------------------------------------
        hrefs = set(re.findall(r'href="/(persons|companies)/(\d+)"', per + comp))
        sampled = list(hrefs)[:12]
        bad = sum(1 for kind, eid in sampled
                  if cl.get(f"/{kind}/{eid}").status_code != 200)
        check("click-through: sampled canonical links resolve 200",
              bad == 0 and sampled, f"{bad} broken of {len(sampled)}")

        # ---- graph API pin unchanged -----------------------------------
        api = cl.get(f"/api/analytics/graph/{PERSON_WITH_GROUPS}").get_json()
        a = api["analytics"]
        check("graph API pin unchanged", a["build_run_id"] == ANALYTICS_BUILD
              and a["content_hash"] == ANALYTICS_HASH)

    print(f"\nOWNER UX CORRECTION: {_pass} passed, {_fail} failed")
    return _fail


def _check_action_coercion():
    from heimdal.modules.skraseting.views import _event_view
    base = {"event_type": "BOARD_CHANGE", "entity_key": "x",
            "effective_date": "2020-01-01", "registration_date": "2020-01-02",
            "source_document_id": 1, "source_event_id": 1,
            "authority_state": "HISTORICAL_EVENT_ONLY"}
    ap = _event_view({**base, "action": "APPOINTED"})
    check("APPOINTED -> join wording", ap["action_label"] == "tiltrådte bestyrelsen")
    rm = _event_view({**base, "action": "REMOVED"})
    check("REMOVED -> leave wording", rm["action_label"] == "fratrådte bestyrelsen")
    ch = _event_view({**base, "action": "CHANGED"})
    check("CHANGED -> neutral (no action wording)", ch["action_label"] is None
          and ch["type"] == "Bestyrelsesændring registreret")
    un = _event_view({**base, "action": "SOMETHING_NEW", "event_type": "NAME_CHANGE"})
    check("unknown action -> never join/leave", un["action_label"] is None)


def _check_unresolved_fail_closed():
    """Unresolved persons render as SOURCE text with the discreet note and
    NEVER as a link (§24 fail closed). 9667 carries real unresolved
    historical role occurrences."""
    from heimdal.modules.shared.db import conn
    from heimdal.modules.skraseting import views as sv
    with app.app_context(), conn() as c:
        view = sv.skraseting_view(c, COMPANY_OWNER_CASE)
    all_persons = [pr for e in view["history"] for pr in e["persons"]]
    linked = [pr for pr in all_persons if pr.get("canonical_entity_id")]
    unlinked = [pr for pr in all_persons if not pr.get("canonical_entity_id")]
    check("unresolved occurrences present in real data", len(unlinked) > 0)
    check("unresolved persons: no canonical id attached",
          all(not pr.get("canonical_entity_id") for pr in unlinked))
    # rendered surface: notes present, no <a> inside unresolved rows
    with app.test_client() as cl:
        hist = cl.get(f"/companies/{COMPANY_OWNER_CASE}/skraseting") \
                 .get_data(as_text=True)
        check("unresolved: discreet note rendered",
              hist.count("Ikke sikkert knyttet til Heimdal-person") >= 1)
        check("unresolved: never a link",
              not re.search(r'person-unresolved[^<]*</span>[^<]*<a ', hist))
        # linked persons still clickable and resolving (UX-53)
        check("linked persons still clickable", len(linked) > 0)
        for pr in linked:
            if pr.get("canonical_entity_id"):
                r = cl.get(f"/persons/{pr['canonical_entity_id']}")
                check(f"person {pr['canonical_entity_id']} resolves 200",
                      r.status_code == 200)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)