import os  # DSNs from tools/dev_env.sh, auth via PGPASSFILE (I-4)
import json, psycopg, pickle, re, pathlib
from collections import defaultdict
from datetime import date

status=json.load(open('full_run_company_status.json'))
completed=[int(k) for k,v in status['companies'].items() if v['status']=='COMPLETED']
pilot=psycopg.connect(os.environ["HEIMDAL_PDFHIST_DB"])

# anchor per company
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

# lysing changes per company (dedupe by obs — a doc may join multiple events)
lysrows=pilot.execute("""
SELECT DISTINCT e.company_id, o.id, d.id, p.structured_data
FROM pdf_source_event e
JOIN pdf_source_event_document sed ON sed.source_event_id=e.id
JOIN pdf_document d ON d.id=sed.document_id
JOIN pdf_document_observation o ON o.document_id=d.id AND o.collection_run_id=424245
JOIN pdf_parse_result p ON p.document_observation_id=o.id
WHERE d.pdf_type='LysingPdf'
""").fetchall()
lys=defaultdict(list)
for cid, obs, doc, sd in lysrows:
    if not sd: continue
    for ch in (sd.get('changes') or []):
        lys[cid].append({"obs":obs,"doc":doc,"ch":ch,"doc_notice_date":sd.get('notice_date')})

def nd(s):
    return re.sub(r'\s+',' ',(s or '')).strip().lower()

def ch_date(ch, doc_notice_date):
    # precedence effective -> registration -> notice -> source_event(doc) date
    for k in ['effective_date','registration_date','notice_date']:
        v=ch.get(k)
        if v: return v
    if doc_notice_date: return doc_notice_date
    return None

MEMBERTYPES={'BOARD_CHANGE':'board','MANAGEMENT_CHANGE':'management','AUDITOR_CHANGE':'auditor'}
SCALARS={'NAME_CHANGE':'name','ADDRESS_CHANGE':'address','CAPITAL_CHANGE':'capital','PURPOSE_CHANGE':'purpose','SIGNING_RULE_CHANGE':'signing_rules','COMPANY_FORM_CHANGE':'company_form'}

projection_recs={}
recon=[]
from collections import Counter
FIELD_STATS={'board':[0,0],'management':[0,0],'auditor':[0,0]}
conflict_types=Counter()
conflict_types=Counter()
uncertainties_total=0
total_changes_considered=0
total_applied=0
total_skipped=0
post_snapshot_companies=[]

for cid in sorted(anchor.keys()):
    snaps=anchor[cid]
    snap_dates=sorted(set(vf for vf,_,_ in snaps))
    latest_vf=snap_dates[-1]
    # anchor record: choose the snapshot with latest valid_from (tie-break latest obs)
    anchor_vf, anchor_obs, asd = [s for s in snaps if s[0]==latest_vf][0]
    if len(snap_dates)>1: post_snapshot_companies.append(cid)
    # state copy from anchor
    anchor_sd = snaps[-1][2]
    state={
        "name": (anchor_sd or {}).get('company_name'),
        "aliases": (anchor_sd or {}).get('alias_names'),
        "address": (anchor_sd or {}).get('company_address'),
        "capital": (anchor_sd or {}).get('capital'),
        "purpose": (anchor_sd or {}).get('purpose'),
        "signing_rules": (anchor_sd or {}).get('signing_rules'),
        "board": list((anchor_sd or {}).get('board') or []),
        "management": list((anchor_sd or {}).get('management') or []),
        "auditor": list((anchor_sd or {}).get('auditor') or []),
        "company_form": (anchor_sd or {}).get('parsed_entity_type') or (anchor_sd or {}).get('entity_type'),
    }
    changes_applied=[]; changes_skipped=[]; uncertainties=[]
    for item in lys.get(cid, []):
        ch=item['ch']; typ=ch.get('type'); act=(ch.get('action') or '').upper()
        d=ch_date(ch, item['doc_notice_date'])
        total_changes_considered+=1
        if not d:
            uncertainties.append({"obs":item['obs'],"type":typ,"reason":"CURRENT_STATE_UNCERTAIN_MISSING_DATE","change":{k:ch.get(k) for k in ('entity','type','action')}})
            changes_skipped.append({"obs":item['obs'],"type":typ,"reason":"MISSING_DATE"})
            uncertainties_total+=1
            continue
        if d <= str(latest_vf):
            # historical change already inside anchor full state
            changes_skipped.append({"obs":item['obs'],"type":typ,"action":act,"date":d,"reason":"PRE_OR_SAME_SNAPSHOT_ALREADY_IN_ANCHOR" if d==str(latest_vf) else "PRE_SNAPSHOT_ALREADY_IN_ANCHOR"})
            continue
        # post-snapshot change (after anchor snapshot)
        post_snapshot_companies.append(cid) if False else None
        if typ in MEMBERTYPES:
            fam=MEMBERTYPES[typ]
            ent=ch.get('entity')
            if not ent:
                uncertainties.append({"obs":item['obs'],"type":typ,"reason":"MEMBERSHIP_CHANGE_NO_ENTITY"})
                changes_skipped.append({"obs":item['obs'],"type":typ,"reason":"NO_ENTITY"})
                continue
            key=nd(ent)
            names=[k for k in ['name'] ]
            if act in ('APPOINTED','ADDED','JOINED'):
                state[fam]=state[fam]+[{"name":ent,"source":"post_snapshot_change"}]
                changes_applied.append({"obs":item['obs'],"type":typ,"action":act,"entity":ent,"date":d})
                total_applied+=1
            elif act in ('REMOVED','RESIGNED','LEFT'):
                before=len(state[fam])
                state[fam]=[m for m in state[fam] if nd(m.get('name') if isinstance(m,dict) else m)!=key]
                changes_applied.append({"obs":item['obs'],"type":typ,"action":act,"entity":ent,"date":d,"removed":before-len(state[fam])})
                total_applied+=1
            else:
                uncertainties.append({"obs":item['obs'],"type":typ,"action":act,"reason":"UNKNOWN_MEMBERSHIP_ACTION"})
                changes_skipped.append({"obs":item['obs'],"type":typ,"reason":"UNKNOWN_ACTION"})
        elif typ in ('NAME_CHANGE','ADDRESS_CHANGE','CAPITAL_CHANGE','PURPOSE_CHANGE','SIGNING_RULE_CHANGE','COMPANY_FORM_CHANGE'):
            fld={'NAME_CHANGE':'name','ADDRESS_CHANGE':'address','CAPITAL_CHANGE':'capital','PURPOSE_CHANGE':'purpose','SIGNING_RULE_CHANGE':'signing_rules','COMPANY_FORM_CHANGE':'company_form'}[typ]
            state[fld]=ch.get('entity') if typ!='NAME_CHANGE' else ch.get('entity')
            changes_applied.append({"obs":item['obs'],"type":typ,"action":act,"value":(ch.get('entity') or '')[:60],"date":d})
            total_applied+=1
        elif typ in ('ARTICLES_CHANGE','OTHER_CHANGE'):
            changes_skipped.append({"obs":item['obs'],"type":typ,"date":d,"reason":"EVENT_ONLY_NO_STATE_SEMANTICS"})
        else:
            uncertainties.append({"obs":item['obs'],"type":typ,"reason":"UNKNOWN_CHANGE_TYPE"})
            changes_skipped.append({"obs":item['obs'],"type":typ,"reason":"UNKNOWN_CHANGE_TYPE"})

    # reconciliation: when no post-snapshot changes applied, projected state MUST equal anchor semantically
    diffs={}
    norm=lambda x: nd(x if isinstance(x,str) else json.dumps(x, ensure_ascii=False))
    if changes_applied:
        pass
    else:
        for f,pk,ak in [("name",state['name'],anchor_sd.get('company_name')),("address",state['address'],anchor_sd.get('company_address')),
               ("capital",state['capital'],anchor_sd.get('capital')),("purpose",state['purpose'],anchor_sd.get('purpose')),
               ("signing",state['signing_rules'],anchor_sd.get('signing_rules')),("company_form",state['company_form'],(anchor_sd.get('parsed_entity_type') or (anchor_sd.get('entity_type'))))]:
            if norm(pv:=pk)!=norm(av:=ak):
                diffs[f]="MISMATCH"
        for fam in ['board','management','auditor']:
            proj=sorted(nd(m.get('name') if isinstance(m,dict) else str(m)) for m in state[fam])
            anch=sorted(nd(m.get('name') if isinstance(m,dict) else str(m)) for m in (anchor_sd.get(fam) or []))
            if proj!=anch:
                diffs[fam]="MEMBERSHIP_MISMATCH"
    if changes_applied:
        post_snapshot_companies.append(cid)
    rec={
        "accounts_dates": (anchor_sd or {}).get('accounts_dates'),
        "anchor_snapshot_document": anchor_obs,
        "anchor_snapshot_date": str(latest_vf),
        "projected_state": {k:state[k] for k in ['name','aliases','address','capital','purpose','signing_rules','board','management','auditor','company_form']},
        "changes_applied": changes_applied,
        "changes_skipped": changes_skipped,
        "uncertainties": uncertainties,
        "evidence": {"anchor_observation_id": anchor_obs, "applied_obs":[c['obs'] for c in changes_applied]},
    }
    projection_recs[str(cid)]=rec
    if diffs:
        recon.append({"registration_number":str(cid),"unexplained_field_diffs":diffs,"with_applied_changes":bool(changes_applied)})
    # field stats
    for fam in ['board','management','auditor']:
        proj=sorted(nd(m.get('name') if isinstance(m,dict) else str(m)) for m in state[fam])
        anch=sorted(nd(m.get('name') if isinstance(m,dict) else str(m)) for m in (anchor_sd.get(fam) or []))
        FIELD_STATS[fam][1]+=1
        if proj==anch: FIELD_STATS[fam][0]+=1

pickle.dump({'recs':projection_recs}, open('/tmp/shadow_recs.pkl','wb'))
print("companies projected:", len(projection_recs))
print("total changes considered:", total_changes_considered, "applied:", total_applied)
print("post-snapshot companies:", len(set(post_snapshot_companies)))
print("no-change companies baseline mismatch count:", len(recon))
print("membership field stats:", FIELD_STATS)
