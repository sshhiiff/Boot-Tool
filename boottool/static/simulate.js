/* Simulate: see designs on a T-shirt drawn to real size (centimetres), move and resize them, save a JPG.
   Everything is drawn as one SVG whose units are centimetres, so a design that is 28 cm wide really is 28 cm
   against the chosen shirt size. */
const Simulate = (() => {
  // Unisex tee, flat measurements in cm: chest = width across the body under the arms, length = top of shoulder to hem.
  const CHART = {XS: {chest: 46, length: 66}, S: {chest: 51, length: 69}, M: {chest: 53.5, length: 72}, L: {chest: 56, length: 74}, XL: {chest: 59, length: 76}, XXL: {chest: 62, length: 78}};
  const SIZES = Object.keys(CHART);
  const PRESETS = ["#ffffff", "#e8e1d0", "#9aa0a6", "#2b2b2b", "#000000", "#14213d", "#1d3f8f", "#c8102e", "#2e5d3a"];
  const MAXPER = 10;
  const S = {size: "L", sleeve: "short", side: "front", colour: "#ffffff", designs: {front: [], back: []}, sel: null, libTab: "convert",
    convert: [], prints: [], printFolder: "", convertFolder: "", q: "", pxcm: 8, real: false, shirtLines: true, jpgLines: false, keep: true, crop: true, ready: false};
  let root = null, wrap = null, svg = null, renderSideHook = null, drag = null, uid = 0, built = false;
  const r1 = v => Math.round(v * 10) / 10;
  const cur = () => S.designs[S.side];
  const byId = id => cur().find(d => d.id === id);
  const sel = () => S.sel && byId(S.sel);

  // ------------------------------------------------------------------ shirt geometry (all cm, x from centre line, y from top of shoulder at the neck)
  function geo() {
    const {chest, length} = CHART[S.size], bw = chest / 2, long = S.sleeve === "long", front = S.side === "front";
    const nw = 4.5 + chest * 0.07, nd = front ? 7.6 : 2.4, band = 2.0, sx = bw - 1.2, sy = 4.6, armH = chest * 0.36;
    const a = (long ? 14 : 27) * Math.PI / 180, sl = long ? 58 : 17.5 + (chest - 56) * 0.12, so = long ? 9.5 + (chest - 56) * 0.05 : 19 + (chest - 56) * 0.12;
    const d = [Math.cos(a), Math.sin(a)], n = [-Math.sin(a), Math.cos(a)];
    const E1 = [sx + d[0] * sl, sy + d[1] * sl], E2 = [E1[0] + n[0] * so, E1[1] + n[1] * so], A = [bw, sy + armH];
    return {chest, length, bw, nw, nd, band, sx, sy, armH, E1, E2, A, d, collarY: nd + band, long, front,
      maxX: Math.max(E2[0], E1[0]), maxY: Math.max(length, E2[1])};
  }
    const f = v => (Math.round(v * 100) / 100);
  const P_ = p => `${f(p[0])} ${f(p[1])}`;
  function shade(hex, k) {            // k < 0 darker, k > 0 lighter
    const n = parseInt(hex.slice(1), 16), c = [n >> 16, (n >> 8) & 255, n & 255].map(v => Math.max(0, Math.min(255, Math.round(k < 0 ? v * (1 + k) : v + (255 - v) * k))));
    return "#" + c.map(v => v.toString(16).padStart(2, "0")).join("");
  }
  const lum = hex => { const n = parseInt(hex.slice(1), 16); return (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255; };

  function shirtPaths(g) {
    const {bw, nw, nd, band, sx, sy, E1, E2, A, length} = g, mx = [(sx + E1[0]) / 2, (sy + E1[1]) / 2 - 0.25], X = p => [-p[0], p[1]], sh = [nw + 0.6, 0.9];
    const outline = `M ${P_(X(sh))} Q 0 -0.4 ${P_(sh)} L ${P_([sx, sy])} Q ${P_(mx)} ${P_(E1)} L ${P_(E2)} L ${P_(A)} L ${P_([bw, length])} L ${P_([-bw, length])} L ${P_(X(A))} L ${P_(X(E2))} L ${P_(X(E1))} Q ${P_(X(mx))} ${P_(X([sx, sy]))} Z`;
    const neck = (dp, w) => `M ${f(-w)} 0 C ${f(-w)} ${f(dp * .78)} ${f(-w * .5)} ${f(dp)} 0 ${f(dp)} C ${f(w * .5)} ${f(dp)} ${f(w)} ${f(dp * .78)} ${f(w)} 0`;
    return {outline, neck, hole: neck(nd, nw) + " Z"};
  }
  function shirtSVG(g, clipId) {
    const {bw, nw, nd, band, sx, sy, E1, E2, A, length, d} = g, c = S.colour, dark = lum(c) < 0.3, line = dark ? shade(c, 0.55) : shade(c, -0.7);
    const {outline, neck, hole} = shirtPaths(g), st = `fill="none" stroke="${line}" stroke-width=".16"`, seam = [];
    for (const m of [1, -1]) {
      const X = p => [p[0] * m, p[1]];
      seam.push(`<path d="M ${P_(X([sx, sy]))} Q ${P_(X([A[0] - 5, (sy + A[1]) / 2 - 2]))} ${P_(X(A))}" ${st}/>`);               // armhole seam
      const off = g.long ? 4.5 : 2.4, v = [A[0] - E2[0], A[1] - E2[1]], lv = Math.hypot(v[0], v[1]);
      const h1 = [E1[0] - d[0] * off, E1[1] - d[1] * off], h2 = [E2[0] + v[0] / lv * off * 1.05, E2[1] + v[1] / lv * off * 1.05];
      seam.push(`<path d="M ${P_(X(h1))} L ${P_(X(h2))}" ${st}/>`);                                                             // sleeve hem band
    }
    seam.push(`<path d="M ${f(-bw)} ${f(length - 3)} L ${f(bw)} ${f(length - 3)}" ${st}/>`);                                      // hem band
    return `<g stroke-linejoin="round">
      <path d="${outline}" fill="${c}" stroke="${line}" stroke-width=".22"/>${seam.join("")}
      <path d="${hole}" fill="${dark ? "#fff" : "#000"}" fill-opacity=".12" stroke="${line}" stroke-width=".22"/>
      <path d="${neck(nd + band, nw + .7)}" fill="none" stroke="${line}" stroke-width=".22"/>
      <path d="${neck(nd + band * .45, nw + .3)}" ${st} stroke-dasharray=".5 .35"/>
    </g>${clipId ? `<clipPath id="${clipId}"><path d="${outline} ${hole}" clip-rule="evenodd"/></clipPath>` : ""}`;
  }

  // ------------------------------------------------------------------ drawing
  function bounds(g) { return {x0: -g.maxX - 4, x1: g.maxX + 4, y0: -6, y1: g.maxY + 6}; }
  function measureLines(g) {
    const col = lum(S.colour) < 0.5 ? "#ffffff" : "#000000", t = (x, y, s, anchor = "middle", rot = 0) => `<text x="${f(x)}" y="${f(y)}" text-anchor="${anchor}" transform="rotate(${rot} ${f(x)} ${f(y)})" font-size="2" fill="${col}" font-family="Arial Narrow,Arial,sans-serif">${s}</text>`;
    const ln = (a, b, c, d) => `<path d="M ${f(a)} ${f(b)} L ${f(c)} ${f(d)}" stroke="${col}" stroke-width="0.12" stroke-dasharray="0.6 0.4" fill="none"/>`;
    const y = g.sy + g.armH + 4;
    return `<g opacity=".85">${ln(-g.bw, y, g.bw, y)}${t(0, y - 0.7, `chest ${g.chest} cm`)}
      ${ln(g.bw + 3, 0, g.bw + 3, g.length)}${t(g.bw + 4.2, g.length / 2, `length ${g.length} cm`, "middle", 90)}
      ${ln(-g.nw, -2, g.nw, -2)}${t(0, -2.7, `neck ${f(g.nw * 2)} cm`)}</g>`;
  }
  function draw() {
    if (!svg) return;
    const g = geo(), b = bounds(g), w = b.x1 - b.x0, h = b.y1 - b.y0, W = wrap.clientWidth, H = wrap.clientHeight;
    if (!S.real) S.pxcm = Math.max(2, Math.min((W - 40) / w, (H - 60) / h));
    const pw = w * S.pxcm, ph = h * S.pxcm;
    svg.setAttribute("viewBox", `${f(b.x0)} ${f(b.y0)} ${f(w)} ${f(h)}`);
    svg.style.width = pw + "px"; svg.style.height = ph + "px";
    const L0 = Math.max(0, (W - pw) / 2), T0 = Math.max(0, (H - 36 - ph) / 2), sc = root.querySelector("#sscroll"), sp = root.querySelector("#sspace");
    svg.style.left = L0 + "px"; svg.style.top = T0 + "px"; sp.style.left = L0 + pw + 20 + "px"; sp.style.top = T0 + ph + 50 + "px";
    sc.style.overflow = S.real ? "auto" : "hidden";
    svg.innerHTML = body(g, true);
    syncZoom();
  }
  const rad = d => (d.rot || 0) * Math.PI / 180;
  const extH = d => Math.abs(d.w * Math.sin(rad(d))) + Math.abs(d.h * Math.cos(rad(d)));   // height of the box that just fits the (rotated) design
  const topC = d => d.y + d.h / 2 - extH(d) / 2;                                              // highest point of the design, measured down from the collar
  const setTopC = (d, v) => { d.y += v - topC(d); };
  const ctr = (d, g) => [d.x, g.collarY + d.y + d.h / 2];
  const rotP = (p, c, a) => { const co = Math.cos(a), si = Math.sin(a), x = p[0] - c[0], y = p[1] - c[1]; return [c[0] + x * co - y * si, c[1] + x * si + y * co]; };
  function designsSVG(withUI, lines) {
    const g = geo(), k = withUI ? 1 / S.pxcm : 0, out = [], imgs = [];
    for (const d of cur()) {
      const [cx, cy] = ctr(d, g);
      imgs.push(`<image href="${d.data}" x="${f(d.x - d.w / 2)}" y="${f(g.collarY + d.y)}" width="${f(d.w)}" height="${f(d.h)}" preserveAspectRatio="none" transform="rotate(${f(d.rot || 0)} ${f(cx)} ${f(cy)})" data-id="${d.id}"/>`);
    }
    out.push(`<g${S.crop ? ' clip-path="url(#sclip)"' : ""}>${imgs.join("")}</g>`);
    if (lines && !withUI) for (const d of cur()) out.push(dimLines(d, g, false));
    if (withUI) {
      const d = sel();
      if (d) {
        const [cx, cy] = ctr(d, g), x = d.x - d.w / 2, y = g.collarY + d.y, hs = 4.2 * k, ry = y - 26 * k;
        let ui = `<rect x="${f(x)}" y="${f(y)}" width="${f(d.w)}" height="${f(d.h)}" fill="none" stroke="#ec008c" stroke-width="${1.2 * k}" stroke-dasharray="${5 * k} ${3 * k}" pointer-events="none"/>`;
        for (const [px, py, id] of [[x, y, "nw"], [x + d.w, y, "ne"], [x, y + d.h, "sw"], [x + d.w, y + d.h, "se"]])
          ui += `<rect x="${f(px - hs)}" y="${f(py - hs)}" width="${f(hs * 2)}" height="${f(hs * 2)}" fill="#fff" stroke="#000" stroke-width="${k}" data-h="${id}" style="cursor:${id === "nw" || id === "se" ? "nwse" : "nesw"}-resize"/>`;
        ui += `<path d="M ${f(d.x)} ${f(y)} L ${f(d.x)} ${f(ry)}" stroke="#ec008c" stroke-width="${k}" pointer-events="none"/><circle cx="${f(d.x)}" cy="${f(ry)}" r="${f(5 * k)}" fill="#fff" stroke="#000" stroke-width="${k}" data-h="rot" style="cursor:grab"/>`;
        out.push(`<g transform="rotate(${f(d.rot || 0)} ${f(cx)} ${f(cy)})">${ui}</g>`, dimLines(d, g, true));
      }
      if (drag && drag.snapX) out.push(`<path d="M 0 -4 L 0 ${f(g.length + 4)}" stroke="#ec008c" stroke-width="${1.2 * k}" pointer-events="none"/><text x="1" y="${f(g.length + 3)}" font-size="${12 * k}" fill="#ec008c" font-family="Arial Narrow,Arial,sans-serif">centre</text>`);
    }
    return out.join("");
  }
  function dimLines(d, g, ui) {
    const col = lum(S.colour) < 0.5 ? "#ffffff" : "#000000", k = ui ? 1 / S.pxcm : 0.2, fs = ui ? 13 * k : 1.8, a = rad(d);
    const x = d.x - d.w / 2, y = g.collarY + topC(d), lab = `${r1(d.w)} × ${r1(d.h)} cm${d.rot ? " · " + r1(d.rot) + "°" : ""}`, c2 = ui ? "#ec008c" : col;
    const eh = Math.abs(d.w * Math.sin(a)) + Math.abs(d.h * Math.cos(a)), bot = g.collarY + d.y + d.h / 2 + eh / 2;
    const tx = (px, py, s, an = "middle") => `<text x="${f(px)}" y="${f(py)}" text-anchor="${an}" font-size="${f(fs)}" fill="${c2}" font-family="Arial Narrow,Arial,sans-serif">${s}</text>`;
    const ln = (a_, b, c, e) => `<path d="M ${f(a_)} ${f(b)} L ${f(c)} ${f(e)}" stroke="${c2}" stroke-width="${f(ui ? k : 0.1)}" fill="none"/>`;
    const xl = x - 3 * (ui ? k * 5 : 0.8);
    return `<g pointer-events="none">${ln(xl, g.collarY, xl, y)}${tx(xl - fs * 0.35, (g.collarY + y) / 2, `${r1(topC(d))} cm`, "end")}${tx(d.x, bot + fs * 1.4, lab)}</g>`;
  }
  function body(g, withUI) {
    return `<rect x="-400" y="-400" width="800" height="800" fill="${withUI ? "none" : "#ffffff"}" data-bg="1"/>${shirtSVG(g, "sclip")}${S.shirtLines && withUI || (!withUI && S.jpgLines) ? measureLines(g) : ""}${designsSVG(withUI, !withUI && S.jpgLines)}`;
  }

  // ------------------------------------------------------------------ designs
  const clampN = (v, a, b) => Math.max(a, Math.min(b, v));
  function placeDefault(d) { const g = geo(); d.x = 0; d.y = 7.5 + (g.front ? 0 : 0); }
  async function toData(url) {
    const blob = await (await fetch(url)).blob();
    return await new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = rej; fr.readAsDataURL(blob); });
  }
  function natSize(data) {
    return new Promise((res, rej) => { const im = new Image(); im.onload = () => res([im.naturalWidth || im.width, im.naturalHeight || im.height]); im.onerror = () => rej(new Error("Could not read that design.")); im.src = data; });
  }
  async function addDesign(lib, name, widthCm, at) {
    if (cur().length >= MAXPER) return toast(`Up to ${MAXPER} designs on one side.`);
    try {
      const url = lib === "print" ? `/api/prints/file?name=${encodeURIComponent(name)}` : `/api/library/file?name=${encodeURIComponent(name)}`;
      let data = await toData(url);
      if (lib === "convert" && !data.startsWith("data:image/svg+xml")) data = "data:image/svg+xml;base64," + data.split(",")[1];
      const [nw, nh] = await natSize(data), g = geo();
      let w = widthCm || Math.min(28, g.chest * 0.5);
      w = clampN(w, 1, 120); const h = w * nh / nw, d = {id: ++uid, lib, name, data, ar: nh / nw, w, h, x: 0, y: 7.5, rot: 0};
      if (at) { d.x = Math.abs(at[0]) < 0.7 ? 0 : r1(at[0]); d.y = r1(at[1] - g.collarY - h / 2); }
      cur().push(d); S.sel = d.id; draw(); renderSideHook && renderSideHook();
    } catch (e) { toast(e.message); }
  }
  function setW(d, w) { w = clampN(w, 1, 150); if (S.keep) { d.h = w * d.ar; } d.w = w; }
  function setH(d, h) { h = clampN(h, 1, 150); if (S.keep) { d.w = h / d.ar; } d.h = h; }
  function quick(kind) {
    const d = sel(); if (!d) return; const g = geo();
    d.x = 0;
    if (kind === "full") { d.w = clampN(g.chest * 0.54, 1, 150); d.h = d.w * d.ar; if (!S.keep) d.h = Math.min(d.h, g.length - g.collarY - 12); }
    if (kind !== "centre") setTopC(d, 7.5);
    draw(); renderSideHook && renderSideHook();
  }

  // ------------------------------------------------------------------ pointer
  const toCm = e => { const m = svg.getScreenCTM().inverse(), p = svg.createSVGPoint(); p.x = e.clientX; p.y = e.clientY; const q = p.matrixTransform(m); return [q.x, q.y]; };
  function onDown(e) {
    if (e.button !== 0) return;
    const g = geo(), [px, py] = toCm(e), hEl = e.target.closest && e.target.closest("[data-h]");
    if (hEl && sel()) {
      const d = sel(); svg.setPointerCapture(e.pointerId);
      drag = hEl.dataset.h === "rot" ? {type: "rot", d} : {type: "size", h: hEl.dataset.h, d, c0: ctr(d, g), w0: d.w, h0: d.h}; return;
    }
    // pick the topmost design under the pointer (the pointer is turned back by the design's rotation; transparent parts count as part of it)
    const hit = [...cur()].reverse().find(d => { const [qx, qy] = rotP([px, py], ctr(d, g), -rad(d)); return qx >= d.x - d.w / 2 && qx <= d.x + d.w / 2 && qy >= g.collarY + d.y && qy <= g.collarY + d.y + d.h; });
    if (hit) { S.sel = hit.id; svg.setPointerCapture(e.pointerId); drag = {type: "move", d: hit, ox: px - hit.x, oy: py - hit.y}; draw(); renderSideHook && renderSideHook(); }
    else { S.sel = null; draw(); renderSideHook && renderSideHook(); }
  }
  function onMove(e) {
    if (!drag) return; const g = geo(), [px, py] = toCm(e), d = drag.d;
    if (drag.type === "move") {
      let nx = px - drag.ox; drag.snapX = false;
      if (Math.abs(nx) < 0.7) { nx = 0; drag.snapX = true; }
      d.x = r1(nx); d.y = r1(py - drag.oy);
    } else if (drag.type === "rot") {
      const [cx, cy] = ctr(d, g); let a = Math.atan2(py - cy, px - cx) * 180 / Math.PI + 90;
      if (e.shiftKey) a = Math.round(a / 15) * 15;
      a = ((a + 180) % 360 + 360) % 360 - 180; d.rot = Math.round(a * 10) / 10;
    } else {
      const hx = drag.h.includes("w") ? -1 : 1, vy = drag.h.includes("n") ? -1 : 1, a = rad(d), c0 = drag.c0;
      const Aw = rotP([c0[0] - hx * drag.w0 / 2, c0[1] - vy * drag.h0 / 2], c0, a);
      const v = rotP([px, py], Aw, -a), lx = (v[0] - Aw[0]) * hx, ly = (v[1] - Aw[1]) * vy;
      let nw = Math.max(1, lx), nh = Math.max(1, ly);
      if (S.keep || !e.shiftKey) { const k = Math.max(nw / drag.w0, nh / drag.h0); nw = drag.w0 * k; nh = nw * d.ar; }
      nw = clampN(nw, 1, 150); nh = clampN(nh, 1, 150);
      const corner = rotP([Aw[0] + hx * nw, Aw[1] + vy * nh], Aw, a), cx = (Aw[0] + corner[0]) / 2, cy = (Aw[1] + corner[1]) / 2;
      d.w = nw; d.h = nh; d.x = cx; d.y = cy - g.collarY - nh / 2;
    }
    draw(); renderSideHook && syncFields();
  }
  function onUp() { if (!drag) return; drag = null; draw(); renderSideHook && renderSideHook(); }
  function dropOn(e) {
    e.preventDefault(); let j; try { j = JSON.parse(e.dataTransfer.getData("text/x-boot-sim")); } catch (x) { return; }
    const [px, py] = toCm(e); addDesign(j.lib, j.name, j.w, [px, py]);
  }

  // ------------------------------------------------------------------ side panel
  const seg = (opts, val, cb) => { const w = el("div", "cells"); for (const [v, l] of opts) { const b = el("span", v === val ? "on" : "", l); b.onclick = () => cb(v); w.append(b); } return w; };
  function field(label, val, cb, unit = "cm") {
    const d = el("div", "row"); d.style.cssText = "display:grid;grid-template-columns:1fr 84px 20px;align-items:center;column-gap:6px;margin:3px 0"; d.append(el("span", "lab", label));
    const i = el("input"); i.type = "text"; i.value = val; i.style.width = "84px"; i.style.textAlign = "right";
    i.onchange = () => { const v = parseFloat(i.value.replace(",", ".")); if (isFinite(v)) cb(v); else i.value = val; };
    i.onkeydown = e => { if (e.key === "Enter") i.blur(); }; d.append(i); d.append(el("span", "lab", unit)); d._i = i; return d;
  }
  const fieldEls = {};
  function syncFields() { const d = sel(); if (!d) return; const set = (k, v) => { if (fieldEls[k] && document.activeElement !== fieldEls[k]) fieldEls[k].value = r1(v); }; set("w", d.w); set("h", d.h); set("y", topC(d)); set("x", d.x); set("r", d.rot || 0); }
  function sb(title, ...kids) { const b = el("div", "box"); b.append(el("div", "bh", title)); for (const k of kids) if (k) b.append(k); return b; }
  const cellOf = (...k) => { const c = el("div", "cell"); for (const x of k) c.append(x); return c; };
  const redraw = () => { draw(); renderSideHook && renderSideHook(); };
  function sideBoxes() {
    for (const k in fieldEls) delete fieldEls[k];
    const g = geo(), out = [];
    const shirt = sb("Shirt", seg(SIZES.map(s => [s, s]), S.size, v => { S.size = v; redraw(); }), seg([["short", "Short sleeve"], ["long", "Long sleeve"]], S.sleeve, v => { S.sleeve = v; redraw(); }),
      seg([["front", "Front"], ["back", "Back"]], S.side, v => { S.side = v; S.sel = null; redraw(); }),
      el("div", "cell", `<div class="hint" style="margin:0">Unisex tee, ${S.size}: chest ${g.chest} cm (flat) · length ${g.length} cm</div>`));
    shirt.children[1].style.borderTop = "1px solid #000"; out.push(shirt);
    // colour
    const cb = el("div", "cell"), row = el("div"); row.style.cssText = "display:flex;flex-wrap:wrap;gap:6px;align-items:center";
    for (const c of PRESETS) { const s = el("span", "sw"); s.style.background = c; s.style.cursor = "pointer"; if (c.toLowerCase() === S.colour.toLowerCase()) s.style.outline = "2px solid #ec008c"; s.title = c; s.onclick = () => { S.colour = c; redraw(); }; row.append(s); }
    const pick = el("input"); pick.type = "color"; pick.value = S.colour; pick.title = "Any colour"; pick.oninput = () => { S.colour = pick.value; draw(); }; pick.onchange = () => renderSideHook && renderSideHook(); row.append(pick);
    cb.append(row);
    const fp = el("div", "bsm", "Use Print colour"); fp.style.cssText += ";margin-top:8px;text-align:center"; fp.title = "Take the garment colour chosen in Print › Printing on colour";
    fp.onclick = () => { S.colour = (P.print.dark ? P.print.dark_bg : "#ffffff") || "#ffffff"; redraw(); }; cb.append(fp);
    out.push(sb("Shirt colour", cb));
    // designs list
    const list = el("div"); list.style.cssText = "max-height:150px;overflow:auto";
    if (!cur().length) list.append(el("div", "cell", `<div class="hint" style="margin:0">No designs on the ${S.side} yet. Hover the Libraries tab at the bottom and click a design to add it. Up to ${MAXPER} per side.</div>`));
    for (const d of cur()) {
      const r = el("div", "cell"); r.style.cssText = "display:flex;gap:8px;align-items:center;cursor:pointer;" + (d.id === S.sel ? "background:var(--acc);color:#fff" : "");
      r.innerHTML = `<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(d.name.replace(/\.(svg|png)$/i, ""))}</span><span>${r1(d.w)}×${r1(d.h)}</span>`;
      r.onclick = () => { S.sel = d.id; redraw(); }; list.append(r);
    }
    out.push(sb(`Designs · ${S.side}`, list));
    const d = sel();
    if (d) {
      const fw = field("Width", r1(d.w), v => { setW(d, v); redraw(); }), fh = field("Height", r1(d.h), v => { setH(d, v); redraw(); });
      const fy = field("From collar", r1(topC(d)), v => { setTopC(d, v); redraw(); }), fx = field("From centre", r1(d.x), v => { d.x = v; redraw(); });
      const fr = field("Rotate", r1(d.rot || 0), v => { d.rot = ((v + 180) % 360 + 360) % 360 - 180; redraw(); }, "°"); fieldEls.r = fr._i;
      fieldEls.w = fw._i; fieldEls.h = fh._i; fieldEls.y = fy._i; fieldEls.x = fx._i;
      const kp = el("label", "chk", `<input type="checkbox"><span>Keep proportions</span>`), ki = kp.querySelector("input"); ki.checked = S.keep; ki.onchange = () => { S.keep = ki.checked; };
      const qk = seg([["centre", "Centre ↔"], ["chest", "Chest"], ["full", "Full front"]], null, v => quick(v)); qk.style.marginTop = "6px";
      const bt = el("div", "cells"); const dup = el("span", "", "Duplicate"), rem = el("span", "", "Remove");
      dup.onclick = () => { if (cur().length >= MAXPER) return toast(`Up to ${MAXPER} designs on one side.`); const c = {...d, id: ++uid, x: d.x + 2, y: d.y + 2}; cur().push(c); S.sel = c.id; redraw(); };
      rem.onclick = () => { S.designs[S.side] = cur().filter(x => x !== d); S.sel = null; redraw(); }; bt.append(dup, rem);
      const bx = sb("Selected design", cellOf(fw, fh, kp, fy, fx, fr), qk, bt);
      qk.style.borderTop = "1px solid #000"; bt.style.borderTop = "1px solid #000"; bx.children[1].style.paddingBottom = "6px"; out.push(bx);
    }
    // view + save
    const v1 = el("label", "chk", `<input type="checkbox"><span>Show shirt measurements</span>`), vi = v1.querySelector("input"); vi.checked = S.shirtLines; vi.onchange = () => { S.shirtLines = vi.checked; draw(); };
    const v2 = el("label", "chk", `<input type="checkbox" id="cropCk"><span>Crop designs to the shirt</span>`), vc = v2.querySelector("input"); vc.checked = S.crop; vc.onchange = () => { S.crop = vc.checked; draw(); };
    out.push(sb("View", cellOf(v1, v2)));
    const j1 = el("label", "chk", `<input type="checkbox" id="jpgLines"><span>Include measurements</span>`), ji = j1.querySelector("input"); ji.checked = S.jpgLines; ji.onchange = () => { S.jpgLines = ji.checked; };
    const sv = el("button", "btn go", "Save mock-up as JPG"); sv.onclick = saveJpg; sv.id = "saveJpg";
    const sbx = sb("Save", cellOf(j1), sv); sbx.style.borderBottom = "0"; out.push(sbx);
    return out;
  }

  // ------------------------------------------------------------------ JPG
  async function saveJpg() {
    try {
      const g = geo(), b = bounds(g), w = b.x1 - b.x0, h = b.y1 - b.y0, ppcm = Math.min(24, 4000 / Math.max(w, h));
      const pw = Math.round(w * ppcm), ph = Math.round(h * ppcm);
      const s = `<svg xmlns="http://www.w3.org/2000/svg" width="${pw}" height="${ph}" viewBox="${f(b.x0)} ${f(b.y0)} ${f(w)} ${f(h)}"><rect x="${f(b.x0)}" y="${f(b.y0)}" width="${f(w)}" height="${f(h)}" fill="#ffffff"/>${shirtSVG(g, "sclip")}${S.jpgLines ? measureLines(g) : ""}${designsSVG(false, S.jpgLines)}</svg>`;
      const url = URL.createObjectURL(new Blob([s], {type: "image/svg+xml"})), im = new Image(); im.src = url; await im.decode();
      const c = document.createElement("canvas"); c.width = pw; c.height = ph; const x = c.getContext("2d"); x.fillStyle = "#fff"; x.fillRect(0, 0, pw, ph); x.drawImage(im, 0, 0, pw, ph); URL.revokeObjectURL(url);
      const blob = await new Promise(r => c.toBlob(r, "image/jpeg", 0.92));
      const a = document.createElement("a"), u = URL.createObjectURL(blob); a.href = u; a.download = `mockup-${S.side}-${S.size}.jpg`; a.click(); setTimeout(() => URL.revokeObjectURL(u), 4000);
    } catch (e) { toast("Could not save the JPG: " + e.message); }
  }

  // ------------------------------------------------------------------ libraries
  async function loadLibs() {
    try { const j = await (await fetch("/api/library")).json(); S.convert = j.items; S.convertFolder = j.folder; } catch (e) { /* empty */ }
    try { const j = await (await fetch("/api/prints")).json(); S.prints = j.items; S.printFolder = j.folder; } catch (e) { /* empty */ }
    renderLib();
  }
  function renderLib() {
    if (!root) return;
    const strip = root.querySelector("#slibStrip"), q = S.q.toLowerCase(), items = (S.libTab === "convert" ? S.convert : S.prints).filter(i => !q || i.name.toLowerCase().includes(q));
    root.querySelector("#slibN").textContent = `Convert (${S.convert.length}) | Print (${S.prints.length})`;
    root.querySelectorAll("[data-lt]").forEach(b => b.classList.toggle("on", b.dataset.lt === S.libTab));
    root.querySelector("#slibFolder").textContent = S.libTab === "convert" ? S.convertFolder : S.printFolder;
    strip.innerHTML = "";
    if (!items.length) strip.append(el("div", "hint", S.libTab === "print" ? "Nothing here yet. In the Print tab, press “Save to Print library”." : "Nothing here yet. Everything you convert in the Convert tab is saved here automatically."));
    for (const it of items) {
      const t = el("div", "lt"), nm = it.name.replace(/\.(svg|png)$/i, ""), src = S.libTab === "print" ? `/api/prints/file?name=${encodeURIComponent(it.name)}` : `/api/library/file?name=${encodeURIComponent(it.name)}`;
      t.innerHTML = `<div class="lth"><img loading="lazy" alt="" src="${src}"></div><div class="ltn" title="${esc(nm)}">${esc(nm)}</div>${S.libTab === "print" ? '<span class="ltx" title="Delete from Print library">×</span>' : ""}`;
      t.style.cursor = "grab"; t.draggable = true;
      t.ondragstart = e => { e.dataTransfer.setData("text/x-boot-sim", JSON.stringify({lib: S.libTab, name: it.name, w: S.libTab === "print" && it.width_mm ? it.width_mm / 10 : 0})); e.dataTransfer.effectAllowed = "copy"; };
      t.onclick = e => { if (e.target.classList.contains("ltx")) return; addDesign(S.libTab, it.name, S.libTab === "print" && it.width_mm ? it.width_mm / 10 : 0); };
      const x = t.querySelector(".ltx"); if (x) x.onclick = async e => { e.stopPropagation(); await fetch("/api/prints/delete", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: it.name})}); loadLibs(); };
      strip.append(t);
    }
  }

  // ------------------------------------------------------------------ mounting
  function build() {
    root = el("div"); root.id = "simulate";
    root.innerHTML = `
      <div class="tool" id="stool"><div id="sfit">Fit</div><div id="sreal" title="1 cm on the shirt is 1 cm on a screen at 100% zoom">Real size</div><div class="sp"></div><span id="szoom" style="padding:0 12px;text-transform:none;color:var(--mute)"></span></div>
      <div id="swrap"><div id="sscroll" style="position:absolute;inset:0;overflow:hidden"><svg id="ssvg" xmlns="http://www.w3.org/2000/svg" style="position:absolute;touch-action:none"></svg><div id="sspace" style="position:absolute;width:1px;height:1px"></div></div>
        <div id="slib"><div class="ltab">Libraries · <span id="slibN"></span></div>
          <div class="lbody"><div class="lhead"><div class="cells" style="border:1px solid #000;width:200px"><span data-lt="convert" class="on">Convert</span><span data-lt="print">Print</span></div><input type="text" id="slibQ" placeholder="Search"><span id="slibFolder"></span></div><div id="slibStrip"></div></div></div>
      </div>`;
    document.querySelector(".main").insertBefore(root, document.querySelector("#vp"));
    wrap = root.querySelector("#swrap"); svg = root.querySelector("#ssvg");
    wrap.addEventListener("dragover", e => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; }); wrap.addEventListener("drop", dropOn);
    svg.addEventListener("pointerdown", onDown); svg.addEventListener("pointermove", onMove); svg.addEventListener("pointerup", onUp); svg.addEventListener("pointercancel", onUp);
    root.querySelector("#sfit").onclick = () => { S.real = false; draw(); };
    root.querySelector("#sreal").onclick = () => { S.real = true; S.pxcm = 96 / 2.54; draw(); };
    root.querySelectorAll("[data-lt]").forEach(b => b.onclick = () => { S.libTab = b.dataset.lt; renderLib(); });
    root.querySelector("#slibQ").oninput = e => { S.q = e.target.value; renderLib(); };
    root.querySelector("#slib").addEventListener("mouseenter", loadLibs);
    for (const t of ["pointerdown", "wheel"]) root.querySelector("#slib").addEventListener(t, e => e.stopPropagation(), {passive: true});
    window.addEventListener("resize", () => { if (root && root.style.display !== "none") draw(); });
    document.addEventListener("keydown", e => {
      if (!root || root.style.display === "none" || /INPUT|TEXTAREA/.test((e.target || {}).tagName || "")) return;
      const d = sel(); if (!d) return;
      if (e.key === "Delete" || e.key === "Backspace") { S.designs[S.side] = cur().filter(x => x !== d); S.sel = null; redraw(); e.preventDefault(); }
      const st = e.shiftKey ? 1 : 0.1, mv = {ArrowLeft: [-st, 0], ArrowRight: [st, 0], ArrowUp: [0, -st], ArrowDown: [0, st]}[e.key];
      if (mv) { d.x = r1(d.x + mv[0]); d.y = r1(d.y + mv[1]); draw(); syncFields(); e.preventDefault(); }
    });
    built = true;
  }
  function syncZoom() { const z = root && root.querySelector("#szoom"); if (z) z.textContent = `${Math.round(S.pxcm / (96 / 2.54) * 100)}% of real size`; }
  return {
    sideBoxes,
    enter(hook) {
      renderSideHook = hook;
      if (!built) build();
      root.style.display = "flex";
      if (!S.ready) { S.ready = true; try { if (P.print.dark) S.colour = P.print.dark_bg || "#ffffff"; } catch (e) { /* default white */ } }
      draw(); loadLibs();
    },
    leave() { if (root) root.style.display = "none"; },
    _S: S, _api: {addDesign, draw, saveJpg, geo, quick, CHART},
  };
})();
