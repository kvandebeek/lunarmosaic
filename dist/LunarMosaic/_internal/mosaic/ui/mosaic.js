// LunarMosaic: import panel sets, build, place colour, edit the chains, preview at 1:1, export (plan phase 7).
import {Autosave} from './save.js';
import {ChainEditor, decimalText, decimalValue, h, spinner} from './chains.js';
import {Previews} from './previews.js';

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
let state = {project: null, job: {state: 'idle'}}, page = 'import', view = 'composite';
let scale = 1, offset = {x: 0, y: 0}, fitted = true, geometry = null, previewVersion = 0, detailTimer, toastTimer;
let polling = false, importing = false, loadedProject = null, editor = null, previews = null;
let analysis = {}, analysisTimer = null, analysisTicket = 0, analysisVersion = 0, shownChain = '';
let buildCanvas = null, estimateVersion = 0, estimateRevision = null, recentVersion = 0, exportVersion = 0, refreshTimer = null;
let colourStale = false;   // bugs-codex.md CODEX-04: a failed re-placement's previous colour result
let mainStale = false;     // BUGS.md bug 19: the mosaic/detail view skipped a refresh while a preview covered it
let histogram = null;
// BUGS.md bug 18: every render gets a server-visible id.  The status endpoint reports the real current block
// and completed render work, while this small client tracker owns the toolbar indicator.
const renderJobs = new Map();
const updateRenderStatus = () => {
  const current = [...renderJobs.values()].at(-1);
  $('#canvas-busy').hidden = !current;
  $('#canvas-render-status').hidden = !current;
  $('#canvas-render-progress').hidden = !current || !current.total;
  if (!current) return;
  $('#canvas-render-status').textContent = current.message || `${current.label}…`;
  if (current.total) $('#canvas-render-progress').value = current.done / current.total;
};
function trackRender(label) {
  const rid = `r${Date.now().toString(36)}${crypto.getRandomValues(new Uint32Array(1))[0].toString(36)}`;
  const job = {rid, label, message: `${label}…`, done: 0, total: 0, active: true};
  renderJobs.set(rid, job); updateRenderStatus();
  const pollStatus = async () => {
    while (job.active) {
      try {
        const s = await api(`render-status?id=${encodeURIComponent(state.project?.id || '')}&rid=${encodeURIComponent(rid)}`);
        if (s.status) {
          job.message = s.status.message || job.message;
          job.done = s.status.done || 0; job.total = s.status.total || 0;
          updateRenderStatus();
        }
      } catch (_) { /* a superseded request or close needs no extra toast */ }
      await new Promise(resolve => setTimeout(resolve, 160));
    }
  };
  pollStatus();
  return {rid, finish: () => { job.active = false; renderJobs.delete(rid); updateRenderStatus(); }};
}
const BUILDER = ['Check panels', 'Find coarse features', 'Discover overlaps', 'Solve panel layout', 'Refine alignment',
                 'Match brightness', 'Select the best-resolving regions', 'Blend seams', 'Finish & save'];
const PLACING = ['Find each panel on the luminance', 'Fit its pose on held-out tiles', 'Match brightness and colour',
                 'Blend the colour canvas'];
const points = {L: [], RGB: []};

function message(text) {
  $('#toast').textContent = text; $('#toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 8000);
}

// bugs-codex.md CODEX-03/CODEX-02: a toast with its own Undo button, for a delete or a chain import
function undoToast(text, undo) {
  const toast = $('#toast');
  toast.replaceChildren(document.createTextNode(text + ' '),
    h('button', {class: 'btn small', type: 'button', onclick: () => { toast.hidden = true; undo(); }}, 'Undo'));
  toast.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { toast.hidden = true; }, 8000);
}

async function api(path, data) {
  const options = data === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)};
  const r = await fetch('/api/' + path, options);
  const o = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(o.error || 'The operation failed.'); e.status = r.status; e.conflict = !!o.conflict; e.exists = !!o.exists; throw e; }
  return o;
}
const payload = (data = {}) => ({id: state.project?.id, ...data});

// One queue for every change, each sent with the revision current when it leaves: this page never conflicts with
// itself, only with another window — and then the change is kept and sent again (save.js).
let queue = Promise.resolve();
function mutate(path, data = {}) {
  const run = queue.then(async () => {
    try {
      state = await api(path, payload({...data, revision: state.project.revision}));
    } catch (e) {
      if (e.conflict) { try { state = await api('state'); render(); } catch (_) { /* reported by the caller */ } }
      throw e;
    }
    render();
    return state;
  });
  queue = run.catch(() => {});
  return run;
}

// BUGS.md bug 14: a path box with no way to browse for one. 'save' asks for a not-yet-existing name (a new
// project folder, an export file); 'folder' and 'file' ask for something that already exists. macOS only; a
// box can still always be typed into.
async function browse(kind, input, name) {
  try {
    const q = new URLSearchParams({kind, start: input.value.trim()});
    if (name) q.set('name', name);
    const r = await api('choose?' + q);
    if (!r.supported) { message('No file chooser is available here (macOS only). Type the path instead.'); return; }
    if (r.path) { input.value = r.path; input.dispatchEvent(new Event('change', {bubbles: true})); input.focus(); }
  } catch (e) { message(e.message); }
}

async function action(name, data = {}) {
  try { await chainSave.flush().catch(() => {}); await queue; state = await api(name, payload(data)); render(); return true; }
  catch (e) { message(e.message); return false; }
}

// The chain: shown at once, saved 300 ms after the last change. A conflict or a lost connection is retried; a
// chain the server refuses waits, with the reason shown, for the next edit.
const chainSave = new Autosave({
  send: doc => mutate('chain', {chain: doc}).then(() => { scheduleRefresh(); loadAnalysis(); },
                                                  e => { if (e.status === 400 && !e.conflict) e.retry = false; throw e; }),
  onState: s => {
    $('#saved').textContent = s.failed ? `Not saved: ${s.failed}${s.retrying ? ' Trying again…' : ''}` : s.saving ? 'Saving…'
      : state.project ? 'Saved locally' : 'Local · private';
    $('#saved').classList.toggle('warning', !!s.failed);
  },
});

// ---------------------------------------------------------------- pages

const lum = p => p.sets.filter(s => s.stream === 'luminance');
const colourSet = p => p.sets.find(s => s.id === 'RGB');
const built = (p, name) => !!p?.[name]?.mosaic;

function showPage(next) {
  // muse-bugs.md MUSE-01: *03 Match & edit* has nothing to show before the luminance mosaic exists
  if (next === 'edit' && state.project && !built(state.project, 'luminance')) { message('Build the luminance mosaic first.'); next = landingPage(); }
  page = next;
  document.body.scrollTop = 0; document.documentElement.scrollTop = 0;
  showVisibility();
  $$('.mast nav button').forEach(b => { b.classList.toggle('active', b.dataset.page === page);
    b.disabled = !state.project || (b.dataset.page === 'edit' && !built(state.project, 'luminance')); });
  if (page === 'edit' && built(state.project, 'luminance')) { refreshPreview(); loadAnalysis(); }
  if (page === 'import') loadEstimate();
  if (page === 'projects' || !state.project) loadRecent();
}

function showVisibility() {
  const editing = page === 'edit' && built(state.project, 'luminance');
  document.body.classList.toggle('editing', editing);
  $('#setup').hidden = editing; $('#workspace').hidden = !editing;
  $('#welcome').hidden = !!state.project && page !== 'projects'; $('#close-project').hidden = !state.project;
  $('.hero').hidden = !!state.project && page !== 'projects';
  $('#import-page').hidden = !state.project || page !== 'import'; $('#build-page').hidden = !state.project || page !== 'build';
  if (state.project && page !== 'projects') $('#recent-projects').hidden = true;
}

function landingPage() {
  const p = state.project;
  if (state.job.state === 'running') return 'build';
  if (['luminance', 'colour'].some(n => ['interrupted', 'failed', 'missing'].includes(p?.[n]?.state))) return 'build';
  return built(p, 'luminance') ? 'edit' : 'import';
}

// FNV-1a, 32 bits: a short key for a long state
const hash = text => { let x = 2166136261; for (let i = 0; i < text.length; i++) { x ^= text.charCodeAt(i); x = Math.imul(x, 16777619); } return (x >>> 0).toString(16); };
const fileSize = bytes => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GiB` : `${(bytes / 1024 ** 2).toFixed(1)} MiB`;

async function loadRecent() {
  const ticket = ++recentVersion;
  try {
    const info = await api('recent'); if (ticket !== recentVersion) return;
    $('#recent-projects').hidden = !info.enabled || (!!state.project && page !== 'projects');
    $('#recent-warning').textContent = info.warning || '';
    const list = $('#recent-list');
    list.replaceChildren();
    if (!info.projects.length) list.append(h('li', {class: 'hint', text: 'Your saved mosaics will appear here after you create or open a project.'}));
    for (const project of info.projects) {
      const button = h('button', {class: 'recent-open', disabled: !project.available || state.job.state === 'running',
          onclick: async () => { if (await action('open', {folder: project.folder})) showPage(landingPage()); }},
        h('strong', {text: project.name}),
        h('small', {text: [project.state, project.panels ? `${project.panels.L} L · ${project.panels.RGB} RGB` : null, project.bytes ? `${fileSize(project.bytes)} native mosaics` : null].filter(Boolean).join(' · ')}),
        h('small', {text: project.folder}));
      list.append(h('li', {}, button));
    }
  } catch (e) { $('#recent-warning').textContent = e.message; }
}

// ---------------------------------------------------------------- import: streams of sets

const setCards = new Map();
const opticsOf = card => Object.fromEntries(['pixel_um', 'focal_mm', 'barlow', 'aperture_mm'].map((key, i) => {
  const raw = [$('.pixel', card), $('.focal', card), $('.barlow', card), $('.aperture', card)][i].value;
  const value = decimalValue(raw);
  // Keep invalid text intact for the API's written validation instead of JSON.stringify turning NaN into null
  // and silently removing a previously entered optical value.
  return [key, raw.trim() === '' ? '' : Number.isFinite(value) ? value : raw];
}));
const arcsec = o => o.pixel_um && o.focal_mm ? 206.265 * o.pixel_um / (o.focal_mm * (o.barlow || 1)) : null;
const setName = (p, id) => p.sets.find(x => x.id === id)?.label || id;

function setCard(s) {
  const key = s.id;
  const card = h('article', {class: 'card set', dataset: {set: key}},
    h('div', {class: 'set-title'}, h('input', {class: 'set-label', 'aria-label': 'Set name', maxlength: 120}), h('span', {class: 'tag state'}),
      key !== 'L' && key !== 'RGB' ? h('button', {class: 'icon remove-set', type: 'button', title: 'Remove this set', 'aria-label': 'Remove this set'}, '×') : null),
    h('label', {class: 'drop', tabindex: 0}, h('span', {class: 'drop-icon', text: '＋'}), h('strong', {text: 'Choose a folder of panels'}),
      h('span', {text: 'TIFF, PNG or JPEG · originals stay untouched'}), h('input', {type: 'file', webkitdirectory: true, multiple: true, hidden: true, class: 'folder-upload'})),
    h('div', {class: 'divider', text: 'or use a local folder directly'}),
    h('div', {class: 'path-row'}, h('input', {class: 'source-path', placeholder: '/path/to/panels', 'aria-label': 'Panel folder'}),
      h('button', {class: 'btn browse', type: 'button'}, 'Browse…'), h('button', {class: 'btn scan', type: 'button'}, 'Use folder')),
    h('details', {class: 'optics', open: true}, h('summary', {}, 'Camera & optics ', h('span', {text: 'needed when sets differ in scale'})),
      h('div', {class: 'optics-grid'}, ...[['pixel', 'Pixel size · µm', '2.9', 0.1], ['focal', 'Focal length · mm', '1200', 1], ['barlow', 'Barlow', '1', 0.1], ['aperture', 'Aperture · mm', '250', 1]]
        .map(([c, l, ph, step]) => { const inp = h('input', {class: `${c} numeric-input`, type: 'text', inputmode: 'decimal', 'data-min': 0, placeholder: ph, 'aria-label': l});
          return h('label', {}, l, h('span', {class: 'param-inputs'}, inp, spinner(inp, {step, min: 0}))); }))),
    h('p', {class: 'scale hint'}), h('div', {class: 'scale-actions button-row'}),
    h('div', {class: 'set-summary'}), h('div', {class: 'set-error'}),
    h('details', {class: 'files'}, h('summary', {text: 'Panel list'}), h('ul', {})));
  $('.set-label', card).onchange = () => mutate('sets', {op: 'update', set: {id: key, label: $('.set-label', card).value}}).catch(e => message(e.message));
  $('.remove-set', card)?.addEventListener('click', () => mutate('sets', {op: 'delete', set: {id: key}}).catch(e => message(e.message)));
  $('.browse', card).onclick = () => browse('folder', $('.source-path', card));
  $('.scan', card).onclick = () => action('inputs', {set: key, paths: [$('.source-path', card).value], optics: opticsOf(card)});
  $('.folder-upload', card).onchange = e => importFiles(key, [...e.target.files]);
  $$('.optics input', card).forEach(el => { el.onchange = () => {
    const now = state.project.sets.find(x => x.id === key);
    if (now.inputs.length) action('inputs', {set: key, paths: now.inputs, optics: opticsOf(card)});
    else mutate('sets', {op: 'update', set: {id: key, optics: opticsOf(card)}}).catch(e => message(e.message));
  }; });
  const drop = $('.drop', card);
  const locked = () => card.dataset.locked === 'true';          // muse-bugs.md MUSE-02: drag bypassed the lock
  drop.onkeydown = e => { if (locked()) return; if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('.folder-upload', card).click(); } };
  drop.ondragover = e => { if (locked()) return; e.preventDefault(); drop.classList.add('drag'); };
  drop.ondragleave = () => drop.classList.remove('drag');
  drop.ondrop = e => { if (locked()) return; e.preventDefault(); drop.classList.remove('drag'); importFiles(key, [...e.dataTransfer.files]); };
  return card;
}

function renderSets(p, busy) {
  const wanted = new Set(p.sets.map(s => s.id));
  for (const [id, card] of setCards) if (!wanted.has(id)) { card.remove(); setCards.delete(id); }
  const mixed = lum(p).filter(s => s.inputs.length).length > 1, both = colourSet(p).stream === 'both';
  for (const s of p.sets) {
    let card = setCards.get(s.id);
    const fresh = !card;
    if (fresh) { card = setCard(s); setCards.set(s.id, card); }
    const home = s.stream === 'luminance' ? $('#lum-sets') : $('#colour-sets');
    if (card.parentElement !== home) home.append(card);
    const stream = p[s.stream === 'luminance' ? 'luminance' : 'colour'];
    if (fresh || loadedProject !== p.id || !card.contains(document.activeElement)) {
      $('.set-label', card).value = s.label || s.id;
      const o = s.optics || {};
      $('.pixel', card).value = o.pixel_um ? decimalText(o.pixel_um) : ''; $('.focal', card).value = o.focal_mm ? decimalText(o.focal_mm) : '';
      $('.barlow', card).value = o.barlow ? decimalText(o.barlow) : ''; $('.aperture', card).value = o.aperture_mm ? decimalText(o.aperture_mm) : '';
    }
    $('.state', card).textContent = stream.mosaic ? 'built' : s.inputs.length ? 'ready' : s.stream === 'colour' ? 'optional' : 'empty';
    const hd = s.headers;
    $('.set-summary', card).textContent = `${s.inputs.length} panel${s.inputs.length === 1 ? '' : 's'}` +
      (hd ? ` · ${hd.min_width}–${hd.max_width} × ${hd.min_height}–${hd.max_height} px · ${hd.depths.join(', ')}` : '');
    $('.set-error', card).textContent = s.skipped?.length ? `${s.skipped.length} unreadable or unsuitable panels left out; see Panel list.` : '';
    $('.files ul', card).replaceChildren(...s.inputs.map(x => h('li', {text: x.split(/[\\/]/).pop()})),
                                         ...(s.skipped || []).map(k => h('li', {class: 'set-error', text: `Skipped ${k.name}: ${k.reason}`})));
    // the pixel scale: from the optics, or measured against another set and confirmed (D10)
    const a = arcsec(s.optics || {}), est = s.scale_estimate, actions = $('.scale-actions', card);
    actions.replaceChildren();
    if (a) $('.scale', card).textContent = `${a.toFixed(3)} ″/px from the optics.`;
    else if (est) {
      $('.scale', card).textContent = `Measured: pixels ${est.ratio.toFixed(3)}× those of ${setName(p, est.relative_to)} (${est.pairs} panel pairs agree within ${(est.spread * 100).toFixed(1)} %). ${est.confirmed ? 'Confirmed.' : 'Confirm it, or enter the optics.'}`;
      if (!est.confirmed) actions.append(h('button', {class: 'btn small', type: 'button', onclick: () => mutate('sets', {op: 'confirm', set: {id: s.id}}).catch(e => message(e.message))}, 'Confirm this scale'));
    } else $('.scale', card).textContent = s.stream === 'luminance' && mixed ? 'Enter the optics, or let LunarMosaic measure the scale: sets of different scales need it.'
      : s.stream === 'luminance' ? 'A single set needs no optics.' : 'Optional: the optics speed up placing colour.';
    if (!a && !est && s.stream === 'luminance' && mixed) actions.append(h('button', {class: 'btn small', type: 'button', onclick: () => action('sets', {op: 'estimate'})}, 'Measure the scale'));
    const locked = busy || !!stream.mosaic || importing || (s.stream === 'luminance' && both);
    card.dataset.locked = locked ? 'true' : 'false';
    $('.drop', card).classList.toggle('locked', locked);
    $$('input,button', card).forEach(el => { el.disabled = locked; });
  }
  $('#colour-both').checked = both;
  $('#colour-both').disabled = busy || built(p, 'luminance') || built(p, 'colour') || lum(p).some(s => s.inputs.length);
  $('#add-lum-set').disabled = busy || built(p, 'luminance') || both;
}

// "The colour panels are 2.1× coarser than the luminance canvas" (plan phase 7, item 1)
function ratioSentence(p) {
  const finest = Math.min(...lum(p).filter(s => s.inputs.length).map(s => arcsec(s.optics || {}) || Infinity));
  const c = arcsec(colourSet(p).optics || {});
  if (!Number.isFinite(finest) || !c || colourSet(p).stream === 'both') return '';
  const r = c / finest;
  return r > 1.01 ? `The colour panels sample ${r.toFixed(1)}× coarser than the luminance: colour is placed at its own sampling and supplies the hue only, so this costs no detail.`
    : r < 0.99 ? `The colour panels sample ${(1 / r).toFixed(1)}× finer than the luminance; colour is reduced to the canvas when placed.`
    : 'Colour and luminance share one pixel scale.';
}

async function loadEstimate() {
  const p = state.project, ticket = ++estimateVersion;
  estimateRevision = p?.revision ?? null;
  if (!p || page !== 'import' || built(p, 'luminance') || !p.sets.some(s => s.stream !== 'colour' && s.inputs.length)) { $('#canvas-card').hidden = true; return; }
  try {
    const e = await api(`canvas-estimate?id=${p.id}`);
    if (ticket !== estimateVersion) return;
    $('#canvas-card').hidden = false;
    const lines = [];
    if (e.problem) lines.push(e.problem);
    if (e.lower_bound) lines.push(`The canvas will be at least ${e.lower_bound.px.toLocaleString('en-US')} px across — the Moon at perigee at ${(e.finest.arcsec_px / e.canvas_scale).toFixed(3)} ″/px — so ${e.lower_bound.mpx.toLocaleString('en-US')} Mpx and ${e.lower_bound.gb} GB as 16-bit RGB. Panels overhang the limb, so the real canvas is larger.`);
    else if (!e.problem) lines.push('Enter the optics of the finest set to estimate the canvas size.');
    if (e.oversampled) lines.push(`The finest set's pixels are about ${e.oversampled.px_per_detail}× smaller than the finest detail it resolves: a canvas at ${e.oversampled.suggested_canvas_scale}× keeps that detail and is ${Math.round(1 / e.oversampled.suggested_canvas_scale ** 2)}× smaller.`);
    $('#canvas-estimate').textContent = lines.join(' ');
    const confirm = $('#canvas-confirm');
    confirm.replaceChildren();
    const ask = e.needs_confirmation && !buildCanvas;
    if (ask || (e.oversampled && !buildCanvas)) {
      const smaller = Math.min(1, e.oversampled ? e.oversampled.suggested_canvas_scale : Math.sqrt(e.threshold_mpx / e.lower_bound.mpx));
      if (ask) confirm.append(h('p', {class: 'warning', text: `That is above the ${e.threshold_mpx} Mpx confirmed before building.`}),
        h('button', {class: 'btn', type: 'button', onclick: () => { buildCanvas = {max_canvas_mpx: Math.ceil(e.lower_bound.mpx * 1.6)}; loadEstimate(); }}, 'Build at full size'));
      confirm.append(h('button', {class: 'btn', type: 'button', onclick: () => { buildCanvas = {canvas_scale: Math.round(smaller * 1000) / 1000}; loadEstimate(); }},
        `Use a smaller canvas (${smaller.toFixed(2)}×)`));
    }
    if (buildCanvas) confirm.append(h('p', {class: 'hint', text: buildCanvas.canvas_scale ? `The canvas will be reduced to ${buildCanvas.canvas_scale}×.` : `Confirmed: up to ${buildCanvas.max_canvas_mpx} Mpx.`}),
      h('button', {class: 'btn small', type: 'button', onclick: () => { buildCanvas = null; loadEstimate(); }}, 'Undo'));
    confirm.hidden = !confirm.children.length;
    $('#build-both').dataset.blocked = $('#build-lum').dataset.blocked = ask ? 'yes' : '';
    render();
  } catch (err) { if (ticket === estimateVersion) $('#canvas-estimate').textContent = err.message; }
}

async function importFiles(key, files) {
  if (!state.project) { message('Create or open a project first.'); return; }
  const supported = files.filter(f => /\.(tiff?|png|jpe?g)$/i.test(f.name));
  if (!supported.length) { message('Choose TIFF, PNG or JPEG panels. FITS is not supported yet.'); return; }
  if (supported.length !== files.length) message(`${files.length - supported.length} non-image files skipped.`);
  importing = true; render(); let count = 0;
  try {
    for (const file of supported) {
      $('#saved').textContent = `Importing ${++count}/${supported.length}…`;
      const q = new URLSearchParams({id: state.project.id, set: key, name: file.name});
      const r = await fetch('/api/upload?' + q, {method: 'POST', body: file});
      const o = await r.json().catch(() => ({})); if (!r.ok) throw new Error(o.error || 'The upload failed.'); state = o;
    }
    const card = setCards.get(key), s = state.project.sets.find(x => x.id === key);
    state = await api('inputs', payload({set: key, paths: s.inputs, optics: opticsOf(card)}));
    message(`${count} panels imported. A copy is kept inside the project.`);
  } catch (e) { message(e.message); }
  finally { importing = false; render(); loadEstimate(); }
}

// ---------------------------------------------------------------- build

function stageList(names, doneUpTo, running) {
  return h('ol', {}, ...names.map((text, i) => h('li', {class: i < doneUpTo ? 'done' : running === i ? 'running' : ''},
    h('span', {text: i < doneUpTo ? '✓' : String(i + 1).padStart(2, '0')}), text)));
}

function renderBuild(p, job, busy) {
  const ls = p.luminance, cs = p.colour, active = busy ? job.set : null, both = colourSet(p).stream === 'both';
  const lPanels = p.sets.filter(s => s.stream !== 'colour').reduce((n, s) => n + s.inputs.length, 0);
  const lSets = p.sets.filter(s => s.stream !== 'colour' && s.inputs.length).length;
  const lCard = h('article', {class: 'card build-card'}, h('h2', {text: 'Luminance'}),
    h('p', {class: 'hint', text: ls.mosaic ? `${ls.width.toLocaleString('en-US')} × ${ls.height.toLocaleString('en-US')} px · saved` : `${lPanels} panels from ${lSets} set${lSets === 1 ? '' : 's'} · ${ls.state}`}));
  const facts = ls.build_facts;
  if (facts && ls.mosaic) lCard.append(h('p', {class: 'hint', text: `${facts.placed} panels placed · ${facts.contributing} contribute${facts.rms_px !== undefined ? ` · ${facts.rms_px.toFixed(2)} px RMS` : ''}`}));
  if (facts?.not_placed?.length && ls.mosaic) lCard.append(h('details', {class: 'files'}, h('summary', {text: `${facts.not_placed.length} panel${facts.not_placed.length === 1 ? '' : 's'} left out`}),
    h('ul', {}, ...facts.not_placed.map(x => h('li', {text: `${x.id}: ${x.reason}`})))));
  const lStage = active === 'L' ? job.stage || 0 : -1;
  lCard.append(stageList(lPanels === 1 ? ['Check panel', 'Keep the native 16-bit TIFF'] : BUILDER, ls.mosaic ? 99 : Math.max(0, lStage - 1), lStage >= 1 ? lStage - 1 : null));
  if (ls.error) lCard.append(h('p', {class: 'set-error', text: ls.error}));
  if (!ls.mosaic && lPanels) lCard.append(h('button', {class: 'btn', type: 'button', disabled: busy, onclick: () => build(['L'])}, ['failed', 'interrupted'].includes(ls.state) ? 'Restart luminance' : 'Build luminance'));
  const cPanels = colourSet(p).inputs.length, placing = active === 'RGB';
  const cCard = h('article', {class: 'card build-card'}, h('h2', {text: 'Colour'}),
    h('p', {class: 'hint', text: both ? 'The colour set is all the data: its mosaic is the colour too.'
      : cs.mosaic ? `${cs.summary?.text || 'Placed'}${cs.summary ? ` · ${cs.summary.placed} placed, ${cs.summary.rejected} left out` : ''}`
      : cPanels ? `${cPanels} panels · placed one by one onto the luminance` : 'No colour panels: the mosaic stays grey.'}));
  // colour_place reports [i/n] per panel; its steps are shown in turn while it runs
  const step = placing ? Math.min(PLACING.length - 1, Math.floor((job.progress || 0) * PLACING.length)) : null;
  if (!both) cCard.append(stageList(PLACING, cs.mosaic ? 99 : placing ? step : 0, placing ? step : null));
  if (placing && job.title) cCard.append(h('p', {class: 'hint', text: job.title}));
  if (cs.error) cCard.append(h('p', {class: 'set-error', text: cs.error}));
  if (!cs.mosaic && cPanels && !both) cCard.append(h('button', {class: 'btn', type: 'button', disabled: busy || !ls.mosaic, onclick: () => build(['RGB'])},
    ls.mosaic ? (cs.state === 'failed' ? 'Place colour again' : 'Place colour') : 'Build luminance first'));
  $('#build-cards').replaceChildren(lCard, cCard);
  const recovery = ['luminance', 'colour'].filter(n => ['interrupted', 'failed', 'missing'].includes(p[n].state));
  $('#recovery').hidden = !recovery.length;
  $('#recovery-summary').textContent = recovery.map(n => `${n === 'luminance' ? 'Luminance' : 'Colour'}: ${p[n].state}${p[n].last_stage ? ` during stage ${p[n].last_stage}` : ''}.`).join(' ') + ' Completed mosaics are kept.';
  $('#job-title').textContent = job.state === 'idle' ? 'Ready to build'
    : job.state === 'cancelled' ? `${job.action || 'Operation'} cancelled${job.stage ? ` · stopped during stage ${job.stage} of 9` : ''}.`
    : job.title || job.action;
  $('#job-progress').value = job.progress || 0;
  $('#job-progress').classList.toggle('cancelled', job.state === 'cancelled');
  $('#job-log').textContent = (job.log || []).join('\n');
  $('#cancel-job').hidden = !busy;
  $('#job-result').textContent = job.error || job.result?.path || (job.state === 'done' && job.result && typeof job.result === 'object'
    ? Object.entries(job.result).filter(([k]) => ['L', 'RGB'].includes(k)).map(([k, v]) => `${k}: ${v}`).join(' · ') : '');
  $('#go-edit').disabled = !ls.mosaic;
}

async function build(sets) {
  if (await action('build', {sets, ...(buildCanvas ? {canvas: buildCanvas} : {})})) showPage('build');
}

// ---------------------------------------------------------------- render

function render() {
  const p = state.project, job = state.job, busy = job.state === 'running';
  if (!chainSave.busy) chainSave.onState({saving: false, failed: chainSave.failed, retrying: chainSave.retrying});
  $('#export-open').disabled = !built(p, 'luminance') || busy;
  $('#atlas-open').disabled = !built(p, 'luminance') || busy;
  if (!p) { loadedProject = null; showVisibility(); return; }
  $('#project-title').textContent = p.name; $('#project-location').textContent = p.folder;
  $('#canvas-project').textContent = p.name.toUpperCase();
  renderSets(p, busy);
  $('#import-note').textContent = ratioSentence(p) || (colourSet(p).inputs.length ? 'Colour is placed panel by panel onto the luminance after it is built.' : 'Colour is optional. You can add it after building luminance.');
  const anyLum = p.sets.some(s => s.stream !== 'colour' && s.inputs.length), blocked = $('#build-both').dataset.blocked === 'yes';
  $('#build-both').disabled = busy || importing || blocked || !anyLum || (built(p, 'luminance') && (built(p, 'colour') || !colourSet(p).inputs.length));
  $('#build-lum').disabled = busy || importing || blocked || !anyLum || built(p, 'luminance');
  $('#build-lum').hidden = !colourSet(p).inputs.length || colourSet(p).stream === 'both';
  renderBuild(p, job, busy);
  renderWorkspace(p, job, busy);
  loadedProject = p.id;
  if (page === 'import' && p.revision !== estimateRevision) loadEstimate();     // the sets or their optics changed
  showVisibility();
}

function renderWorkspace(p, job, busy) {
  const ls = p.luminance, cs = p.colour, m = p.match;
  // bugs-codex.md CODEX-04: a failed re-placement leaves the previous colour canvas and match in place
  const stale = !!cs.mosaic && (cs.state === 'failed' || cs.state === 'interrupted');
  colourStale = stale;
  $('#l-layer-state').textContent = ls.mosaic ? `${ls.width.toLocaleString('en-US')} × ${ls.height.toLocaleString('en-US')}` : 'Not built';
  $('#rgb-layer-state').textContent = (cs.mosaic ? (m?.method === 'both' ? 'From the same mosaic' : cs.summary ? `Placed · ${cs.summary.placed} panels` : 'Matched') : 'Add a colour set')
    + (stale ? ' · ⚠ the last placement attempt failed; showing the previous result' : '');
  $('#rgb-layer-state').classList.toggle('stale', stale);
  for (const [key, name] of [['L', 'luminance'], ['RGB', 'colour']]) {
    const img = $(`[data-thumbnail="${key}"]`), badge = img.parentElement, s = p[name], stamp = `${p.id}:${s.mosaic}:${s.started || 0}`;
    if (!s.mosaic) { img.hidden = true; $('span', badge).hidden = false; img.dataset.stamp = ''; }
    else if (img.dataset.stamp !== stamp) {
      img.hidden = true; $('span', badge).hidden = false; img.dataset.stamp = stamp;
      img.onload = () => { img.hidden = false; $('span', badge).hidden = true; };
      img.onerror = () => { img.hidden = true; $('span', badge).hidden = false; };
      img.src = `/api/layer-thumbnail?id=${p.id}&set=${key}&v=${s.started || 0}`;
    }
  }
  $('[data-visible="rgb_visible"]').checked = p.view.rgb_visible; $('[data-visible="l_visible"]').checked = p.view.l_visible;
  $('[data-visible="rgb_visible"]').disabled = !m; $('[data-visible="l_visible"]').disabled = !ls.mosaic;
  $('#layer-visibility-info').textContent = !p.view.l_visible && (!p.view.rgb_visible || !m) ? 'No visible layer. Show luminance, or colour once it is placed.'
    : !p.view.l_visible ? 'Colour supplies lightness and hue while luminance is hidden.' : !p.view.rgb_visible || !m ? 'Luminance only; the colour chain is kept.'
    : 'Luminance supplies the lightness, colour the hue.';
  const single = colourSet(p).inputs.length === 1 && colourSet(p).stream === 'colour';
  $('#match').disabled = busy || !ls.mosaic || !colourSet(p).inputs.length || colourSet(p).stream === 'both';
  $('#match').textContent = m ? 'Place colour again' : 'Place colour';
  $('#manual-open').disabled = busy || !ls.mosaic || !single;
  $('#manual-open').title = single ? 'Click the same features on both images' : 'Control points place a single-image colour set';
  $('#check-match').disabled = !m || busy || m.method === 'both';
  $('#north-up').disabled = !ls.mosaic || busy;
  $('#coverage').disabled = !m;
  // muse-bugs.md MUSE-06: these show nothing meaningful before colour is placed
  const COLOUR_VIEWS = new Set(['colour', 'overlay', 'coverage', 'errors']);
  $$('[data-view]').forEach(b => { b.disabled = !m && COLOUR_VIEWS.has(b.dataset.view); });
  $('#show-error-map').disabled = !m;
  const checks = m?.diagnostics;
  $('#match-chip').textContent = !m ? 'Colour not placed' : m.method === 'both' ? 'Colour from the same mosaic'
    : `${cs.summary?.text || `Match ${m.median.toFixed(2)} px · worst ${m.worst.toFixed(2)} px`}${checks?.measured ? ` · checked ${checks.median.toFixed(2)} px` : ''}`;
  $('#match-chip').classList.toggle('stale', stale);
  $('#match-description').textContent = !m ? (colourSet(p).inputs.length ? 'Colour is placed once luminance is built.' : 'Add colour panels to colour the mosaic.')
    : m.method === 'placement' ? `${m.inliers} of ${m.candidates} colour panels placed at ${m.scale.toFixed(3)}× the canvas pixel.`
    : m.method === 'both' ? 'The colour set is the luminance too.'
    : 'One global match, from before per-panel placement: place colour again to fit each panel.';
  $('#match-explanation').textContent = !m ? 'Build both streams to measure how the colour sits on the luminance.' : m.method === 'both' ? 'Nothing is placed: one mosaic gives both.'
    : `Each colour panel was found on the luminance and fitted with the simplest pose its own tiles support; every fourth tile was held out to measure the fit. ${m.warning || ''}${checks?.measured ? ` An independent check of ${checks.measured} patches measured a median offset of ${checks.median.toFixed(2)} px, worst ${checks.worst.toFixed(2)} px.` : ''}`;
  $('#coverage').value = p.geometry.coverage; $('#mirror').checked = p.geometry.mirror;
  if (document.activeElement !== $('#rotation')) $('#rotation').value = decimalText(Math.round(p.geometry.rotation * 100) / 100);
  const orient = p.orientation;
  $('#orientation-info').textContent = orient ? `${orient.matches} terrain matches · ${orient.rms_px.toFixed(2)} px RMS. ${Math.abs(p.geometry.rotation - orient.rotation) < .001 && p.geometry.mirror === orient.mirror ? 'Lunar north is up.' : 'The view was turned since.'}`
    : 'Both layers turn together. Uses lunar reference maps, downloaded if needed.';
  $('#status-job').textContent = busy ? `${job.title || job.action} · ${Math.round((job.progress || 0) * 100)} %` : job.state === 'failed' ? job.error || '' : '';
  $('#cancel-export').hidden = !busy;
  // the migration notes, shown once
  const notes = p.notes || [];
  $('#notes').hidden = !notes.length;
  if (notes.length && $('#notes').dataset.shown !== JSON.stringify(notes)) {
    $('#notes').dataset.shown = JSON.stringify(notes);
    $('#notes').replaceChildren(h('div', {class: 'eyebrow', text: 'WHAT CHANGED WHEN THIS PROJECT WAS OPENED'}),
      h('ul', {}, ...notes.map(n => h('li', {text: n}))), h('button', {class: 'btn small', type: 'button', onclick: () => mutate('notes').catch(e => message(e.message))}, 'Got it'));
  }
  // the editor shows our working chain while a save is pending, the server's otherwise; never while typing in it
  chainSave.adopt({format: 'lunarmosaic-chain', version: 1, ...p.chain});
  const doc = chainSave.working, key = JSON.stringify(doc) + ':' + analysisVersion + ':' + !!m, active = document.activeElement;
  const typing = $('#chains').contains(active) && /INPUT|SELECT|TEXTAREA/.test(active.tagName);
  if (editor && key !== shownChain && !typing) { shownChain = key; editor.show(doc, analysis, {hasColour: !!m}); }
  // what a preview's pixels depend on: not the revision, which a rename or a build note also moves
  if (previews && ls.mosaic) previews.render(hash(JSON.stringify([p.chain.protect_space, p.chain.chains, p.geometry,
    ls.mosaic, ls.started || 0, cs.mosaic, cs.revision || 0])));
}

// ---------------------------------------------------------------- the canvas

function scheduleRefresh() {
  // bug 19: a preview rectangle covers the canvas while it's open, so recomputing what's hidden behind it can wait
  if (previews?.hasOpen) { mainStale = true; return; }
  clearTimeout(refreshTimer); refreshTimer = setTimeout(() => refreshPreview(false), 150);
}

function loadAnalysis() {
  if (page !== 'edit' || !built(state.project, 'luminance')) return;
  clearTimeout(analysisTimer);
  analysisTimer = setTimeout(async () => {
    const ticket = ++analysisTicket;
    try {
      const a = await api(`analysis?id=${state.project.id}`);
      if (ticket !== analysisTicket) return;           // a newer chain's readouts were asked for meanwhile
      analysis = a.blocks; analysisVersion++; render();
    } catch (e) { /* readouts wait for the statistics; a failure shows in the canvas */ }
  }, 250);
}

function legend() {
  const sets = geometry?.sets || [];
  $('#legend').hidden = view !== 'resolution';
  $('#legend-list').replaceChildren(...(sets.length ? sets.map(s => h('li', {},
    h('span', {class: 'swatch', style: `background:rgb(${s.colour.map(c => Math.round(c * 255)).join(',')})`}),
    h('span', {}, h('strong', {text: s.label}), h('small', {text: [s.tier ? `tier ${s.tier}` : null, s.scale_rel ? `pixels ${s.scale_rel.toFixed(2)}× the canvas's` : null,
      s.arcsec_px ? `${s.arcsec_px.toFixed(3)} ″/px` : null, s.detail_px ? `detail ${s.detail_px.toFixed(1)} px` : null,
      typeof s.disk_share === 'number' ? `${(s.disk_share * 100).toFixed(0)} % of the disk` : null].filter(Boolean).join(' · ')}))))
    : [h('li', {class: 'hint', text: 'This mosaic has no set report: it was built from one set.'})]));
}

// This is deliberately calculated from the same rendered 8-bit previews shown on screen.  It is a live
// exposure/colour diagnostic, not a claim about the 16-bit source distribution used by export.
class Histogram {
  constructor(root) {
    this.root = root; this.stamp = ''; this.tickets = new Map();
    root.addEventListener('toggle', () => { if (root.open) this.render(this.stamp); });
  }
  render(stamp) {
    this.stamp = stamp;
    if (!this.root.open || !state.project) return;
    const colour = !!state.project.match;
    $('#histogram-colour-row').hidden = !colour;
    this.load('luminance', 'luminance', stamp);
    if (colour) this.load('colour', 'colour', stamp);
  }
  async load(name, viewName, stamp) {
    const ticket = (this.tickets.get(name) || 0) + 1, stats = $(`#histogram-${name}-stats`);
    this.tickets.set(name, ticket);
    stats.textContent = 'Updating…';
    try {
      const q = new URLSearchParams({id: state.project.id, view: viewName, v: state.project.revision});
      const response = await fetch('/api/preview?' + q);
      if (!response.ok) throw new Error('Histogram preview unavailable.');
      const bitmap = await createImageBitmap(await response.blob());
      if (ticket !== this.tickets.get(name) || !this.root.open || stamp !== this.stamp) { bitmap.close(); return; }
      const max = 512, ratio = Math.min(1, max / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement('canvas'); canvas.width = Math.max(1, Math.round(bitmap.width * ratio)); canvas.height = Math.max(1, Math.round(bitmap.height * ratio));
      const context = canvas.getContext('2d', {willReadFrequently: true}); context.drawImage(bitmap, 0, 0, canvas.width, canvas.height); bitmap.close();
      const bins = [new Uint32Array(256), new Uint32Array(256), new Uint32Array(256)], data = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let pixels = 0;
      for (let i = 0; i < data.length; i += 4) { if (!data[i + 3]) continue; bins[0][data[i]]++; bins[1][data[i + 1]]++; bins[2][data[i + 2]]++; pixels++; }
      this.draw($(`#histogram-${name}`), bins, name === 'luminance');
      const percentile = p => { let total = 0; for (let i = 0; i < 256; i++) total += bins[0][i]; const target = total * p; let seen = 0; for (let i = 0; i < 256; i++) { seen += bins[0][i]; if (seen >= target) return i; } return 255; };
      stats.textContent = pixels ? `black ${percentile(.01)} · mid ${percentile(.5)} · white ${percentile(.99)}` : 'No visible pixels';
    } catch (_) { if (ticket === this.tickets.get(name)) stats.textContent = 'Preview unavailable'; }
  }
  draw(canvas, bins, luminance) {
    const context = canvas.getContext('2d'), {width, height} = canvas; context.clearRect(0, 0, width, height);
    const peak = Math.max(1, ...bins.flatMap(bin => [...bin]));
    const colours = luminance ? ['#d9dce4'] : ['#ee6d6d', '#73d68a', '#6ba9ff'];
    bins.forEach((bin, channel) => { context.beginPath(); context.strokeStyle = colours[channel] || colours[0]; context.lineWidth = 1.5; for (let x = 0; x < 256; x++) { const px = x / 255 * (width - 1), y = height - 2 - Math.log1p(bin[x]) / Math.log1p(peak) * (height - 5); x ? context.lineTo(px, y) : context.moveTo(px, y); } context.stroke(); });
  }
}

async function refreshPreview(reset = true) {
  if (page !== 'edit' || !built(state.project, 'luminance')) return;
  mainStale = false;
  const ticket = ++previewVersion, id = state.project.id;
  const render = trackRender('Canvas');
  try {
    const info = await api('geometry?id=' + encodeURIComponent(id)); if (ticket !== previewVersion) return;
    geometry = info;
    legend();
    $('#status-dimensions').textContent = `${info.width.toLocaleString('en-US')} × ${info.height.toLocaleString('en-US')} · ${view}`;
    $('#coverage-info').textContent = info.matched ? `${info.missing_colour.toFixed(1)} % of the luminance has no colour.` : 'No colour placed.';
    const response = await fetch(`/api/preview?id=${id}&view=${view}&v=${state.project.revision}&rid=${encodeURIComponent(render.rid)}`);
    if (!response.ok) { const e = await response.json().catch(() => ({})); throw new Error(e.error || 'The preview failed.'); }
    const url = URL.createObjectURL(await response.blob()); if (ticket !== previewVersion) { URL.revokeObjectURL(url); return; }
    const im = $('#mosaic'), old = im.dataset.url;
    im.onload = () => { if (old) URL.revokeObjectURL(old); im.dataset.url = url; $('#empty-canvas').hidden = true; if (reset || fitted) fit(); else positionImage(); };
    im.src = url;
    histogram?.render(`${state.project.revision}:${view}`);
  } catch (e) { message(e.message); }
  finally { render.finish(); }
}

function fit() {
  if (!geometry) return;
  const stage = $('#stage'), right = stage.clientWidth > 900 ? 420 : 300, bottom = previews?.list().length ? 210 : 120;
  scale = Math.max(.001, Math.min((stage.clientWidth - right - 90) / geometry.width, (stage.clientHeight - bottom - 130) / geometry.height));
  offset = {x: Math.max(70, (stage.clientWidth - right - geometry.width * scale) / 2), y: Math.max(110, (stage.clientHeight - bottom - geometry.height * scale) / 2)};
  fitted = true; positionImage();
}

function positionImage() {
  if (!geometry) return;
  const im = $('#mosaic');
  im.style.width = geometry.width + 'px'; im.style.height = geometry.height + 'px';
  im.style.transform = `translate(${offset.x}px,${offset.y}px) scale(${scale})`;
  // below 75 % the canvas runs every block at a reduced scale (plan §4.5): judge detail in a preview at 1:1
  $('#zoom-label').textContent = `${Math.round(scale * 100)}%${scale < .75 ? ' · approximate' : ''}`;
  $('#zoom-label').title = scale < .75 ? 'Approximate below 75 %: draw a preview to judge detail at 1:1.' : '';
  $('#detail').hidden = true; clearTimeout(detailTimer); detailTimer = setTimeout(loadDetail, 180);
  previews?.place();
}

async function loadDetail() {
  // bug 36: pan and zoom always deserve detail; only a chain change deferred behind an open viewer (bug 19) waits,
  // and closing the last viewer refreshes the canvas, which reloads this
  if (page !== 'edit' || !geometry || mainStale) return;
  const stage = $('#stage'), ticket = previewVersion, position = `${scale},${offset.x},${offset.y}`;
  const q = new URLSearchParams({id: state.project.id, view, x: -offset.x, y: -offset.y, w: stage.clientWidth, h: stage.clientHeight, scale});
  const render = trackRender('Detail'); q.set('rid', render.rid);
  try {
    const r = await fetch('/api/viewport?' + q); if (!r.ok) return;
    const url = URL.createObjectURL(await r.blob());
    if (ticket !== previewVersion || position !== `${scale},${offset.x},${offset.y}`) { URL.revokeObjectURL(url); return; }
    const im = $('#detail'), old = im.dataset.url;
    im.onload = () => { if (old) URL.revokeObjectURL(old); im.dataset.url = url; im.hidden = false; };
    im.src = url;
  } catch (e) { /* keep the preview while disconnected */ }
  finally { render.finish(); }
}

function zoom(factor, x, y) {
  if (!geometry) return;
  const stage = $('#stage'); x ??= stage.clientWidth / 2; y ??= stage.clientHeight / 2;
  const next = Math.max(.005, Math.min(8, scale * factor));
  offset.x = x - (x - offset.x) * next / scale; offset.y = y - (y - offset.y) * next / scale;
  scale = next; fitted = false; positionImage();
}

// view px of the full oriented view -> canvas px, for the rectangles a block measures (white balance references)
function toCanvas([x, y, w, hh]) {
  const M = geometry.full.matrix, det = M[0][0] * M[1][1] - M[0][1] * M[1][0];
  const inv = ([u, v]) => { const a = u - M[0][2], b = v - M[1][2]; return [(M[1][1] * a - M[0][1] * b) / det, (-M[1][0] * a + M[0][0] * b) / det]; };
  const c = [[x, y], [x + w, y], [x, y + hh], [x + w, y + hh]].map(inv);
  const xs = c.map(q => q[0]), ys = c.map(q => q[1]);
  const x0 = Math.max(0, Math.min(...xs)), y0 = Math.max(0, Math.min(...ys));
  return [Math.round(x0), Math.round(y0), Math.round(Math.max(...xs) - x0), Math.round(Math.max(...ys) - y0)];
}

async function poll() {
  if (polling || importing) return;
  polling = true;
  try {
    const old = state, next = await api('state');
    if (importing) return;
    // a reply older than one this page already has (a save that landed meanwhile) is dropped
    if (state.project && next.project?.id === state.project.id && next.project.revision < state.project.revision) return;
    state = next; render();
    const was = old.project, now = state.project;
    if (page === 'edit' && now && (was?.id !== now.id || was?.revision !== now.revision)) { scheduleRefresh(); loadAnalysis(); }
    if (old.job.state === 'running' && state.job.state !== 'running') {
      if (state.job.error) message(state.job.error);
      // bugs.md bug 33: the export used to only report failure; a finished one now says where the file went
      else if (state.job.state === 'done' && state.job.action === 'Export image' && state.job.result?.path) message(`Exported to ${state.job.result.path}.`);
      if (page === 'edit') refreshPreview(false);
      if (page === 'import') loadEstimate();
    }
  } catch (e) { $('#saved').textContent = 'Connection lost · reconnecting…'; }
  finally { polling = false; }
}

// ---------------------------------------------------------------- the match popover

async function openPlacement() {
  const pop = $('#match-popover');
  pop.hidden = !pop.hidden;
  $('#match-chip').setAttribute('aria-expanded', String(!pop.hidden));
  if (pop.hidden) return;
  const table = $('#placement-table');
  table.replaceChildren();
  if (!state.project.match || state.project.match.method !== 'placement') return;
  try {
    const r = await api(`placement?id=${state.project.id}`);
    if (!r.panels.length) return;
    table.append(h('caption', {text: `${r.summary.placed} placed, ${r.summary.rejected} left out · scale ${r.k.toFixed(3)}× (${r.scale_source}).${r.photometry?.notes?.length ? ' ' + r.photometry.notes.join(' ') : ''}`}),
      h('thead', {}, h('tr', {}, ...['Panel', 'Placed', 'Pose', 'Tiles', 'Held out', 'Red / blue shift'].map(t => h('th', {scope: 'col', text: t})))),
      h('tbody', {}, ...r.panels.map(x => h('tr', {class: x.state === 'placed' ? '' : 'rejected'},
        h('th', {scope: 'row', text: x.name}), h('td', {text: x.state === 'placed' ? 'yes' : `no: ${x.reason}`}),
        h('td', {text: x.model || '—'}), h('td', {text: x.tiles ?? '—'}),
        h('td', {text: typeof x.held_out_rms === 'number' ? `${x.held_out_rms.toFixed(2)} px` : '—'}),
        h('td', {class: 'why', text: `R ${x.rgb_shift.r}; B ${x.rgb_shift.b}`})))));
  } catch (e) { message(e.message); }
}

// ---------------------------------------------------------------- wiring

function wire() {
  $$('[data-page]').forEach(b => { b.onclick = () => showPage(b.dataset.page); });
  $('#home').onclick = () => showPage('projects');
  $('#close-project').onclick = async () => { if (await action('close')) showPage('import'); };
  $('#create').onclick = async () => { if (await action('new', {folder: $('#project-folder').value, name: $('#project-name').value})) showPage('import'); };
  $('#project-folder-browse').onclick = () => browse('save', $('#project-folder'), `${$('#project-name').value || 'Untitled Moon'}.lunarmosaic`);
  $('#open-project').onclick = async () => { if (await action('open', {folder: $('#open-folder').value})) showPage(landingPage()); };
  $('#open-folder-browse').onclick = () => browse('folder', $('#open-folder'));
  $('#recent-clear').onclick = async () => { if (await action('recent-clear')) loadRecent(); };
  $('#add-lum-set').onclick = () => mutate('sets', {op: 'create', set: {stream: 'luminance'}}).catch(e => message(e.message));
  $('#colour-both').onchange = () => mutate('sets', {op: 'update', set: {id: 'RGB', stream: $('#colour-both').checked ? 'both' : 'colour'}}).then(loadEstimate).catch(e => { message(e.message); render(); });
  $('#build-both').onclick = () => build(['L', 'RGB']);
  $('#build-lum').onclick = () => build(['L']);
  $('#cancel-job').onclick = () => action('cancel');
  $('#cancel-export').onclick = () => action('cancel');
  $('#go-edit').onclick = async () => { await poll(); showPage('edit'); };      // with the finished build's state
  $('#match').onclick = () => action('match');
  $('#check-match').onclick = () => action('check-match');
  $('#north-up').onclick = () => { fitted = true; action('north-up'); };
  $('#atlas-open').onclick = async () => {
    if (await action('atlas')) message('Opening LunarAtlas…');
  };
  $('#match-chip').onclick = openPlacement;
  $('#match-popover-close').onclick = () => { $('#match-popover').hidden = true; $('#match-chip').setAttribute('aria-expanded', 'false'); $('#match-chip').focus(); };
  $('#show-error-map').onclick = () => { $('[data-view="errors"]').click(); $('#match-popover-close').click(); };
  $$('[data-visible]').forEach(el => { el.onchange = () => mutate('geometry', {view: {[el.dataset.visible]: el.checked}}).then(() => refreshPreview(false)).catch(e => { message(e.message); render(); }); });
  const turn = g => { fitted = true; mutate('geometry', {geometry: g}).then(() => refreshPreview()).catch(e => { message(e.message); render(); }); };
  $('#coverage').onchange = () => turn({coverage: $('#coverage').value});
  $('#rotation').onchange = () => {
    const input = $('#rotation'), value = decimalValue(input.value);
    if (!Number.isFinite(value) || value < -180 || value > 180) { input.value = decimalText(state.project.geometry.rotation); message('Rotation must be between −180 and 180 degrees.'); return; }
    input.value = decimalText(value); turn({rotation: value});
  };
  $('#rotation').insertAdjacentElement('afterend', spinner($('#rotation'), {step: 0.1, min: -180, max: 180}));
  $('#mirror').onchange = () => turn({mirror: $('#mirror').checked});
  $$('[data-view]').forEach(b => { b.onclick = () => { view = b.dataset.view; $$('[data-view]').forEach(x => x.classList.toggle('active', x === b)); refreshPreview(false); }; });
  $('#fit').onclick = fit; $('#zoom-in').onclick = () => zoom(1.4); $('#zoom-out').onclick = () => zoom(1 / 1.4); $('#one-to-one').onclick = () => zoom(1 / scale);
  $('#preview-tool').onclick = () => previews.add().catch(e => message(e.message));
  // BUGS.md 38: one person's display preference, like a viewer's layout, so it stays in this browser
  const BG_KEY = 'lunarmosaic.canvas-background', background = value => {
    document.documentElement.style.setProperty('--canvas-bg', value); $('#canvas-bg').value = value;
  };
  try { const saved = localStorage.getItem(BG_KEY); if (/^#[0-9a-f]{6}$/i.test(saved || '')) background(saved); } catch (_) { /* storage denied */ }
  $('#canvas-bg').oninput = () => {
    background($('#canvas-bg').value);
    try { localStorage.setItem(BG_KEY, $('#canvas-bg').value); } catch (_) { /* storage denied */ }
  };
  const stage = $('#stage');
  stage.onwheel = e => { e.preventDefault(); const r = stage.getBoundingClientRect(); zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - r.left, e.clientY - r.top); };
  let drag = null;
  stage.onpointerdown = e => { if (stage.classList.contains('drawing')) return; drag = {x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y}; stage.setPointerCapture(e.pointerId); };
  stage.onpointermove = e => { if (!drag) return; offset = {x: drag.ox + e.clientX - drag.x, y: drag.oy + e.clientY - drag.y}; fitted = false; positionImage(); };
  stage.onpointerup = () => { drag = null; }; stage.onpointercancel = () => { drag = null; };
  window.onresize = () => { if (fitted) fit(); else positionImage(); };
  // export, with the chain as a file
  $('#export-path-browse').onclick = () => browse('save', $('#export-path'), $('#export-path').value.split(/[\\/]/).pop());
  $('#export-open').onclick = async () => {
    try {
      await chainSave.flush().catch(() => {}); await queue;
      const p = state.project, hasColour = !!p.match;
      // BUGS.md bug 26: stamp the default name with the panels' own capture time, the way LunarAtlas reads it
      const STAMP = /\d{4}-\d{2}-\d{2}-\d{4}_\d/;
      const stamps = p.sets.filter(x => x.stream !== 'colour').flatMap(x => x.inputs)
        .map(x => (x.split(/[\\/]/).pop().match(STAMP) || [])[0]).filter(Boolean).sort();
      const base = stamps.length ? `${stamps[Math.floor(stamps.length / 2)]}-Moon` : 'Moon';
      $('#export-path').value = p.folder.replace(/[/\\]$/, '') + `/exports/${base}${hasColour ? '-LRGB' : ''}.` + $('#format').value;
      // bug 25: a luminance-only project has no colour coverage or layer choice to make
      $('#export-title').textContent = hasColour ? 'Export from both layers' : 'Export the luminance';
      $('#export-coverage-row').hidden = !hasColour;
      $('#export-layer-row').hidden = !hasColour;
      $('#export-coverage').value = p.geometry.coverage;
      $('#export-layer').value = hasColour ? $('#export-layer').value : 'luminance';
      $('#export-layer option[value=colour]').disabled = !hasColour;
      // bugs-codex.md CODEX-04: exporting the previous colour after a failed re-placement needs an explicit yes
      $('#export-stale-row').hidden = !colourStale;
      $('#export-stale-ack').checked = false;
      $('#export-replace-row').hidden = true;
      $('#export-dialog').showModal(); exportPreview();
    } catch (e) { message(e.message); }
  };
  $('#format').onchange = () => { $('#export-path').value = $('#export-path').value.replace(/\.[^./\\]+$/, '.' + $('#format').value); exportPreview(); };
  for (const sel of ['#export-scale', '#export-layer', '#export-coverage', '#export-canvas', '#export-position', '#export-pad-left', '#export-pad-right', '#export-pad-top', '#export-pad-bottom']) $(sel).onchange = exportPreview;
  $('#export-stale-ack').onchange = () => exportPreview();
  $('#export-run').onclick = async () => {
    if (!$('#export-stale-row').hidden && !$('#export-stale-ack').checked) { message('Acknowledge the stale colour placement first, or cancel the export.'); return; }
    // bugs.md bug 33: an export used to jump to the Build page; its progress now stays in the workspace it was asked from
    const fromPage = page;
    // an existing file is replaced only after a yes in the dialog itself: the server refuses first, with
    // `exists`, and the row below asks (a native confirm() would stack behind this dialog, CODEX-02)
    const overwrite = !$('#export-replace-row').hidden && $('#export-replace').checked;
    if (!$('#export-replace-row').hidden && !overwrite) { message('Tick “replace it”, or choose another file name.'); return; }
    try {
      await chainSave.flush().catch(() => {}); await queue;
      state = await api('export', payload({output: $('#export-path').value, overwrite, factor: Number($('#export-scale').value), layer: $('#export-layer').value, coverage: $('#export-coverage').value, canvas: $('#export-canvas').value, position: $('#export-position').value, padding: exportPadding()}));
      render();
    } catch (e) {
      if (e.exists) { $('#export-replace-text').textContent = `${e.message} Replace it.`; $('#export-replace').checked = false; $('#export-replace-row').hidden = false; }
      else message(e.message);
      return;
    }
    $('#export-dialog').close();
    if (fromPage !== 'edit') showPage('build');
  };
  // a different name needs its own answer
  $('#export-path').addEventListener('input', () => { $('#export-replace-row').hidden = true; });
  $('#export-path').addEventListener('change', () => { $('#export-replace-row').hidden = true; });
  $('#export-dialog form').onsubmit = e => { e.preventDefault(); };
  $$('[data-export-close]').forEach(b => { b.onclick = () => $('#export-dialog').close(); });
  $('#chain-export').onclick = async () => {
    try {
      await chainSave.flush().catch(() => {}); await queue;
      const doc = await api(`chain-file?id=${state.project.id}`);
      const url = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2)], {type: 'application/json'}));
      const a = h('a', {href: url, download: `${(state.project.name || 'moon').replace(/[\\/:*?"<>|]+/g, '-')}.chain.json`});
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) { message(e.message); }
  };
  $('#chain-import').onchange = async e => {
    const file = e.target.files[0]; e.target.value = '';
    if (!file) return;
    try {
      const doc = JSON.parse(await file.text());
      const blockCount = d => ['luminance', 'colour', 'final'].reduce((n, k) => n + (d?.chains?.[k]?.length || 0), 0);
      const now = blockCount(chainSave.working), next = blockCount(doc);
      const previous = JSON.parse(JSON.stringify(chainSave.working));
      await chainSave.flush().catch(() => {});
      await mutate('chain-file', {chain: doc});
      shownChain = ''; render(); scheduleRefresh(); loadAnalysis(); exportPreview();
      // bugs-codex.md CODEX-02: applied at once (nothing blocks on a dialog), but named and reversible right
      // after, the same as a deleted block or preview — not a native confirm(), which a dialog already open
      // (the export dialog, here) would stack behind, unseen, and which blocks the whole page meanwhile
      undoToast(`Chain loaded: ${now} block${now === 1 ? '' : 's'} replaced with ${next} from the file.`,
        () => mutate('chain', {chain: previous}).then(() => {
          shownChain = ''; render(); scheduleRefresh(); loadAnalysis(); exportPreview();
        }).catch(err => message(err.message)));
    } catch (err) { message(err instanceof SyntaxError ? 'This file is not a chain document (not JSON).' : err.message); }
  };
  // control points for a single-image colour set: luminance points in mosaic px, colour points in the image's px
  $('#manual-open').onclick = () => { clearPoints(); $('#manual-l').src = `/api/native-preview?id=${state.project.id}&set=L`; $('#manual-rgb').src = `/api/native-preview?id=${state.project.id}&set=RGB`; $('#manual-dialog').showModal(); };
  $$('[data-points]').forEach(box => { box.onclick = e => {
    const key = box.dataset.points, expected = points.L.length === points.RGB.length ? 'L' : 'RGB';
    if (key !== expected) { message(`Choose the corresponding point in ${expected === 'L' ? 'luminance' : 'colour'} first.`); return; }
    const r = $('img', box).getBoundingClientRect(), u = (e.clientX - r.left) / r.width, v = (e.clientY - r.top) / r.height;
    if (u < 0 || u > 1 || v < 0 || v > 1) return;
    const p = state.project, hd = colourSet(p).headers;
    const [w, hh] = key === 'L' ? [p.luminance.width, p.luminance.height] : [hd.max_width, hd.max_height];
    points[key].push([u * w, v * hh]);
    box.append(h('span', {class: 'point-dot', style: `left:${u * 100}%;top:${v * 100}%`, text: points[key].length}));
    $('#point-count').textContent = key === 'L' ? `Choose point ${points.L.length} in colour.` : `${points.L.length} pairs chosen. Add another pair or use these points.`;
  }; });
  $('#clear-points').onclick = clearPoints;
  $('#manual-run').onclick = async () => { if (await action('manual', {l_points: points.L, rgb_points: points.RGB})) { $('#manual-dialog').close(); showPage('build'); } };
  document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); chainSave.flush().then(() => message('Project saved locally.')).catch(err => message(err.message)); }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'e') { e.preventDefault(); if (!$('#export-open').disabled) $('#export-open').click(); }
    if (e.key === 'Escape') { previews?.cancelDraw(); if (!$('#match-popover').hidden) $('#match-popover-close').click(); }
    const el = document.activeElement, typing = /INPUT|SELECT|TEXTAREA/.test(el?.tagName || '') || el?.isContentEditable;
    if (!typing && !e.metaKey && !e.ctrlKey && !e.altKey && e.key.toLowerCase() === 'p' && page === 'edit' && !document.querySelector('dialog[open]')) {
      e.preventDefault(); previews.add().catch(err => message(err.message));
    }
  });
  window.addEventListener('beforeunload', e => { if (chainSave.busy || importing) { e.preventDefault(); e.returnValue = ''; } });
}

function clearPoints() { points.L = []; points.RGB = []; $$('.point-dot').forEach(el => el.remove()); $('#point-count').textContent = 'Choose point 1 in luminance.'; }

function exportPadding() {
  return Object.fromEntries(['left', 'right', 'top', 'bottom'].map(side => [side, $(`#export-pad-${side}`).value]));
}

async function exportPreview() {
  if (!$('#export-dialog').open) return;
  const ticket = ++exportVersion; $('#export-run').disabled = true; $('#export-summary').textContent = 'Preparing preview…'; $('#export-preview').hidden = true;
  const padding = exportPadding();
  const q = new URLSearchParams({id: state.project.id, coverage: $('#export-coverage').value, factor: $('#export-scale').value, layer: $('#export-layer').value, canvas: $('#export-canvas').value, position: $('#export-position').value, pad_left: padding.left, pad_right: padding.right, pad_top: padding.top, pad_bottom: padding.bottom});
  try {
    const info = await api('export-preview?' + q); if (ticket !== exportVersion) return;
    $('#export-preview').src = info.preview; $('#export-preview').hidden = false;
    const format = $('#format').value;
    const jpegOK = format !== 'jpg' || (info.width * info.height <= info.jpeg_max_pixels && Math.max(info.width, info.height) <= info.jpeg_max_side);
    const north = state.project.orientation && Math.abs(state.project.geometry.rotation - state.project.orientation.rotation) < .001 && state.project.geometry.mirror === state.project.orientation.mirror;
    $('#export-summary').textContent = `${info.width.toLocaleString('en-US')} × ${info.height.toLocaleString('en-US')} px · ${info.composition} · ${format === 'jpg' ? '8' : '16'}-bit · ${north ? 'lunar north up' : 'current editable orientation'} · ${info.missing_colour.toFixed(1)} % of the luminance without colour. ${format === 'tif' ? `About ${fileSize(info.raw_bytes)} uncompressed.` : 'The compressed size depends on the image.'} ${info.moon_placed === 'mosaic' ? 'No clear lunar limb was found, so the mosaic itself is placed, not the whole disk. ' : ''}The preview approximates sharpening; judge detail in a preview at 1:1.${jpegOK ? '' : ' Too large for JPEG: choose TIFF or PNG, or a smaller size.'}`;
    const staleBlocked = !$('#export-stale-row').hidden && !$('#export-stale-ack').checked;
    $('#export-run').disabled = !jpegOK || staleBlocked;
  } catch (e) { if (ticket === exportVersion) $('#export-summary').textContent = e.message; }
}

async function start() {
  wire();
  try {
    const [first, catalog] = await Promise.all([api('state'), api('blocks')]);
    state = first;
    editor = new ChainEditor({root: $('#chains'), catalog: catalog.blocks,
      onChange: doc => { chainSave.change(doc); },
      onNotice: (text, undo) => undoToast(text, undo),
      onBrowse: (kind, input) => browse(kind, input),
      onDraw: async (which, done) => {
        const rect = await previews.draw(`Drag the ${which} reference on the mosaic · Esc cancels`);
        if (rect) done(toCanvas(rect));
        else { shownChain = ''; render(); }               // the choice goes back to what it was
      }});
    previews = new Previews({stage: $('#stage'), layer: $('#overlay'), strip: $('#preview-strip'), viewerHost: $('#preview-viewers'),
      api: (route, body) => mutate(route, body), project: () => state.project, onError: e => message(e.message),
      onNotice: (text, undo) => undoToast(text, undo),
      onRenderStart: label => trackRender(label),
      onClose: () => { if (!previews?.hasOpen && mainStale) { mainStale = false; scheduleRefresh(); } },
      view: () => ({scale, offset, box: geometry?.box || [0, 0, 0, 0]})});
    histogram = new Histogram($('#histogram-inspector'));
    render(); showPage(landingPage());
  } catch (e) { message(e.message); }
  setInterval(poll, 1200);
}

start();
