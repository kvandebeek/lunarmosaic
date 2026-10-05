// The chain editor (plan phase 7, item 4): the luminance and colour chains, the merge, the final chain.
//
// Blocks show their parameters in the owner's units (Appendix A, served by /api/blocks), with readouts from
// /api/analysis. Blocks can be added (those a path cannot take are greyed, with the reason), reordered by dragging
// or with Alt+↑/↓, switched off, duplicated and deleted; every block has a limb fade. Each change hands the whole
// new chain document to onChange, which saves it.
const PATHS = [['luminance', 'Luminance chain', 'on the luminance, before the merge'],
               ['colour', 'Colour chain', 'on the colour, before the merge'],
               ['final', 'Final chain', 'on the merged image']];
const GROUPS = [['tone', 'Tone'], ['detail', 'Detail'], ['colour', 'Colour']];

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) el.append(c.nodeType ? c : document.createTextNode(String(c)));
  return el;
}

export const newId = () => [...crypto.getRandomValues(new Uint8Array(4))].map(b => b.toString(16).padStart(2, '0')).join('');
const clone = o => JSON.parse(JSON.stringify(o));
const fmt = (v, d = 2) => (typeof v === 'number' ? v.toFixed(d) : '—');
const signed = v => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(2);
const gain = b => (b >= 0 ? 1 + 10 * b : 1 / (1 + 10 * -b));

// one line under a block's name, so a collapsed chain still reads as a recipe
export function summary(e) {
  const p = e.params;
  switch (e.type) {
    case 'wavelets': {
      const on = p.biases.map((b, i) => [i, b]).filter(([, b]) => b !== 0);
      return on.length ? on.map(([i, b]) => `L${i + 1} ${b.toFixed(2)}`).join(' · ') : 'all layers at 0';
    }
    case 'unsharp_mask': return `${Math.round(p.amount)} % · ${p.radius.toFixed(1)} px · ${Math.round(p.threshold)}${p.mode === 'luminosity' ? ' · luminosity' : ''}`;
    case 'levels': return `black ${Math.round(p.black)} · white ${Math.round(p.white)} · γ ${p.gamma.toFixed(2)}`;
    case 'curves': return `${p.points.length} points`;
    case 'highlights': return `${p.strength.toFixed(2)} · knee p${Math.round(p.knee_percentile)}`;
    case 'contrast': return signed(p.strength);
    case 'gaussian_blur': return `σ ${p.sigma.toFixed(1)} px`;
    case 'deconvolution': return p.psf.kind === 'file' ? `PSF image · ${p.iterations} iterations`
      : `R ${p.psf.airy_radius} · D ${p.psf.diffraction_intensity} · S ${p.psf.seeing_index} · ${p.iterations} iterations`;
    case 'white_balance': return `background ${p.background.mode} · white ${p.white.mode}${p.neutralize_background ? ' · neutralised' : ''}`;
    case 'rgb_shift': return p.mode === 'auto' ? 'automatic' : `R ${p.manual.r.map(v => v.toFixed(2)).join(', ')} · B ${p.manual.b.map(v => v.toFixed(2)).join(', ')}`;
    case 'saturation': return `×${p.factor.toFixed(2)} · ${p.passes} pass${p.passes === 1 ? '' : 'es'}${p.lunar_hue ? ' · lunar hues' : ''}${p.chroma_blur_px ? ` · smooth ${p.chroma_blur_px} px` : ''}`;
    default: return '';
  }
}

// a number with its slider (when the range is bounded) and its unit
function numberField(spec, value, set) {
  const bounded = spec.min !== undefined && spec.max !== undefined;
  const step = spec.step || (spec.kind === 'int' ? 1 : 0.01);
  const lo = spec.min !== undefined ? spec.min + (spec.min_open ? step : 0) : undefined;
  const hi = spec.max !== undefined ? spec.max - (spec.max_open ? step : 0) : undefined;
  const parse = v => { const n = Number(v); return spec.kind === 'int' ? Math.round(n) : n; };
  const input = h('input', {type: 'number', value, step, min: lo, max: hi, 'aria-label': spec.label});
  const slider = bounded ? h('input', {type: 'range', value, step, min: lo, max: hi, tabindex: -1, 'aria-hidden': 'true'}) : null;
  // while a slider moves, changes are live (saved, not re-rendered, so the drag goes on); release re-renders
  const commit = (v, live) => {
    let n = parse(v);
    if (!Number.isFinite(n)) return;
    if (lo !== undefined) n = Math.max(lo, n);
    if (hi !== undefined) n = Math.min(hi, n);
    input.value = n;
    if (slider) slider.value = n;
    set(n, live);
  };
  input.addEventListener('change', () => commit(input.value));
  if (slider) {
    slider.addEventListener('input', () => { input.value = slider.value; commit(slider.value, true); });
    slider.addEventListener('change', () => commit(slider.value));
  }
  return h('label', {class: 'param'}, h('span', {class: 'param-name'}, spec.label, spec.unit ? h('small', {text: ` ${spec.unit}`}) : null),
           h('span', {class: 'param-inputs'}, slider, input));
}

function choiceField(spec, value, set) {
  const select = h('select', {'aria-label': spec.label, onchange: () => set(select.value)},
                   ...spec.choices.map(c => h('option', {value: c, selected: c === value}, c)));
  return h('label', {class: 'param'}, h('span', {class: 'param-name'}, spec.label), select);
}

function boolField(spec, value, set, disabled, note) {
  const box = h('input', {type: 'checkbox', checked: value, disabled, onchange: () => set(box.checked)});
  return h('label', {class: 'param check'}, box, h('span', {}, spec.label, note ? h('small', {class: 'hint', text: ` — ${note}`}) : null));
}

// PixInsight-style layer rows: the bias, and in grey the gain it really applies
function waveletRows(e, read, set) {
  const p = e.params;
  const rows = p.biases.map((b, i) => {
    const out = h('span', {class: 'gain', text: `×${gain(b).toFixed(1)}`});
    const field = numberField({label: `Layer ${i + 1} · ${2 ** i} px`, kind: 'float', min: -1, max: 15, step: 0.05}, b, (v, live) => {
      out.textContent = `×${gain(v).toFixed(1)}`;
      const biases = [...p.biases]; biases[i] = v; set({biases}, live);
    });
    field.append(out);
    return field;
  });
  const layers = h('div', {class: 'layer-count'}, h('button', {class: 'btn small', type: 'button', disabled: p.biases.length >= 8,
      onclick: () => set({biases: [...p.biases, 0], denoise: [...p.denoise, ...(p.denoise.length >= p.biases.length ? [0] : [])]})}, '+ layer'),
    h('button', {class: 'btn small', type: 'button', disabled: p.biases.length <= 1,
      onclick: () => set({biases: p.biases.slice(0, -1), denoise: p.denoise.slice(0, p.biases.length - 1)})}, '− layer'));
  const denoise = p.biases.map((_, i) => numberField({label: `Grain protection, layer ${i + 1}`, kind: 'float', min: 0, max: 10, step: 0.1, unit: '× noise'},
    p.denoise[i] ?? 0, (v, live) => { const d = p.biases.map((_, j) => p.denoise[j] ?? 0); d[i] = v; set({denoise: d}, live); }));
  const r = read || {};
  const hint = r.suggested_layer ? `Detail reaches layer ${r.suggested_layer} (information to ${fmt(r.cutoff, 3)} cycles/px): pushing finer layers mostly raises grain.` : null;
  return [h('p', {class: 'hint', text: 'PixInsight bias; the grey figure is the gain it applies to that layer.'}), ...rows, layers,
          h('details', {class: 'sub'}, h('summary', {text: 'Grain protection'}), ...denoise), hint ? h('p', {class: 'hint', text: hint}) : null];
}

// Photoshop-style curve over the histogram arriving at this block
function natural(xs, ys) {
  const n = xs.length, hh = xs.slice(1).map((x, i) => x - xs[i]), M = new Array(n).fill(0);
  if (n > 2) {
    const a = [], b = [], c = [], r = [];
    for (let i = 1; i < n - 1; i++) {
      a.push(hh[i - 1]); b.push(2 * (hh[i - 1] + hh[i])); c.push(hh[i]);
      r.push(6 * ((ys[i + 1] - ys[i]) / hh[i] - (ys[i] - ys[i - 1]) / hh[i - 1]));
    }
    for (let i = 1; i < r.length; i++) { const m = a[i] / b[i - 1]; b[i] -= m * c[i - 1]; r[i] -= m * r[i - 1]; }
    const sol = new Array(r.length);
    for (let i = r.length - 1; i >= 0; i--) sol[i] = (r[i] - (i < r.length - 1 ? c[i] * sol[i + 1] : 0)) / b[i];
    sol.forEach((v, i) => { M[i + 1] = v; });
  }
  return x => {
    x = Math.min(xs[n - 1], Math.max(xs[0], x));
    let i = 0; while (i < n - 2 && x > xs[i + 1]) i++;
    const t = x - xs[i], d = hh[i];
    return ys[i] + ((ys[i + 1] - ys[i]) / d - d * (2 * M[i] + M[i + 1]) / 6) * t + M[i] / 2 * t * t + (M[i + 1] - M[i]) / (6 * d) * t ** 3;
  };
}

function curveEditor(e, read, set) {
  const S = 256, ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `-8 -8 ${S + 16} ${S + 16}`);
  svg.setAttribute('class', 'curve');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Curve: click to add a point, drag to move it, drag it off the box to remove it');
  let pts = e.params.points.map(p => [...p]);
  const el = (tag, attrs) => { const x = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) x.setAttribute(k, v); return x; };
  const hist = read && read.histogram;
  if (hist) {
    const top = Math.max(...hist.map(v => Math.log1p(v)), 1);
    svg.append(el('path', {class: 'hist', d: 'M0 256 ' + hist.map((v, i) => `L${i} ${S - Math.log1p(v) / top * S}`).join(' ') + ' L255 256 Z'}));
  }
  svg.append(el('rect', {x: 0, y: 0, width: S, height: S, class: 'frame'}));
  for (const q of [64, 128, 192]) svg.append(el('path', {class: 'grid', d: `M${q} 0V${S}M0 ${q}H${S}`}));
  const line = el('path', {class: 'line'});
  svg.append(line);
  const handles = el('g', {});
  svg.append(handles);
  const out = h('p', {class: 'hint'});
  const draw = () => {
    const f = natural(pts.map(p => p[0]), pts.map(p => p[1]));
    line.setAttribute('d', Array.from({length: S}, (_, x) => `${x ? 'L' : 'M'}${x} ${S - Math.min(255, Math.max(0, f(x)))}`).join(''));
    handles.replaceChildren(...pts.map((p, i) => el('circle', {cx: p[0], cy: S - p[1], r: 5, class: 'handle', 'data-i': i})));
    out.textContent = pts.map(p => `${Math.round(p[0])}→${Math.round(p[1])}`).join(' · ');
  };
  const at = ev => { const r = svg.getBoundingClientRect(), k = (S + 16) / r.width; return [(ev.clientX - r.left) * k - 8, S - ((ev.clientY - r.top) * k - 8)]; };
  let drag = null;
  svg.addEventListener('pointerdown', ev => {
    const [x, y] = at(ev), i = Number(ev.target.dataset?.i);
    if (Number.isInteger(i)) drag = i;
    else if (x > 0 && x < 255) {
      const j = pts.findIndex(p => p[0] > x);
      if (j > 0 && pts[j][0] - x >= 1 && x - pts[j - 1][0] >= 1 && pts.length < 16) { pts.splice(j, 0, [Math.round(x), Math.round(Math.min(255, Math.max(0, y)))]); drag = j; draw(); }
    }
    if (drag !== null) svg.setPointerCapture(ev.pointerId);
  });
  svg.addEventListener('pointermove', ev => {
    if (drag === null) return;
    const [x, y] = at(ev);
    const off = y < -24 || y > S + 24 || x < -24 || x > S + 24;
    if (off && pts.length > 2) { pts.splice(drag, 1); drag = null; draw(); return; }
    const lo = drag ? pts[drag - 1][0] + 1 : 0, hi = drag < pts.length - 1 ? pts[drag + 1][0] - 1 : 255;
    pts[drag] = [Math.round(Math.min(hi, Math.max(lo, x))), Math.round(Math.min(255, Math.max(0, y)))];
    draw();
  });
  const end = () => { if (drag !== null || pts.length !== e.params.points.length) set({points: pts.map(p => [...p])}); drag = null; };
  svg.addEventListener('pointerup', end);
  svg.addEventListener('pointercancel', end);
  draw();
  return [svg, out, h('p', {class: 'hint', text: hist ? 'The histogram is of the image arriving at this block, after every block above it.' : 'The histogram appears once the statistics are ready.'})];
}

function psfField(e, set) {
  const psf = e.params.psf;
  const kind = h('select', {'aria-label': 'PSF', onchange: () => set({psf: kind.value === 'file' ? {kind: 'file', path: psf.path || ''}
      : {kind: 'synthetic', airy_radius: 3, diffraction_intensity: 100, seeing_index: 3}})},
    h('option', {value: 'synthetic', selected: psf.kind === 'synthetic'}, 'Synthetic · LuckyStackWorker'),
    h('option', {value: 'file', selected: psf.kind === 'file'}, 'PSF image'));
  const rows = [h('label', {class: 'param'}, h('span', {class: 'param-name'}, 'PSF'), kind)];
  if (psf.kind === 'file') {
    const path = h('input', {value: psf.path || '', placeholder: '/path/to/psf.tif', onchange: () => set({psf: {kind: 'file', path: path.value}})});
    rows.push(h('label', {class: 'param'}, h('span', {class: 'param-name'}, 'PSF image (16-bit, centred)'), path));
  } else {
    for (const [k, label, lo, hi, step] of [['airy_radius', 'Airy disk radius', 1, 100, 0.5], ['diffraction_intensity', 'Diffraction intensity', 10, 200, 1], ['seeing_index', 'Seeing index', 1, 5, 0.1]])
      rows.push(numberField({label, kind: 'float', min: lo, max: hi, step, unit: k === 'airy_radius' ? 'px' : ''}, psf[k], (v, live) => set({psf: {...psf, [k]: v}}, live)));
  }
  return rows;
}

function referenceField(spec, value, set, draw) {
  const mode = h('select', {'aria-label': spec.label, onchange: () => {
    if (mode.value === 'auto') set({mode: 'auto'});
    else draw(rect => set({mode: 'rect', rect}));
  }}, h('option', {value: 'auto', selected: value.mode === 'auto'}, 'automatic'), h('option', {value: 'rect', selected: value.mode === 'rect'}, 'a rectangle I draw'));
  const where = value.mode === 'rect' ? h('small', {class: 'hint', text: ` ${value.rect.map(Math.round).join(' · ')} px`}) : null;
  const redraw = value.mode === 'rect' ? h('button', {class: 'btn small', type: 'button', onclick: () => draw(rect => set({mode: 'rect', rect}))}, 'Draw again') : null;
  return h('label', {class: 'param'}, h('span', {class: 'param-name'}, spec.label, where), mode, redraw);
}

function nudgePad(channel, value, set) {
  const step = 0.05, [dx, dy] = value;
  const move = (ax, ay) => set([Math.max(-5, Math.min(5, +(dx + ax * step).toFixed(3))), Math.max(-5, Math.min(5, +(dy + ay * step).toFixed(3)))]);
  const arrows = [[-1, -1, '↖'], [0, -1, '↑'], [1, -1, '↗'], [-1, 0, '←'], null, [1, 0, '→'], [-1, 1, '↙'], [0, 1, '↓'], [1, 1, '↘']];
  return h('div', {class: 'nudge'}, h('strong', {text: channel.toUpperCase()}),
    h('div', {class: 'pad'}, ...arrows.map(a => a ? h('button', {type: 'button', class: 'btn small', 'aria-label': `Move ${channel} ${a[2]}`, onclick: () => move(a[0], a[1])}, a[2])
      : h('button', {type: 'button', class: 'btn small', title: 'Reset', onclick: () => set([0, 0])}, '·'))),
    h('small', {class: 'hint', text: `dx ${dx.toFixed(2)} · dy ${dy.toFixed(2)} px`}));
}

const pair = v => `(${v.map(x => x.toFixed(2)).join(', ')})`;

function readoutLines(e, r) {
  const lines = [];
  if (e.type === 'white_balance') lines.push(h('p', {class: 'hint', text: 'PixInsight sometimes also rescales overall brightness; this block never does.'}));
  if (!r) return lines;
  if (r.warning) lines.push(h('p', {class: 'warning', text: r.warning}));
  if (typeof r.fine_detail === 'number') lines.push(h('p', {class: 'hint', text: `Fine detail ×${r.fine_detail.toFixed(2)} in the probe crop (real detail and grain alike; inspect a preview at 1:1).`}));
  if (e.type === 'highlights' && typeof r.affected === 'number') lines.push(h('p', {class: 'hint', text: `${r.affected.toFixed(1)} % of the disk lies above the knee.`}));
  if (e.type === 'contrast' && typeof r.m === 'number') lines.push(h('p', {class: 'hint', text: `Pivot at L ${(r.m * 100).toFixed(0)}, the disk's median brightness.`}));
  if (e.type === 'white_balance' && r.g) {
    lines.push(h('p', {class: 'hint', text: `Gains R ${fmt(r.g[0], 3)} · G ${fmt(r.g[1], 3)} · B ${fmt(r.g[2], 3)}${r.ratios ? ` (ratios ${r.ratios.map(v => v.toFixed(3)).join(' : ')})` : ''}.`}));
    if (r.note) lines.push(h('p', {class: 'hint', text: r.note}));
  }
  if (e.type === 'rgb_shift' && (r.r || r.b)) for (const c of ['r', 'b']) if (r[c]) {
    lines.push(h('p', {class: 'hint', text: `${c.toUpperCase()}: ${r[c].why}${r[c].apply && (r[c].apply[0] || r[c].apply[1]) ? ` (dx ${r[c].apply[0].toFixed(2)}, dy ${r[c].apply[1].toFixed(2)} px)` : ''}.`}));
    // every estimate behind the decision: phase correlation and optical flow on each textured crop (D16)
    if (r.estimates?.[c]?.length) lines.push(h('p', {class: 'hint estimates', text: `${c.toUpperCase()} measured, phase / flow, px: ${r.estimates[c].map(([a, b], i) => `crop ${i + 1} ${pair(a)} / ${pair(b)}`).join(' · ')}`}));
  }
  if (e.type === 'deconvolution' && r.first_dark_ring_px) lines.push(h('p', {class: 'hint', text: `First dark ring at ${r.first_dark_ring_px.map(v => v.toFixed(2)).join(' / ')} px (R / G / B): the Airy disk radius is not the ring.`}));
  return lines;
}

export class ChainEditor {
  constructor({root, catalog, onChange, onDraw = () => {}, onSelect = () => {}}) {
    Object.assign(this, {root, onChange, onDraw, onSelect});
    this.catalog = Object.fromEntries(catalog.map(b => [b.type, b]));
    this.open = new Set();      // block ids shown expanded
    this.menu = null;           // the path whose add menu is open
    this.analysis = {};
    this.doc = null;
    this.dragging = null;
    this.pressed = false;       // a pointer is down in the editor (a curve point moving): show() waits for it
    this.waiting = null;
    this.version = 0;           // counts the editor's own changes
    root.addEventListener('pointerdown', () => { this.pressed = true; }, true);
    window.addEventListener('pointerup', () => this.release(), true);
    window.addEventListener('pointercancel', () => this.release(), true);
  }

  release() {
    if (!this.pressed) return;
    this.pressed = false;
    // after the gesture's own handlers have run (a curve commits its points on pointerup); a document that came
    // before an edit of ours is older than what the editor holds, so only its readouts are kept
    if (this.waiting) setTimeout(() => {
      const w = this.waiting;
      this.waiting = null;
      if (!w || this.pressed) return;
      if (w.version === this.version) this.show(w.doc, w.analysis);
      else if (w.analysis) this.analysis = w.analysis;
    }, 0);
  }

  // A document from outside (the saved chain, new readouts): drawn again, keeping focus on the same block.
  show(doc, analysis) {
    if (this.pressed) { this.waiting = {doc, analysis, version: this.version}; return; }
    this.doc = doc;
    if (analysis) this.analysis = analysis;
    const active = document.activeElement;
    this.render(this.root.contains(active) ? active.closest('[data-id]')?.dataset.id : undefined);
  }

  commit(mutate, focus) {
    const doc = clone(this.doc);
    mutate(doc);
    this.doc = doc;
    this.version++;
    this.onChange(doc);
    this.render(focus);
  }

  // a change while a slider moves is saved without re-rendering (the drag goes on)
  edit(mutate, live) {
    if (!live) return this.commit(mutate);
    const doc = clone(this.doc);
    mutate(doc);
    this.doc = doc;
    this.version++;
    this.onChange(doc);
  }

  update(path, id, params, live) {
    const change = d => { const e = d.chains[path].find(x => x.id === id); Object.assign(e.params, clone(params)); };
    if (!live) return this.commit(change);
    const doc = clone(this.doc);
    change(doc);
    this.doc = doc;
    this.version++;
    this.onChange(doc);
    const card = this.root.querySelector(`[data-id="${id}"] .block-title small`);
    if (card) card.textContent = summary(doc.chains[path].find(x => x.id === id));
  }

  add(path, type) {
    const spec = this.catalog[type], id = newId();
    const params = Object.fromEntries(spec.params.map(p => [p.name, clone(p.default)]));
    this.open.add(id);
    this.menu = null;
    this.commit(d => d.chains[path].push({id, type, enabled: true, limb_fade_px: type === 'saturation' ? 40 : 0, params}), id);
  }

  move(path, id, delta) {
    const list = this.doc.chains[path], i = list.findIndex(x => x.id === id), j = i + delta;
    if (j < 0 || j >= list.length) return;
    this.commit(d => { const l = d.chains[path]; [l[i], l[j]] = [l[j], l[i]]; }, id);
  }

  render(focus) {
    const doc = this.doc;
    if (!doc) return;
    const sections = PATHS.map(([path, title, where]) => {
      const list = doc.chains[path];
      const cards = list.map((e, i) => this.card(path, e, i, list.length));
      const zone = h('ol', {class: 'chain-list', dataset: {path}, 'aria-label': title,
        ondragover: ev => this.dragOver(ev, path), ondrop: ev => this.drop(ev, path)}, ...cards);
      const add = h('button', {class: 'btn add-block', type: 'button', 'aria-expanded': String(this.menu === path),
        onclick: () => { this.menu = this.menu === path ? null : path; this.render(); }}, '+ Add a block');
      const parts = [h('div', {class: 'chain-head'}, h('div', {class: 'eyebrow', text: title.toUpperCase()}), h('small', {class: 'hint', text: where})),
                     list.length ? null : h('p', {class: 'hint empty-chain', text: 'No blocks: the image passes through unchanged.'}),
                     zone, add, this.menu === path ? this.addMenu(path) : null];
      return h('section', {class: 'chain', dataset: {path}}, ...parts);
    });
    const strength = doc.chains.merge?.strength ?? 1;
    const merge = h('section', {class: 'chain merge'}, h('div', {class: 'chain-head'}, h('div', {class: 'eyebrow', text: 'MERGE'}),
      h('small', {class: 'hint', text: 'lightness from the luminance, colour from the colour chain'})),
      numberField({label: 'Colour strength', kind: 'float', min: 0, max: 1, step: 0.01}, strength,
        (v, live) => this.edit(d => { d.chains.merge = {strength: v}; }, live)));
    const protect = boolField({label: 'Keep space black'}, doc.protect_space !== false,
      v => this.commit(d => { d.protect_space = v; }), false, 'sky outside the Moon is restored after every block');
    this.root.replaceChildren(sections[0], sections[1], merge, sections[2], h('div', {class: 'chain-foot'}, protect));
    if (focus) this.root.querySelector(`[data-id="${focus}"] .block-head`)?.focus();
  }

  card(path, e, i, n) {
    const spec = this.catalog[e.type];
    const opened = this.open.has(e.id);
    const refusal = spec && !spec.paths.includes(path) ? spec.refusals[path] : null;
    const toggle = h('input', {type: 'checkbox', role: 'switch', checked: e.enabled !== false, 'aria-label': `Use ${spec?.label || e.type}`,
      onchange: ev => { ev.stopPropagation(); this.commit(d => { d.chains[path][i].enabled = toggle.checked; }); }});
    const head = h('div', {class: 'block-head', tabindex: 0, role: 'button', 'aria-expanded': String(opened),
        onclick: ev => { if (ev.target.closest('.block-tools')) return; this.flip(e.id); },
        onkeydown: ev => this.key(ev, path, e, i)},
      h('span', {class: 'grip', draggable: 'true', title: 'Drag to reorder (or Alt + ↑ / ↓)', 'aria-hidden': 'true',
        ondragstart: ev => { this.dragging = {path, id: e.id}; ev.dataTransfer.effectAllowed = 'move'; ev.dataTransfer.setData('text/plain', e.id); }}, '⋮⋮'),
      h('span', {class: 'block-title'}, h('strong', {text: spec?.label || e.type}), h('small', {text: summary(e)})),
      h('span', {class: 'block-tools'},
        h('label', {class: 'layer-toggle', title: 'Use this block'}, toggle, h('span', {'aria-hidden': 'true'})),
        h('button', {class: 'icon', type: 'button', title: 'Duplicate', 'aria-label': 'Duplicate', onclick: () => {
          const copy = {...clone(e), id: newId()};
          this.open.add(copy.id);
          this.commit(d => d.chains[path].splice(i + 1, 0, copy), copy.id);
        }}, '⧉'),
        h('button', {class: 'icon', type: 'button', title: 'Delete', 'aria-label': 'Delete', onclick: () => this.commit(d => d.chains[path].splice(i, 1))}, '×')));
    const li = h('li', {class: `block${e.enabled === false ? ' off' : ''}${refusal ? ' refused' : ''}${opened ? ' open' : ''}`, dataset: {id: e.id, path}}, head);
    if (refusal) li.append(h('p', {class: 'warning', text: refusal}));
    if (opened && spec) li.append(this.body(path, e, spec));
    return li;
  }

  flip(id) {
    if (this.open.has(id)) this.open.delete(id); else this.open.add(id);
    this.onSelect(id);
    this.render(id);
  }

  key(ev, path, e, i) {
    if (ev.altKey && (ev.key === 'ArrowUp' || ev.key === 'ArrowDown')) { ev.preventDefault(); this.move(path, e.id, ev.key === 'ArrowUp' ? -1 : 1); }
    else if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); this.flip(e.id); }
  }

  body(path, e, spec) {
    const read = this.analysis[e.id];
    const set = (params, live) => this.update(path, e.id, params, live);
    const fields = [];
    const specs = Object.fromEntries(spec.params.map(p => [p.name, p]));
    if (e.type === 'wavelets') {
      fields.push(...waveletRows(e, read, set), choiceField(specs.kernel, e.params.kernel, v => set({kernel: v})));
    } else if (e.type === 'curves') {
      fields.push(...curveEditor(e, read, set));
    } else if (e.type === 'deconvolution') {
      fields.push(...psfField(e, set));
      for (const k of ['iterations', 'dering_strength', 'dering_radius', 'blend_raw']) fields.push(numberField(specs[k], e.params[k], (v, live) => set({[k]: v}, live)));
    } else if (e.type === 'white_balance') {
      fields.push(referenceField(specs.background, e.params.background, v => set({background: v}), cb => this.onDraw('background', cb)),
                  referenceField(specs.white, e.params.white, v => set({white: v}), cb => this.onDraw('white', cb)));
      for (const k of ['white_low', 'white_high', 'background_target']) fields.push(numberField(specs[k], e.params[k], (v, live) => set({[k]: v}, live)));
      fields.push(boolField(specs.neutralize_background, e.params.neutralize_background, v => set({neutralize_background: v})),
                  boolField(specs.structure_detection, false, () => {}, true, 'not available yet'));
    } else if (e.type === 'rgb_shift') {
      fields.push(choiceField(specs.mode, e.params.mode, v => set({mode: v})));
      if (e.params.mode === 'manual') fields.push(h('div', {class: 'nudges'}, ...['r', 'b'].map(c => nudgePad(c, e.params.manual[c], v => set({manual: {...e.params.manual, [c]: v}})))));
    } else {
      for (const p of spec.params) {
        const v = e.params[p.name];
        if (p.kind === 'bool') fields.push(boolField(p, v, x => set({[p.name]: x})));
        else if (p.kind === 'choice') fields.push(choiceField(p, v, x => set({[p.name]: x})));
        else if (p.kind === 'float' || p.kind === 'int') fields.push(numberField(p, v, (x, live) => set({[p.name]: x}, live)));
      }
    }
    const fade = numberField({label: e.type === 'saturation' ? 'Fade its factor near the limb' : 'Fade near the limb', kind: 'float', min: 0, max: 2000, step: 1, unit: 'px (0 = off)'},
      e.limb_fade_px || 0, (v, live) => this.edit(d => { d.chains[path].find(x => x.id === e.id).limb_fade_px = v; }, live));
    return h('div', {class: 'block-body'}, h('p', {class: 'hint', text: spec.note}), ...fields, fade, ...readoutLines(e, read));
  }

  addMenu(path) {
    const groups = GROUPS.map(([g, title]) => h('div', {class: 'menu-group'}, h('div', {class: 'eyebrow', text: title.toUpperCase()}),
      ...Object.values(this.catalog).filter(b => b.group === g).map(b => {
        const refusal = b.paths.includes(path) ? null : b.refusals[path];
        return h('button', {class: 'menu-item', type: 'button', disabled: !!refusal, title: refusal || b.note, onclick: () => this.add(path, b.type)},
          h('strong', {text: b.label}), h('small', {text: refusal || b.note}));
      })));
    return h('div', {class: 'add-menu', role: 'menu', 'aria-label': `Add to the ${path} chain`}, ...groups);
  }

  dragOver(ev, path) {
    if (!this.dragging) return;
    const spec = this.catalog[this.doc.chains[this.dragging.path].find(x => x.id === this.dragging.id)?.type];
    if (spec && !spec.paths.includes(path)) return;      // not droppable there: no drop effect
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'move';
  }

  drop(ev, path) {
    if (!this.dragging) return;
    ev.preventDefault();
    const {path: from, id} = this.dragging;
    this.dragging = null;
    const items = [...ev.currentTarget.querySelectorAll(':scope > li')];
    let at = items.findIndex(li => ev.clientY < li.getBoundingClientRect().top + li.getBoundingClientRect().height / 2);
    if (at < 0) at = items.length;
    this.commit(d => {
      const src = d.chains[from], i = src.findIndex(x => x.id === id), [e] = src.splice(i, 1);
      if (from === path && i < at) at -= 1;
      d.chains[path].splice(at, 0, e);
    }, id);
  }
}
