# Heimdal Graph — Design System

*Single engine: `static/graph_network.js` + `templates/macros.html` + `templates/network.html`*

## Tokens

### Kant-arter (EDGE_KIND) — ord + form + kulør
| kind | label | CSS | stroke | dash | farve |
|---|---|---|---|---|---|
| `DIRECT_OWNERSHIP` | Ejerskab | `edge-ownership` | solid | `[]` | `rgba(56,189,248,0.75)` — cyan |
| `CONTROL` | Kontrolregistrering | `edge-control` | stiplet | `[7,4]` | `rgba(245,158,11,0.6)` — amber |
| `ROLE` | Rolle | `edge-role` | prikket | `[1,3.5]` | `rgba(244,114,182,0.6)` — pink |

> Aldrig kulør alene — altid label + stregstil + legend. WCAG 1.4.1.

### Nodetyper (KIND)
| kind | glyph | fill | cls |
|---|---|---|---|
| `PERSON` | ● | `#f43f5e` | `k-person` |
| `COMPANY` | ■ | `#0284c7` | `k-company` |
| `ORGANIZATION` | ◆ | `#f59e0b` | `k-org` |
| `AUTHORITY` | ▲ | `#10b981` | `k-authority` |
| center | ring | `#38bdf8` 24px | `node-center` |
| hub (≥2 stier) | amber halo + tal-badge | `#D97706` | — |
| søg-match | amber ring 3px | `#f59e0b` | — |

### Pct-bånd (threshold)
- `0–25%` — slate, `25–50%` — cyan, `≥50%` — green
- Slider `0–50 step 5` + histogram 3 søjler (h = count/max*14 +4)

## Komponenter

### `<heimdal-graph entity kind>`
Custom element, lazy via `IntersectionObserver`, `prefers-reduced-motion` disables force, `Cache-Control: max-age=60` på `/api/network`.
- Attrs: `entity` (int), `kind` (`COMPANY|PERSON`)
- Keyboard: `Arrows` pan, `+/-` zoom, `0` reset, `Enter` åbn node, `Esc` clear hover/søg, `Tab` fokus
- A11y: `tabindex=0 role=application`, `legend button[aria-pressed]`, `ledger` table under canvas (truth), `outline:2px solid var(--accent)` focus-ring
- `?edge=OWNERSHIP,CONTROL,ROLE` + `localStorage heimdal_graph_filter` synk

### A — §21 SVG (network.html)
Server `ego_layout` cirkel r300, fluid `width:100% max-height:65vh`, én sticky toolbar `SVG — statisk cirkel`, legend `button[aria-pressed]` filtrér via `line.filtered-out{opacity:.12}`, URL `?edge` + localStorage.

### B — Interaktiv (macros.html)
Canvas force `kRepel 2400 kSpring 0.04 damping 0.85`, center pinned, 300 ticks, `resetTransform` DPR fix, neighbourhood fade 0.15 på hover, histogram, `network-ledger` under, `fitNetwork()` bounds 40px pad.

### C — Stifinder (pathfinder.html)
Mini-graf 280px: union af op til 5 stier ×4 hops, hubs `amber halo + tal-badge`, valgt sti tyk amber 6px overlay, sammenligningstabel `Sti | Længde | Svageste kant | Mellemmænd` radio → highlight, `PNG/CSV` eksport.

### D — Sankey (group_detail.html)
`HeimdalSankeyFlow` kun når `group.tree`. Tom → `empty-state` (ingen 440px kasse). Tree-siden viser `Kontrol % · Effektiv %` begge steder.

## Do / Dont
- Do: tabel under hver graf
- Dont: emoji som ikon (Lucide SVG)
- Do: legend = filter
- Dont: time-slider før historik findes (skjult til B5)
- Do: `ctx.setLineDash` på canvas

## Filer
- `static/graph_network.js` — single engine
- `templates/macros.html` — `interactive_network_graph`
- `templates/network.html` — `EDGE_KIND` + legend filter JS
- `templates/pathfinder.html` — mini-graph + comparison table
- `templates/group_detail.html` — Sankey empty-state
- `static/heimdal.css` — focus-ring + filtered-out
- `heimdal/modules/graph/routes.py` — `Cache-Control`, `?edge`

## Performance budget
- `graph_network.js` < 60kb, `tick` < 8ms @50 noder, lazy `IntersectionObserver`, `resetTransform` ved resize, ingen fetch når skjult tab.

## Historik (B5)
- `GET /api/network/<id>/historical?date=YYYY-MM-DD` — i dag echo, planlagt til `WHERE snapshot <= date` fade 0.2 for nye kanter (Gotham-mønster).
