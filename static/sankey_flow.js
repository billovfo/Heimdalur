/**
 * Heimdal Corporate Group Sankey & Capital Flow Visualizer
 * Zero-dependency HTML5 Canvas Sankey flow diagram generator.
 */

class HeimdalSankeyFlow {
  constructor(containerId, parentId) {
    this.container = document.getElementById(containerId);
    if (!this.container) return;
    // T4.3 (F-10): visible states — never a silently blank sankey
    this.shell = new (window.HeimdalGraphShell || function (el) {
      this.el = el; this.msg = null; this.setState = function () {};
    })(this.container, { surface: 'konstellations-flow' });
    this.shell.setState('LOADING');

    this.parentId = parentId;
    this.canvas = document.createElement('canvas');
    this.ctx = this.canvas.getContext('2d');
    this.container.appendChild(this.canvas);

    this.nodes = [];
    this.links = [];
    this.hoveredLink = null;
    this.hoveredNode = null;

    this.initResize();
    this.loadData();
  }

  initResize() {
    const updateSize = () => {
      const rect = this.container.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      this.width = rect.width || 800;
      this.height = Math.max(400, rect.height || 460);
      this.canvas.width = this.width * dpr;
      this.canvas.height = this.height * dpr;
      this.canvas.style.width = `${this.width}px`;
      this.canvas.style.height = `${this.height}px`;
      this.ctx.scale(dpr, dpr);
      this.layoutAndRender();
    };
    updateSize();
    window.addEventListener('resize', updateSize);
  }

  async loadData() {
    // T4.3 (F-10): 200-rig → LOADED; 200-tom → EMPTY; 4xx/5xx/netværk → ERROR
    try {
      const res = await fetch(`/api/groups/${this.parentId}/flow`);
      if (!res.ok) {
        console.error('Failed to load group flow: HTTP', res.status);
        this.shell.setState('ERROR');
        return;
      }
      const data = await res.json();
      this.nodes = data.nodes || [];
      this.links = data.links || [];
      this.layoutAndRender();
      this.bindEvents();
      this.shell.setState(this.nodes.length ? 'LOADED' : 'EMPTY');
    } catch (err) {
      console.error('Failed to load group flow:', err);
      this.shell.setState('ERROR');
    }
  }

  layoutAndRender() {
    if (!this.nodes.length) return;

    // Group nodes by depth column
    const columns = [];
    this.nodes.forEach(node => {
      const d = node.depth || 0;
      if (!columns[d]) columns[d] = [];
      columns[d].push(node);
    });

    const numCols = columns.length;
    const colWidth = (this.width - 120) / Math.max(1, numCols - 1);
    const nodeWidth = 24;

    // Position nodes vertically in each column
    columns.forEach((col, cIdx) => {
      const x = 60 + cIdx * colWidth;
      const totalH = this.height - 80;
      const itemH = Math.min(60, totalH / Math.max(1, col.length));
      const spacing = totalH / Math.max(1, col.length);

      col.forEach((node, rIdx) => {
        node.x = x;
        node.y = 40 + rIdx * spacing + (spacing - itemH) / 2;
        node.width = nodeWidth;
        node.height = Math.max(28, itemH * 0.8);
      });
    });

    this.render();
  }

  render() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.width, this.height);

    const nodeMap = new Map(this.nodes.map(n => [n.id, n]));

    // 1. Draw Flow Ribbons (Links)
    this.links.forEach(link => {
      const src = nodeMap.get(link.source);
      const tgt = nodeMap.get(link.target);
      if (!src || !tgt) return;

      const x0 = src.x + src.width;
      const y0 = src.y + src.height / 2;
      const x1 = tgt.x;
      const y1 = tgt.y + tgt.height / 2;
      const ribbonHeight = Math.max(6, Math.min(24, (link.value / 100) * 24));

      ctx.beginPath();
      ctx.moveTo(x0, y0 - ribbonHeight / 2);
      ctx.bezierCurveTo(x0 + (x1 - x0) * 0.5, y0 - ribbonHeight / 2, x1 - (x1 - x0) * 0.5, y1 - ribbonHeight / 2, x1, y1 - ribbonHeight / 2);
      ctx.lineTo(x1, y1 + ribbonHeight / 2);
      ctx.bezierCurveTo(x1 - (x1 - x0) * 0.5, y1 + ribbonHeight / 2, x0 + (x1 - x0) * 0.5, y0 + ribbonHeight / 2, x0, y0 + ribbonHeight / 2);
      ctx.closePath();

      const isHovered = this.hoveredLink === link;
      const grad = ctx.createLinearGradient(x0, 0, x1, 0);
      if (link.type === 'SUBSIDIARY') {
        grad.addColorStop(0, isHovered ? 'rgba(56, 189, 248, 0.7)' : 'rgba(56, 189, 248, 0.35)');
        grad.addColorStop(1, isHovered ? 'rgba(14, 165, 233, 0.8)' : 'rgba(14, 165, 233, 0.45)');
      } else {
        grad.addColorStop(0, isHovered ? 'rgba(245, 158, 11, 0.7)' : 'rgba(245, 158, 11, 0.35)');
        grad.addColorStop(1, isHovered ? 'rgba(217, 119, 6, 0.8)' : 'rgba(217, 119, 6, 0.45)');
      }
      ctx.fillStyle = grad;
      ctx.fill();

      ctx.strokeStyle = isHovered ? '#ffffff' : 'rgba(255, 255, 255, 0.15)';
      ctx.lineWidth = isHovered ? 1.5 : 0.75;
      ctx.stroke();

      // Flow Percentage Badge
      const midX = (x0 + x1) / 2;
      const midY = (y0 + y1) / 2;
      ctx.fillStyle = '#0f172a';
      ctx.beginPath();
      ctx.roundRect(midX - 26, midY - 9, 52, 18, 4);
      ctx.fill();
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
      ctx.stroke();

      ctx.fillStyle = isHovered ? '#38bdf8' : '#e2e8f0';
      ctx.font = 'bold 10px monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(`${link.control_pct}%`, midX, midY);
    });

    // 2. Draw Nodes
    this.nodes.forEach(node => {
      ctx.fillStyle = node.depth === 0 ? '#3b82f6' : (node.kind === 'PERSON' ? '#f43f5e' : '#0284c7');
      ctx.beginPath();
      ctx.roundRect(node.x, node.y, node.width, node.height, 4);
      ctx.fill();

      ctx.strokeStyle = this.hoveredNode === node ? '#ffffff' : 'rgba(255, 255, 255, 0.3)';
      ctx.lineWidth = this.hoveredNode === node ? 2 : 1;
      ctx.stroke();

      // Label text
      ctx.fillStyle = '#ffffff';
      ctx.font = node.depth === 0 ? 'bold 12px sans-serif' : '11px sans-serif';
      ctx.textAlign = node.depth === 0 ? 'right' : 'left';
      ctx.textBaseline = 'middle';

      const labelX = node.depth === 0 ? node.x - 10 : node.x + node.width + 10;
      const labelY = node.y + node.height / 2;
      const labelText = node.name.length > 24 ? node.name.slice(0, 22) + '…' : node.name;
      ctx.fillText(labelText, labelX, labelY);
    });
  }

  bindEvents() {
    this.canvas.addEventListener('mousemove', e => {
      const rect = this.canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;

      let foundNode = null;
      for (const n of this.nodes) {
        if (sx >= n.x && sx <= n.x + n.width && sy >= n.y && sy <= n.y + n.height) {
          foundNode = n;
          break;
        }
      }

      if (foundNode !== this.hoveredNode) {
        this.hoveredNode = foundNode;
        this.canvas.style.cursor = foundNode ? 'pointer' : 'default';
        this.render();
      }
    });

    this.canvas.addEventListener('click', () => {
      if (this.hoveredNode) {
        window.location.href = `/companies/${this.hoveredNode.id}`;
      }
    });
  }
}

window.HeimdalSankeyFlow = HeimdalSankeyFlow;

// Bootstrap (templates/group_detail.html, G-05): the parent id is a data attribute.
document.addEventListener('DOMContentLoaded', () => {
  const c = document.querySelector('#sankey-flow-container[data-parent-id]');
  if (c) new HeimdalSankeyFlow(c.id, parseInt(c.dataset.parentId, 10));
});
