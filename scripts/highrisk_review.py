import os  # DSNs from tools/dev_env.sh, auth via PGPASSFILE (I-4)
import json, psycopg, re, pathlib
pilot=psycopg.connect(os.environ["HEIMDAL_PDFHIST_DB"])
shadow=json.load(open('full_run_shadow_current_state.json'))
samelib={s['cid']: s for s in shadow['same_day_flagged']}
HR=['7053','8408','1869','5273','5534','7321','3690','2868','3691','2695','4963','883','1924','4800','3927','3491','6119','100','2574','4623']
def norm(s): return re.sub(r'\s+',' ',(s or '')).strip().lower()
results=[]; material=[]
for cid in HR:
    rec=companies[cid] if (companies:=shadow['companies']) else None
    rec=json.loads(json.dumps(shadow['companies'][cid]))
    anchor_sd=pilot.execute("""
      SELECT p.structured_data FROM company_pdf_snapshot_history h
      JOIN pdf_parse_result p ON p.document_observation_id=h.document_observation_id
      WHERE h.company_id=%s ORDER BY h.valid_from DESC, h.document_observation_id DESC LIMIT 1
    """,(int(cid),)).fetchone()[0]
    lys=pilot.execute("""
      SELECT DISTINCT o.id, p.structured_data->'changes'
      FROM pdf_source_event e
      JOIN pdf_source_event_document sed ON sed.source_event_id=e.id
      JOIN pdf_document d ON d.id=sed.document_id
      JOIN pdf_document_observation o ON o.document_id=d.id AND o.collection_run_id=424245
      JOIN pdf_parse_result p ON p.document_observation_id=o.id
      WHERE e.company_id=%s AND d.pdf_type='LysingPdf'
    """,(int(cid),)).fetchall()
    trail=[]
    for obs, changes in lys:
        for ch in (changes or []):
            d=None
            for k in ('effective_date','registration_date','notice_date'):
                if ch.get(k): d=ch[k]; break
            if d and ch.get('type'):
                trail.append({"obs":obs,"date":d,"type":ch.get('type'),"action":ch.get('action'),"entity":(ch.get('entity') or '').strip()})
    trail.sort(key=lambda x:(x['date'],x['obs'] or 0))
    # removed-member-absent check
    issues=[]
    # collect issues properly
    fam_issue_list=[]
    for fam,famtype in [('board','BOARD_CHANGE'),('management','MANAGEMENT_CHANGE'),('auditor','AUDITOR_CHANGE')]:
        anchor_names={norm(m.get('name') if isinstance(m,dict) else str(m)) for m in (anchor_sd.get(fam) or [])}
        for t in trail:
            if t['type']==famtype and (t['action'] or '').upper() in ('REMOVED','RESIGNED','LEFT') and t['entity'] and norm(t['entity']) in anchor_names:
                fam_issue_list.append({"family":fam,"entity":t['entity'],"removed_date":t['date'],"obs":t['obs']})
    # last change per family replay consistency (informational)
    fam_issue_list.sort(key=lambda x:(x['removed_date'],x['obs']))
    # build review record
    rv={
      "registration_number": cid,
      "anchor_date": rec['anchor_snapshot_date'],
      "trail_changes": len(trail),
      "last_membership_changes": [t for t in trail if t['type'] in ('BOARD_CHANGE','MANAGEMENT_CHANGE','AUDITOR_CHANGE')][-6:],
      "removed_member_still_in_anchor": fam_issue_list,
      "same_day": [samelib[int(cid)]] if int(cid) in samelib else [],
      "uncertainties": rec['uncertainties'],
      "applied_changes": rec['changes_applied'],
      "final_anchor_membership": {f: [(m.get('name') if isinstance(m,dict) else str(m)) for m in (anchor_sd.get(f) or [])] for f in ['board','management','auditor']},
      "scalar_history": [{"date":t['date'],"type":t['type'],"entity":t['entity'][:60]} for t in trail if t['type'] in ('NAME_CHANGE','ADDRESS_CHANGE','CAPITAL_CHANGE','PURPOSE_CHANGE','SIGNING_RULE_CHANGE')][-6:],
      "event_only": [{ "date":t['date'],"type":t['type'],"obs":t['obs']} for t in trail if t['type'] in ('ARTICLES_CHANGE','OTHER_CHANGE')][:6],
      "projection_result": "IDENTICAL_TO_ANCHOR" if not rec['changes_applied'] else "POST_SNAPSHOT_APPLIED",
    }
    # material checks for review
    if cid=='7053':
        rv['material_finding']={"severity":"CURRENT_STATE_UNCERTAIN (same-day)","category":"SNAPSHOT_CONFLICT/SAME_DAY","detail":"same-day Lysing REMOVED the stjóri (reg 2026-08-31) but same-day anchor still lists him; day-granularity ordering unresolvable; projection keeps anchor per policy — flagged, owner should decide whether current management may be empty."}
    if cid=='1869':
        rv['material_finding']={"severity":"MINOR (source artifact)","category":"SOURCE_DATE_ARTIFACT","detail":"effective_date 2924-11-19 from source text 'Viðtøkur broyttar: 19.11.2924' (typo in source PDF text layer, verified via independent extraction); event-only; no projection impact"}
    if cid=='8408':
        rv['manual_check']="anchor == same-day Lysing semantics (Arnhold appointed and listed, Sonni removed and absent) — CONSISTENT"
    if cid=='5273':
        # extra snapshot from validation run at 2026-08-30 vs current 2026-08-31: verify anchor picked 424245
        rv['manual_check']="two snapshots: 2026-08-30 (run 424243 obs 561, historical validation) + 2026-08-31 (run 424245 obs 7073); anchor=published latest"
    rv.pop('_trail',None) if False else None
    if fam_issue_list:
        material.append((cid, fam_issue_list))
    results.append(rv)
import pickle
pickle.dump(results, open('tmp_highrisk_review.pkl','wb'))
print("reviewed", len(results))
for r in results:
    f=r['removed_member_still_in_anchor']
    print(r['registration_number'], "changes", r['trail_changes'], "issues", len(f), "same-day", len(r['same_day']), "uncert", len(r['uncertainties']))
    if f: print("   ISSUE:", json.dumps(f, ensure_ascii=False)[:200])
