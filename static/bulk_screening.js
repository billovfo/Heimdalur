/* Bulk Screening (templates/bulk_screening.html). Moved out of the template so
   the CSP needs no script-src 'unsafe-inline' (G-05): the buttons carry
   data-bulk-action / data-bulk-filter, "Udskriv" is data-action="print" (app.js). */
let lastBulkResults = [];

function handleFileSelect(e) {
  const files = e.target.files;
  if (files.length > 0) {
    readFile(files[0]);
  }
}

function readFile(file) {
  const reader = new FileReader();
  reader.onload = (event) => {
    document.getElementById('bulk-input').value = event.target.result;
  };
  reader.readAsText(file);
}

function observationLabel(count) {
  if (count > 0) {
    return count === 1 ? '1 observation' : `${count} observationer`;
  }
  return 'Ingen';
}

async function runBulkScreening() {
  const raw = document.getElementById('bulk-input').value.trim();
  if (!raw) return alert('Indtast venligst mindst ét selskabsnummer eller navn.');

  const identifiers = raw.split('\n').map(s => s.trim()).filter(Boolean);
  if (identifiers.length > 200) return alert('Højst 200 linjer pr. opslag.');
  const tbody = document.getElementById('bulk-results-body');
  const skel = document.getElementById('bulk-skeleton');
  const scroll = document.querySelector('#bulk-table').closest('.table-scroll');
  const empty0 = document.getElementById('bulk-empty-state');
  if(empty0) empty0.style.display = 'none';
  if(skel && scroll){ skel.style.display='block'; skel.setAttribute('aria-hidden','false'); scroll.style.display='none'; } else { tbody.innerHTML = '<tr><td colspan="5" class="text-center py-6">Slår op…</td></tr>'; }

  try {
    const res = await fetch('/api/bulk/screen', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ identifiers })
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    lastBulkResults = data.results || [];
    
    document.getElementById('bulk-count').textContent = lastBulkResults.length
      ? `${lastBulkResults.length} enheder slået op` : 'Ingen resultater';

    const empty = document.getElementById('bulk-empty-state');
    if(skel){ skel.style.display='none'; skel.setAttribute('aria-hidden','true'); }
    if (lastBulkResults.length === 0) {
      if(empty) empty.style.display = 'block';
      if(scroll) scroll.style.display = 'none';
      return;
    }
    if(empty) empty.style.display = 'none';
    if(scroll) scroll.style.display = 'block';

    // Update KPI Bar
    const total = lastBulkResults.length;
    const obs = lastBulkResults.filter(r => r.observation_count > 0).length;
    const pep = lastBulkResults.filter(r => r.has_pep).length;

    document.getElementById('kpi-total').textContent = total;
    document.getElementById('kpi-obs').textContent = obs;
    // has_pep === null: no PEP data loaded (D14), so there is no PEP count to show.
    document.getElementById('kpi-pep').textContent = lastBulkResults.some(r => r.has_pep === null) ? '—' : pep;
    document.getElementById('count-all').textContent = total;
    document.getElementById('bulk-kpi-bar').style.display = 'grid';
    document.getElementById('bulk-filters-wrap').style.display = 'flex';

    if(skel && scroll){ skel.style.display='none'; skel.setAttribute('aria-hidden','true'); scroll.style.display='block'; }
    renderTableRows(lastBulkResults);
  } catch (err) {
    console.error('Bulk screening error:', err);
    if(skel && scroll){ skel.style.display='none'; skel.setAttribute('aria-hidden','true'); scroll.style.display='block'; }
    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-danger py-6">Opslaget kunne ikke gennemføres. Prøv igen — eller åbn siden op igen. Dine data er ikke ændret.</td></tr>';
  }
}

function renderTableRows(items) {
  const tbody = document.getElementById('bulk-results-body');
  const wrap = document.getElementById('bulk-result-wrap');
  if (!items.length) {
    if(wrap) wrap.style.display = 'none';
    const e = document.getElementById('bulk-empty-state');
    if(e){ e.style.display = 'block'; e.querySelector('.empty-state-title').textContent = 'Ingen enheder matcher filteret'; e.querySelector('.empty-state-body').textContent = 'Prøv «Alle» eller kør et nyt opslag.'; }
    return;
  }
  const e2 = document.getElementById('bulk-empty-state');
  if(e2) e2.style.display = 'none';
  if(wrap) wrap.style.display = 'block';

  // T1.2: DOM-API rendering (textContent) — ingen HTML-interpolation af rå værdier.
  // Rå query/navn/pep_title/regnr kan indeholde HTML-tegn (craftet input) og skal
  // aldrig konkatenes ind i innerHTML. Attributter valideres (id skal være int).
  const _td = (cls, text) => {
    const td = document.createElement('td');
    if (cls) td.className = cls;
    if (text !== undefined && text !== null) td.textContent = String(text);
    return td;
  };
  const _isKnownId = r => Number.isInteger(r.id) && (r.kind === 'PERSON' || r.kind === 'COMPANY');
  const _entityCell = r => {
    const td = document.createElement('td');
    const strong = document.createElement('strong');
    if (_isKnownId(r)) {
      const a = document.createElement('a');
      a.className = 'entity-link';
      a.href = r.kind === 'PERSON' ? `/persons/${r.id}` : `/companies/${r.id}`;
      a.textContent = r.name || '';
      strong.appendChild(a);
    } else {
      strong.textContent = r.name || '';
    }
    td.appendChild(strong);
    if (r.regnr) {
      const pill = document.createElement('span');
      pill.className = 'regnr-pill';
      pill.textContent = r.regnr;
      td.appendChild(pill);
    }
    return td;
  };
  const _pepCell = r => {
    const td = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = r.has_pep ? 'badge badge-outline' : 'badge badge-outline text-muted';
    if (r.has_pep) {
      const NS = 'http://www.w3.org/2000/svg';
      const svg = document.createElementNS(NS, 'svg');
      svg.setAttribute('class', 'icon-inline');
      svg.setAttribute('width', '12');
      svg.setAttribute('height', '12');
      svg.setAttribute('aria-hidden', 'true');
      const use = document.createElementNS(NS, 'use');
      use.setAttribute('href', '/static/icons/lucide.svg#lucide-landmark');
      svg.appendChild(use);
      badge.appendChild(svg);
      badge.appendChild(document.createTextNode('PEP: ' + (r.pep_title || 'Registreret')));
    } else {
      badge.textContent = r.has_pep === null ? 'PEP-data ikke indlæst' : 'Ingen';
    }
    td.appendChild(badge);
    return td;
  };
  // T1.1: handling pr. entity-kind — KYC findes kun for COMPANY (/persons/<id>/kyc
  // findes ikke). Unknown får ingen falsk KYC-action.
  const _kycCell = r => {
    const td = document.createElement('td');
    if (Number.isInteger(r.id) && r.kind === 'COMPANY') {
      const a = document.createElement('a');
      a.href = `/companies/${r.id}/kyc`;
      a.className = 'btn btn-xs btn-outline';
      a.textContent = 'KYC Dossier →';
      td.appendChild(a);
    } else if (r.kind === 'PERSON') {
      const m = document.createElement('span');
      m.className = 'text-xs text-muted';
      m.textContent = 'KYC: kun virksomheder';
      td.appendChild(m);
    } else {
      td.textContent = '—';
    }
    return td;
  };

  tbody.textContent = '';
  for (const r of items) {
    const tr = document.createElement('tr');
    tr.className = 'bulk-row';
    tr.appendChild(_td('font-mono text-xs text-muted', r.query));
    tr.appendChild(_entityCell(r));
    tr.appendChild(_pepCell(r));
    const obsTd = document.createElement('td');
    obsTd.className = 'num';
    obsTd.textContent = observationLabel(r.observation_count);
    tr.appendChild(obsTd);
    tr.appendChild(_kycCell(r));
    tbody.appendChild(tr);
  }
}

function filterBulkResults(type, btn) {
  document.querySelectorAll('.filter-chip').forEach(b => {
    b.classList.remove('btn-primary');
    b.classList.add('btn-outline');
  });
  if (btn) {
    btn.classList.remove('btn-outline');
    btn.classList.add('btn-primary');
  }

  if (type === 'OBS') {
    renderTableRows(lastBulkResults.filter(r => r.observation_count > 0));
  } else if (type === 'PEP') {
    renderTableRows(lastBulkResults.filter(r => r.has_pep));
  } else {
    renderTableRows(lastBulkResults);
  }
}

function exportBulkCSV() {
  if (!lastBulkResults.length) return alert('Ingen resultater at eksportere.');
  const rows = [['Query', 'ID', 'Name', 'Kind', 'RegNr', 'PEP', 'ObservationCount']].concat(
    lastBulkResults.map(r => [
      r.query, r.id, r.name, r.kind, r.regnr || '', r.has_pep === null ? 'NOT_LOADED' : r.has_pep ? 'YES' : 'NO', r.observation_count
    ]));
  window.heimdalDownloadCsv(`heimdal_bulk_audit_${new Date().toISOString().slice(0,10)}.csv`, rows);
}

async function addAllToWatchlist() {
  if (!lastBulkResults.length) return alert('Ingen resultater at tilføje.');
  for (const r of lastBulkResults) {
    if (r.id) {
      await fetch('/api/watchlist/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ canonical_entity_id: r.id, entity_kind: r.kind || 'COMPANY' })
      });
    }
  }
  alert(`Alle ${lastBulkResults.length} enheder er tilføjet til dit Overvågningscenter!`);
}

document.getElementById('file-upload')?.addEventListener('change', handleFileSelect);
document.addEventListener('click', (e) => {
  const chip = e.target.closest('[data-bulk-filter]');
  if (chip) return filterBulkResults(chip.dataset.bulkFilter, chip);
  const b = e.target.closest('[data-bulk-action]');
  if (!b) return;
  const a = b.dataset.bulkAction;
  if (a === 'pick-file') document.getElementById('file-upload').click();
  else if (a === 'run') runBulkScreening();
  else if (a === 'example') document.getElementById('bulk-input').value = '3690\n5396';
  else if (a === 'csv') exportBulkCSV();
  else if (a === 'watch-all') addAllToWatchlist();
});
