'use strict';

// ---------------------------------------------------------------- basics
const PH = ['G1', 'S', 'G2M'];
const PH_LABEL = ['G1', 'S', 'G2/M', 'Colourless'];
const PH_COL = ['#3FBF6A', '#E5486E', '#F2C94C', '#8C8C8C'];
const PH_TXT = ['#5FD487', '#F07C99', '#F2C94C', '#B4BAC2'];
const GATE_LABEL = ['G1', 'S', 'G2/M', 'Dim'];
const VIEWS = ['experiment', 'viewer', 'population', 'cells', 'figures'];
const COLORS = ['#7AB0FF', '#F59E5B', '#C59BFF', '#5FD4C4', '#F27FB2', '#B8C46A', '#A0A8B2'];

const S = {
  st: null, view: 'experiment', polling: false, activeCond: null,
  v: { well: null, field: null, frame: 0, layers: new Set(['phase', 'red', 'green', 'bgsub', 'masks']), ids: false, playing: false, sel: null,
       scatter: null, gg: null, gr: null, timer: null, cells: null, ver: 0, fps: 6 },
  fig: { data: null },
  pop: { metric: 'count_norm', scond: null, data: null },
  sc: { cond: null, sort: 'division', sel: null, data: null },
};

const main = document.getElementById('main');
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (v, d = 1) => (v == null || !isFinite(v) ? '–' : (+v).toFixed(d).replace(/\.?0+$/, '') || '0');
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

async function api(path, body) {
  const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch('/api/' + path, opt);
  let j = {};
  try { j = await r.json(); } catch (e) { /* not json */ }
  if (!r.ok || (j && j.error)) throw new Error((j && j.error) || 'Request failed: ' + path);
  return j;
}

function toast(msg, opts = {}) {
  const box = document.getElementById('toast');
  const el = document.createElement('div');
  el.className = 'toast' + (opts.bad ? ' bad' : '');
  el.innerHTML = `<span>${esc(msg)}</span>`;
  if (opts.action) {
    const b = document.createElement('button');
    b.className = 'btn sm';
    b.textContent = opts.action.label;
    b.onclick = () => { opts.action.fn(); el.remove(); };
    el.appendChild(b);
  }
  box.appendChild(el);
  setTimeout(() => el.remove(), opts.ms || (opts.bad ? 9000 : 5000));
}
const fail = (e) => toast(e.message || String(e), { bad: true });

const ICON = {
  folder: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path></svg>',
  check: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#3FBF6A" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l5 5L20 7"></path></svg>',
  warn: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#F2C94C" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l9.5 17h-19z"></path><path d="M12 10v4M12 17h.01"></path></svg>',
  info: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#9CC2FF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 11v5M12 8h.01"></path></svg>',
  arrow: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"></path></svg>',
  down: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"></path></svg>',
  play: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"></path></svg>',
  pause: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1"></rect><rect x="14" y="5" width="4" height="14" rx="1"></rect></svg>',
  plus: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"></path></svg>',
  x: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"></path></svg>',
};

// ---------------------------------------------------------------- state & navigation
async function refreshState() {
  S.st = await api('state');
  document.getElementById('version').textContent = S.st.version;
  updateSteps();
  renderTopLeft();
  if (S.st.progress && S.st.progress.running) startPolling();
  return S.st;
}

function hasResults() { return !!(S.st && S.st.experiment && S.st.experiment.has_results); }

function updateSteps() {
  $$('.step').forEach((b) => {
    const v = b.dataset.view;
    b.classList.toggle('active', v === S.view);
    b.disabled = v !== 'experiment' && !hasResults();
    b.setAttribute('aria-current', v === S.view ? 'page' : 'false');
  });
}

function setView(v) {
  stopPlay();
  S.view = v;
  updateSteps();
  render();
  main.scrollTop = 0;
  window.scrollTo(0, 0);
}

$$('.step').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));

function render() {
  try {
    if (S.view === 'viewer') return renderViewer();
    if (S.view === 'population') return renderPopulation();
    if (S.view === 'cells') return renderCells();
    if (S.view === 'figures') return renderFigures();
    return renderExperiment();
  } catch (e) { fail(e); }
}

function header(step, title, right = '') {
  return `<div class="head"><div class="title"><span class="eyebrow">Step ${step} of 5</span><h1>${title}</h1></div>${right}</div>`;
}

function needResults() {
  main.innerHTML = `<div class="empty"><h2>Run the analysis first</h2><p class="muted">Open an experiment and run the analysis on step 1. This screen fills in when it finishes.</p><button class="btn primary" id="go1">Go to Experiment</button></div>`;
  $('#go1').onclick = () => setView('experiment');
}

// ---------------------------------------------------------------- progress
// Progress is polled until the job ends. A failed request (the Mac slept, the page was
// suspended in the background) never stops the polling; it just tries again.
function startPolling(force = false) {
  if (S.polling && !force) return;
  S.polling = true;
  const gen = S.pollGen = (S.pollGen || 0) + 1;  // a restarted poller retires the old one
  let misses = 0;
  const tick = async () => {
    if (gen !== S.pollGen) return;
    let p;
    try { p = await api('progress'); misses = 0; } catch (e) {
      misses++;
      setTimeout(tick, Math.min(5000, 500 * 2 ** Math.min(misses, 4)));
      return;
    }
    if (gen !== S.pollGen) return;
    S.st.progress = p;
    S.lastProgressAt = Date.now();
    updateProgressUI(p);
    renderJobPill(p);
    if (p.running) { setTimeout(tick, 700); return; }
    S.polling = false;
    try { await refreshState(); } catch (e) { /* next poll catches up */ }
    const what = p.job === 'retrack' ? 'Re-tracking' : p.job === 'measure' ? 'Confluence' : 'Analysis';
    if (p.error) toast(`${what} failed: ` + p.error, { bad: true });
    else if (p.cancelled) toast('Cancelled.');
    else if (p.stage === 'Done' && p.job === 'measure') toast('Confluence and colonies updated.');
    else if (p.stage === 'Done' && p.job !== 'demo') toast(`${what} finished.`, { action: hasResults() ? { label: 'View results', fn: () => setView('viewer') } : null });
    if (p.job === 'measure') { S.v.ver++; if (S.view !== 'experiment') render(); }
    if (S.view === 'experiment') render();
  };
  tick();
}

// safety net: if anything ever stops the polling while a job runs, pick it up again
setInterval(async () => {
  if (!S.st) return;
  if (S.polling) {  // a poller that has gone quiet for 15 s is replaced
    if (Date.now() - (S.lastProgressAt || Date.now()) > 15000) startPolling(true);
    return;
  }
  try { const p = await api('progress'); if (p.running) startPolling(); else renderJobPill(p); } catch (e) { /* offline */ }
  return;
}, 5000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && S.st && S.st.progress && S.st.progress.running && Date.now() - (S.lastProgressAt || 0) > 3000) startPolling(true);
});

function renderJobPill(p) {
  const el = document.getElementById('job-pill');
  if (!el) return;
  if (!p || !p.running) { el.innerHTML = ''; return; }
  const pct = Math.round((p.fraction || 0) * 100);
  el.innerHTML = `<button class="pill" id="job-btn" style="cursor:pointer" title="${esc(p.stage || 'Working')}: ${pct}%. Click to see details."><span class="spin-dot" aria-hidden="true"></span>${esc(p.stage || 'Working')} <span class="mono">${pct}%</span><span class="minibar"><div style="width:${pct}%"></div></span></button>`;
  $('#job-btn').onclick = () => setView('experiment');
}

function fmtEta(s) {
  if (s == null || !isFinite(s)) return '';
  if (s < 60) return 'less than a minute left';
  const m = Math.round(s / 60);
  if (m < 60) return `about ${m} min left`;
  return `about ${Math.floor(m / 60)} h ${m % 60} min left`;
}

function progressHTML(p) {
  const pct = Math.round((p.fraction || 0) * 100);
  const frames = p.total ? `${p.done || 0} of ${p.total} ${p.unit || 'frames'}` : '';
  return `<div class="progress" id="prog">
    <div class="runbar"><strong id="prog-stage">${esc(p.stage || 'Working')}…</strong><span class="mono small muted" id="prog-txt">${pct}% · ${esc(frames)} ${esc(fmtEta(p.eta_s))}</span></div>
    ${p.workers > 1 ? `<span class="small muted">Using ${p.workers} processor cores. You can switch to other apps; the analysis keeps going and the Dock icon shows the progress.</span>` : ''}
    <div class="bar"><div id="prog-bar" style="width:${pct}%"></div></div>
    <div><button class="btn sm" id="btn-cancel">Cancel</button></div></div>`;
}

function updateProgressUI(p) {
  const bar = document.getElementById('prog-bar');
  if (bar) {
    const pct = Math.round((p.fraction || 0) * 100);
    bar.style.width = pct + '%';
    $('#prog-stage').textContent = (p.stage || 'Working') + '…';
    $('#prog-txt').textContent = `${pct}% · ${p.total ? `${p.done || 0} of ${p.total} ${p.unit || 'frames'}` : ''} ${fmtEta(p.eta_s)}`;
  } else if (p.running && S.view === 'experiment') {
    render();
  }
}

// ---------------------------------------------------------------- step 1: experiment
async function chooseFolder() {
  try {
    let r;
    const shell = window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder;
    if (shell) r = { path: await window.pywebview.api.pick_folder() };
    else r = await api('pick_folder', {});
    if (r.path) await openFolder(r.path);
    else if (!S.st.native_dialogs) { const m = $('#manual'); if (m) m.style.display = 'flex'; }
  } catch (e) { fail(e); }
}

async function openFolder(path) {
  try {
    S.st = await api('open', { path });
    S.v.well = null; S.sc.sel = null; S.sc.cond = null; S.pop.scond = null; S.activeCond = null;
    updateSteps();
    setView('experiment');
    if (S.st.progress && S.st.progress.running) startPolling();
  } catch (e) { fail(e); }
}

function renderWelcome() {
  const st = S.st;
  const p = st.progress || {};
  const recent = (st.recent || []).map((r) => `<button type="button" data-folder="${esc(r.folder)}"><span>${esc(r.name)}<br><span class="small muted mono">${esc(r.folder)}</span></span><span class="small muted">${esc(r.opened || '')}</span></button>`).join('');
  main.innerHTML = `<div class="welcome">
    <div style="display:flex;flex-direction:column;gap:10px"><span class="eyebrow">PIP-FUCCI · Incucyte</span>
      <h1>Analyse a PIP-FUCCI time-lapse experiment</h1>
      <p class="muted" style="font-size:15px">Open the folder you exported from the Incucyte: raw single-channel TIFFs for Phase, Green and Red. Results are saved next to the images, in a folder called “PIP-FUCCI results”.</p></div>
    ${p.running ? `<div class="card">${progressHTML(p)}</div>` : `<div style="display:flex;gap:12px;flex-wrap:wrap">
      <button class="btn primary" id="btn-open">${ICON.folder} Open Incucyte folder…</button>
      <button class="btn" id="btn-demo">Try with demo data</button></div>`}
    <div id="manual" style="display:none;gap:10px;flex-wrap:wrap;align-items:center">
      <input class="input" id="manual-path" placeholder="/Users/you/Incucyte/Export" style="flex:1 1 360px">
      <button class="btn" id="manual-open">Open</button></div>
    ${recent ? `<div class="card tight"><h3>Recent experiments</h3><div class="recent">${recent}</div></div>` : ''}
    <p class="small muted">The demo creates a small synthetic experiment in “${esc(st.demo_folder)}” and analyses it, so you can try every screen.</p>
  </div>`;
  const o = $('#btn-open'); if (o) o.onclick = chooseFolder;
  const d = $('#btn-demo'); if (d) d.onclick = async () => { try { await api('demo', {}); await refreshState(); render(); } catch (e) { fail(e); } };
  const c = $('#btn-cancel'); if (c) c.onclick = () => api('cancel', {}).catch(fail);
  $('#manual-open').onclick = () => openFolder($('#manual-path').value.trim());
  $$('.recent button').forEach((b) => b.onclick = () => openFolder(b.dataset.folder));
}

function runCard(e) {
  const p = S.st.progress || {};
  let body;
  const sm = e.summary;
  const nMovies = Object.values(sm.fields || {}).reduce((a, f) => a + f.length, 0);
  if (p.running) body = progressHTML(p);
  else {
    let msg, btns;
    if (!e.has_results) {
      msg = `<strong>Ready to analyse</strong><span class="muted"> · ${nMovies} movie${nMovies === 1 ? '' : 's'} (well × field), ${sm.n_frames} frames each.</span>`;
      btns = `<button class="btn primary" id="btn-run" ${e.channels_ok ? '' : 'disabled'}>Run analysis ${ICON.arrow}</button>`;
    } else if (e.stale === 'segmentation') {
      msg = `<strong>Settings changed</strong><span class="muted"> · segmentation settings changed since the last run. Re-run to update the results.</span>`;
      btns = `<button class="btn primary" id="btn-run">Re-run analysis</button>`;
    } else if (e.stale === 'tracking') {
      msg = `<strong>Tracking settings changed</strong><span class="muted"> · updating tracks takes seconds; segmentation is kept.</span>`;
      btns = `<button class="btn" id="btn-run">Re-run everything</button><button class="btn primary" id="btn-retrack">Update tracking</button>`;
    } else if (e.old_format) {
      msg = `<strong>Analysed with an earlier version</strong><span class="muted"> · re-run to get background removal, tracking through colourless cells and confluence of all cells. Plate map, gates and settings are kept.</span>`;
      btns = `<button class="btn" id="btn-next">Look at current results</button><button class="btn primary" id="btn-run">Re-run analysis</button>`;
    } else {
      msg = `<strong>Analysed</strong><span class="muted"> · ${esc((e.run || '').replace('T', ' '))} · ${e.n_cells.toLocaleString()} detections in ${e.n_tracks.toLocaleString()} tracks.</span>`;
      btns = `<button class="btn" id="btn-run">Re-run analysis</button><button class="btn primary" id="btn-next">Continue to segmentation ${ICON.arrow}</button>`;
    }
    body = `<div class="runbar"><div>${msg}${e.channels_ok ? '' : `<div class="small" style="color:#F7DFA0;margin-top:4px">${esc(e.channels_msg)}</div>`}</div><div style="display:flex;gap:10px;flex-wrap:wrap">${btns}</div></div>`;
    if (p.error) body = `<div class="err">The last run failed: ${esc(p.error)}</div>` + body;
  }
  return `<section class="card" aria-label="Analysis">${body}</section>`;
}

function dataCard(e) {
  const sm = e.summary, s = e.settings;
  const fpw = (sm.fields_per_well || []).join(', ') || '–';
  const hours = sm.n_frames > 1 ? (sm.n_frames - 1) * s.interval_min / 60 : 0;
  const issues = (sm.issues || []).map((t) => `<div class="check">${ICON.warn}<span>${esc(t)}</span></div>`).join('');
  const img = sm.image || {};
  const okLines = [];
  if (img.width) okLines.push(`${img.width} × ${img.height} px, ${esc(img.dtype)}${img.rgb ? ' colour' : ', single channel'}`);
  okLines.push('Well, field and timepoint read from file names');
  const opts = (sel) => ['phase', 'green', 'red', 'ignore'].concat(sel === 'rgb' ? ['rgb'] : [])
    .map((o) => `<option value="${o}" ${o === sel ? 'selected' : ''}>${{ phase: 'Phase contrast', green: 'Green (PIP-mVenus)', red: 'Red (mCherry-Geminin)', ignore: 'Ignore', rgb: 'Merged colour' }[o]}</option>`).join('');
  const groups = (sm.groups || []).map((g) => `<div class="setrow" style="padding:8px 0"><div class="lbl"><span class="mono" style="color:var(--text2);font-size:13px">${esc(g.key)}</span><span>${g.count} files</span></div>
    <select class="input chmap" data-key="${esc(g.key)}" aria-label="Channel for ${esc(g.key)}" style="flex:0 0 220px">${opts(s.channel_map[g.key])}</select></div>`).join('');
  return `<section class="card" aria-labelledby="data-h">
    <div class="card-head"><div><h2 id="data-h">Image data</h2><p>Raw single-channel images, not merged colour exports.</p></div>
      <button class="btn" id="btn-reveal">${ICON.folder} Show in Finder</button></div>
    <div class="path">${esc(sm.folder)}</div>
    <div class="stats">
      <div class="stat"><span class="small muted">Wells</span><span class="v">${sm.wells.length}</span></div>
      <div class="stat"><span class="small muted">Fields / well</span><span class="v">${esc(fpw)}</span></div>
      <div class="stat"><span class="small muted">Frames</span><span class="v">${sm.n_frames}</span></div>
      <div class="stat"><span class="small muted">Movie length</span><span class="v">${num(hours)} h</span></div>
    </div>
    <div style="display:flex;flex-direction:column;gap:8px">${issues}${okLines.map((t) => `<div class="check">${ICON.check}<span>${esc(t)}</span></div>`).join('')}</div>
    <div><h3>Channels</h3><p class="small muted" style="margin-top:2px">Files are grouped by folder and name. Check each group is the right channel.</p>${groups}</div>
  </section>`;
}

function plateCard(e) {
  const s = e.settings, sm = e.summary;
  const present = new Set(sm.wells);
  const conds = s.conditions || [];
  if (!S.activeCond || !conds.find((c) => c.id === S.activeCond)) S.activeCond = conds.length ? conds[0].id : null;
  const byId = Object.fromEntries(conds.map((c) => [c.id, c]));
  const rows = 'ABCDEFGH'.split('').map((r) => {
    const cells = Array.from({ length: 12 }, (_, i) => {
      const w = r + (i + 1);
      const isPar = s.parental_well === w;
      const c = byId[s.wells[w]];
      const col = isPar ? '#A0A8B2' : c ? c.color : null;
      const label = `Well ${w}` + (present.has(w) ? (isPar ? ', parental control' : c ? ', ' + c.name : ', not assigned') : ', no images');
      return `<button type="button" class="well" data-well="${w}" ${present.has(w) ? '' : 'disabled'} title="${esc(label)}" aria-label="${esc(label)}"
        style="${col ? `background:${col};border-color:${col}` : present.has(w) ? 'border-color:#6B7480' : ''}${isPar ? ';background-image:repeating-linear-gradient(45deg,#0F1215 0 3px,transparent 3px 6px)' : ''}"></button>`;
    }).join('');
    return `<div class="prow"><span class="hdr">${r}</span>${cells}</div>`;
  }).join('');
  const counts = {};
  Object.entries(s.wells).forEach(([w, c]) => { if (present.has(w)) counts[c] = (counts[c] || 0) + 1; });
  const condRows = conds.map((c) => `<div class="cond">
      <button type="button" class="chip cond-pick" data-id="${c.id}" aria-pressed="${c.id === S.activeCond}" title="Paint wells with this condition"><span class="dot" style="background:${c.color}"></span><span class="mono small muted">${counts[c.id] || 0}</span></button>
      <input type="text" class="input cond-name" data-id="${c.id}" value="${esc(c.name)}" aria-label="Condition name">
      <input type="color" class="cond-color" data-id="${c.id}" value="${c.color}" aria-label="Condition colour">
      <button type="button" class="btn sm cond-del" data-id="${c.id}" aria-label="Remove ${esc(c.name)}">${ICON.x}</button></div>`).join('');
  const parOpts = ['<option value="">None</option>'].concat(sm.wells.map((w) => `<option value="${w}" ${s.parental_well === w ? 'selected' : ''}>${w}</option>`)).join('');
  return `<section class="card" aria-labelledby="plate-h">
    <div class="card-head"><div><h2 id="plate-h">Plate map</h2><p>Pick a condition, then click wells to assign or clear them. Statistics are done per well.</p></div></div>
    <div style="overflow-x:auto"><div class="plate"><div class="prow"><span></span>${Array.from({ length: 12 }, (_, i) => `<span class="hdr">${i + 1}</span>`).join('')}</div>${rows}</div></div>
    <div class="cond-list">${condRows}<div><button type="button" class="btn ghost sm" id="cond-add">${ICON.plus} Condition</button></div></div>
    <div class="setrow"><div class="lbl"><label for="parental">Parental (non-FUCCI) control</label><span>Optional. Sets the autofluorescence floor for the gates.</span></div>
      <select id="parental" class="input" style="flex:0 0 160px">${parOpts}</select></div>
  </section>`;
}

function setRow(id, label, hint, value, unit, step, derived, placeholder = '') {
  return `<div class="setrow"><div class="lbl"><label for="${id}">${label}</label><span>${hint}</span></div>
    <div class="unit-input"><input id="${id}" type="number" min="0" step="${step}" value="${esc(value)}" placeholder="${esc(placeholder)}" data-field="${id}"><span>${unit}</span></div>
    <div class="der">${derived}</div></div>`;
}

function settingsCard(e) {
  const s = e.settings, d = e.derived;
  const warns = (d.warnings || []).map((w) => `<div class="check">${ICON.warn}<span>${esc(w)}</span></div>`).join('');
  const sens = Math.round(s.sensitivity * 100);
  return `<section class="card" aria-labelledby="acq-h" style="gap:0">
    <div class="card-head" style="padding-bottom:16px"><div><h2 id="acq-h">Acquisition settings</h2><p>Entered per experiment, never hard-coded. Every time and distance parameter below is derived from them, and all axes are in hours.</p></div><span class="small muted">Derived for this experiment</span></div>
    ${setRow('interval_min', 'Frame interval', 'Link distance, gap closing, smoothing, division logic', s.interval_min, 'min', 1, `1 h smoothing = ${d.smoothing_frames} frame${d.smoothing_frames === 1 ? '' : 's'} · durations ± ${num(s.interval_min)} min`)}
    ${setRow('pixel_um', 'Pixel size', '0.62 µm/px is the Incucyte at 20X', s.pixel_um, 'µm/px', 0.01, `1 µm = ${num(1 / s.pixel_um, 2)} px`)}
    ${setRow('nucleus_um', 'Typical nucleus diameter', 'Segmentation diameter', s.nucleus_um, 'µm', 0.5, `${num(d.nucleus_px)} px across`)}
    ${setRow('min_object_um2', 'Smallest object counted as a cell', 'Confluence and colonies: anything smaller in the phase-contrast mask is treated as debris and ignored. Changing it only re-measures, it does not re-run the analysis.', s.min_object_um2, 'µm²', 10, `≈ ${num(d.min_object_diam_um)} µm across · ${d.min_object_px} px`)}
    ${setRow('bg_radius_um', 'Background removal size', 'Green and red haze broader than this (autofluorescence, uneven medium) is subtracted before nuclei are found: a top-hat filter. Empty = one nucleus diameter.', s.bg_radius_um == null ? '' : s.bg_radius_um, 'µm', 1, `${num(d.bg_radius_um)} µm = ${num(d.bg_radius_px)} px${s.bg_radius_um == null ? ' (automatic)' : ''}`, 'auto')}
    ${setRow('max_speed_um_h', 'Max cell speed', 'Search radius = speed × interval ÷ pixel size', s.max_speed_um_h, 'µm/h', 1, `${num(d.search_radius_px)} px search radius per frame`)}
    ${setRow('max_gap_h', 'Max gap', 'Joins a track across frames where the cell was missed', s.max_gap_h, 'h', 0.5, `Closes gaps of up to ${d.max_gap_frames} frame${d.max_gap_frames === 1 ? '' : 's'}`)}
    ${setRow('colourless_h', 'Follow colourless cells for up to', 'Right after division neither reporter is on. Cells are followed through that stretch on phase contrast, for up to this long.', s.colourless_h, 'h', 1, `${d.colourless_frames} frames · shown grey as “Colourless”`)}
    ${setRow('smoothing_h', 'Phase smoothing window', 'Averages phase evidence over this time', s.smoothing_h, 'h', 0.5, `${d.smoothing_frames} frame${d.smoothing_frames === 1 ? '' : 's'}`)}
    <div class="setrow"><div class="lbl"><span class="name">Segmentation</span><span>${S.st.cellpose ? 'Cellpose is installed and can be used instead.' : 'Built-in nuclear segmentation on red + green.'}</span></div>
      ${S.st.cellpose ? `<select class="input" id="method" style="flex:0 0 160px"><option value="classic" ${s.method === 'classic' ? 'selected' : ''}>Built-in</option><option value="cellpose" ${s.method === 'cellpose' ? 'selected' : ''}>Cellpose</option></select>` : ''}
      <div class="range-row" style="flex:1 1 300px"><label for="sensitivity"><span>Sensitivity to dim cells</span><span class="mono muted">${sens}%</span></label>
        <input type="range" id="sensitivity" min="0" max="100" step="5" value="${sens}"><span class="small muted">Higher keeps dim cells at the G1/S and S/G2 transitions; too high picks up background.</span></div></div>
    <div class="info" style="margin:6px 0 12px">${ICON.info}<span><strong>Division detection: </strong>${esc(d.division_rule)}</span></div>
    ${warns ? `<div class="warn" style="margin-bottom:14px">${warns}</div>` : ''}
  </section>`;
}

function renderExperiment() {
  const e = S.st.experiment;
  if (!e) return renderWelcome();
  const s = e.settings;
  main.innerHTML = header(1, 'Experiment setup', `<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">
      <div style="display:flex;flex-direction:column;gap:6px;min-width:280px"><label for="exp-name" class="small muted">Experiment name</label><input id="exp-name" class="input" value="${esc(s.experiment_name)}"></div>
      <button class="btn" id="btn-other">${ICON.folder} Open another folder…</button></div>`)
    + runCard(e) + `<div class="grid2">${dataCard(e)}${plateCard(e)}</div>` + settingsCard(e)
    + `<p class="small muted">Settings, software versions and thresholds are saved with every output, so any figure can be traced back to how it was made.</p>`;

  const save = async (patch) => {
    try {
      const r = await api('settings', patch); S.st = r.state; markSaved(); render();
      if (r.result && r.result.action === 'remeasure') startPolling();
    } catch (err) { fail(err); }
  };
  $('#exp-name').onchange = (ev) => save({ experiment_name: ev.target.value });
  $('#btn-other').onclick = chooseFolder;
  $('#btn-reveal').onclick = () => api('reveal', { path: e.summary.folder }).catch(fail);
  const run = $('#btn-run'); if (run) run.onclick = async () => { try { await api('run', {}); await refreshState(); render(); } catch (err) { fail(err); } };
  const rt = $('#btn-retrack'); if (rt) rt.onclick = async () => { try { await api('retrack', {}); await refreshState(); render(); } catch (err) { fail(err); } };
  const nx = $('#btn-next'); if (nx) nx.onclick = () => setView('viewer');
  const cc = $('#btn-cancel'); if (cc) cc.onclick = () => api('cancel', {}).catch(fail);
  $$('.chmap').forEach((sel) => sel.onchange = () => save({ channel_map: { ...s.channel_map, [sel.dataset.key]: sel.value } }));
  $$('input[data-field]').forEach((inp) => inp.onchange = () => save({ [inp.dataset.field]: inp.value === '' ? null : parseFloat(inp.value) }));
  const sens = $('#sensitivity');
  sens.oninput = () => { sens.parentElement.querySelector('.mono').textContent = sens.value + '%'; };
  sens.onchange = () => save({ sensitivity: parseInt(sens.value, 10) / 100 });
  const m = $('#method'); if (m) m.onchange = () => save({ method: m.value });
  $('#parental').onchange = (ev) => save({ parental_well: ev.target.value || null });
  $$('.well').forEach((b) => b.onclick = () => {
    const w = b.dataset.well;
    const wells = { ...s.wells };
    if (!S.activeCond) return toast('Add a condition first.');
    if (wells[w] === S.activeCond) delete wells[w]; else wells[w] = S.activeCond;
    save({ wells });
  });
  $$('.cond-pick').forEach((b) => b.onclick = () => { S.activeCond = b.dataset.id; render(); });
  const conds = () => s.conditions.map((c) => ({ ...c }));
  $$('.cond-name').forEach((inp) => inp.onchange = () => save({ conditions: conds().map((c) => c.id === inp.dataset.id ? { ...c, name: inp.value || c.name } : c) }));
  $$('.cond-color').forEach((inp) => inp.onchange = () => save({ conditions: conds().map((c) => c.id === inp.dataset.id ? { ...c, color: inp.value } : c) }));
  $$('.cond-del').forEach((b) => b.onclick = () => {
    const id = b.dataset.id;
    const wells = Object.fromEntries(Object.entries(s.wells).filter(([, c]) => c !== id));
    save({ conditions: conds().filter((c) => c.id !== id), wells });
  });
  $('#cond-add').onclick = () => {
    const cs = conds();
    let n = cs.length + 1;
    while (cs.find((c) => c.id === 'c' + n)) n++;
    cs.push({ id: 'c' + n, name: 'Condition ' + n, color: COLORS[(n - 1) % COLORS.length] });
    S.activeCond = 'c' + n;
    save({ conditions: cs });
  };
}

// ---------------------------------------------------------------- step 2: viewer + gates
function stopPlay() {
  S.v.playing = false;
  if (S.v.timer) { clearTimeout(S.v.timer); S.v.timer = null; }
}

const NAV_ICON = {
  first: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 5v14M18 6l-7 6 7 6"></path></svg>',
  prev: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6"></path></svg>',
  next: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"></path></svg>',
  last: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 5v14M6 6l7 6-7 6"></path></svg>',
};

function condOfWell(w) {
  const s = S.st.experiment.settings;
  const c = s.conditions.find((x) => x.id === s.wells[w]);
  return w === s.parental_well ? 'Parental' : c ? c.name : 'Unassigned';
}

function renderViewer() {
  const e = S.st.experiment;
  if (!hasResults()) return needResults();
  const movies = e.movies;
  if (!S.v.well || !movies.find((m) => m.well === S.v.well && m.field === S.v.field)) { S.v.well = movies[0].well; S.v.field = movies[0].field; }
  const nF = e.hours.length;
  S.v.frame = clamp(S.v.frame, 0, nF - 1);
  const s = e.settings;
  const mv = movies.find((m) => m.well === S.v.well && m.field === S.v.field);
  const shape = mv.shape || [1040, 1408];
  S.v.shape = shape;
  const scalePct = (50 / s.pixel_um) / shape[1] * 100;
  const opts = movies.map((m) => `<option value="${m.well}|${m.field}" ${m.well === S.v.well && m.field === S.v.field ? 'selected' : ''}>${m.well} · ${esc(condOfWell(m.well))} · field ${m.field}</option>`).join('');
  const layerDefs = [['phase', 'Phase contrast', '#8A929B'], ['green', 'PIP-mVenus', '#3FBF6A'], ['red', 'mCherry-Geminin', '#E5486E'],
    ['bgsub', 'Remove background', '#9CC2FF'], ['masks', 'Nucleus outlines', '#E7E9EC'], ['ids', 'Cell numbers', '#F2C94C'], ['confluence', 'Confluence area', '#5AB8FF']];
  const layers = layerDefs.map(([k, n, c]) => `<label class="chip" title="${k === 'bgsub' ? 'Show red and green with the autofluorescent background subtracted, as the analysis sees them' : k === 'confluence' ? 'Area counted as covered by cells (from phase contrast, all cells whatever their colour)' : ''}"><input type="checkbox" data-layer="${k}" ${(k === 'ids' ? S.v.ids : S.v.layers.has(k)) ? 'checked' : ''}><span class="dot" style="background:${c};border-radius:3px"></span>${n}</label>`).join('');
  main.innerHTML = header(2, 'Segment &amp; gate', `<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <span class="mono small" style="padding:6px 10px;border-radius:6px;background:var(--panel2);color:var(--text2)">${num(s.interval_min)} min · ${num(s.pixel_um, 3)} µm/px</span>
      <select class="input" id="movie" aria-label="Well and field">${opts}</select></div>`)
    + `<div class="row-wrap">
      <section class="card tight" style="flex:3 1 560px" aria-label="Image viewer">
        <div class="legend" role="group" aria-label="Layers">${layers}</div>
        <div class="viewer-wrap" id="vwrap" style="aspect-ratio:${shape[1]} / ${shape[0]}">
          <img id="frame-img" alt="Field of view with segmented nuclei">
          <svg class="ids-layer" id="ids" viewBox="0 0 ${shape[1]} ${shape[0]}" preserveAspectRatio="none" aria-label="Cell numbers"></svg>
          <div class="ov" style="left:12px;top:12px;pointer-events:none" id="ov-time">t = 0 h</div>
          <div class="ov" style="right:12px;top:12px;pointer-events:none">${S.v.well} · field ${S.v.field}</div>
          <div class="scale" style="width:${scalePct}%;pointer-events:none"><div></div><span>50 µm</span></div>
        </div>
        <div class="frame-nav" role="group" aria-label="Frame">
          <button class="btn" id="f-first" aria-label="First frame" title="First frame">${NAV_ICON.first}</button>
          <button class="btn" id="f-b5" aria-label="Back 5 frames" title="Back 5 frames">−5</button>
          <button class="btn" id="f-prev" aria-label="Previous frame" title="Previous frame (←)">${NAV_ICON.prev}</button>
          <button class="play" id="play" aria-label="Play" title="Play / pause (Space)">${ICON.play}</button>
          <button class="btn" id="f-next" aria-label="Next frame" title="Next frame (→)">${NAV_ICON.next}</button>
          <button class="btn" id="f-f5" aria-label="Forward 5 frames" title="Forward 5 frames">+5</button>
          <button class="btn" id="f-last" aria-label="Last frame" title="Last frame">${NAV_ICON.last}</button>
          <span style="display:inline-flex;align-items:center;gap:6px;margin-left:8px" class="small muted"><label for="f-num">Frame</label><input type="number" id="f-num" min="1" max="${nF}" value="${S.v.frame + 1}"> of ${nF}</span>
          <select class="input" id="fps" aria-label="Playback speed" style="height:40px;margin-left:auto">${[2, 4, 6, 10].map((v) => `<option value="${v}" ${S.v.fps === v ? 'selected' : ''}>${v} frames/s</option>`).join('')}</select>
        </div>
        <div class="scrub">
          <input type="range" id="scrub" min="0" max="${nF - 1}" step="1" value="${S.v.frame}" aria-label="Time">
          <div style="display:flex;flex-direction:column;align-items:flex-end;font-family:var(--mono);font-size:12px;color:var(--text2);min-width:120px">
            <span style="font-size:14px;color:var(--text)" id="t-label"></span><span id="f-label"></span></div>
        </div>
        <div id="sel-info"></div>
        <p class="small muted"><span class="kbd">←</span> <span class="kbd">→</span> one frame · <span class="kbd">Shift</span> + arrow five frames · <span class="kbd">Space</span> play or pause · click a cell to select it and follow it through the movie.</p>
      </section>
      <div style="flex:2 1 340px;min-width:0;display:flex;flex-direction:column;gap:22px">
        <section class="card tight" aria-labelledby="gate-h">
          <div class="card-head"><div><h2 id="gate-h">Phase gates</h2><p>Red vs green for every nucleus in this field at <span id="gate-t">0</span> h. Drag a gate line or use the sliders; every cell is reclassified.</p></div>
            <button class="btn sm" id="auto-gates">Auto</button></div>
          <canvas id="scatter" style="width:100%;aspect-ratio:1.07;border-radius:8px;background:#0F1215;touch-action:none" role="img" aria-label="Scatter of green versus red with gates"></canvas>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(150px,100%),1fr));gap:12px">
            <div class="range-row"><label for="gg"><span>Green gate</span><span class="mono muted" id="gg-l"></span></label><input type="range" id="gg" min="0.02" max="1.2" step="0.005"></div>
            <div class="range-row"><label for="gr"><span>Red gate</span><span class="mono muted" id="gr-l"></span></label><input type="range" id="gr" min="0.02" max="1.2" step="0.005"></div>
          </div>
          <div class="stack" id="stack"></div>
          <div class="classes" id="classes"></div>
        </section>
        <section class="card tight" aria-labelledby="det-h">
          <h2 id="det-h">Detection</h2>
          <div class="stats"><div class="stat"><span class="small muted">Nuclei here</span><span class="v" id="st-det">–</span></div>
            <div class="stat" title="Cells with neither reporter on, followed on phase contrast"><span class="small muted">Colourless</span><span class="v" id="st-cl">–</span></div>
            <div class="stat" title="Area covered by all cells, from phase contrast"><span class="small muted">Confluence</span><span class="v" id="st-conf">–</span></div>
            <div class="stat" title="Separate objects in the confluence mask (touching cells form one colony)"><span class="small muted">Colonies</span><span class="v" id="st-col">–</span></div>
            <div class="stat"><span class="small muted">Mean colony area</span><span class="v" id="st-cola" style="font-size:18px">–</span></div>
            <div class="stat"><span class="small muted">Tracks (all)</span><span class="v">${e.n_tracks.toLocaleString()}</span></div></div>
          <div class="setrow" style="border-top:1px solid #23282D;padding-top:12px"><div class="lbl"><label for="v-minobj">Smallest object counted</label><span>Smaller specks in the confluence mask are debris: drawn with an <span style="color:#FF8C26">orange outline</span> and left out of confluence and colonies.</span></div>
            <div class="unit-input" style="flex:0 0 140px"><input id="v-minobj" type="number" min="0" step="10" value="${esc(s.min_object_um2)}"><span>µm²</span></div></div>
          <p class="small muted">Confluence is the area covered by every cell in phase contrast, coloured or not; turn on “Confluence area” to see it. Colourless cells (neither reporter on, mostly right after division) are followed on phase contrast and drawn as dashed circles under “Cell numbers”.</p>
          <div><div class="kv"><span class="muted">Method</span><span>${s.method === 'cellpose' ? 'Cellpose' : 'Built-in'} on red + green</span></div>
          <div class="kv"><span class="muted">Background</span><span class="mono">top-hat, ${num(e.derived.bg_radius_um)} µm</span></div>
          <div class="kv"><span class="muted">Diameter</span><span class="mono">${num(e.derived.nucleus_px)} px (${num(s.nucleus_um)} µm)</span></div>
          <div class="kv"><span class="muted">Registration</span><span>Phase contrast, per field</span></div></div>
        </section>
        <div style="display:flex;justify-content:flex-end;gap:10px;flex-wrap:wrap">
          <button class="btn primary" id="to-pop">Population results ${ICON.arrow}</button></div>
      </div></div>`;

  $('#movie').onchange = (ev) => { const [w, f] = ev.target.value.split('|'); S.v.well = w; S.v.field = parseInt(f, 10); S.v.sel = null; stopPlay(); renderViewer(); };
  $$('input[data-layer]').forEach((c) => c.onchange = () => {
    const k = c.dataset.layer;
    if (k === 'ids') { S.v.ids = c.checked; drawIds(); if (c.checked) loadCells(); return; }
    if (c.checked) S.v.layers.add(k); else S.v.layers.delete(k);
    loadFrame();
  });
  const scrub = $('#scrub');
  scrub.oninput = () => goFrame(parseInt(scrub.value, 10));
  $('#f-num').onchange = (ev) => goFrame((parseInt(ev.target.value, 10) || 1) - 1);
  $('#f-first').onclick = () => goFrame(0);
  $('#f-last').onclick = () => goFrame(nF - 1);
  $('#f-prev').onclick = () => goFrame(S.v.frame - 1);
  $('#f-next').onclick = () => goFrame(S.v.frame + 1);
  $('#f-b5').onclick = () => goFrame(S.v.frame - 5);
  $('#f-f5').onclick = () => goFrame(S.v.frame + 5);
  $('#fps').onchange = (ev) => { S.v.fps = parseInt(ev.target.value, 10); };
  $('#play').onclick = togglePlay;
  $('#to-pop').onclick = () => setView('population');
  $('#auto-gates').onclick = async () => { try { await api('gates', { auto: true }); S.v.ver++; markSaved(); await refreshState(); loadScatter(); loadFrame(); } catch (err) { fail(err); } };
  const img = $('#frame-img');
  img.onload = () => { if (S.v.playing) S.v.timer = setTimeout(advance, Math.max(0, 1000 / S.v.fps - (performance.now() - S.v.t0))); };
  img.onerror = () => { if (S.v.playing) S.v.timer = setTimeout(advance, 300); };
  $('#ids').onclick = pickCell;
  $('#v-minobj').onchange = async (ev) => {
    const v = parseFloat(ev.target.value);
    if (!isFinite(v) || v < 0) return;
    try {
      const r = await api('settings', { min_object_um2: v });
      S.st = r.state; markSaved();
      if (!S.v.layers.has('confluence')) { S.v.layers.add('confluence'); const c = $('input[data-layer=confluence]'); if (c) c.checked = true; }
      S.v.ver++; loadFrame();
      if (r.result.action === 'remeasure') startPolling();
    } catch (err) { fail(err); }
  };
  setupGates();
  loadFrame();
  loadScatter();
}

function togglePlay() {
  const b = $('#play');
  if (S.v.playing) { stopPlay(); if (b) { b.innerHTML = ICON.play; b.setAttribute('aria-label', 'Play'); } loadFrame(); loadScatter(); return; }
  S.v.playing = true;
  if (b) { b.innerHTML = ICON.pause; b.setAttribute('aria-label', 'Pause'); }
  advance();
}

let scatterT;
function goFrame(f) {
  const nF = S.st.experiment.hours.length;
  S.v.frame = clamp(f, 0, nF - 1);
  loadFrame();
  clearTimeout(scatterT);
  scatterT = setTimeout(loadScatter, 200);
}

function advance() {
  if (!S.v.playing || S.view !== 'viewer') return;
  const nF = S.st.experiment.hours.length;
  S.v.frame = (S.v.frame + 1) % nF;
  loadFrame();
}

function frameUrl(f) {
  const e = S.st.experiment;
  const layers = Array.from(S.v.layers).sort().join(',');
  return `/api/frame?well=${S.v.well}&field=${S.v.field}&frame=${f}&layers=${layers}&sel=${encodeURIComponent(S.v.sel || '')}&v=${encodeURIComponent((e.run || '') + '-' + (e.settings.gate_green || 'a') + '-' + (e.settings.gate_red || 'a') + '-' + S.v.ver)}`;
}

const prefetched = new Map();
function prefetch(f) {
  const nF = S.st.experiment.hours.length;
  if (f < 0 || f >= nF) return;
  const u = frameUrl(f);
  if (prefetched.has(u)) return;
  const im = new Image();
  im.src = u;
  prefetched.set(u, im);
  if (prefetched.size > 40) prefetched.delete(prefetched.keys().next().value);
}

function loadFrame() {
  const img = $('#frame-img');
  if (!img) return;
  const e = S.st.experiment;
  const f = S.v.frame;
  const h = e.hours[f] || 0;
  S.v.t0 = performance.now();
  img.src = frameUrl(f);
  const sc = $('#scrub'); if (sc) sc.value = f;
  const fn = $('#f-num'); if (fn && document.activeElement !== fn) fn.value = f + 1;
  $('#ov-time').textContent = `t = ${num(h)} h`;
  $('#t-label').textContent = `${num(h)} h / ${num(e.hours[e.hours.length - 1])} h`;
  $('#f-label').textContent = `frame ${f + 1} of ${e.hours.length}`;
  prefetch(f + 1);
  if (S.v.playing) prefetch(f + 2); else prefetch(f - 1);
  if (S.v.ids || S.v.sel) loadCells(); else { S.v.cells = null; drawIds(); }
  if (!S.v.playing) {
    api(`stats?well=${S.v.well}&field=${S.v.field}&frame=${f}`).then((st) => {
      const d = $('#st-det');
      if (d) {
        d.textContent = st.detected; $('#st-cl').textContent = st.colourless; $('#st-conf').textContent = st.confluence == null ? '–' : st.confluence + '%';
        $('#st-col').textContent = st.colonies == null ? '–' : st.colonies;
        $('#st-cola').textContent = st.colony_area == null ? '–' : `${Math.round(st.colony_area).toLocaleString()} µm²`;
      }
    }).catch(() => {});
  }
}

async function loadCells() {
  const want = `${S.v.well}|${S.v.field}|${S.v.frame}`;
  try {
    const d = await api(`frame_cells?well=${S.v.well}&field=${S.v.field}&frame=${S.v.frame}`);
    if (want !== `${S.v.well}|${S.v.field}|${S.v.frame}`) return;  // moved on meanwhile
    S.v.cells = d.cells;
    S.v.cellsAt = want;
    drawIds();
    selInfo();
  } catch (e) { /* not critical */ }
}

function drawIds() {
  const svg = $('#ids');
  if (!svg) return;
  const e = S.st.experiment;
  const npx = e.derived.nucleus_px || 20;
  const k = S.v.shape[1] / (svg.clientWidth || S.v.shape[1]);  // image pixels per screen pixel
  const fs = Math.max(npx * 0.7, 12 * k);
  if (!S.v.cells || (!S.v.ids && !S.v.sel)) { svg.innerHTML = ''; return; }
  let out = '';
  for (const c of S.v.cells) {
    const sel = c.key === S.v.sel;
    if (!S.v.ids && !sel) continue;
    const col = sel ? '#FFFFFF' : c.excluded ? '#6B7480' : PH_TXT[c.call] || '#FFFFFF';
    if (c.interp) out += `<circle cx="${c.x}" cy="${c.y}" r="${(npx * 0.55).toFixed(1)}" fill="none" stroke="${sel ? '#fff' : '#B4BAC2'}" stroke-width="${sel ? 3 : 2}" stroke-dasharray="5 4"/>`;
    if (S.v.ids || sel) out += `<text x="${c.x}" y="${(c.y - npx * 0.62).toFixed(1)}" text-anchor="middle" font-size="${(sel ? fs * 1.25 : fs).toFixed(1)}" fill="${col}" stroke-width="${(fs / 4).toFixed(1)}">${c.id}</text>`;
  }
  svg.innerHTML = out;
}

async function pickCell(ev) {
  const svg = $('#ids');
  const rect = svg.getBoundingClientRect();
  const x = (ev.clientX - rect.left) / rect.width * S.v.shape[1];
  const y = (ev.clientY - rect.top) / rect.height * S.v.shape[0];
  if (!S.v.cells || S.v.cellsAt !== `${S.v.well}|${S.v.field}|${S.v.frame}`) {
    try { S.v.cells = (await api(`frame_cells?well=${S.v.well}&field=${S.v.field}&frame=${S.v.frame}`)).cells; S.v.cellsAt = `${S.v.well}|${S.v.field}|${S.v.frame}`; } catch (e) { return; }
  }
  const npx = S.st.experiment.derived.nucleus_px || 20;
  let best = null, bd = (npx * 1.2) ** 2;
  for (const c of S.v.cells) { const d = (c.x - x) ** 2 + (c.y - y) ** 2; if (d < bd) { bd = d; best = c; } }
  S.v.sel = best ? best.key : null;
  loadFrame();
  selInfo();
}

function selInfo() {
  const box = $('#sel-info');
  if (!box) return;
  if (!S.v.sel) { box.innerHTML = ''; return; }
  const c = (S.v.cells || []).find((x) => x.key === S.v.sel);
  const [w, f, id] = S.v.sel.split('-');
  const now = c ? `<span style="color:${PH_TXT[c.call]}">${PH_LABEL[c.call]}${c.interp ? ' (no nucleus signal; position from phase contrast)' : ''}</span>` : '<span class="muted">not in this frame</span>';
  box.innerHTML = `<div class="sel-info"><strong>Cell ${esc(id)}</strong><span class="muted">${esc(w)} · field ${esc(f)}</span>${now}
    <span style="margin-left:auto;display:flex;gap:8px;flex-wrap:wrap"><button class="btn sm" id="sel-open">Open in Single cells</button><button class="btn sm" id="sel-tree">Family tree</button><button class="btn sm" id="sel-clear" aria-label="Clear selection">${ICON.x}</button></span></div>`;
  $('#sel-clear').onclick = () => { S.v.sel = null; loadFrame(); selInfo(); };
  $('#sel-open').onclick = () => openTrack(S.v.sel, false);
  $('#sel-tree').onclick = () => openTrack(S.v.sel, true);
}

function openTrack(key, tree) {
  const e = S.st.experiment, s = e.settings;
  const w = key.split('-')[0];
  const cid = s.wells[w];
  S.sc.cond = cid && w !== s.parental_well ? cid : '_all';
  S.sc.sel = key; S.sc.pinned = true; S.sc.scrollTree = tree;
  setView('cells');
}

async function loadScatter() {
  if (S.view !== 'viewer') return;
  try {
    const d = await api(`scatter?frame=${S.v.frame}&well=${S.v.well}&field=${S.v.field}`);
    S.v.scatter = d;
    if (S.v.gg == null || !S.v.dragging) { S.v.gg = d.gate_green; S.v.gr = d.gate_red; }
    const gt = $('#gate-t'); if (gt) gt.textContent = num(S.st.experiment.hours[S.v.frame]);
    syncGateUI();
    drawScatter();
  } catch (e) { fail(e); }
}

function classify(r, g, gr, gg) { const hg = g >= gg, hr = r >= gr; return hg && !hr ? 0 : !hg && hr ? 1 : hg && hr ? 2 : 3; }

const SC = { m: { l: 46, r: 12, t: 12, b: 40 }, dom: 1.2 };
function scGeom(cv) {
  const w = cv.clientWidth, h = cv.clientHeight;
  const { l, r, t, b } = SC.m;
  return { w, h, x0: l, x1: w - r, y0: t, y1: h - b, sx: (v) => l + clamp(v, 0, SC.dom) / SC.dom * (w - r - l), sy: (v) => (h - b) - clamp(v, 0, SC.dom) / SC.dom * (h - b - t),
    ix: (px) => (px - l) / (w - r - l) * SC.dom, iy: (py) => ((h - b) - py) / (h - b - t) * SC.dom };
}

function drawScatter() {
  const cv = $('#scatter');
  if (!cv || !S.v.scatter) return;
  const dpr = window.devicePixelRatio || 1;
  cv.width = cv.clientWidth * dpr; cv.height = cv.clientHeight * dpr;
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const G = scGeom(cv);
  ctx.clearRect(0, 0, G.w, G.h);
  ctx.strokeStyle = '#3A4148'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(G.x0, G.y0); ctx.lineTo(G.x0, G.y1); ctx.lineTo(G.x1, G.y1); ctx.stroke();
  ctx.fillStyle = '#A0A8B2'; ctx.font = '10px ui-monospace, Menlo, monospace';
  ctx.textAlign = 'center';
  [0, 0.5, 1].forEach((v) => { ctx.fillText(v.toFixed(1), G.sx(v), G.y1 + 14); });
  ctx.textAlign = 'right';
  [0, 0.5, 1].forEach((v) => { ctx.fillText(v.toFixed(1), G.x0 - 6, G.sy(v) + 3); });
  ctx.fillStyle = '#C9D1DA'; ctx.font = '11px -apple-system, system-ui, sans-serif'; ctx.textAlign = 'center';
  ctx.fillText('PIP-mVenus (green), normalised', (G.x0 + G.x1) / 2, G.h - 8);
  ctx.save(); ctx.translate(13, (G.y0 + G.y1) / 2); ctx.rotate(-Math.PI / 2); ctx.fillText('mCherry-Geminin (red), normalised', 0, 0); ctx.restore();
  const d = S.v.scatter, gg = S.v.gg, gr = S.v.gr;
  const counts = [0, 0, 0, 0];
  for (let i = 0; i < d.r.length; i++) {
    const k = classify(d.r[i], d.g[i], gr, gg);
    counts[k]++;
    ctx.fillStyle = PH_COL[k];
    ctx.globalAlpha = 0.8;
    ctx.beginPath(); ctx.arc(G.sx(d.g[i]), G.sy(d.r[i]), 2.4, 0, Math.PI * 2); ctx.fill();
  }
  ctx.globalAlpha = 1;
  if (d.floor) {
    ctx.setLineDash([2, 3]); ctx.strokeStyle = '#6B7480';
    ctx.beginPath(); ctx.moveTo(G.sx(d.floor.green), G.y0); ctx.lineTo(G.sx(d.floor.green), G.y1); ctx.moveTo(G.x0, G.sy(d.floor.red)); ctx.lineTo(G.x1, G.sy(d.floor.red)); ctx.stroke();
  }
  ctx.setLineDash([5, 4]); ctx.strokeStyle = '#E7E9EC'; ctx.lineWidth = 1.4;
  ctx.beginPath(); ctx.moveTo(G.sx(gg), G.y0); ctx.lineTo(G.sx(gg), G.y1); ctx.moveTo(G.x0, G.sy(gr)); ctx.lineTo(G.x1, G.sy(gr)); ctx.stroke();
  ctx.setLineDash([]);
  ctx.font = '600 12px -apple-system, system-ui, sans-serif';
  ctx.textAlign = 'right'; ctx.fillStyle = PH_TXT[0]; ctx.fillText('G1', G.x1 - 4, G.y1 - 8);
  ctx.textAlign = 'left'; ctx.fillStyle = PH_TXT[1]; ctx.fillText('S', G.x0 + 8, G.y0 + 14);
  ctx.textAlign = 'right'; ctx.fillStyle = PH_TXT[2]; ctx.fillText('G2/M', G.x1 - 4, G.y0 + 14);
  ctx.textAlign = 'left'; ctx.fillStyle = PH_TXT[3]; ctx.fillText('Dim', G.x0 + 8, G.y1 - 8);
  // class table
  const n = d.r.length || 1;
  const names = ['G1 (green high, red low)', 'S (red, little green)', 'G2/M (both high)', 'Dim or in transition'];
  const scale = d.n / n;
  $('#stack').innerHTML = counts.map((c, k) => `<span style="flex:0 0 ${(c / n * 100).toFixed(2)}%;background:${PH_COL[k]}"></span>`).join('');
  $('#classes').innerHTML = counts.map((c, k) => `<div class="crow"><span class="dot" style="background:${PH_COL[k]}"></span><span>${names[k]}</span><span class="mono muted" style="text-align:right">${Math.round(c * scale)}</span><span class="mono" style="text-align:right">${Math.round(c / n * 100)}%</span></div>`).join('');
}

function syncGateUI() {
  const gg = $('#gg'), gr = $('#gr');
  if (!gg) return;
  gg.value = S.v.gg; gr.value = S.v.gr;
  $('#gg-l').textContent = (+S.v.gg).toFixed(3); $('#gr-l').textContent = (+S.v.gr).toFixed(3);
}

async function commitGates() {
  try {
    await api('gates', { gate_green: +S.v.gg, gate_red: +S.v.gr });
    S.st.experiment.settings.gate_green = +S.v.gg; S.st.experiment.settings.gate_red = +S.v.gr;
    S.v.ver++; markSaved();
    loadFrame();
  } catch (e) { fail(e); }
}

function setupGates() {
  const cv = $('#scatter');
  const gg = $('#gg'), gr = $('#gr');
  gg.oninput = () => { S.v.gg = +gg.value; syncGateUI(); drawScatter(); };
  gr.oninput = () => { S.v.gr = +gr.value; syncGateUI(); drawScatter(); };
  gg.onchange = commitGates; gr.onchange = commitGates;
  let drag = null;
  cv.onpointerdown = (ev) => {
    const G = scGeom(cv); const rect = cv.getBoundingClientRect();
    const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
    const dx = Math.abs(x - G.sx(S.v.gg)), dy = Math.abs(y - G.sy(S.v.gr));
    if (Math.min(dx, dy) > 10) return;
    drag = dx < dy ? 'g' : 'r';
    S.v.dragging = true;
    cv.setPointerCapture(ev.pointerId);
  };
  cv.onpointermove = (ev) => {
    const G = scGeom(cv); const rect = cv.getBoundingClientRect();
    const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
    if (!drag) {
      const near = Math.min(Math.abs(x - G.sx(S.v.gg)), Math.abs(y - G.sy(S.v.gr))) <= 10;
      cv.style.cursor = near ? (Math.abs(x - G.sx(S.v.gg)) < Math.abs(y - G.sy(S.v.gr)) ? 'ew-resize' : 'ns-resize') : 'default';
      return;
    }
    if (drag === 'g') S.v.gg = clamp(G.ix(x), 0.02, 1.2); else S.v.gr = clamp(G.iy(y), 0.02, 1.2);
    syncGateUI(); drawScatter();
  };
  cv.onpointerup = () => { if (drag) { drag = null; S.v.dragging = false; commitGates(); } };
}

// ---------------------------------------------------------------- charts
function niceStep(max) { return max > 120 ? 48 : max > 48 ? 24 : max > 24 ? 12 : 6; }

function lineChart({ w = 360, h = 236, hours, series, ymin = 0, ymax, yticks, yfmt = (v) => v, ylabel = '', refY = null }) {
  const l = 44, r = 10, t = 12, b = 42;
  const xmax = Math.max(1, hours[hours.length - 1] || 1);
  const sx = (x) => l + x / xmax * (w - l - r);
  const sy = (y) => (h - b) - (clamp(y, ymin, ymax) - ymin) / (ymax - ymin) * (h - b - t);
  let s = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(ylabel)} over time" style="width:100%;height:auto;display:block">`;
  yticks.forEach((v) => { s += `<path d="M${l} ${sy(v).toFixed(1)}H${w - r}" stroke="${v === ymin ? '#3A4148' : '#23282D'}" stroke-width="1"/><text x="${l - 6}" y="${(sy(v) + 3).toFixed(1)}" fill="#A0A8B2" font-size="10" font-family="ui-monospace,Menlo,monospace" text-anchor="end">${yfmt(v)}</text>`; });
  const st = niceStep(xmax);
  for (let x = 0; x <= xmax + 1e-6; x += st) s += `<text x="${sx(x).toFixed(1)}" y="${h - b + 15}" fill="#A0A8B2" font-size="10" font-family="ui-monospace,Menlo,monospace" text-anchor="middle">${x}</text>`;
  s += `<text x="${(l + w - r) / 2}" y="${h - 6}" fill="#C9D1DA" font-size="11" text-anchor="middle">Time (h)</text>`;
  if (refY != null) s += `<path d="M${l} ${sy(refY).toFixed(1)}H${w - r}" stroke="#6B7480" stroke-dasharray="4 4" fill="none"/>`;
  series.forEach((se) => {
    const pts = [], up = [], lo = [];
    se.mean.forEach((m, i) => {
      if (m == null) return;
      const x = sx(hours[i]); const e = se.sem ? (se.sem[i] || 0) : 0;
      pts.push(`${x.toFixed(1)} ${sy(m).toFixed(1)}`); up.push(`${x.toFixed(1)} ${sy(m + e).toFixed(1)}`); lo.push(`${x.toFixed(1)} ${sy(m - e).toFixed(1)}`);
    });
    if (!pts.length) return;
    if (se.sem) s += `<path d="M${up.join('L')}L${lo.reverse().join('L')}Z" fill="${se.color}" fill-opacity="0.2"/>`;
    s += `<path d="M${pts.join('L')}" fill="none" stroke="${se.color}" stroke-width="2" stroke-linejoin="round"${se.dashed ? ' stroke-dasharray="6 4"' : ''}/>`;
  });
  return s + '</svg>';
}

function condLegend(conds) {
  return conds.map((c) => `<span title="${esc(c.wells.join(', '))}"><span class="ln" style="background:${c.dashed ? `repeating-linear-gradient(90deg,${c.color} 0 5px,transparent 5px 8px)` : c.color}"></span>${esc(c.name)} <span class="mono muted">${c.wells.length > 4 ? c.wells.length + ' wells' : esc(c.wells.join(', '))}</span></span>`).join('');
}

// ---------------------------------------------------------------- step 3: population
async function renderPopulation() {
  if (!hasResults()) return needResults();
  main.innerHTML = header(3, 'Population') + '<p class="muted">Loading…</p>';
  let d;
  try { d = await api('population'); } catch (e) { return fail(e); }
  S.pop.data = d;
  if (!d.conditions.length) {
    main.innerHTML = header(3, 'Population') + '<div class="empty"><h2>No conditions with wells</h2><p class="muted">Assign wells to conditions on the plate map in step 1.</p></div>';
    return;
  }
  const scConds = ownConditions();
  if (!S.pop.scond || !scConds.find((c) => c.id === S.pop.scond)) S.pop.scond = scConds.length ? scConds[0].id : null;
  const panels = ['G1', 'S', 'G2M', 'Colourless'].map((p, i) => `<figure style="margin:0;display:flex;flex-direction:column;gap:8px">
      <figcaption style="display:flex;align-items:center;gap:8px;font-weight:600"><span class="dot" style="background:${PH_COL[i]}"></span>${PH_LABEL[i]}<span class="small muted" style="font-weight:400">% of cells</span></figcaption>
      ${lineChart({ w: 300, h: 214, hours: d.hours, series: d.conditions.filter((c) => c[p]).map((c) => ({ color: c.color, dashed: c.dashed, mean: c[p].mean, sem: c[p].sem })), ymin: 0, ymax: 100, yticks: [0, 25, 50, 75, 100], ylabel: '% ' + PH_LABEL[i] })}</figure>`).join('');
  const linked = (S.st.experiment.linked || []).filter((l) => l.ok);
  const metrics = [['count_norm', 'Cell number (× t0)'], ['total', 'Nuclei'], ['confluence', 'Confluence']];
  const nF = d.hours.length;
  const tps = [0, Math.round((nF - 1) / 3), Math.round(2 * (nF - 1) / 3), nF - 1];
  main.innerHTML = header(3, 'Population', `<div class="legend">${condLegend(d.conditions)}<span class="muted">Mean ± SEM across wells</span></div>`)
    + (linked.length ? `<div class="info">${ICON.info}<span>Includes ${linked.length} other experiment${linked.length === 1 ? '' : 's'} (${linked.map((l) => esc(l.name)).join(', ')}); conditions with the same name are pooled, wells are the replicates. <button class="link" id="go-fig">Change on the Figures page</button></span></div>` : '')
    + `<section class="card" aria-labelledby="frac-h"><div class="card-head"><div><h2 id="frac-h">Phase fractions over time</h2><p>Each well is summarised first; bands are the SEM across wells. Phase calls are smoothed per track; Colourless = neither reporter on (mostly just after division).</p></div></div>
       <div class="grid4">${panels}</div></section>
     <section class="card" aria-labelledby="col-h"><div class="card-head"><div><h2 id="col-h">Colonies</h2><p>From the confluence mask (phase contrast, every cell): touching cells form one colony, objects smaller than ${num(S.st.experiment.settings.min_object_um2)} µm² are ignored as debris (change it in step 2). Mean ± SEM across wells.</p></div></div>
       <div class="grid2">
         <figure style="margin:0;display:flex;flex-direction:column;gap:8px"><figcaption style="font-weight:600">Colony number <span class="small muted" style="font-weight:400">per image</span></figcaption>${colonyChart(d, 'colonies')}</figure>
         <figure style="margin:0;display:flex;flex-direction:column;gap:8px"><figcaption style="font-weight:600">Average colony area <span class="small muted" style="font-weight:400">µm²</span></figcaption>${colonyChart(d, 'colony_area')}</figure>
       </div></section>
     <div class="grid2" style="align-items:stretch">
       <section class="card" aria-labelledby="cnt-h"><div class="card-head"><div><h2 id="cnt-h">Growth</h2><p id="cnt-sub"></p></div>
         <div class="seg" role="group" aria-label="Metric">${metrics.map(([k, n]) => `<button type="button" data-metric="${k}" aria-pressed="${S.pop.metric === k}">${n}</button>`).join('')}</div></div>
         <div id="cnt-chart"></div></section>
       <section class="card" aria-labelledby="sc-h"><div class="card-head"><div><h2 id="sc-h">Red vs green at selected timepoints</h2><p>FUCCI-style plots with the gates from step 2.</p></div>
         <div class="seg" role="group" aria-label="Condition">${scConds.map((c) => `<button type="button" data-scond="${c.id}" aria-pressed="${S.pop.scond === c.id}"><span class="dot" style="background:${c.color}"></span>${esc(c.name)}</button>`).join('')}</div></div>
         <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px">${tps.map((f, i) => `<figure style="margin:0;padding:10px;border-radius:8px;background:var(--well);display:flex;flex-direction:column;gap:6px">
           <figcaption style="display:flex;justify-content:space-between;font-size:13px"><strong>${num(d.hours[f])} h</strong><span class="mono muted" id="scn-${i}"></span></figcaption>
           <canvas id="sc-${i}" data-frame="${f}" style="width:100%;aspect-ratio:1.1"></canvas><div class="mono small" id="scp-${i}"></div></figure>`).join('')}</div></section>
     </div>
     ${exportCard()}`;
  drawCount();
  $$('[data-metric]').forEach((b) => b.onclick = () => { S.pop.metric = b.dataset.metric; $$('[data-metric]').forEach((x) => x.setAttribute('aria-pressed', x === b)); drawCount(); });
  $$('[data-scond]').forEach((b) => b.onclick = () => { S.pop.scond = b.dataset.scond; $$('[data-scond]').forEach((x) => x.setAttribute('aria-pressed', x === b)); drawSmallScatters(tps); });
  drawSmallScatters(tps);
  bindExport();
  const gf = $('#go-fig'); if (gf) gf.onclick = () => setView('figures');
}

function ownConditions() {
  const e = S.st.experiment, s = e.settings;
  return s.conditions.filter((c) => Object.entries(s.wells).some(([w, id]) => id === c.id && e.summary.wells.includes(w) && w !== s.parental_well));
}

function drawCount() {
  const d = S.pop.data, m = S.pop.metric;
  let max = 0;
  d.conditions.forEach((c) => c[m].mean.forEach((v, i) => { if (v != null) max = Math.max(max, v + (c[m].sem[i] || 0)); }));
  let ymax, yticks, fmt = (v) => v, sub, ref = null;
  if (m === 'count_norm') { ymax = Math.max(1.5, Math.ceil(max * 2) / 2); const st = ymax > 4 ? 1 : 0.5; yticks = []; for (let v = 0; v <= ymax + 1e-6; v += st) yticks.push(+v.toFixed(2)); sub = 'Segmented nuclei per well, as a fold of the first frame. The dashed line is the start.'; ref = 1; }
  else if (m === 'total') { ymax = Math.max(10, Math.ceil(max / 50) * 50); yticks = [0, ymax / 4, ymax / 2, 3 * ymax / 4, ymax].map(Math.round); sub = 'Segmented nuclei per well (all fields).'; }
  else { ymax = Math.min(100, Math.max(10, Math.ceil(max / 10) * 10)); yticks = [0, ymax / 4, ymax / 2, 3 * ymax / 4, ymax].map((v) => Math.round(v)); fmt = (v) => v + '%'; sub = 'Phase-contrast confluence, mean of fields per well.'; }
  $('#cnt-sub').textContent = sub;
  $('#cnt-chart').innerHTML = lineChart({ w: 520, h: 300, hours: d.hours, series: d.conditions.map((c) => ({ color: c.color, dashed: c.dashed, mean: c[m].mean, sem: c[m].sem })), ymin: 0, ymax, yticks, yfmt: fmt, refY: ref, ylabel: sub });
}

function niceTicks(max, n = 4) {
  const raw = Math.max(max, 1e-9) / n;
  const p = 10 ** Math.floor(Math.log10(raw));
  const st = [1, 2, 2.5, 5, 10].map((m) => m * p).find((v) => v >= raw);
  const ymax = Math.max(st, Math.ceil(max / st - 1e-9) * st);
  const t = []; for (let v = 0; v <= ymax + 1e-9; v += st) t.push(+v.toFixed(6));
  return { ymax, ticks: t };
}

function colonyChart(d, key) {
  const conds = d.conditions.filter((c) => c[key]);
  let max = 0;
  conds.forEach((c) => c[key].mean.forEach((v, i) => { if (v != null) max = Math.max(max, v + (c[key].sem[i] || 0)); }));
  if (!max) return '<p class="muted small">No colony measurements yet. Re-run the analysis (analyses made before this version have none).</p>';
  const { ymax, ticks } = niceTicks(max);
  const fmt = key === 'colony_area' ? (v) => (v >= 10000 ? (v / 1000).toFixed(0) + 'k' : v >= 1000 ? (v / 1000).toFixed(1).replace(/\.0$/, '') + 'k' : String(v)) : (v) => String(v);
  return lineChart({ w: 520, h: 280, hours: d.hours, series: conds.map((c) => ({ color: c.color, dashed: c.dashed, mean: c[key].mean, sem: c[key].sem })), ymin: 0, ymax, yticks: ticks, yfmt: fmt, ylabel: key === 'colonies' ? 'Colonies per image' : 'Mean colony area (µm²)' });
}

async function drawSmallScatters(tps) {
  for (let i = 0; i < tps.length; i++) {
    const cv = $('#sc-' + i);
    if (!cv) return;
    let d;
    try { d = await api(`scatter?frame=${tps[i]}${S.pop.scond ? '&condition=' + S.pop.scond : ''}`); } catch (e) { continue; }
    const dpr = window.devicePixelRatio || 1;
    cv.width = cv.clientWidth * dpr; cv.height = cv.clientHeight * dpr;
    const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const w = cv.clientWidth, h = cv.clientHeight, l = 8, b = 8, dom = 1.2;
    const sx = (v) => l + clamp(v, 0, dom) / dom * (w - l - 4), sy = (v) => (h - b) - clamp(v, 0, dom) / dom * (h - b - 4);
    ctx.strokeStyle = '#3A4148'; ctx.beginPath(); ctx.moveTo(l, 4); ctx.lineTo(l, h - b); ctx.lineTo(w - 4, h - b); ctx.stroke();
    const cnt = [0, 0, 0, 0];
    for (let k = 0; k < d.r.length; k++) {
      const c = d.raw[k]; cnt[c]++;
      ctx.fillStyle = PH_COL[c]; ctx.globalAlpha = 0.75;
      ctx.beginPath(); ctx.arc(sx(d.g[k]), sy(d.r[k]), 1.8, 0, 7); ctx.fill();
    }
    ctx.globalAlpha = 1; ctx.setLineDash([3, 3]); ctx.strokeStyle = '#E7E9EC';
    ctx.beginPath(); ctx.moveTo(sx(d.gate_green), 4); ctx.lineTo(sx(d.gate_green), h - b); ctx.moveTo(l, sy(d.gate_red)); ctx.lineTo(w - 4, sy(d.gate_red)); ctx.stroke(); ctx.setLineDash([]);
    const n = d.r.length || 1;
    $('#scn-' + i).textContent = 'n = ' + d.n;
    $('#scp-' + i).innerHTML = [0, 1, 2].map((k) => `<span style="color:${PH_TXT[k]};margin-right:10px">${GATE_LABEL[k]} ${Math.round(cnt[k] / n * 100)}%</span>`).join('');
  }
}

function exportCard() {
  return `<section class="card" aria-labelledby="exp-h" style="flex-direction:row;flex-wrap:wrap;align-items:center;justify-content:space-between">
    <div style="flex:1 1 320px"><h2 id="exp-h">Export</h2><p class="small muted" style="margin-top:4px">Prism tables (one column per well, grouped by condition), per-cell and per-track tables, and the settings and software versions used. Run statistics on well means, never on individual cells.</p></div>
    <button class="btn primary" id="btn-export">${ICON.down} Export all tables</button></section>`;
}

function bindExport() {
  const b = $('#btn-export');
  if (!b) return;
  b.onclick = async () => {
    b.disabled = true;
    try {
      const r = await api('export', {});
      toast('Exported to “' + r.path.split('/').slice(-3).join('/') + '”.', { action: { label: 'Show in Finder', fn: () => api('reveal', { path: r.path }).catch(fail) }, ms: 12000 });
    } catch (e) { fail(e); }
    b.disabled = false;
  };
}

// ---------------------------------------------------------------- step 4: single cells
async function renderCells() {
  if (!hasResults()) return needResults();
  const e = S.st.experiment, s = e.settings;
  const conds = s.conditions.filter((c) => Object.entries(s.wells).some(([w, id]) => id === c.id && e.summary.wells.includes(w) && w !== s.parental_well));
  if (!S.sc.cond || (S.sc.cond !== '_all' && !conds.find((c) => c.id === S.sc.cond))) S.sc.cond = conds.length ? conds[0].id : '_all';
  const segBtns = conds.map((c) => `<button type="button" data-cond="${c.id}" aria-pressed="${S.sc.cond === c.id}"><span class="dot" style="background:${c.color}"></span>${esc(c.name)}</button>`).join('')
    + `<button type="button" data-cond="_all" aria-pressed="${S.sc.cond === '_all'}">All wells</button>`;
  main.innerHTML = header(4, 'Single cells', `<div class="seg" role="group" aria-label="Condition">${segBtns}</div>`)
    + `<div class="row-wrap">
      <section class="card tight" style="flex:3 1 520px" aria-labelledby="hm-h">
        <div class="card-head"><div><h2 id="hm-h">Track heatmap</h2><p>One row per track, coloured by phase call. Both daughters are followed after every division. Click a row to inspect it.</p></div>
          <div style="display:flex;flex-direction:column;gap:4px"><label for="sort" class="small muted">Sort by</label>
          <select id="sort" class="input" style="height:36px"><option value="division" ${S.sc.sort === 'division' ? 'selected' : ''}>Time of first division</option><option value="g1" ${S.sc.sort === 'g1' ? 'selected' : ''}>Time in G1</option><option value="start" ${S.sc.sort === 'start' ? 'selected' : ''}>Start time</option></select></div></div>
        <div class="legend">${[0, 1, 2, 3].map((k) => `<span><span class="sw" style="background:${PH_COL[k]}"></span>${PH_LABEL[k]}</span>`).join('')}<span class="muted" id="hm-count"></span></div>
        <div class="heat-scroll" id="hm-wrap"><canvas id="heat"></canvas></div>
        <div style="display:flex;justify-content:space-between;font-family:var(--mono);font-size:11px;color:var(--muted)" id="hm-axis"></div>
      </section>
      <section class="card tight" style="flex:2 1 380px" id="cell-panel" aria-live="polite"><p class="muted">Select a track.</p></section>
    </div>
    <section class="card tight" aria-labelledby="tree-h" id="tree-card">
      <div class="card-head"><div><h2 id="tree-h">Family tree</h2><p id="tree-sub">The selected cell's lineage: its oldest tracked ancestor at the top, every division as a fork into two daughters. Bars are coloured by phase over time. Click a cell to inspect it.</p></div>
        <div class="legend">${[0, 1, 2, 3].map((k) => `<span><span class="sw" style="background:${PH_COL[k]}"></span>${PH_LABEL[k]}</span>`).join('')}<span><span class="sw" style="background:#F7DFA0"></span>Parentage uncertain</span></div></div>
      <div class="tree-wrap" id="tree-wrap"><p class="muted" style="padding:14px">Select a track.</p></div>
    </section>
    <section class="card" aria-labelledby="dur-h"><div class="card-head"><div><h2 id="dur-h">Phase durations by condition</h2><p id="dur-sub">One dot per cell per phase, fully observed phases only; bars are medians.</p></div></div>
      <div id="dur-chart"><p class="muted">Loading…</p></div><div class="stats" id="dur-stats"></div></section>
    ${exportCard()}`;
  $$('[data-cond]').forEach((b) => b.onclick = () => { S.sc.cond = b.dataset.cond; S.sc.sel = null; S.sc.pinned = false; renderCells(); });
  $('#sort').onchange = (ev) => { S.sc.sort = ev.target.value; loadHeatmap(); };
  bindExport();
  loadHeatmap();
  loadDurations();
}

async function loadHeatmap() {
  const q = S.sc.cond === '_all' ? '' : `condition=${S.sc.cond}&`;
  let d;
  try { d = await api(`tracks?${q}sort=${S.sc.sort}`); } catch (e) { return fail(e); }
  S.sc.data = d;
  $('#hm-count').textContent = d.total > d.rows.length ? `Showing ${d.rows.length} of ${d.total} tracks (3+ frames)` : `${d.total} tracks (3+ frames)`;
  const xmax = d.hours[d.hours.length - 1] || 0, st = niceStep(xmax);
  const ticks = []; for (let x = 0; x <= xmax + 1e-6; x += st) ticks.push(x);
  $('#hm-axis').innerHTML = ticks.map((x) => `<span>${x}${x === ticks[ticks.length - 1] ? ' h' : ''}</span>`).join('');
  if (!S.sc.sel || (!S.sc.pinned && !d.rows.find((r) => r.key === S.sc.sel))) {
    const pick = d.rows.find((r) => r.parent && r.fate === 'Divided') || d.rows[0];
    S.sc.sel = pick ? pick.key : null;
  }
  drawHeat();
  if (S.sc.sel) loadTrack(S.sc.sel);
  else $('#cell-panel').innerHTML = '<p class="muted">No tracks with 3 or more frames in this condition.</p>';
}

function drawHeat() {
  const d = S.sc.data, cv = $('#heat');
  if (!cv || !d) return;
  const rows = d.rows, nF = d.n_frames;
  const rowH = rows.length <= 60 ? 14 : 10;
  const w = $('#hm-wrap').clientWidth;
  const h = Math.max(rowH, rows.length * rowH);
  const dpr = window.devicePixelRatio || 1;
  cv.style.width = w + 'px'; cv.style.height = h + 'px';
  cv.width = w * dpr; cv.height = h * dpr;
  const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const cw = w / nF;
  rows.forEach((r, i) => {
    const y = i * rowH;
    ctx.globalAlpha = r.excluded ? 0.25 : 1;
    for (let f = 0; f < nF; f++) {
      const st = r.states[f];
      if (st < 0) continue;
      ctx.fillStyle = PH_COL[st];
      ctx.fillRect(f * cw, y + 1, Math.ceil(cw), rowH - 2);
    }
    ctx.globalAlpha = 1;
    if (r.low_conf) { ctx.fillStyle = '#F7DFA0'; ctx.fillRect(0, y + 1, 2, rowH - 2); }
    if (r.key === S.sc.sel) { ctx.strokeStyle = '#FFFFFF'; ctx.lineWidth = 1.5; ctx.strokeRect(0.75, y + 0.75, w - 1.5, rowH - 1.5); }
  });
  let tip = document.querySelector('.tooltip');
  if (!tip) { tip = document.createElement('div'); tip.className = 'tooltip'; tip.style.display = 'none'; document.body.appendChild(tip); }
  cv.onmousemove = (ev) => {
    const rect = cv.getBoundingClientRect();
    const i = Math.floor((ev.clientY - rect.top) / rowH);
    const r = rows[i];
    if (!r) { tip.style.display = 'none'; return; }
    const f = Math.floor((ev.clientX - rect.left) / cw);
    tip.textContent = `${r.label} · ${r.fate} · ${num(d.hours[f])} h${r.low_conf ? ' · parentage uncertain' : ''}`;
    tip.style.display = 'block'; tip.style.left = (ev.clientX + 12) + 'px'; tip.style.top = (ev.clientY + 12) + 'px';
  };
  cv.onmouseleave = () => { tip.style.display = 'none'; };
  cv.onclick = (ev) => {
    const rect = cv.getBoundingClientRect();
    const r = rows[Math.floor((ev.clientY - rect.top) / rowH)];
    if (r) { S.sc.sel = r.key; S.sc.pinned = false; drawHeat(); loadTrack(r.key); }
  };
}

async function loadTrack(key) {
  const panel = $('#cell-panel');
  let d;
  try { d = await api('track?key=' + encodeURIComponent(key)); } catch (e) { return fail(e); }
  const e = S.st.experiment;
  const hours = e.hours, xmax = hours[hours.length - 1] || 1;
  const label = `${d.well}·${d.field}·${d.tid}`;
  const lineage = d.parent ? `daughter of <button class="link" data-goto="${esc(d.parent)}">${esc(d.parent.replace(/-/g, '·'))}</button>, born at ${num(hours[d.start])} h` : (d.start === 0 ? 'present at the first frame' : `appeared at ${num(hours[d.start])} h`);
  const kids = d.children.length ? ` · daughters ${d.children.map((c) => `<button class="link" data-goto="${esc(c)}">${esc(c.replace(/-/g, '·'))}</button>`).join(', ')}` : '';
  const flags = d.flags.map((f) => `<span class="flag ${f.startsWith('Low') ? 'amber' : ''}">${esc(f)}</span>`).join('');
  // montage labels
  const idx = Object.fromEntries(d.frames.map((f, i) => [f, i]));
  const mcols = d.montage_frames.length || 1;
  const calls = d.montage_frames.map((f) => { const p = (d.call || d.phase)[idx[f]]; return `<div><div style="color:${PH_TXT[p]};font-weight:600">${PH_LABEL[p]}</div><div class="mono muted" style="font-size:10px">${num(hours[f])}h</div></div>`; }).join('');
  // traces
  const W = 520, H = 210, l = 36, r = 10, t = 10, b = 36;
  const sx = (x) => l + x / xmax * (W - l - r), sy = (v) => (H - b) - clamp(v, 0, 1.2) / 1.2 * (H - b - t);
  let shade = '';
  for (const sg of d.durations.segments) {
    const ph = PH.indexOf(sg.phase);
    const x0 = sx(hours[sg.start] - e.settings.interval_min / 120), x1 = sx(hours[sg.end] + e.settings.interval_min / 120);
    shade += `<rect x="${x0.toFixed(1)}" y="${t}" width="${Math.max(1, x1 - x0).toFixed(1)}" height="${H - b - t}" fill="${PH_COL[ph]}" fill-opacity="0.16"/>`;
  }
  const lg = d.frames.map((f, i) => `${sx(hours[f]).toFixed(1)} ${sy(d.g[i]).toFixed(1)}`).join('L');
  const lr = d.frames.map((f, i) => `${sx(hours[f]).toFixed(1)} ${sy(d.r[i]).toFixed(1)}`).join('L');
  const st = niceStep(xmax);
  let xt = ''; for (let x = 0; x <= xmax + 1e-6; x += st) xt += `<text x="${sx(x).toFixed(1)}" y="${H - b + 14}" fill="#A0A8B2" font-size="10" font-family="ui-monospace,Menlo,monospace" text-anchor="middle">${x}</text>`;
  const traces = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Green and red intensity over time with phase shading" style="width:100%;height:auto;display:block;background:#0F1215;border-radius:8px">
    ${shade}<path d="M${l} ${sy(0)}H${W - r}" stroke="#3A4148"/><path d="M${l} ${sy(0.5).toFixed(1)}H${W - r}M${l} ${sy(1).toFixed(1)}H${W - r}" stroke="#23282D"/>
    <text x="${l - 6}" y="${sy(0) + 3}" fill="#A0A8B2" font-size="10" text-anchor="end">0</text><text x="${l - 6}" y="${(sy(1) + 3).toFixed(1)}" fill="#A0A8B2" font-size="10" text-anchor="end">1.0</text>
    ${xt}<text x="${(l + W - r) / 2}" y="${H - 6}" fill="#C9D1DA" font-size="11" text-anchor="middle">Time (h)</text>
    <path d="M${lg}" fill="none" stroke="#5FD487" stroke-width="2" stroke-linejoin="round"/><path d="M${lr}" fill="none" stroke="#F07C99" stroke-width="2" stroke-linejoin="round"/></svg>`;
  const prec = num(d.precision_h);
  const durRows = d.durations.segments.map((sg) => `<tr><td><span class="dot" style="background:${PH_COL[PH.indexOf(sg.phase)]};margin-right:8px"></span>${PH_LABEL[PH.indexOf(sg.phase)]} <span class="muted small">${num(hours[sg.start])}–${num(hours[sg.end])} h</span></td><td style="color:${sg.observed ? 'var(--text)' : 'var(--muted)'}">${sg.observed ? `${num(sg.hours)} h ± ${prec} h` : 'censored'}</td></tr>`).join('')
    + `<tr><td><strong>Full cycle</strong></td><td style="color:${d.durations.cycle_h != null ? 'var(--text)' : 'var(--muted)'}">${d.durations.cycle_h != null ? `${num(d.durations.cycle_h)} h ± ${prec} h` : 'needs birth and division in the movie'}</td></tr>`;
  panel.innerHTML = `<div style="display:flex;flex-direction:column;gap:8px">
      <div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:8px"><h2 style="font-size:18px">Track <span class="mono">${esc(label)}</span></h2><span class="fate">${esc(d.fate)}</span></div>
      <span class="small muted">${esc(d.well)} · field ${d.field} · ${esc(d.condition)} · ${lineage}${kids}</span>
      <div style="display:flex;flex-wrap:wrap;gap:6px">${flags}</div></div>
    <div style="display:flex;flex-direction:column;gap:6px"><h3>Image montage</h3>
      <div class="mont-grid"><div class="mont-labels"><span>Red</span><span>Green</span><span>Phase</span><span>Merge</span></div><img class="montage" src="/api/montage?key=${encodeURIComponent(d.key)}" alt="Image montage of this cell over time"></div>
      <div class="mont-grid"><span class="small muted">Call</span><div class="calls" style="grid-template-columns:repeat(${mcols},minmax(0,1fr))">${calls}</div></div></div>
    <figure style="margin:0;display:flex;flex-direction:column;gap:6px"><figcaption class="legend"><strong style="font-size:13px;color:var(--text)">Traces</strong><span><span class="ln" style="background:#5FD487"></span>PIP-mVenus</span><span><span class="ln" style="background:#F07C99"></span>mCherry-Geminin</span><span class="muted">Shading = phase call</span></figcaption>${traces}</figure>
    <div><h3 style="margin-bottom:4px">Phase durations</h3><table class="dur">${durRows}</table></div>
    <div style="display:flex;flex-wrap:wrap;gap:8px">
      <button class="btn" id="tr-exclude">${d.excluded ? 'Include track' : 'Exclude track'}</button>
      <button class="btn" id="tr-view">Show in viewer</button>
      <button class="btn ${d.reviewed ? '' : 'primary'}" id="tr-rev" style="margin-left:auto">${d.reviewed ? 'Reviewed ✓' : 'Mark reviewed'}</button></div>`;
  $$('[data-goto]', panel).forEach((b) => b.onclick = () => { S.sc.sel = b.dataset.goto; S.sc.pinned = true; drawHeat(); loadTrack(b.dataset.goto); });
  $('#tr-exclude').onclick = async () => { try { await api('track_flag', { key: d.key, excluded: !d.excluded }); toast(d.excluded ? 'Track included again.' : 'Track excluded from all results.'); loadHeatmap(); loadDurations(); } catch (er) { fail(er); } };
  $('#tr-rev').onclick = async () => { try { await api('track_flag', { key: d.key, reviewed: !d.reviewed }); loadTrack(d.key); } catch (er) { fail(er); } };
  $('#tr-view').onclick = () => { S.v.well = d.well; S.v.field = d.field; S.v.frame = d.frames[0]; S.v.sel = d.key; setView('viewer'); };
  loadTree(d.key);
}

async function loadTree(key) {
  const wrap = $('#tree-wrap');
  if (!wrap) return;
  let d;
  try { d = await api('lineage?key=' + encodeURIComponent(key)); } catch (e) { wrap.innerHTML = `<p class="muted" style="padding:14px">${esc(e.message)}</p>`; return; }
  if (S.sc.sel !== key) return;
  wrap.innerHTML = treeSVG(d, key);
  $$('.node', wrap).forEach((g) => {
    const go = () => { S.sc.sel = g.dataset.key; S.sc.pinned = true; drawHeat(); loadTrack(g.dataset.key); };
    g.onclick = go;
    g.onkeydown = (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); go(); } };
  });
  const n = d.nodes.length, nd = d.nodes.filter((x) => x.children.length).length;
  $('#tree-sub').textContent = `Lineage of the selected cell: ${n} track${n === 1 ? '' : 's'}, ${nd} division${nd === 1 ? '' : 's'}, starting from cell ${d.nodes.length ? d.nodes[0].id : '–'}. Bars are coloured by phase over time; click a cell to inspect it.`;
  if (S.sc.scrollTree) { S.sc.scrollTree = false; $('#tree-card').scrollIntoView({ behavior: 'smooth', block: 'start' }); }
}

function treeSVG(d, selKey) {
  const byKey = Object.fromEntries(d.nodes.map((n) => [n.key, n]));
  const hours = d.hours, nF = hours.length;
  const dt = nF > 1 ? hours[1] - hours[0] : 1;
  const rows = {};
  let leaf = 0;
  const place = (k, depth) => {
    const n = byKey[k];
    if (!n || depth > 40) return 0;
    const kids = n.children.filter((c) => byKey[c]);
    if (!kids.length) { rows[k] = leaf++; return rows[k]; }
    const ys = kids.map((c) => place(c, depth + 1));
    rows[k] = (Math.min(...ys) + Math.max(...ys)) / 2;
    return rows[k];
  };
  place(d.root, 0);
  d.nodes.forEach((n) => { if (rows[n.key] == null) rows[n.key] = leaf++; });
  const W = Math.max(640, ($('#tree-wrap') || {}).clientWidth || 900), RH = 32, l = 24, r = 120, t = 16;
  const H = t + Math.max(1, leaf) * RH + 34;
  const xmax = hours[nF - 1] + dt;
  const X = (f) => l + (hours[Math.min(f, nF - 1)] + (f >= nF ? dt : 0)) / xmax * (W - l - r);
  const XE = (f) => l + (hours[f] + dt) / xmax * (W - l - r);
  const Y = (k) => t + rows[k] * RH + RH / 2;
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Family tree" style="display:block">`;
  const st = niceStep(xmax);
  for (let x = 0; x <= xmax + 1e-6; x += st) {
    const px = l + x / xmax * (W - l - r);
    s += `<path d="M${px.toFixed(1)} ${t - 6}V${H - 28}" stroke="#1E2328"/><text x="${px.toFixed(1)}" y="${H - 12}" fill="#A0A8B2" font-size="10" font-family="ui-monospace,Menlo,monospace" text-anchor="middle">${x}${x + st > xmax ? ' h' : ''}</text>`;
  }
  // connectors
  d.nodes.forEach((n) => {
    const kids = n.children.filter((c) => byKey[c]);
    if (!kids.length) return;
    const xs = XE(n.end);
    const ys = kids.map(Y);
    s += `<path d="M${xs.toFixed(1)} ${Math.min(Y(n.key), ...ys).toFixed(1)}V${Math.max(Y(n.key), ...ys).toFixed(1)}" stroke="#7D8590" stroke-width="1.5" fill="none"/>`;
    kids.forEach((c) => { s += `<path d="M${xs.toFixed(1)} ${Y(c).toFixed(1)}H${X(byKey[c].start).toFixed(1)}" stroke="#7D8590" stroke-width="1.5" fill="none"/>`; });
  });
  d.nodes.forEach((n) => {
    const y = Y(n.key), sel = n.key === selKey;
    let bars = `<rect x="${X(n.start).toFixed(1)}" y="${(y - 7).toFixed(1)}" width="${Math.max(2, XE(n.end) - X(n.start)).toFixed(1)}" height="14" rx="3" fill="#23282D"/>`;
    let run = null;
    const flush = () => { if (run) bars += `<rect x="${X(run.f0).toFixed(1)}" y="${(y - 6).toFixed(1)}" width="${Math.max(1, XE(run.f1) - X(run.f0)).toFixed(1)}" height="12" fill="${PH_COL[run.s]}"/>`; };
    for (const [f, ph] of n.states) {
      if (run && ph === run.s && f === run.f1 + 1) run.f1 = f; else { flush(); run = { f0: f, f1: f, s: ph }; }
    }
    flush();
    const label = `Cell ${n.id}, ${num(hours[n.start])} to ${num(hours[n.end])} h, ${n.fate}`;
    s += `<g class="node" data-key="${esc(n.key)}" tabindex="0" role="button" aria-label="${esc(label)}"><title>${esc(label)}</title>
      <rect x="${(X(n.start) - 4).toFixed(1)}" y="${(y - 22).toFixed(1)}" width="${(XE(n.end) - X(n.start) + 8).toFixed(1)}" height="32" rx="5" fill="transparent" ${sel ? 'stroke="#FFFFFF" stroke-width="1.6"' : ''}/>
      ${bars}${n.low_conf ? `<rect x="${(X(n.start) - 3).toFixed(1)}" y="${(y - 7).toFixed(1)}" width="3" height="14" fill="#F7DFA0"/>` : ''}
      <text x="${(X(n.start) + 2).toFixed(1)}" y="${(y - 10).toFixed(1)}" fill="${sel ? '#FFFFFF' : '#C9D1DA'}" font-size="11" font-weight="${sel ? 700 : 500}" font-family="ui-monospace,Menlo,monospace">Cell ${n.id}</text>
      ${n.children.length ? '' : `<text x="${(XE(n.end) + 6).toFixed(1)}" y="${(y + 4).toFixed(1)}" fill="#A0A8B2" font-size="11">${esc(n.fate)}</text>`}</g>`;
  });
  return s + '</svg>';
}

async function loadDurations() {
  let d;
  try { d = await api('durations'); } catch (e) { return fail(e); }
  const conds = d.conditions;
  const groups = [['G1', 'G1'], ['S', 'S'], ['G2M', 'G2/M'], ['cycle', 'Full cycle']];
  let max = 12;
  conds.forEach((c) => { groups.forEach(([k]) => { (k === 'cycle' ? c.cycle : c.values[k]).forEach((v) => { max = Math.max(max, v); }); }); });
  const ymax = Math.ceil(max / 12) * 12;
  const W = 900, H = 280, l = 60, r = 10, t = 12, b = 44;
  const gw = (W - l - r) / groups.length;
  const sy = (v) => (H - b) - v / ymax * (H - b - t);
  const nc = Math.max(1, conds.length);
  const colW = Math.min(80, gw * 0.8 / nc);
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Dot plot of phase durations by condition" style="width:100%;height:auto;display:block">`;
  for (let v = 0; v <= ymax; v += ymax / 4) s += `<path d="M${l} ${sy(v).toFixed(1)}H${W - r}" stroke="${v === 0 ? '#3A4148' : '#23282D'}"/><text x="${l - 8}" y="${(sy(v) + 4).toFixed(1)}" fill="#A0A8B2" font-size="11" font-family="ui-monospace,Menlo,monospace" text-anchor="end">${num(v)}</text>`;
  s += `<text x="16" y="${(t + H - b) / 2}" fill="#C9D1DA" font-size="12" text-anchor="middle" transform="rotate(-90 16 ${(t + H - b) / 2})">Duration (h)</text>`;
  let seed = 1; const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  groups.forEach(([k, name], gi) => {
    const cx = l + gw * (gi + 0.5);
    s += `<text x="${cx}" y="${H - b + 22}" fill="#E7E9EC" font-size="13" font-weight="600" text-anchor="middle">${name}</text>`;
    conds.forEach((c, ci) => {
      const x = cx + (ci - (nc - 1) / 2) * colW;
      const vals = k === 'cycle' ? c.cycle : c.values[k];
      vals.forEach((v) => { s += `<circle cx="${(x + (rnd() - 0.5) * colW * 0.7).toFixed(1)}" cy="${sy(v).toFixed(1)}" r="2.6" fill="${c.color}" fill-opacity="0.55"/>`; });
      const med = k === 'cycle' ? c.median_cycle : c.median[k];
      if (med != null) s += `<path d="M${(x - colW * 0.42).toFixed(1)} ${sy(med).toFixed(1)}H${(x + colW * 0.42).toFixed(1)}" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round"/>`;
    });
  });
  $('#dur-chart').innerHTML = s + '</svg>';
  $('#dur-sub').textContent = `One dot per cell per phase, fully observed phases only; bars are medians. Every value is ± ${num(d.precision_h)} h (one frame). Censored phases are left out here and kept in the export.`;
  $('#dur-stats').innerHTML = groups.map(([k, name]) => `<div class="stat"><span class="small muted">Median ${name}</span>${conds.map((c) => {
    const med = k === 'cycle' ? c.median_cycle : c.median[k]; const n = (k === 'cycle' ? c.cycle : c.values[k]).length;
    return `<span class="mono small" style="color:${c.color}">${esc(c.name)}: ${med == null ? '–' : num(med) + ' h'} <span class="muted">(n = ${n})</span></span>`; }).join('')}</div>`).join('');
}

// ---------------------------------------------------------------- step 5: figures
const PUB = { G1: '#3DAA4F', S: '#E0393E', G2M: '#F2C230', Colourless: '#8C8C8C', Unclassified: '#E8E8E8' };
const CATS = ['G1', 'S', 'G2M', 'Colourless', 'Unclassified'];
const CAT_LABEL = { G1: 'G1', S: 'S', G2M: 'G2/M', Colourless: 'Colourless', Unclassified: 'Unclassified' };
const FF = 'Helvetica, Arial, sans-serif';
const r1 = (v) => (Math.round(v * 10) / 10).toString();

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

function timeTicks(xmax) { const st = niceStep(xmax); const t = []; for (let x = 0; x <= xmax + 1e-6; x += st) t.push(x); return t; }

function niceMax(v, steps = [10, 20, 25, 50, 100]) {
  for (const s of steps) if (v <= s) return s;
  return Math.ceil(v / 50) * 50;
}

function figComposition(d, conds) {
  const pw = 230, ph = 180, gap = 34, l = 58, t = 34, b = 48, legW = 130;
  const W = l + conds.length * pw + (conds.length - 1) * gap + 28 + legW, H = t + ph + b;
  const hours = d.hours, xmax = hours[hours.length - 1] || 1;
  let s = svgOpen(W, H, 'Cell-cycle composition over time');
  conds.forEach((c, i) => {
    const x0 = l + i * (pw + gap);
    const sx = (h) => x0 + h / xmax * pw, sy = (v) => t + ph - v / 100 * ph;
    const cum = hours.map(() => 0);
    CATS.forEach((k) => {
      const vals = (c.composition[k] || []).map((v) => (v == null ? 0 : v));
      if (!vals.length) return;
      const up = hours.map((h, j) => `${r1(sx(h))} ${r1(sy(cum[j] + vals[j]))}`);
      const lo = hours.map((h, j) => `${r1(sx(h))} ${r1(sy(cum[j]))}`).reverse();
      s += `<path d="M${up.join('L')}L${lo.join('L')}Z" fill="${PUB[k]}" stroke="none"/>`;
      vals.forEach((v, j) => { cum[j] += v; });
    });
    s += yAxis(x0, t, t + ph, [0, 25, 50, 75, 100], sy) + xAxis(t + ph, x0, x0 + pw, timeTicks(xmax), sx);
    s += `<text x="${x0 + pw / 2}" y="${t - 12}" font-size="13" font-weight="700" text-anchor="middle" fill="${esc(c.color)}">${esc(c.name)} (n=${c.n})</text>`;
    s += `<text x="${x0 + pw / 2}" y="${H - 10}" font-size="12" text-anchor="middle" fill="#000">Time (h)</text>`;
  });
  s += `<text x="16" y="${t + ph / 2}" font-size="12" text-anchor="middle" fill="#000" transform="rotate(-90 16 ${t + ph / 2})">% of cells</text>`;
  const lx = W - legW + 4;
  [...CATS].reverse().forEach((k, j) => {
    const y = t + 8 + j * 20;
    s += `<rect x="${lx}" y="${y}" width="14" height="12" fill="${PUB[k]}" stroke="${k === 'Unclassified' ? '#BBBBBB' : 'none'}"/><text x="${lx + 20}" y="${y + 10}" font-size="11" fill="#000">${CAT_LABEL[k]}</text>`;
  });
  return s + '</svg>';
}

function quantile(sorted, q) {
  if (!sorted.length) return null;
  const pos = (sorted.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

function figDurations(d, conds) {
  const phases = [['G1', 'G1 duration'], ['S', 'S duration'], ['G2M', 'G2/M duration']];
  const longest = Math.max(4, ...conds.map((c) => Math.min(18, c.name.length)));
  const cw = Math.max(58, Math.min(124, longest * 6.4 + 12)), l = 62, t = 34, ph = 200, gap = 40, b = 64;
  const pw = Math.max(120, conds.length * cw + 20);
  const W = l + phases.length * pw + (phases.length - 1) * gap + 16, H = t + ph + b;
  let s = svgOpen(W, H, 'Hours per observed stage');
  let seed = 7; const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  phases.forEach(([k, title], pi) => {
    const x0 = l + pi * (pw + gap);
    let mx = 4;
    conds.forEach((c) => (c.durations[k] || []).forEach((v) => { mx = Math.max(mx, v); }));
    const ymax = niceMax(mx * 1.05, [4, 6, 8, 10, 12, 16, 20, 24, 30, 36, 48, 60, 72, 96]);
    const st = ymax <= 12 ? 2 : ymax <= 24 ? 4 : ymax <= 48 ? 12 : 24;
    const ticks = []; for (let v = 0; v <= ymax + 1e-6; v += st) ticks.push(v);
    const sy = (v) => t + ph - v / ymax * ph;
    s += yAxis(x0, t, t + ph, ticks, sy) + `<path d="M${x0} ${t + ph}H${x0 + pw}" stroke="#000"/>`;
    s += `<text x="${x0 + pw / 2}" y="${t - 14}" font-size="13" font-weight="700" text-anchor="middle" fill="#000">${title}</text>`;
    conds.forEach((c, ci) => {
      const cx = x0 + 10 + cw * (ci + 0.5) + (pw - 20 - conds.length * cw) / 2;
      const vals = (c.durations[k] || []).slice().sort((a, b2) => a - b2);
      if (vals.length) {
        const q1 = quantile(vals, 0.25), q3 = quantile(vals, 0.75), med = quantile(vals, 0.5), iqr = q3 - q1;
        const wlo = vals.find((v) => v >= q1 - 1.5 * iqr), whi = [...vals].reverse().find((v) => v <= q3 + 1.5 * iqr);
        const bw = cw * 0.56;
        s += `<path d="M${cx} ${r1(sy(wlo))}V${r1(sy(q1))}M${cx} ${r1(sy(q3))}V${r1(sy(whi))}M${r1(cx - bw / 4)} ${r1(sy(wlo))}H${r1(cx + bw / 4)}M${r1(cx - bw / 4)} ${r1(sy(whi))}H${r1(cx + bw / 4)}" stroke="#000" stroke-width="1.2" fill="none"/>`;
        s += `<rect x="${r1(cx - bw / 2)}" y="${r1(sy(q3))}" width="${r1(bw)}" height="${r1(Math.max(0.5, sy(q1) - sy(q3)))}" fill="none" stroke="#000" stroke-width="1.2"/>`;
        vals.forEach((v) => { s += `<circle cx="${r1(cx + (rnd() - 0.5) * cw * 0.62)}" cy="${r1(sy(v))}" r="2.8" fill="${PUB[k]}" fill-opacity="0.85" stroke="#000" stroke-width="0.6"/>`; });
        s += `<path d="M${r1(cx - bw / 2)} ${r1(sy(med))}H${r1(cx + bw / 2)}" stroke="#000" stroke-width="2.2"/>`;
      }
      const name = c.name.length > 18 ? c.name.slice(0, 17) + '…' : c.name;
      s += `<text x="${r1(cx)}" y="${t + ph + 17}" font-size="11" text-anchor="middle" fill="${esc(c.color)}" font-weight="700">${esc(name)}<title>${esc(c.name)}</title></text><text x="${r1(cx)}" y="${t + ph + 31}" font-size="10" text-anchor="middle" fill="#444">n=${vals.length}</text>`;
    });
  });
  s += `<text x="16" y="${t + ph / 2}" font-size="12" text-anchor="middle" fill="#000" transform="rotate(-90 16 ${t + ph / 2})">Hours per observed stage</text>`;
  return s + '</svg>';
}

function figFractions(d, conds) {
  const panels = [['G1', 'G1 (green)'], ['S', 'S (red)'], ['G2M', 'G2/M (yellow)']];
  const pw = 250, ph = 190, gap = 46, l = 58, b = 48;
  const legRows = Math.ceil(conds.length / 1);
  const t = 40 + legRows * 16;
  const W = l + panels.length * pw + (panels.length - 1) * gap + 16, H = t + ph + b;
  const hours = d.hours, xmax = hours[hours.length - 1] || 1;
  let s = svgOpen(W, H, 'Phase fractions over time');
  let mx = 10;
  conds.forEach((c) => panels.forEach(([k]) => (c.composition[k] || []).forEach((v) => { if (v != null) mx = Math.max(mx, v); })));
  const ymax = niceMax(mx * 1.05, [20, 30, 40, 50, 60, 80, 100]);
  const st = ymax <= 30 ? 5 : ymax <= 60 ? 10 : 20;
  const ticks = []; for (let v = 0; v <= ymax + 1e-6; v += st) ticks.push(v);
  panels.forEach(([k, title], pi) => {
    const x0 = l + pi * (pw + gap);
    const sx = (h) => x0 + h / xmax * pw, sy = (v) => t + ph - Math.min(v, ymax) / ymax * ph;
    s += yAxis(x0, t, t + ph, ticks, sy) + xAxis(t + ph, x0, x0 + pw, timeTicks(xmax), sx);
    s += `<text x="${x0 + pw / 2}" y="18" font-size="13" font-weight="700" text-anchor="middle" fill="#000">${title}</text>`;
    s += `<text x="${x0 + pw / 2}" y="${H - 10}" font-size="12" text-anchor="middle" fill="#000">Time (h)</text>`;
    conds.forEach((c) => {
      const pts = [];
      (c.composition[k] || []).forEach((v, j) => { if (v != null) pts.push(`${r1(sx(hours[j]))} ${r1(sy(v))}`); });
      if (pts.length) s += `<path d="M${pts.join('L')}" fill="none" stroke="${esc(c.color)}" stroke-width="2" stroke-linejoin="round"${c.dashed ? ' stroke-dasharray="6 4"' : ''}/>`;
    });
    if (pi === 0) {  // legend between the first panel's title and its plot, so it never hides a line
      conds.forEach((c, j) => {
        const y = 36 + j * 16, lx = x0 + 8;
        s += `<path d="M${lx} ${y - 4}H${lx + 22}" stroke="${esc(c.color)}" stroke-width="2"${c.dashed ? ' stroke-dasharray="6 4"' : ''}/><text x="${lx + 28}" y="${y}" font-size="11" fill="#000">${esc(c.name.length > 26 ? c.name.slice(0, 25) + '…' : c.name)} (n=${c.n})</text>`;
      });
    }
  });
  s += `<text x="16" y="${t + ph / 2}" font-size="12" text-anchor="middle" fill="#000" transform="rotate(-90 16 ${t + ph / 2})">% of cells</text>`;
  return s + '</svg>';
}

function figColonies(pop, conds) {
  const panels = [['colonies', 'Colony number', 'Colonies per image'], ['colony_area', 'Average colony area', 'Mean colony area (µm²)']];
  const pw = 280, ph = 200, gap = 70, l = 66, b = 48;
  const byId = Object.fromEntries(pop.conditions.map((c) => [c.id, c]));
  const rows = conds.filter((c) => byId[c.id] && byId[c.id].colonies);
  const t = 40 + rows.length * 16;
  const W = l + panels.length * pw + (panels.length - 1) * gap + 16, H = t + ph + b;
  const hours = pop.hours, xmax = hours[hours.length - 1] || 1;
  let s = svgOpen(W, H, 'Colonies over time');
  panels.forEach(([k, title, ylab], pi) => {
    const x0 = l + pi * (pw + gap);
    let mx = 0;
    rows.forEach((c) => byId[c.id][k].mean.forEach((v, j) => { if (v != null) mx = Math.max(mx, v + (byId[c.id][k].sem[j] || 0)); }));
    const { ymax, ticks } = niceTicks(mx || 1);
    const sx = (h) => x0 + h / xmax * pw, sy = (v) => t + ph - Math.min(v, ymax) / ymax * ph;
    const fmt = (v) => (k === 'colony_area' && v >= 1000 ? (v / 1000).toFixed(v % 1000 ? 1 : 0) + 'k' : String(v));
    s += yAxis(x0, t, t + ph, ticks, sy, fmt) + xAxis(t + ph, x0, x0 + pw, timeTicks(xmax), sx);
    s += `<text x="${x0 + pw / 2}" y="18" font-size="13" font-weight="700" text-anchor="middle" fill="#000">${title}</text>`;
    s += `<text x="${x0 + pw / 2}" y="${H - 10}" font-size="12" text-anchor="middle" fill="#000">Time (h)</text>`;
    s += `<text x="${x0 - 50}" y="${t + ph / 2}" font-size="12" text-anchor="middle" fill="#000" transform="rotate(-90 ${x0 - 50} ${t + ph / 2})">${ylab}</text>`;
    rows.forEach((c) => {
      const d = byId[c.id][k];
      const up = [], lo = [], ln = [];
      d.mean.forEach((v, j) => {
        if (v == null) return;
        const e = d.sem[j] || 0;
        up.push(`${r1(sx(hours[j]))} ${r1(sy(v + e))}`); lo.push(`${r1(sx(hours[j]))} ${r1(sy(Math.max(0, v - e)))}`); ln.push(`${r1(sx(hours[j]))} ${r1(sy(v))}`);
      });
      if (!ln.length) return;
      s += `<path d="M${up.join('L')}L${lo.reverse().join('L')}Z" fill="${esc(c.color)}" fill-opacity="0.18" stroke="none"/>`;
      s += `<path d="M${ln.join('L')}" fill="none" stroke="${esc(c.color)}" stroke-width="2" stroke-linejoin="round"${c.dashed ? ' stroke-dasharray="6 4"' : ''}/>`;
    });
    if (pi === 0) {
      rows.forEach((c, j) => {
        const y = 36 + j * 16, lx = x0 + 8;
        s += `<path d="M${lx} ${y - 4}H${lx + 22}" stroke="${esc(c.color)}" stroke-width="2"${c.dashed ? ' stroke-dasharray="6 4"' : ''}/><text x="${lx + 28}" y="${y}" font-size="11" fill="#000">${esc(c.name.length > 26 ? c.name.slice(0, 25) + '…' : c.name)} (${byId[c.id].wells.length} well${byId[c.id].wells.length === 1 ? '' : 's'})</text>`;
      });
    }
  });
  return s + '</svg>';
}

function figSection(id, title, sub, svg) {
  return `<section class="card" aria-labelledby="${id}-h"><div class="card-head"><div><h2 id="${id}-h">${title}</h2><p>${sub}</p></div>
      <button class="btn" data-save="${id}" data-name="${esc(title)}">${ICON.down} Save PNG + SVG</button></div>
    <div class="fig-card" id="${id}">${svg}</div></section>`;
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

async function renderFigures() {
  if (!hasResults()) return needResults();
  if (!S.fig.data) main.innerHTML = header(5, 'Figures') + '<p class="muted">Loading…</p>';
  let d, pop;
  try { [d, pop] = await Promise.all([api('figures'), api('population')]); } catch (e) { return fail(e); }
  if (S.view !== 'figures') return;
  S.fig.data = d;
  const hasCol = pop.conditions.some((c) => c.colonies && c.colonies.mean.some((v) => v != null));
  const e = S.st.experiment;
  const shown = d.conditions.filter((c) => !c.hidden && c.n > 0);
  const empty = '<p style="color:#444;padding:20px">No conditions to show. Assign wells on the plate map (step 1) or tick “Show” above.</p>';
  main.innerHTML = header(5, 'Figures', `<button class="btn primary" id="fig-saveall">${ICON.down} Save all figures</button>`)
    + experimentsCard(e, d) + stylesCard(d)
    + figSection('fig-a', 'Cell-cycle composition over time', 'Every cell present in the first frame (a founder) is followed through its divisions, along one daughter each time. Colourless = neither reporter on; Unclassified = no longer tracked (lost or left the field).', shown.length ? figComposition(d, shown) : empty)
    + figSection('fig-b', 'Time spent in each phase', 'Hours in each phase, for phases seen from start to end inside the movie. One dot per cell per phase; boxes show the median and quartiles, whiskers 1.5 × IQR. Every value is ± one frame interval.', shown.length ? figDurations(d, shown) : empty)
    + figSection('fig-c', 'Phase fractions over time', 'Percent of the followed cells in each phase, per condition (same cells as the composition figure).', shown.length ? figFractions(d, shown) : empty)
    + figSection('fig-d', 'Colonies over time', `From the confluence mask: touching cells form one colony; objects under ${num(S.st.experiment.settings.min_object_um2)} µm² are ignored as debris. Lines are the mean across wells, bands the SEM.`, !shown.length ? empty : hasCol ? figColonies(pop, shown) : '<p style="color:#444;padding:20px">No colony measurements yet: re-run the analysis.</p>')
    + `<p class="small muted">Saved figures go to the “figures” folder inside “PIP-FUCCI results”: SVG for Illustrator or Inkscape, PNG at 3× for slides. The numbers behind them are in every table export (“Figure data”).</p>`;
  bindFigures(d);
}

function bindFigures(d) {
  const addLinked = async (path) => {
    try { S.st = await api('link', { path }); markSaved(); S.fig.data = null; renderFigures(); toast('Experiment added.'); } catch (err) { fail(err); }
  };
  $('#link-add').onclick = async () => {
    try {
      let p;
      const shell = window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder;
      if (shell) p = await window.pywebview.api.pick_folder(); else p = (await api('pick_folder', {})).path;
      if (p) addLinked(p); else if (!S.st.native_dialogs) $('#link-manual').style.display = 'flex';
    } catch (err) { fail(err); }
  };
  $('#link-go').onclick = () => addLinked($('#link-path').value.trim());
  $$('[data-unlink]').forEach((b) => b.onclick = async () => { try { S.st = await api('unlink', { path: b.dataset.unlink }); markSaved(); renderFigures(); } catch (err) { fail(err); } });
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
  $('#fig-saveall').onclick = async () => {
    let last = null;
    for (const b of $$('[data-save]')) last = await saveFigure(b.dataset.save, b.dataset.name, false) || last;
    if (last) toast(`Saved ${$$('[data-save]').length} figures (PNG + SVG).`, { action: { label: 'Show in Finder', fn: () => api('reveal', { path: last.folder }).catch(fail) }, ms: 12000 });
  };
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

async function saveFigure(id, name, announce) {
  const el = $('#' + id + ' svg');
  if (!el) return null;
  const svg = '<?xml version="1.0" encoding="UTF-8"?>\n' + el.outerHTML;
  let png = null;
  try { png = await svgToPng(el.outerHTML); } catch (e) { /* SVG still saved */ }
  try {
    const r = await api('save_figure', { name, svg, png });
    if (announce) toast(`Saved “${name}” (${png ? 'PNG + SVG' : 'SVG'}).`, { action: { label: 'Show in Finder', fn: () => api('reveal', { path: r.path }).catch(fail) }, ms: 12000 });
    return r;
  } catch (e) { fail(e); return null; }
}

// ---------------------------------------------------------------- keyboard + resize
document.addEventListener('keydown', (ev) => {
  if (S.view !== 'viewer' || !$('#modal').hidden || ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
  if (ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') {
    ev.preventDefault();
    if (S.v.playing) togglePlay();
    goFrame(S.v.frame + (ev.key === 'ArrowRight' ? 1 : -1) * (ev.shiftKey ? 5 : 1));
  } else if (ev.key === ' ' && document.activeElement.tagName !== 'BUTTON') {
    ev.preventDefault();
    togglePlay();
  } else if (ev.key === 'Home') goFrame(0);
  else if (ev.key === 'End') goFrame(S.st.experiment.hours.length - 1);
});
let rz;
window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { if (S.view === 'viewer') drawScatter(); if (S.view === 'cells') drawHeat(); }, 150); });

// ---------------------------------------------------------------- updates
function analysisRunning() { return !!(S.st && S.st.progress && S.st.progress.running); }

async function updateNow() {
  const ov = document.getElementById('overlay');
  ov.hidden = false;
  S.updating = true;
  stopPlay();
  try {
    const r = await api('update_now', {});
    $('#overlay-title').textContent = `Updating to version ${r.version}…`;
    const keep = new URLSearchParams({ view: S.view });
    if (S.v.well) { keep.set('w', S.v.well); keep.set('fd', S.v.field); keep.set('t', S.v.frame); }
    if (S.v.sel) keep.set('vs', S.v.sel);
    if (S.sc.sel) keep.set('cs', S.sc.sel);
    location.replace(r.url + '#' + keep.toString());
  } catch (e) {
    ov.hidden = true;
    S.updating = false;
    fail(e);
  }
}

// top bar: experiment name, "saved" indicator, update status, settings
function renderTopLeft() {
  const tl = document.getElementById('top-left');
  const e = S.st && S.st.experiment;
  tl.innerHTML = e ? `<span style="color:var(--text);font-weight:500">${esc(e.settings.experiment_name || e.summary.folder.split('/').pop())}</span>` : '';
  const sv = document.getElementById('saved');
  if (e && e.saved_at) {
    sv.hidden = false;
    sv.title = 'Settings, plate map, gates, track flags and results are saved automatically, as soon as you change them, in the “PIP-FUCCI results” folder next to the images.';
    sv.innerHTML = `${ICON.check}<span>Saved automatically${S.savedFlash ? '' : ` · ${esc(e.saved_at)}`}</span>`;
  } else sv.hidden = true;
}

function markSaved() {
  const sv = document.getElementById('saved');
  const now = new Date();
  if (S.st && S.st.experiment) S.st.experiment.saved_at = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
  renderTopLeft();
  sv.style.color = '#5FD487';
  clearTimeout(S.savedT);
  S.savedT = setTimeout(() => { sv.style.color = ''; }, 1500);
}

let lastInput = Date.now();
['pointerdown', 'keydown', 'wheel'].forEach((t) => document.addEventListener(t, () => { lastInput = Date.now(); }, { passive: true }));

async function loadPrefs() {
  try { S.prefs = await api('prefs'); } catch (e) { S.prefs = S.prefs || { auto_check: true, auto_install: true, workers: 0, cpus: 1 }; }
  return S.prefs;
}

async function refreshUpdate() {
  let u;
  try { u = await api('update'); } catch (e) { clearTimeout(S.updTimer); S.updTimer = setTimeout(refreshUpdate, 20000); return; }
  S.upd = u;
  if (!S.prefs) await loadPrefs();
  const pill = document.getElementById('upd-pill');
  const banner = document.getElementById('banner');
  const ver = S.st ? S.st.version : '';
  if (u.just_updated) toast(`CycleTrack is now version ${u.just_updated}.`);
  const busy = analysisRunning();
  if (!u.enabled) {  // running from source: just say a new release exists
    pill.innerHTML = u.available ? `<button class="pill ready" id="upd-btn">Download version ${esc(u.latest)}</button>` : `<span class="pill">Version <span class="mono">${esc(ver)}</span></span>`;
    const bt = $('#upd-btn'); if (bt) bt.onclick = () => api('open_url', { url: u.url }).catch(fail);
    banner.innerHTML = '';
  } else if (u.state === 'ready') {
    pill.innerHTML = `<button class="pill ready" id="upd-btn" ${busy ? 'disabled' : ''} title="${busy ? 'Available when the analysis has finished' : 'Switches to the new version in a few seconds; your experiment stays open'}">${ICON.down} Update now · ${esc(u.ready_version)}</button>`;
    $('#upd-btn').onclick = updateNow;
    if (S.bannerHidden !== u.ready_version && !S.prefs.auto_install) {
      banner.innerHTML = `<div class="banner"><div class="txt">${ICON.info}<span><strong>Version ${esc(u.ready_version)} of CycleTrack is ready.</strong> <span class="muted">${busy ? 'You can update when the analysis finishes.' : 'Takes a few seconds; your experiment stays open.'}</span></span></div>
        <div style="display:flex;gap:8px"><button class="btn sm" id="upd-later">Later</button><button class="btn sm primary" id="upd-now" ${busy ? 'disabled' : ''} style="min-height:32px">Update now</button></div></div>`;
      $('#upd-now').onclick = updateNow;
      $('#upd-later').onclick = () => { S.bannerHidden = u.ready_version; banner.innerHTML = ''; };
    } else banner.innerHTML = '';
    // automatic install: when nothing is running and she hasn't touched the app for a minute
    if (S.prefs.auto_install && !busy && Date.now() - lastInput > 60000 && $('#modal').hidden && !S.updating) {
      toast(`Installing version ${u.ready_version}…`);
      updateNow();
    }
  } else if (u.state === 'downloading' || u.state === 'installing') {
    const pct = Math.round((u.progress || 0) * 100);
    pill.innerHTML = `<span class="pill" title="A new version is downloading in the background">Downloading ${esc(u.latest || 'update')} <span class="minibar"><div style="width:${pct}%"></div></span></span>`;
    banner.innerHTML = '';
  } else {
    const label = u.state === 'checking' ? 'Checking for updates…' : u.state === 'up_to_date' ? 'Up to date' : u.state === 'off' ? 'Automatic updates off' : u.state === 'error' ? 'Could not check for updates' : '';
    pill.innerHTML = `<button class="pill" id="upd-btn" style="cursor:pointer" title="${esc(u.message || 'Update settings')}">${u.state === 'up_to_date' ? ICON.check : u.state === 'error' ? ICON.warn : ''}<span class="mono">${esc(ver)}</span>${label ? ` · ${label}` : ''}</button>`;
    $('#upd-btn').onclick = openSettings;
    banner.innerHTML = '';
  }
  if (!$('#modal').hidden && S.modal === 'settings') settingsStatus();
  const fast = ['downloading', 'installing', 'checking'].includes(u.state) || u.state === 'ready';
  clearTimeout(S.updTimer);
  S.updTimer = setTimeout(refreshUpdate, fast ? 2000 : 20000);
}

function closeModal() { $('#modal').hidden = true; S.modal = null; }
$('#modal').addEventListener('click', (ev) => { if (ev.target.id === 'modal') closeModal(); });
document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && !$('#modal').hidden) closeModal(); });
document.getElementById('btn-settings').onclick = () => openSettings();

function settingsStatus() {
  const el = $('#set-status');
  if (!el || !S.upd) return;
  const u = S.upd;
  const when = u.checked_at ? new Date(u.checked_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : null;
  el.textContent = !u.enabled ? (u.available ? `Version ${u.latest} is available.` : 'Updates are handled by the installed app.')
    : u.state === 'ready' ? `Version ${u.ready_version} has downloaded and is ready.`
      : u.state === 'downloading' ? `Downloading version ${u.latest}…`
        : u.state === 'checking' ? 'Checking…'
          : u.state === 'error' ? `Could not check (${u.message || 'no connection'}).`
            : `You have the newest version${when ? ` (checked ${when})` : ''}.`;
}

async function openSettings() {
  const p = await loadPrefs();
  const box = $('#modal-box');
  const cores = Array.from({ length: p.cpus }, (_, i) => i + 1);
  box.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center"><h2 id="modal-title" style="font-size:18px">Settings</h2><button class="icon-btn" id="m-close" aria-label="Close">${ICON.x}</button></div>
    <div><h3>Updates</h3>
      <label class="toggle"><input type="checkbox" id="p-check" ${p.auto_check ? 'checked' : ''}><span class="t"><strong>Check for updates automatically</strong><span>Looks for a new version when the app opens and every 15 minutes, and downloads it in the background.</span></span></label>
      <label class="toggle"><input type="checkbox" id="p-install" ${p.auto_install ? 'checked' : ''}><span class="t"><strong>Install updates automatically</strong><span>Switches to a downloaded version the next time the app is opened, or after a minute without use when no analysis is running. Your experiment stays open. When off, use the blue “Update now” button at the top right.</span></span></label>
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding-top:10px;border-top:1px solid #23282D"><button class="btn sm" id="p-now">Check now</button><span class="small muted" id="set-status"></span></div></div>
    <div><h3>Notifications</h3>
      <label class="toggle"><input type="checkbox" id="p-notify" ${p.notify !== false ? 'checked' : ''}><span class="t"><strong>Notify me when an analysis finishes</strong><span>A macOS notification and a bounce of the Dock icon. While it runs, the Dock icon shows the progress. The first time, macOS asks whether CycleTrack may send notifications: click Allow.</span></span></label></div>
    <div><h3>Processing</h3>
      <div class="setrow"><div class="lbl"><label for="p-workers">Processor cores for analysis</label><span>Images are segmented in parallel. Auto uses all but one core, limited by memory. This Mac has ${p.cpus} core${p.cpus === 1 ? '' : 's'}.</span></div>
        <select class="input" id="p-workers" style="flex:0 0 150px"><option value="0" ${!p.workers ? 'selected' : ''}>Auto</option>${cores.map((n) => `<option value="${n}" ${p.workers === n ? 'selected' : ''}>${n}</option>`).join('')}</select></div></div>
    <div class="info">${ICON.check}<span>Everything is saved automatically: settings, plate map, gates, track flags and results go to the “PIP-FUCCI results” folder next to the images as soon as they change. There is no Save button to forget.</span></div>
    <div class="kv small"><span class="muted">Version</span><span class="mono">${esc(S.st.version)}</span></div>`;
  $('#modal').hidden = false;
  S.modal = 'settings';
  settingsStatus();
  $('#m-close').onclick = closeModal;
  $('#m-close').focus();
  const save = async (patch) => { try { S.prefs = await api('prefs', patch); refreshUpdate(); } catch (e) { fail(e); } };
  $('#p-check').onchange = (ev) => save({ auto_check: ev.target.checked });
  $('#p-install').onchange = (ev) => save({ auto_install: ev.target.checked });
  $('#p-workers').onchange = (ev) => save({ workers: parseInt(ev.target.value, 10) });
  $('#p-notify').onchange = (ev) => save({ notify: ev.target.checked });
  $('#p-now').onclick = async () => {
    $('#set-status').textContent = 'Checking…';
    try { await api('check_updates', {}); setTimeout(refreshUpdate, 1500); setTimeout(refreshUpdate, 5000); } catch (e) { fail(e); }
  };
}

// ---------------------------------------------------------------- start
(async function start() {
  try {
    const h = new URLSearchParams(location.hash.slice(1));  // coming back after "Update now"
    if (VIEWS.includes(h.get('view'))) S.view = h.get('view');
    if (h.get('w')) { S.v.well = h.get('w'); S.v.field = parseInt(h.get('fd'), 10) || 1; S.v.frame = parseInt(h.get('t'), 10) || 0; }
    if (h.get('vs')) S.v.sel = h.get('vs');
    if (h.get('cs')) { S.sc.sel = h.get('cs'); S.sc.pinned = true; }
    history.replaceState(null, '', location.pathname);
    await refreshState();
    if (S.view !== 'experiment' && !hasResults()) S.view = 'experiment';
    updateSteps();
    render();
    refreshUpdate();
  } catch (e) { fail(e); }
})();
