/* Find forbindelse (templates/pathfinder.html). Moved out of the template so the
   CSP needs no script-src 'unsafe-inline' (G-05); the paths come in the
   #pf-data JSON block. */

// Swap button — keyboard accessible
document.getElementById('pf-swap')?.addEventListener('click', function(){
  const a=document.getElementById('pf-from'), b=document.getElementById('pf-to');
  if(!a||!b) return; const t=a.value; a.value=b.value; b.value=t; a.focus();
});

(function(){
  const dataEl = document.getElementById('pf-data');
  if (!dataEl) return;
  const { paths, hubs } = JSON.parse(dataEl.textContent);
  const hubIds = new Set(hubs.map(h=>String(h.canonical_entity_id)));
  // Build union graph for mini-graph
  const nodes = new Map();
  const edges = [];
  let edgeIdx=0;
  paths.forEach((path, pi)=>{
    path.forEach(hop=>{
      [hop.from_node, hop.to_node].forEach(n=>{
        const id=String(n.canonical_entity_id);
        if(!nodes.has(id)) nodes.set(id, {id, name:n.display_name, kind:n.entity_kind, is_center:false, radius: hubIds.has(id)?20:16});
      });
      const sid=String(hop.from_node.canonical_entity_id), tid=String(hop.to_node.canonical_entity_id);
      const type = (hop.edge_type||'').includes('OWNERSHIP') ? 'DIRECT_OWNERSHIP' : (hop.edge_type||'').includes('ROLE') ? 'ROLE' : 'CONTROL';
      edges.push({id:'e'+edgeIdx++, source:sid, target:tid, type, label:hop.edge_label||'', parsed_percentage:null, pathIdx:pi});
    });
  });
  const data = {nodes: Array.from(nodes.values()).map(n=>({...n, x:0,y:0})), edges};
  // Mark start/end as centers
  if(nodes.size){
    const first = String(paths[0][0].from_node.canonical_entity_id);
    const last = String(paths[0][paths[0].length-1].to_node.canonical_entity_id);
    nodes.get(first).is_center = true; nodes.get(last).is_center = true;
  }
  const containerId='pathfinder-mini-graph';
  const dummyId = paths[0][0].from_node.canonical_entity_id;
  const g = new HeimdalNetworkGraph(containerId, dummyId, 'COMPANY');
  // Wait for dummy fetch to settle then override
  setTimeout(()=>{
    g.nodes.clear(); data.nodes.forEach(n=> g.nodes.set(n.id, {...n, x:(Math.random()-0.5)*180, y:(Math.random()-0.5)*120, vx:0, vy:0, radius:n.radius}));
    g.edges = data.edges.slice();
    g.updateLedger && g.updateLedger();
    // Custom render hook: highlight selected path + hub halo
    let selected = 0;
    const origRender = g.render.bind(g);
    g.render = function(){
      origRender();
      // overlay halo for hubs and selected path
      const ctx=this.ctx;
      ctx.save(); ctx.translate(this.panX,this.panY); ctx.scale(this.zoom,this.zoom);
      if(hubIds.size){
        this.nodes.forEach(n=>{
          if(hubIds.has(String(n.id))){
            ctx.beginPath(); ctx.arc(n.x,n.y,n.radius+6,0,Math.PI*2);
            ctx.strokeStyle='rgba(217,119,6,0.9)'; ctx.lineWidth=2.5; ctx.setLineDash([]); ctx.stroke();
            ctx.fillStyle='rgba(217,119,6,0.95)'; ctx.font='10px sans-serif'; ctx.textAlign='center';
            const cnt = hubs.find(h=>String(h.canonical_entity_id)===String(n.id))?.path_count||0;
            if(cnt>1){ ctx.beginPath(); ctx.arc(n.x+14,n.y-14,9,0,Math.PI*2); ctx.fillStyle='#D97706'; ctx.fill(); ctx.fillStyle='#fff'; ctx.fillText(String(cnt), n.x+14, n.y-14+3); }
          }
        });
      }
      // highlight selected path edges thick
      this.edges.forEach(e=>{
        if(e.pathIdx===selected){
          const s=this.nodes.get(e.source), t=this.nodes.get(e.target);
          if(!s||!t) return;
          ctx.beginPath(); ctx.moveTo(s.x,s.y); ctx.lineTo(t.x,t.y);
          ctx.strokeStyle='rgba(245,158,11,0.45)'; ctx.lineWidth=6; ctx.stroke();
        }
      });
      ctx.restore();
    };
    g.render();
    // Table row click → select path
    document.querySelectorAll('.pf-row').forEach(row=>{
      row.addEventListener('click', ()=>{
        const idx=parseInt(row.dataset.pathIdx,10);
        selected=idx;
        document.querySelectorAll('.pf-row input[type="radio"]').forEach((r,i)=> r.checked=(i===idx));
        document.querySelectorAll('.path-chain-card').forEach((card,i)=> card.style.outline = i===idx ? '2px solid #D97706' : '');
        g.render();
      });
    });
    document.querySelectorAll('.pf-row input[type="radio"]').forEach((r,i)=>{
      r.addEventListener('change', ()=>{ selected=i; g.render(); });
    });
  }, 450);
  window.pfGraph = g;
  const exportCsv = ()=>{
    const rows=[['sti','hop','fra','relation','til']];
    paths.forEach((p,pi)=>{
      p.forEach((h,hi)=> rows.push([pi+1, hi+1, h.from_node.display_name, h.edge_label, h.to_node.display_name]));
    });
    window.heimdalDownloadCsv('heimdal_stifinder_'+Date.now()+'.csv', rows);
  };
  document.querySelector('[data-pf-action="png"]')?.addEventListener('click', ()=> g.exportPNG());
  document.querySelector('[data-pf-action="csv"]')?.addEventListener('click', exportCsv);
})();

/* T4.6 (F-32): Pathfinder-resolver — genbruger /api/search (person +
   virksomhed). Navn -> kanonisk ID -> sti. Tvetydigt navn giver EKSPLIKTIT
   valg (aldrig gaet); int-ID sendes uaendret videre som i dag. */
(function () {
  'use strict';
  var form = document.querySelector('.pathfinder-form');
  var statusEl = document.getElementById('pf-resolver-status');
  var choicesEls = {
     from: document.getElementById('pf-from-choices'),
     to: document.getElementById('pf-to-choices')
   };
  var listEl = document.getElementById('recent-entities');
  var fields = {
    from: document.getElementById('pf-from'),
    to: document.getElementById('pf-to')
  };
  if (!form || !fields.from || !fields.to) return;

  function isInt(v) { return /^\d+$/.test(v.trim()); }
  function setStatus(msg) { if (statusEl) statusEl.textContent = msg || ''; }
  function hideChoices() {
     Object.keys(choicesEls).forEach(function (side) {
       var choicesEl = choicesEls[side];
       if (!choicesEl) return;
       choicesEl.hidden = true;
       while (choicesEl.firstChild) choicesEl.removeChild(choicesEl.firstChild);
     });
   }
   function search(q) {
    return fetch('/api/search?q=' + encodeURIComponent(q))
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) { return (data && data.results) || []; });
  }
  function resolveSide(side) {
    var raw = fields[side].value.trim();
    if (!raw) { setStatus('Udfyld både "Fra" og "Til".'); return Promise.resolve(false); }
    if (isInt(raw)) return Promise.resolve(true);
    return search(raw).then(function (results) {
      if (results.length === 1) {
        fields[side].value = String(results[0].id);
        return true;
      }
      if (!results.length) {
        setStatus('Ingen entitet matcher "' + raw + '". Prøv et andet navn, et registreringsnummer eller et ID.');
        return false;
      }
      showChoices(side, raw, results);
      return false;
    });
  }
  function showChoices(side, raw, results) {
     var choicesEl = choicesEls[side];
     if (!choicesEl) return;
     hideChoices();
    while (choicesEl.firstChild) choicesEl.removeChild(choicesEl.firstChild);
    var title = document.createElement('p');
    title.className = 'text-sm font-bold';
    title.textContent = '"' + raw + '" matcher ' + results.length + ' entiteter - vælg den rigtige:';
    choicesEl.appendChild(title);
    results.slice(0, 8).forEach(function (r) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn btn-outline text-xs pathfinder-choice';
      b.textContent = r.name + (r.regnr ? ' · ' + r.regnr : '');
      b.setAttribute('aria-label', 'Vælg ' + r.name + ' som ' + (side === 'from' ? 'startpunkt' : 'slutpunkt'));
      b.addEventListener('click', function () {
        fields[side].value = String(r.id);
        hideChoices();
        setStatus('');
      });
      choicesEl.appendChild(b);
    });
    choicesEl.hidden = false;
    setStatus('Flere entiteter matcher - vælg eksplicit (der gaettes ikke).');
  }
  var timer = null;
  ['from', 'to'].forEach(function (side) {
    fields[side].addEventListener('input', function (e) {
      clearTimeout(timer);
      var v = e.target.value.trim();
      if (!listEl) return;
      if (v.length < 2 || isInt(v)) {
        while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
        return;
      }
      timer = setTimeout(function () {
        search(v).then(function (results) {
          while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
          results.slice(0, 8).forEach(function (x) {
            var o = document.createElement('option');
            o.value = x.name;
            listEl.appendChild(o);
          });
        }).catch(function () {});
      }, 250);
    });
  });
  form.addEventListener('submit', function (e) {
    var rawFrom = fields.from.value.trim();
    var rawTo = fields.to.value.trim();
    if (isInt(rawFrom) && isInt(rawTo)) return; // int-path uændret
    e.preventDefault();
    hideChoices();
    setStatus('Søger…');
    resolveSide('from').then(function (r1) {
      if (r1 !== true) return;
      resolveSide('to').then(function (r2) {
        if (r2 !== true) return;
        setStatus('');
        form.submit();
        });
   }).catch(function () {
      setStatus('Søgningen kunne ikke fuldføres - indtast et ID i stedet.');
    });
  });
})();
