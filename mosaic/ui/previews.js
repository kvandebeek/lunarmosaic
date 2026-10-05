// Preview rectangles, PixInsight-style (plan phase 7, item 5): drawn on the mosaic, named, saved with the project,
// each rendered at 1:1 through /api/render with the whole chain. Their coordinates are view px — the canvas after
// rotation and mirror, before the coverage crop — so the server moves them when the view turns.
import {h} from './chains.js';

export class Previews {
  // view: () => ({scale, offset: {x, y}, box: [x0, y0, x1, y1]}) — how view px reach the screen
  constructor({stage, layer, strip, viewer, api, project, view, onError}) {
    Object.assign(this, {stage, layer, strip, viewer, api, project, view, onError});
    this.drawing = null;
    this.selected = null;
    this.images = new Map();     // id -> {key, ready: Promise<blob url>}: one request per preview and stamp
    this.thumbs = new Map();     // id -> its <img> in the strip
    this.shown = new Map();      // id -> the url last shown, kept on screen while a newer render loads
    this.stamp = '';             // what the rendered pixels depend on: the project revision and the view
    this.shape = '';             // the strip as last built: the list and the selection
    this.pending = null;         // the resolve of a draw promise (white balance references use it too)
  }

  toScreen(x, y) {
    const v = this.view();
    return [v.offset.x + (x - v.box[0]) * v.scale, v.offset.y + (y - v.box[1]) * v.scale];
  }

  toView(sx, sy) {
    const v = this.view(), r = this.stage.getBoundingClientRect();
    return [(sx - r.left - v.offset.x) / v.scale + v.box[0], (sy - r.top - v.offset.y) / v.scale + v.box[1]];
  }

  // Draw a rectangle on the stage; resolves with [x, y, w, h] in view px, or null when cancelled.
  draw(label) {
    this.cancelDraw();
    this.viewer.hidden = true;                 // the canvas must be free to draw on; finish() brings it back
    this.stage.classList.add('drawing');
    this.layer.dataset.hint = label || 'Drag a rectangle on the mosaic · Esc cancels';
    return new Promise(resolve => {
      this.pending = resolve;
      const down = ev => {
        if (ev.button !== 0) return;
        ev.stopImmediatePropagation();
        ev.preventDefault();
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
          window.removeEventListener('pointermove', move, true);
          window.removeEventListener('pointerup', up, true);
          box.remove();
          const [x1, y1] = this.toView(e.clientX, e.clientY);
          const x = Math.max(0, Math.round(Math.min(x0, x1))), y = Math.max(0, Math.round(Math.min(y0, y1)));
          const rect = [x, y, Math.round(Math.max(x0, x1)) - x, Math.round(Math.max(y0, y1)) - y];
          this.finish(rect[2] >= 16 && rect[3] >= 16 ? rect : null);
        };
        window.addEventListener('pointermove', move, true);
        window.addEventListener('pointerup', up, true);
      };
      this.drawing = down;
      this.stage.addEventListener('pointerdown', down, true);
    });
  }

  finish(rect) {
    if (this.drawing) this.stage.removeEventListener('pointerdown', this.drawing, true);
    this.drawing = null;
    this.stage.classList.remove('drawing');
    delete this.layer.dataset.hint;
    if (this.selected) this.viewer.hidden = false;
    const resolve = this.pending;
    this.pending = null;
    if (resolve) resolve(rect);
  }

  cancelDraw() { if (this.pending) this.finish(null); }

  async add() {
    const rect = await this.draw('Drag the preview rectangle on the mosaic · Esc cancels');
    if (!rect) return;
    const [x, y, w, hh] = rect;
    const before = new Set(this.list().map(v => v.id));
    await this.api('previews', {op: 'add', preview: {x, y, w: Math.min(w, 4096), h: Math.min(hh, 4096)}});
    const made = this.list().find(v => !before.has(v.id));
    if (made) this.select(made.id);
  }

  list() { return this.project()?.chain?.previews || []; }

  // The rectangles on the mosaic, where the view puts them now (pan and zoom call this alone).
  place() {
    this.layer.querySelectorAll('.preview-rect:not(.drawing-rect)').forEach(el => el.remove());
    const v = this.view();
    for (const p of this.list()) {
      const [a, b] = this.toScreen(p.x, p.y);
      this.layer.append(h('div', {class: `preview-rect${p.id === this.selected ? ' selected' : ''}`, dataset: {id: p.id},
        style: `left:${a}px;top:${b}px;width:${p.w * v.scale}px;height:${p.h * v.scale}px`},
        h('span', {text: `${p.id.toUpperCase()} · ${p.name}`})));
    }
  }

  // The rectangles and the strip; stamp changes whenever the rendered pixels may change, and every preview is
  // rendered again then. The strip is rebuilt only when the list or the selection changes.
  render(stamp) {
    this.stamp = stamp;
    const previews = this.list();
    this.place();
    const shape = JSON.stringify([previews, this.selected]);
    if (shape !== this.shape) {
      this.shape = shape;
      this.thumbs.clear();
      this.strip.replaceChildren(...previews.map(p => this.item(p)),
        h('button', {class: 'btn add-preview', type: 'button', title: 'Draw a preview rectangle (P)', onclick: () => this.add().catch(e => this.onError(e))}, '+ Preview'));
      for (const id of [...this.images.keys()]) if (!previews.some(p => p.id === id)) this.forget(id);
    }
    this.strip.hidden = false;
    if (this.selected && !previews.some(p => p.id === this.selected)) { this.close(); return; }
    for (const p of previews) this.load(p.id, stamp);
    if (this.selected) this.title();
  }

  item(p) {
    const img = h('img', {alt: `${p.name} at 1:1`});
    if (this.shown.has(p.id)) img.src = this.shown.get(p.id);
    this.thumbs.set(p.id, img);
    const name = h('span', {class: 'preview-name', text: `${p.id.toUpperCase()} · ${p.name}`, title: 'Double-click to rename'});
    name.addEventListener('dblclick', async () => {
      const next = prompt('Name this preview', p.name);
      if (next && next.trim()) await this.api('previews', {op: 'update', preview: {id: p.id, name: next.trim()}}).catch(e => this.onError(e));
    });
    return h('figure', {class: `preview-item${p.id === this.selected ? ' selected' : ''}`, dataset: {id: p.id}},
      h('button', {class: 'preview-thumb', type: 'button', 'aria-label': `Open ${p.name} at 1:1`, onclick: () => this.select(p.id)}, img),
      h('figcaption', {}, name, h('button', {class: 'icon', type: 'button', title: 'Delete preview', 'aria-label': `Delete ${p.name}`,
        onclick: () => this.api('previews', {op: 'delete', preview: {id: p.id}}).catch(e => this.onError(e))}, '×')));
  }

  // /api/render at 1:1 for one preview, once per stamp; the strip and the viewer show it when it arrives
  async load(id, stamp) {
    const key = `${id}:${stamp}`;
    let entry = this.images.get(id);
    if (!entry || entry.key !== key) {
      const pid = this.project()?.id;
      entry = {key, ready: fetch(`/api/render?id=${pid}&preview=${id}&depth=8&v=${encodeURIComponent(stamp)}`).then(async r => {
        if (!r.ok) throw new Error((await r.json()).error);
        return URL.createObjectURL(await r.blob());
      })};
      this.images.set(id, entry);
    }
    let url;
    try { url = await entry.ready; }
    catch (e) {
      if (this.images.get(id) === entry) { this.images.delete(id); this.onError(e); }
      return;
    }
    if (this.images.get(id) !== entry) return;          // a newer render was asked for meanwhile
    const old = this.shown.get(id);
    if (old === url) return;
    this.shown.set(id, url);
    const img = this.thumbs.get(id);
    if (img) img.src = url;
    if (this.selected === id) this.viewer.querySelector('img').src = url;
    if (old) setTimeout(() => URL.revokeObjectURL(old), 5000);
  }

  forget(id) {
    const url = this.shown.get(id);
    if (url) setTimeout(() => URL.revokeObjectURL(url), 5000);
    this.shown.delete(id);
    this.images.delete(id);
  }

  title() {
    const p = this.list().find(v => v.id === this.selected);
    if (p) this.viewer.querySelector('.viewer-title').textContent = `${p.id.toUpperCase()} · ${p.name} · ${p.w} × ${p.h} px at 1:1`;
  }

  select(id) {
    if (!this.list().some(v => v.id === id)) return;
    this.selected = id;
    this.viewer.hidden = false;
    const img = this.viewer.querySelector('img');
    img.removeAttribute('src');
    if (this.shown.has(id)) img.src = this.shown.get(id);
    this.render(this.stamp);
  }

  close() { this.selected = null; this.viewer.hidden = true; this.render(this.stamp); }
}
