// The browser journey of plan phase 7, driven through the Chrome DevTools protocol (ui_journey.py runs it).
//
//   node ui_journey.js DEVTOOLS_PORT APP_URL CONFIG_JSON
//
// Every step acts as a user would — clicks, typing into fields, real mouse drags and key presses — and then waits
// for what the user would see, checking it against the server's state. Any console error, uncaught exception or
// failed request ends the journey.
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const [port, appUrl, config] = process.argv.slice(2);
const cfg = JSON.parse(config);
const errors = [];
const pending = new Map();
let ws, seq = 0;

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, {resolve, reject, method});
    ws.send(JSON.stringify({id, method, params}));
  });
}

function onMessage(event) {
  const m = JSON.parse(event.data);
  if (m.id) {
    const p = pending.get(m.id);
    pending.delete(m.id);
    if (m.error) p.reject(new Error(`${p.method}: ${m.error.message}`));
    else p.resolve(m.result);
    return;
  }
  const at = () => `(during: ${step})`;
  if (m.method === 'Runtime.exceptionThrown') {
    const d = m.params.exceptionDetails;
    errors.push(`uncaught ${d.exception?.description || d.text} ${at()}`);
  } else if (m.method === 'Runtime.consoleAPICalled' && ['error', 'assert'].includes(m.params.type)) {
    errors.push(`console.${m.params.type}: ${m.params.args.map(a => a.value ?? a.description).join(' ')} ${at()}`);
  } else if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') {
    errors.push(`${m.params.entry.source}: ${m.params.entry.text} ${m.params.entry.url || ''} ${at()}`);
  }
}

async function js(expression) {
  const r = await send('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true, userGesture: true});
  if (r.exceptionDetails) {
    const d = r.exceptionDetails;
    throw new Error(`In the page: ${d.exception?.description || d.text}\n  ${expression.slice(0, 400)}`);
  }
  return r.result.value;
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function until(expression, what, timeout = 60000) {
  const t0 = Date.now();
  for (;;) {
    const v = await js(expression);
    if (v) return v;
    if (Date.now() - t0 > timeout) throw new Error(`Timed out after ${Math.round(timeout / 1000)} s waiting for ${what}`);
    await sleep(150);
  }
}

function check(ok, what) {
  if (!ok) throw new Error(`Check failed: ${what}`);
  console.log(`  ok  ${what}`);
}

let step = 'start';
async function shot(name) {
  if (!process.env.JOURNEY_SHOTS) return;
  await sleep(300);
  const png = await send('Page.captureScreenshot', {format: 'png'});
  fs.writeFileSync(path.join(process.env.JOURNEY_SHOTS, `${name}.png`), Buffer.from(png.data, 'base64'));
}
function begin(name) { step = name; console.log(`- ${name}`); }

// helpers defined in the page before its own scripts, on every load
const HELPERS = `
window.J = {
  el(s) { const e = document.querySelector(s); if (!e) throw new Error('No element ' + s); return e; },
  shown(s) { const e = document.querySelector(s); return !!e && !e.closest('[hidden]') && e.getClientRects().length > 0; },
  click(s) { const e = J.el(s); if (e.disabled) throw new Error(s + ' is disabled'); e.click(); return true; },
  set(s, v, fire) { const e = J.el(s); e.value = String(v); if (fire) e.dispatchEvent(new Event('change', {bubbles: true})); return true; },
  text(s) { return J.el(s).textContent; },
  async state() { const r = await fetch('/api/state'); return r.json(); },
  async get(route) { const r = await fetch('/api/' + route); if (!r.ok) throw new Error(route + ': ' + r.status); return r.json(); },
  loaded(img) { return !!img && img.complete && img.naturalWidth > 0; },
  // a hash of the pixels: tells whether a render changed
  signature(img) {
    const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
    const g = c.getContext('2d'); g.drawImage(img, 0, 0);
    const d = g.getImageData(0, 0, c.width, c.height).data;
    let h = 2166136261; for (let i = 0; i < d.length; i++) { h ^= d[i]; h = Math.imul(h, 16777619); }
    return h >>> 0;
  },
  menu(path, label) {
    return [...document.querySelectorAll('[data-path="' + path + '"] .menu-item')].find(b => b.querySelector('strong').textContent === label);
  },
  blocks() {
    return Object.fromEntries(['luminance', 'colour', 'final'].map(p => [p, [...document.querySelectorAll('section.chain[data-path="' + p + '"] .block')]
      .map(b => b.querySelector('.block-title strong').textContent + ' | ' + b.querySelector('.block-title small').textContent)]));
  },
  thumbs() { return [...document.querySelectorAll('#preview-strip .preview-item img')]; },
  // the share of pixels with a clear hue (8-bit max − min above 20)
  colourful(img) {
    const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
    const g = c.getContext('2d'); g.drawImage(img, 0, 0);
    const d = g.getImageData(0, 0, c.width, c.height).data;
    let n = 0, lit = 0;
    for (let i = 0; i < d.length; i += 4) {
      const hi = Math.max(d[i], d[i + 1], d[i + 2]), lo = Math.min(d[i], d[i + 1], d[i + 2]);
      if (hi > 30) { lit++; if (hi - lo > 20) n++; }
    }
    return lit ? n / lit : 0;
  },
};`;

async function press(key, code, keyCode, modifiers = 0, text = undefined) {
  await send('Input.dispatchKeyEvent', {type: text ? 'keyDown' : 'rawKeyDown', key, code, windowsVirtualKeyCode: keyCode, modifiers, text});
  await send('Input.dispatchKeyEvent', {type: 'keyUp', key, code, windowsVirtualKeyCode: keyCode, modifiers});
}

async function drag(x0, y0, x1, y1) {
  await send('Input.dispatchMouseEvent', {type: 'mouseMoved', x: x0, y: y0});
  await send('Input.dispatchMouseEvent', {type: 'mousePressed', x: x0, y: y0, button: 'left', buttons: 1, clickCount: 1});
  for (let i = 1; i <= 6; i++) {
    await send('Input.dispatchMouseEvent', {type: 'mouseMoved', x: x0 + (x1 - x0) * i / 6, y: y0 + (y1 - y0) * i / 6, button: 'left', buttons: 1});
  }
  await send('Input.dispatchMouseEvent', {type: 'mouseReleased', x: x1, y: y1, button: 'left', buttons: 0, clickCount: 1});
}

const idle = () => until(`J.state().then(s => s.job.state !== 'running' && document.querySelector('#saved').textContent === 'Saved locally')`, 'the project to be saved and idle');
const serverChain = () => js(`J.state().then(s => s.project.chain)`);

async function job(what, timeout) {
  const s = await until(`J.state().then(s => s.job.state !== 'running' ? s.job : null)`, what, timeout);
  if (s.state !== 'done') throw new Error(`${what}: ${s.state} — ${s.error || ''}\n${(s.log || []).slice(-15).join('\n')}`);
  return s;
}

async function addBlock(chainPath, label) {
  await js(`J.click('section.chain[data-path="${chainPath}"] .add-block')`);
  await until(`!!J.menu('${chainPath}', '${label}')`, `the add menu of the ${chainPath} chain`);
  await js(`J.menu('${chainPath}', '${label}').click()`);
  await until(`J.state().then(s => s.project.chain.chains['${chainPath}'].length && J.blocks()['${chainPath}'].some(t => t.startsWith('${label}')))`, `${label} on the ${chainPath} chain`);
}

// a preview's 1:1 renders, as hashes, once every thumbnail and the server agree on the count
async function thumbs(count, different = null) {
  return until(`(() => { const t = J.thumbs(); if (t.length !== ${count} || !t.every(J.loaded)) return null;
    const s = t.map(J.signature); const before = ${JSON.stringify(different)};
    return before && s.some((v, i) => v === before[i]) ? null : s; })()`, `${count} preview renders${different ? ' to change' : ''}`, 120000);
}

async function main() {
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target = targets.find(t => t.type === 'page');
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  ws.onmessage = onMessage;
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable');
  await send('Page.addScriptToEvaluateOnNewDocument', {source: HELPERS});
  await send('Emulation.setDeviceMetricsOverride', {width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false});

  begin('open the app and create a project');
  await send('Page.navigate', {url: appUrl});
  await until(`document.readyState === 'complete' && !!window.J && J.shown('#create')`, 'the welcome page');
  await js(`J.set('#project-name', 'Journey Moon'); J.set('#project-folder', ${JSON.stringify(cfg.folder)}); J.click('#create')`);
  await until(`J.shown('#import-page') && J.shown('[data-set="L"]')`, 'the import page');

  begin('import two luminance sets at different scales and one colour set');
  const useFolder = async (set, folder, focal) => {
    await js(`(() => { const c = J.el('[data-set="${set}"]');
      c.querySelector('.source-path').value = ${JSON.stringify(folder)};
      c.querySelector('.pixel').value = '2.9'; c.querySelector('.focal').value = '${focal}';
      c.querySelector('.scan').click(); return true; })()`);
  };
  await useFolder('L', cfg.fine, 2000);
  await until(`J.text('[data-set="L"] .set-summary').startsWith('${cfg.counts.fine} panels')`, 'the fine set to be read');
  await js(`J.click('#add-lum-set')`);
  await until(`J.shown('[data-set="s2"]')`, 'a second luminance set');
  await js(`J.set('[data-set="s2"] .set-label', 'Wide field', true)`);
  await useFolder('s2', cfg.coarse, 1000);
  await until(`J.text('[data-set="s2"] .set-summary').startsWith('${cfg.counts.coarse} panels')`, 'the coarse set to be read');
  await useFolder('RGB', cfg.colour, 1000);
  await until(`J.text('[data-set="RGB"] .set-summary').startsWith('${cfg.counts.colour} panels')`, 'the colour set to be read');
  check(await js(`J.text('[data-set="L"] .scale')`) === '0.299 ″/px from the optics.', 'the fine set\'s scale from its optics');
  check(await js(`J.text('[data-set="s2"] .scale')`) === '0.598 ″/px from the optics.', 'the coarse set\'s scale from its optics');
  check(/sample 2\.0× coarser than the luminance/.test(await js(`J.text('#import-note')`)), 'the colour/luminance ratio in words');
  await until(`J.shown('#canvas-card') && /at least [\\d,]+ px across/.test(J.text('#canvas-estimate'))`, 'the canvas lower bound');
  await shot('01-import');
  const sets = await js(`J.state().then(s => s.project.sets.map(x => [x.id, x.stream, x.label, x.inputs.length]))`);
  check(JSON.stringify(sets) === JSON.stringify([['L', 'luminance', 'Luminance', cfg.counts.fine], ['RGB', 'colour', 'Colour', cfg.counts.colour],
                                                 ['s2', 'luminance', 'Wide field', cfg.counts.coarse]]), 'three sets on two streams, as entered');

  begin('build the luminance and place the colour');
  await js(`J.click('#build-both')`);
  await until(`J.shown('#build-page')`, 'the build page');
  await until(`J.state().then(s => s.job.set === 'RGB' || s.job.state !== 'running')`, 'the luminance build', 600000);
  await job('the build', 600000);
  const built = await js(`J.state().then(s => [s.project.luminance.state, s.project.colour.state, s.project.colour.summary])`);
  check(built[0] === 'built' && built[1] === 'built', 'both streams built');
  check(built[2].placed === cfg.counts.colour, `every colour panel placed (${built[2].placed} of ${cfg.counts.colour})`);
  await until(`!J.el('#go-edit').disabled`, 'the workspace button');
  await shot('02-build');

  begin('the workspace: placement chip and per-panel table');
  await js(`J.click('#go-edit')`);
  await until(`J.shown('#workspace') && J.loaded(J.el('#mosaic')) && J.el('#mosaic').style.transform !== ''`, 'the mosaic on the canvas', 120000);
  const chip = await until(`/^Match \\d+\\.\\d\\d px · worst \\d+\\.\\d\\d px/.test(J.text('#match-chip')) && J.text('#match-chip')`, 'the placement chip');
  check(true, `the chip shows measured numbers: "${chip}"`);
  await js(`J.click('#match-chip')`);
  await until(`J.shown('#match-popover') && document.querySelectorAll('#placement-table tbody tr').length === ${cfg.counts.colour}`, 'the per-panel table');
  const row = await js(`[...J.el('#placement-table tbody tr').children].map(td => td.textContent)`);
  check(row[1] === 'yes' && /^(similarity|affine|poly3)$/.test(row[2]) && / px$/.test(row[4]), `a panel row: ${row.join(' · ')}`);
  await shot('03-placement');
  await js(`J.click('#match-popover-close')`);
  await until(`!J.shown('#match-popover')`, 'the popover to close');

  begin('add wavelets, white balance and curves (and contrast)');
  await until(`document.querySelectorAll('section.chain[data-path]').length === 3`, 'the chain editor');
  await js(`J.click('section.chain[data-path="luminance"] .add-block')`);
  await until(`!!J.menu('luminance', 'Saturation')`, 'the luminance add menu');
  const refused = await js(`(() => { const b = J.menu('luminance', 'Saturation'); return [b.disabled, b.title]; })()`);
  check(refused[0] && /changes colour/.test(refused[1]), `a colour block is greyed on the luminance chain: "${refused[1]}"`);
  await js(`J.click('section.chain[data-path="luminance"] .add-block')`);        // closes the menu again
  await addBlock('luminance', 'À-trous wavelets');
  await addBlock('colour', 'White balance');
  await addBlock('final', 'Curves');
  await addBlock('final', 'Contrast');
  await idle();
  const gains = await js(`[...document.querySelectorAll('section.chain[data-path="luminance"] .gain')].map(g => g.textContent)`);
  check(gains.length === 4 && gains.every(g => g === '×1.0'), 'the PixInsight gain beside every wavelet bias');
  check(/never does/.test(await js(`J.text('section.chain[data-path="colour"] .block-body')`)), 'the white balance brightness sentence');
  await js(`(() => { const i = document.querySelector('section.chain[data-path="luminance"] .block-body input[type=number]');
    i.value = '0.5'; i.dispatchEvent(new Event('change', {bubbles: true})); return true; })()`);
  await until(`J.state().then(s => s.project.chain.chains.luminance[0].params.biases[0] === 0.5)`, 'the wavelet bias to be saved');
  check(await js(`document.querySelector('section.chain[data-path="luminance"] .gain').textContent`) === '×6.0', 'bias 0.5 shows gain ×6.0');
  await idle();

  begin('draw two previews on the mosaic');
  const box = await js(`(() => { const r = J.el('#mosaic').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()`);
  const [L, T, W, H] = box;
  await js(`document.activeElement && document.activeElement.blur(), true`);
  await press('p', 'KeyP', 80, 0, 'p');
  await until(`J.el('#stage').classList.contains('drawing')`, 'the preview tool');
  await drag(L + W * .28, T + H * .40, L + W * .44, T + H * .55);
  await until(`J.state().then(s => s.project.chain.previews.length === 1)`, 'the first preview to be saved');
  await thumbs(1);
  await until(`J.shown('#preview-viewer') && J.loaded(document.querySelector('#preview-viewer img'))`, 'the preview at 1:1');
  const p1 = (await serverChain()).previews[0];
  const natural = await js(`[document.querySelector('#preview-viewer img').naturalWidth, document.querySelector('#preview-viewer img').naturalHeight]`);
  check(natural[0] === p1.w && natural[1] === p1.h, `the viewer shows the preview at 1:1 (${natural.join(' × ')} px)`);
  await js(`J.click('#preview-tool')`);
  await until(`J.el('#stage').classList.contains('drawing')`, 'the preview tool again');
  await drag(L + W * .55, T + H * .25, L + W * .72, T + H * .38);
  await until(`J.state().then(s => s.project.chain.previews.length === 2)`, 'the second preview to be saved');
  const before = await thumbs(2);
  check(await js(`document.querySelectorAll('#overlay .preview-rect').length`) === 2, 'both rectangles drawn on the mosaic');
  await shot('04-chains-and-previews');

  begin('change a parameter: every preview renders again');
  await js(`(() => { const i = document.querySelector('section.chain[data-path="luminance"] .block-body input[type=number]');
    i.value = '1.5'; i.dispatchEvent(new Event('change', {bubbles: true})); return true; })()`);
  await until(`J.state().then(s => s.project.chain.chains.luminance[0].params.biases[0] === 1.5)`, 'the stronger bias to be saved');
  const after = await thumbs(2, before);
  check(after.every((v, i) => v !== before[i]), 'both previews changed');

  begin('rotate the view: the previews follow');
  const id = await js(`J.state().then(s => s.project.id)`);
  const A = (await js(`J.get('geometry?id=${id}')`)).full.matrix;
  const old = (await serverChain()).previews;
  await js(`J.set('#rotation', '90', true)`);
  await until(`J.state().then(s => s.project.geometry.rotation === 90)`, 'the rotation to be saved');
  const geo = await js(`J.get('geometry?id=${id}')`);
  const moved = (await serverChain()).previews;
  const B = geo.full.matrix;
  const apply = (M, [x, y]) => [M[0][0] * x + M[0][1] * y + M[0][2], M[1][0] * x + M[1][1] * y + M[1][2]];
  const inv = M => { const d = M[0][0] * M[1][1] - M[0][1] * M[1][0];
    const a = [[M[1][1] / d, -M[0][1] / d], [-M[1][0] / d, M[0][0] / d]];
    return [[a[0][0], a[0][1], -(a[0][0] * M[0][2] + a[0][1] * M[1][2])], [a[1][0], a[1][1], -(a[1][0] * M[0][2] + a[1][1] * M[1][2])]]; };
  for (const [i, p] of old.entries()) {
    const want = apply(B, apply(inv(A), [p.x + p.w / 2, p.y + p.h / 2])), q = moved[i];
    const got = [q.x + q.w / 2, q.y + q.h / 2];
    check(Math.hypot(got[0] - want[0], got[1] - want[1]) <= 1 && q.w === p.w && q.h === p.h,
          `${p.id} follows its centre: (${got.map(v => v.toFixed(1)).join(', ')}) for (${want.map(v => v.toFixed(1)).join(', ')})`);
  }
  const lumSize = await js(`J.state().then(s => [s.project.luminance.width, s.project.luminance.height])`);
  check(geo.full.width === lumSize[1] && geo.full.height === lumSize[0], `the turned view is ${geo.full.width} × ${geo.full.height} px`);
  await thumbs(2, after);

  begin('the resolution view names each luminance set');
  const composite = await js(`[J.el('#mosaic').src, J.colourful(J.el('#mosaic'))]`);
  await js(`J.click('[data-view="resolution"]')`);
  await until(`J.shown('#legend') && document.querySelectorAll('#legend-list li').length === 2`, 'the set legend');
  const legend = await js(`[...document.querySelectorAll('#legend-list li')].map(li => li.innerText.replace(/\\s+/g, ' '))`);
  check(/^Luminance tier 1 .*0\.299 ″\/px/.test(legend[0]) && /^Wide field tier 2 .*0\.598 ″\/px/.test(legend[1]), `the legend: ${legend.join(' | ')}`);
  await until(`J.el('#mosaic').src !== ${JSON.stringify('')} + ${JSON.stringify(composite[0])} && J.loaded(J.el('#mosaic'))`, 'the resolution view');
  const tinted = await js(`J.colourful(J.el('#mosaic'))`);
  check(tinted > composite[1] + .2, `the view is tinted by set (${(tinted * 100).toFixed(0)} % of pixels coloured, ${(composite[1] * 100).toFixed(0)} % in the composite)`);
  await shot('05-resolution');
  await js(`J.click('[data-view="composite"]')`);
  await until(`!J.shown('#legend') && J.text('#status-dimensions').endsWith('composite')`, 'the composite view again');

  begin('reorder by keyboard');
  const finalIds = (await serverChain()).chains.final.map(e => e.id);
  await js(`document.querySelector('section.chain[data-path="final"] .block[data-id="${finalIds[0]}"] .block-head').focus(), true`);
  await press('ArrowDown', 'ArrowDown', 40, 1);
  await until(`J.state().then(s => s.project.chain.chains.final.map(e => e.id).join() === '${[finalIds[1], finalIds[0]].join()}')`, 'Alt+↓ to move curves below contrast');
  check(await js(`document.activeElement.closest('[data-id]')?.dataset.id`) === finalIds[0], 'the moved block keeps the focus');
  await press('ArrowUp', 'ArrowUp', 38, 1);
  await until(`J.state().then(s => s.project.chain.chains.final.map(e => e.id).join() === '${finalIds.join()}')`, 'Alt+↑ to move it back');
  // contrast above curves, as the rest of the journey expects
  await js(`document.querySelector('section.chain[data-path="final"] .block[data-id="${finalIds[1]}"] .block-head').focus(), true`);
  await press('ArrowUp', 'ArrowUp', 38, 1);
  await until(`J.state().then(s => s.project.chain.chains.final.map(e => e.type).join() === 'contrast,curves')`, 'contrast first');
  await idle();

  begin('reload the page: the state is identical');
  const snapshot = async () => js(`J.state().then(s => JSON.stringify({chain: s.project.chain, geometry: s.project.geometry, view: s.project.view,
    blocks: J.blocks(), previews: [...document.querySelectorAll('#preview-strip .preview-name')].map(n => n.textContent),
    rotation: J.el('#rotation').value, chip: J.text('#match-chip')}))`);
  const was = await snapshot();
  await js(`window.beforeReload = true`);          // Page.reload returns at once: wait for the new document
  await send('Page.reload');
  await until(`!window.beforeReload && document.readyState === 'complete' && !!window.J && J.shown('#workspace') && J.loaded(J.el('#mosaic')) && document.querySelectorAll('.block').length === 4
    && J.thumbs().length === 2 && J.thumbs().every(J.loaded)`, 'the workspace after the reload', 120000);
  const now = await snapshot();
  const differs = Object.keys(JSON.parse(was)).filter(k => JSON.stringify(JSON.parse(was)[k]) !== JSON.stringify(JSON.parse(now)[k]));
  check(!differs.length, 'chain, geometry, view, editor, previews and chip unchanged by the reload' +
        differs.map(k => `\n    ${k}: ${JSON.stringify(JSON.parse(was)[k])}\n    -> ${JSON.stringify(JSON.parse(now)[k])}`).join(''));

  begin('load a chain file, then export a TIFF');
  const file = await js(`J.get('chain-file?id=${id}')`);
  file.chains.final.find(e => e.type === 'contrast').params.strength = 0.2;
  fs.writeFileSync(cfg.chain_file, JSON.stringify(file, null, 2));
  await js(`J.click('#export-open')`);
  await until(`J.el('#export-dialog').open && !J.el('#export-run').disabled`, 'the export dialog and its preview', 120000);
  await shot('06-export');
  const {root} = await send('DOM.getDocument');
  const {nodeId} = await send('DOM.querySelector', {nodeId: root.nodeId, selector: '#chain-import'});
  await send('DOM.setFileInputFiles', {files: [cfg.chain_file], nodeId});
  await until(`J.state().then(s => s.project.chain.chains.final[0].params.strength === 0.2)`, 'the chain file to be loaded');
  const loaded = await serverChain();
  check(loaded.previews.length === 2 && loaded.chains.final.every(e => !finalIds.includes(e.id)), 'the file\'s blocks get fresh ids; the previews stay');
  await until(`J.blocks().final[0] === 'Contrast | +0.20'`, 'the editor to show the loaded chain');
  await until(`!J.el('#export-run').disabled`, 'the export preview of the loaded chain', 120000);
  await js(`J.set('#export-path', ${JSON.stringify(cfg.export)}); J.click('#export-run')`);
  await until(`J.shown('#build-page')`, 'the export to start');
  await job('the export', 600000);
  check(fs.existsSync(cfg.export) && fs.statSync(cfg.export).size > 1e6, `the TIFF was written (${path.basename(cfg.export)})`);

  begin('close and reopen the project');
  const kept = await snapshot();
  await js(`J.click('#home')`);
  await until(`J.shown('#close-project')`, 'the projects page');
  await js(`J.click('#close-project')`);
  await until(`J.state().then(s => s.project === null && J.shown('#create'))`, 'the project to close');
  await until(`[...document.querySelectorAll('#recent-list .recent-open strong')].some(s => s.textContent === 'Journey Moon')`, 'the recent list');
  await js(`J.set('#open-folder', ${JSON.stringify(cfg.folder)}); J.click('#open-project')`);
  await until(`J.shown('#workspace') && J.loaded(J.el('#mosaic')) && document.querySelectorAll('.block').length === 4 && J.thumbs().length === 2
    && J.thumbs().every(J.loaded)`, 'the reopened workspace', 120000);
  const reopened = JSON.parse(await snapshot()), keptState = JSON.parse(kept);
  check(JSON.stringify(reopened.chain) === JSON.stringify(keptState.chain) && JSON.stringify(reopened.blocks) === JSON.stringify(keptState.blocks),
        'the reopened project has the same chain and editor');

  begin('no console errors');
  await sleep(500);
  if (errors.length) throw new Error('Console errors:\n' + errors.join('\n'));
  console.log('PASS journey');
}

// on failure: what the page showed, as text and (with JOURNEY_SHOTS set to a folder) as a screenshot
async function dump() {
  try {
    const text = await js(`[...document.querySelectorAll('main, dialog[open], #toast')].filter(e => !e.hidden).map(e => e.innerText).join('\\n----\\n')`);
    console.log('Page text:\n' + text.split('\n').filter(Boolean).slice(0, 120).join('\n'));
    if (process.env.JOURNEY_SHOTS) {
      const shot = await send('Page.captureScreenshot', {format: 'png'});
      const file = path.join(process.env.JOURNEY_SHOTS, `journey-${step.replace(/[^a-z0-9]+/gi, '-').slice(0, 40)}.png`);
      fs.writeFileSync(file, Buffer.from(shot.data, 'base64'));
      console.log(`Screenshot: ${file}`);
    }
  } catch (e) { console.log(`(no page dump: ${e.message})`); }
}

main().then(() => process.exit(0), async e => {
  console.log(`FAILED at "${step}": ${e.stack || e}`);
  if (errors.length) console.log('Console errors so far:\n' + errors.join('\n'));
  await dump();
  process.exit(1);
});
