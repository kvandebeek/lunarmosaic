// Preview rectangles live in the oriented view and are rendered at 1:1 through /api/render. Viewer layout stays
// browser-local: position, size and zoom describe one person's screen, not the project or its pixels.
import {h} from './chains.js';

const MODES = new Set(['final', 'luminance', 'colour']);
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export class Previews {
  constructor({stage, layer, strip, viewerHost, api, project, view, onError, onNotice = () => {}, onClose = () => {}, onRenderStart = () => null}) {
    Object.assign(this, {stage, layer, strip, viewerHost, api, project, view, onError, onNotice, onClose, onRenderStart});
    this.drawing = null;
    this.open = new Map();          // preview id -> {el, image, mode, zoom, observer}
    this.images = new Map();        // "id:mode" -> latest request {stamp, pending, ready}
    this.thumbs = new Map();
    this.shown = new Map();         // "id:mode" -> object URL
    this.stamp = '';
    this.shape = '';
    this.pending = null;
    this.z = 10;
  }

  get hasOpen() { return this.open.size > 0; }
  imageKey(id, mode) { return `${id}:${mode}`; }
  layoutKey(id) { return `lunarmosaic.preview-viewer:${this.project()?.id || 'none'}:${id}`; }

  toScreen(x, y) {
    const v = this.view();
    return [v.offset.x + (x - v.box[0]) * v.scale, v.offset.y + (y - v.box[1]) * v.scale];
  }

  toView(sx, sy) {
    const v = this.view(), r = this.stage.getBoundingClientRect();
    return [(sx - r.left - v.offset.x) / v.scale + v.box[0], (sy - r.top - v.offset.y) / v.scale + v.box[1]];
  }

  draw(label) {
    this.cancelDraw();
    this.viewerHost.hidden = true;
    this.stage.classList.add('drawing');
    this.layer.dataset.hint = label || 'Drag a rectangle on the mosaic · Esc cancels';
    return new Promise(resolve => {
      this.pending = resolve;
      const down = ev => {
        if (ev.button !== 0) return;
        ev.stopImmediatePropagation(); ev.preventDefault();
        const [x0, y0] = this.toView(ev.clientX, ev.clientY);
        const box = h('div', {class: 'preview-rect drawing-rect'});
        this.layer.append(box);
        const move = e => {
          const [x1, y1] = this.toView(e.clientX, e.clientY);
          const [a, b] = this.toScreen(Math.min(x0, x1), Math.min(y0, y1));
          const v = this.view();
          Object.assign(box.style, {left: `${a}px`, top: `${b}px`, width: `${Math.abs(x1 - x0) * v.scale}px`, height: `${Math.abs(y1 - y0) * v.scale}px`});
        };
        const up = e => {
          window.removeEventListener('pointermove', move, true); window.removeEventListener('pointerup', up, true);
          box.remove();
          const [x1, y1] = this.toView(e.clientX, e.clientY);
          const x = Math.max(0, Math.round(Math.min(x0, x1))), y = Math.max(0, Math.round(Math.min(y0, y1)));
          const rect = [x, y, Math.round(Math.max(x0, x1)) - x, Math.round(Math.max(y0, y1)) - y];
          this.finish(rect[2] >= 16 && rect[3] >= 16 ? rect : null);
        };
        window.addEventListener('pointermove', move, true); window.addEventListener('pointerup', up, true);
      };
      this.drawing = down;
      this.stage.addEventListener('pointerdown', down, true);
    });
  }

  finish(rect) {
    if (this.drawing) this.stage.removeEventListener('pointerdown', this.drawing, true);
    this.drawing = null; this.stage.classList.remove('drawing'); delete this.layer.dataset.hint;
    this.viewerHost.hidden = false;
    const resolve = this.pending; this.pending = null;
    if (resolve) resolve(rect);
  }
  cancelDraw() { if (this.pending) this.finish(null); }

  async add() {
    const rect = await this.draw('Drag the preview rectangle on the mosaic · Esc cancels');
    if (!rect) return;
    const [x, y, w, hh] = rect, before = new Set(this.list().map(v => v.id));
    await this.api('previews', {op: 'add', preview: {x, y, w: Math.min(w, 4096), h: Math.min(hh, 4096)}});
    const made = this.list().find(v => !before.has(v.id));
    if (made) this.openPreview(made.id);
  }

  list() { return this.project()?.chain?.previews || []; }

  place() {
    this.layer.querySelectorAll('.preview-rect:not(.drawing-rect)').forEach(el => el.remove());
    const v = this.view();
    for (const p of this.list()) {
      const [a, b] = this.toScreen(p.x, p.y);
      this.layer.append(h('div', {class: `preview-rect${this.open.has(p.id) ? ' selected' : ''}`, dataset: {id: p.id}, title: p.name,
        style: `left:${a}px;top:${b}px;width:${p.w * v.scale}px;height:${p.h * v.scale}px`}, h('span', {text: p.id.toUpperCase()})));
    }
  }

  render(stamp) {
    this.stamp = stamp;
    const previews = this.list(), ids = new Set(previews.map(p => p.id));
    const gone = [...this.open.keys()].filter(id => !ids.has(id));
    gone.forEach(id => this.removeViewer(id));
    this.place();
    const shape = JSON.stringify([previews, [...this.open.keys()].sort()]);
    if (shape !== this.shape) {
      this.shape = shape; this.thumbs.clear();
      this.strip.replaceChildren(...previews.map(p => this.item(p)), h('button', {class: 'btn add-preview', type: 'button', title: 'Draw a preview rectangle (P)', onclick: () => this.add().catch(e => this.onError(e))}, '+ Preview'));
      for (const id of new Set([...this.images.keys(), ...this.shown.keys()].map(k => k.slice(0, k.lastIndexOf(':'))))) if (!ids.has(id)) this.forget(id);
    }
    this.strip.hidden = false;
    // Bug 19: visible previews win over invisible work. With none open, thumbnails are refreshed normally.
    for (const p of previews) {
      const viewer = this.open.get(p.id);
      if (viewer && !this.project()?.match && viewer.mode === 'colour') this.setMode(p.id, 'final');
      if (!this.hasOpen || viewer) this.load(p.id, stamp, viewer?.mode || 'final');
      if (viewer) this.title(p, viewer);
    }
    // a preview deleted from the strip takes its open viewer with it: that is a close, as far as the canvas knows
    if (gone.length) this.onClose();
  }

  item(p) {
    const img = h('img', {alt: `${p.name} at 1:1`, draggable: 'false'});
    const active = this.open.get(p.id);
    const previous = this.shown.get(this.imageKey(p.id, active?.mode || 'final')) || this.shown.get(this.imageKey(p.id, 'final'));
    if (previous) img.src = previous;
    this.thumbs.set(p.id, img);
    const name = h('span', {class: 'preview-name', text: `${p.id.toUpperCase()} · ${p.name}`, title: 'Double-click to rename'});
    name.addEventListener('dblclick', async () => {
      const next = prompt('Name this preview', p.name);
      if (next && next.trim()) await this.api('previews', {op: 'update', preview: {id: p.id, name: next.trim()}}).catch(e => this.onError(e));
    });
    return h('figure', {class: `preview-item${active ? ' selected' : ''}`, dataset: {id: p.id}},
      h('button', {class: 'preview-thumb', type: 'button', 'aria-label': `Open ${p.name} at 1:1`, onclick: () => this.openPreview(p.id)}, img),
      h('figcaption', {}, name, h('button', {class: 'icon', type: 'button', title: 'Delete preview', 'aria-label': `Delete ${p.name}`,
        onclick: () => this.api('previews', {op: 'delete', preview: {id: p.id}}).then(() =>
          this.onNotice(`'${p.name}' deleted.`, () => this.api('previews', {op: 'add', preview: {x: p.x, y: p.y, w: p.w, h: p.h, name: p.name}}).catch(e => this.onError(e))))
          .catch(e => this.onError(e))}, '×')));
  }

  makeViewer(p) {
    const template = document.querySelector('#preview-viewer-template');
    const el = template.content.firstElementChild.cloneNode(true);
    const viewer = {id: p.id, el, image: $('img', el), mode: 'final', zoom: 1, observer: null};
    el.dataset.id = p.id; this.viewerHost.append(el); this.restoreLayout(viewer); this.bringFront(viewer);
    $('.viewer-modes', el).hidden = !this.project()?.match;
    $$('[data-mode]', el).forEach(button => { button.onclick = () => this.setMode(p.id, button.dataset.mode); });
    $('[data-action="close"]', el).onclick = () => this.close(p.id);
    $('[data-action="zoom-in"]', el).onclick = () => this.setZoom(viewer, viewer.zoom * 1.4);
    $('[data-action="zoom-out"]', el).onclick = () => this.setZoom(viewer, viewer.zoom / 1.4);
    $('[data-action="zoom-reset"]', el).onclick = () => this.setZoom(viewer, 1);
    const body = $('.viewer-body', el);
    body.onwheel = e => {
      e.preventDefault(); e.stopPropagation();
      const r = body.getBoundingClientRect();
      this.setZoom(viewer, viewer.zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15), {x: e.clientX - r.left - body.clientLeft, y: e.clientY - r.top - body.clientTop});
    };
    viewer.image.addEventListener('load', () => this.setZoom(viewer, viewer.zoom));
    el.addEventListener('pointerdown', () => this.bringFront(viewer));
    this.drag(viewer);
    this.pan(viewer);
    viewer.observer = new ResizeObserver(() => { this.clamp(viewer); this.saveLayout(viewer); });
    viewer.observer.observe(el);
    return viewer;
  }

  openPreview(id) {
    const p = this.list().find(x => x.id === id);
    if (!p) return;
    let viewer = this.open.get(id);
    if (!viewer) { viewer = this.makeViewer(p); this.open.set(id, viewer); }
    this.bringFront(viewer); this.render(this.stamp);
  }

  setMode(id, mode) {
    const viewer = this.open.get(id);
    if (!viewer || !MODES.has(mode) || (mode === 'colour' && !this.project()?.match)) return;
    viewer.mode = mode;
    $$('[data-mode]', viewer.el).forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
    viewer.image.removeAttribute('src');
    viewer.el.classList.remove('loading');
    const url = this.shown.get(this.imageKey(id, mode)); if (url) viewer.image.src = url;
    this.thumbs.get(id)?.closest('.preview-thumb')?.classList.remove('loading');
    if (url) { const thumb = this.thumbs.get(id); if (thumb) thumb.src = url; }
    this.load(id, this.stamp, mode);
  }

  // Bug 37: one request slot per preview *and* mode, so switching modes back and forth reuses a render still in
  // flight instead of discarding it; only a newer stamp (a chain change) supersedes a slot.
  async load(id, stamp, mode) {
    const slot = this.imageKey(id, mode);
    let entry = this.images.get(slot);
    const shows = () => (this.open.get(id)?.mode || 'final') === mode;
    const setLoading = value => {
      if (!shows()) return;
      this.thumbs.get(id)?.closest('.preview-thumb')?.classList.toggle('loading', value);
      this.open.get(id)?.el.classList.toggle('loading', value);
    };
    if (!entry || entry.stamp !== stamp) {
      const progress = this.onRenderStart(`Preview ${id.toUpperCase()}`), pid = this.project()?.id;
      entry = {stamp, pending: true};
      entry.ready = fetch(`/api/render?id=${encodeURIComponent(pid)}&preview=${encodeURIComponent(id)}&depth=8&stage=${mode}&v=${encodeURIComponent(stamp)}&rid=${encodeURIComponent(progress?.rid || '')}`)
        .then(async r => { if (!r.ok) throw new Error((await r.json()).error); return URL.createObjectURL(await r.blob()); })
        .finally(() => { entry.pending = false; progress?.finish(); });
      this.images.set(slot, entry);
    }
    if (entry.pending) setLoading(true);
    let url;
    try { url = await entry.ready; }
    catch (e) {
      if (this.images.get(slot) === entry) { this.images.delete(slot); this.onError(e); }
      setLoading(false); return;
    }
    if (this.images.get(slot) === entry) setLoading(false);
    // A newer stamp superseded this request while its fetch was in flight.  This URL was never installed in
    // `shown`, so forget() cannot reclaim it for us.
    if (this.images.get(slot) !== entry) { if (!this.installed(url)) URL.revokeObjectURL(url); return; }
    const old = this.shown.get(slot);
    if (old === url) return;
    this.shown.set(slot, url);
    if (shows()) {
      const thumb = this.thumbs.get(id); if (thumb) thumb.src = url;
      const current = this.open.get(id); if (current) current.image.src = url;
    }
    if (old) setTimeout(() => URL.revokeObjectURL(old), 5000);
  }

  installed(url) { for (const u of this.shown.values()) if (u === url) return true; return false; }

  title(p, viewer) {
    $('.viewer-title', viewer.el).textContent = `${p.id.toUpperCase()} · ${p.name} · ${p.w} × ${p.h} px at 1:1`;
    $('.viewer-modes', viewer.el).hidden = !this.project()?.match;
  }

  // `at` is the point in the body that stays put (the cursor for the wheel); the buttons keep the centre
  setZoom(viewer, zoom, at) {
    const body = $('.viewer-body', viewer.el), before = viewer.zoom;
    viewer.zoom = Math.max(.25, Math.min(8, zoom));
    if (viewer.image.naturalWidth) {
      const x = at?.x ?? body.clientWidth / 2, y = at?.y ?? body.clientHeight / 2;
      const u = (body.scrollLeft + x) / before, v = (body.scrollTop + y) / before;
      viewer.image.style.width = `${Math.round(viewer.image.naturalWidth * viewer.zoom)}px`;
      body.scrollLeft = u * viewer.zoom - x; body.scrollTop = v * viewer.zoom - y;
    }
    $('[data-action="zoom-reset"]', viewer.el).textContent = `${Math.round(viewer.zoom * 100)}%`;
  }

  // BUGS.md 35: a zoomed preview is larger than its window; dragging the image scrolls it like a hand tool
  pan(viewer) {
    const body = $('.viewer-body', viewer.el);
    body.addEventListener('pointerdown', event => {
      const r = body.getBoundingClientRect();
      const onScrollbar = event.clientX - r.left >= body.clientLeft + body.clientWidth || event.clientY - r.top >= body.clientTop + body.clientHeight;
      if (event.button !== 0 || onScrollbar) return;
      event.preventDefault();
      const origin = {x: event.clientX, y: event.clientY, left: body.scrollLeft, top: body.scrollTop};
      body.setPointerCapture(event.pointerId); body.classList.add('panning');
      const move = e => { body.scrollLeft = origin.left - (e.clientX - origin.x); body.scrollTop = origin.top - (e.clientY - origin.y); };
      const stop = e => {
        body.removeEventListener('pointermove', move); body.removeEventListener('pointerup', stop); body.removeEventListener('pointercancel', stop);
        if (body.hasPointerCapture(e.pointerId)) body.releasePointerCapture(e.pointerId);
        body.classList.remove('panning');
      };
      body.addEventListener('pointermove', move); body.addEventListener('pointerup', stop); body.addEventListener('pointercancel', stop);
    });
  }

  drag(viewer) {
    const head = $('.viewer-head', viewer.el);
    head.addEventListener('pointerdown', event => {
      if (event.button !== 0 || event.target.closest('button')) return;
      event.preventDefault(); event.stopPropagation(); this.bringFront(viewer);
      const left = parseFloat(viewer.el.style.left) || 0, top = parseFloat(viewer.el.style.top) || 0;
      const origin = {x: event.clientX, y: event.clientY, left, top};
      head.setPointerCapture(event.pointerId);
      const move = e => { viewer.el.style.left = `${origin.left + e.clientX - origin.x}px`; viewer.el.style.top = `${origin.top + e.clientY - origin.y}px`; viewer.el.style.right = 'auto'; this.clamp(viewer); };
      const stop = e => { head.removeEventListener('pointermove', move); head.removeEventListener('pointerup', stop); head.removeEventListener('pointercancel', stop); if (head.hasPointerCapture(e.pointerId)) head.releasePointerCapture(e.pointerId); this.saveLayout(viewer); };
      head.addEventListener('pointermove', move); head.addEventListener('pointerup', stop); head.addEventListener('pointercancel', stop);
    });
  }

  clamp(viewer) {
    const host = document.querySelector('#workspace'), el = viewer.el;
    if (!host || !el.isConnected) return;
    const maxX = Math.max(0, host.clientWidth - Math.min(el.offsetWidth, host.clientWidth));
    const maxY = Math.max(0, host.clientHeight - Math.min(el.offsetHeight, host.clientHeight));
    const x = Math.min(maxX, Math.max(0, parseFloat(el.style.left) || 0));
    const y = Math.min(maxY, Math.max(0, parseFloat(el.style.top) || 0));
    el.style.left = `${x}px`; el.style.top = `${y}px`; el.style.right = 'auto';
  }

  restoreLayout(viewer) {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(this.layoutKey(viewer.id)) || 'null'); } catch (_) { /* invalid old layout */ }
    const n = this.open.size;
    Object.assign(viewer.el.style, {left: `${saved?.x ?? 110 + (n % 5) * 32}px`, top: `${saved?.y ?? 38 + (n % 5) * 28}px`, right: 'auto',
      width: `${saved?.w ?? 420}px`, height: `${saved?.h ?? 360}px`});
    this.clamp(viewer);
  }

  saveLayout(viewer) {
    if (!viewer.el.isConnected) return;
    const value = {x: Math.round(parseFloat(viewer.el.style.left) || 0), y: Math.round(parseFloat(viewer.el.style.top) || 0), w: viewer.el.offsetWidth, h: viewer.el.offsetHeight};
    try { localStorage.setItem(this.layoutKey(viewer.id), JSON.stringify(value)); } catch (_) { /* private browsing can deny storage */ }
  }

  bringFront(viewer) { viewer.el.style.zIndex = String(++this.z); }
  removeViewer(id) { const viewer = this.open.get(id); if (!viewer) return; viewer.observer?.disconnect(); viewer.el.remove(); this.open.delete(id); }
  close(id) { this.removeViewer(id); this.render(this.stamp); this.onClose(); }

  forget(id) {
    for (const [key, url] of this.shown) if (key.startsWith(`${id}:`)) { setTimeout(() => URL.revokeObjectURL(url), 5000); this.shown.delete(key); }
    for (const key of [...this.images.keys()]) if (key.startsWith(`${id}:`)) this.images.delete(key);
  }
}
