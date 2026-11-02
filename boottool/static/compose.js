/* Compose: place, arrange, colour, draw and combine vector graphics, then send the page to Print.
   Built on paper.js (vendored in static/vendor, so it works offline). All units are millimetres. */
const Compose = (() => {
  const MMPX = 96 / 25.4;                                  // screen pixels per mm at "100%"
  const SWATCHES = ["#000000", "#c8102e", "#f26522", "#f2a900", "#00a651", "#1d3f8f", "#00aeef", "#ec008c", "#ffffff", "#d3d3d3"];
  const SIZES = {A5: [148, 210], A4: [210, 297], A3: [297, 420]};
  const S = {
    ready: false, tool: "select", W: 297, H: 210, units: "mm", sel: [], hist: [], hi: -1, lock: true,
    lib: [], libFolder: "", libQ: "", fonts: null, fontsFolder: "", font: null, text: {text: "BOOTLEG", size: 36, spacing: 0, align: "left"},
    shape: "rect", sides: 5, style: {fill: "#000000", stroke: null, weight: 0.5}, penFill: false,
    chipF: undefined, chipS: undefined, scopeAll: false, nodes: [], space: false, snap: true,
    simplify: 0.05, drag: null, pen: null, mouse: null,
    fillC: "#ffffff", fillGrow: 0.3, fillGap: 0.3,
  };
  let L = {}, T = null, root = null, cv = null;
  const num = (v, d = 2) => +(+v).toFixed(d);
  const unitK = () => S.units === "in" ? 25.4 : 1;
  const fu = v => num(v / unitK(), S.units === "in" ? 3 : 2);
  const tu = v => +v * unitK();
  const isPI = i => i instanceof paper.PathItem;
  const zoom = () => paper.view.zoom;
  const px = n => n / zoom();
  const hex = c => c ? c.toCSS(true) : null;
  const topOf = it => { while (it && it.parent && it.parent !== L.art) it = it.parent; return it; };
  const kindOf = it => (it.data && it.data.kind) || "shape";
  const toast_ = m => (typeof toast === "function" ? toast(m) : console.log(m));

  // ------------------------------------------------------------------ items
  function leaves(items) {
    const out = [];
    for (const it of items) {
      if (isPI(it)) out.push(it);
      else for (const p of it.getItems({match: i => isPI(i) && !(i.parent instanceof paper.CompoundPath)})) out.push(p);
    }
    return out;
  }
  function paths(items) {
    const out = [];
    for (const it of items) {
      if (it instanceof paper.Path) out.push(it);
      for (const p of it.getItems({class: paper.Path})) if (p !== it) out.push(p);
    }
    return out;
  }
  const allItems = () => L.art.children.slice();
  const selBounds = () => S.sel.length ? S.sel.map(i => i.bounds).reduce((a, b) => a.unite(b)) : null;
  function nameOf(it, base) { it.data = Object.assign({}, it.data, {name: (it.data && it.data.name) || base}); it.name = it.data.name; return it; }
  function place(it, kind, name) {
    it.data = Object.assign({}, it.data, {kind, name: name || (it.data && it.data.name) || kind});
    it.name = it.data.name; return it;
  }

  // ------------------------------------------------------------------ history
  function snap() { return L.art.exportJSON(); }
  function commit() {
    S.hist = S.hist.slice(0, S.hi + 1); S.hist.push(snap()); if (S.hist.length > 30) S.hist.shift();
    S.hi = S.hist.length - 1; syncBar();
  }
  function restore(i) {
    S.hi = i; S.sel = []; S.nodes = []; S.pen = null;
    L.art.removeChildren(); L.art.importJSON(S.hist[i]);
    for (const c of L.art.children) c.name = (c.data && c.data.name) || c.name;
    drawUI(); renderSide(); syncBar();
  }
  const undo = () => { if (S.hi > 0) restore(S.hi - 1); };
  const redo = () => { if (S.hi < S.hist.length - 1) restore(S.hi + 1); };

  // ------------------------------------------------------------------ view
  function size() { const r = cv.parentElement.getBoundingClientRect(); return [Math.max(50, r.width), Math.max(50, r.height)]; }
  function resize() { if (!S.ready) return; const [w, h] = size(); paper.view.viewSize = new paper.Size(w, h); drawUI(); }
  function fit() {
    const [w, h] = size(); paper.view.viewSize = new paper.Size(w, h);
    paper.view.zoom = Math.min((w - 60) / S.W, (h - 90) / S.H);
    paper.view.center = new paper.Point(S.W / 2, S.H / 2 + 6 / paper.view.zoom * 0); drawUI(); syncBar();
  }
  function setZoom(z, at) {
    z = Math.min(Math.max(z, 0.05), 400);
    const p = at ? paper.view.viewToProject(at) : null; paper.view.zoom = z;
    if (p) paper.view.center = paper.view.center.add(p.subtract(paper.view.viewToProject(at)));
    drawUI(); syncBar();
  }
  function drawBoard() {
    L.bg.removeChildren();
    const r = new paper.Path.Rectangle({point: [0, 0], size: [S.W, S.H], fillColor: "#ffffff", strokeColor: "#000000", strokeWidth: 1, strokeScaling: false});
    r.shadowColor = new paper.Color(0, 0, 0, 0.25); r.shadowBlur = 14; r.shadowOffset = new paper.Point(0, 4);
    L.bg.addChild(r);
  }
  function setBoard(w, h) {
    S.W = Math.min(Math.max(+w || S.W, 20), 1200); S.H = Math.min(Math.max(+h || S.H, 20), 1200);
    drawBoard(); fit(); renderSide();
  }

  // ------------------------------------------------------------------ gizmo and node overlay
  function sq(p, s, filled) {
    const r = new paper.Path.Rectangle({point: p.subtract(s / 2), size: s, fillColor: filled ? "#404040" : "#ffffff", strokeColor: "#000000", strokeWidth: 1, strokeScaling: false});
    L.ui.addChild(r); return r;
  }
  function handles(b) {
    const m = [];
    for (const [ix, iy] of [[-1, -1], [0, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [0, 1], [1, 1]])
      m.push({ix, iy, p: new paper.Point(ix < 0 ? b.left : ix > 0 ? b.right : b.center.x, iy < 0 ? b.top : iy > 0 ? b.bottom : b.center.y)});
    return m;
  }
  const rotPoint = b => new paper.Point(b.center.x, b.top - px(26));
  function drawUI() {
    if (!S.ready) return;
    L.ui.removeChildren();
    const sz = px(8);
    if (S.tool === "nodes") drawNodes();
    else if (S.sel.length && S.tool !== "pen") {
      const b = selBounds();
      L.ui.addChild(new paper.Path.Rectangle({rectangle: b, strokeColor: "#404040", strokeWidth: 1, strokeScaling: false, dashArray: [5, 4]}));
      if (S.tool === "select") {
        for (const h of handles(b)) sq(h.p, sz, false);
        const rp = rotPoint(b);
        L.ui.addChild(new paper.Path.Line({from: [b.center.x, b.top], to: rp, strokeColor: "#404040", strokeWidth: 1, strokeScaling: false}));
        L.ui.addChild(new paper.Path.Circle({center: rp, radius: px(5), fillColor: "#ffffff", strokeColor: "#000000", strokeWidth: 1, strokeScaling: false}));
      }
    }
    if (S.pen) drawPenPreview();
    if (S.drag && S.drag.guides) for (const g of S.drag.guides) L.ui.addChild(new paper.Path.Line({from: g[0], to: g[1], strokeColor: "#ec008c", strokeWidth: 1, strokeScaling: false}));
    paper.view.update();
  }
  function drawNodes() {
    const view = paper.view.bounds.expand(px(20)); let n = 0;
    S.nodeList = [];
    for (const p of paths(S.sel)) {
      if (!p.bounds.intersects(view)) continue;
      for (const s of p.segments) {
        if (n++ > 4000) break;
        const on = S.nodes.includes(s);
        if (on) {
          for (const [h, nm] of [[s.handleIn, "in"], [s.handleOut, "out"]]) {
            if (h.isZero()) continue;
            const hp = s.point.add(h);
            L.ui.addChild(new paper.Path.Line({from: s.point, to: hp, strokeColor: "#404040", strokeWidth: 1, strokeScaling: false}));
            L.ui.addChild(new paper.Path.Circle({center: hp, radius: px(4), fillColor: "#ffffff", strokeColor: "#000000", strokeWidth: 1, strokeScaling: false}));
            S.nodeList.push({seg: s, h: nm, p: hp});
          }
        }
        sq(s.point, px(7), on); S.nodeList.push({seg: s, h: null, p: s.point});
      }
    }
  }
  function drawPenPreview() {
    const pn = S.pen; if (!pn || !pn.path.segments.length) return;
    const last = pn.path.lastSegment;
    if (S.mouse && !pn.dragging) {
      const c = new paper.Path({strokeColor: "#9a9a9a", strokeWidth: 1, strokeScaling: false, dashArray: [5, 4]});
      c.add(new paper.Segment(last.point, null, last.handleOut)); c.add(new paper.Segment(S.mouse));
      L.ui.addChild(c);
    }
    for (const s of pn.path.segments) sq(s.point, px(7), s === pn.path.firstSegment);
  }

  // ------------------------------------------------------------------ selection
  function select(items, additive) {
    items = items.filter(Boolean);
    S.sel = additive ? [...new Set([...S.sel, ...items])] : items;
    S.nodes = S.nodes.filter(s => S.sel.some(i => i === topOf(s.path)));
    S.chipF = S.chipS = undefined; drawUI(); renderSide();
  }
  function hitItem(pt) {
    const r = L.art.hitTest(pt, {fill: true, stroke: true, tolerance: px(4), match: h => h.item.visible && !(topOf(h.item) || {}).locked});
    return r ? topOf(r.item) : null;
  }
  function inView(it) { return it.visible && !it.locked; }

  // ------------------------------------------------------------------ placing things
  function stripClip(g) { if (g.clipped && g.children.length) { const c = g.children[0]; g.clipped = false; c.remove(); } }
  function fitInto(it, at, maxFrac = 0.6) {
    const natW = it.bounds.width * 25.4 / 96, natH = it.bounds.height * 25.4 / 96;      // SVG pixels at 96 per inch
    let k = 25.4 / 96;
    if (natW > S.W * maxFrac || natH > S.H * maxFrac) k = Math.min(S.W * maxFrac / it.bounds.width, S.H * maxFrac / it.bounds.height);
    it.scale(k, new paper.Point(it.bounds.topLeft));
    it.position = at || new paper.Point(S.W / 2, S.H / 2);
  }
  async function addFromLibrary(name, at) {
    try {
      const r = await fetch("/api/library/file?name=" + encodeURIComponent(name)); if (!r.ok) throw new Error("Could not open " + name);
      addSvg(await r.text(), name.replace(/\.svg$/i, ""), at);
    } catch (e) { toast_(e.message); }
  }
  function addSvg(text, name, at) {
    const g = L.art.importSVG(text, {expandShapes: true, insert: true, applyMatrix: true});
    if (!g) { toast_("That SVG could not be read."); return; }
    stripClip(g); L.art.addChild(g);
    if (g instanceof paper.Group) g.applyMatrix = true;
    fitInto(g, at); place(g, "svg", name);
    commit(); select([g]);
  }
  function addImageFile(file, at) {
    const url = URL.createObjectURL(file);
    const ras = new paper.Raster({source: url, insert: false}); ras.onLoad = () => {
      L.art.addChild(ras);
      const k = Math.min(S.W * 0.6 / ras.width, S.H * 0.6 / ras.height, 25.4 / 96);
      ras.scale(k); ras.position = at || new paper.Point(S.W / 2, S.H / 2);
      place(ras, "image", file.name.replace(/\.[^.]+$/, "")); commit(); select([ras]);
    };
  }
  async function addFile(file, at) {
    if (/\.svg$/i.test(file.name) || file.type === "image/svg+xml") addSvg(await file.text(), file.name.replace(/\.svg$/i, ""), at);
    else if ((file.type || "").startsWith("image/")) addImageFile(file, at);
    else toast_("Choose an SVG or an image.");
  }

  // ------------------------------------------------------------------ text
  async function ensureFonts() {
    if (S.fonts) return;
    try {
      const j = await (await fetch("/api/fonts")).json(); S.fonts = j.fonts; S.fontsFolder = j.folder;
      let saved = null; try { saved = localStorage.getItem("boottool.font"); } catch (e) {}
      const pick = n => S.fonts.find(f => (f.family + " " + f.style).toLowerCase() === (n || "").toLowerCase());
      S.font = (saved && pick(saved)) || pick("Impact Regular") || pick("Arial Bold") || pick("Arial Narrow Regular") || S.fonts.find(f => /sans/i.test(f.family) && /bold/i.test(f.style)) || S.fonts[0] || null;
    } catch (e) { S.fonts = []; }
  }
  async function outlineText(t) {
    if (!S.font) throw new Error("No fonts found. Put .ttf or .otf files in " + S.fontsFolder);
    const r = await fetch("/api/text", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({font: S.font.id, text: t.text, size: t.size, spacing: t.spacing, align: t.align})});
    const j = await r.json(); if (!r.ok) throw new Error(j.error); return j;
  }
  async function addText(at) {
    await ensureFonts();
    try {
      const t = {...S.text, size: S.text.size * 25.4 / 72, font: S.font.id, fname: S.font.family + " " + S.font.style};
      const j = await outlineText(t);
      const it = new paper.CompoundPath({pathData: j.d, fillColor: S.style.fill || "#000000", insert: false});
      L.art.addChild(it); it.position = at.add([j.w / 2, j.h / 2]);
      place(it, "text", S.text.text.split("\n")[0].slice(0, 24));
      it.data.text = {text: S.text.text, size: S.text.size, spacing: S.text.spacing, align: S.text.align, font: S.font.id, fname: t.fname, rot: 0};
      commit(); select([it]);
    } catch (e) { toast_(e.message); }
  }
  let textTimer = null;
  function retext(it, change) {
    Object.assign(it.data.text, change);
    clearTimeout(textTimer);
    textTimer = setTimeout(async () => {
      const d = it.data.text;
      try {
        const j = await outlineText({text: d.text, size: d.size * 25.4 / 72, spacing: d.spacing, align: d.align, font: d.font});
        const c = it.bounds.center, fill = it.fillColor, stroke = it.strokeColor, sw = it.strokeWidth;
        it.pathData = j.d; it.fillColor = fill; it.strokeColor = stroke; it.strokeWidth = sw;
        it.position = c; if (d.rot) it.rotate(d.rot, c);
        it.name = it.data.name = d.text.split("\n")[0].slice(0, 24) || "Text"; commit(); drawUI(); renderLayersOnly();
      } catch (e) { toast_(e.message); }
    }, 220);
  }

  // Type straight onto the page: a box with a caret where you click (Text tool), or double-click existing text (Select tool) to edit it.
  let editor = null;
  async function openEditor(pt, it) {
    await closeEditor(true); await ensureFonts();
    const d = it ? it.data.text : null, size = (d ? d.size : S.text.size) * 25.4 / 72 * paper.view.zoom;
    const font = d ? (S.fonts || []).find(f => f.id === d.font) : S.font;
    const at = it ? it.bounds.topLeft : pt, vp = paper.view.projectToView(at);
    const ta = document.createElement("textarea"); ta.spellcheck = false; ta.value = d ? d.text : "";
    const fill = it ? (it.fillColor ? it.fillColor.toCSS(true) : "#000") : (S.style.fill || "#000");
    ta.style.cssText = `position:absolute;left:${vp.x}px;top:${vp.y}px;z-index:6;margin:0;padding:0;border:0;outline:1px dashed #404040;outline-offset:5px;background:transparent;resize:none;overflow:hidden;white-space:pre;line-height:1.15;` +
      `font-size:${size}px;font-family:${font ? JSON.stringify(font.family) + "," : ""}Arial,sans-serif;font-weight:${font && /bold/i.test(font.style) ? 700 : 400};color:${fill};min-width:${Math.max(size, 20)}px;caret-color:#000`;
    const fitBox = () => { ta.style.height = "0"; ta.style.width = "0"; ta.style.height = ta.scrollHeight + "px"; ta.style.width = Math.max(ta.scrollWidth + 4, size) + "px"; };
    ta.oninput = fitBox;
    ta.onkeydown = e => { e.stopPropagation(); if (e.key === "Escape" || (e.key === "Enter" && (e.metaKey || e.ctrlKey))) { e.preventDefault(); ta.blur(); } };
    ta.onkeyup = e => e.stopPropagation();
    ta.onpointerdown = e => e.stopPropagation();
    ta.onblur = () => closeEditor(false);
    root.querySelector("#cwrap").append(ta);
    editor = {ta, pt, it, hint: el("div", "", "")};
    if (it) { it.visible = false; drawUI(); }
    fitBox(); setTimeout(() => { if (editor && editor.ta === ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); } }, 40);
    toast_("Type on the page. Enter for a new line, Esc or click outside to finish.");
  }
  async function closeEditor(silent) {
    const e = editor; if (!e) return; editor = null;
    const text = e.ta.value.replace(/\s+$/, ""); e.ta.onblur = null; e.ta.remove();
    if (e.it) {
      e.it.visible = true;
      if (text && text !== e.it.data.text.text) retext(e.it, {text}); drawUI(); renderSide();
    } else if (text.trim()) { S.text.text = text; await addText(e.pt); }
  }

  // ------------------------------------------------------------------ shapes
  function makeShape(a, b, shift) {
    let r = new paper.Rectangle(a, b);
    if (shift) { const s = Math.max(r.width, r.height); r = new paper.Rectangle(a, new paper.Point(a.x + (b.x >= a.x ? s : -s), a.y + (b.y >= a.y ? s : -s))); }
    if (r.width < 0.2 || r.height < 0.2) return null;
    let p;
    if (S.shape === "ellipse") p = new paper.Path.Ellipse({rectangle: r, insert: false});
    else if (S.shape === "polygon") p = new paper.Path.RegularPolygon({center: r.center, sides: S.sides, radius: Math.min(r.width, r.height) / 2, insert: false});
    else if (S.shape === "star") p = new paper.Path.Star({center: r.center, points: S.sides, radius1: Math.min(r.width, r.height) / 2, radius2: Math.min(r.width, r.height) / 4, insert: false});
    else p = new paper.Path.Rectangle({rectangle: r, insert: false});
    if (S.shape === "polygon" || S.shape === "star") { const k = Math.max(r.width, r.height) / Math.max(p.bounds.width, p.bounds.height, 0.001); p.scale(k); p.position = r.center; }
    p.fillColor = S.style.fill; p.strokeColor = S.style.stroke; if (S.style.stroke) p.strokeWidth = S.style.weight;
    L.art.addChild(p); place(p, "shape", {rect: "Rectangle", ellipse: "Ellipse", polygon: "Polygon", star: "Star"}[S.shape]);
    return p;
  }

  // ------------------------------------------------------------------ pen
  function penFinish(close) {
    const pn = S.pen; if (!pn) return; S.pen = null;
    const p = pn.path;
    if (p.segments.length < 2) { p.remove(); drawUI(); return; }
    if (close) { p.closed = true; if (S.penFill && S.style.fill) p.fillColor = S.style.fill; }
    commit(); select([topOf(p)]);
  }
  function penDown(e) {
    const pt = e.point, shift = e.event.shiftKey;
    if (!S.pen) {
      // with a path selected: click a point to delete it, click a line to add one
      const ps = paths(S.sel);
      if (ps.length) {
        for (const p of ps) {
          const h = p.hitTest(pt, {segments: true, stroke: true, tolerance: px(6)});
          if (h && h.type === "segment") { h.segment.remove(); commit(); drawUI(); return; }
          if (h && h.type === "stroke" && h.location) { p.divideAt(h.location); commit(); drawUI(); return; }
        }
      }
      const path = new paper.Path({strokeColor: S.style.stroke || "#000000", strokeWidth: S.style.weight, strokeScaling: true, fillColor: null, insert: false});
      L.art.addChild(path); place(path, "pen", "Pen path");
      path.add(pt); S.pen = {path, dragging: true, start: pt}; drawUI(); return;
    }
    const p = S.pen.path;
    if (p.segments.length > 2 && pt.getDistance(p.firstSegment.point) < px(8)) { S.pen.closing = true; S.pen.dragging = true; return; }
    let q = pt;
    if (shift && p.lastSegment) {
      const a = p.lastSegment.point, d = pt.subtract(a), ang = Math.round(Math.atan2(d.y, d.x) / (Math.PI / 4)) * Math.PI / 4;
      q = a.add(new paper.Point({length: d.length, angle: ang * 180 / Math.PI}));
    }
    p.add(q); S.pen.dragging = true; S.pen.start = q; drawUI();
  }
  function penDrag(e) {
    const pn = S.pen; if (!pn || !pn.dragging || pn.closing) return;
    const s = pn.path.lastSegment, d = e.point.subtract(s.point);
    if (d.length > px(3)) { s.handleOut = d; s.handleIn = d.multiply(-1); }
    drawUI();
  }
  function penUp(e) {
    const pn = S.pen; if (!pn) return; pn.dragging = false;
    if (pn.closing) penFinish(true); else drawUI();
  }

  // ------------------------------------------------------------------ nodes
  function nodeAt(pt) {
    const tol = px(8); let bh = null, bd = tol, ba = null, ad = tol;
    for (const n of (S.nodeList || [])) {
      const d = n.p.getDistance(pt);
      if (n.h) { if (d < bd) { bd = d; bh = n; } } else if (d < ad) { ad = d; ba = n; }
    }
    return bh || ba;                                   // a handle wins over an anchor sitting under it
  }
  function markShape(items) { for (const i of items) { const t = topOf(i); if (t && t.data && t.data.kind === "text") t.data.kind = "shape"; } }
  function nodesDown(e) {
    const pt = e.point, n = nodeAt(pt);
    if (n && n.h) { S.drag = {type: "handle", seg: n.seg, which: n.h}; return; }
    if (n) {
      if (e.event.shiftKey) S.nodes = S.nodes.includes(n.seg) ? S.nodes.filter(s => s !== n.seg) : [...S.nodes, n.seg];
      else if (!S.nodes.includes(n.seg)) S.nodes = [n.seg];
      S.drag = {type: "nodes", last: pt, moved: false}; drawUI(); renderSide(); return;
    }
    // double click on a line adds a point
    if (e.event.detail >= 2) for (const p of paths(S.sel)) {
      const h = p.hitTest(pt, {stroke: true, tolerance: px(6)});
      if (h && h.location) { const s = p.divideAt(h.location); S.nodes = s ? [s] : []; commit(); drawUI(); renderSide(); return; }
    }
    if (!e.event.shiftKey) S.nodes = [];
    S.drag = {type: "marquee", a: pt, add: e.event.shiftKey}; drawUI();
  }
  function nodesDrag(e) {
    const d = S.drag; if (!d) return;
    if (d.type === "handle") {
      const s = d.seg, v = e.point.subtract(s.point);
      const other = d.which === "in" ? "handleOut" : "handleIn", mine = d.which === "in" ? "handleIn" : "handleOut";
      const keepOpp = !e.event.altKey && !s[other].isZero();
      const colinear = keepOpp && Math.abs(s[mine].getDirectedAngle(s[other]) % 360) > 170 && Math.abs(s[mine].getDirectedAngle(s[other]) % 360) < 190;
      s[mine] = v; if (colinear) s[other] = v.normalize(s[other].length).multiply(-1);
      d.moved = true; markShape(S.sel); drawUI();
    } else if (d.type === "nodes") {
      const dl = e.point.subtract(d.last); d.last = e.point; d.moved = true;
      for (const s of S.nodes) s.point = s.point.add(dl);
      markShape(S.sel); drawUI();
    } else if (d.type === "marquee") {
      L.ui.removeChildren(); drawNodes();
      L.ui.addChild(new paper.Path.Rectangle({rectangle: new paper.Rectangle(d.a, e.point), strokeColor: "#404040", strokeWidth: 1, strokeScaling: false, dashArray: [4, 3], fillColor: new paper.Color(0, 0, 0, 0.06)}));
    }
  }
  function nodesUp(e) {
    const d = S.drag; S.drag = null; if (!d) return;
    if (d.type === "marquee") {
      const r = new paper.Rectangle(d.a, e.point), hit = [];
      if (r.width > px(3) || r.height > px(3)) for (const p of paths(S.sel)) for (const s of p.segments) if (r.contains(s.point)) hit.push(s);
      S.nodes = d.add ? [...new Set([...S.nodes, ...hit])] : hit; drawUI(); renderSide();
    } else if (d.moved) commit();
  }
  function setNodeType(t) {
    for (const s of S.nodes) {
      if (t === "corner") { s.handleIn = new paper.Point(0, 0); s.handleOut = new paper.Point(0, 0); }
      else {
        s.smooth({type: "continuous"});
        if (t === "symmetric") { const l = (s.handleIn.length + s.handleOut.length) / 2; if (l > 0) { s.handleOut = s.handleOut.normalize(l); s.handleIn = s.handleOut.multiply(-1); } }
      }
    }
    markShape(S.sel); commit(); drawUI();
  }
  function deleteNodes() {
    for (const s of S.nodes.slice()) { const p = s.path; if (p && p.segments.length > 2) s.remove(); }
    S.nodes = []; markShape(S.sel); commit(); drawUI(); renderSide();
  }
  function breakAt() {
    const s = S.nodes[0]; if (!s || !s.path) return;
    const p = s.path, loc = s.location;
    if (p.closed) { p.splitAt(loc); p.closed = false; } else p.splitAt(loc);
    S.nodes = []; commit(); drawUI(); renderSide();
  }
  function simplifySel() {
    let a = 0, b = 0;
    for (const p of paths(S.sel)) { a += p.segments.length; p.simplify(Math.max(S.simplify, 0.001)); b += p.segments.length; }
    markShape(S.sel); S.nodes = []; commit(); drawUI(); renderSide(); toast_(`Points: ${a.toLocaleString()} to ${b.toLocaleString()}`);
  }

  const hasArea = r => r && (r.children ? r.children.length > 0 && Math.abs(r.area) > 1e-6 : Math.abs(r.area) > 1e-6 || (!r.closed && r.length > 1e-3 && !r.fillColor));

  // ------------------------------------------------------------------ combine
  const styleFrom = (dst, src) => { dst.fillColor = src.fillColor; dst.strokeColor = src.strokeColor; dst.strokeWidth = src.strokeWidth; };
  function closedCopy(l) { const c = l.clone({insert: false}); if (c instanceof paper.Path) c.closed = true; return c; }
  function combine(op) {
    const ls = leaves(S.sel).sort((a, b) => zIndex(a) - zIndex(b));
    if (op === "join") return joinEnds();
    if (op === "combine") {
      if (ls.length < 2) return toast_("Select two or more shapes.");
      const c = new paper.CompoundPath({children: ls.map(l => closedCopy(l)), fillRule: "evenodd", insert: false}); styleFrom(c, ls[ls.length - 1]);
      return replaceWith(ls, [c]);
    }
    if (op === "letters") {
      const cps = ls.filter(l => l instanceof paper.CompoundPath); if (!cps.length) return toast_("Select some text or a combined shape first.");
      const out = [];
      for (const c of cps) {
        const kids = c.children.filter(k => Math.abs(k.area) > 1e-6).sort((a, b) => Math.abs(b.area) - Math.abs(a.area)), outers = [];
        for (const k of kids) { let host = null; for (const o of outers) if (o.path.bounds.contains(k.bounds) && o.path.contains(k.interiorPoint)) host = o; if (host) host.holes.push(k); else outers.push({path: k, holes: []}); }
        outers.sort((a, b) => a.path.bounds.left - b.path.bounds.left);
        for (const o of outers) { const cp = new paper.CompoundPath({children: [o.path, ...o.holes].map(k => k.clone({insert: false})), fillRule: "evenodd", insert: false}); styleFrom(cp, c); out.push(cp); }
      }
      return replaceWith(cps, out);
    }
    if (op === "release") {
      const cps = ls.filter(l => l instanceof paper.CompoundPath); if (!cps.length) return toast_("Select a combined shape first.");
      const out = []; for (const c of cps) for (const ch of c.children.slice()) { const p = ch.clone({insert: false}); styleFrom(p, c); out.push(p); }
      return replaceWith(cps, out);
    }
    if (ls.length < 2) return toast_("Select two or more shapes.");
    const top = ls[ls.length - 1], rest = ls.slice(0, -1);
    let res = [];
    if (op === "unite") { let r = closedCopy(ls[0]); for (const l of ls.slice(1)) r = r.unite(closedCopy(l), {insert: false}); styleFrom(r, top); res = [r]; }
    else if (op === "intersect") { let r = closedCopy(ls[0]); for (const l of ls.slice(1)) r = r.intersect(closedCopy(l), {insert: false}); styleFrom(r, top); res = [r]; }
    else if (op === "exclude") { let r = closedCopy(ls[0]); for (const l of ls.slice(1)) r = r.exclude(closedCopy(l), {insert: false}); styleFrom(r, top); res = [r]; }
    else if (op === "subtract") {
      let cut = closedCopy(top); const back = ls.slice(0, -1);
      res = back.map(b => { const r = closedCopy(b).subtract(cut, {insert: false}); styleFrom(r, b); return r; });
    } else if (op === "split") {
      const cutter = closedCopy(top);
      for (const b of rest) { const a = closedCopy(b).intersect(cutter, {insert: false}), c = closedCopy(b).subtract(cutter, {insert: false}); styleFrom(a, b); styleFrom(c, b); res.push(a, c); }
      res = res.filter(hasArea);
    }
    res = res.filter(hasArea); if (!res.length) return toast_("Nothing was left after that. Check that the shapes overlap.");
    replaceWith(ls, res);
  }
  function zIndex(it) { let z = 0, t = it; const chain = []; while (t && t !== L.art) { chain.unshift(t.index); t = t.parent; } chain.forEach((v, i) => z += v / Math.pow(1e4, i)); return z; }
  function replaceWith(olds, news) {
    const tops = [...new Set(olds.map(topOf))], idx = Math.max(...tops.map(t => t.index));
    for (const o of olds) o.remove();
    for (const t of tops) if (t.parent && t.children && !t.children.length) t.remove();
    const made = news.map(n => { L.art.insertChild(Math.min(idx, L.art.children.length), n); n.applyMatrix = true; return place(n, "shape", "Shape"); });
    commit(); select(made);
  }
  function joinEnds() {
    const ps = paths(S.sel).filter(p => !p.closed && p.segments.length > 1);
    if (ps.length === 1) { ps[0].closed = true; markShape(S.sel); commit(); drawUI(); return; }
    if (ps.length < 2) return toast_("Select an open line, or two open lines.");
    let a = ps[0];
    for (const b of ps.slice(1)) {
      const ea = [a.firstSegment.point, a.lastSegment.point], eb = [b.firstSegment.point, b.lastSegment.point];
      let d = Infinity; for (const x of ea) for (const y of eb) d = Math.min(d, x.getDistance(y));
      a.join(b, d + 1e-6);                               // connect the two nearest ends, without also closing the loop
    }
    commit(); select([topOf(a)]);
  }

  // ------------------------------------------------------------------ colour
  function colourSets(items) {
    const f = new Map(), s = new Map();
    for (const l of leaves(items)) {
      if (l instanceof paper.Raster) continue;
      const a = hex(l.fillColor), b = hex(l.strokeColor);
      f.set(a, (f.get(a) || 0) + 1); s.set(b, (s.get(b) || 0) + 1);
    }
    return {f: [...f.entries()].sort((x, y) => y[1] - x[1]), s: [...s.entries()].sort((x, y) => y[1] - x[1])};
  }
  function applyColour(kind, val) {
    const targets = S.scopeAll || !S.sel.length ? allItems() : S.sel;
    if (!S.sel.length && !S.scopeAll) { S.style[kind] = val; renderSide(); return; }
    const from = kind === "fill" ? S.chipF : S.chipS, key = kind === "fill" ? "fillColor" : "strokeColor";
    const first = colourSets(targets)[kind === "fill" ? "f" : "s"][0];
    const target = from === undefined ? (first ? first[0] : null) : from;
    for (const l of leaves(targets)) {
      if (l instanceof paper.Raster || hex(l[key]) !== target) continue;
      l[key] = val; if (kind === "stroke" && val && !l.strokeWidth) l.strokeWidth = S.style.weight;
      const t = topOf(l); if (t && t.data) {}
    }
    if (kind === "fill") S.chipF = val; else S.chipS = val;
    commit(); drawUI(); renderSide();
  }
  function applyWeight(w) {
    for (const l of leaves(S.sel)) if (l.strokeColor) l.strokeWidth = w;
    S.style.weight = w; commit(); drawUI();
  }

  // ------------------------------------------------------------------ transforms by number / buttons
  function setBounds(x, y, w, h) {
    const b = selBounds(); if (!b) return;
    const sx = Math.max(w, 0.01) / b.width, sy = Math.max(h, 0.01) / b.height;
    const anyText = S.sel.some(i => kindOf(i) === "text");
    const k = anyText ? sx : null;
    for (const it of S.sel) { it.scale(k || sx, k || sy, b.topLeft); if (kindOf(it) === "text") it.data.text.size *= sx; }
    const nb = selBounds(); for (const it of S.sel) it.translate(new paper.Point(x - nb.x, y - nb.y));
    commit(); drawUI(); syncProps();
  }
  function rotateSel(deg) {
    const b = selBounds(); if (!b) return;
    for (const it of S.sel) { it.rotate(deg, b.center); if (kindOf(it) === "text") it.data.text.rot = ((it.data.text.rot || 0) + deg) % 360; }
    commit(); drawUI(); syncProps();
  }
  function flip(h) {
    const b = selBounds(); if (!b) return;
    for (const it of S.sel) { it.scale(h ? -1 : 1, h ? 1 : -1, b.center); if (kindOf(it) === "text") it.data.kind = "shape"; }
    commit(); drawUI();
  }
  function alignBoard(axis) {
    const b = selBounds(); if (!b) return;
    const dx = axis === "x" ? S.W / 2 - b.center.x : 0, dy = axis === "y" ? S.H / 2 - b.center.y : 0;
    for (const it of S.sel) it.translate(new paper.Point(dx, dy)); commit(); drawUI(); syncProps();
  }
  function order(kind) {
    for (const it of kind === "front" || kind === "up" ? S.sel.slice() : S.sel.slice().reverse()) {
      if (kind === "front") it.bringToFront(); else if (kind === "back") it.sendToBack();
      else if (kind === "up" && it.nextSibling) it.insertAbove(it.nextSibling); else if (kind === "down" && it.previousSibling) it.insertBelow(it.previousSibling);
    }
    commit(); renderSide();
  }
  function dup() {
    const made = S.sel.map(it => { const c = it.clone(); c.translate(new paper.Point(3, 3)); c.data = JSON.parse(JSON.stringify(it.data || {})); return c; });
    commit(); select(made);
  }
  function del() {
    if (S.tool === "nodes" && S.nodes.length) return deleteNodes();
    for (const it of S.sel) it.remove(); S.sel = []; S.nodes = []; commit(); drawUI(); renderSide();
  }

  // ------------------------------------------------------------------ groups
  function group() {
    const items = S.sel.filter(i => i.parent === L.art).sort((a, b) => a.index - b.index);
    if (items.length < 2) return toast_("Select two or more graphics to group them.");
    const top = items[items.length - 1].index, g = new paper.Group({applyMatrix: true, insert: false});
    for (const i of items) g.addChild(i);
    L.art.insertChild(Math.max(0, Math.min(top - (items.length - 1), L.art.children.length)), g);
    place(g, "group", "Group"); g.data = Object.assign({}, g.data, {open: true});
    commit(); select([g]);
  }
  function ungroup() {
    const out = [];
    for (const g of S.sel) {
      if (!(g instanceof paper.Group) || g.clipped || g.parent !== L.art) { out.push(g); continue; }
      const idx = g.index; g.applyMatrix = true;
      const kids = g.removeChildren(); L.art.insertChildren(idx, kids); g.remove();
      for (const k of kids) { k.name = (k.data && k.data.name) || k.name; out.push(k); }
    }
    commit(); select(out);
  }

  // ------------------------------------------------------------------ fill tool: click a hollow area to fill it
  async function rasterArt(ppm) {
    for (const c of L.ui.children) c.visible = false; L.bg.visible = false;
    let body; try { body = L.art.exportSVG({asString: true, precision: 3}); } finally { L.bg.visible = true; for (const c of L.ui.children) c.visible = true; }
    const w = Math.round(S.W * ppm), h = Math.round(S.H * ppm);
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${S.W} ${S.H}">${body}</svg>`;
    const url = URL.createObjectURL(new Blob([svg], {type: "image/svg+xml"})), img = new Image(); img.src = url; await img.decode();
    const c = document.createElement("canvas"); c.width = w; c.height = h; const g = c.getContext("2d", {willReadFrequently: true}); g.drawImage(img, 0, 0, w, h); URL.revokeObjectURL(url);
    return {w, h, data: g.getImageData(0, 0, w, h).data};
  }
  function dilate(m, w, h, r) {                                     // square grow of a 0/1 mask by r pixels (two passes)
    if (r < 1) return m;
    const t = new Uint8Array(m.length), o = new Uint8Array(m.length);
    for (let y = 0; y < h; y++) { let last = -1e9; const row = y * w; for (let x = 0; x < w; x++) { if (m[row + x]) last = x; if (x - last <= r) t[row + x] = 1; } last = 1e9; for (let x = w - 1; x >= 0; x--) { if (m[row + x]) last = x; if (last - x <= r) t[row + x] = 1; } }
    for (let x = 0; x < w; x++) { let last = -1e9; for (let y = 0; y < h; y++) { if (t[y * w + x]) last = y; if (y - last <= r) o[y * w + x] = 1; } last = 1e9; for (let y = h - 1; y >= 0; y--) { if (t[y * w + x]) last = y; if (last - y <= r) o[y * w + x] = 1; } }
    return o;
  }
  function floodRegion(ink, w, h, sx, sy) {                          // returns {mask, open}
    const mask = new Uint8Array(w * h), st = [sy * w + sx]; let open = false; mask[sy * w + sx] = 1;
    while (st.length) {
      const p = st.pop(), x = p % w, y = (p - x) / w;
      if (x === 0 || y === 0 || x === w - 1 || y === h - 1) open = true;
      if (x > 0 && !ink[p - 1] && !mask[p - 1]) { mask[p - 1] = 1; st.push(p - 1); }
      if (x < w - 1 && !ink[p + 1] && !mask[p + 1]) { mask[p + 1] = 1; st.push(p + 1); }
      if (y > 0 && !ink[p - w] && !mask[p - w]) { mask[p - w] = 1; st.push(p - w); }
      if (y < h - 1 && !ink[p + w] && !mask[p + w]) { mask[p + w] = 1; st.push(p + w); }
    }
    return {mask, open};
  }
  function traceMask(mask, w, h) {                                   // outlines (outer edges and holes) of a 0/1 mask as pixel-corner polygons
    const at = (x, y) => (x >= 0 && y >= 0 && x < w && y < h) ? mask[y * w + x] : 0, next = new Map(), W1 = w + 1;
    const add = (x0, y0, x1, y1) => { const k = y0 * W1 + x0; (next.get(k) || next.set(k, []).get(k)).push([x1, y1]); };
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (mask[y * w + x]) {
      if (!at(x, y - 1)) add(x, y, x + 1, y); if (!at(x + 1, y)) add(x + 1, y, x + 1, y + 1);
      if (!at(x, y + 1)) add(x + 1, y + 1, x, y + 1); if (!at(x - 1, y)) add(x, y + 1, x, y);
    }
    const loops = [];
    for (const [k0, outs] of next) while (outs.length) {
      const pts = []; let [x, y] = [k0 % W1, (k0 - k0 % W1) / W1], e = outs.pop();
      const sx = x, sy = y; pts.push([x, y]); x = e[0]; y = e[1];
      let guard = 0;
      while ((x !== sx || y !== sy) && guard++ < 4e6) { pts.push([x, y]); const o = next.get(y * W1 + x); if (!o || !o.length) break; const nx = o.pop(); x = nx[0]; y = nx[1]; }
      if (pts.length > 3) loops.push(pts);
    }
    return loops;
  }
  function dp(pts, eps) {                                            // Douglas-Peucker on a closed loop (iterative)
    const n = pts.length; if (n < 8) return pts;
    const keep = new Uint8Array(n); keep[0] = 1; let far = 0, fd = -1;
    for (let i = 1; i < n; i++) { const d = (pts[i][0] - pts[0][0]) ** 2 + (pts[i][1] - pts[0][1]) ** 2; if (d > fd) { fd = d; far = i; } }
    keep[far] = 1; const st = [[0, far], [far, n]];
    while (st.length) {
      const [a, b] = st.pop(), A = pts[a], B = pts[b % n]; let m = -1, md = eps;
      const dx = B[0] - A[0], dy = B[1] - A[1], L2 = dx * dx + dy * dy || 1;
      for (let i = a + 1; i < b; i++) { const P = pts[i], t = Math.max(0, Math.min(1, ((P[0] - A[0]) * dx + (P[1] - A[1]) * dy) / L2)), d = Math.hypot(P[0] - A[0] - t * dx, P[1] - A[1] - t * dy); if (d > md) { md = d; m = i; } }
      if (m >= 0) { keep[m] = 1; st.push([a, m], [m, b]); }
    }
    return pts.filter((_, i) => keep[i]);
  }
  async function fillAt(pt, shift) {
    if (S.busy) return; S.busy = true;
    try {
      const under = hitItem(pt);
      if (under && kindOf(under) === "fill") { under.fillColor = S.fillC; commit(); select([under]); return; }          // clicking an existing fill recolours it
      const ppm = Math.min(8, 3000 / Math.max(S.W, S.H)), {w, h, data} = await rasterArt(ppm);
      const px0 = Math.floor(pt.x * ppm), py0 = Math.floor(pt.y * ppm);
      if (px0 < 0 || py0 < 0 || px0 >= w || py0 >= h) return toast_("Click inside the page.");
      let ink = new Uint8Array(w * h); for (let i = 0; i < ink.length; i++) ink[i] = data[i * 4 + 3] > 40 ? 1 : 0;
      if (ink[py0 * w + px0]) return toast_("Click inside a hollow area, not on a shape.");
      const gap = Math.round(S.fillGap * ppm), grow = Math.round(S.fillGrow * ppm);
      ink = dilate(ink, w, h, gap);
      if (ink[py0 * w + px0]) return toast_("That spot is closed in by the Close gaps setting. Lower it and try again.");
      const {mask, open} = floodRegion(ink, w, h, px0, py0);
      if (open) return toast_("That area is open to the outside. Raise “Close gaps”, or close it with the Pen first.");
      const out = dilate(mask, w, h, gap + grow), loops = traceMask(out, w, h).map(l => dp(l, 0.8)).filter(l => l.length > 2);
      if (!loops.length) return toast_("Nothing to fill there.");
      const cp = new paper.CompoundPath({insert: false, fillRule: "evenodd"});
      for (const l of loops) cp.addChild(new paper.Path({segments: l.map(p => [p[0] / ppm, p[1] / ppm]), closed: true, insert: false}));
      cp.fillColor = S.fillC; cp.strokeColor = null; place(cp, "fill", "Fill");
      // put it directly behind the graphic that was clicked next to (the top-most item whose box holds the click)
      const host = allItems().reverse().find(i => i.visible && i.bounds.contains(pt));
      const inGroup = host && kindOf(host) === "group";
      if (host && shift && !inGroup) { const g = new paper.Group({applyMatrix: true, insert: false}); L.art.insertChild(host.index, g); g.addChild(cp); g.addChild(host); place(g, "group", "Group"); g.data = Object.assign({}, g.data, {open: true}); }
      else if (inGroup && shift) host.insertChild(0, cp);
      else if (host) L.art.insertChild(host.index, cp); else L.art.insertChild(0, cp);
      commit(); select([topOf(cp)]);
    } catch (e) { toast_("Could not fill that area: " + e.message); }
    finally { S.busy = false; }
  }

  // ------------------------------------------------------------------ pointer tool
  function startSelectDrag(e) {
    const pt = e.point, b = selBounds();
    if (b) {
      const rp = rotPoint(b);
      if (rp.getDistance(pt) < px(9)) { S.drag = {type: "rotate", c: b.center, a0: pt.subtract(b.center).angle, tot: 0}; return; }
      for (const h of handles(b)) if (h.p.getDistance(pt) < px(9)) {
        S.drag = {type: "scale", ix: h.ix, iy: h.iy, b0: b.clone(), sizes: S.sel.map(i => i.bounds.height), done: 1}; return;
      }
    }
    const it = hitItem(pt);
    if (it) {
      if (e.event.shiftKey) { S.sel.includes(it) ? select(S.sel.filter(i => i !== it)) : select([it], true); }
      else if (!S.sel.includes(it)) select([it]);
      if (S.sel.includes(it)) S.drag = {type: "move", start: pt, b0: selBounds().clone(), tot: new paper.Point(0, 0), moved: false, guides: []};
      return;
    }
    if (!e.event.shiftKey) select([]);
    S.drag = {type: "marquee", a: pt, add: e.event.shiftKey};
  }
  function dragSelect(e) {
    const d = S.drag; if (!d) return;
    if (d.type === "move") {
      d.moved = true; const tot = e.point.subtract(d.start); let nb = d.b0.clone(); nb.point = nb.point.add(tot);
      d.guides = []; let sx = 0, sy = 0;
      if (S.snap && !e.event.altKey) {
        const th = px(5), xs = [0, S.W / 2, S.W], ys = [0, S.H / 2, S.H];
        for (const mx of [nb.left, nb.center.x, nb.right]) for (const gx of xs) if (Math.abs(mx - gx) < th && !sx) { sx = gx - mx; d.guides.push([new paper.Point(gx, -50), new paper.Point(gx, S.H + 50)]); }
        for (const my of [nb.top, nb.center.y, nb.bottom]) for (const gy of ys) if (Math.abs(my - gy) < th && !sy) { sy = gy - my; d.guides.push([new paper.Point(-50, gy), new paper.Point(S.W + 50, gy)]); }
      }
      const want = d.b0.point.add(tot).add([sx, sy]), cur = selBounds().point, dl = want.subtract(cur);
      for (const it of S.sel) it.translate(dl); drawUI();
    } else if (d.type === "scale") {
      const b = d.b0, {ix, iy} = d, ax = ix < 0 ? b.right : ix > 0 ? b.left : b.center.x, ay = iy < 0 ? b.bottom : iy > 0 ? b.top : b.center.y;
      const hx = ix < 0 ? b.left : ix > 0 ? b.right : b.center.x, hy = iy < 0 ? b.top : iy > 0 ? b.bottom : b.center.y;
      // Always measured against the box as it was when the drag began, so the size follows the pointer exactly instead of drifting.
      let tx = ix ? (e.point.x - ax) / ((hx - ax) || 1e-6) : 1, ty = iy ? (e.point.y - ay) / ((hy - ay) || 1e-6) : 1;
      const uniform = (ix && iy) ? !e.event.shiftKey === S.lock : false, anyText = S.sel.some(i => kindOf(i) === "text");
      if (uniform || anyText) { const t = (ix && iy) ? (Math.abs(tx - 1) > Math.abs(ty - 1) ? tx : ty) : (ix ? tx : ty); tx = ty = t; }
      tx = Math.max(tx, 0.01); ty = Math.max(ty, 0.01);
      d.tx = d.tx || 1; d.ty = d.ty || 1;
      const sx = tx / d.tx, sy = ty / d.ty; d.tx = tx; d.ty = ty;
      for (const it of S.sel) it.scale(sx, sy, new paper.Point(ax, ay));
      d.done = tx;
      if (d.raf) return;
      d.raf = true; requestAnimationFrame(() => { d.raf = false; drawUI(); syncProps(); });
    } else if (d.type === "rotate") {
      let tot = e.point.subtract(d.c).angle - d.a0; if (e.event.shiftKey) tot = Math.round(tot / 15) * 15;
      const dl = tot - d.tot; d.tot = tot;
      for (const it of S.sel) it.rotate(dl, d.c); drawUI(); syncProps();
    } else if (d.type === "marquee") {
      drawUI(); L.ui.addChild(new paper.Path.Rectangle({rectangle: new paper.Rectangle(d.a, e.point), strokeColor: "#404040", strokeWidth: 1, strokeScaling: false, dashArray: [4, 3], fillColor: new paper.Color(0, 0, 0, 0.06)}));
    }
  }
  function upSelect(e) {
    const d = S.drag; S.drag = null; if (!d) return;
    if (d.type === "move") { if (d.moved) commit(); drawUI(); }
    else if (d.type === "scale") {
      S.sel.forEach(i => { if (kindOf(i) === "text") i.data.text.size *= d.done; });
      commit(); drawUI();
    }
    else if (d.type === "rotate") {
      S.sel.forEach(i => { if (kindOf(i) === "text") i.data.text.rot = ((i.data.text.rot || 0) + d.tot) % 360; });
      if (d.tot) commit(); drawUI();
    } else if (d.type === "marquee") {
      const r = new paper.Rectangle(d.a, e.point);
      if (r.width > px(3) || r.height > px(3)) select(allItems().filter(i => inView(i) && i.bounds.intersects(r)), d.add); else drawUI();
    }
  }

  // ------------------------------------------------------------------ tool events
  function setup() {
    paper.setup(cv);
    L.bg = new paper.Layer(); L.art = new paper.Layer(); L.ui = new paper.Layer();
    paper.settings.hitTolerance = 0;
    drawBoard(); S.hist = [snap()]; S.hi = 0;
    T = new paper.Tool();
    T.onMouseDown = e => {
      cv.focus && cv.focus({preventScroll: true});
      if (S.space || e.event.button === 1) { S.drag = {type: "pan"}; return; }
      if (e.event.button !== 0) return;
      ({select: startSelectDrag, nodes: nodesDown, pen: penDown,
        text: ev => { if (editor) closeEditor(false); else openEditor(ev.point); }, shape: ev => { S.drag = {type: "shape", a: ev.point}; }, fill: ev => fillAt(ev.point, ev.event.shiftKey),})[S.tool](e);
    };
    T.onMouseDrag = e => {
      const d = S.drag;
      if (d && d.type === "pan") { paper.view.center = paper.view.center.add(e.downPoint.subtract(e.point)); drawUI(); return; }
      if (S.tool === "select") dragSelect(e);
      else if (S.tool === "nodes") nodesDrag(e);
      else if (S.tool === "pen") penDrag(e);
      else if (d && d.type === "shape") { drawUI(); const r = new paper.Rectangle(d.a, e.point); L.ui.addChild(new paper.Path.Rectangle({rectangle: r, strokeColor: "#404040", strokeWidth: 1, strokeScaling: false, dashArray: [4, 3]})); }
    };
    T.onMouseUp = e => {
      const d = S.drag;
      if (d && d.type === "pan") { S.drag = null; return; }
      if (S.tool === "select") upSelect(e);
      else if (S.tool === "nodes") nodesUp(e);
      else if (S.tool === "pen") penUp(e);
      else if (d && d.type === "shape") { S.drag = null; const p = makeShape(d.a, e.point, e.event.shiftKey); if (p) { commit(); select([p]); } else drawUI(); }
    };
    T.onMouseMove = e => {
      S.mouse = e.point; if (S.pen) drawUI();
      cv.style.cursor = S.space ? "grab" : ({select: "default", nodes: "default", pen: "crosshair", text: "text", shape: "crosshair", fill: "crosshair"})[S.tool];
    };
    cv.addEventListener("dblclick", e => {
      if (S.tool === "pen" && S.pen) { penFinish(false); return; }
      if (S.tool === "select") { const it = hitItem(paper.view.viewToProject(new paper.Point(e.offsetX, e.offsetY))); if (it && kindOf(it) === "text") { select([it]); openEditor(null, it); } }
    });
    cv.addEventListener("wheel", e => {
      e.preventDefault(); const r = cv.getBoundingClientRect();
      setZoom(zoom() * Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)), new paper.Point(e.clientX - r.left, e.clientY - r.top));
    }, {passive: false});
    cv.addEventListener("dragover", e => { e.preventDefault(); });
    cv.addEventListener("drop", async e => {
      e.preventDefault(); e.stopPropagation();
      const r = cv.getBoundingClientRect(), at = paper.view.viewToProject(new paper.Point(e.clientX - r.left, e.clientY - r.top));
      const name = e.dataTransfer.getData("text/x-boot-lib");
      if (name) return addFromLibrary(name, at);
      for (const f of e.dataTransfer.files) await addFile(f, at);
    });
    document.addEventListener("keydown", keydown); document.addEventListener("keyup", e => { if (e.code === "Space") S.space = false; });
    new ResizeObserver(resize).observe(cv.parentElement);
    S.ready = true;
  }
  function keydown(e) {
    if (typeof tool === "undefined" || tool !== "compose") return;
    const tg = e.target; if (tg && (tg.isContentEditable || (/^(INPUT|TEXTAREA|SELECT)$/.test(tg.tagName) && tg.type !== "checkbox"))) return;
    const mod = e.metaKey || e.ctrlKey;
    if (e.code === "Space") { S.space = true; e.preventDefault(); return; }
    if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
    if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); return; }
    if (mod && e.key.toLowerCase() === "g") { e.preventDefault(); e.shiftKey ? ungroup() : group(); return; }
    if (mod && e.key.toLowerCase() === "d") { e.preventDefault(); dup(); return; }
    if (mod && e.key.toLowerCase() === "a") { e.preventDefault(); select(allItems().filter(inView)); return; }
    if (e.key === "Enter" && S.pen) { penFinish(false); return; }
    if (e.key === "Escape") { if (S.pen) penFinish(false); else select([]); return; }
    if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); if (S.sel.length) del(); return; }
    const key = {v: "select", a: "nodes", p: "pen", t: "text", s: "shape", f: "fill"}[e.key.toLowerCase()];
    if (key && !mod) { setTool(key); return; }
    const step = e.shiftKey ? 1 : 0.1, dir = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[e.key];
    if (dir && (S.sel.length || S.nodes.length)) {
      e.preventDefault(); const dl = new paper.Point(dir[0] * step, dir[1] * step);
      if (S.tool === "nodes" && S.nodes.length) { for (const s of S.nodes) s.point = s.point.add(dl); markShape(S.sel); } else for (const it of S.sel) it.translate(dl);
      commit(); drawUI(); syncProps();
    }
  }

  function setTool(t) {
    if (S.pen) penFinish(false);
    S.tool = t; S.drag = null; S.nodes = [];
    root.querySelectorAll("[data-ct]").forEach(b => b.classList.toggle("on", b.dataset.ct === t));
    if (t === "text") ensureFonts().then(renderSide);
    drawUI(); renderSide();
  }

  // ------------------------------------------------------------------ library drawer
  async function loadLibrary() {
    try { const j = await (await fetch("/api/library")).json(); S.lib = j.items; S.libFolder = j.folder; renderLib(); } catch (e) { /* offline library is just empty */ }
  }
  function renderLib() {
    if (!root) return;
    const strip = root.querySelector("#clibStrip"), q = S.libQ.toLowerCase();
    root.querySelector("#clibN").textContent = `(${S.lib.length})`; root.querySelector("#clibFolder").textContent = S.libFolder;
    strip.innerHTML = "";
    const items = S.lib.filter(i => !q || i.name.toLowerCase().includes(q));
    if (!items.length) strip.append(el("div", "hint", S.lib.length ? "Nothing matches." : "Nothing here yet. Everything you convert in the Convert tab is saved here automatically."));
    for (const it of items) {
      const t = el("div", "lt"); t.draggable = true;
      t.innerHTML = `<div class="lth"><img loading="lazy" alt="" src="/api/library/file?name=${encodeURIComponent(it.name)}"></div><div class="ltn">${esc(it.name.replace(/\.svg$/i, ""))}</div><span class="ltx" title="Delete from library">×</span>`;
      t.ondragstart = e => { e.dataTransfer.setData("text/x-boot-lib", it.name); e.dataTransfer.effectAllowed = "copy"; };
      t.onclick = e => { if (!e.target.classList.contains("ltx")) addFromLibrary(it.name); };
      t.querySelector(".ltx").onclick = async e => {
        e.stopPropagation();
        await fetch("/api/library/delete", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: it.name})}); loadLibrary();
      };
      t.querySelector(".ltn").ondblclick = e => {
        e.stopPropagation(); const n = e.target, old = n.textContent; n.contentEditable = "true"; n.focus(); selectAllIn(n);
        let finished = false;
        const done = async ok => {
          if (finished) return; finished = true;
          n.contentEditable = "false"; const v = n.textContent.trim();
          if (ok && v && v !== old) await fetch("/api/library/rename", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: it.name, to: v})});
          loadLibrary();
        };
        n.onkeydown = ev => { if (ev.key === "Enter") { ev.preventDefault(); done(true); } if (ev.key === "Escape") done(false); }; n.onblur = () => done(true);
      };
      strip.append(t);
    }
  }

  function selectAllIn(n) { const r = document.createRange(); r.selectNodeContents(n); const g = getSelection(); g.removeAllRanges(); g.addRange(r); }

  // ------------------------------------------------------------------ side panel builders
  const row = (label, node) => { const c = el("div", "cell"); c.append(el("div", "lab", label)); if (node) c.append(node); return c; };
  function seg(opts, cur, cb, wrap) {
    const s = el("div", "cells" + (wrap ? " wrap" : "")); s.style.borderTop = "0";
    for (const [v, l] of opts) { const b = el("span", v === cur ? "on" : "", l); b.onclick = () => cb(v); s.append(b); }
    return s;
  }
  function btns(list) {
    const s = el("div", "cells wrap");
    for (const [l, f, dis] of list) { const b = el("span", "", l); if (dis) b.style.opacity = ".4"; else b.onclick = f; s.append(b); }
    return s;
  }
  function field(label, val, cb, w) {
    const d = el("div", "row"); d.style.margin = "3px 0"; d.append(el("span", "lab", label));
    const i = el("input"); i.type = "text"; i.value = val; i.style.width = (w || 84) + "px"; i.style.textAlign = "right";
    i.onchange = () => cb(i.value); i.onkeydown = e => { if (e.key === "Enter") i.blur(); }; d.append(i); d._i = i; return d;
  }
  function box(title, ...kids) { const b = el("div", "box"); b.append(el("div", "bh", title)); for (const k of kids) if (k) b.append(k); return b; }

  const propEls = {};
  function syncProps() {
    const b = selBounds(); if (!b) return;
    const set = (k, v) => { if (propEls[k] && document.activeElement !== propEls[k]) propEls[k].value = fu(v); };
    set("x", b.x); set("y", b.y); set("w", b.width); set("h", b.height);
  }
  function renderLayersOnly() { const o = root && root.querySelector("#clayers"); if (o) o.replaceWith(layersBox()); }
  const thumbCache = new Map();
  function thumb(it) {
    const b = it.bounds; if (!b || !b.width || !b.height) return "";
    const lv = leaves([it]), segs = lv.reduce((n, l) => n + (l.segments ? l.segments.length : 0), 0);
    const key = it.id + "|" + b.toString() + "|" + segs + "|" + lv.map(l => (l.fillColor ? l.fillColor.toCSS(true) : "-") + (l.strokeColor ? l.strokeColor.toCSS(true) : "-")).join(",");
    if (thumbCache.has(key)) return thumbCache.get(key);
    const pad = Math.max(b.width, b.height) * 0.04 + 0.5;
    let inner;
    if (it instanceof paper.Raster || segs > 4000) inner = `<rect x="${b.x}" y="${b.y}" width="${b.width}" height="${b.height}" fill="${(lv[0] && lv[0].fillColor) ? lv[0].fillColor.toCSS(true) : "#888"}"/>`;
    else { try { inner = it.exportSVG({asString: true, precision: 1}); } catch (e) { inner = ""; } }
    const out = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${b.x - pad} ${b.y - pad} ${b.width + 2 * pad} ${b.height + 2 * pad}" preserveAspectRatio="xMidYMid meet">${inner}</svg>`;
    if (thumbCache.size > 300) thumbCache.clear();
    thumbCache.set(key, out); return out;
  }
  function layersBox() {
    const b = box("Layers"); b.id = "clayers";
    const kids = L.art.children.slice().reverse();
    if (!kids.length) b.append(el("div", "cell", `<div class="hint" style="margin:0">Empty page. Hover the Library tab at the bottom and click a graphic, or drop a file here.</div>`));
    for (const it of kids) {
      const r = el("div", "cell lyr" + (S.sel.includes(it) ? " on" : ""));
      const isG = kindOf(it) === "group" && it instanceof paper.Group, open = isG && !(it.data && it.data.open === false);
      r.innerHTML = `${isG ? `<span class="lb" data-a="o" title="Open or close the group">${open ? "▾" : "▸"}</span>` : ""}<span class="th">${thumb(it)}</span><span class="ln" title="Double-click to rename">${esc((it.data && it.data.name) || it.name || "Item")}</span><span class="lb" data-a="v" title="Show or hide">${it.visible ? "◉" : "○"}</span><span class="lb" data-a="l" title="Lock">${it.locked ? "🔒" : "🔓"}</span>`;
      r.onclick = e => {
        const a = e.target.dataset.a;
        if (a === "o") { it.data = Object.assign({}, it.data, {open: it.data && it.data.open === false}); renderSide(); return; }
        if (a === "v") { it.visible = !it.visible; if (!it.visible) S.sel = S.sel.filter(s => s !== it); commit(); drawUI(); renderSide(); return; }
        if (a === "l") { it.locked = !it.locked; if (it.locked) S.sel = S.sel.filter(s => s !== it); commit(); drawUI(); renderSide(); return; }
        if (!it.locked) select([it], e.shiftKey);
      };
      r.querySelector(".ln").ondblclick = e => {
        e.stopPropagation(); const n = e.target; n.contentEditable = "true"; n.focus(); selectAllIn(n);
        let fin = false; const done = () => { if (fin) return; fin = true; n.contentEditable = "false"; it.data = Object.assign({}, it.data, {name: n.textContent.trim() || "Item"}); it.name = it.data.name; commit(); };
        n.onblur = done; n.onkeydown = ev => { if (ev.key === "Enter") { ev.preventDefault(); n.blur(); } };
      };
      b.append(r);
      if (open) for (const k of it.children.slice().reverse()) {
        const c = el("div", "cell lyr" + (S.sel.includes(it) ? " on" : "")); c.style.paddingLeft = "30px"; if (!S.sel.includes(it)) c.style.background = "#cfcfcf";
        c.innerHTML = `<span class="th">${thumb(k)}</span><span class="ln">${esc((k.data && k.data.name) || k.name || "Item")}</span><span class="lb" data-a="v" title="Show or hide">${k.visible ? "◉" : "○"}</span>`;
        c.onclick = e => { if (e.target.dataset.a === "v") { k.visible = !k.visible; commit(); drawUI(); renderSide(); return; } if (!it.locked) select([it], e.shiftKey); };
        b.append(c);
      }
    }
    return b;
  }
  function boardBox() {
    const b = box("Artboard");
    const cur = Object.entries(SIZES).find(([, [a, c]]) => (S.W === c && S.H === a) || (S.W === a && S.H === c));
    const land = S.W >= S.H;
    b.append(seg(Object.keys(SIZES).map(k => [k, k]), cur ? cur[0] : "", k => { const [a, c] = SIZES[k]; setBoard(land ? c : a, land ? a : c); }));
    b.append(seg([["landscape", "Landscape"], ["portrait", "Portrait"]], land ? "landscape" : "portrait", o => { if ((o === "landscape") !== land) setBoard(S.H, S.W); }));
    const c = el("div", "cell");
    c.append(field("Width", fu(S.W), v => setBoard(tu(v), S.H)), field("Height", fu(S.H), v => setBoard(S.W, tu(v))));
    const u = el("div", "row"); u.append(el("span", "lab", "Units")); u.append(seg([["mm", "mm"], ["in", "inch"]], S.units, v => { S.units = v; renderSide(); })); u.lastChild.style.width = "120px"; u.lastChild.style.border = "1px solid #000";
    c.append(u, el("div", "hint", "Any size from 20 to 1200 mm. Use the presets or type your own.")); b.append(c);
    return b;
  }
  function propsBox() {
    if (!S.sel.length) return null;
    const b = el("div", "box"), kinds = [...new Set(S.sel.map(kindOf))];
    b.append(el("div", "bh", S.sel.length === 1 ? ((S.sel[0].data && S.sel[0].data.name) || "Selected") : `${S.sel.length} selected`));
    const c = el("div", "cell"), bb = selBounds();
    const mk = (k, l, f) => { const r = field(l, fu(bb[k === "w" ? "width" : k === "h" ? "height" : k]), f); propEls[k] = r._i; return r; };
    c.append(mk("x", "X", v => setBounds(tu(v), bb.y, bb.width, bb.height)), mk("y", "Y", v => setBounds(bb.x, tu(v), bb.width, bb.height)),
      mk("w", "Width", v => { const w = tu(v); setBounds(bb.x, bb.y, w, S.lock ? bb.height * w / bb.width : bb.height); }),
      mk("h", "Height", v => { const h = tu(v); setBounds(bb.x, bb.y, S.lock ? bb.width * h / bb.height : bb.width, h); }));
    const l = el("label", "chk", `<input type="checkbox"><span>Keep proportions</span>`); l.querySelector("input").checked = S.lock; l.querySelector("input").onchange = e => { S.lock = e.target.checked; }; c.append(l);
    c.append(field("Rotate by (°)", "0", v => { rotateSel(+v || 0); renderSide(); }));
    b.append(c);
    b.append(btns([["Flip ↔", () => flip(true)], ["Flip ↕", () => flip(false)], ["Centre ↔", () => alignBoard("x")], ["Centre ↕", () => alignBoard("y")]]));
    b.append(btns([["To front", () => order("front")], ["Forward", () => order("up")], ["Backward", () => order("down")], ["To back", () => order("back")]]));
    b.append(btns([["Duplicate", dup], ["Delete", del]]));
    b.append(btns([["Group", group, S.sel.filter(i => i.parent === L.art).length < 2], ["Ungroup", ungroup, !S.sel.some(i => i instanceof paper.Group && !i.clipped)]]));
    return b;
  }
  function colourBox() {
    const b = el("div", "box"), hasSel = S.sel.length > 0, targets = S.scopeAll || !hasSel ? allItems() : S.sel;
    b.append(el("div", "bh", hasSel ? "Colour of selection" : "Colour for new shapes"));
    const sets = hasSel || S.scopeAll ? colourSets(targets) : {f: [[S.style.fill, 1]], s: [[S.style.stroke, 1]]};
    const chips = (kind, list, cur) => {
      const w = el("div", "chips");
      const sel = cur === undefined ? (list[0] ? list[0][0] : null) : cur;
      for (const [v, n] of list) {
        const c = el("span", "chip" + (v === sel ? " on" : ""), ""); c.title = v ? v + (n > 1 ? ` (${n} shapes)` : "") : "none";
        c.style.background = v || "repeating-linear-gradient(135deg,#fff 0 4px,#d33 4px 6px)";
        c.onclick = () => { if (kind === "fill") S.chipF = v; else S.chipS = v; renderSide(); }; w.append(c);
      }
      return w;
    };
    const part = (kind, label) => {
      const c = el("div", "cell"); c.append(el("div", "lab", label));
      c.append(chips(kind, sets[kind === "fill" ? "f" : "s"], kind === "fill" ? S.chipF : S.chipS));
      const sw = el("div", "chips");
      for (const col of [...SWATCHES, null]) {
        const s = el("span", "chip sw2", ""); s.title = col || "none"; s.style.background = col || "repeating-linear-gradient(135deg,#fff 0 4px,#d33 4px 6px)";
        s.onclick = () => applyColour(kind, col); sw.append(s);
      }
      const pick = el("input"); pick.type = "color"; pick.title = "Any colour"; pick.value = (sets[kind === "fill" ? "f" : "s"][0] || ["#000000"])[0] || "#000000";
      pick.onchange = () => applyColour(kind, pick.value); sw.append(pick); c.append(sw);
      return c;
    };
    b.append(part("fill", "Fill"), part("stroke", "Outline"));
    const wt = el("div", "cell"); wt.append(field("Outline weight (mm)", num(S.style.weight, 2), v => { const w = Math.max(+v || 0.1, 0.01); if (hasSel) applyWeight(w); else { S.style.weight = w; } }, 70));
    if (hasSel) { const l = el("label", "chk", `<input type="checkbox"><span>Change this colour on the whole page</span>`); l.querySelector("input").checked = S.scopeAll; l.querySelector("input").onchange = e => { S.scopeAll = e.target.checked; renderSide(); }; wt.append(l); }
    b.append(wt);
    const cols = colourSets(allItems()).f.filter(x => x[0]).length;
    b.append(el("div", "cell", `<div class="hint" style="margin:0">Colours on this page: ${cols}. In Print, each colour becomes its own ink (Spot colour) or is split into CMYK.</div>`));
    return b;
  }
  function textBox() {
    const it = S.sel.length === 1 && kindOf(S.sel[0]) === "text" ? S.sel[0] : null;
    if (S.tool !== "text" && !it) return null;
    const d = it ? it.data.text : S.text, b = el("div", "box");
    b.append(el("div", "bh", it ? "Text" : "Text tool"));
    const c = el("div", "cell");
    if (!it) c.append(el("div", "hint", "Click on the page to add text. Change it here afterwards."));
    const ta = el("textarea"); ta.value = d.text; ta.style.height = "64px"; ta.style.fontFamily = "inherit"; ta.style.fontSize = "15px";
    ta.oninput = () => { if (it) retext(it, {text: ta.value}); else S.text.text = ta.value; };
    c.append(ta);
    const fs = el("select"); fs.style.cssText = "width:100%;margin-top:6px;border:1px solid #000;padding:4px;background:#fff";
    for (const f of S.fonts || []) { const o = el("option", "", esc(f.family + " — " + f.style)); o.value = f.id; fs.append(o); }
    const curFont = it ? it.data.text.font : (S.font && S.font.id); fs.value = curFont;
    fs.onchange = () => { const f = (S.fonts || []).find(x => x.id == fs.value); S.font = f; try { localStorage.setItem("boottool.font", f.family + " " + f.style); } catch (e) {} if (it) { it.data.text.font = f.id; it.data.text.fname = f.family + " " + f.style; retextFont(it, f.id); } };
    c.append(fs);
    c.append(field("Size (pt)", num(d.size, 1), v => { if (it) retext(it, {size: Math.max(+v || 1, 1)}); else S.text.size = Math.max(+v || 1, 1); }, 70));
    c.append(field("Letter spacing", d.spacing, v => { if (it) retext(it, {spacing: +v || 0}); else S.text.spacing = +v || 0; }, 70));
    c.append(seg([["left", "Left"], ["center", "Centre"], ["right", "Right"]], d.align, a => { if (it) retext(it, {align: a}); else S.text.align = a; renderSide(); }));
    c.append(el("div", "hint", `Need another font? Put .ttf or .otf files in <b>${esc(S.fontsFolder || "~/BootTool/Fonts")}</b> and press Refresh.`));
    const rf = el("span", "bsm", "Refresh fonts"); rf.style.display = "inline-block"; rf.style.marginTop = "6px"; rf.onclick = async () => { const j = await (await fetch("/api/fonts?refresh=1")).json(); S.fonts = j.fonts; renderSide(); }; c.append(rf);
    b.append(c);
    if (it) b.append(btns([["Turn into a plain shape", () => { it.data.kind = "shape"; commit(); renderSide(); }]]));
    return b;
  }
  function retextFont(it, fid) { S.font = (S.fonts || []).find(f => f.id == fid) || S.font; retext(it, {}); }
  function toolBox() {
    if (S.tool === "shape") return box("Shape", seg([["rect", "Rectangle"], ["ellipse", "Ellipse"], ["polygon", "Polygon"], ["star", "Star"]], S.shape, v => { S.shape = v; renderSide(); }, true),
      (S.shape === "polygon" || S.shape === "star") ? (() => { const c = el("div", "cell"); c.append(field(S.shape === "star" ? "Points" : "Sides", S.sides, v => { S.sides = Math.min(Math.max(Math.round(+v) || 5, 3), 24); })); return c; })() : null,
      el("div", "cell", `<div class="hint" style="margin:0">Drag on the page. Hold Shift for a square or circle. Colours come from the Colour box.</div>`));
    if (S.tool === "fill") {
      const sw = el("div", "cell"), row = el("div"); row.style.cssText = "display:flex;flex-wrap:wrap;gap:6px;align-items:center";
      for (const c of ["#ffffff", ...SWATCHES.filter(x => x !== "#ffffff")]) { const x = el("span", "sw"); x.style.background = c; x.style.cursor = "pointer"; if (c.toLowerCase() === S.fillC.toLowerCase()) x.style.outline = "2px solid #ec008c"; x.onclick = () => { S.fillC = c; renderSide(); }; row.append(x); }
      const ci = el("input"); ci.type = "color"; ci.value = S.fillC; ci.oninput = () => { S.fillC = ci.value; }; ci.onchange = () => renderSide(); row.append(ci); sw.append(row);
      const g = el("div", "cell"); g.append(field("Grow by (mm)", S.fillGrow, v => { S.fillGrow = Math.min(Math.max(+v || 0, 0), 3); }, 70), field("Close gaps (mm)", S.fillGap, v => { S.fillGap = Math.min(Math.max(+v || 0, 0), 3); }, 70));
      return box("Fill", sw, g, el("div", "cell", `<div class="hint" style="margin:0">Click inside a hollow area to fill it. The fill goes directly behind the graphic, as its own layer. Hold Shift to group the fill with the graphic. Click an existing fill to change its colour. <b>Grow</b> tucks the fill under the lines. <b>Close gaps</b> lets it treat tiny breaks in an outline as closed.</div>`));
    }
    if (S.tool === "pen") return box("Pen",
      el("div", "cell", `<div class="hint" style="margin:0;line-height:1.5">Click: corner point<br>Click and drag: smooth curve<br>Click the first point: close the shape<br>Enter or double-click: finish an open line<br>Shift: straight or 45° lines<br>With a path selected: click a point to delete it, click a line to add one</div>`),
      (() => { const l = el("label", "chk", `<input type="checkbox"><span>Fill closed shapes</span>`); l.style.padding = "0 12px 9px"; const i = l.querySelector("input"); i.checked = S.penFill; i.onchange = () => { S.penFill = i.checked; }; return l; })());
    if (S.tool === "nodes") {
      const n = S.nodes.length, kids = [];
      kids.push(el("div", "cell", `<div class="hint" style="margin:0">${S.sel.length ? `${n} point${n === 1 ? "" : "s"} selected. Drag a point, or a round handle to bend the curve. Shift-click or drag a box to pick several. Double-click a line to add a point. Hold Alt to bend one handle alone.` : "Select a shape first, then edit its points."}</div>`));
      kids.push(seg([["corner", "Corner"], ["smooth", "Smooth"], ["symmetric", "Symmetric"]], "", t => { if (n) setNodeType(t); }));
      kids.push(btns([["Delete point", deleteNodes, !n], ["Break path here", breakAt, n !== 1]]));
      const c = el("div", "cell"); c.append(field("Simplify (mm)", S.simplify, v => { S.simplify = Math.max(+v || 0.05, 0.001); }, 70)); kids.push(c);
      kids.push(btns([["Simplify selected shapes", simplifySel, !S.sel.length]]));
      const total = S.sel.length ? paths(S.sel).reduce((a, p) => a + p.segments.length, 0) : 0;
      if (total) kids.push(el("div", "cell", `<div class="hint" style="margin:0">${total.toLocaleString()} points in the selection${total > 4000 ? ". Only those in view are shown." : "."}</div>`));
      return box("Nodes", ...kids);
    }
    return null;
  }
  function combineBox() {
    if (!S.sel.length) return null;
    const n = leaves(S.sel).length;
    return box("Combine shapes",
      btns([["Unite", () => combine("unite"), n < 2], ["Subtract", () => combine("subtract"), n < 2], ["Intersect", () => combine("intersect"), n < 2], ["Exclude", () => combine("exclude"), n < 2],
        ["Split", () => combine("split"), n < 2], ["Combine", () => combine("combine"), n < 2], ["Separate", () => combine("release")], ["Split letters", () => combine("letters")], ["Join ends", () => combine("join")]]),
      el("div", "cell", `<div class="hint" style="margin:0"><b>Unite</b>: overlapping shapes become one outline. <b>Subtract</b>: the front shape punches out the shapes behind it. <b>Intersect</b>: keep only the overlap. <b>Exclude</b>: keep what does not overlap. <b>Split</b>: the front shape cuts the others into inside and outside pieces. <b>Combine</b>: one shape with holes. <b>Separate</b>: undo Combine. <b>Split letters</b>: turn text into one shape per letter, keeping the holes in letters like O and B. <b>Join ends</b>: connect two open lines, or close one.</div>`));
  }
  function outputBox() {
    const b = el("div", "box"); b.append(el("div", "bh", "Output"));
    const go = el("button", "btn go", "Send to Print →"); go.onclick = sendToPrint;
    const sv = el("button", "btn", "Save as SVG"); sv.onclick = saveSvg;
    b.append(go, sv); return b;
  }
  function sideBoxes() {
    for (const k in propEls) delete propEls[k];
    return [boardBox(), layersBox(), propsBox(), colourBox(), textBox(), toolBox(), combineBox(), outputBox()].filter(Boolean);
  }
  function renderSide() { if (typeof tool !== "undefined" && tool === "compose" && typeof renderSideHook === "function") renderSideHook(); }

  // ------------------------------------------------------------------ output
  function svgString(ppi) {
    for (const c of L.ui.children) c.visible = false; L.bg.visible = false;
    const body = L.art.exportSVG({asString: true, precision: 3}); L.bg.visible = true; for (const c of L.ui.children) c.visible = true;
    const px_ = ppi ? ` width="${Math.round(S.W / 25.4 * ppi)}" height="${Math.round(S.H / 25.4 * ppi)}"` : ` width="${S.W}mm" height="${S.H}mm"`;
    return `<svg xmlns="http://www.w3.org/2000/svg"${px_} viewBox="0 0 ${S.W} ${S.H}">${ppi ? `<rect width="${S.W}" height="${S.H}" fill="#fff"/>` : ""}${body.replace(/^<g[^>]*>|<\/g>$/g, m => m)}</svg>`;
  }
  function saveSvg() {
    const a = document.createElement("a"), u = URL.createObjectURL(new Blob([svgString(0)], {type: "image/svg+xml"}));
    a.href = u; a.download = ((typeof fileName === "string" && fileName) || "composition") + ".svg"; a.click(); setTimeout(() => URL.revokeObjectURL(u), 4000);
  }
  async function renderPng(ppi, hide) {
    const hidden = allItems().filter(i => i.visible && hide(i)); hidden.forEach(i => i.visible = false);
    let svg; try { svg = svgString(ppi); } finally { hidden.forEach(i => i.visible = true); }
    const url = URL.createObjectURL(new Blob([svg], {type: "image/svg+xml"})), img = new Image(); img.src = url; await img.decode();
    const c = document.createElement("canvas"); c.width = Math.round(S.W / 25.4 * ppi); c.height = Math.round(S.H / 25.4 * ppi);
    const g = c.getContext("2d"); g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height); g.drawImage(img, 0, 0, c.width, c.height); URL.revokeObjectURL(url);
    return new Promise(r => c.toBlob(r, "image/png"));
  }
  async function sendToPrint() {
    const vis = allItems().filter(i => i.visible);
    if (!vis.length) return toast_("Put something on the page first.");
    try {
      const ppi = Math.min(300, Math.sqrt(25e6 / (S.W * S.H)) * 25.4), nm = (typeof fileName === "string" && fileName ? fileName : "composition");
      const n = Math.min(10, Math.max(1, colourSets(vis).f.filter(x => x[0]).length));
      PG.size = "Custom"; PG.custom_w = num(S.W + 2 * PG.margin, 1); PG.custom_h = num(S.H + 2 * PG.margin, 1);
      P.print.colors = []; P.print.names = []; P.print.picked = false; P.print.keep_solid = false;
      const blob = await renderPng(ppi, () => false);
      Object.assign(P.print, {method: "spot", kind: "flat", inks: n});
      await load(new File([blob], nm + ".png", {type: "image/png"}), "print");
    } catch (e) { toast_("Could not send the page to Print: " + e.message); }
  }

  // ------------------------------------------------------------------ mounting
  function build() {
    root = el("div"); root.id = "compose";
    root.innerHTML = `
      <div class="tool" id="ctool">
        <div data-ct="select" class="on" title="V">Select<i>V</i></div><div data-ct="nodes" title="A">Nodes<i>A</i></div><div data-ct="pen" title="P">Pen<i>P</i></div><div data-ct="text" title="T">Text<i>T</i></div><div data-ct="shape" title="S">Shape<i>S</i></div><div data-ct="fill" title="F">Fill<i>F</i></div>
        <div class="sp"></div>
        <div id="cadd">+ Add file</div><div id="cundo">Undo</div><div id="credo">Redo</div><div id="cfit">Fit</div><div id="czoom" style="cursor:default;text-transform:none;min-width:64px;text-align:center">100%</div>
        <input type="file" id="cfile" accept="image/*,.svg" multiple hidden>
      </div>
      <div id="cwrap"><canvas id="ccanvas" tabindex="0"></canvas>
        <div id="clib"><div class="ltab">Library <span id="clibN"></span></div>
          <div class="lbody"><div class="lhead"><input type="text" id="clibQ" placeholder="Search"><span id="clibFolder"></span></div><div id="clibStrip"></div></div></div>
      </div>`;
    document.querySelector(".main").insertBefore(root, document.querySelector("#vp"));
    cv = root.querySelector("#ccanvas");
    root.querySelectorAll("[data-ct]").forEach(b => b.onclick = () => setTool(b.dataset.ct));
    root.querySelector("#cundo").onclick = undo; root.querySelector("#credo").onclick = redo; root.querySelector("#cfit").onclick = fit;
    root.querySelector("#cadd").onclick = () => root.querySelector("#cfile").click();
    root.querySelector("#cfile").onchange = async e => { for (const f of e.target.files) await addFile(f); e.target.value = ""; };
    root.querySelector("#clibQ").oninput = e => { S.libQ = e.target.value; renderLib(); };
    root.querySelector("#clib").addEventListener("mouseenter", loadLibrary);
    for (const t of ["pointerdown", "wheel"]) root.querySelector("#clib").addEventListener(t, e => e.stopPropagation(), {passive: true});
  }
  function syncBar() {
    if (!root) return;
    root.querySelector("#cundo").style.opacity = S.hi > 0 ? 1 : .4; root.querySelector("#credo").style.opacity = S.hi < S.hist.length - 1 ? 1 : .4;
    root.querySelector("#czoom").textContent = Math.round(zoom() / MMPX * 100) + "%";
  }
  let renderSideHook = null;
  return {
    sideBoxes,
    enter(hook) {
      renderSideHook = hook;
      if (!root) build();
      root.style.display = "flex";
      if (!S.ready) { cv.width = 800; cv.height = 500; setup(); }
      resize(); if (!S.fitted) { fit(); S.fitted = true; } else drawUI();
      loadLibrary(); syncBar(); ensureFonts();
    },
    leave() { if (S.pen) penFinish(false); if (root) root.style.display = "none"; },
    _S: S, _api: {group, ungroup, fillAt, addSvg, commit, select, combine, setTool, leaves, paths, undo, redo, setBounds, rotateSel, applyColour, sendToPrint, svgString, setBoard, simplifySel, deleteNodes, setNodeType, retext, addText, getL: () => L, hist: () => S.hist.length},
  };
})();
