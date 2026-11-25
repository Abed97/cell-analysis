'use strict';
// Publication figures (white background, Helvetica) and the box-plot charts shared with the
// Population page. Every figure function returns { svg, panels } so ix.js can add hover read-outs
// and zoom; `zoom` is null or { h0, h1 } in hours.

const PUB = { G1: '#3DAA4F', S: '#E0393E', G2M: '#F2C230', Colourless: '#8C8C8C', Unclassified: '#E8E8E8' };
const CATS = ['G1', 'S', 'G2M', 'Colourless', 'Unclassified'];
const CAT_LABEL = { G1: 'G1', S: 'S', G2M: 'G2/M', Colourless: 'Colourless', Unclassified: 'Unclassified' };
const FF = 'Helvetica, Arial, sans-serif';
const r1 = (v) => (Math.round(v * 10) / 10).toString();
const pct = (v) => ixNum(v, 1) + '%';
const um2 = (v) => (v == null ? '–' : Math.round(v).toLocaleString() + ' µm²');
const hrs = (v) => ixNum(v, 1) + ' h';

const TH = {
  pub: { axis: '#000', text: '#000', sub: '#444', band: '#F4F4F4', box: '#000', dotEdge: '#000', ring: '#111', font: FF, fs: 11, cross: '#666' },
  dark: { axis: '#3A4148', text: '#A0A8B2', sub: '#C9D1DA', band: '#14181C', box: '#E7E9EC', dotEdge: '#0F1215', ring: '#FFFFFF', font: 'ui-monospace,Menlo,monospace', fs: 10, cross: '#8A929B', grid: '#23282D' },
};

function svgOpen(W, H, label) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" font-family="${FF}" role="img" aria-label="${esc(label)}"><rect x="0" y="0" width="${W}" height="${H}" fill="#FFFFFF"/>`;
}

function yAxis(x, y0, y1, ticks, sy, fmt = (v) => v) {
  let s = `<path d="M${x} ${y0}V${y1}" stroke="#000" stroke-width="1"/>`;
  ticks.forEach((v) => { const y = r1(sy(v)); s += `<path d="M${x - 4} ${y}H${x}" stroke="#000"/><text x="${x - 7}" y="${(+y + 3.8).toFixed(1)}" font-size="11" text-anchor="end" fill="#000">${fmt(v)}</text>`; });
  return s;
}

function xAxis(y, x0, x1, ticks, sx) {
  let s = `<path d="M${x0} ${y}H${x1}" stroke="#000" stroke-width="1"/>`;
  ticks.forEach((v) => { const x = r1(sx(v)); s += `<path d="M${x} ${y}V${y + 4}" stroke="#000"/><text x="${x}" y="${y + 16}" font-size="11" text-anchor="middle" fill="#000">${v}</text>`; });
  return s;
}

function niceMax(v, steps = [10, 20, 25, 50, 100]) {
  for (const s of steps) if (v <= s) return s;
  return Math.ceil(v / 50) * 50;
}

function quantile(sorted, q) {
  if (!sorted.length) return null;
  const pos = (sorted.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

function boxStats(sorted) {
  if (!sorted.length) return null;
  const q1 = quantile(sorted, 0.25), q3 = quantile(sorted, 0.75), med = quantile(sorted, 0.5), iqr = q3 - q1;
  return { n: sorted.length, q1, q3, med, mean: sorted.reduce((a, b) => a + b, 0) / sorted.length, min: sorted[0], max: sorted[sorted.length - 1],
    lo: sorted.find((v) => v >= q1 - 1.5 * iqr), hi: [...sorted].reverse().find((v) => v <= q3 + 1.5 * iqr) };
}

function seeded(seed = 7) { return () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; }; }

// ---------------------------------------------------------------- axes set by hand
// Settings: fig_axes[figure][axis] = { min, max, step } (any may be missing = automatic).
let FIG_ID = null;  // the figure being drawn (its axis settings apply)

function axSpec(key, id = FIG_ID) {
  const a = (((S.st && S.st.experiment.settings.fig_axes) || {})[id] || {})[key];
  return a && (a.min != null || a.max != null || a.step != null) ? a : null;
}

function stepTicks(a, b, step, maxN = 40) {
  const out = [];
  if (!(step > 0) || (b - a) / step > maxN) return null;
  for (let v = Math.ceil(a / step - 1e-9) * step; v <= b + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}

function rangeTicks(a, b) {  // nice steps that include both ends when they fall on a step
  const raw = (b - a) / 5;
  const p = 10 ** Math.floor(Math.log10(Math.max(raw, 1e-9)));
  const st = [1, 2, 2.5, 5, 10].map((m) => m * p).find((v) => v >= raw);
  return stepTicks(a, b, st) || [a, b];
}

// y range: the automatic one ({ ymax, ticks }, from 0) unless set by hand
function yRange(auto, key = 'y') {
  const sp = axSpec(key);
  if (!sp) return { ymin: 0, ymax: auto.ymax, ticks: auto.ticks };
  const ymin = sp.min != null ? sp.min : 0, ymax = sp.max != null ? sp.max : Math.max(auto.ymax, ymin + 1e-6);
  if (!(ymax > ymin)) return { ymin: 0, ymax: auto.ymax, ticks: auto.ticks };
  return { ymin, ymax, ticks: (sp.step && stepTicks(ymin, ymax, sp.step)) || (sp.min == null && sp.max == null ? auto.ticks : rangeTicks(ymin, ymax)), manual: true };
}

// time range: a drag-zoom wins, then the range set by hand, then everything
function xRange(zoom, hFull) {
  const sp = axSpec('x');
  const h0 = zoom ? zoom.h0 : sp && sp.min != null ? sp.min : 0;
  const h1 = zoom ? zoom.h1 : sp && sp.max != null ? sp.max : hFull;
  const ok = h1 > h0;
  const a = ok ? h0 : 0, b = ok ? h1 : hFull;
  return { h0: a, h1: b, ticks: (sp && sp.step && stepTicks(a, b, sp.step)) || hourTicks(a, b) };
}

// one zoomable time frame (axes + clip) inside a publication figure
function pubTime(x0, t, pw, ph, hFull, zoom, yticks, ymax, yfmt, ymin = 0) {
  const { h0, h1, ticks } = xRange(zoom, hFull);
  const sx = (h) => x0 + (h - h0) / (h1 - h0) * pw;
  const sy = (v) => t + ph - (Math.min(Math.max(v, ymin), ymax) - ymin) / (ymax - ymin) * ph;
  const cid = ixId();
  const s = `<defs><clipPath id="${cid}"><rect x="${x0}" y="${t - 1}" width="${pw}" height="${ph + 2}"/></clipPath></defs>`
    + yAxis(x0, t, t + ph, yticks, sy, yfmt) + xAxis(t + ph, x0, x0 + pw, ticks, sx);
  return { s, sx, sy, cid, h0, h1 };
}

function pubLegend(x, y, conds, label = (c) => c.name) {
  let s = '';
  conds.forEach((c, j) => {
    const yy = y + j * 16;
    s += `<path d="M${x} ${yy - 4}H${x + 22}" stroke="${esc(c.color)}" stroke-width="2"${c.dashed ? ' stroke-dasharray="6 4"' : ''}/><text x="${x + 28}" y="${yy}" font-size="11" fill="#000">${esc(label(c))}</text>`;
  });
  return s;
}

function shortName(n, k = 26) { return n.length > k ? n.slice(0, k - 1) + '…' : n; }

// ---------------------------------------------------------------- A: composition (stacked)
function figComposition(d, conds, zoom = null) {
  const pw = 230, ph = 180, gap = 34, l = 58, t = 34, b = 48, legW = 130;
  const W = l + conds.length * pw + (conds.length - 1) * gap + 28 + legW, H = t + ph + b;
  const hours = d.hours, hFull = hours[hours.length - 1] || 1;
  let s = svgOpen(W, H, 'Cell-cycle composition over time');
  const panels = [];
  conds.forEach((c, i) => {
    const x0 = l + i * (pw + gap);
    const yr = yRange({ ymax: 100, ticks: [0, 25, 50, 75, 100] });
    const f = pubTime(x0, t, pw, ph, hFull, zoom, yr.ticks, yr.ymax, (v) => v, yr.ymin);
    s += f.s + `<g clip-path="url(#${f.cid})">`;
    const cum = hours.map(() => 0);
    CATS.forEach((k) => {
      const vals = (c.composition[k] || []).map((v) => (v == null ? 0 : v));
      if (!vals.length) return;
      const up = hours.map((h, j) => `${r1(f.sx(h))} ${r1(f.sy(cum[j] + vals[j]))}`);
      const lo = hours.map((h, j) => `${r1(f.sx(h))} ${r1(f.sy(cum[j]))}`).reverse();
      s += `<path d="M${up.join('L')}L${lo.join('L')}Z" fill="${PUB[k]}" stroke="none"/>`;
      vals.forEach((v, j) => { cum[j] += v; });
    });
    s += '</g>';
    // redraw the axes over the areas
    s += `<path d="M${x0} ${t}V${t + ph}H${x0 + pw}" stroke="#000" fill="none"/>`;
    s += `<text x="${x0 + pw / 2}" y="${t - 12}" font-size="13" font-weight="700" text-anchor="middle" fill="${esc(c.color)}">${esc(c.name)} (n=${c.n})</text>`;
    s += `<text x="${x0 + pw / 2}" y="${H - 10}" font-size="12" text-anchor="middle" fill="#000">Time (h)</text>`;
    panels.push({ type: 'time', stack: true, x0, x1: x0 + pw, y0: t, y1: t + ph, h0: f.h0, h1: f.h1, hFull: [0, hFull], title: c.name, fmt: pct, cross: '#000',
      series: CATS.map((k) => ({ name: CAT_LABEL[k], color: PUB[k], hours, values: c.composition[k] || [] })) });
  });
  s += `<text x="16" y="${t + ph / 2}" font-size="12" text-anchor="middle" fill="#000" transform="rotate(-90 16 ${t + ph / 2})">% of cells</text>`;
  const lx = W - legW + 4;
  [...CATS].reverse().forEach((k, j) => {
    const y = t + 8 + j * 20;
    s += `<rect x="${lx}" y="${y}" width="14" height="12" fill="${PUB[k]}" stroke="${k === 'Unclassified' ? '#BBBBBB' : 'none'}"/><text x="${lx + 20}" y="${y + 10}" font-size="11" fill="#000">${CAT_LABEL[k]}</text>`;
  });
  return { svg: s + '</svg>', panels };
}

// ---------------------------------------------------------------- A2: composition of all cells
// Every cell in the images at each time point (not only founders), as the mean of the wells.
function figPopComposition(pop, conds, zoom = null) {
  const by = Object.fromEntries(pop.conditions.map((c) => [c.id, c]));
  const cs = conds.filter((c) => by[c.id]);
  const cats = ['G1', 'S', 'G2M', 'Colourless'];
  const pw = 230, ph = 180, gap = 34, l = 58, t = 34, b = 48, legW = 120;
  const W = l + cs.length * pw + (cs.length - 1) * gap + 28 + legW, H = t + ph + b;
  const hours = pop.hours, hFull = hours[hours.length - 1] || 1;
  let s = svgOpen(W, H, 'Cell-cycle composition of all cells over time');
  const panels = [];
  cs.forEach((c, i) => {
    const p = by[c.id];
    const x0 = l + i * (pw + gap);
    const yr = yRange({ ymax: 100, ticks: [0, 25, 50, 75, 100] });
    const f = pubTime(x0, t, pw, ph, hFull, zoom, yr.ticks, yr.ymax, (v) => v, yr.ymin);
    // the wells' mean fractions, rescaled to add up to exactly 100% at every time point
    const vals = {};
    cats.forEach((k) => { vals[k] = []; });
    hours.forEach((_, j) => {
      const raw = cats.map((k) => (p[k] && p[k].mean[j] != null ? p[k].mean[j] : null));
      const sum = raw.reduce((a, v) => a + (v || 0), 0);
      cats.forEach((k, ki) => { vals[k].push(raw[ki] == null || sum <= 0 ? null : raw[ki] / sum * 100); });
    });
    s += f.s + `<g clip-path="url(#${f.cid})">`;
    const cum = hours.map(() => 0);
    cats.forEach((k) => {
      const up = [], lo = [];
      let run = [];
      const flush = () => {
        if (run.length) s += `<path d="M${run.map((j) => `${r1(f.sx(hours[j]))} ${r1(f.sy(cum[j] + vals[k][j]))}`).join('L')}L${run.slice().reverse().map((j) => `${r1(f.sx(hours[j]))} ${r1(f.sy(cum[j]))}`).join('L')}Z" fill="${PUB[k]}" stroke="none"/>`;
        run = [];
      };
      hours.forEach((_, j) => { if (vals[k][j] == null) flush(); else run.push(j); });
      flush();
      hours.forEach((_, j) => { cum[j] += vals[k][j] || 0; });
      up.length = lo.length = 0;
    });
    s += '</g>';
    s += `<path d="M${x0} ${t}V${t + ph}H${x0 + pw}" stroke="#000" fill="none"/>`;
    const nw = p.wells.length;
    s += `<text x="${x0 + pw / 2}" y="${t - 12}" font-size="13" font-weight="700" text-anchor="middle" fill="${esc(c.color)}">${esc(shortName(c.name, 22))} (${nw} well${nw === 1 ? '' : 's'})</text>`;
    s += `<text x="${x0 + pw / 2}" y="${H - 10}" font-size="12" text-anchor="middle" fill="#000">Time (h)</text>`;
    panels.push({ type: 'time', stack: true, x0, x1: x0 + pw, y0: t, y1: t + ph, h0: f.h0, h1: f.h1, hFull: [0, hFull], title: c.name, fmt: pct, cross: '#000',
      series: cats.map((k) => ({ name: CAT_LABEL[k], color: PUB[k], hours, values: vals[k] }))
        .concat([{ name: 'Nuclei per well', color: '#FFFFFF', hours, values: p.total ? p.total.mean : [], fmt: (v) => ixNum(v, 0) }]) });
  });
  s += `<text x="16" y="${t + ph / 2}" font-size="12" text-anchor="middle" fill="#000" transform="rotate(-90 16 ${t + ph / 2})">% of cells</text>`;
  const lx = W - legW + 4;
  [...cats].reverse().forEach((k, j) => {
    const y = t + 8 + j * 20;
    s += `<rect x="${lx}" y="${y}" width="14" height="12" fill="${PUB[k]}"/><text x="${lx + 20}" y="${y + 10}" font-size="11" fill="#000">${CAT_LABEL[k]}</text>`;
  });
  return { svg: s + '</svg>', panels };
}

// ---------------------------------------------------------------- statistics on box plots
function statsOpts() { return { on: true, unit: 'auto', control: null, label: 'stars', hide_ns: true, ...((S.st.experiment.settings || {}).stats_opts || {}) }; }

function pText(p) {
  if (p == null || !isFinite(p)) return '–';
  return p < 1e-4 ? 'p < 0.0001' : `p = ${p < 0.01 ? p.toFixed(4) : p.toFixed(3)}`;
}

function pairLabel(pr) { return statsOpts().label === 'p' ? pText(pr.p) : pr.stars; }

// Brackets for the pairs of one family over boxes at x positions (by condition key), stacked so
// that none overlaps; returns {levels, draw(yTop)}.
function bracketLayout(fam, xOf) {
  const o = statsOpts();
  if (!o.on || !fam || !fam.ok) return { levels: 0, draw: () => '' };
  const pairs = fam.pairs.filter((pr) => xOf[pr.a] != null && xOf[pr.b] != null && (!o.hide_ns || pr.p < 0.05))
    .map((pr) => ({ ...pr, x1: Math.min(xOf[pr.a], xOf[pr.b]), x2: Math.max(xOf[pr.a], xOf[pr.b]) }))
    .sort((u, v) => (u.x2 - u.x1) - (v.x2 - v.x1));
  const rows = [];
  pairs.forEach((pr) => {
    let lv = 0;
    while ((rows[lv] || []).some((q) => !(pr.x2 < q.x1 + 4 || pr.x1 > q.x2 - 4))) lv++;
    (rows[lv] = rows[lv] || []).push(pr);
    pr.level = lv;
  });
  return {
    levels: rows.length,
    draw: (yBase) => pairs.map((pr) => {
      const y = yBase - pr.level * 16;
      const tip = `${pr.b_name} vs ${pr.a_name}: ${pText(pr.p)} (${fam.post || fam.test})`;
      return `<g><title>${esc(tip)}</title><path d="M${r1(pr.x1)} ${r1(y + 4)}V${r1(y)}H${r1(pr.x2)}V${r1(y + 4)}" fill="none" stroke="#000" stroke-width="1"/>`
        + `<text x="${r1((pr.x1 + pr.x2) / 2)}" y="${r1(y - 2.5)}" font-size="${o.label === 'p' ? 9.5 : 11}" text-anchor="middle" fill="#000">${esc(pairLabel(pr))}</text></g>`;
    }).join(''),
  };
}

function omnibusText(fam) {
  if (!fam || !fam.ok) return fam && fam.why ? fam.why : '';
  const unit = { cells: 'cells', images: 'images', colonies: 'colonies', wells: 'wells', experiments: 'experiments' }[fam.unit_label] || fam.unit_label;
  return `${fam.test.replace(' (Greenhouse-Geisser)', '')}, ${pText(fam.p)} · n = ${fam.groups.map((g) => g.n).join(', ')} ${unit}`;
}

// ---------------------------------------------------------------- B: durations (box + dots)
function figDurations(d, conds, st) {
  const phases = [['G1', 'G1 duration'], ['S', 'S duration'], ['G2M', 'G2/M duration']];
  const longest = Math.max(4, ...conds.map((c) => Math.min(18, c.name.length)));
  const cw = Math.max(58, Math.min(124, longest * 6.4 + 12)), l = 62, ph = 200, gap = 40, b = 64;
  const pw = Math.max(120, conds.length * cw + 20);
  const fams = Object.fromEntries(((st && st.families) || []).map((f) => [f.id, f]));
  const so = statsOpts();
  const cxOf = (pi, ci) => l + pi * (pw + gap) + 10 + cw * (ci + 0.5) + (pw - 20 - conds.length * cw) / 2;
  const layouts = phases.map(([k], pi) => bracketLayout(fams[k], Object.fromEntries(conds.map((c, ci) => [c.id, cxOf(pi, ci)]))));
  const levels = Math.max(0, ...layouts.map((x) => x.levels));
  const showTest = so.on && st && st.families && st.families.length;
  const t = 34 + (showTest ? 14 : 0) + levels * 16 + (levels ? 6 : 0);
  const W = l + phases.length * pw + (phases.length - 1) * gap + 16, H = t + ph + b;
  let s = svgOpen(W, H, 'Hours per observed stage');
  const rnd = seeded(7);
  const panels = [];
  phases.forEach(([k, title], pi) => {
    const x0 = l + pi * (pw + gap);
    let mx = 4;
    conds.forEach((c) => (c.durations[k] || []).forEach((v) => { mx = Math.max(mx, v); }));
    const autoMax = niceMax(mx * 1.05, [4, 6, 8, 10, 12, 16, 20, 24, 30, 36, 48, 60, 72, 96]);
    const st = autoMax <= 12 ? 2 : autoMax <= 24 ? 4 : autoMax <= 48 ? 12 : 24;
    const autoTicks = []; for (let v = 0; v <= autoMax + 1e-6; v += st) autoTicks.push(v);
    const { ymin, ymax, ticks } = yRange({ ymax: autoMax, ticks: autoTicks });
    const sy = (v) => t + ph - (Math.min(Math.max(v, ymin), ymax) - ymin) / (ymax - ymin) * ph;
    s += yAxis(x0, t, t + ph, ticks, sy) + `<path d="M${x0} ${t + ph}H${x0 + pw}" stroke="#000"/>`;
    s += `<text x="${x0 + pw / 2}" y="18" font-size="13" font-weight="700" text-anchor="middle" fill="#000">${title}</text>`;
    if (showTest) s += `<text x="${x0 + pw / 2}" y="32" font-size="9.5" text-anchor="middle" fill="#444">${esc(omnibusText(fams[k]))}${fams[k] && fams[k].ok && fams[k].unit !== 'objects' ? ` (◇ = ${fams[k].unit === 'wells' ? 'well' : 'experiment'} medians)` : ''}</text>`;
    s += layouts[pi].draw(t - 8);
    const panel = { type: 'box', x0, x1: x0 + pw, y0: t, y1: t + ph, boxes: [], dots: [], ring: '#111' };
    conds.forEach((c, ci) => {
      const cx = cxOf(pi, ci);
      const keys = (c.duration_keys || {})[k] || [];
      const pairs = (c.durations[k] || []).map((v, i) => [v, keys[i] || null]).sort((a, b2) => a[0] - b2[0]);
      const vals = pairs.map((p) => p[0]);
      const stt = boxStats(vals);
      const bw = cw * 0.56;
      if (stt) {
        s += `<path d="M${cx} ${r1(sy(stt.lo))}V${r1(sy(stt.q1))}M${cx} ${r1(sy(stt.q3))}V${r1(sy(stt.hi))}M${r1(cx - bw / 4)} ${r1(sy(stt.lo))}H${r1(cx + bw / 4)}M${r1(cx - bw / 4)} ${r1(sy(stt.hi))}H${r1(cx + bw / 4)}" stroke="#000" stroke-width="1.2" fill="none"/>`;
        s += `<rect x="${r1(cx - bw / 2)}" y="${r1(sy(stt.q3))}" width="${r1(bw)}" height="${r1(Math.max(0.5, sy(stt.q1) - sy(stt.q3)))}" fill="none" stroke="#000" stroke-width="1.2"/>`;
        pairs.forEach(([v, key]) => {
          const x = cx + (rnd() - 0.5) * cw * 0.62, y = sy(v);
          s += `<circle cx="${r1(x)}" cy="${r1(y)}" r="2.8" fill="${PUB[k]}" fill-opacity="0.85" stroke="#000" stroke-width="0.6"/>`;
          panel.dots.push({ x, y, key, text: `${c.name}: ${CAT_LABEL[k]} lasted ${hrs(v)}${key ? ` (cell ${key.split('-').pop()}, ${key.split('-')[0]})` : ''}` });
        });
        s += `<path d="M${r1(cx - bw / 2)} ${r1(sy(stt.med))}H${r1(cx + bw / 2)}" stroke="#000" stroke-width="2.2"/>`;
        // the values the statistics compare (one per well or experiment), SuperPlot style
        const fam = so.on && fams[k] && fams[k].ok ? fams[k] : null;
        const gr = fam && fam.groups.find((x) => x.key === c.id);
        if (gr && gr.units && gr.units.length) {
          const m = gr.units.length;
          gr.units.forEach((u, ui) => {
            const x = cx + (m > 1 ? (ui / (m - 1) - 0.5) * bw * 0.7 : 0), y = sy(u.v);
            s += `<path d="M${r1(x)} ${r1(y - 6)}L${r1(x + 6)} ${r1(y)}L${r1(x)} ${r1(y + 6)}L${r1(x - 6)} ${r1(y)}Z" fill="#FFFFFF" stroke="#000" stroke-width="1.6"/>`;
            panel.dots.push({ x, y, text: `${c.name}: ${u.label} median ${hrs(u.v)}` });
          });
        }
      }
      panel.boxes.push({ cx, half: cw / 2, name: c.name, title: `${c.name} · ${CAT_LABEL[k]}`, color: c.color, stats: stt, fmt: hrs });
      const name = c.name.length > 18 ? c.name.slice(0, 17) + '…' : c.name;
      s += `<text x="${r1(cx)}" y="${t + ph + 17}" font-size="11" text-anchor="middle" fill="${esc(c.color)}" font-weight="700">${esc(name)}<title>${esc(c.name)}</title></text><text x="${r1(cx)}" y="${t + ph + 31}" font-size="10" text-anchor="middle" fill="#444">n=${vals.length}</text>`;
    });
    panels.push(panel);
  });
  s += `<text x="16" y="${t + ph / 2}" font-size="12" text-anchor="middle" fill="#000" transform="rotate(-90 16 ${t + ph / 2})">Hours per observed stage</text>`;
  return { svg: s + '</svg>', panels };
}

// ---------------------------------------------------------------- lines over time, several panels
// panelsDef: [{ key or get(c) -> {mean, sem}, title, ymax/auto, ticks, fmt, ref }]
function figLines({ hours, conds, panelsDef, zoom, label, ylabel, legendFn, band = false, pw = 250, ph = 190, gap = 46 }) {
  const l = 62, b = 48;
  const t = 40 + conds.length * 16;
  const W = l + panelsDef.length * pw + (panelsDef.length - 1) * gap + 16, H = t + ph + b;
  const hFull = hours[hours.length - 1] || 1;
  let s = svgOpen(W, H, label);
  const panels = [];
  panelsDef.forEach((pd, pi) => {
    const x0 = l + pi * (pw + gap);
    const data = conds.map((c) => pd.get(c)).map((x) => x || { mean: [], sem: null });
    const xr = xRange(zoom, hFull);
    const auto = pd.scale ? pd.scale(visibleMax(hours, data.map((x) => ({ mean: x.mean, sem: band ? x.sem : null })), xr.h0, xr.h1)) : { ymax: pd.ymax, ticks: pd.ticks };
    const { ymin, ymax, ticks } = yRange(auto, pd.ax || 'y');
    const f = pubTime(x0, t, pw, ph, hFull, zoom, ticks, ymax, pd.fmtTick || ((v) => v), ymin);
    s += f.s;
    s += `<text x="${x0 + pw / 2}" y="18" font-size="13" font-weight="700" text-anchor="middle" fill="#000">${pd.title}</text>`;
    s += `<text x="${x0 + pw / 2}" y="${H - 10}" font-size="12" text-anchor="middle" fill="#000">Time (h)</text>`;
    if (pd.ylabel) s += `<text x="${x0 - 46}" y="${t + ph / 2}" font-size="12" text-anchor="middle" fill="#000" transform="rotate(-90 ${x0 - 46} ${t + ph / 2})">${pd.ylabel}</text>`;
    if (pd.ref != null) s += `<path d="M${x0} ${r1(f.sy(pd.ref))}H${x0 + pw}" stroke="#999" stroke-dasharray="4 4"/>`;
    s += `<g clip-path="url(#${f.cid})">`;
    conds.forEach((c, ci) => {
      const dd = data[ci];
      const up = [], lo = [], ln = [];
      dd.mean.forEach((v, j) => {
        if (v == null) return;
        const e = band && dd.sem ? (dd.sem[j] || 0) : 0;
        up.push(`${r1(f.sx(hours[j]))} ${r1(f.sy(v + e))}`); lo.push(`${r1(f.sx(hours[j]))} ${r1(f.sy(v - e))}`); ln.push(`${r1(f.sx(hours[j]))} ${r1(f.sy(v))}`);
      });
      if (!ln.length) return;
      if (band && dd.sem) s += `<path d="M${up.join('L')}L${lo.reverse().join('L')}Z" fill="${esc(c.color)}" fill-opacity="0.18" stroke="none"/>`;
      s += `<path d="M${ln.join('L')}" fill="none" stroke="${esc(c.color)}" stroke-width="2" stroke-linejoin="round"${c.dashed ? ' stroke-dasharray="6 4"' : ''}/>`;
    });
    s += '</g>';
    if (pi === 0) s += pubLegend(x0 + 8, 36, conds, legendFn);
    panels.push({ type: 'time', x0, x1: x0 + pw, y0: t, y1: t + ph, h0: f.h0, h1: f.h1, hFull: [0, hFull], sy: f.sy, title: pd.title, fmt: pd.fmt, ring: '#111', cross: '#666',
      series: conds.map((c, ci) => ({ name: c.name, color: c.color, dashed: c.dashed, hours, values: data[ci].mean, sem: band ? data[ci].sem : null })) });
  });
  if (ylabel) s += `<text x="16" y="${t + ph / 2}" font-size="12" text-anchor="middle" fill="#000" transform="rotate(-90 16 ${t + ph / 2})">${ylabel}</text>`;
  return { svg: s + '</svg>', panels };
}

function pctScale(v) {
  const ymax = niceMax(Math.max(10, v * 1.05), [20, 30, 40, 50, 60, 80, 100]);
  const st = ymax <= 30 ? 5 : ymax <= 60 ? 10 : 20;
  const ticks = []; for (let x = 0; x <= ymax + 1e-6; x += st) ticks.push(x);
  return { ymax, ticks };
}

function autoScale(v) { const r = niceTicks(Math.max(v * 1.05, 1e-6)); return { ymax: r.ymax, ticks: r.ticks }; }
function kFmt(v) { return v >= 10000 ? (v / 1000).toFixed(0) + 'k' : v >= 1000 ? (v / 1000).toFixed(1).replace(/\.0$/, '') + 'k' : String(+v.toFixed(2)); }

// C: phase fractions of the followed cells (cohort)
function figFractions(d, conds, zoom = null) {
  return figLines({ hours: d.hours, conds, zoom, label: 'Phase fractions over time', ylabel: '% of cells', legendFn: (c) => `${shortName(c.name)} (n=${c.n})`,
    panelsDef: [['G1', 'G1 (green)'], ['S', 'S (red)'], ['G2M', 'G2/M (yellow)']].map(([k, title]) => ({ title, fmt: pct, get: (c) => ({ mean: c.composition[k] || [], sem: null }), scale: pctScale })) });
}

// D: colonies over time (mean ± SEM across wells)
function figColonies(pop, conds, zoom = null) {
  const by = Object.fromEntries(pop.conditions.map((c) => [c.id, c]));
  const cs = conds.filter((c) => by[c.id] && by[c.id].colonies);
  return figLines({ hours: pop.hours, conds: cs, zoom, band: true, label: 'Colonies over time', pw: 280, gap: 70,
    legendFn: (c) => `${shortName(c.name)} (${by[c.id].wells.length} well${by[c.id].wells.length === 1 ? '' : 's'})`,
    panelsDef: [
      { title: 'Colony number', ylabel: 'Colonies per image', ax: 'y0', fmt: (v) => ixNum(v, 1), get: (c) => by[c.id].colonies, scale: autoScale },
      { title: 'Average colony area', ylabel: 'Mean colony area (µm²)', ax: 'y1', fmt: um2, fmtTick: kFmt, get: (c) => by[c.id].colony_area, scale: autoScale },
    ] });
}

// E: phase fractions, all cells per well (mean ± SEM across wells)
function figPopPhases(pop, conds, zoom = null) {
  const by = Object.fromEntries(pop.conditions.map((c) => [c.id, c]));
  const cs = conds.filter((c) => by[c.id]);
  return figLines({ hours: pop.hours, conds: cs, zoom, band: true, label: 'Phase fractions, all cells', ylabel: '% of cells', pw: 220, gap: 40,
    legendFn: (c) => `${shortName(c.name)} (${by[c.id].wells.length} well${by[c.id].wells.length === 1 ? '' : 's'})`,
    panelsDef: [['G1', 'G1'], ['S', 'S'], ['G2M', 'G2/M'], ['Colourless', 'Colourless']].map(([k, title]) => ({ title, fmt: pct, get: (c) => by[c.id][k], scale: pctScale })) });
}

// F: growth and confluence (mean ± SEM across wells)
function figGrowth(pop, conds, zoom = null) {
  const by = Object.fromEntries(pop.conditions.map((c) => [c.id, c]));
  const cs = conds.filter((c) => by[c.id]);
  return figLines({ hours: pop.hours, conds: cs, zoom, band: true, label: 'Growth and confluence', pw: 250, gap: 70,
    legendFn: (c) => `${shortName(c.name)} (${by[c.id].wells.length} well${by[c.id].wells.length === 1 ? '' : 's'})`,
    panelsDef: [
      { title: 'Cell number', ylabel: 'Fold of first frame', ax: 'y0', ref: 1, fmt: (v) => ixNum(v, 2) + '×', get: (c) => by[c.id].count_norm, scale: autoScale },
      { title: 'Nuclei per well', ylabel: 'Nuclei', ax: 'y1', fmt: (v) => ixNum(v, 0), fmtTick: kFmt, get: (c) => by[c.id].total, scale: autoScale },
      { title: 'Confluence', ylabel: '% of area covered', ax: 'y2', fmt: pct, get: (c) => by[c.id].confluence, scale: pctScale },
    ] });
}

// ---------------------------------------------------------------- box plots over time (colonies)
// One panel: groups of boxes at each time point, one box per condition.
function boxTimePanel({ s0 = '', x0, t, pw, ph, theme, times, conds, key, log = false, title = '', ylabel = '', ax = null, marks = null }) {
  const T = TH[theme];
  const nc = Math.max(1, conds.length), ng = Math.max(1, times.length);
  const gw = pw / ng, bw = Math.min(34, gw * 0.84 / nc);
  const area = key === 'areas';
  const getB = (c, ti) => (c[key] || [])[ti] || {};
  let vmax = 0, vmin = Infinity;
  conds.forEach((c) => times.forEach((_, ti) => {
    const b = getB(c, ti), st = b.stats;
    if (!st) return;
    vmax = Math.max(vmax, st.max != null ? st.max : st.hi);
    vmin = Math.min(vmin, st.min != null ? Math.max(1, st.min) : Math.max(1, st.lo));
  }));
  let sy, ticks, fmtTick;
  const sp = ax ? axSpec(ax) : null;
  if (area && log && vmax > 0) {
    let a = Math.floor(Math.log10(Math.max(1, vmin))), bb = Math.ceil(Math.log10(Math.max(10, vmax)));
    if (sp && sp.min > 0) a = Math.log10(sp.min);
    if (sp && sp.max > 0) bb = Math.log10(sp.max);
    if (!(bb > a)) { a = Math.floor(Math.log10(Math.max(1, vmin))); bb = a + 1; }
    sy = (v) => t + ph - (Math.min(Math.max(Math.log10(Math.max(v, 1e-9)), a), bb) - a) / (bb - a) * ph;
    ticks = []; for (let e = Math.ceil(a - 1e-9); e <= bb + 1e-9; e++) ticks.push(10 ** e);
    fmtTick = (v) => (v >= 1e6 ? v / 1e6 + 'M' : v >= 1e3 ? v / 1e3 + 'k' : String(v));
  } else {
    const r = niceTicks(Math.max(vmax * 1.05, area ? 100 : 4));
    const yr = ax ? yRange({ ymax: r.ymax, ticks: r.ticks }, ax) : { ymin: 0, ymax: r.ymax, ticks: r.ticks };
    sy = (v) => t + ph - (Math.min(Math.max(v, yr.ymin), yr.ymax) - yr.ymin) / (yr.ymax - yr.ymin) * ph;
    ticks = yr.ticks;
    fmtTick = area ? kFmt : (v) => String(+v.toFixed(2));
  }
  let s = s0;
  // bands and axes
  times.forEach((tt, ti) => { if (ti % 2 === 0) s += `<rect x="${r1(x0 + ti * gw)}" y="${t}" width="${r1(gw)}" height="${ph}" fill="${T.band}"/>`; });
  ticks.forEach((v) => {
    const y = r1(sy(v));
    if (theme === 'dark') s += `<path d="M${x0} ${y}H${x0 + pw}" stroke="${T.grid}"/>`;
    s += `<path d="M${x0 - 4} ${y}H${x0}" stroke="${T.axis}"/><text x="${x0 - 7}" y="${(+y + 3.8).toFixed(1)}" font-size="${T.fs + 1}" font-family="${T.font}" text-anchor="end" fill="${T.text}">${fmtTick(v)}</text>`;
  });
  s += `<path d="M${x0} ${t}V${t + ph}H${x0 + pw}" stroke="${T.axis}" fill="none"/>`;
  times.forEach((tt, ti) => { s += `<text x="${r1(x0 + (ti + 0.5) * gw)}" y="${t + ph + 16}" font-size="${T.fs + 1}" font-family="${T.font}" text-anchor="middle" fill="${T.text}">${ixNum(tt, 1)}</text>`; });
  s += `<text x="${x0 + pw / 2}" y="${t + ph + 34}" font-size="12" text-anchor="middle" fill="${T.sub}">Time (h)</text>`;
  if (title) s += `<text x="${x0 + pw / 2}" y="${t - (marks ? 24 : 14)}" font-size="13" font-weight="700" text-anchor="middle" fill="${theme === 'pub' ? '#000' : T.sub}">${title}</text>`;
  if (marks) times.forEach((tt, ti) => {
    const m = marks[ti];
    if (!m || !m.text) return;
    const xa = x0 + ti * gw + gw / 2 - (nc - 1) / 2 * bw, xb = x0 + ti * gw + gw / 2 + (nc - 1) / 2 * bw;
    s += `<g><title>${esc(m.tip)}</title>${nc > 1 ? `<path d="M${r1(xa)} ${t - 3}V${t - 7}H${r1(xb)}V${t - 3}" fill="none" stroke="${T.axis}" stroke-width="1"/>` : ''}<text x="${r1((xa + xb) / 2)}" y="${t - 9.5}" font-size="${m.small ? 8.5 : 10.5}" text-anchor="middle" fill="${T.text}">${esc(m.text)}</text></g>`;
  });
  if (ylabel) s += `<text x="${x0 - 46}" y="${t + ph / 2}" font-size="12" text-anchor="middle" fill="${T.sub}" transform="rotate(-90 ${x0 - 46} ${t + ph / 2})">${ylabel}</text>`;
  const panel = { type: 'box', x0, x1: x0 + pw, y0: t, y1: t + ph, boxes: [], dots: [], ring: T.ring };
  const rnd = seeded(11);
  const fmt = area ? um2 : (v) => ixNum(v, 1);
  times.forEach((tt, ti) => {
    conds.forEach((c, ci) => {
      const b = getB(c, ti), st = b.stats;
      const cx = x0 + ti * gw + gw / 2 + (ci - (nc - 1) / 2) * bw;
      panel.boxes.push({ cx, half: bw / 2, name: c.name, title: `${c.name} · ${ixNum(tt, 1)} h`, color: c.color, stats: st, fmt });
      if (!st) return;
      const hw = bw * 0.36;
      const edge = theme === 'pub' ? '#000' : c.color;
      s += `<path d="M${r1(cx)} ${r1(sy(st.lo))}V${r1(sy(st.q1))}M${r1(cx)} ${r1(sy(st.q3))}V${r1(sy(st.hi))}" stroke="${edge}" stroke-width="1.1"/>`;
      s += `<rect x="${r1(cx - hw)}" y="${r1(sy(st.q3))}" width="${r1(2 * hw)}" height="${r1(Math.max(0.6, sy(st.q1) - sy(st.q3)))}" fill="${esc(c.color)}" fill-opacity="${theme === 'pub' ? 0.35 : 0.28}" stroke="${edge}" stroke-width="1.1"/>`;
      // dots: every image for counts, a random sample of colonies for areas
      const vals = area ? (b.sample || []) : (b.values || []);
      const step = Math.max(1, Math.ceil(vals.length / 120));
      for (let i = 0; i < vals.length; i += step) {
        const v = vals[i];
        const x = cx + (rnd() - 0.5) * hw * 1.6, y = sy(v);
        s += `<circle cx="${r1(x)}" cy="${r1(y)}" r="1.9" fill="${esc(c.color)}" fill-opacity="0.75" stroke="${T.dotEdge}" stroke-width="0.4"/>`;
        const where = !area && b.labels && b.labels[i] ? ` (${b.labels[i]})` : '';
        panel.dots.push({ x, y, text: `${c.name} · ${ixNum(tt, 1)} h: ${area ? um2(v) : `${v} colonies${where}`}` });
      }
      s += `<path d="M${r1(cx - hw)} ${r1(sy(st.med))}H${r1(cx + hw)}" stroke="${theme === 'pub' ? '#000' : '#FFFFFF'}" stroke-width="2"/>`;
    });
  });
  return { s, panel };
}

// Population page: one dark box chart
function colonyBoxChart(dist, conds, key, log) {
  const W = 560, H = 300, x0 = 58, t = 14, pw = W - x0 - 10, ph = H - t - 50;
  const r = boxTimePanel({ x0, t, pw, ph, theme: 'dark', times: dist.times, conds, key, log,
    ylabel: key === 'areas' ? 'Colony area (µm²)' : 'Colonies per image' });
  return { svg: `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Box plots over time" style="width:100%;height:auto;display:block">${r.s}</svg>`, panels: [r.panel] };
}

// Figures page: both colony box plots side by side
// One marker per time point: the test across conditions there (stars or p), for the colony box plots.
function colonyMarks(st, measure, times) {
  const o = statsOpts();
  if (!o.on || !st || !st.families) return null;
  return times.map((tt) => {
    const f = st.families.find((x) => x.measure === measure && Math.abs(x.time - tt) < 1e-6);
    if (!f || !f.ok) return null;
    if (o.hide_ns && !(f.p < 0.05)) return null;
    const pairs = f.pairs.filter((pr) => pr.p < 0.05).map((pr) => `${pr.b_name} vs ${pr.a_name} ${pr.stars}`).join('; ');
    return { text: o.label === 'p' ? pText(f.p).replace(/^p = /, '') : f.stars, small: o.label === 'p',
      tip: `${f.title}: ${f.test}, ${pText(f.p)}${pairs ? `. ${f.post || ''}: ${pairs}` : ''}` };
  });
}

function figColonyBoxes(dist, conds, log, st) {
  const ng = Math.max(1, dist.times.length), nc = Math.max(1, conds.length);
  const pw = Math.max(320, Math.min(620, ng * (nc * 16 + 14))), ph = 210, l = 66, gap = 80;
  const mc = colonyMarks(st, 'count', dist.times), ma = colonyMarks(st, 'area', dist.times);
  const t = 40 + conds.length * 16 + (mc || ma ? 12 : 0);
  const W = l + 2 * pw + gap + 16, H = t + ph + 52;
  let s = svgOpen(W, H, 'Colonies over time, box plots');
  const a = boxTimePanel({ x0: l, t, pw, ph, theme: 'pub', times: dist.times, conds, key: 'counts', title: 'Colony number', ylabel: 'Colonies per image', ax: 'y0', marks: mc });
  const b = boxTimePanel({ x0: l + pw + gap, t, pw, ph, theme: 'pub', times: dist.times, conds, key: 'areas', log, title: `Colony area${log ? ' (log scale)' : ''}`, ylabel: 'Colony area (µm²)', ax: 'y1', marks: ma });
  s += a.s + b.s;
  // legend (boxes)
  conds.forEach((c, j) => { const y = 36 + j * 16; s += `<rect x="${l + 8}" y="${y - 10}" width="14" height="11" fill="${esc(c.color)}" fill-opacity="0.35" stroke="#000"/><text x="${l + 28}" y="${y}" font-size="11" fill="#000">${esc(shortName(c.name))}</text>`; });
  return { svg: s + '</svg>', panels: [a.panel, b.panel] };
}

// G: red vs green (FUCCI) plots at four time points, per condition, from the open experiment
function figScatter(sc, conds, tps, hours, gates) {
  const cell = 150, gap = 18, l = 92, t = 46, b = 50;
  const nr = conds.length, ncol = tps.length;
  const W = l + ncol * cell + (ncol - 1) * gap + 16, H = t + nr * cell + (nr - 1) * gap + b;
  let s = svgOpen(W, H, 'Red versus green at selected time points');
  const panels = [];
  const gx = axSpec('x') || {}, gy = axSpec('y') || {};
  const gx0 = gx.min != null ? gx.min : 0, gx1 = gx.max != null && gx.max > gx0 ? gx.max : 1.25;
  const ry0 = gy.min != null ? gy.min : 0, ry1 = gy.max != null && gy.max > ry0 ? gy.max : 1.25;
  conds.forEach((c, ri) => {
    tps.forEach((f, ci) => {
      const x0 = l + ci * (cell + gap), y0 = t + ri * (cell + gap);
      const d = sc[`${c.id}|${f}`];
      const sx = (g) => x0 + (Math.min(Math.max(g, gx0), gx1) - gx0) / (gx1 - gx0) * cell;
      const sy = (r) => y0 + cell - (Math.min(Math.max(r, ry0), ry1) - ry0) / (ry1 - ry0) * cell;
      s += `<rect x="${x0}" y="${y0}" width="${cell}" height="${cell}" fill="none" stroke="#000"/>`;
      if (d) {
        const n = d.r.length;
        for (let i = 0; i < n; i++) if (d.g[i] >= gx0 && d.g[i] <= gx1 && d.r[i] >= ry0 && d.r[i] <= ry1) s += `<circle cx="${r1(sx(d.g[i]))}" cy="${r1(sy(d.r[i]))}" r="1.3" fill="${[PUB.G1, PUB.S, PUB.G2M, '#999'][d.raw[i]]}" fill-opacity="0.6"/>`;
        s += `<path d="M${r1(sx(gates.g))} ${y0}V${y0 + cell}M${x0} ${r1(sy(gates.r))}H${x0 + cell}" stroke="#000" stroke-dasharray="4 3"/>`;
        const cnt = d.counts, tot = Math.max(1, d.n);
        s += `<text x="${x0 + cell - 4}" y="${y0 + 12}" font-size="9" text-anchor="end" fill="#000">n=${d.n}</text>`;
        if (!d.n) s += `<text x="${x0 + cell / 2}" y="${y0 + cell / 2}" font-size="10" text-anchor="middle" fill="#888">no cells (image left out?)</text>`;
        panels.push({ type: 'box', x0, x1: x0 + cell, y0, y1: y0 + cell, dots: [], boxes: [{ cx: x0 + cell / 2, half: cell / 2, name: c.name, color: c.color, stats: null,
          info: `<div class="h"><span class="sw" style="background:${c.color}"></span>${ixEsc(c.name)} · ${ixNum(hours[f], 1)} h</div>` + ['G1', 'S', 'G2/M', 'Dim'].map((nm, k) => `<div class="r"><span class="n">${nm}</span><span class="v">${Math.round(cnt[k] / tot * 100)}%</span></div>`).join('') + `<div class="r"><span class="n">Nuclei</span><span class="v">${d.n}</span></div>` }] });
      }
      if (ri === 0) s += `<text x="${x0 + cell / 2}" y="${t - 10}" font-size="12" font-weight="700" text-anchor="middle" fill="#000">${ixNum(hours[f], 1)} h</text>`;
      if (ci === 0) s += `<text x="${l - 34}" y="${y0 + cell / 2}" font-size="12" font-weight="700" text-anchor="middle" fill="${esc(c.color)}" transform="rotate(-90 ${l - 34} ${y0 + cell / 2})">${esc(shortName(c.name, 18))}</text>`;
    });
  });
  // axis ticks on the bottom row and the left column
  const gt = (gx.step && stepTicks(gx0, gx1, gx.step, 12)) || rangeTicks(gx0, gx1), rt = (gy.step && stepTicks(ry0, ry1, gy.step, 12)) || rangeTicks(ry0, ry1);
  tps.forEach((f, ci) => {
    const x0 = l + ci * (cell + gap), yb = t + nr * cell + (nr - 1) * gap;
    gt.forEach((v) => { const x = r1(x0 + (v - gx0) / (gx1 - gx0) * cell); s += `<path d="M${x} ${yb}V${yb + 3}" stroke="#000"/><text x="${x}" y="${yb + 13}" font-size="8" text-anchor="middle" fill="#000">${+v.toFixed(2)}</text>`; });
  });
  conds.forEach((c, ri) => {
    const y0 = t + ri * (cell + gap);
    rt.forEach((v) => { const y = r1(y0 + cell - (v - ry0) / (ry1 - ry0) * cell); s += `<path d="M${l - 3} ${y}H${l}" stroke="#000"/><text x="${l - 5}" y="${(+y + 3).toFixed(1)}" font-size="8" text-anchor="end" fill="#000">${+v.toFixed(2)}</text>`; });
  });
  s += `<text x="${l + (W - l) / 2}" y="${H - 8}" font-size="12" text-anchor="middle" fill="#000">PIP-mVenus (green, normalised)</text>`;
  s += `<text x="14" y="${t + (H - t - b) / 2}" font-size="12" text-anchor="middle" fill="#000" transform="rotate(-90 14 ${t + (H - t - b) / 2})">mCherry-Geminin (red, normalised)</text>`;
  return { svg: s + '</svg>', panels };
}
function experimentsCard(e, d) {
  const own = `<div class="linked-row"><div><strong>${esc(e.settings.experiment_name || e.summary.folder.split('/').pop())}</strong> <span class="muted small">this experiment</span><div class="small muted mono">${esc(e.summary.folder)}</div></div></div>`;
  const rows = (e.linked || []).map((l) => `<div class="linked-row"><div style="min-width:0"><strong>${esc(l.name)}</strong> ${l.ok ? `<span class="small muted">${esc((l.conditions || []).join(', '))}</span>` : `<span class="small" style="color:#F7DFA0">${esc(l.msg || '')}</span>`}<div class="small muted mono" style="overflow-wrap:anywhere">${esc(l.folder)}</div></div>
      <button class="btn sm" data-unlink="${esc(l.folder)}">${ICON.x} Remove</button></div>`).join('');
  return `<section class="card" aria-labelledby="ex-h"><div class="card-head"><div><h2 id="ex-h">Experiments in these figures</h2><p>Add other analysed experiments (another plate, another day) to compare them here and on the Population page. Conditions with the same name are pooled, wells stay the replicates, and time is in hours so different frame intervals line up. The other experiments are only read, never changed.</p></div>
      <button class="btn" id="link-add">${ICON.plus} Add analysed experiment…</button></div>
    <div>${own}${rows}</div>
    <div id="link-manual" style="display:none;gap:10px;flex-wrap:wrap;align-items:center"><input class="input" id="link-path" placeholder="/Users/you/Incucyte/Other experiment" style="flex:1 1 360px"><button class="btn" id="link-go">Add</button></div></section>`;
}

function stylesCard(d) {
  const rows = d.conditions.map((c, i) => `<div class="style-row" data-key="${esc(c.id)}">
      <span style="display:flex;flex-direction:column;gap:2px"><button class="btn sm" data-move="-1" aria-label="Move ${esc(c.name)} up" ${i === 0 ? 'disabled' : ''} style="min-height:20px;padding:0 6px">▲</button><button class="btn sm" data-move="1" aria-label="Move ${esc(c.name)} down" ${i === d.conditions.length - 1 ? 'disabled' : ''} style="min-height:20px;padding:0 6px">▼</button></span>
      <span style="display:flex;align-items:center;gap:10px;min-width:0"><input type="color" data-color value="${esc(c.color)}" aria-label="Colour of ${esc(c.name)}"><span style="min-width:0"><strong>${esc(c.name)}</strong> <span class="muted small">n=${c.n} cells${c.experiments.length > 1 ? ` · ${c.experiments.length} experiments` : ''}</span></span></span>
      <label class="chip"><input type="checkbox" data-dashed ${c.dashed ? 'checked' : ''}>Dashed line</label>
      <label class="chip"><input type="checkbox" data-show ${c.hidden ? '' : 'checked'}>Show</label>
      <span></span></div>`).join('');
  return `<section class="card tight" aria-labelledby="sty-h"><div class="card-head"><div><h2 id="sty-h">Conditions</h2><p>Order, colour and line style used in the figures and on the Population page. n is the number of founder cells (present in the first frame) followed through the movie.</p></div></div><div>${rows}</div></section>`;
}

// ---------------------------------------------------------------- the Figures page
const FIG_HINT_TIME = 'Hover for values. Zoom with − / + (or pinch), or drag across a time range to show only that part of the time axis; double-click or “Reset zoom” to go back. Saving keeps the time range you see.';
const FIG_HINT_BOX = 'Hover a box for median, quartiles and n, or a dot for its value. Zoom with − / + (or pinch).';

function condKey(n) { return String(n).toLowerCase().split(/\s+/).filter(Boolean).join(' '); }

function statsQuery(what) {
  const o = statsOpts();
  return `box_stats?what=${what}&unit=${o.unit}${o.control ? `&control=${encodeURIComponent(o.control)}` : ''}${what === 'colonies' && S.fig.every ? `&every=${S.fig.every}` : ''}`;
}

async function loadStats(ctx) {
  const o = statsOpts();
  if (!o.on) { ctx.stDur = null; ctx.stCol = null; return; }
  const [a, b] = await Promise.all([api(statsQuery('durations')).catch(() => null), api(statsQuery('colonies')).catch(() => null)]);
  ctx.stDur = a; ctx.stCol = b;
}

async function figData(force = false) {
  if (S.fig.ctx && !force) return S.fig.ctx;
  const ev = S.fig.every ? `?every=${S.fig.every}` : '';
  const [d, pop, dist] = await Promise.all([api('figures'), api('population'), api('colony_dist' + ev)]);
  S.fig.ctx = { d, pop, dist };
  await loadStats(S.fig.ctx);
  return S.fig.ctx;
}

function inFig(id, fn) {
  const prev = FIG_ID;
  FIG_ID = id;
  try { return fn(); } finally { FIG_ID = prev; }
}

const AX_TIME = { k: 'x', label: 'Time (h)' };
const AX_PCT = { k: 'y', label: '% of cells' };

function figDefs(ctx) {
  return figDefsRaw(ctx).map((d) => {
    const out = { ...d };
    if (d.build) out.build = (z) => inFig(d.id, () => d.build(z));
    if (d.variants) out.variants = d.variants.map((v) => ({ ...v, build: () => inFig(d.id, () => v.build()) }));
    return out;
  });
}

function figDefsRaw(ctx) {
  const { d, pop, dist, stDur, stCol } = ctx;
  const shown = d.conditions.filter((c) => !c.hidden && c.n > 0);
  const shownAll = d.conditions.filter((c) => !c.hidden);
  const byId = Object.fromEntries((dist.conditions || []).map((c) => [c.id, c]));
  const distConds = shownAll.filter((c) => byId[c.id]).map((c) => ({ ...byId[c.id], name: c.name, color: c.color, dashed: c.dashed }));
  const hasCol = pop.conditions.some((c) => c.colonies && c.colonies.mean.some((v) => v != null));
  const minObj = num(S.st.experiment.settings.min_object_um2);
  return [
    { id: 'fig-a', title: 'Cell-cycle composition over time', time: true, ok: shown.length,
      sub: 'Every cell present in the first frame (a founder) is followed through its divisions, along one daughter each time. Colourless = neither reporter on; Unclassified = no longer tracked (lost or left the field).',
      axes: [AX_TIME, AX_PCT], build: (z) => figComposition(d, shown, z) },
    { id: 'fig-a2', title: 'Cell-cycle composition of all cells over time', time: true, ok: shownAll.length,
      sub: 'Every cell in the images at each time point, not only the founders and their daughters: the share in each phase per well, averaged over the wells of a condition. Images left out (blurry) are skipped.',
      axes: [AX_TIME, AX_PCT], build: (z) => figPopComposition(pop, shownAll, z) },
    { id: 'fig-b', title: 'Time spent in each phase', ok: shown.length, clickable: true,
      sub: 'Hours in each phase, for phases seen from start to end inside the movie. One dot per cell per phase; boxes show the median and quartiles, whiskers 1.5 × IQR. Every value is ± one frame interval.',
      axes: [{ k: 'y', label: 'Hours per observed stage' }], build: () => figDurations(d, shown, stDur), stats: 'durations', st: stDur },
    { id: 'fig-c', title: 'Phase fractions of the followed cells', time: true, ok: shown.length,
      sub: 'Percent of the followed cells in each phase, per condition (the same cells as the composition figure).',
      axes: [AX_TIME, AX_PCT], build: (z) => figFractions(d, shown, z) },
    { id: 'fig-e', title: 'Phase fractions of all cells', time: true, ok: shownAll.length,
      sub: 'Every segmented cell in every frame, summarised per well; lines are the mean across wells and bands the SEM (the Population page, publication style).',
      axes: [AX_TIME, AX_PCT], build: (z) => figPopPhases(pop, shownAll, z) },
    { id: 'fig-f', title: 'Growth and confluence', time: true, ok: shownAll.length,
      sub: 'Cell number as a fold of the first frame, nuclei per well, and the area covered by cells (phase contrast). Mean ± SEM across wells.',
      axes: [AX_TIME, { k: 'y0', label: 'Cell number (fold)' }, { k: 'y1', label: 'Nuclei per well' }, { k: 'y2', label: 'Confluence (%)' }],
      build: (z) => figGrowth(pop, shownAll, z) },
    { id: 'fig-d', title: 'Colonies over time', time: S.fig.colStyle !== 'box', ok: hasCol, colonies: true,
      sub: `From the confluence mask: touching cells form one colony; objects under ${minObj} µm² are ignored as debris. ${S.fig.colStyle === 'box' ? 'Box plots: colonies per image (one dot per image) and the area of every colony (a sample of dots shown), at regular time points.' : 'Lines are the mean across wells, bands the SEM.'}`,
      axes: (S.fig.colStyle === 'box' ? [] : [AX_TIME]).concat([{ k: 'y0', label: 'Colonies per image' }, { k: 'y1', label: `Colony area (µm²)${S.fig.colStyle === 'box' && S.fig.log ? ', log' : ''}` }]),
      build: (z) => (S.fig.colStyle === 'box' ? figColonyBoxes(dist, distConds, S.fig.log, stCol) : figColonies(pop, shownAll, z)),
      stats: S.fig.colStyle === 'box' ? 'colonies' : null, st: stCol,
      variants: [
        { name: 'Colonies over time (lines)', build: () => figColonies(pop, shownAll, null) },
        { name: 'Colonies over time (box plots)', build: () => figColonyBoxes(dist, distConds, S.fig.log, stCol) },
      ] },
    { id: 'fig-g', title: 'Red versus green at selected time points', ok: true, scatter: true,
      axes: [{ k: 'x', label: 'Green (PIP-mVenus)' }, { k: 'y', label: 'Red (mCherry-Geminin)' }],
      hint: 'Type a time in hours and press Add (up to 8; each snaps to the nearest image); × removes one, Automatic goes back to four evenly spaced. The Population page uses the same time points. Hover a plot for the share in each gate. Zoom with − / + (or pinch).',
      sub: 'FUCCI-style plots for this experiment, coloured by gate (green G1, red S, yellow G2/M, grey dim), with the gates from step 2. Up to 600 cells per plot.' },
  ];
}

function colonyControls() {
  const st = S.fig.colStyle === 'box';
  const everyOpts = [['', 'Auto'], ['1', '1 h'], ['2', '2 h'], ['3', '3 h'], ['6', '6 h'], ['12', '12 h'], ['24', '24 h'], ['48', '48 h']];
  return `<div class="seg" role="group" aria-label="Colony plot style"><button type="button" data-colstyle="lines" aria-pressed="${!st}">Lines</button><button type="button" data-colstyle="box" aria-pressed="${st}">Box plots</button></div>
    ${st ? `<label class="small muted" style="display:inline-flex;align-items:center;gap:6px">Every <select class="input" id="col-every" style="height:34px">${everyOpts.map(([v, n]) => `<option value="${v}" ${String(S.fig.every || '') === v ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
      <label class="chip"><input type="checkbox" id="col-log" ${S.fig.log ? 'checked' : ''}>Log scale (area)</label>` : ''}`;
}

function figSection(def) {
  const empty = def.colonies ? '<p style="color:#444;padding:20px">No colony measurements yet: open the experiment again or re-run the analysis.</p>'
    : '<p style="color:#444;padding:20px">No conditions to show. Assign wells on the plate map (step 1) or tick “Show” above.</p>';
  const body = def.scatter ? '<p style="color:#444;padding:20px">Loading…</p>' : def.ok ? ixChart(def.build, { group: def.id }) : empty;
  return `<section class="card" aria-labelledby="${def.id}-h"><div class="card-head"><div><h2 id="${def.id}-h">${def.title}</h2><p>${def.sub}</p></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;justify-content:flex-end">${def.colonies ? colonyControls() : ''}${def.scatter ? scatterControls() : ''}${def.axes && def.axes.length ? `<button type="button" class="btn sm" data-axes="${def.id}" aria-expanded="${!!S.fig.axOpen[def.id]}">Axes${axSet(def.id) ? ' ·  set' : ''}</button>` : ''}${magControls(def.id)}<button class="btn" data-save="${def.id}" data-name="${esc(def.title)}">${ICON.down} Save PNG + SVG</button></div></div>
    ${def.axes && S.fig.axOpen[def.id] ? axesEditor(def) : ''}
    ${def.stats && def.ok ? statsControls(def) : ''}
    <div class="fig-card" id="${def.id}" data-mag="${S.fig.mag[def.id] || 0}">${body}</div>
    <p class="small muted">${def.hint || (def.time ? FIG_HINT_TIME : def.clickable ? FIG_HINT_BOX + ' Click a dot to open that cell.' : FIG_HINT_BOX)}</p>
    ${def.stats && def.ok && statsOpts().on ? statsTable(def.st, def.stats) : ''}</section>`;
}

const UNIT_NAME = { durations: 'Each cell', colonies: 'Each image / colony' };

function statsControls(def) {
  const o = statsOpts();
  const fam = def.st && def.st.families ? def.st.families.find((f) => f.ok) || def.st.families[0] : null;
  const av = (fam && fam.available) || {};
  const autoUnit = fam && fam.ok && o.unit === 'auto' ? ` (${fam.unit_label})` : '';
  const conds = (S.fig.ctx.d.conditions || []).filter((c) => !c.hidden);
  const opt = (v, label, n, min) => `<option value="${v}" ${o.unit === v ? 'selected' : ''} ${n != null && n < min ? 'disabled' : ''}>${label}${n != null ? ` · n ≥ ${n}` : ''}</option>`;
  return `<div class="stats-ctl" role="group" aria-label="Statistics">
    <label class="chip"><input type="checkbox" data-stopt="on" ${o.on ? 'checked' : ''}>Statistics</label>
    ${o.on ? `<label class="small muted" for="st-unit-${def.id}">Compare</label>
    <select class="input" id="st-unit-${def.id}" data-stopt="unit" title="What counts as one sample. Experiments (biological replicates) or wells (technical replicates) give honest p values; every cell on its own is descriptive only.">
      <option value="auto" ${o.unit === 'auto' ? 'selected' : ''}>Automatic${autoUnit}</option>
      ${opt('experiments', 'Experiments', av.experiments, 2)}${opt('wells', 'Wells', av.wells, 2)}${opt('objects', UNIT_NAME[def.stats], av.objects, 3)}</select>
    <select class="input" data-stopt="control" aria-label="Which pairs to compare">
      <option value="" ${!o.control ? 'selected' : ''}>All pairs</option>
      ${conds.map((c) => `<option value="${esc(c.id)}" ${o.control === c.id ? 'selected' : ''}>Each vs ${esc(c.name)}</option>`).join('')}</select>
    <select class="input" data-stopt="label" aria-label="Show stars or p values"><option value="stars" ${o.label !== 'p' ? 'selected' : ''}>Stars</option><option value="p" ${o.label === 'p' ? 'selected' : ''}>P values</option></select>
    <label class="chip"><input type="checkbox" data-stopt="hide_ns" ${o.hide_ns ? 'checked' : ''}>Hide ns</label>` : ''}</div>`;
}

function statsTable(st, what) {
  if (!st || !st.families) return '<p class="small muted">Statistics unavailable.</p>';
  const unitsOf = (f) => (f.units_of ? ` ${f.units_of}` : '');
  const fams = st.families;
  const famHTML = (f) => {
    if (!f.ok) return `<div class="st-fam"><strong>${esc(f.title)}</strong> <span class="small muted">${esc(f.why || '')}</span></div>`;
    const ctr = f.center === 'mean' ? 'mean' : 'median';
    const groups = f.groups.map((g) => `${esc(g.name)} ${num(f.center === 'mean' ? g.mean : g.median)}${unitsOf(f)} (n = ${g.n})`).join(' · ');
    const rows = f.pairs.map((pr) => `<tr><td>${esc(pr.b_name)} vs ${esc(pr.a_name)}</td><td class="mono">${pr.diff >= 0 ? '+' : ''}${num(pr.diff)}${unitsOf(f)}</td><td class="mono">${pr.ci ? `${num(pr.ci[0])} to ${num(pr.ci[1])}` : '–'}</td><td class="mono">${esc(pText(pr.p).replace(/^p = /, '').replace(/^p /, ''))}</td><td><strong>${esc(pr.stars)}</strong></td></tr>`).join('');
    return `<div class="st-fam"><div><strong>${esc(f.title)}</strong> · ${esc(f.test)}, ${esc(f.stat_label)} = ${num(f.stat)}, ${esc(pText(f.p))} <strong>${esc(f.stars)}</strong>${f.post ? ` · ${esc(f.post)}` : ''}</div>
      <div class="small muted">${esc(f.unit_label)}: ${ctr}s ${groups}</div>
      <table class="st-table"><thead><tr><th>Comparison</th><th>Difference (${ctr})</th><th>95% CI</th><th>${f.pairs.length > 1 ? 'Adjusted p' : 'p'}</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
  };
  let body;
  if (what === 'colonies') {
    const by = (m) => fams.filter((f) => f.measure === m);
    body = [['count', 'Colonies per image'], ['area', 'Colony area']].map(([m, name]) => `<details class="st-sub"><summary>${name}: ${by(m).filter((f) => f.ok && f.p < 0.05).length} of ${by(m).length} time points differ (p < 0.05)</summary>${by(m).map(famHTML).join('')}</details>`).join('');
  } else body = fams.map(famHTML).join('');
  const notes = [...new Set(fams.flatMap((f) => f.notes || []))];
  const cens = what === 'durations' ? fams.filter((f) => f.ok).map((f) => `${f.title.replace(' duration', '')}: ${f.groups.map((g) => g.censored || 0).reduce((a, b) => a + b, 0)}`).join(', ') : '';
  return `<details class="stats-box" ${S.fig.stOpen && S.fig.stOpen[what] === false ? '' : 'open'} data-stbox="${what}"><summary><strong>Statistics</strong></summary>
    ${notes.length ? `<div class="info">${ICON.info}<span>${notes.map(esc).join(' ')}</span></div>` : ''}
    ${body}
    ${cens ? `<p class="small muted">Phases cut off by the start or end of the movie are left out (${esc(cens)} per phase). Long phases are cut off more often, so medians lean short.</p>` : ''}
    <div class="st-methods"><span class="small muted">For the methods section</span><p>${esc(st.methods)}</p></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap"><button type="button" class="btn sm" data-stcopy="${what}">Copy methods text</button><button type="button" class="btn sm" data-stsave="${what}">${ICON.down} Save table (CSV)</button></div></details>`;
}

async function scatterData(tps) {
  const own = ownConditions();
  const out = {};
  await Promise.all(own.flatMap((c) => tps.map(async (f) => {
    try { out[`${c.id}|${f}`] = await api(`scatter?frame=${f}&condition=${c.id}&max=600`); } catch (e) { /* skip */ }
  })));
  return out;
}

function scatterConds(d) {
  const style = Object.fromEntries(d.conditions.map((c) => [c.id, c]));
  return ownConditions().map((c) => { const st = style[condKey(c.name)]; return st && st.hidden ? null : { id: c.id, name: c.name, color: st ? st.color : c.color }; }).filter(Boolean);
}

// Frames of the red-vs-green plots: the hours chosen on the Figures page (nearest frame), or four
// evenly spaced ones.
function scatterTps() {
  const hours = S.st.experiment.hours, nF = hours.length;
  const want = S.st.experiment.settings.scatter_hours || [];
  if (!want.length) return [...new Set([0, Math.round((nF - 1) / 3), Math.round(2 * (nF - 1) / 3), nF - 1])];
  const near = (h) => hours.reduce((bi, x, i) => (Math.abs(x - h) < Math.abs(hours[bi] - h) ? i : bi), 0);
  return [...new Set(want.map(near))].sort((a, b) => a - b);
}

function scatterControls() {
  const e = S.st.experiment, hours = e.hours, want = e.settings.scatter_hours || [];
  const chips = scatterTps().map((f) => `<span class="chip" style="gap:6px">${num(hours[f])} h${want.length ? `<button type="button" class="x-btn" data-sc-del="${hours[f]}" aria-label="Remove ${num(hours[f])} h">${ICON.x}</button>` : ''}</span>`).join('');
  return `<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"><span class="small muted">Time points${want.length ? '' : ' (automatic)'}</span>${chips}
    <span class="unit-input" style="width:118px"><input type="number" id="sc-add" min="0" max="${hours[hours.length - 1]}" step="any" placeholder="add" aria-label="Add a time point in hours"><span>h</span></span>
    <button type="button" class="btn sm" id="sc-add-btn">Add</button>${want.length ? '<button type="button" class="btn sm ghost" id="sc-auto">Automatic</button>' : ''}</div>`;
}

async function buildScatterFigure(d) {
  const tps = scatterTps();
  const sc = await scatterData(tps);
  const any = Object.values(sc)[0];
  const gates = { g: any ? any.gate_green : 0.3, r: any ? any.gate_red : 0.3 };
  return () => inFig('fig-g', () => figScatter(sc, scatterConds(d), tps, S.st.experiment.hours, gates));
}

function exName(x) {
  const [mv, fr] = x.split('@');
  let n = mv.replace('_', ' field ');
  if (fr != null) { const h = S.st.experiment.hours[+fr]; n += h != null ? ` at ${ixNum(h, 2)} h` : ` frame ${+fr + 1}`; }
  return n;
}

function excludedNote() {
  const ex = (S.st.experiment.settings.excluded || []);
  if (!ex.length) return '';
  const nice = ex.map(exName);
  return `<div class="info">${ICON.info}<span>Left out of all results (blurry images): <strong>${esc(nice.join(', '))}</strong>. Change this on the plate map (step 1) or in Segment &amp; gate.</span></div>`;
}

async function renderFigures(force = false) {
  if (!hasResults()) return needResults();
  if (!S.fig.ctx || force) main.innerHTML = header(5, 'Figures') + '<p class="muted">Loading…</p>';
  let ctx;
  try { ctx = await figData(true); } catch (e) { return fail(e); }
  if (S.view !== 'figures') return;
  const { d } = ctx;
  S.fig.data = d;
  const e = S.st.experiment;
  const defs = figDefs(ctx);
  main.innerHTML = header(5, 'Figures', `<button class="btn primary" id="fig-saveall">${ICON.down} Export all graphs &amp; figures</button>`)
    + excludedNote() + experimentsCard(e, d) + stylesCard(d)
    + defs.map(figSection).join('')
    + `<p class="small muted">Saved figures go to the “figures” folder inside “PIP-FUCCI results”: SVG for Illustrator or Inkscape, PNG at 3× for slides. The numbers behind them are in every table export (“Figure data”).</p>`;
  bindFigures(d);
  const g = defs.find((x) => x.scatter);
  if (g) {
    const build = await buildScatterFigure(d);
    const el = $('#fig-g');
    if (el && S.view === 'figures') { el.innerHTML = scatterConds(d).length ? ixChart(build, { group: 'fig-g' }) : '<p style="color:#444;padding:20px">No conditions of this experiment to show.</p>'; applyMag('fig-g'); }
  }
}

function rerenderFig(id) {
  const def = figDefs(S.fig.ctx).find((x) => x.id === id);
  const el = $('#' + id);
  if (!def || !el) return;
  el.closest('section').outerHTML = figSection(def);
  bindFigures(S.fig.data);
  if (def.scatter) buildScatterFigure(S.fig.data).then((build) => { const g = $('#fig-g'); if (g) { g.innerHTML = ixChart(build, { group: 'fig-g' }); applyMag('fig-g'); } });
}

function bindFigures(d) {
  const addLinked = async (path) => {
    try { S.st = await api('link', { path }); markSaved(); renderFigures(true); toast('Experiment added.'); } catch (err) { fail(err); }
  };
  const la = $('#link-add');
  if (la) la.onclick = async () => {
    try {
      let p;
      const shell = window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder;
      if (shell) p = await window.pywebview.api.pick_folder(); else p = (await api('pick_folder', {})).path;
      if (p) addLinked(p); else if (!S.st.native_dialogs) $('#link-manual').style.display = 'flex';
    } catch (err) { fail(err); }
  };
  const lg = $('#link-go'); if (lg) lg.onclick = () => addLinked($('#link-path').value.trim());
  $$('[data-unlink]').forEach((b) => b.onclick = async () => { try { S.st = await api('unlink', { path: b.dataset.unlink }); markSaved(); renderFigures(true); } catch (err) { fail(err); } });
  const styles = () => {
    const out = {};
    $$('.style-row').forEach((row, i) => {
      out[row.dataset.key] = { color: $('[data-color]', row).value, dashed: $('[data-dashed]', row).checked, hidden: !$('[data-show]', row).checked, order: i };
    });
    return out;
  };
  const save = async (st) => {
    try { const r = await api('settings', { styles: { ...(S.st.experiment.settings.styles || {}), ...st } }); S.st = r.state; markSaved(); renderFigures(); } catch (err) { fail(err); }
  };
  $$('.style-row input').forEach((inp) => inp.onchange = () => save(styles()));
  $$('.style-row [data-move]').forEach((b) => b.onclick = () => {
    const row = b.closest('.style-row');
    const sib = +b.dataset.move < 0 ? row.previousElementSibling : row.nextElementSibling;
    if (!sib) return;
    if (+b.dataset.move < 0) row.parentNode.insertBefore(row, sib); else row.parentNode.insertBefore(sib, row);
    save(styles());
  });
  $$('[data-save]').forEach((b) => b.onclick = async () => { b.disabled = true; await saveFigure(b.dataset.save, b.dataset.name, true); b.disabled = false; });
  $$('[data-colstyle]').forEach((b) => b.onclick = () => { S.fig.colStyle = b.dataset.colstyle; delete IX.zoom['fig-d']; rerenderFig('fig-d'); });
  const ce = $('#col-every');
  if (ce) ce.onchange = async () => {
    S.fig.every = ce.value ? parseFloat(ce.value) : null;
    try { S.fig.ctx.dist = await api('colony_dist' + (S.fig.every ? `?every=${S.fig.every}` : '')); await loadStats(S.fig.ctx); } catch (err) { return fail(err); }
    rerenderFig('fig-d');
  };
  const cl = $('#col-log'); if (cl) cl.onchange = () => { S.fig.log = cl.checked; rerenderFig('fig-d'); };
  const sa = $('#fig-saveall'); if (sa) sa.onclick = () => exportAll(sa);
  const setHours = async (hs) => {
    try { const r = await api('settings', { scatter_hours: hs }); S.st = r.state; markSaved(); } catch (err) { return fail(err); }
    rerenderFig('fig-g');
  };
  const addH = () => {
    const v = parseFloat($('#sc-add').value);
    const hours = S.st.experiment.hours;
    if (!isFinite(v) || v < 0 || v > hours[hours.length - 1] + 1e-6) return toast(`Enter a time between 0 and ${num(hours[hours.length - 1])} h.`, { bad: true });
    const cur = (S.st.experiment.settings.scatter_hours || []).length ? S.st.experiment.settings.scatter_hours : scatterTps().map((f) => hours[f]);
    if (cur.length >= 8) return toast('Up to 8 time points.', { bad: true });
    setHours([...cur, v]);
  };
  const sab = $('#sc-add-btn'); if (sab) sab.onclick = addH;
  const sai = $('#sc-add'); if (sai) sai.onkeydown = (ev) => { if (ev.key === 'Enter') addH(); };
  const sau = $('#sc-auto'); if (sau) sau.onclick = () => setHours([]);
  $$('[data-sc-del]').forEach((b) => b.onclick = () => {
    const h = parseFloat(b.dataset.scDel);
    const hours = S.st.experiment.hours;
    const left = (S.st.experiment.settings.scatter_hours || []).filter((x) => Math.abs(hours[scatterNear(x)] - h) > 1e-6);
    setHours(left);
  });
  $$('[data-axes]').forEach((b) => b.onclick = () => { S.fig.axOpen[b.dataset.axes] = !S.fig.axOpen[b.dataset.axes]; rerenderFig(b.dataset.axes); });
  $$('[data-stopt]').forEach((el) => el.onchange = async () => {
    const o = statsOpts();
    const k = el.dataset.stopt;
    o[k] = el.type === 'checkbox' ? el.checked : (el.value || null);
    if (k === 'unit' && !o.unit) o.unit = 'auto';
    try { const r = await api('settings', { stats_opts: o }); S.st = r.state; markSaved(); } catch (err) { return fail(err); }
    try { await loadStats(S.fig.ctx); } catch (err) { return fail(err); }
    rerenderFig('fig-b'); rerenderFig('fig-d');
  });
  $$('[data-stbox]').forEach((det) => det.ontoggle = () => { S.fig.stOpen = { ...(S.fig.stOpen || {}), [det.dataset.stbox]: det.open }; });
  $$('[data-stcopy]').forEach((b) => b.onclick = async () => {
    const st = b.dataset.stcopy === 'colonies' ? S.fig.ctx.stCol : S.fig.ctx.stDur;
    try { await navigator.clipboard.writeText(st.methods); toast('Methods text copied.'); } catch (err) { toast('Select the text and copy it with ⌘C.'); }
  });
  $$('[data-stsave]').forEach((b) => b.onclick = async () => {
    try { const r = await api(statsQuery(b.dataset.stsave) + '&save=1'); toast('Saved the statistics table.', { action: { label: 'Show in Finder', fn: () => api('reveal', { path: r.saved }).catch(fail) }, ms: 12000 }); } catch (err) { fail(err); }
  });
  $$('[data-ax-auto]').forEach((b) => b.onclick = () => saveAxes(b.dataset.axAuto, null));
  $$('.ax-in').forEach((inp) => inp.onchange = () => {
    const id = inp.dataset.axFig;
    const spec = {};
    $$(`.ax-in[data-ax-fig="${id}"]`).forEach((x) => {
      if (x.value === '') return;
      const v = parseFloat(x.value);
      if (!isFinite(v)) return;
      (spec[x.dataset.ax] = spec[x.dataset.ax] || {})[x.dataset.axK] = v;
    });
    for (const [ax, sp] of Object.entries(spec)) {
      if (sp.min != null && sp.max != null && sp.max <= sp.min) { toast(`“To” must be larger than “from”.`, { bad: true }); return; }
      if (sp.step != null && sp.step <= 0) { toast('Ticks must be a positive step.', { bad: true }); return; }
    }
    saveAxes(id, spec);
  });
  $$('.fig-card').forEach((c) => applyMag(c.id));
}

function scatterNear(h) {
  const hours = S.st.experiment.hours;
  return hours.reduce((bi, x, i) => (Math.abs(x - h) < Math.abs(hours[bi] - h) ? i : bi), 0);
}

function svgToPng(svgText, scale = 3) {
  return new Promise((resolve, reject) => {
    const m = /width="(\d+(?:\.\d+)?)" height="(\d+(?:\.\d+)?)"/.exec(svgText);
    const w = m ? +m[1] : 800, h = m ? +m[2] : 400;
    const img = new Image();
    img.onload = () => {
      const cv = document.createElement('canvas');
      cv.width = Math.round(w * scale); cv.height = Math.round(h * scale);
      const ctx = cv.getContext('2d');
      ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, cv.width, cv.height);
      ctx.drawImage(img, 0, 0, cv.width, cv.height);
      try { resolve(cv.toDataURL('image/png')); } catch (e) { reject(e); }
    };
    img.onerror = () => reject(new Error('Could not draw the figure as PNG'));
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgText);
  });
}

async function saveSVG(name, svgText, folder = null) {
  let png = null;
  try { png = await svgToPng(svgText); } catch (e) { /* SVG still saved */ }
  return api('save_figure', { name, svg: '<?xml version="1.0" encoding="UTF-8"?>\n' + svgText, png, folder });
}

async function saveFigure(id, name, announce) {
  const el = $('#' + id + ' svg');
  if (!el) return null;
  try {
    const r = await saveSVG(name, ixCleanSVG(el));
    if (announce) toast(`Saved “${name}” (PNG + SVG).`, { action: { label: 'Show in Finder', fn: () => api('reveal', { path: r.path }).catch(fail) }, ms: 12000 });
    return r;
  } catch (e) { fail(e); return null; }
}

// Every graph and figure, full time range, PNG + SVG, into one dated folder.
async function exportAll(btn) {
  const label = btn ? btn.innerHTML : '';
  const setLabel = (t) => { if (btn) btn.textContent = t; };
  if (btn) btn.disabled = true;
  try {
    setLabel('Preparing…');
    const ctx = await figData(S.view !== 'figures');
    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const folder = `All graphs ${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
    const jobs = [];
    figDefs(ctx).forEach((def, i) => {
      const letter = String.fromCharCode(65 + i);
      if (def.scatter) jobs.push({ name: `${letter} ${def.title}`, scatter: true });
      else if (!def.ok) return;
      else if (def.variants) def.variants.forEach((v, k) => jobs.push({ name: `${letter}${k + 1} ${v.name}`, build: v.build }));
      else jobs.push({ name: `${letter} ${def.title}`, build: () => def.build(null) });
    });
    let last = null, n = 0;
    for (const j of jobs) {
      setLabel(`Exporting ${++n} of ${jobs.length}…`);
      let out;
      if (j.scatter) { if (!scatterConds(ctx.d).length) continue; out = (await buildScatterFigure(ctx.d))(); } else out = j.build();
      last = await saveSVG(j.name, out.svg, folder);
    }
    if (statsOpts().on && last) {
      await api(statsQuery('durations') + `&save=1&folder=${encodeURIComponent(folder)}`).catch(() => null);
      await api(statsQuery('colonies') + `&save=1&folder=${encodeURIComponent(folder)}`).catch(() => null);
    }
    if (last) toast(`Exported ${n} graphs and figures (PNG + SVG)${statsOpts().on ? ' and the statistics tables' : ''}.`, { action: { label: 'Show in Finder', fn: () => api('reveal', { path: last.folder }).catch(fail) }, ms: 15000 });
  } catch (e) { fail(e); }
  if (btn) { btn.disabled = false; btn.innerHTML = label; }
}

// ---------------------------------------------------------------- axes editor
function axSet(id) { return Object.keys(((S.st.experiment.settings.fig_axes || {})[id]) || {}).length > 0; }

function axesEditor(def) {
  const cur = (S.st.experiment.settings.fig_axes || {})[def.id] || {};
  const inp = (ax, k, ph) => `<input type="number" step="any" class="ax-in" data-ax-fig="${def.id}" data-ax="${ax.k}" data-ax-k="${k}" value="${cur[ax.k] && cur[ax.k][k] != null ? cur[ax.k][k] : ''}" placeholder="${ph}" aria-label="${esc(ax.label)} ${k}">`;
  return `<div class="axes-ed" role="group" aria-label="Axes of ${esc(def.title)}">
    ${def.axes.map((ax) => `<div class="ax-row"><span class="ax-l">${esc(ax.label)}</span><span class="small muted">from</span>${inp(ax, 'min', 'auto')}<span class="small muted">to</span>${inp(ax, 'max', 'auto')}<span class="small muted">ticks every</span>${inp(ax, 'step', 'auto')}</div>`).join('')}
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><button type="button" class="btn sm ghost" data-ax-auto="${def.id}">All automatic</button><span class="small muted">Empty = automatic. Saved with the experiment and used in “Export all”. ${def.axes.some((a) => a.k === 'x') ? 'Dragging across the time axis still zooms for a closer look; “Reset zoom” returns to these axes.' : ''}</span></div></div>`;
}

async function saveAxes(id, spec) {
  const all = { ...(S.st.experiment.settings.fig_axes || {}) };
  if (spec && Object.keys(spec).length) all[id] = spec; else delete all[id];
  try { const r = await api('settings', { fig_axes: all }); S.st = r.state; markSaved(); } catch (err) { return fail(err); }
  delete IX.zoom[id];
  rerenderFig(id);
}

// ---------------------------------------------------------------- magnifying a figure
// 0 = fit the card width; otherwise a zoom factor on the figure's natural size.
const MAG_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3, 4];

function magControls(id) {
  const m = S.fig.mag[id] || 0;
  return `<div class="seg mag" role="group" aria-label="Zoom the figure"><button type="button" data-mag="${id}" data-step="-1" aria-label="Zoom out">−</button>
    <button type="button" data-mag="${id}" data-step="0" aria-label="Fit to width" title="Fit to width">${m ? Math.round(m * 100) + '%' : 'Fit'}</button>
    <button type="button" data-mag="${id}" data-step="1" aria-label="Zoom in">+</button></div>`;
}

function natWidth(svg) { return +(svg.getAttribute('width') || svg.viewBox.baseVal.width || 800); }

function fitScale(card, svg) { return Math.min(1, (card.clientWidth - 28) / natWidth(svg)); }

function applyMag(id, keepAt = null) {
  const card = document.getElementById(id);
  const svg = card && card.querySelector('svg');
  if (!svg) return;
  const m = S.fig.mag[id] || 0;
  const w = natWidth(svg);
  const before = keepAt ? { sx: card.scrollLeft, sy: card.scrollTop, w: svg.getBoundingClientRect().width } : null;
  svg.style.width = m ? Math.round(w * m) + 'px' : '100%';
  svg.style.maxWidth = m ? 'none' : w + 'px';
  svg.style.height = 'auto';
  card.dataset.mag = m;
  card.classList.toggle('magnified', !!m && w * m > card.clientWidth - 28);
  if (before && keepAt) {  // keep the point under the pointer in place
    const k = svg.getBoundingClientRect().width / (before.w || 1);
    card.scrollLeft = (before.sx + keepAt.x) * k - keepAt.x;
    card.scrollTop = (before.sy + keepAt.y) * k - keepAt.y;
  }
  const lab = document.querySelector(`[data-mag="${id}"][data-step="0"]`);
  if (lab) lab.textContent = m ? Math.round(m * 100) + '%' : 'Fit';
}

function stepMag(id, dir) {
  const card = document.getElementById(id);
  const svg = card && card.querySelector('svg');
  if (!svg) return;
  const cur = S.fig.mag[id] || fitScale(card, svg);
  let next;
  if (dir === 0) next = 0;
  else if (dir > 0) next = MAG_STEPS.find((x) => x > cur + 1e-3) || MAG_STEPS[MAG_STEPS.length - 1];
  else next = [...MAG_STEPS].reverse().find((x) => x < cur - 1e-3) || MAG_STEPS[0];
  if (next && Math.abs(next - fitScale(card, svg)) < 1e-3) next = 0;
  S.fig.mag[id] = next;
  applyMag(id);
}

function magBy(id, factor, at) {
  const card = document.getElementById(id);
  const svg = card && card.querySelector('svg');
  if (!svg) return;
  const cur = S.fig.mag[id] || fitScale(card, svg);
  S.fig.mag[id] = Math.min(6, Math.max(0.25, cur * factor));
  applyMag(id, at);
}

document.addEventListener('click', (ev) => {
  const b = ev.target.closest('[data-mag][data-step]');
  if (b) stepMag(b.dataset.mag, +b.dataset.step);
});

// pinch on a figure magnifies it: Ctrl + wheel (Chrome) or Safari's gesture events (the app)
document.addEventListener('wheel', (ev) => {
  if (!ev.ctrlKey) return;
  const card = ev.target.closest && ev.target.closest('.fig-card');
  if (!card) return;
  ev.preventDefault();
  ev.stopImmediatePropagation();
  const r = card.getBoundingClientRect();
  magBy(card.id, Math.exp(-ev.deltaY * 0.01), { x: ev.clientX - r.left, y: ev.clientY - r.top });
}, { passive: false, capture: true });

let gestureMag = null;
document.addEventListener('gesturestart', (ev) => {
  const card = ev.target.closest && ev.target.closest('.fig-card');
  if (!card) return;
  ev.preventDefault();
  const svg = card.querySelector('svg');
  gestureMag = { id: card.id, start: S.fig.mag[card.id] || (svg ? fitScale(card, svg) : 1) };
});
document.addEventListener('gesturechange', (ev) => {
  if (!gestureMag) return;
  ev.preventDefault();
  const card = document.getElementById(gestureMag.id);
  const r = card.getBoundingClientRect();
  S.fig.mag[gestureMag.id] = Math.min(6, Math.max(0.25, gestureMag.start * ev.scale));
  applyMag(gestureMag.id, { x: ev.clientX - r.left, y: ev.clientY - r.top });
});
document.addEventListener('gestureend', () => { gestureMag = null; });
