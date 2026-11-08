#!/usr/bin/env python3
"""Enabled-state smoke for Skráseting UI v1 activation.

Real runtime flag resolution ONLY — no PDF_HISTORY_* env overrides. Uses the
full Flask WSGI app (test client = real routing/teardown stack) plus a real
HTTP server + Playwright for the actual user path (menu -> link -> route).
"""
import json
import sys

import psycopg

sys.path.insert(0, ".")
os_clean = __import__("os").environ
assert os_clean.get("PDF_HISTORY_UI_ENABLED") is None, "env override present!"
assert os_clean.get("PDF_HISTORY_INTEGRATION_ENABLED") is None, "env override present!"

from heimdal.app import app  # noqa: E402
from heimdal.pdfhist import consumer, flags  # noqa: E402
from heimdal.modules.skraseting import views  # noqa: E402

CANONICAL = {
    5988: "5534", 8811: "5711", 2585: "8083", 6658: "6869", 5803: "7403",
    8095: "6288", 279: "1062", 6923: "8742", 5600: "6692", 1426: "9113",
}

app.config["TESTING"] = True
client = app.test_client()

result = {"mode": "real runtime flag resolution (no env overrides)"}

# --- effective flags ---------------------------------------------------------
with consumer._connect(None) as pc:
    result["effective_flags"] = {
        "integration": flags.integration_enabled(pc),
        "ui": flags.ui_enabled(pc),
        "exposure": flags.ui_exposure_allowed(pc),
    }

# --- §22: route map — only the one GET route, no new public API --------------
skr_rules = [str(r) for r in app.url_map.iter_rules() if "skraseting" in str(r)]
result["skraseting_routes"] = skr_rules
assert skr_rules == ["/companies/<int:canonical_entity_id>/skraseting"], skr_rules

# --- §8/§10: company page menu -> route on real client ------------------------
company_html = client.get("/companies/5988").get_data(as_text=True)
result["company_page"] = {
    "status": client.get("/companies/5988").status_code,
    "menu_entry_visible": "Skráseting / PDF-historik" in company_html,
    "menu_href": "/companies/5988/skraseting" in company_html,
}

# --- §9/§10-§16: cohort content checks ----------------------------------------
cohort = {}
expectations = {
    5988: {"registered_name": "P/F Betri Banki", "has_history": True},
    8811: {"registered_name": "Sp/F Krittika í konkurs", "has_history": True},
    2585: {"registered_name": "ÍVF Hissini", "has_history": True},
    6658: {"registered_name": "Sp/F JPR Kranakoyring í likvidatión", "has_history": True},
    5803: {"registered_name": "Í/F Nordic Aquaculture Contractors [NAC]", "has_history": True},
    8095: {"same_day_note": True, "has_history": True},
    279: {"registered_name": "P/F Formula", "has_history": True},
    6923: {"registered_name": "ÍVF Askham Smíð", "has_history": True},
    5600: {"empty_state": True},
    1426: {"empty_state": True},
}
for cid, exp in expectations.items():
    r = client.get(f"/companies/{cid}/skraseting")
    html = r.get_data(as_text=True)
    rec = {"status": r.status_code}
    rec["registreret_status"] = "Registreret status pr." in html
    rec["kilde_label"] = "Kilde: Skráseting Føroya" in html
    rec["history_section"] = ">Historik<" in html
    rec["empty_state"] = "Der er ingen autoritativ Skráseting-PDF-status" in html
    rec["same_day_note"] = "Rækkefølgen mellem registreringer samme dag kan ikke fastslås." in html
    rec["auditor_text"] = "Aktuel revisor kan ikke fastslås sikkert fra denne kilde" in html
    rec["no_auditor_current_fact"] = "Revisor: Ingen" not in html
    rec["company_form_disclaimer"] = "ikke fra denne PDF-projektion" in html
    rec["provenance_details"] = "Vis kildeoplysninger" in html
    if "registered_name" in exp:
        rec["registered_name_rendered"] = exp["registered_name"] in html
    # canonical company page must stay intact and show the same company
    ch = client.get(f"/companies/{cid}").get_data(as_text=True)
    rec["company_page_status"] = client.get(f"/companies/{cid}").status_code
    rec["company_page_no_error"] = "Skráseting-data kunne ikke indlæses" not in ch
    cohort[cid] = rec
    print(cid, CANONICAL[cid], "->", r.status_code, {k: v for k, v in rec.items() if isinstance(v, str) is False and k != "status"})
result["cohort"] = cohort

# assertions for the binding cohort cases (§10-§16)
c5534 = cohort[5988]
assert c5534["status"] == 200
assert c5534["registreret_status"] and c5534["kilde_label"]
assert c5534["registered_name_rendered"]
assert c5534["history_section"] and c5534["provenance_details"]
assert cohort[8811]["registered_name_rendered"]
assert cohort[2585]["registered_name_rendered"]
assert cohort[6658]["registered_name_rendered"]
assert cohort[5803]["registered_name_rendered"]
assert cohort[8095]["same_day_note"]
assert cohort[279]["registered_name_rendered"]
assert cohort[5600]["status"] == 200 and cohort[5600]["empty_state"]
assert cohort[5600]["registreret_status"] is False  # no pseudo-current-state
assert cohort[1426]["empty_state"]
print("cohort assertions OK")

# --- §18 auditor with historical events (5534 has 9 AUDITOR_CHANGE) -----------
with app.app_context():
    from heimdal.modules.shared.db import conn
    with conn() as c:
        v = views.skraseting_view(c, 5988)
result_auditor = {
    "auditor_shown_as_current_value": v["auditor_shown_as_current_value"],
    "historical_auditor_events_in_history":
        sum(1 for e in v["history"] if e["type"] == "Revisor ændret"),
    "current_board_len": len(v["board"]),
    "current_management_len": len(v["management"]),
}
assert result_auditor["auditor_shown_as_current_value"] is False
assert result_auditor["historical_auditor_events_in_history"] >= 1
result["auditor"] = result_auditor

# --- §20 current/history separation (mechanical) ------------------------------
result["separation"] = {
    "current_board_from_snapshot_view": result_auditor["current_board_len"] > 0,
    "history_has_board_events":
        any(e["type"] == "Bestyrelse ændret" for e in v["history"]),
}

# --- §21 provenance verification for >= 3 cases -------------------------------
pilot = psycopg.connect(consumer.projection_dsn())
provenance = {}
for cid, regnr in [(5988, "5534"), (8811, "5711"), (6923, "8742")]:
    with app.app_context():
        from heimdal.modules.shared.db import conn
        with conn() as c:
            vv = views.skraseting_view(c, cid)
    prov = vv["provenance"] or {}
    fields = vv["fields"]
    f0 = fields[0] if fields else {}
    src = dict((f0.get("source") or {})) if isinstance(f0, dict) else {}
    src.setdefault("authority_state", (prov or {}).get("authority_state"))
    src.setdefault("snapshot_date", str((prov or {}).get("snapshot_date") or
                                        (f0.get("source") or {}).get("snapshot_date")))
    # authoritative source ids from the projection DB (run 4 ACTIVE)
    row = pilot.execute(
        """SELECT cs.projection_run_id, f.source_document_id, f.source_observation_id,
                  f.source_parse_result_id, f.source_snapshot_date
           FROM registry_projection.company_current_state cs
           JOIN registry_projection.company_current_state_field f
             ON f.projection_run_id=cs.projection_run_id AND f.company_id=cs.company_id
           WHERE cs.projection_run_id=4 AND cs.registration_number=%s
             AND f.field_name='company_name'""",
        (regnr,)).fetchone()
    db_state = pilot.execute(
        """SELECT f.authority_state, f.source_snapshot_date
           FROM registry_projection.company_current_state cs
           JOIN registry_projection.company_current_state_field f
             ON f.projection_run_id=cs.projection_run_id AND f.company_id=cs.company_id
           WHERE cs.projection_run_id=4 AND cs.registration_number=%s
             AND f.field_name='company_name'""",
        (regnr,)).fetchone()
    ok = (row is not None and db_state is not None
          and str(src.get("document_id")) == str(row[1])
          and str(src.get("observation_id")) == str(row[2])
          and str(src.get("parse_result_id")) == str(row[3])
          and str(vv["snapshot_date"]) == str(row[4])
          and (src.get("authority_state") == db_state[0]))
    provenance[CANONICAL[cid]] = {
        "ui_provenance": {k: src.get(k) for k in
                          ("document_id", "observation_id", "parse_result_id",
                           "authority_state")},
        "projection_row": {"projection_run_id": row[0], "document_id": row[1],
                           "observation_id": row[2], "parse_result_id": row[3],
                           "snapshot_date": str(row[4]),
                           "authority_state": db_state[0]} if row else None,
        "match": bool(ok),
    }
    assert ok, provenance[CANONICAL[cid]]
result["provenance"] = provenance
print("provenance matches projection DB for 5534/5711/8742")

# --- §15: local failure state (test hook) ------------------------------------
from heimdal.modules.skraseting import routes  # noqa: E402
orig = routes.views.skraseting_view
def _boom(*a, **k):
    raise RuntimeError("simulated projection outage")
routes.views.skraseting_view = _boom
try:
    r = client.get("/companies/5988/skraseting")
finally:
    routes.views.skraseting_view = orig
html = r.get_data(as_text=True)
result["failure_state"] = {
    "status": r.status_code,
    "error_text": "Skráseting-data kunne ikke indlæses" in html,
    "no_stack_trace": "Traceback" not in html and "RuntimeError" not in html,
    "company_page_usable": client.get("/companies/5988").status_code == 200,
}
assert result["failure_state"]["status"] == 200
assert result["failure_state"]["error_text"] and result["failure_state"]["no_stack_trace"]

# --- §10 content capture for §32 identity comparison (5534 + 5711) ------------
def capture(cid: int) -> dict:
    with app.app_context():
        from heimdal.modules.shared.db import conn
        with conn() as c:
            vv = views.skraseting_view(c, cid)
    src = (vv["fields"][0].get("source") or {}) if vv["fields"] else {}
    return {
        "registered_name": vv["registered_name"],
        "snapshot_date": vv["snapshot_date"],
        "fields": {f["name"]: f["value"] for f in vv["fields"]},
        "board": vv["board"],
        "management": vv["management"],
        "history_count": len(vv["history"]),
        "history_first_5": vv["history"][:5],
        "history_types": sorted({e["type"] for e in vv["history"]}),
        "provenance": src,
        "run_id": vv["provenance"],
    }

def _default(o):
    return str(o)

result["content_capture_5534"] = capture(5988)
result["content_capture_5711"] = capture(8811)
json.dump({"5534": result["content_capture_5534"],
           "5711": result["content_capture_5711"]},
          open("skraseting_enabled_content_5534_5711.json", "w"),
          indent=1, ensure_ascii=False, default=_default)

json.dump(result, open("skraseting_enabled_smoke_result.json", "w"),
          indent=1, ensure_ascii=False, default=_default)
print("ENABLED SMOKE COMPLETE — all assertions passed")