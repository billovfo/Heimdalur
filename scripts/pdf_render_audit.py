import json, subprocess, pathlib, re, pickle
from PIL import Image
import numpy as np

sel=json.load(open('pdf_render_accuracy_selection.json'))
outdir=pathlib.Path('tmp_render_pages')
results=[]
for s in sel['selected']:
    base=outdir/f"doc{s['document_id']}_obs{s['observation_id']}"
    pdf=str(base)+".pdf"
    r1=subprocess.run(['pdftotext','-enc','UTF-8',pdf,'-'],capture_output=True,text=True)
    poppler=r1.stdout
    r2=subprocess.run(['pdftotext','-layout','-enc','UTF-8',pdf,'-'],capture_output=True,text=True)
    layout=r2.stdout
    et_path=outdir/f"et_obs{s['observation_id']}.txt"
    pypdf_text=et_path.read_text()
    page_images=sorted(outdir.glob(f"doc{s['document_id']}_obs{s['observation_id']}_p*.png"))
    ink=[]
    for pg in page_images:
        img=np.array(Image.open(pg).convert('L'))
        ink.append(round(float((img<128).mean()),4))
    def toks(x):
        t=re.sub(r'[^\wÆØÅæøå.,:/%-]+',' ',(x or '').lower())
        return set(w for w in t.split() if len(w)>=2)
    tp=toks(pypdf_text); to=toks(poppler); to2=toks(layout)
    # tokens pypdf misses entirely (present in poppler) -> extraction-gap candidates
    missing_in_pypdf=sorted(to - tp, key=lambda w:(len(w),w))
    missing_in_poppler=sorted(tp - to, key=lambda w:(len(w),w))
    # date/number sets
    def nums(x):
        x=x or ''
        return set(re.findall(r'\d{1,3}(?:\.\d{3})+|\d+(?:,\d{2})?', x))
    def dateset(x):
        x=x or ''
        return set(re.findall(r'\b\d{1,2}\.\d{1,2}\.\d{4}\b', x))
    d_pypdf, d_pop = dateset(pypdf_text), dateset(poppler)
    n_pypdf, n_pop = nums(pypdf_text), nums(poppler)
    results.append({
        "document_id":s['document_id'],"observation_id":s['observation_id'],"regnr":s['registration_number'],
        "pdf_type":s['pdf_type'],"pages":len(page_images),
        "pypdf_len":len(pypdf_text or ''),"poppler_len":len(poppler or ''),"layout_len":len(layout or ''),
        "pypdf_chars_frac": round(len(pypdf_text or '')/max(len(poppler or ''),1),3),
        "ink_density": ink,
        "tokens_missing_in_pypdf": missing_in_pypdf[:60],
        "tokens_missing_in_poppler": missing_in_poppler[:60],
        "n_tokens_missing_pypdf": len(missing_in_pypdf), "n_tokens_missing_poppler": len(missing_in_poppler),
        "dates_missing_in_pypdf": sorted(d_pop - d_pypdf), "dates_missing_in_poppler": sorted(d_pypdf - d_pop),
        "numbers_missing_in_pypdf": sorted(n_pop - n_pypdf)[:40], "numbers_missing_in_poppler": sorted(n_pypdf - n_pop)[:40],
    })
print(json.dumps([{k:r[k] for k in ['document_id','observation_id','pdf_type','pages','pypdf_chars_frac','n_tokens_missing_pypdf','n_tokens_missing_poppler','ink_density']} for r in results], indent=1))
pickle.dump(results, open('tmp_render_audit_stage1.pkl','wb'))
