import os  # DSNs from tools/dev_env.sh, auth via PGPASSFILE (I-4)
import json, psycopg, re, pathlib
from collections import defaultdict, Counter
from datetime import date

status=json.load(open('full_run_company_status.json'))
completed=[int(k) for k,v in status['companies'].items() if v['status']=='COMPLETED']
pilot=psycopg.connect(os.environ["HEIMDAL_PDFHIST_DB"])

rows=pilot.execute("""
SELECT h.company_id, h.valid_from, h.document_observation_id, p.structured_data
FROM company_pdf_snapshot_history h
JOIN pdf_parse_result p ON p.document_observation_id=h.document_observation_id
WHERE h.company_id = ANY(%s)
ORDER BY h.company_id, h.valid_from, h.document_observation_id
""", (completed,)).fetchall()
anchor=defaultdict(list)
for cid, vf, obs, sd in rows:
    anchor[cid].append((vf, obs, sd))

lysrows=pilot.execute("""
SELECT DISTINCT e.company_id, o.id, d.id, p.structured_data, p.structured_data->'changes'
FROM pdf_source_event e
JOIN pdf_source_event_document sed ON sed.source_event_id=e.id
JOIN pdf_document d ON d.id=sed.document_id
JOIN pdf_document_observation o ON o.document_id=d.id AND o.collection_run_id=424245
JOIN pdf_parse_result p ON p.document_observation_id=o.id
WHERE d.pdf_type='LysingPdf'
""").fetchall()
lys=defaultdict(list)
for cid, obs, doc, sd, changes in lysrows:
    if not sd: continue
    for ch in (changes or []):
        lys[cid].append({"obs":obs,"doc":doc,"ch":ch,"doc_notice_date":sd.get('notice_date')})

def nd(s): return re.sub(r'\s+',' ',(s or '')).strip().lower()
def ch_date(ch, doc_notice):
    for k in ['effective_date','registration_date','notice_date']:
        if ch.get(k): return ch[k]
    return doc_notice

MEMBERTYPES={'BOARD_CHANGE':'board','MANAGEMENT_CHANGE':'management','AUDITOR_CHANGE':'auditor'}
SCALA={'NAME_CHANGE':'name','ADDRESS_CHANGE':'address','CAPITAL_CHANGE':'capital','PURPOSE_CHANGE':'purpose','SIGNING_RULE_CHANGE':'signing_rules'}
EVENT_ONLY={'ARTICLES_CHANGE','OTHER_CHANGE'}

recs={}; post_snapshot=[]; same_day_flagged=[]; field_metrics=defaultdict(lambda:[0,0])
conflict_types=Counter(); unc_by_reason=Counter()
n_applied=0; n_considered=0
for cid in sorted(anchor.keys()):
    snaps=anchor[cid]
    snap_dates=sorted(set(vf for vf,_,_ in snaps))
    latest_vf=snap_dates[-1]
    anchor_sd=[s for s in snaps if s[0]==latest_vf][-1][2]
    state={
        "name":(anchor_sd or {}).get('company_name'), "aliases":(anchor_sd or {}).get('alias_names'),
        "address":(anchor_sd or {}).get('company_address'), "capital":(anchor_sd or {}).get('capital'),
        "purpose":(anchor_sd or {}).get('purpose'), "signing_rules":(anchor_sd or {}).get('signing_rules'),
        "board":list((anchor_sd or {}).get('board') or []), "management":list((anchor_sd or {}).get('management') or []),
        "auditor":list((anchor_sd or {}).get('auditor') or []), "accounts_dates":(anchor_sd or {}).get('accounts_dates'),
        "company_form":(anchor_sd or {}).get('parsed_entity_type'),  # not present in Felag structured_data (reported as unavailable)
    }
    applied=[]; skipped=[]; uncertainties=[]
    for item in lys.get(cid, []):
        ch=item['ch']; typ=ch.get('type'); act=(ch.get('action') or '').upper()
        d=ch_date(ch, item.get('doc_notice_date'))
        if not d:
            unc={"obs":item['obs'],"type":typ,"reason":"CURRENT_STATE_UNCERTAIN_MISSING_DATE"}
            uncertainties.append(unc); skipped.append({"obs":item['obs'],"type":typ,"reason":"MISSING_DATE"})
            continue
        if d < str(latest_vf):
            skipped.append({"obs":item['obs'],"type":typ,"action":act,"date":d,"reason":"PRE_SNAPSHOT_ALREADY_IN_ANCHOR"}); continue
        if d == str(latest_vf):
            # same-day as anchor snapshot: anchor is authoritative full state of that day
            if typ in MEMBERTYPES:
                same_day_flagged.append({"cid":cid,"obs":item['obs'],"action":act,"entity":ch.get('entity')})
            skipped.append({"obs":item['obs'],"type":typ,"action":act,"date":d,"reason":"SAME_DAY_ANCHOR_AUTHORITY"}); continue
        # strictly after snapshot
        if typ == 'ARTICLES_CHANGE' or typ=='OTHER_CHANGE':
            # event-only; ALSO if date is anomalous (year>2100) treat uncertainty
            if int(d[:4])>2100:
                uncertainties.append({"obs":item['obs'],"type":typ,"reason":"SOURCE_DATE_ANOMALY","date":d,"raw":ch.get('raw_text','')[:60]})
            skipped.append({"obs":item['obs'],"type":typ,"date":d,"reason":"EVENT_ONLY_NO_STATE_SEMANTICS"}); continue
        post_snapshot.append(cid)
        if typ in MEMBERTYPES:
            fam=MEMBERTYPES[typ]; ent=ch.get('entity')
            if not ent:
                uncertainties.append({"obs":item['obs'],"type":typ,"reason":"NO_ENTITY"}); continue
            key=nd(ent)
            if act in ('APPOINTED','ADDED','JOINED'):
                state[fam]=state[fam]+[{"name":ent,"source":"post_snapshot_change","evidence_obs":item['obs']}]
                applied.append({"obs":item['obs'],"type":typ,"action":act,"entity":ent,"date":d})
            elif act in ('REMOVED','RESIGNED','LEFT'):
                before=len(state[fam])
                state[fam]=[m for m in state[fam] if nd(m.get('name') if isinstance(m,dict) else str(m))!=key]
                applied.append({"obs":item['obs'],"type":typ,"action":act,"entity":ent,"date":d,"removed_count":before-len(state[fam])})
            else:
                uncertainties.append({"obs":item['obs'],"type":typ,"action":act,"reason":"UNKNOWN_MEMBERSHIP_ACTION"})
        elif typ in SCALA:
            state[SCALA[typ]]=ch.get('entity')
            applied.append({"obs":item['obs'],"type":typ,"action":act,"value":(ch.get('entity') or '')[:60],"date":d})
        else:
            uncertainties.append({"obs":item['obs'],"type":typ,"reason":"UNKNOWN_CHANGE_TYPE"})
            skipped.append({"obs":item['obs'],"type":typ,"reason":"UNKNOWN_CHANGE_TYPE"})
    n_applied+=len(applied)
    rec={"registration_number":str(cid),"anchor_snapshot_document":[s for s in snaps if s[0]==latest_vf][-1][1],
         "anchor_snapshot_date":str(latest_vf),
         "projected_state":{k:state[k] for k in ['name','aliases','address','capital','purpose','signing_rules','board','management','auditor','company_form','accounts_dates']},
         "changes_applied":applied,"changes_skipped":skipped,"uncertainties":uncertainties,
         "evidence":{"anchor_observation_id":[s for s in snaps if s[0]==latest_vf][-1][1],"applied_obs":[a['obs'] for a in applied]}}
    recs[str(cid)]=rec


# ---- finalization ----
norm=lambda x: nd(x if isinstance(x,str) else json.dumps(x, ensure_ascii=False))
recon=[]; cm=Counter()
for cid_s, rec in recs.items():
    if rec['changes_applied']:
        conflict_types['EXPECTED_POST_SNAPSHOT_CHANGE']+=1
        continue
    # must equal anchor
    # recompute anchor copy for comparison
    cid=int(cid_s)
    snaps=anchor[cid]
    latest_vf=sorted(set(vf for vf,_,_ in snaps))[-1]
    asd=[s for s in snaps if s[0]==latest_vf][-1][2]
    proj=rec['projected_state']; diffs={}
    def canon(x):
        if x is None: return ""
        if isinstance(x,(list,dict)):
            if len(x)==0: return ""
            return nd(json.dumps(x, ensure_ascii=False))
        return nd(str(x))
    def eq(x, y): return canon(x)==canon(y)
    for f,ak in [('name','company_name'),('aliases','alias_names'),('address','company_address'),('capital','capital'),('purpose','purpose'),('signing_rules','signing_rules'),('board','board'),('management','management'),('auditor','auditor'),('accounts_dates','accounts_dates')]:
        field_metrics[f][1]+=1
        ok=eq(proj[f], asd.get(ak))
        if ok: field_metrics[f][0]+=1
        else: diffs[f]="MISMATCH"
    # company_form unavailable in anchor jsonb: count separately
    if asd.get('parsed_entity_type') is None and proj['company_form'] is None:
        field_metrics['company_form'][1]+=1; field_metrics['company_form'][0]-=0  # availability handled in metrics
    if diffs:
        recon.append({"registration_number":cid_s,"diffs":diffs}); conflict_types['PROJECTION_LOGIC_ERROR']+=len(diffs)
    else:
        conflict_types['BASELINE_ANCHOR_IDENTITY']+=1
print("projected", len(recs), "baseline mismatches:", len(recon))
print("field metrics:", {k:v for k,v in field_metrics.items()})
print("conflicts:", dict(conflict_types))
print("same_day flagged:", len(same_day_flagged), "post-snapshot:", set(post_snapshot))
out={'recs':recs,'same_day':same_day_flagged,'field_metrics':{k:v for k,v in field_metrics.items()},'conflicts':dict(conflict_types),'post_snapshot':sorted(set(post_snapshot)),'baseline_mismatches':recon}
import pickle
pickle.dump(out, open('tmp_shadow_out.pkl','wb'))
