/**
 * Heimdal Portal Application JavaScript (Vanilla JS, 0 dependencies, Offline)
 * Provides:
 * - Theme Switcher (Dark / Light / System)
 * - Global Command Palette (Cmd+K / Ctrl+K)
 * - Quick Inspector Slide-over Drawer
 * - SVG Network & Tree Pan/Zoom + Interactive Path Highlighting
 * - One-Click Copy to Clipboard & Toast Feedback
 */

(function () {
  'use strict';

  /* ==========================================================================
     1. THEME SWITCHER
     ========================================================================== */
  const THEME_KEY = 'heimdal_theme';

  
  // Spine drawer toggle (mobile)
  function initSpineDrawer(){
    var btn = document.getElementById('spine-toggle-btn');
    var spine = document.getElementById('spine-nav');
    if(!btn || !spine) return;
    // Show button on small screens via JS (CSS also)
    function sync(){
      if(window.innerWidth <= 960){
        btn.style.display='grid';
        btn.setAttribute('aria-expanded', spine.classList.contains('open') ? 'true' : 'false');
      } else {
        btn.style.display='none';
        spine.classList.remove('open');
        document.body.classList.remove('spine-open');
      }
    }
    var lastFocus = null;
    function getFocusable(){ return Array.prototype.slice.call(spine.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])')); }
    function trapFocus(e){
      if(!spine.classList.contains('open') || e.key !== 'Tab') return;
      var focusable = getFocusable();
      if(!focusable.length) return;
      var first = focusable[0], last = focusable[focusable.length-1];
      if(e.shiftKey && document.activeElement === first){ e.preventDefault(); last.focus(); }
      else if(!e.shiftKey && document.activeElement === last){ e.preventDefault(); first.focus(); }
    }
    function closeDrawer(returnFocus){
      var wasOpen = spine.classList.contains('open');
      spine.classList.remove('open');
      document.body.classList.remove('spine-open');
      btn.setAttribute('aria-expanded','false');
      var bd=document.getElementById('spine-backdrop'); if(bd) bd.remove();
      spine.removeEventListener('keydown', trapFocus);
      if(returnFocus !== false && lastFocus && wasOpen){
        try{ lastFocus.focus(); }catch(e){}
      }
    }
    btn.addEventListener('click', function(){
      var isOpen = spine.classList.toggle('open');
      document.body.classList.toggle('spine-open', isOpen);
      btn.setAttribute('aria-expanded', String(isOpen));
      if(isOpen){
        lastFocus = document.activeElement;
        var bd = document.getElementById('spine-backdrop');
        if(!bd){
          bd = document.createElement('div');
          bd.id='spine-backdrop';
          bd.style.cssText='position:fixed; inset:0; background:rgba(11,18,25,.45); backdrop-filter:blur(4px); z-index:15;';
          document.body.appendChild(bd);
          bd.addEventListener('click', function(){ closeDrawer(true); });
        }
        // focus first link for keyboard users
        var focusable = getFocusable();
        if(focusable.length){ focusable[0].focus(); }
        spine.addEventListener('keydown', trapFocus);
      } else {
        var bd2 = document.getElementById('spine-backdrop');
        if(bd2) bd2.remove();
        spine.removeEventListener('keydown', trapFocus);
        if(lastFocus){ try{ lastFocus.focus(); }catch(e){} }
      }
    });
    window.addEventListener('resize', sync);
    sync();
    // close on Esc (global)
    document.addEventListener('keydown', function(e){ if(e.key==='Escape' && spine.classList.contains('open')){ closeDrawer(true); }});
  }

  function initTheme() {
    const savedTheme = localStorage.getItem(THEME_KEY) || 'dark';
    setTheme(savedTheme, false);

    const themeToggleBtn = document.getElementById('theme-toggle-btn');
    if (themeToggleBtn) {
      themeToggleBtn.addEventListener('click', () => {
        const current = document.documentElement.getAttribute('data-theme') || 'dark';
        const next = current === 'dark' ? 'light' : 'dark';
        setTheme(next, true);
      });
    }
  }

  function setTheme(theme, save) {
    document.documentElement.setAttribute('data-theme', theme);
    if (save) {
      localStorage.setItem(THEME_KEY, theme);
    }
    const themeLabel = document.getElementById('theme-toggle-label');
    if (themeLabel) {
      themeLabel.textContent = theme === 'dark' ? 'Mørkt tema' : 'Lyst tema';
    }
    const themeIcon = document.getElementById('theme-toggle-icon');
    if (themeIcon) {
      themeIcon.innerHTML = theme === 'dark'
        ? '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>'
        : '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>';
    }
  }

  /* ==========================================================================
     2. TOAST NOTIFICATIONS
     ========================================================================== */
  function showToast(message, opts){
    opts = opts||{};
    let wrap = document.getElementById('heimdal-toast-wrap');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.id = 'heimdal-toast-wrap';
      wrap.style.cssText='position:fixed;bottom:1rem;right:1rem;z-index:9999;display:flex;flex-direction:column;gap:.5rem;pointer-events:none;';
      document.body.appendChild(wrap);
    }
    let toast = document.createElement('div');
    toast.className = 'toast-notification show';
    toast.setAttribute('role','status');
    toast.style.cssText='pointer-events:auto; display:flex; align-items:center; gap:.6rem; padding:.7rem .9rem; background:var(--surface-elevated); border:1px solid var(--rule); border-radius:var(--r-md); box-shadow:var(--shadow-md); color:var(--text); font-size:.88rem; max-width:360px;';
    var icon = opts.type==='error' ? '<span style="color:var(--danger)">✕</span>' : opts.type==='success' ? '<span style="color:var(--success)">✓</span>' : '<span>●</span>';
    toast.innerHTML = icon + '<span style="flex:1;">' + escapeHtml(message) + '</span>' + (opts.action ? '<button style="margin-left:.4rem; padding:.2rem .5rem; border-radius:999px; border:1px solid var(--rule); background:var(--surface); cursor:pointer; font-size:.8rem;">'+escapeHtml(opts.action.label)+'</button>' : '');
    if(opts.action){
      toast.querySelector('button').addEventListener('click', function(){ opts.action.onClick(); toast.remove(); });
    }
    wrap.appendChild(toast);
    var tmr = setTimeout(function(){ toast.classList.remove('show'); setTimeout(function(){ toast.remove(); }, 300); }, opts.duration||2200);
    toast.addEventListener('mouseenter', function(){ clearTimeout(tmr); });
    toast.addEventListener('mouseleave', function(){ tmr = setTimeout(function(){ toast.remove(); }, 800); });
    return toast;
  }

  /* ==========================================================================
     3. ONE-CLICK COPY
     ========================================================================== */
  
  // Optimistic watchlist toggle for [data-watch-id]
  function initWatchButtons(){
    document.addEventListener('click', async function(e){
      var t = e.target; if(!t || typeof t.closest !== 'function') return;
      var btn = t.closest('.btn-watchlist[data-watch-id]');
      if(!btn) return;
      e.preventDefault();
      var id = btn.getAttribute('data-watch-id');
      var kind = btn.getAttribute('data-watch-kind');
      var wasActive = btn.classList.contains('active') || btn.getAttribute('aria-pressed')==='true';
      btn.classList.toggle('active');
      btn.setAttribute('aria-pressed', String(!wasActive));
      var origHtml = btn.innerHTML;
      btn.disabled = true;
      try{
        var res = await fetch('/api/watchlist/toggle', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({canonical_entity_id: parseInt(id), entity_kind: kind})});
        if(res.ok){
          var added = !wasActive;
          showToast(added ? 'Tilføjet til overvågning' : 'Fjernet fra overvågning', {type:'success', action:{label:'Fortryd', onClick:function(){ fetch('/api/watchlist/toggle',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({canonical_entity_id: parseInt(id), entity_kind: kind})}); btn.classList.toggle('active'); }}});
          // update badge elsewhere if needed
          btn.innerHTML = added ? '<svg width="14" height="14" aria-hidden="true"><use href="/static/icons/lucide.svg#lucide-star"></use></svg> Overvåget' : origHtml;
        } else {
          btn.classList.toggle('active');
          showToast('Kunne ikke opdatere overvågning', {type:'error'});
          btn.innerHTML = origHtml;
        }
      }catch(err){
        btn.classList.toggle('active');
        showToast('Fejl ved overvågning', {type:'error'});
        btn.innerHTML = origHtml;
      } finally { btn.disabled=false; }
    });
  }

  // Overvågning "Fjern" ([data-unwatch-id], templates/watchlist.html): one click
  // is exactly one POST (T1.3), then the row fades out.
  function initUnwatchButtons(){
    document.addEventListener('click', async function(e){
      var btn = e.target.closest && e.target.closest('[data-unwatch-id]');
      if(!btn) return;
      var row = btn.closest('tr');
      if(row){ row.style.transition='opacity 200ms ease'; row.style.opacity='.45'; }
      try {
        var res = await fetch('/api/watchlist/toggle', {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({canonical_entity_id: parseInt(btn.dataset.unwatchId, 10), entity_kind: btn.dataset.unwatchKind})});
        if(res.ok){
          if(row){ row.style.opacity='0'; setTimeout(function(){ row.remove(); if(!document.querySelector('.ledger tbody tr')) location.reload(); }, 200); }
          showToast('Fjernet fra overvågning', {type:'success'});
        } else {
          if(row) row.style.opacity='1';
          showToast('Kunne ikke fjerne', {type:'error'});
        }
      } catch(err){
        if(row) row.style.opacity='1';
        showToast('Fejl ved fjernelse', {type:'error'});
      }
    });
  }

  // [data-action="print"] buttons (dossiers, bulk screening): no inline onclick (G-05).
  function initPrintButtons(){
    document.addEventListener('click', function(e){
      if(e.target.closest && e.target.closest('[data-action="print"]')) window.print();
    });
  }

  // Static SVG network legend (templates/network.html): toggle an edge kind,
  // remember it in ?edge= and localStorage, restore it on load.
  function initSvgLegendFilter(){
    var buttons = document.querySelectorAll('.svg-legend button[data-filter]');
    if(!buttons.length) return;
    function toggle(btn){
      var kind = btn.dataset.filter;
      var pressed = btn.getAttribute('aria-pressed') !== 'false';
      btn.setAttribute('aria-pressed', pressed ? 'false' : 'true');
      var cls = kind === 'OWNERSHIP' ? 'edge-ownership' : kind === 'CONTROL' ? 'edge-control' : 'edge-role';
      document.querySelectorAll('.network-svg line.edge').forEach(function(el){
        if(el.classList.contains(cls)) el.classList.toggle('filtered-out', pressed);
      });
      try {
        var url = new URL(window.location);
        var cur = (url.searchParams.get('edge')||'OWNERSHIP,CONTROL,ROLE').split(',').filter(Boolean);
        var next = pressed ? cur.filter(function(k){ return k!==kind; }) : Array.from(new Set(cur.concat([kind])));
        if(next.length===3) url.searchParams.delete('edge'); else url.searchParams.set('edge', next.join(','));
        history.replaceState(null,'', url);
        localStorage.setItem('heimdal_svg_filter', next.join(','));
      } catch(err){}
    }
    buttons.forEach(function(b){ b.addEventListener('click', function(){ toggle(b); }); });
    try {
      var saved = localStorage.getItem('heimdal_svg_filter');
      var param = new URLSearchParams(window.location.search).get('edge');
      var active = (param||saved||'OWNERSHIP,CONTROL,ROLE').split(',').filter(Boolean);
      buttons.forEach(function(b){ if(active.indexOf(b.dataset.filter) < 0) toggle(b); });
    } catch(err){}
  }

  function initCopyButtons() {
    document.addEventListener('click', (e) => {
      const t = e.target; if(!t || typeof t.closest !== 'function') return;
      const btn = t.closest('.btn-copy');
      if (!btn) return;
      const textToCopy = btn.getAttribute('data-copy') || btn.textContent.trim();
      if (!textToCopy) return;

      navigator.clipboard.writeText(textToCopy).then(() => {
        showToast(`Kopieret: "${textToCopy}"`);
      }).catch(() => {
        showToast('Kunne ikke kopiere');
      });
    });
  }

  /* ==========================================================================
     4. COMMAND PALETTE (Cmd+K / Ctrl+K)
     ========================================================================== */
  let cmdModal = null;
  let cmdInput = null;
  let cmdResults = null;
  let selectedIndex = -1;
  let searchDebounceTimer = null;

  function initCommandPalette() {
    cmdModal = document.getElementById('cmd-palette-modal');
    if (!cmdModal) return;

    cmdInput = document.getElementById('cmd-palette-input');
    cmdResults = document.getElementById('cmd-palette-results');

    // Trigger button in topbar
    const triggerBtn = document.getElementById('cmd-palette-trigger');
    if (triggerBtn) {
      triggerBtn.addEventListener('click', openCommandPalette);
    }

    // Global shortcut Cmd+K / Ctrl+K
    window.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (cmdModal.classList.contains('open')) {
          closeCommandPalette();
        } else {
          openCommandPalette();
        }
      } else if (e.key === 'Escape' && cmdModal.classList.contains('open')) {
        closeCommandPalette();
      }
    });

    // Close on backdrop click
    cmdModal.addEventListener('click', (e) => {
      if (e.target === cmdModal) {
        closeCommandPalette();
      }
    });

    // Input typing listener
    if (cmdInput) {
      cmdInput.addEventListener('input', () => {
        clearTimeout(searchDebounceTimer);
        const q = cmdInput.value.trim();
        if (!q) {
          renderDefaultCommands();
          return;
        }
        searchDebounceTimer = setTimeout(() => {
          performSearch(q);
        }, 160);
      });

      cmdInput.addEventListener('keydown', handleCommandPaletteKeys);
    }
  }

  function openCommandPalette() {
    if (!cmdModal) return;
    cmdModal.classList.add('open');
    cmdModal.setAttribute('aria-hidden', 'false');
    if (cmdInput) {
      cmdInput.value = '';
      cmdInput.focus();
    }
    renderDefaultCommands();
  }

  function closeCommandPalette() {
    if (!cmdModal) return;
    cmdModal.classList.remove('open');
    cmdModal.setAttribute('aria-hidden', 'true');
    selectedIndex = -1;
  }

  // T2.2 (plan v1/F-21): the dashboard hero promises "selskab, person og
  // registreringsnummer" but posted to company-only /companies. Reuse the
  // palette's existing /api/search handling (both kinds) — no new search
  // service, no misleading navigation.
  function initHeroSearch() {
    var heroForm = document.querySelector('form[role="search"][aria-label="Søg i registeret"]');
    if (!heroForm || heroForm.__heroBound) return;
    heroForm.__heroBound = true;
    heroForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var input = heroForm.querySelector('input[name="q"]');
      var q = input ? input.value.trim() : '';
      if (!q) return;
      openCommandPalette();
      if (cmdInput) {
        cmdInput.value = q;
        performSearch(q);
      }
    });
  }

  function renderDefaultCommands() {
    selectedIndex = -1;
    if (!cmdResults) return;
    var _icon = function(id){ return '<svg width="16" height="16" aria-hidden="true"><use href="/static/icons/lucide.svg#'+id+'"></use></svg>'; };
    // Recent (localStorage) when query empty
    var recentHtml = '';
    try {
      var recent = JSON.parse(localStorage.getItem('heimdal_recent')||'[]');
      if (recent && recent.length) {
        recentHtml = '<div class="cmd-group-title">Senest besøgte</div>';
        recent.slice(0,5).forEach(function(r, i){
          recentHtml += '<a href="'+escapeHtml(r.url)+'" class="cmd-item" data-index="'+(5+i)+'"><span class="cmd-icon">'+_icon('lucide-clock')+'</span><span class="cmd-label">'+escapeHtml(r.name)+'</span><span class="cmd-hint">'+escapeHtml(r.meta||'')+'</span></a>';
        });
      }
    } catch(e){}
    cmdResults.innerHTML = `
      <div class="cmd-group-title">Hurtige genveje</div>
      <a href="/" class="cmd-item" data-index="0">
        <span class="cmd-icon">`+_icon('lucide-layout-dashboard')+`</span>
        <span class="cmd-label">Gå til Dashboard</span>
        <span class="cmd-hint">Startside</span>
      </a>
      <a href="/companies" class="cmd-item" data-index="1">
        <span class="cmd-icon">`+_icon('lucide-building-2')+`</span>
        <span class="cmd-label">Søg i virksomheder</span>
        <span class="cmd-hint">Liste</span>
      </a>
      <a href="/persons" class="cmd-item" data-index="2">
        <span class="cmd-icon">`+_icon('lucide-users')+`</span>
        <span class="cmd-label">Søg i personer</span>
        <span class="cmd-hint">Liste</span>
      </a>
      <a href="/persons/relationer" class="cmd-item" data-index="3">
        <span class="cmd-icon">`+_icon('lucide-network')+`</span>
        <span class="cmd-label">Person-relationer</span>
        <span class="cmd-hint">Netværk</span>
      </a>
      <a href="/find-forbindelse" class="cmd-item" data-index="4">
        <span class="cmd-icon">`+_icon('lucide-route')+`</span>
        <span class="cmd-label">Find forbindelse</span>
        <span class="cmd-hint">Pathfinder</span>
      </a>
      `+recentHtml+`
    `;
    highlightItem(0);
  }

  function performSearch(query) {
    if (!cmdResults) return;
    cmdResults.innerHTML = '<div class="cmd-loading">Søger i virksomheder og personer...</div>';

    fetch(`/api/search?q=${encodeURIComponent(query)}`)
      .then(res => res.json())
      .then(data => {
        if (!data.results || data.results.length === 0) {
          cmdResults.innerHTML = `<div class="cmd-empty">Ingen resultater for <strong>"${escapeHtml(query)}"</strong></div>`;
          selectedIndex = -1;
          return;
        }

        let html = '<div class="cmd-group-title">Søgeresultater</div>';
        data.results.forEach((item, idx) => {
          const kindIcon = item.kind === 'PERSON' ? '●' : '■';
          const kindCls = item.kind === 'PERSON' ? 'k-person' : 'k-company';
          const kindName = item.kind === 'PERSON' ? 'Person' : (item.kind === 'COMPANY' ? 'Virksomhed' : item.kind);
          const metaInfo = item.regnr ? `Reg.nr. ${item.regnr}` : (item.status ? item.status : `ID ${item.id}`);

          html += `
            <a href="${escapeHtml(item.url)}" class="cmd-item" data-index="${idx}">
              <span class="cmd-glyph ${kindCls}">${kindIcon}</span>
              <div class="cmd-details">
                <span class="cmd-label">${escapeHtml(item.name)}</span>
                <span class="cmd-meta">${escapeHtml(kindName)} · <span class="mono">${escapeHtml(metaInfo)}</span></span>
              </div>
              <span class="cmd-action-badge">Åbn ↵</span>
            </a>
          `;
        });
        cmdResults.innerHTML = html;
        highlightItem(0);
      })
      .catch(() => {
        cmdResults.innerHTML = '<div class="cmd-empty">Der opstod en fejl under søgningen.</div>';
      });
  }

  function handleCommandPaletteKeys(e) {
    const items = cmdResults ? cmdResults.querySelectorAll('.cmd-item') : [];
    if (!items.length) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      selectedIndex = (selectedIndex + 1) % items.length;
      highlightItem(selectedIndex);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      selectedIndex = (selectedIndex - 1 + items.length) % items.length;
      highlightItem(selectedIndex);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (selectedIndex >= 0 && items[selectedIndex]) {
        items[selectedIndex].click();
      }
    }
  }

  function highlightItem(index) {
    if (!cmdResults) return;
    const items = cmdResults.querySelectorAll('.cmd-item');
    items.forEach((item, i) => {
      if (i === index) {
        item.classList.add('active');
        item.scrollIntoView({ block: 'nearest' });
      } else {
        item.classList.remove('active');
      }
    });
    selectedIndex = index;
  }

  /* ==========================================================================
     5. QUICK INSPECTOR DRAWER
     ========================================================================== */
  let drawerBackdrop = null;
  let drawerElem = null;

  function initInspectorDrawer() {
    drawerBackdrop = document.getElementById('inspector-backdrop');
    drawerElem = document.getElementById('inspector-drawer');
    if (!drawerElem) return;

    // Close button & backdrop
    const closeBtn = document.getElementById('inspector-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', closeInspector);
    if (drawerBackdrop) drawerBackdrop.addEventListener('click', closeInspector);

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && drawerElem.classList.contains('open')) {
        closeInspector();
      }
    });

    // Inspect buttons click listener
    document.addEventListener('click', (e) => {
      const t = e.target; if(!t || typeof t.closest !== 'function') return;
      const trigger = t.closest('[data-inspect-id]');
      if (!trigger) return;
      e.preventDefault();
      e.stopPropagation();
      const entityId = trigger.getAttribute('data-inspect-id');
      if (entityId) {
        openInspector(entityId);
      }
    });
  }

  function openInspector(entityId) {
    if (!drawerElem) return;
    drawerElem.classList.add('open');
    if (drawerBackdrop) drawerBackdrop.classList.add('open');

    const titleEl = document.getElementById('inspector-title');
    const kindEl = document.getElementById('inspector-kind');
    const contentEl = document.getElementById('inspector-content');

    if (titleEl) titleEl.textContent = 'Indlæser detaljer...';
    if (contentEl) contentEl.innerHTML = '<div class="drawer-loading">Henter profildata...</div>';

    fetch(`/api/inspect/${entityId}`)
      .then(res => res.json())
      .then(data => {
        if (data.error) {
          if (contentEl) contentEl.innerHTML = '<div class="drawer-error">Enheden blev ikke fundet.</div>';
          return;
        }
        if (titleEl) titleEl.textContent = data.name;
        if (kindEl) {
          kindEl.textContent = data.kind === 'PERSON' ? 'Person' : (data.kind === 'COMPANY' ? 'Virksomhed' : data.kind);
          kindEl.className = `kind-label k-${data.kind.toLowerCase()}`;
        }

        let bodyHtml = `
          <div class="drawer-meta-grid">
            <div>
              <span class="drawer-meta-label">ID</span>
              <span class="mono">${escapeHtml(data.id)}</span>
            </div>
            ${data.regnr ? `
            <div>
              <span class="drawer-meta-label">Reg.nr.</span>
              <span class="mono">${escapeHtml(data.regnr)}</span>
            </div>` : ''}
            ${data.inventory_status ? `
            <div>
              <span class="drawer-meta-label">Status</span>
              <span class="mono">${escapeHtml(data.inventory_status)}</span>
            </div>` : ''}
          </div>
        `;

        if (data.kind === 'PERSON') {
          if (data.direct_holdings && data.direct_holdings.length > 0) {
            bodyHtml += `
              <div class="drawer-section">
                <h4 class="drawer-section-title">Direkte ejerandele</h4>
                <ul class="drawer-list">
                  ${data.direct_holdings.map(h => `
                    <li>
                      <a href="/companies/${escapeHtml(h.id)}">${escapeHtml(h.name)}</a>
                      <span class="mono pct-tag">${h.pct ? escapeHtml(h.pct) + ' %' : 'andel'}</span>
                    </li>
                  `).join('')}
                </ul>
              </div>
            `;
          }
          if (data.roles && data.roles.length > 0) {
            bodyHtml += `
              <div class="drawer-section">
                <h4 class="drawer-section-title">Ledelses- og bestyrelsesposter</h4>
                <ul class="drawer-list">
                  ${data.roles.map(r => `
                    <li>
                      <a href="/companies/${escapeHtml(r.id)}">${escapeHtml(r.name)}</a>
                      <span class="role-tag">${escapeHtml(r.role)}</span>
                    </li>
                  `).join('')}
                </ul>
              </div>
            `;
          }
        } else if (data.kind === 'COMPANY') {
          if (data.owners && data.owners.length > 0) {
            bodyHtml += `
              <div class="drawer-section">
                <h4 class="drawer-section-title">Direkte ejere</h4>
                <ul class="drawer-list">
                  ${data.owners.map(o => `
                    <li>
                      <a href="${o.kind === 'PERSON' ? '/persons/' : '/companies/'}${escapeHtml(o.id)}">${escapeHtml(o.name)}</a>
                      <span class="mono pct-tag">${o.pct ? escapeHtml(o.pct) + ' %' : 'ejer'}</span>
                    </li>
                  `).join('')}
                </ul>
              </div>
            `;
          }
        }

        bodyHtml += `
          <div class="drawer-actions">
            <a href="${escapeHtml(data.url)}" class="btn btn-primary">Åbn fuld profil →</a>
          </div>
        `;

        if (contentEl) contentEl.innerHTML = bodyHtml;
      })
      .catch(() => {
        if (contentEl) contentEl.innerHTML = '<div class="drawer-error">Kunne ikke hente detaljer.</div>';
      });
  }

  function closeInspector() {
    if (drawerElem) drawerElem.classList.remove('open');
    if (drawerBackdrop) drawerBackdrop.classList.remove('open');
  }

  /* ==========================================================================
     6. INTERACTIVE SVG PAN / ZOOM & PATH HIGHLIGHTING
     ========================================================================== */
  function initSvgInteractivity() {
    const svgs = document.querySelectorAll('.network-svg, .tree-svg');
    svgs.forEach((svg) => {
      const wrapper = svg.closest('.table-scroll') || svg.parentElement;
      if (!wrapper) return;

      // Add pan/zoom control toolbar
      let controls = wrapper.querySelector('.graph-controls');
      if (!controls) {
        controls = document.createElement('div');
        controls.className = 'graph-controls';
        controls.innerHTML = `
          <button type="button" class="graph-btn" data-action="zoom-in" title="Zoom ind" aria-label="Zoom ind">+</button>
          <button type="button" class="graph-btn" data-action="zoom-out" title="Zoom ud" aria-label="Zoom ud">−</button>
          <button type="button" class="graph-btn" data-action="reset" title="Nulstil visning" aria-label="Nulstil visning">⊙</button>
          <button type="button" class="graph-btn" data-action="export-svg" title="Gem som SVG" aria-label="Gem grafen som SVG-fil"><svg width="14" height="14" aria-hidden="true"><use href="/static/icons/lucide.svg#lucide-download"></use></svg></button>
        `;
        wrapper.style.position = 'relative';
        wrapper.insertBefore(controls, svg);
      }

      let scale = 1;
      let viewBox = svg.viewBox.baseVal;
      const initialViewBox = { x: viewBox.x, y: viewBox.y, w: viewBox.width, h: viewBox.height };

      controls.addEventListener('click', (e) => {
        const t = e.target; if(!t || typeof t.closest !== 'function') return;
        const btn = t.closest('[data-action]');
        if (!btn) return;
        const action = btn.getAttribute('data-action');
        if (action === 'zoom-in') {
          scale = Math.max(0.4, scale * 0.8);
          applyZoom();
        } else if (action === 'zoom-out') {
          scale = Math.min(2.5, scale * 1.25);
          applyZoom();
        } else if (action === 'reset') {
          scale = 1;
          svg.setAttribute('viewBox', `${initialViewBox.x} ${initialViewBox.y} ${initialViewBox.w} ${initialViewBox.h}`);
        } else if (action === 'export-svg') {
          exportSvg(svg);
        }
      });

      function applyZoom() {
        const cx = initialViewBox.x + initialViewBox.w / 2;
        const cy = initialViewBox.y + initialViewBox.h / 2;
        const newW = initialViewBox.w * scale;
        const newH = initialViewBox.h * scale;
        const newX = cx - newW / 2;
        const newY = cy - newH / 2;
        svg.setAttribute('viewBox', `${newX} ${newY} ${newW} ${newH}`);
      }

      function exportSvg(svgEl) {
        const serializer = new XMLSerializer();
        let source = serializer.serializeToString(svgEl);
        if (!source.match(/^<svg[^>]+xmlns="http\:\/\/www\.w3\.org\/2000\/svg"/)) {
          source = source.replace(/^<svg/, '<svg xmlns="http://www.w3.org/2000/svg"');
        }
        const blob = new Blob([source], { type: 'image/svg+xml;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `heimdal_graf_${Date.now()}.svg`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast('SVG downloadet!');
      }

      // Floating Tooltip for SVG
      let tooltip = document.getElementById('graph-tooltip');
      if (!tooltip) {
        tooltip = document.createElement('div');
        tooltip.id = 'graph-tooltip';
        tooltip.className = 'graph-tooltip';
        document.body.appendChild(tooltip);
      }

      // Node and Edge hover highlighting & floating tooltip
      const nodes = svg.querySelectorAll('a');
      nodes.forEach((node) => {
        node.addEventListener('mouseenter', (e) => {
          node.classList.add('node-focused');
          const title = node.querySelector('text')?.textContent || node.getAttribute('aria-label') || 'Enhed';
          tooltip.innerHTML = `<span class="graph-tooltip-title">${escapeHtml(title)}</span><div class="graph-tooltip-sub">Klik for at se profil</div>`;
          tooltip.classList.add('visible');
        });
        node.addEventListener('mousemove', (e) => {
          tooltip.style.left = (e.clientX + 14) + 'px';
          tooltip.style.top = (e.clientY + 14) + 'px';
        });
        node.addEventListener('mouseleave', () => {
          node.classList.remove('node-focused');
          tooltip.classList.remove('visible');
        });
      });
    });
  }

  
  // Record recent entity visits for palette
  function recordRecent(name, url, meta){
    try{
      var r = JSON.parse(localStorage.getItem('heimdal_recent')||'[]');
      r = r.filter(function(x){ return x.url !== url; });
      r.unshift({name:name, url:url, meta:meta, ts:Date.now()});
      localStorage.setItem('heimdal_recent', JSON.stringify(r.slice(0,8)));
    }catch(e){}
  }
  document.addEventListener('click', function(e){
    var t = e.target; if(!t || typeof t.closest !== 'function') return;
    var a = t.closest('a[href^="/companies/"], a[href^="/persons/"]');
    if(a){ recordRecent(a.textContent.trim().slice(0,60), a.getAttribute('href'), ''); }
  });

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  /* One CSV rule for every client-side export (F-9/G-11), the same as
     csv_cell() in heimdal/modules/shared/csv_export.py: quote every cell,
     double embedded quotes, and prefix ' to a string cell that starts with
     = + - @ \t \r so a spreadsheet never runs it as a formula. A Blob, not a
     data: URI, so a '#' in a name cannot cut the file short. */
  function csvCell(v) {
    let s = v == null ? '' : String(v);
    if (typeof v === 'string' && /^[=+\-@\t\r]/.test(s)) s = "'" + s;
    return '"' + s.replace(/"/g, '""') + '"';
  }
  window.heimdalCsvText = (rows) => rows.map((r) => r.map(csvCell).join(',')).join('\r\n') + '\r\n';
  window.heimdalDownloadCsv = (filename, rows) => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([window.heimdalCsvText(rows)], { type: 'text/csv;charset=utf-8' }));
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  document.addEventListener('keydown', e=>{ if(e.key==='?' && !e.metaKey && !e.ctrlKey && document.activeElement.tagName!=='INPUT' && document.activeElement.tagName!=='TEXTAREA'){ e.preventDefault(); var h=document.getElementById('heimdal-help'); if(!h){ h=document.createElement('div'); h.id='heimdal-help'; h.style.cssText='position:fixed;bottom:1rem;left:50%;transform:translateX(-50%);background:var(--surface-elevated);border:1px solid var(--rule);border-radius:var(--r-md);padding:.7rem 1rem;font-size:.8rem;z-index:9999;'; h.innerHTML='<b>Genveje</b>: ⌘K søg · ⌘B nav · ? hjælp · Esc luk'; document.body.appendChild(h); setTimeout(()=>h.remove(),3000);} }})

  // Section anchor-nav — aria-current via IntersectionObserver (P3-RECORD-01)
  function initSectionNav(){
    var nav = document.querySelector('.section-nav');
    if(!nav || !('IntersectionObserver' in window)) return;
    var links = Array.prototype.slice.call(nav.querySelectorAll('a[href^="#"]'));
    if(!links.length) return;
    var idMap = {};
    links.forEach(function(a){ idMap[a.getAttribute('href').slice(1)] = a; });
    // Map timeline anchor to actual section #aendringer if needed
    var timelineLink = idMap['timeline'];
    var aendringer = document.getElementById('aendringer');
    var sections = Object.keys(idMap).map(function(id){ return document.getElementById(id); }).filter(Boolean);
    // Also observe aendringer for Historik when id is timeline
    if(timelineLink && aendringer && !sections.includes(aendringer)) sections.push(aendringer);
    var observer = new IntersectionObserver(function(entries){
      entries.forEach(function(entry){
        if(entry.isIntersecting){
          var id = entry.target.id;
          // Map aendringer → timeline link
          if(id === 'aendringer' && idMap['timeline']) id = 'timeline';
          links.forEach(function(a){ a.removeAttribute('aria-current'); });
          if(idMap[id]) idMap[id].setAttribute('aria-current','location');
        }
      });
    }, { rootMargin: "-60px 0px -70% 0px", threshold: 0 });
    sections.forEach(function(s){ observer.observe(s); });
  }

  // Generic tooltip — P5-TOOLTIP-01: data-tooltip, 300ms delay, 280px, Esc, focus
  function initTooltips(){
    var tip = null, timer = null, current = null;
    function ensureTip(){
      if(tip) return tip;
      tip = document.createElement('div');
      tip.className = 'heimdal-tooltip';
      tip.setAttribute('role','tooltip');
      tip.setAttribute('aria-hidden','true');
      document.body.appendChild(tip);
      return tip;
    }
    function show(el){
      var text = el.getAttribute('data-tooltip');
      if(!text) return;
      current = el;
      var t = ensureTip();
      t.textContent = text;
      t.setAttribute('aria-hidden','false');
      t.classList.add('visible');
      // position: try above, fallback below
      var rect = el.getBoundingClientRect();
      // need to measure after visible
      var w = t.offsetWidth, h = t.offsetHeight;
      var left = Math.max(8, Math.min(window.innerWidth - w - 8, rect.left + rect.width/2 - w/2));
      var top = rect.top - h - 8;
      if(top < 8) top = rect.bottom + 8;
      t.style.left = left + 'px';
      t.style.top = top + 'px';
    }
    function hide(){
      if(tip){ tip.classList.remove('visible'); tip.setAttribute('aria-hidden','true'); }
      current = null;
      clearTimeout(timer); timer = null;
    }
    document.addEventListener('mouseenter', function(e){
      var t = e.target;
      if(!t || typeof t.closest !== 'function') return;
      var el = t.closest('[data-tooltip]');
      if(!el) return;
      clearTimeout(timer);
      timer = setTimeout(function(){ show(el); }, 300);
    }, true);
    document.addEventListener('mouseleave', function(e){
      var t = e.target;
      if(!t || typeof t.closest !== 'function') return;
      var el = t.closest('[data-tooltip]');
      if(el) hide();
    }, true);
    document.addEventListener('focusin', function(e){
      var t = e.target;
      if(!t || typeof t.closest !== 'function') return;
      var el = t.closest('[data-tooltip]');
      if(el) show(el);
    });
    document.addEventListener('focusout', function(e){
      var t = e.target;
      if(!t || typeof t.closest !== 'function') return;
      var el = t.closest('[data-tooltip]');
      if(el) hide();
    });
    document.addEventListener('keydown', function(e){ if(e.key==='Escape') hide(); });
    // reduced-motion: tip uses opacity only, already minimal
  }

  function initTabs(){
    var tablists = Array.prototype.slice.call(document.querySelectorAll('.tablist'));
    if(!tablists.length) return;
    tablists.forEach(function(tablist){
      var group = tablist.getAttribute('data-tab-group') || '';
      // T2.3 (plan v1/F-04): only real panel-tabs participate in the tablist
      // contract. Route links (e.g. Historik) are NOT tabs — they are normal
      // links styled like tabs and never reach activate()/roving-index.
      var tabs = Array.prototype.slice.call(tablist.querySelectorAll('button[role="tab"][data-tab]'));
      var panels;
      if(group){
        panels = Array.prototype.slice.call(document.querySelectorAll('.tab-panel[data-tab-group="'+group+'"]'));
      } else {
        // default group: panels without explicit group (company/person)
        panels = Array.prototype.slice.call(document.querySelectorAll('.tab-panel:not([data-tab-group])'));
        // if home also has grouped panels, exclude them
        if(tablists.some(function(tl){return tl.getAttribute('data-tab-group');})){
          panels = panels.filter(function(p){return !p.getAttribute('data-tab-group');});
        }
      }
      if(!tabs.length || !panels.length) return;
      function activate(name, pushHash){
        tabs.forEach(function(t){
          var on = t.getAttribute('data-tab')===name;
          t.setAttribute('aria-selected', on ? 'true':'false');
          t.tabIndex = on ? 0 : -1;
        });
        panels.forEach(function(p){
          var on = p.getAttribute('data-panel')===name;
          p.classList.toggle('is-active', on);
          p.hidden = !on;
          if(on) p.removeAttribute('hidden'); else p.setAttribute('hidden','');
        });
        if(!group){
          // T2.3: global tab persistence removed — hash/deep-link always wins
          // over the previous session's tab choice (no heimdal_tab_v2 write).
          if(pushHash){
            var map = { ejerskab:'ejere', personer:'roller', roller:'roller', relationer:'virksomhedsrelationer', netvaerk:'panel-netvaerk', overblik:'panel-overblik', analyse:'panel-analyse', kilder:'kilder-dok' };
            var h = map[name] || name;
            if(history.replaceState) history.replaceState(null,'','#'+h);
          }
        } else {
          try{ localStorage.setItem('heimdal_tab_'+group, name); }catch(e){}
        }
      }
      tabs.forEach(function(t){
        t.addEventListener('click', function(){ activate(t.getAttribute('data-tab'), !group); });
      });
      tablist.addEventListener('keydown', function(e){
        var idx = tabs.indexOf(document.activeElement);
        if(idx===-1) return;
        if(e.key==='ArrowRight'){ e.preventDefault(); var n=(idx+1)%tabs.length; tabs[n].focus(); activate(tabs[n].getAttribute('data-tab'), !group); }
        if(e.key==='ArrowLeft'){ e.preventDefault(); var n2=(idx-1+tabs.length)%tabs.length; tabs[n2].focus(); activate(tabs[n2].getAttribute('data-tab'), !group); }
        if(e.key==='Home'){ e.preventDefault(); tabs[0].focus(); activate(tabs[0].getAttribute('data-tab'), !group); }
        if(e.key==='End'){ e.preventDefault(); tabs[tabs.length-1].focus(); activate(tabs[tabs.length-1].getAttribute('data-tab'), !group); }
      });
      var initial = null;
      if(!group){
        var hash = (location.hash||'').replace('#','');
        var hashMap = { ejere:'ejerskab', besiddelser:'ejerskab', roller:'roller', stemmerettigheder:'roller', virksomhedsrelationer:'relationer', personrelationer:'relationer', 'panel-netvaerk':'netvaerk', 'panel-overblik':'overblik', 'panel-analyse':'analyse', 'kilder-dok':'kilder' };
        initial = hashMap[hash] || null;
        // T2.3: no heimdal_tab_v2 fallback read — hash wins, otherwise default
        // (Overblik / first tab). Entity A's tab choice never seeds entity B.
      } else {
        try{ initial = localStorage.getItem('heimdal_tab_'+group); }catch(e){}
      }
      if(initial && tabs.some(function(t){return t.getAttribute('data-tab')===initial;})){
        activate(initial, false);
      } else {
        var def = tabs.find(function(t){return t.getAttribute('aria-selected')==='true';});
        if(def) activate(def.getAttribute('data-tab'), false);
        else if(tabs[0]) activate(tabs[0].getAttribute('data-tab'), false);
      }
      if(!group){
        window.addEventListener('hashchange', function(){
          var h2=(location.hash||'').replace('#','');
          var hm={ ejere:'ejerskab', besiddelser:'ejerskab', roller:'roller', stemmerettigheder:'roller', virksomhedsrelationer:'relationer', personrelationer:'relationer', 'panel-netvaerk':'netvaerk', 'panel-overblik':'overblik', 'panel-analyse':'analyse', 'kilder-dok':'kilder' };
          var m2=hm[h2];
          if(m2) activate(m2,false);
        });
      }
    });
  }

  function initAdvanced(){
    var cb = document.getElementById('adv-toggle');
    if(!cb) return;
    var KEY='heimdal_advanced';
    function apply(v){ document.documentElement.classList.toggle('show-advanced', v); document.body.classList.toggle('show-advanced', v); }
    var stored=false; try{ stored=localStorage.getItem(KEY)==='1'; }catch(e){}
    cb.checked=stored; apply(stored);
    cb.addEventListener('change', function(){ var v=cb.checked; apply(v); try{ localStorage.setItem(KEY, v?'1':'0'); }catch(e){} });
  }

  /* ==========================================================================
     PRINT (T6.2, plan v1/F-29 prep): <details> åbnes ikke af printeren selv —
     ved print foldes alle disclosures ud (og genoprettes efter). Print-CSS
     i heimdal.css skjuler kontroller og håndhæver sidebrud-hygiejne.
     ========================================================================== */
  function initPrintDisclosures() {
    window.addEventListener('beforeprint', () => {
      document.querySelectorAll('details:not([open])').forEach((d) => {
        d.setAttribute('open', '');
        d.dataset.printOpened = '1';
      });
    });
    window.addEventListener('afterprint', () => {
      document.querySelectorAll('details[data-print-opened]').forEach((d) => {
        d.removeAttribute('open');
        delete d.dataset.printOpened;
      });
    });
  }

  /* ==========================================================================
     INITIALIZATION
     ========================================================================== */
  document.addEventListener('DOMContentLoaded', () => {
    initSpineDrawer();
    initTheme();
    initCopyButtons();
    initCommandPalette();
    initHeroSearch();
    initInspectorDrawer();
    initWatchButtons();
    initUnwatchButtons();
    initPrintButtons();
    initSvgLegendFilter();
    initSvgInteractivity();
    initSectionNav();
    initTooltips();
    initTabs();
    initAdvanced();
    initPrintDisclosures();
  });
})();
