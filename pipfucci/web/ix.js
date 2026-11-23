'use strict';
// Interactive charts: hover read-outs, drag to zoom into a time range, pinch to zoom, two-finger
// swipe to pan, double-click or "Reset zoom" to go back, click a dot to open that cell.
//
// A chart is any SVG made by a render function: render(zoom) -> { svg, panels }. `zoom` is null or
// { h0, h1 } (hours). Each panel describes one plot area in SVG units so the pointer can be
// mapped back to data:
//   { type: 'time', x0, x1, y0, y1, h0, h1, hFull: [a, b], sy(v), series: [{ name, color, hours,
//     values, sem, fmt }], stack, title }
//   { type: 'box', x0, x1, y0, y1, boxes: [{ cx, half, name, color, stats, fmt, title }],
//     dots: [{ x, y, text, key }] }
// Charts in the same group zoom together.

const IX = { charts: new Map(), zoom: {}, seq: 0, drag: null, onDot: null };
const SVGNS = 'http://www.w3.org/2000/svg';

function ixId() { return 'ix' + (++IX.seq); }

function ixChart(render, opts = {}) {
  const id = ixId();
  const ch = { id, render, group: opts.group || id };
  IX.charts.set(id, ch);
  const out = render(IX.zoom[ch.group] || null);
  ch.panels = out.panels || [];
  return `<div class="ichart" data-ix="${id}">${out.svg}${ixResetBtn(ch)}</div>`;
}

function ixResetBtn(ch) {
  return IX.zoom[ch.group] ? `<button type="button" class="ix-reset" data-ixreset="${ch.group}">Reset zoom</button>` : '';
}

function ixRerender(group) {
  for (const ch of [...IX.charts.values()]) {
    if (ch.group !== group) continue;
    const el = document.querySelector(`[data-ix="${ch.id}"]`);
    if (!el) { IX.charts.delete(ch.id); continue; }
    const out = ch.render(IX.zoom[group] || null);
    ch.panels = out.panels || [];
    el.innerHTML = out.svg + ixResetBtn(ch);
  }
}

function ixSetZoom(group, z) {
  if (z && !(z.h1 - z.h0 > 1e-6)) return;
  if (z) IX.zoom[group] = z; else delete IX.zoom[group];
  ixRerender(group);
}

// A new page starts unzoomed; charts that are gone are forgotten.
function ixReset() { IX.charts.clear(); IX.zoom = {}; ixHide(); }

// The SVG of a chart as it should be saved: without the hover overlay.
function ixCleanSVG(svgEl) {
  const c = svgEl.cloneNode(true);
  c.querySelectorAll('.ix-ov').forEach((n) => n.remove());
  return c.outerHTML;
}

// ---------------------------------------------------------------- helpers
function ixTip() {
  let t = document.getElementById('ix-tip');
  if (!t) { t = document.createElement('div'); t.id = 'ix-tip'; t.setAttribute('role', 'tooltip'); t.hidden = true; document.body.appendChild(t); }
  return t;
}

function ixHide() {
  const t = document.getElementById('ix-tip'); if (t) t.hidden = true;
  document.querySelectorAll('.ichart .ix-ov').forEach((n) => n.remove());
}

function ixFind(target) {
  const el = target && target.closest ? target.closest('.ichart') : null;
  if (!el) return null;
  const ch = IX.charts.get(el.dataset.ix);
  const svg = el.querySelector('svg');
  return ch && svg ? { el, ch, svg } : null;
}

function ixPoint(svg, ev) {
  const m = svg.getScreenCTM();
  if (!m) return null;
  const p = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(m.inverse());
  return { x: p.x, y: p.y };
}

function ixPanelAt(ch, p, pad = 4) {
  return ch.panels.find((q) => p.x >= q.x0 - pad && p.x <= q.x1 + pad && p.y >= q.y0 - pad && p.y <= q.y1 + pad);
}

function ixHourAt(q, x) { return q.h0 + (Math.min(Math.max(x, q.x0), q.x1) - q.x0) / (q.x1 - q.x0) * (q.h1 - q.h0); }
function ixXAt(q, h) { return q.x0 + (h - q.h0) / (q.h1 - q.h0) * (q.x1 - q.x0); }

function ixNearest(hours, h) {
  let lo = 0, hi = hours.length - 1;
  if (hi < 0) return -1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (hours[mid] < h) lo = mid; else hi = mid; }
  return Math.abs(hours[lo] - h) <= Math.abs(hours[hi] - h) ? lo : hi;
}

function ixOverlay(svg) {
  let g = svg.querySelector(':scope > .ix-ov');
  if (!g) { g = document.createElementNS(SVGNS, 'g'); g.setAttribute('class', 'ix-ov'); g.setAttribute('pointer-events', 'none'); svg.appendChild(g); }
  return g;
}

function ixEsc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function ixNum(v, d = 1) { if (v == null || !isFinite(v)) return '–'; const a = Math.abs(v); return a >= 1000 ? Math.round(v).toLocaleString() : a >= 100 ? v.toFixed(0) : v.toFixed(d).replace(/\.0$/, ''); }

function ixShowTip(ev, html) {
  const t = ixTip();
  t.innerHTML = html;
  t.hidden = false;
  const pad = 14, w = t.offsetWidth, h = t.offsetHeight;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + w > window.innerWidth - 8) x = ev.clientX - w - pad;
  if (y + h > window.innerHeight - 8) y = ev.clientY - h - pad;
  t.style.left = Math.max(8, x) + 'px';
  t.style.top = Math.max(8, y) + 'px';
}

// ---------------------------------------------------------------- hover
function ixHover(hit, ev) {
  const p = ixPoint(hit.svg, ev);
  if (!p) return;
  const q = ixPanelAt(hit.ch, p);
  const ov = ixOverlay(hit.svg);
  ov.innerHTML = '';
  if (!q) { ixTip().hidden = true; return; }
  if (q.type === 'time') return ixHoverTime(hit, q, p, ov, ev);
  if (q.type === 'box') return ixHoverBox(hit, q, p, ov, ev);
}

function ixHoverTime(hit, q, p, ov, ev) {
  const h = ixHourAt(q, p.x);
  const ref = q.series.find((s) => s.hours && s.hours.length) || null;
  if (!ref) return;
  const i = ixNearest(ref.hours, h);
  const th = ref.hours[i];
  if (th < q.h0 - 1e-9 || th > q.h1 + 1e-9) { ixTip().hidden = true; return; }
  const x = ixXAt(q, th);
  let s = `<line x1="${x}" x2="${x}" y1="${q.y0}" y2="${q.y1}" stroke="${q.cross || '#8A929B'}" stroke-width="1" stroke-dasharray="3 3"/>`;
  const rows = [];
  for (const se of q.series) {
    if (!se.hours) continue;
    const j = se.hours === ref.hours ? i : ixNearest(se.hours, th);
    const v = se.values[j];
    if (v == null || !isFinite(v)) continue;
    if (!q.stack && q.sy && !se.noDot) s += `<circle cx="${x}" cy="${q.sy(v)}" r="4" fill="${se.color}" stroke="${q.ring || '#FFFFFF'}" stroke-width="1.5"/>`;
    const e = se.sem ? se.sem[j] : null;
    const f = se.fmt || q.fmt || ixNum;
    rows.push(`<div class="r"><span class="sw" style="background:${se.color}${se.dashed ? ';background-image:repeating-linear-gradient(90deg,transparent 0 3px,#fff 3px 5px)' : ''}"></span><span class="n">${ixEsc(se.name)}</span><span class="v">${f(v)}${e ? ` <span class="m">± ${f(e)}</span>` : ''}</span></div>`);
  }
  ov.innerHTML = s;
  if (!rows.length) { ixTip().hidden = true; return; }
  if (q.stack) rows.reverse();
  ixShowTip(ev, `<div class="h">${q.title ? ixEsc(q.title) + ' · ' : ''}${ixNum(th, 2)} h</div>${rows.join('')}`);
}

function ixHoverBox(hit, q, p, ov, ev) {
  // a dot under the pointer first, then the box column
  let best = null, bd = 49;
  for (const d of q.dots || []) { const dd = (d.x - p.x) ** 2 + (d.y - p.y) ** 2; if (dd < bd) { bd = dd; best = d; } }
  if (best) {
    ov.innerHTML = `<circle cx="${best.x}" cy="${best.y}" r="6" fill="none" stroke="${q.ring || '#111'}" stroke-width="2"/>`;
    hit.el.style.cursor = best.key ? 'pointer' : '';
    return ixShowTip(ev, `${best.html || ixEsc(best.text)}${best.key ? '<div class="m">Click to open this cell</div>' : ''}`);
  }
  hit.el.style.cursor = '';
  let b = null, bdx = Infinity;  // the nearest column, generous so gaps between boxes still read out
  for (const x of q.boxes || []) { const dx = Math.abs(p.x - x.cx); if (dx <= Math.max(x.half * 1.6, x.half + 4) && dx < bdx) { bdx = dx; b = x; } }
  if (b && !b.stats && b.info) { ov.innerHTML = ''; return ixShowTip(ev, b.info); }
  if (!b || !b.stats) { ov.innerHTML = ''; ixTip().hidden = true; return; }
  const f = b.fmt || ixNum, st = b.stats;
  ov.innerHTML = `<rect x="${b.cx - b.half}" y="${q.y0}" width="${2 * b.half}" height="${q.y1 - q.y0}" fill="${q.ring || '#111'}" fill-opacity="0.06"/>`;
  ixShowTip(ev, `<div class="h"><span class="sw" style="background:${b.color}"></span>${ixEsc(b.title || b.name)}</div>
    <div class="r"><span class="n">Median</span><span class="v">${f(st.med)}</span></div>
    <div class="r"><span class="n">Quartiles</span><span class="v">${f(st.q1)} – ${f(st.q3)}</span></div>
    <div class="r"><span class="n">Mean</span><span class="v">${f(st.mean)}</span></div>
    <div class="r"><span class="n">Range</span><span class="v">${f(st.min != null ? st.min : st.lo)} – ${f(st.max != null ? st.max : st.hi)}</span></div>
    <div class="r"><span class="n">n</span><span class="v">${st.n}</span></div>`);
}

// ---------------------------------------------------------------- zoom
function ixZoomAround(hit, q, h, factor) {
  const [a, b] = q.hFull;
  const cur = IX.zoom[hit.ch.group] || { h0: a, h1: b };
  let span = (cur.h1 - cur.h0) * factor;
  const minSpan = q.minSpan || (b - a) / 200 || 0.5;
  span = Math.min(b - a, Math.max(minSpan, span));
  const frac = (h - cur.h0) / (cur.h1 - cur.h0 || 1);
  let h0 = h - frac * span, h1 = h0 + span;
  if (h0 < a) { h0 = a; h1 = a + span; }
  if (h1 > b) { h1 = b; h0 = b - span; }
  ixSetZoom(hit.ch.group, span >= b - a - 1e-9 ? null : { h0, h1 });
}

function ixPan(hit, q, dh) {
  const [a, b] = q.hFull;
  const cur = IX.zoom[hit.ch.group];
  if (!cur) return;
  const span = cur.h1 - cur.h0;
  let h0 = Math.min(Math.max(a, cur.h0 + dh), b - span);
  ixSetZoom(hit.ch.group, { h0, h1: h0 + span });
}

document.addEventListener('pointermove', (ev) => {
  if (IX.drag) {
    const d = IX.drag;
    const p = ixPoint(d.svg, ev);
    if (!p) return;
    const x = Math.min(Math.max(p.x, d.q.x0), d.q.x1);
    d.x1 = x;
    const ov = ixOverlay(d.svg);
    ov.innerHTML = Math.abs(d.x1 - d.x0) > 2 ? `<rect x="${Math.min(d.x0, d.x1)}" y="${d.q.y0}" width="${Math.abs(d.x1 - d.x0)}" height="${d.q.y1 - d.q.y0}" fill="#4A8BFF" fill-opacity="0.15" stroke="#4A8BFF" stroke-width="1"/>` : '';
    ixTip().hidden = true;
    return;
  }
  const hit = ixFind(ev.target);
  if (!hit) { if (!document.getElementById('ix-tip')?.hidden) ixHide(); return; }
  ixHover(hit, ev);
});

document.addEventListener('pointerdown', (ev) => {
  if (ev.button !== 0 || ev.target.closest('.ix-reset')) return;
  const hit = ixFind(ev.target);
  if (!hit) return;
  const p = ixPoint(hit.svg, ev);
  const q = p && ixPanelAt(hit.ch, p, 0);
  if (!q) return;
  IX.drag = { ...hit, q, x0: p.x, x1: p.x, moved: false };
  if (q.type === 'time') ev.preventDefault();  // no text selection while dragging
});

document.addEventListener('pointerup', (ev) => {
  const d = IX.drag;
  IX.drag = null;
  if (!d) return;
  const ov = d.svg.querySelector('.ix-ov'); if (ov) ov.innerHTML = '';
  if (d.q.type === 'time' && Math.abs(d.x1 - d.x0) > 6) {
    const a = ixHourAt(d.q, Math.min(d.x0, d.x1)), b = ixHourAt(d.q, Math.max(d.x0, d.x1));
    ixSetZoom(d.ch.group, { h0: a, h1: b });
    return;
  }
  if (d.q.type === 'box' && IX.onDot) {  // a click on a dot opens that cell
    const p = ixPoint(d.svg, ev);
    if (!p) return;
    let best = null, bd = 49;
    for (const x of d.q.dots || []) { const dd = (x.x - p.x) ** 2 + (x.y - p.y) ** 2; if (dd < bd) { bd = dd; best = x; } }
    if (best && best.key) IX.onDot(best.key);
  }
});

document.addEventListener('dblclick', (ev) => {
  const hit = ixFind(ev.target);
  if (hit && IX.zoom[hit.ch.group]) { ev.preventDefault(); ixSetZoom(hit.ch.group, null); }
});

document.addEventListener('click', (ev) => {
  const b = ev.target.closest('[data-ixreset]');
  if (b) ixSetZoom(b.dataset.ixreset, null);
});

document.addEventListener('pointerleave', ixHide);
document.addEventListener('scroll', () => { const t = document.getElementById('ix-tip'); if (t) t.hidden = true; }, true);

document.addEventListener('wheel', (ev) => {
  const hit = ixFind(ev.target);
  if (!hit) return;
  const p = ixPoint(hit.svg, ev);
  const q = p && ixPanelAt(hit.ch, p, 0);
  if (!q || q.type !== 'time' || !q.hFull) return;
  if (ev.ctrlKey) {  // trackpad pinch (or Ctrl + scroll)
    ev.preventDefault();
    ixZoomAround(hit, q, ixHourAt(q, p.x), Math.exp(ev.deltaY * 0.01));
  } else if (IX.zoom[hit.ch.group] && Math.abs(ev.deltaX) > Math.abs(ev.deltaY)) {  // sideways swipe pans
    ev.preventDefault();
    ixPan(hit, q, ev.deltaX / (q.x1 - q.x0) * (q.h1 - q.h0));
  }
}, { passive: false });

// Safari / the app: pinch arrives as gesture events, not Ctrl + wheel
let ixGesture = null;
document.addEventListener('gesturestart', (ev) => {
  if (ev.target.closest && ev.target.closest('.fig-card')) return;  // figures magnify instead
  const hit = ixFind(ev.target);
  if (!hit) return;
  const p = ixPoint(hit.svg, ev);
  const q = p && ixPanelAt(hit.ch, p, 0);
  if (!q || q.type !== 'time' || !q.hFull) return;
  ev.preventDefault();
  ixGesture = { hit, q, h: ixHourAt(q, p.x), scale: 1 };
});
document.addEventListener('gesturechange', (ev) => {
  if (!ixGesture) return;
  ev.preventDefault();
  const g = ixGesture;
  const f = g.scale / (ev.scale || 1);
  g.scale = ev.scale || 1;
  const ch = IX.charts.get(g.hit.ch.id);
  const q = ch && ch.panels[0] ? ch.panels.find((x) => x.type === 'time') : g.q;
  ixZoomAround(g.hit, q || g.q, g.h, f);
});
document.addEventListener('gestureend', () => { ixGesture = null; });
