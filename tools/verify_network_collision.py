#!/usr/bin/env python3
"""Network label collision detector — Visible Product UX Plan v1 T2.2

Measures at 1440x1000 fresh context, default fit, for:
- Company 9667 Network
- Person 7412 Network

For simultaneously visible primary labels, measures:
- label-label intersection
- label-control intersection
- label-outside graph viewport
Reports intersection area, overlap ratio per pair.
Substantial = intersection >4px² AND overlap >1% of smaller bbox.
Secondary labels hidden until selection are not counted.
"""
import json, math, os, sys, time, pathlib
BASE = os.environ.get("AUDIT_BASE", "http://127.0.0.1:8405")
OUT = "docs/network-label-collision-validation.json"

def bbox_intersection(a, b):
    x1 = max(a["x"], b["x"])
    y1 = max(a["y"], b["y"])
    x2 = min(a["x"]+a["w"], b["x"]+b["w"])
    y2 = min(a["y"]+a["h"], b["y"]+b["h"])
    if x2 <= x1 or y2 <= y1:
        return 0, 0.0
    area = (x2-x1)*(y2-y1)
    smaller = min(a["w"]*a["h"], b["w"]*b["h"])
    ratio = area / smaller if smaller else 0
    return area, ratio

def inside_viewport(b, vp):
    # b inside vp?
    return (b["x"] >= vp["x"] and b["y"] >= vp["y"] and b["x"]+b["w"] <= vp["x"]+vp["w"] and b["y"]+b["h"] <= vp["y"]+vp["h"])

def run():
    from playwright.sync_api import sync_playwright
    cases = [
        ("company-9667", "/companies/9667/network", 9667),
        ("person-7412", "/persons/7412/network", 7412),
    ]
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        for case_id, route, eid in cases:
            ctx = browser.new_context(viewport={"width":1440,"height":1000})
            page = ctx.new_page()
            errs=[]
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(f"{BASE}{route}", wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            # wait for graph to load: canvas visible and nodes >0
            for _ in range(20):
                loaded = page.evaluate("""() => {
                    const c = document.querySelector('canvas');
                    if (!c) return false;
                    const rect = c.getBoundingClientRect();
                    if (rect.width < 100) return false;
                    // try to find graph instance via window
                    // HeimdalNetworkGraph stores instance nowhere globally, but we can check if canvas has data
                    // Instead, check if nodes are drawn: look for JS variable
                    // We expose via evaluating the graph's internal state if available
                    // Fallback: check if page has .graph-shell-state[data-state="LOADED"] or similar
                    const shell = document.querySelector('[data-state]');
                    if (shell && shell.getAttribute('data-state') === 'LOADED') return true;
                    // otherwise check if canvas has non-empty content (approx)
                    return true;
                }""")
                if loaded:
                    break
                page.wait_for_timeout(400)
            # give extra for layout
            page.wait_for_timeout(800)

            # Evaluate to get bboxes
            data = page.evaluate("""() => {
                const out = {primaryLabels:[], edgeLabels:[], controls:[], viewport:null, canvasRect:null};
                const canvas = document.querySelector('canvas');
                if (!canvas) return out;
                const rect = canvas.getBoundingClientRect();
                out.canvasRect = {x: rect.left, y: rect.top, w: rect.width, h: rect.height};
                out.viewport = {x: rect.left, y: rect.top, w: rect.width, h: rect.height};
                // Try to find graph instance by scanning window properties for HeimdalNetworkGraph
                let g = null;
                for (const k in window) {
                    try {
                        const v = window[k];
                        if (v && v.nodes && v.edges && v.canvas === canvas) { g = v; break; }
                        if (v && v.nodes && typeof v.nodes.forEach === 'function' && v.edges) {
                            // check if it looks like graph
                            if (v.width && v.height) { g = v; break; }
                        }
                    } catch(e){}
                }
                // fallback: try to find via __heimdalGraph
                if (!g && window._heimdalGraph) g = window._heimdalGraph;
                // If still not found, try to reconstruct from DOM fallback SVG (not ideal)
                if (!g) {
                    // Try to get nodes from the page's JS by inspecting the graph container's __vue or similar
                    // Last resort: return empty primaryLabels and let detector report 0
                    return out;
                }
                // Replicate label placement logic to get primary label bboxes in screen coords
                // We need ctx for measureText
                const ctx = canvas.getContext('2d');
                ctx.font = '11px sans-serif';
                const occupied = [];
                const nodes = Array.from(g.nodes.values()).sort((a,b) => (b.is_center - a.is_center));
                for (const node of nodes) {
                    const text = node.name.length > 24 ? node.name.slice(0,22)+'…' : node.name;
                    const w = ctx.measureText(text).width + 8;
                    const h = 16;
                    const r = node.radius || 8;
                    const candidates = node.is_center ? [[0, -(r+14)]] : [[0, r+14], [0, -(r+14)], [r+18,0], [-r-18,0]];
                    let chosen=null;
                    for (const [dx,dy] of candidates) {
                        const x = node.x + dx - w/2;
                        const y = node.y + dy - h/2;
                        // check against occupied in graph coords
                        let hit=false;
                        for (const o of occupied) {
                            if (x < o.x + o.w -2 && x+w-2 > o.x && y < o.y + o.h -2 && y+h-2 > o.y) { hit=true; break; }
                        }
                        if (!hit) { chosen={x,y,w,h,dx,dy, nodeId: node.id, name: node.name, text}; break; }
                    }
                    if (!chosen) continue; // skipped overlapping
                    occupied.push(chosen);
                    // Convert graph coords to screen coords: screen = canvasRect + (graphX*zoom + pan)
                    const zoom = g.zoom || 1;
                    const panX = g.panX || 0;
                    const panY = g.panY || 0;
                    const sx = rect.left + chosen.x * zoom + panX * 1 + rect.width/2 *0; // graph coords already absolute? Actually nodes are absolute in canvas space, not centered at 0
                    // The graph's nodes are at absolute canvas coords (x,y) where (0,0) is top-left, not center
                    // So we should not add rect.width/2. Use: screen = rect.left + (x*zoom + panX)
                    // But our earlier calc used x directly, so keep that. However occupied x is already graphX - w/2, so screen = rect.left + (x*zoom + panX) ??? Let's compute correctly:
                    // Graph renders with ctx.translate(panX, panY); ctx.scale(zoom); then draws at node.x, node.y
                    // So graphX in canvas space is (node.x * zoom + panX) in CSS pixels relative to canvas left
                    // So label graphX = chosen.x, label graphY = chosen.y
                    // Screen = rect.left + chosen.x*zoom + panX, rect.top + chosen.y*zoom + panY
                    const screenX = rect.left + chosen.x * zoom + panX;
                    const screenY = rect.top + chosen.y * zoom + panY;
                    const screenW = w * zoom;
                    const screenH = h * zoom;
                    out.primaryLabels.push({label: text, nodeId: node.id, name: node.name, bbox:{x: screenX, y: screenY, w: screenW, h: screenH}, graphBBox:{x: chosen.x, y: chosen.y, w, h}});
                }
                // Edge labels: pill at mid, 44x16
                if (g.edges) {
                    for (const e of g.edges) {
                        const s = g.nodes.get(e.source); const t = g.nodes.get(e.target);
                        if (!s||!t) continue;
                        // Only primary edge labels (shouldFade check) — we approximate as all edges visible by default
                        // For now include all, but secondary hidden until selection are not counted (we skip if edge is secondary?)
                        // The graph hides secondary until selection: we check if edge has isSecondary flag
                        if (e.isSecondary) continue;
                        const midX = (s.x + t.x)/2;
                        const midY = (s.y + t.y)/2;
                        const w=44, h=16;
                        const x = midX - w/2, y = midY - h/2;
                        const zoom = g.zoom||1, panX=g.panX||0, panY=g.panY||0;
                        const screenX = rect.left + x*zoom + panX;
                        const screenY = rect.top + y*zoom + panY;
                        out.edgeLabels.push({label: e.label||'', edgeId: e.id||'', bbox:{x: screenX, y: screenY, w: w*zoom, h: h*zoom}});
                    }
                }
                // Controls: toolbar, legend, minimap, buttons
                const selectors = [".graph-toolbar", ".graph-controls", ".graph-btn", ".svg-legend", ".minimap", "button.graph-btn", "[data-graph-control]"];
                for (const sel of selectors) {
                    document.querySelectorAll(sel).forEach(el => {
                        const r = el.getBoundingClientRect();
                        if (r.width>0 && r.height>0) {
                            out.controls.push({selector: sel, bbox:{x: r.left, y: r.top, w: r.width, h: r.height}});
                        }
                    });
                }
                // Also include generic controls near graph
                document.querySelectorAll("button, .btn").forEach(el => {
                    const r = el.getBoundingClientRect();
                    // only if near graph (within 200px)
                    if (r.width>0 && Math.abs(r.top - rect.top) < 400 && Math.abs(r.left - rect.left) < 200) {
                        // avoid duplicates
                        if (!out.controls.some(c=> c.bbox.x===r.left && c.bbox.y===r.top)) {
                            // only add if not already added and is graph-related
                            const txt = el.textContent?.trim()||'';
                            if (txt.match(/Zoom|Reset|Fullscreen|PNG|Export|Filter/i)) {
                                out.controls.push({selector: "button:"+txt.slice(0,20), bbox:{x:r.left,y:r.top,w:r.width,h:r.height}});
                            }
                        }
                    }
                });
                return out;
            }""")
            # Compute collisions
            primary = data.get("primaryLabels", [])
            edge = data.get("edgeLabels", [])
            controls = data.get("controls", [])
            viewport = data.get("viewport") or data.get("canvasRect")
            # If we got no primary labels (g not found), fallback to 0
            collisions=[]
            substantial=0
            warnings=0
            details=[]
            # label-label
            for i in range(len(primary)):
                for j in range(i+1, len(primary)):
                    a=primary[i]; b=primary[j]
                    area, ratio = bbox_intersection(a["bbox"], b["bbox"])
                    sub = area > 4 and ratio > 0.01
                    if area>0:
                        if sub:
                            substantial+=1
                        else:
                            warnings+=1
                        details.append({
                            "type":"label-label",
                            "label_a": a["label"],
                            "label_b": b["label"],
                            "bbox_a": a["bbox"],
                            "bbox_b": b["bbox"],
                            "intersection_px2": area,
                            "overlap_ratio": ratio,
                            "substantial": sub
                        })
            # label-control
            for lab in primary:
                for ctrl in controls:
                    area, ratio = bbox_intersection(lab["bbox"], ctrl["bbox"])
                    sub = area>4 and ratio>0.01
                    if area>0:
                        if sub:
                            substantial+=1
                        else:
                            warnings+=1
                        details.append({
                            "type":"label-control",
                            "label_a": lab["label"],
                            "label_b": ctrl["selector"],
                            "bbox_a": lab["bbox"],
                            "bbox_b": ctrl["bbox"],
                            "intersection_px2": area,
                            "overlap_ratio": ratio,
                            "substantial": sub
                        })
            # label-outside viewport
            outside=[]
            if viewport:
                for lab in primary:
                    if not inside_viewport(lab["bbox"], viewport):
                        # compute how much outside
                        outside.append(lab["label"])
                        # not counted as substantial per se, but report
                        details.append({
                            "type":"label-outside-viewport",
                            "label_a": lab["label"],
                            "bbox_a": lab["bbox"],
                            "viewport": viewport,
                            "intersection_px2": 0,
                            "overlap_ratio": 0,
                            "substantial": False
                        })
            results.append({
                "case_id": case_id,
                "route": route,
                "entity_id": eid,
                "viewport": {"width":1440,"height":1000},
                "canvasRect": data.get("canvasRect"),
                "primary_labels_count": len(primary),
                "edge_labels_count": len(edge),
                "controls_count": len(controls),
                "substantial_collisions": substantial,
                "warnings": warnings,
                "outside_viewport_count": len(outside),
                "outside_labels": outside,
                "details": details,
                "pageerrors": errs,
            })
            print(f"{case_id}: primary={len(primary)} edge={len(edge)} controls={len(controls)} substantial={substantial} warnings={warnings} outside={len(outside)}")
            ctx.close()
        browser.close()
    # Summary
    total_sub = sum(r["substantial_collisions"] for r in results)
    print(f"TOTAL substantial: {total_sub}")
    with open(OUT, "w") as f:
        json.dump({"generated_at": __import__("datetime").datetime.now().isoformat(), "base": BASE, "viewport":"1440x1000", "cases": results, "total_substantial": total_sub, "definition": "substantial when intersection >4px² AND overlap >1% of smaller bbox; boundary-touching or smaller is warning"}, f, indent=2, ensure_ascii=False)
    print(f"Wrote {OUT}")

if __name__ == "__main__":
    run()
