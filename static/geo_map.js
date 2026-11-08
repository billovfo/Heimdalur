/* Heimdal Faroese city map: Leaflet aggregate bubbles + accessible table.
 *
 * Data is server-rendered client-side aggregate data (city, region, point,
 * company count). No addresses or entity identifiers ever reach the DOM —
 * city strings are written with textContent, never innerHTML. */

const radiusFor = (count, maxCount) => {
  const max = Math.max(1, maxCount || count);
  return 8 + 24 * Math.sqrt(Math.min(count, max) / max);
};

const REGION_CENTRE_EXCEPTIONS = {
  SUDURSTREYMOY: [62.01014, -6.77157],
};

function aggregateRegions(cities) {
  const byRegion = new Map();
  for (const city of cities) {
    const entry = byRegion.get(city.region) || { points: [], company_count: 0 };
    entry.points.push(city);
    entry.company_count += city.company_count;
    byRegion.set(city.region, entry);
  }
  return [...byRegion].map(([region, { points, company_count }]) => {
    const centre = points.reduce((sum, point) => [sum[0] + point.latitude, sum[1] + point.longitude], [0, 0]);
    const [latitude, longitude] = REGION_CENTRE_EXCEPTIONS[region]
      || [centre[0] / points.length, centre[1] / points.length];
    return { city: regionLabel(region), region, latitude, longitude, company_count };
  });
}

const regionLabel = (region) =>
  (window.HEIMDAL_REGION_LABELS || {})[region] || region;

class HeimdalGeoMap {
  constructor({ containerId, data, tileUrl }) {
    this.container = document.getElementById(containerId);
    if (!this.container) return;

    this.cities = data.cities || [];
    this.coverage = data.coverage || {};
    this.viewMode = 'city';
    // T4.3 (F-10): visible EMPTY/LOADED state — kartdata er inline
    // server-renderet (ingen fetch → ingen LOADING/ERROR her), men et tomt
    // snapshot må aldrig ligne en tom side uden forklaring
    this.shell = new (window.HeimdalGraphShell || function (el) {
      this.el = el; this.msg = null; this.setState = function () {};
    })(this.container, {
      surface: 'kortet',
      emptyTitle: 'Ingen placeringsdata',
      emptyHint: 'Ingen selskaber har en registreret færøsk placering i dette snapshot.',
    });

    this.markerLayer = L.layerGroup();

    this.map = L.map(this.container, {
      center: [61.95, -6.85],
      zoom: 9,
      zoomControl: true,
      attributionControl: true,
    });

    if (tileUrl) {
      L.tileLayer(tileUrl, {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
        maxZoom: 18,
      }).addTo(this.map);
    }

    this.markerLayer.addTo(this.map);
    this.updateCoverage();
    this.bindFilters();
    this.render();
  }

  updateCoverage() {
    this._setText('geo-coverage-faroe', this.coverage.faroe_islands);
    this._setText('geo-coverage-unknown', this.coverage.unknown);
    this._setText('geo-coverage-foreign', this.coverage.foreign);
  }

  activeItems() {
    return this.viewMode === 'region' ? aggregateRegions(this.cities) : this.cities;
  }

  render() {
    const items = this.activeItems();
    const maxCount = Math.max(1, ...items.map((d) => d.company_count));
    this.shell.setState(items.length ? 'LOADED' : 'EMPTY');
    this.markerLayer.clearLayers();
    for (const city of items) {
      const marker = L.circleMarker([city.latitude, city.longitude], {
        radius: radiusFor(city.company_count, maxCount),
        color: '#8ab4f8',
        weight: 1,
        fillColor: '#3b82f6',
        fillOpacity: 0.38,
      });
      const tooltip = document.createElement('span');
      tooltip.textContent = `${city.city} — ${city.company_count} · klik for virksomheder`;
      marker.bindTooltip(tooltip, { direction: 'top' });
      marker.on('click', () => this.selectCity(city));
      marker.addTo(this.markerLayer);
    }
    this.renderTable(items);
  }

  renderTable(cities) {
    const tbody = document.getElementById('geo-table-body');
    if (!tbody) return;
    tbody.textContent = '';
    const isRegion = this.viewMode === 'region';
    for (const city of cities) {
      const tr = document.createElement('tr');

      const tdCity = document.createElement('td');
      if (isRegion) {
        tdCity.textContent = city.city;
        tdCity.className = 'font-semibold';
      } else {
        const link = document.createElement('a');
        link.href = `/companies?q=${encodeURIComponent(city.city.split(' ')[0])}`;
        link.className = 'font-semibold';
        link.textContent = city.city;
        tdCity.appendChild(link);
      }

      const tdRegion = document.createElement('td');
      tdRegion.textContent = regionLabel(city.region);

      const tdCount = document.createElement('td');
      tdCount.className = 'num mono font-bold';
      tdCount.textContent = String(city.company_count);

      tr.appendChild(tdCity);
      tr.appendChild(tdRegion);
      tr.appendChild(tdCount);
      tbody.appendChild(tr);
    }
  }

  selectCity(city) {
    this._setText('geo-inspector-city', city.city);
    this._setText('geo-inspector-count', city.company_count);
    this._setText('geo-inspector-region', regionLabel(city.region));
    this.loadSelectedCompanies(city);
    document.getElementById('geo-selected-companies')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  loadSelectedCompanies(item) {
    if (this.selectedAbortController) this.selectedAbortController.abort();
    const controller = new AbortController();
    this.selectedAbortController = controller;

    const params = new URLSearchParams();
    if (this.viewMode === 'region') params.set('region', item.region);
    else params.set('city', item.city);
    this._setText('geo-selected-status', 'Indlæser virksomheder…');

    fetch(`/api/geo/companies?${params}`, { signal: controller.signal })
      .then((response) => {
        if (controller.signal.aborted) return null;
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((data) => {
        if (controller.signal.aborted || data == null) return;
        this.renderSelectedCompanies(data.companies || []);
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        const tbody = document.getElementById('geo-selected-body');
        if (tbody) tbody.textContent = '';
        this._setText('geo-selected-status', 'Virksomhedslisten kunne ikke indlæses.');
      });
  }

  renderSelectedCompanies(companies) {
    const tbody = document.getElementById('geo-selected-body');
    if (!tbody) return;
    tbody.textContent = '';
    const status = document.getElementById('geo-selected-status');
    if (!companies.length) {
      if (status) status.textContent = 'Ingen virksomheder fundet.';
      return;
    }
    if (status) {
      status.textContent = companies.length === 1
        ? '1 virksomhed fundet'
        : `${companies.length} virksomheder fundet`;
    }
    for (const company of companies) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      const link = document.createElement('a');
      link.href = company.url;
      link.textContent = company.name;
      td.appendChild(link);
      tr.appendChild(td);
      tbody.appendChild(tr);
    }
  }

  bindFilters() {
    const viewButtons = document.querySelectorAll('[data-geo-view]');
    for (const button of viewButtons) {
      button.addEventListener('click', () => this.setViewMode(button.dataset.geoView));
    }
    this.updateViewButtons();
  }

  setViewMode(viewMode) {
    if (viewMode === this.viewMode) return;
    this.viewMode = viewMode;
    this.resetSelection();
    this.updateViewButtons();
    this.updateViewLabels();
    this.render();
  }

  resetSelection() {
    if (this.selectedAbortController) this.selectedAbortController.abort();
    this._setText(
      'geo-inspector-city',
      this.viewMode === 'region' ? 'Vælg en region på kortet' : 'Vælg en by på kortet',
    );
    this._setText('geo-inspector-count', '—');
    this._setText('geo-inspector-region', '—');
    this._setText('geo-selected-status', 'Vælg en by eller region på kortet for at se virksomheder.');
    const tbody = document.getElementById('geo-selected-body');
    if (tbody) tbody.textContent = '';
  }

  updateViewButtons() {
    const viewButtons = document.querySelectorAll('[data-geo-view]');
    for (const button of viewButtons) {
      button.setAttribute('aria-pressed', String(button.dataset.geoView === this.viewMode));
    }
  }

  updateViewLabels() {
    const isRegion = this.viewMode === 'region';
    this._setText('geo-inspector-title', isRegion ? 'Valgt region' : 'Valgt by');
    this._setText('geo-inspector-kind', isRegion ? 'Aktiv Region' : 'Aktiv By');
    this._setText('geo-table-th-first', isRegion ? 'Region' : 'By');
  }

  _setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value == null ? '' : String(value);
  }
}

window.HeimdalGeoMap = HeimdalGeoMap;

// Bootstrap (templates/geo_map.html, G-05): server data comes in a JSON block.
document.addEventListener('DOMContentLoaded', () => {
  const cfg = document.getElementById('geo-map-data');
  if (!cfg) return;
  const { data, tileUrl, regionLabels } = JSON.parse(cfg.textContent);
  window.HEIMDAL_REGION_LABELS = regionLabels;
  window.geoMapInstance = new HeimdalGeoMap({ containerId: 'geo-map-container', data, tileUrl });
});
