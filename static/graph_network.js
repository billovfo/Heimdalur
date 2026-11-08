/**
 * Heimdal Force-Directed Network Graph Visualizer
 * Zero-dependency, pure HTML5 Canvas spring-electrical graph simulation.
 */

class HeimdalNetworkGraph {
  constructor(containerId, entityId, entityKind) {
    this.container = document.getElementById(containerId);
    if (!this.container) return;
    // T4.3 (F-10): visible states — never a silently blank canvas
    this.shell = new (window.HeimdalGraphShell || function (el) {
      this.el = el; this.msg = null;
      this.setState = function () {};
    })(this.container, { surface: 'netværksgrafen', retry: function () { location.reload(); } });
    this.shell.setState('LOADING');

    this.entityId = entityId;
    this.entityKind = entityKind;
    this.canvas = document.createElement('canvas');
    this.ctx = this.canvas.getContext('2d');
    this.container.style.position = 'relative';
    this.container.appendChild(this.canvas);
    // B5 minimap
    this.minimap = document.createElement('canvas');
    this.minimap.width=90; this.minimap.height=90;
    this.minimap.style.cssText='position:absolute;right:8px;bottom:8px;width:90px;height:90px;border:1px solid var(--rule);border-radius:6px;background:rgba(11,18,25,.92);display:none;';
    this.minimapCtx=this.minimap.getContext('2d');
    this.container.appendChild(this.minimap);
    this.selectionDetail = document.createElement('div');
    this.selectionDetail.className = 'network-selection-detail';
    this.selectionDetail.hidden = true;
    this.selectionDetail.setAttribute('aria-live', 'polite');
    this.container.appendChild(this.selectionDetail);

    this.nodes = new Map();
    this.edges = [];
    this.minPct = 0;
    this.filters = {
      DIRECT_OWNERSHIP: true,
      ROLE: true,
      CONTROL: true
    };
    try {
      const p = new URLSearchParams(window.location.search);
      const e = p.get('edge');
      if (e) {
        const active = new Set(e.split(',').map(s=>s.trim()).filter(Boolean));
        Object.keys(this.filters).forEach(k => this.filters[k] = active.has(k));
      } else {
        const saved = localStorage.getItem('heimdal_graph_filter');
        if (saved) {
          const active = new Set(saved.split(',').map(s=>s.trim()).filter(Boolean));
          Object.keys(this.filters).forEach(k => this.filters[k] = active.has(k));
        }
      }
    } catch(_e){}

    this.zoom = 1;
    this.searchTerm = null;
    this._pulse = 0;
    this.panX = 0;
    this.panY = 0;
    this.isDragging = false;
    this.draggedNode = null;
    this.lastMouse = { x: 0, y: 0 };
    this.hoveredNode = null;
    this.selectedNode = null;
    // Magnetisk kraft / afstand — 0=kompakt 100=luftigt, default 52 (luft uden spild)

    try {
      const savedMagnet = localStorage.getItem(`heimdal_graph_magnet_${this.entityId}`);
      this.magnet = savedMagnet !== null ? Math.max(0, Math.min(100, parseInt(savedMagnet,10))) : 52;
    } catch(_e){ this.magnet = 52; }
    // spor udvidelser for at kunne lukke igen (dblclick toggle)
    this._expanded = new Map(); // sourceId -> {nodeIds:Set, edgeIds:Set}
    this._initialIds = null;

    this.initResize();
    this.bindEvents();
    this.loadData(this.entityId);
  }

  initResize() {
    const updateSize = () => {
      const rect = this.container.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      this.width = rect.width || 800;
      this.height = Math.max(480, rect.height || 620);
      this.canvas.width = this.width * dpr;
      this.canvas.height = this.height * dpr;
      this.canvas.style.width = `${this.width}px`;
      this.canvas.style.height = `${this.height}px`;
      if (this.ctx.resetTransform) this.ctx.resetTransform();
      else this.ctx.setTransform(1,0,0,1,0,0);
      this.ctx.scale(dpr, dpr);
      if (!this.panX && !this.panY) {
        this.panX = this.width / 2;
        this.panY = this.height / 2;
      }
    };
    this._doResize = updateSize;
    updateSize();
    window.addEventListener('resize', updateSize);
    // pause when tab hidden / offscreen
    this._reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if ('IntersectionObserver' in window) {
      this._visible = false;
      const obs = new IntersectionObserver((entries) => {
        entries.forEach(e => {
          this._visible = e.isIntersecting && e.intersectionRatio > 0;
          if (this._visible && this.nodes.size && !this.animating) this.startSimulation();
        });
      }, { threshold: 0.01 });
      obs.observe(this.container);
    } else {
      this._visible = true;
    }
  }

  async loadData(entityId, sourceId=null) {
    // T4.3 (F-10): 200-rig → LOADED; 200-tom → EMPTY; 4xx/5xx/netværksfejl →
    // synlig ERROR (aldrig silent return / blank canvas)
    try {
      this.shell.setState('LOADING');
      const res = await fetch(`/api/network/${entityId}?min_pct=${this.minPct}`);
      if (!res.ok) {
        console.error('Failed to load network graph: HTTP', res.status);
        this.shell.setState('ERROR');
        return;
      }
      const data = await res.json();
      const nodeCount = (data && data.nodes ? data.nodes.length : 0);
      this.mergeGraphData(data, sourceId);
      this.shell.setState(nodeCount ? 'LOADED' : 'EMPTY');
    } catch (err) {
      console.error('Failed to load network graph:', err);
      this.shell.setState('ERROR');
    }
  }

  mergeGraphData(data, sourceId=null) {
    // Merge — nye noder lægges formfast: omkring kilden ved udfold, ellers spredt om centrum
    const spread = Math.min(this.width||900, this.height||680) * 0.36;
    const newNodeIds = new Set();
    const newEdgeIds = new Set();
    const src = sourceId !== null ? this.nodes.get(sourceId) : null;
    const srcX = src ? src.x : 0, srcY = src ? src.y : 0;
    const pending = data.nodes.filter(n=> !this.nodes.has(n.id) && !n.is_center);
    // retning væk fra eksisterende klynge: centroid → kilde
    let outward = null;
    if(src){
      let cx=0, cy=0, cnt=0;
      this.nodes.forEach(v=>{ cx+=v.x; cy+=v.y; cnt++; });
      if(cnt){ cx/=cnt; cy/=cnt; }
      const dx = srcX - cx, dy = srcY - cy;
      const len = Math.hypot(dx, dy) || 1;
      outward = Math.atan2(dy, dx);
      // hvis kilden ligger næsten i centroid (центр), brug vinkel væk fra centrum 0,0
      if(len < 30) outward = Math.atan2(srcY, srcX) || (Math.random()*Math.PI*2);
    }
    let ringIdx = 0;
    const cone = Math.PI * 0.62; // ~112° vifte væk fra klyngen
    const step = pending.length>1 ? cone / (pending.length-1) : 0;
    const magnetDist = 110 + (this.magnet||70)*1.9;
    data.nodes.forEach(n => {
      if (!this.nodes.has(n.id)) {
        let angle, dist;
        if(n.is_center){ angle=0; dist=0; }
        else if(src){
          if(pending.length===1){
            angle = outward + (Math.random()-0.5)*0.18;
          } else {
            angle = outward - cone/2 + ringIdx*step + (Math.random()-0.5)*0.22;
            ringIdx++;
          }
          dist = magnetDist * (0.95 + Math.random()*0.40);
          dist = Math.max(dist, 95);
        } else {
          angle = Math.random() * Math.PI * 2;
          dist = spread * (0.55 + Math.random()*0.45);
        }
        newNodeIds.add(n.id);
        this.nodes.set(n.id, {
          ...n,
          x: n.is_center ? 0 : (src ? srcX + Math.cos(angle)*dist : Math.cos(angle)*dist),
          y: n.is_center ? 0 : (src ? srcY + Math.sin(angle)*dist : Math.sin(angle)*dist),
          vx: src ? src.vx*0.2 : 0,
          vy: src ? src.vy*0.2 : 0,
          radius: n.is_center ? 24 : (n.kind === 'COMPANY' ? 18 : 16)
        });
      }
    });

    // Merge edges
    data.edges.forEach(e => {
      if (!this.edges.some(existing => existing.id === e.id)) {
        newEdgeIds.add(e.id);
        this.edges.push(e);
      }
    });

    if(sourceId!==null && (newNodeIds.size||newEdgeIds.size)){
      this._expanded.set(sourceId, {nodeIds:newNodeIds, edgeIds:newEdgeIds});
    }
    // gem initial snapshot ved første load
    if(!this._initialIds && this.nodes.size){
      this._initialIds = {nodes:new Set(this.nodes.keys()), edges:new Set(this.edges.map(e=>e.id))};
    }
    this.startSimulation();
    // First paint uses the complete useful graph, rather than a clipped 1:1 view.
    if (sourceId === null && this._initialIds && this._initialIds.nodes.size === this.nodes.size) this.fitNetwork();
    this.updateLedger();
    this.updateHistogram();
  }

  updateHistogram(){
    const hist = document.getElementById(`graph-histogram-${this.entityId}`);
    if(!hist || !this.edges.length) return;
    const counts=[0,0,0];
    this.edges.forEach(e=>{
      const p=e.parsed_percentage;
      if(p===null || p===undefined) counts[0]++;
      else if(p<25) counts[0]++;
      else if(p<50) counts[1]++;
      else counts[2]++;
    });
    const max=Math.max(1,...counts);
    const buckets=hist.querySelectorAll('.hist-bar');
    counts.forEach((c,i)=>{
      const bar=buckets[i];
      if(!bar) return;
      const h = 4 + (c/max)*14;
      bar.style.height = h+'px';
      bar.style.background = c ? (i===0?'rgba(148,163,184,.85)': i===1?'rgba(56,189,248,.85)':'rgba(34,197,94,.85)') : 'rgba(148,163,184,.25)';
      bar.title = `${['0-25%','25-50%','≥50%'][i]}: ${c} kanter`;
      // dim buckets below threshold
      const thr=this.minPct;
      const active = (i===0 && thr<=25) || (i===1 && thr<=50) || (i===2);
      bar.style.opacity = active ? '1' : '0.35';
    });
  }

  updateLedger() {
    const ledger = document.getElementById(`network-ledger-${this.entityId}`);
    if (!ledger) return;
    const tbody = ledger.querySelector('tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    if (!this.edges.length) { ledger.style.display = 'none'; return; }
    const typeLabel = { 'DIRECT_OWNERSHIP': 'Ejerskab', 'ROLE': 'Rolle', 'CONTROL': 'Kontrol', 'REGISTERED_CONTROL_ACTOR': 'Kontrol' };
    const DANISH_ROLE_LABELS = {"Nevnd":"Bestyrelse","Nevndarformaður":"Bestyrelsesformand","Nevndarforkvinna":"Bestyrelsesformand","Nevndarlimur":"Bestyrelsesmedlem","Stjórn":"Ledelse","Stjóri":"Direktør","Forstjóri":"Direktør","Veruligur Eigari":"Reel ejer","Veruligur Eigari fyri":"Reel ejer","Hevur Veruligan Eigara":"Reel ejer"};
    this.edges.forEach(e => {
      const src = this.nodes.get(e.source) || { name: e.source, kind: '?' };
      const tgt = this.nodes.get(e.target) || { name: e.target, kind: '?' };
      const dispLabel = (e.type==='ROLE' || (e.label && e.label.includes('Veruligur'))) ? (DANISH_ROLE_LABELS[e.label] || e.label) : e.label;
      // G-02: registry text goes in with textContent, never innerHTML
      const tr = document.createElement('tr');
      const cell = (text, kind) => {
        const td = document.createElement('td');
        const el = kind === undefined ? td : td.appendChild(document.createElement('span'));
        if (kind !== undefined) el.className = `k-${String(kind || '').toLowerCase()}`;
        el.textContent = text;
        tr.appendChild(td);
      };
      cell(src.name, src.kind);
      cell(`${typeLabel[e.type] || e.type} · ${dispLabel || ''}`);
      cell(tgt.name, tgt.kind);
      tbody.appendChild(tr);
    });
    ledger.style.display = 'block';
  }

  startSimulation() {
    if (this.animating) return;
    this.animating = true;
    this.iterations = 0;
    this.tick();
  }

  tick() {
    if (this._reduceMotion) {
      this.render();
      this.animating = false;
      return;
    }
    if (this._visible === false && !this.isDragging) {
      this.animating = false;
      return;
    }
    this.updatePhysics();
    this.render();

    this.iterations++;
    if (this.iterations < 300 || this.isDragging) {
      requestAnimationFrame(() => this.tick());
    } else {
      this.animating = false;
    }
  }

  setMagnet(val){
    this.magnet = Math.max(0, Math.min(100, parseInt(val,10)||0));
    try{ localStorage.setItem(`heimdal_graph_magnet_${this.entityId}`, String(this.magnet)); }catch(_e){}
    const v = document.getElementById(`graph-magnet-val-${this.entityId}`);
    if(v) v.textContent = this.magnet + '%';
    this.startSimulation();
  }

  resetGraph(){
    if(!this._initialIds) return;
    // behold kun initial noder + kanter
    const keepNodes = new Set(this._initialIds.nodes);
    const keepEdges = new Set(this._initialIds.edges);
    for(const id of Array.from(this.nodes.keys())) if(!keepNodes.has(id)) this.nodes.delete(id);
    this.edges = this.edges.filter(e=> keepEdges.has(e.id));
    this._expanded.clear();
    this.startSimulation();
  }

  _collapseNode(sourceId){
    const rec = this._expanded.get(sourceId);
    if(!rec) return false;
    // fjern kun noder der er blade og kun forbundet via denne udvidelse
    for(const nid of rec.nodeIds){
      // behold hvis noden har andre kanter udenfor rec (dvs. forbundet til flere kilder)
      const stillLinked = this.edges.some(e=> (e.source===nid || e.target===nid) && !rec.edgeIds.has(e.id));
      if(!stillLinked) this.nodes.delete(nid);
    }
    this.edges = this.edges.filter(e=> !rec.edgeIds.has(e.id));
    this._expanded.delete(sourceId);
    this.startSimulation();
    return true;
  }

  updatePhysics() {
    const nodesArr = Array.from(this.nodes.values());
    // Magnet 0..100 → repel 2500..9000 (alle noder repeller hinanden), fjederlængde 110..300
    const kRepel = 3200 + this.magnet * 72;
    const kSpring = 0.035;
    const centerAttract = 0.006 - this.magnet * 0.00002; // 0.006→0.004
    const targetDist = 140 + this.magnet * 1.8;
    const damping = 0.85;

    // Repulsion between all node pairs
    for (let i = 0; i < nodesArr.length; i++) {
      const n1 = nodesArr[i];
      for (let j = i + 1; j < nodesArr.length; j++) {
        const n2 = nodesArr[j];
        let dx = n2.x - n1.x;
        let dy = n2.y - n1.y;
        let distSq = dx * dx + dy * dy || 1;
        let dist = Math.sqrt(distSq);
        if (dist < 520) {
          let f = kRepel / distSq;
          let fx = (dx / dist) * f;
          let fy = (dy / dist) * f;
          if (!n1.fixed && n1 !== this.draggedNode) { n1.vx -= fx; n1.vy -= fy; }
          if (!n2.fixed && n2 !== this.draggedNode) { n2.vx += fx; n2.vy += fy; }
        }
      }
    }

    // Spring forces along active edges
    this.edges.forEach(edge => {
      if (!this.filters[edge.type]) return;
      if (edge.parsed_percentage !== null && edge.parsed_percentage < this.minPct) return;

      const src = this.nodes.get(edge.source);
      const tgt = this.nodes.get(edge.target);
      if (!src || !tgt) return;

      let dx = tgt.x - src.x;
      let dy = tgt.y - src.y;
      let dist = Math.sqrt(dx * dx + dy * dy) || 1;
      let f = (dist - targetDist) * kSpring;
      let fx = (dx / dist) * f;
      let fy = (dy / dist) * f;

      if (!src.fixed && src !== this.draggedNode) { src.vx += fx; src.vy += fy; }
      if (!tgt.fixed && tgt !== this.draggedNode) { tgt.vx -= fx; tgt.vy -= fy; }
    });

    // Center attraction and movement damping
    nodesArr.forEach(n => {
      if (n.is_center) {
        n.x = 0;
        n.y = 0;
        return;
      }
      n.vx -= n.x * centerAttract;
      n.vy -= n.y * centerAttract;
      n.vx *= damping;
      n.vy *= damping;
      if (n !== this.draggedNode) {
        n.x += n.vx;
        n.y += n.vy;
      }
    });
  }

  render() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.width, this.height);

    ctx.save();
    ctx.translate(this.panX, this.panY);
    ctx.scale(this.zoom, this.zoom);

    // Neighbourhood fade — hovered node keeps incident edges at 100%, rest at 15%
    const hoveredId = this.hoveredNode ? this.hoveredNode.id : null;
    const isIncidentEdge = (e) => hoveredId && (e.source === hoveredId || e.target === hoveredId);
    const shouldFadeEdge = (e) => hoveredId && !isIncidentEdge(e);
    const neighborSet = new Set();
    if (hoveredId) {
      neighborSet.add(hoveredId);
      this.edges.forEach(e => {
        if (e.source === hoveredId) neighborSet.add(e.target);
        if (e.target === hoveredId) neighborSet.add(e.source);
      });
    }
    this.edges.forEach(edge => {
      if (!this.filters[edge.type]) return;
      if (edge.parsed_percentage !== null && edge.parsed_percentage < this.minPct) return;

      const src = this.nodes.get(edge.source);
      const tgt = this.nodes.get(edge.target);
      if (!src || !tgt) return;

      const faded = shouldFadeEdge(edge);
      ctx.globalAlpha = faded ? 0.15 : 1;
      ctx.beginPath();
      ctx.moveTo(src.x, src.y);
      ctx.lineTo(tgt.x, tgt.y);

      if (edge.type === 'DIRECT_OWNERSHIP') {
        ctx.strokeStyle = 'rgba(56, 189, 248, 0.75)';
        ctx.lineWidth = Math.max(1.5, Math.min(5, (edge.parsed_percentage || 25) / 20));
        ctx.setLineDash([]);
      } else if (edge.type === 'CONTROL') {
        ctx.strokeStyle = 'rgba(245, 158, 11, 0.6)';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([7, 4]);
      } else {
        ctx.strokeStyle = 'rgba(244, 114, 182, 0.6)';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([1, 3.5]);
      }
      ctx.stroke();
      // retningspil for ejerskab/kontrol — lille trekant ved target (ejer → ejet)
      if((edge.type==='DIRECT_OWNERSHIP' || edge.type==='CONTROL') && !faded){
        const ang = Math.atan2(tgt.y - src.y, tgt.x - src.x);
        const r = (tgt.radius || 16) + 3;
        const ax = tgt.x - Math.cos(ang)*r;
        const ay = tgt.y - Math.sin(ang)*r;
        const len = 9, wid = 5;
        ctx.save();
        ctx.translate(ax, ay);
        ctx.rotate(ang);
        ctx.beginPath();
        ctx.moveTo(0,0);
        ctx.lineTo(-len, wid);
        ctx.lineTo(-len, -wid);
        ctx.closePath();
        ctx.fillStyle = edge.type==='DIRECT_OWNERSHIP' ? 'rgba(56,189,248,0.95)' : 'rgba(245,158,11,0.9)';
        ctx.fill();
        ctx.restore();
      }
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;

      // Draw Edge Label / Percentage Pill — product vocabulary mapping (visible label Danish)
      // Relation labels are secondary detail: reveal only for the selected/hovered neighbourhood.
      if (edge.label && this.selectedNode && (edge.source === this.selectedNode.id || edge.target === this.selectedNode.id)) {
        const midX = (src.x + tgt.x) / 2;
        const midY = (src.y + tgt.y) / 2;
        ctx.font = '10px monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const DANISH_ROLE_LABELS = {"Nevnd":"Bestyrelse","Nevndarformaður":"Bestyrelsesformand","Nevndarforkvinna":"Bestyrelsesformand","Nevndarlimur":"Bestyrelsesmedlem","Stjórn":"Ledelse","Stjóri":"Direktør","Forstjóri":"Direktør","Veruligur Eigari":"Reel ejer","Veruligur Eigari fyri":"Reel ejer","Hevur Veruligan Eigara":"Reel ejer"};
        const rawLabel = DANISH_ROLE_LABELS[edge.label] || edge.label;
        const dispLabel = rawLabel.length > 14 ? rawLabel.slice(0, 12) + '…' : rawLabel;
        ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
        ctx.beginPath();
        ctx.roundRect(midX - 22, midY - 8, 44, 16, 4);
        ctx.fill();
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.15)';
        ctx.lineWidth = 1;
        ctx.stroke();

        ctx.fillStyle = '#38bdf8';
        ctx.fillText(dispLabel, midX, midY);
      }
    });

    // Draw Nodes — dim non-neighbours when hovering
    this.nodes.forEach(node => {
      ctx.save();
      ctx.translate(node.x, node.y);
      if (hoveredId && !neighborSet.has(node.id)) ctx.globalAlpha = 0.15;

      // Search highlight ring
      if (this.searchTerm && node.name.toLowerCase().includes(this.searchTerm)) {
        ctx.beginPath();
        ctx.arc(0, 0, node.radius + 7, 0, Math.PI * 2);
        ctx.strokeStyle = '#f59e0b';
        ctx.lineWidth = 3;
        ctx.stroke();
      }
      // Pulse ring for center node
      if (node.is_center) {
        ctx.beginPath();
        ctx.arc(0, 0, node.radius + 6, 0, Math.PI * 2);
        ctx.strokeStyle = 'rgba(56, 189, 248, 0.4)';
        ctx.lineWidth = 2;
        ctx.stroke();
      }

      // Selected node halo makes the source of secondary detail unambiguous.
      if (node === this.selectedNode) {
        ctx.beginPath(); ctx.arc(0, 0, node.radius + 9, 0, Math.PI * 2);
        ctx.strokeStyle = '#f59e0b'; ctx.lineWidth = 3; ctx.stroke();
      }

      // Node Circle
      ctx.beginPath();
      ctx.arc(0, 0, node.radius, 0, Math.PI * 2);
      if (node.kind === 'PERSON') {
        ctx.fillStyle = '#f43f5e';
      } else if (node.kind === 'COMPANY') {
        ctx.fillStyle = '#0284c7';
      } else if (node.kind === 'ORGANIZATION') {
        ctx.fillStyle = '#f59e0b';
      } else {
        ctx.fillStyle = '#10b981';
      }
      ctx.fill();
      ctx.strokeStyle = node === this.hoveredNode ? '#ffffff' : 'rgba(255, 255, 255, 0.3)';
      ctx.lineWidth = node === this.hoveredNode ? 2.5 : 1.5;
      ctx.stroke();

      // Node Icon / Initial
      ctx.fillStyle = '#ffffff';
      ctx.font = `bold ${node.radius * 0.9}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const initial = node.kind === 'PERSON' ? 'P' : (node.kind === 'COMPANY' ? 'C' : 'O');
      ctx.fillText(initial, 0, 1);

      ctx.restore();
    });

    // Primary labels get deterministic collision-aware placement. Secondary relation
    // labels remain hidden until a node is selected/hovered.
    const occupied = [];
    const labelNodes = Array.from(this.nodes.values()).sort((a,b) =>
      (b.is_center - a.is_center) || ((b === this.selectedNode) - (a === this.selectedNode)));
    ctx.font = '11px sans-serif';
    labelNodes.forEach(node => {
      const text = node.name.length > 24 ? node.name.slice(0, 22) + '…' : node.name;
      const w = ctx.measureText(text).width + 8, h = 16, r = node.radius;
      const candidates = node.is_center
        ? [[0, -(r + 14)], [0, r + 14]]
        : [
            [0, r + 14],
            [0, -(r + 14)],
            [r + 18, 0],
            [-r - 18, 0],
            [r + 14, r + 12],
            [-r - 14, r + 12],
            [r + 14, -r - 12],
            [-r - 14, -r - 12]
          ];
      let chosen = null;
      for (const [dx,dy] of candidates) {
        const x = node.x + dx - w / 2, y = node.y + dy - h / 2;
        const hit = occupied.some(o => x < o.x + o.w + 2 && x + w + 2 > o.x && y < o.y + o.h + 2 && y + h + 2 > o.y);
        if (!hit) { chosen = {x,y,w,h,dx,dy}; break; }
      }
      if (!chosen) return; // never paint an overlapping primary label
      occupied.push(chosen);
      ctx.save();
      ctx.fillStyle = node.is_center ? '#ffffff' : 'rgba(255, 255, 255, 0.9)';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(text, node.x + chosen.dx, node.y + chosen.dy);
      ctx.restore();
    });

    this.updateSelectionDetail();
    ctx.restore();
    this.renderMinimap();
  }

  renderMinimap(){
    if(!this.minimap || !this.nodes.size) return;
    const show = this.nodes.size>8 || this.zoom!==1;
    this.minimap.style.display = show ? 'block' : 'none';
    if(!show) return;
    const mCtx=this.minimapCtx; mCtx.clearRect(0,0,90,90);
    let minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity;
    this.nodes.forEach(n=>{ minX=Math.min(minX,n.x); maxX=Math.max(maxX,n.x); minY=Math.min(minY,n.y); maxY=Math.max(maxY,n.y); });
    const pad=20; const bw=(maxX-minX)+pad*2||120, bh=(maxY-minY)+pad*2||120; const sc=Math.min(88/bw, 88/bh);
    mCtx.save(); mCtx.translate(45,45); mCtx.scale(sc,sc); mCtx.translate(-(minX+maxX)/2, -(minY+maxY)/2);
    this.edges.forEach(e=>{
      const s=this.nodes.get(e.source), t=this.nodes.get(e.target); if(!s||!t) return;
      mCtx.beginPath(); mCtx.moveTo(s.x,s.y); mCtx.lineTo(t.x,t.y); mCtx.strokeStyle='rgba(148,163,184,.6)'; mCtx.lineWidth=1/sc; mCtx.stroke();
    });
    this.nodes.forEach(n=>{
      mCtx.beginPath(); mCtx.arc(n.x,n.y,4/sc,0,Math.PI*2);
      mCtx.fillStyle=n.is_center?'#38bdf8': (n.kind==='PERSON'?'#f43f5e':'#0284c7'); mCtx.fill();
    });
    const viewW=this.width/this.zoom, viewH=this.height/this.zoom;
    const viewX= -this.panX/this.zoom, viewY= -this.panY/this.zoom;
    mCtx.strokeStyle='rgba(245,158,11,.9)'; mCtx.lineWidth=1/sc; mCtx.strokeRect(viewX-viewW/2, viewY-viewH/2, viewW, viewH);
    mCtx.restore();
  }

  updateSelectionDetail() {
    if (!this.selectionDetail) return;
    const node = this.selectedNode;
    if (!node) { this.selectionDetail.hidden = true; this.selectionDetail.textContent = ''; return; }
    const kind = node.kind === 'PERSON' ? 'Person' : node.kind === 'COMPANY' ? 'Virksomhed' : 'Organisation';
    const relations = this.edges.filter(e => e.source === node.id || e.target === node.id);
    this.selectionDetail.textContent = '';
    const title = document.createElement('strong'); title.textContent = node.name; this.selectionDetail.appendChild(title);
    const meta = document.createElement('span'); meta.textContent = `${kind} · ${relations.length} relation${relations.length === 1 ? '' : 'er'} · dobbeltklik for at folde ud`; this.selectionDetail.appendChild(meta);
    this.selectionDetail.hidden = false;
  }

  screenToWorld(sx, sy) {
    return {
      x: (sx - this.panX) / this.zoom,
      y: (sy - this.panY) / this.zoom
    };
  }

  findNodeAt(sx, sy) {
    const world = this.screenToWorld(sx, sy);
    for (const node of this.nodes.values()) {
      const dx = world.x - node.x;
      const dy = world.y - node.y;
      if (dx * dx + dy * dy <= node.radius * node.radius) {
        return node;
      }
    }
    return null;
  }

  bindEvents() {
    this.canvas.addEventListener('mousedown', e => {
      const rect = this.canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const node = this.findNodeAt(sx, sy);

      if (node) {
        this.draggedNode = node;
        this.startSimulation();
      } else {
        this.isDragging = true;
      }
      this.lastMouse = { x: e.clientX, y: e.clientY };
    });

    window.addEventListener('mousemove', e => {
      const rect = this.canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;

      if (this.draggedNode) {
        const world = this.screenToWorld(sx, sy);
        this.draggedNode.x = world.x;
        this.draggedNode.y = world.y;
        this.draggedNode.vx = 0;
        this.draggedNode.vy = 0;
        this.startSimulation();
      } else if (this.isDragging) {
        const dx = e.clientX - this.lastMouse.x;
        const dy = e.clientY - this.lastMouse.y;
        this.panX += dx;
        this.panY += dy;
        this.render();
      } else {
        const node = this.findNodeAt(sx, sy);
        if (node !== this.hoveredNode) {
          this.hoveredNode = node;
          this.canvas.style.cursor = node ? 'pointer' : 'default';
          this.render();
        }
      }
      this.lastMouse = { x: e.clientX, y: e.clientY };
    });

    window.addEventListener('mouseup', () => {
      this.draggedNode = null;
      this.isDragging = false;
    });

    this.canvas.addEventListener('wheel', e => {
      e.preventDefault();
      const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9;
      this.zoom = Math.max(0.3, Math.min(3.0, this.zoom * zoomFactor));
      this.render();
    });

    // Click selects a node and reveals secondary detail/labels.
    this.canvas.addEventListener('click', e => {
      const rect = this.canvas.getBoundingClientRect();
      const node = this.findNodeAt(e.clientX - rect.left, e.clientY - rect.top);
      this.selectedNode = node;
      this.hoveredNode = node;
      this.render();
    });

    // Double-click to expand/collapse (toggle)
    this.canvas.addEventListener('dblclick', e => {
      const rect = this.canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const node = this.findNodeAt(sx, sy);
      if (node && !node.is_center) {
        if(this._expanded.has(node.id)){
          this._collapseNode(node.id);
        } else {
          this.loadData(node.id, node.id);
        }
      }
    });

    // Keyboard: focus container → arrows pan, +/- zoom, Enter open, Esc clear
    this.container.addEventListener('keydown', e => {
      const step = 24;
      if (e.key === 'ArrowUp') { this.panY += step; this.render(); e.preventDefault(); }
      else if (e.key === 'ArrowDown') { this.panY -= step; this.render(); e.preventDefault(); }
      else if (e.key === 'ArrowLeft') { this.panX += step; this.render(); e.preventDefault(); }
      else if (e.key === 'ArrowRight') { this.panX -= step; this.render(); e.preventDefault(); }
      else if (e.key === '+' || e.key === '=' ) { this.zoom = Math.min(3, this.zoom*1.12); this.render(); e.preventDefault(); }
      else if (e.key === '-' || e.key === '_' ) { this.zoom = Math.max(0.3, this.zoom/1.12); this.render(); e.preventDefault(); }
      else if (e.key === '0') { this.panX = this.width/2; this.panY = this.height/2; this.zoom = 1; this.render(); e.preventDefault(); }
      else if (e.key === 'Enter' && this.hoveredNode) { window.location.href = this.hoveredNode.kind === 'PERSON' ? `/persons/${this.hoveredNode.id}` : `/companies/${this.hoveredNode.id}`; }
      else if (e.key === 'Escape') { this.hoveredNode = null; this.selectedNode = null; this.searchTerm = null; this.render(); }
    });
  }

  setThreshold(minPct) {
    this.minPct = minPct;
    this.updateHistogram();
    this.render();
  }

  toggleFilter(type, enabled) {
    this.filters[type] = enabled;
    try {
      const active = Object.entries(this.filters).filter(([,v])=>v).map(([k])=>k);
      localStorage.setItem('heimdal_graph_filter', active.join(','));
      const url = new URL(window.location);
      if (active.length===3) url.searchParams.delete('edge'); else url.searchParams.set('edge', active.join(','));
      history.replaceState(null,'', url);
    } catch(_e){}
    this.render();
  }

  search(term) {
    this.searchTerm = (term||'').trim().toLowerCase() || null;
    this.render();
    if (this.searchTerm) {
      for (const n of this.nodes.values()) {
        if (n.name.toLowerCase().includes(this.searchTerm)) {
          this.hoveredNode = n;
          break;
        }
      }
    }
  }

  exportPNG() {
    const link = document.createElement('a');
    link.download = `heimdal_netvaerk_${this.entityId}_${Date.now()}.png`;
    link.href = this.canvas.toDataURL('image/png');
    link.click();
  }

  fitNetwork(){
    if(!this.nodes.size) return;
    let minX=Infinity, maxX=-Infinity, minY=Infinity, maxY=-Infinity;
    this.nodes.forEach(n=>{
      minX=Math.min(minX,n.x - n.radius); maxX=Math.max(maxX,n.x + n.radius);
      minY=Math.min(minY,n.y - n.radius); maxY=Math.max(maxY,n.y + n.radius);
    });
    const pad=72; minX-=pad; maxX+=pad; minY-=pad; maxY+=pad;
    const bw=maxX-minX, bh=maxY-minY;
    // Leave a calm margin for labels and the compact overlay controls.
    const scale=Math.min(this.width/bw, this.height/bh, 1.25) * 0.92;
    this.zoom=Math.max(0.42, Math.min(1.55, scale));
    this.panX=this.width/2 - (minX+bw/2)*this.zoom;
    this.panY=this.height/2 - (minY+bh/2)*this.zoom;
    this.render();
  }
}

window.HeimdalNetworkGraph = HeimdalNetworkGraph;
window.fitNetwork = () => window.heimdalGraph && window.heimdalGraph.fitNetwork();
HeimdalNetworkGraph.prototype.toggleFullscreen = function(){
  const c=this.container;
  const isFs = c.classList.contains('is-fullscreen');
  // luk — uanset om native fullscreen også er aktiv
  if(isFs){
    c.classList.remove('is-fullscreen');
    document.body.style.overflow='';
    if(document.fullscreenElement) try{ document.exitFullscreen().catch(()=>{});}catch(_e){}
    // dobbelt resize: næste frame + efter transition
    requestAnimationFrame(()=>{ this._doResize && this._doResize(); this.fitNetwork(); });
    setTimeout(()=>{ this._doResize && this._doResize(); this.render(); }, 120);
    return;
  }
  // åbn: CSS fullscreen, ingen !important-canvas — lærredet skal tegnes i ny opløsning
  c.classList.add('is-fullscreen');
  document.body.style.overflow='hidden';
  // tving layout før resize, så getBoundingClientRect giver viewport-størrelse
  void c.offsetHeight;
  requestAnimationFrame(()=>{ this._doResize && this._doResize(); this.fitNetwork(); });
  setTimeout(()=>{ this._doResize && this._doResize(); this.render(); }, 120);
  const onKey=(e)=>{ if(e.key==='Escape'){ this.toggleFullscreen(); document.removeEventListener('keydown',onKey); }};
  document.addEventListener('keydown', onKey, {once:true});
  // hvis browser gik i native fullscreen, ryd når den forlades via Esc/browser-UI
  const onFsChange=()=>{ if(!document.fullscreenElement && c.classList.contains('is-fullscreen')){ c.classList.remove('is-fullscreen'); document.body.style.overflow=''; requestAnimationFrame(()=>{ this._doResize && this._doResize(); this.fitNetwork(); }); }};
  document.addEventListener('fullscreenchange', onFsChange, {once:true});
  // valgfrit native fullscreen — må ikke ødelægge CSS-fallback hvis den afvises
  try{ if(c.requestFullscreen) c.requestFullscreen().catch(()=>{}); }catch(_e){}
};
window.toggleHeimdalFullscreen = function(){
  const g = window.heimdalGraph;
  if(g && g.toggleFullscreen) { g.toggleFullscreen(); return; }
  const c=document.getElementById('network-graph-container');
  if(!c) return;
  const on = c.classList.toggle('is-fullscreen');
  document.body.style.overflow = on ? 'hidden' : '';
};

// Custom element <heimdal-graph entity="5708" kind="COMPANY"> — single engine
class HeimdalGraphElement extends HTMLElement {
  connectedCallback(){
    const eid = this.getAttribute('entity') || this.getAttribute('entity-id');
    const kind = this.getAttribute('kind') || 'COMPANY';
    if(!eid) return;
    if(this._initialized) return; this._initialized=true;
    const container = document.createElement('div');
    container.id = 'hg-'+Math.random().toString(36).slice(2,9);
    container.className='network-graph-container';
    container.style.minHeight='520px';
    container.setAttribute('tabindex','0');
    container.setAttribute('role','application');
    this.appendChild(container);
    // reuse same class, no extra fetch if already loaded elsewhere
    const g = new HeimdalNetworkGraph(container.id, parseInt(eid,10), kind);
    this._graph = g;
    if(!window.heimdalGraph) window.heimdalGraph=g;
  }
}
if(!customElements.get('heimdal-graph')) customElements.define('heimdal-graph', HeimdalGraphElement);

// Bootstrap for the interactive_network_graph macro (templates/macros.html, G-05):
// the container carries the entity, the toolbar controls carry data-graph-*.
document.addEventListener('DOMContentLoaded', () => {
  const c = document.querySelector('#network-graph-container[data-entity-id]');
  if (!c) return;
  const id = c.dataset.entityId;
  const g = window.heimdalGraph = new HeimdalNetworkGraph(c.id, parseInt(id, 10), c.dataset.entityKind);
  const magnet = document.getElementById(`graph-magnet-${id}`);
  const magnetVal = document.getElementById(`graph-magnet-val-${id}`);
  if (magnet && g.magnet != null) { magnet.value = g.magnet; if (magnetVal) magnetVal.textContent = g.magnet + '%'; }
  const section = c.closest('section') || document;
  section.addEventListener('click', (e) => {
    const b = e.target.closest('[data-graph-action]');
    if (!b) return;
    const a = b.dataset.graphAction;
    if (a === 'reset') g.resetGraph();
    else if (a === 'fullscreen') window.toggleHeimdalFullscreen();
    else if (a === 'png') g.exportPNG();
  });
  section.addEventListener('input', (e) => {
    const t = e.target, kind = t.dataset && t.dataset.graphInput;
    if (kind === 'threshold') {
      const v = document.getElementById(`graph-slider-val-${id}`);
      if (v) v.textContent = t.value + '%';
      g.setThreshold(parseFloat(t.value));
    } else if (kind === 'magnet') g.setMagnet(t.value);
    else if (kind === 'search') g.search(t.value);
  });
  section.addEventListener('change', (e) => {
    const t = e.target;
    if (t.dataset && t.dataset.graphFilter) g.toggleFilter(t.dataset.graphFilter, t.checked);
  });
});
