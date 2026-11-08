/* GraphShell (T4.3 / F-10, remediation plan v1): fælles synlige tilstande for
 * graf-flader — LOADING, LOADED, EMPTY, ERROR.
 *
 * Hvorfor: silent return ved ikke-OK fetch betød, at en 700px-canvas bare var
 * blank — det ligner "ingen data". API-fejl SKAL være synlige og forståelige
 * uden console. Lean: ingen rammer, kun tilstand + besked + næste handling.
 *
 * Brug:
 *   const shell = new HeimdalGraphShell(containerEl, { surface: 'netværk' });
 *   shell.setState('LOADING');
 *   shell.setState('LOADED');            // fjerner overlay
 *   shell.setState('EMPTY');             // tom snapshot-visning
 *   shell.setState('ERROR');             // synlig fejl + næste handling
 *
 * Ingen analytics, ingen data-adfærd — kun tilstandsoverflade. Beskeder skrives
 * med textContent (aldrig innerHTML med data). */

(function () {
  'use strict';

  var STATES = {
    LOADING: {
      cls: 'graph-shell-loading',
      title: 'Henter grafdata…',
      hint: 'Data hentes fra det lokale snapshot.',
    },
    EMPTY: {
      cls: 'graph-shell-empty',
      title: 'Ingen grafdata i dette snapshot',
      hint: 'Ingen relationer at tegne for denne enhed i det aktuelle snapshot.',
    },
    ERROR: {
      cls: 'graph-shell-error',
      title: 'Grafdata kunne ikke hentes',
      hint: 'Der opstod en teknisk fejl under hentningen. Kantlisten/tabellen viser stadig data.',
    },
  };

  function GraphShell(container, opts) {
    opts = opts || {};
    this.el = container;
    this.surface = opts.surface || 'grafen';
    this.retryFn = typeof opts.retry === 'function' ? opts.retry : null;
    // a surface may name its own empty state (geo: 'Ingen placeringsdata')
    this.emptyTitle = opts.emptyTitle || null;
    this.emptyHint = opts.emptyHint || null;
    this.msg = null;
    this._mount();
  }

  GraphShell.prototype._mount = function () {
    if (!this.el) return;
    var m = this.el.querySelector('.graph-shell-state');
    if (!m) {
      m = document.createElement('div');
      m.className = 'graph-shell-state';
      m.setAttribute('role', 'status');
      this.el.appendChild(m);
    }
    this.el.classList.add('graph-shell');
    this.msg = m;
  };

  GraphShell.prototype.setState = function (name) {
    if (!this.msg) return;
    var s = STATES[name];
    if (!s) {
      // ukendt tilstand = LOADED (fail safe: aldrig hængende overlay)
      name = 'LOADED';
      s = null;
    }
    this.msg.dataset.state = name;
    if (name === 'LOADED' || !s) {
      this.msg.hidden = true;
      this.msg.textContent = '';
      this.el.classList.remove('graph-shell-blank');
      return;
    }
    this.msg.hidden = false;
    this.msg.className = 'graph-shell-state ' + s.cls;
    while (this.msg.firstChild) this.msg.removeChild(this.msg.firstChild);
    var t = document.createElement('strong');
    t.textContent = (name === 'EMPTY' && this.emptyTitle) || s.title;
    var h = document.createElement('span');
    h.className = 'graph-shell-hint';
    h.textContent = (name === 'EMPTY' && this.emptyHint) || s.hint;
    this.msg.appendChild(t);
    this.msg.appendChild(h);
    if (name === 'ERROR') {
      if (this.retryFn) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn btn-outline text-xs graph-shell-retry';
        b.textContent = 'Prøv igen';
        b.addEventListener('click', this.retryFn);
        this.msg.appendChild(b);
      } else {
        var r = document.createElement('span');
        r.className = 'graph-shell-hint';
        r.textContent = 'Næste handling: genindlæs siden.';
        this.msg.appendChild(r);
      }
    }
    // en tom/fejl-tilstand skal ALDRIG ligne data — marker canvas blank
    this.el.classList.toggle('graph-shell-blank',
      name === 'EMPTY' || name === 'ERROR' || name === 'LOADING');
  };

  window.HeimdalGraphShell = GraphShell;
})();