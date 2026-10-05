// Autosave behaviour of the shipped ui/save.js (plan phase 7, item 6; BUGS.md bug 11): Node, no npm packages.
//
//   node ui_autosave.js SCENARIO
//
// The module is loaded as it ships; the server and the timers are fakes the scenario drives step by step.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const settle = () => new Promise(resolve => setImmediate(resolve));
const conflict = () => Object.assign(new Error('The project changed elsewhere.'), {status: 409, conflict: true});
const refused = () => Object.assign(new Error('Block 1: black must be below white.'), {status: 400, retry: false});

async function harness() {
  const source = fs.readFileSync(path.join(__dirname, '../ui/save.js'), 'utf8');
  const {Autosave} = await import('data:text/javascript,' + encodeURIComponent(source));
  const requests = [], timers = new Map(), states = [];
  let next = 0;
  const saver = new Autosave({
    send: doc => new Promise((resolve, reject) => requests.push({doc: structuredClone(doc), resolve, reject})),
    schedule: (fn, ms) => { timers.set(++next, {fn, ms}); return next; },
    cancel: id => timers.delete(id),
    onState: s => states.push({...s}),
  });
  const h = {saver, requests, timers, states};
  h.timer = () => { assert.equal(timers.size, 1, 'Expected exactly one armed timer'); return [...timers.values()][0]; };
  h.fire = async () => { const [id, t] = [...timers.entries()][0]; timers.delete(id); t.fn(); await settle(); };
  h.accept = async i => { requests[i].resolve(); await settle(); };
  h.fail = async (i, e) => { requests[i].reject(e); await settle(); };
  h.last = () => states[states.length - 1];
  return h;
}

const doc = (extra = {}) => ({format: 'lunarmosaic-chain', version: 1, protect_space: true, chains: {luminance: [], colour: [], merge: {strength: 1}, final: []}, ...extra});
const wavelets = biases => doc({chains: {luminance: [{id: 'a1b2c3d4', type: 'wavelets', enabled: true, limb_fade_px: 0, params: {biases, kernel: 'linear', denoise: [0, 0, 0, 0]}}], colour: [], merge: {strength: 1}, final: []}});

const scenarios = {
  // a failed save keeps the document on screen and sends it again on its own
  async single_failure() {
    const h = await harness();
    const A = doc({protect_space: false});
    h.saver.change(A);
    assert.equal(h.timer().ms, 300, 'Saved after 300 ms of quiet');
    await h.fire();
    assert.equal(h.requests.length, 1);
    await h.fail(0, conflict());
    assert.deepEqual(h.saver.working, A, 'The failed document stays on screen');
    assert.ok(h.saver.busy);
    assert.equal(h.last().failed, 'The project changed elsewhere.');
    assert.equal(h.last().retrying, true);
    assert.equal(h.timer().ms, 2000, 'Sent again after 2 s');
    await h.fire();
    assert.deepEqual(h.requests[1].doc, A);
    await h.accept(1);
    assert.equal(h.saver.busy, false);
    assert.equal(h.last().failed, null);
    assert.equal(h.timers.size, 0);
  },

  // an edit made while a save is out wins over the failed one, and is sent as soon as possible
  async different_fields_failure() {
    const h = await harness();
    const A = doc({protect_space: false}), B = doc({protect_space: false, chains: {...doc().chains, merge: {strength: .5}}});
    h.saver.change(A);
    await h.fire();
    h.saver.change(B);
    await h.fail(0, conflict());
    assert.deepEqual(h.saver.working, B);
    assert.equal(h.timer().ms, 300, 'The newer edit goes out after the usual quiet time, not the retry delay');
    await h.fire();
    assert.deepEqual(h.requests[1].doc, B, 'Both changes are in the document sent again');
    await h.accept(1);
    assert.equal(h.saver.busy, false);
  },

  // an edit made while a save is out follows it; the document sent first is not changed afterwards
  async different_fields_success() {
    const h = await harness();
    const A = doc({protect_space: false}), B = doc({protect_space: false, chains: {...doc().chains, merge: {strength: .5}}});
    h.saver.change(A);
    const flushing = h.saver.flush();
    h.saver.change(B);
    await h.fire();                                   // the timer of B finds A in flight and waits
    assert.equal(h.requests.length, 1);
    await h.accept(0);
    assert.equal(h.requests.length, 2, 'B follows as soon as A is answered');
    assert.deepEqual(h.requests[0].doc, A, 'The submitted snapshot must not change');
    assert.deepEqual(h.requests[1].doc, B);
    await h.accept(1);
    await flushing;
    assert.equal(h.saver.busy, false);
    assert.deepEqual(h.saver.working, B);
    assert.equal(h.last().saving, false);
  },

  // the newer value of one parameter wins, including going back to the value the server already has
  async same_field() {
    for (const value of [4, 1]) {
      const h = await harness();
      const make = v => doc({chains: {...doc().chains, merge: {strength: v / 4}}});
      h.saver.change(make(2));
      await h.fire();
      h.saver.change(make(value));
      await h.fail(0, conflict());
      await h.fire();
      assert.deepEqual(h.requests[1].doc, make(value));
      await h.accept(1);
      assert.deepEqual(h.saver.working, make(value));
    }
  },

  // a lost connection is retried like a conflict
  async offline() {
    const h = await harness();
    const A = doc({protect_space: false});
    h.saver.change(A);
    await h.fire();
    await h.fail(0, new TypeError('Failed to fetch'));
    assert.match(h.last().failed, /Failed to fetch/);
    assert.equal(h.last().retrying, true);
    await h.fire();
    await h.fail(1, new TypeError('Failed to fetch'));
    await h.fire();
    await h.accept(2);
    assert.deepEqual(h.requests[2].doc, A);
    assert.equal(h.saver.busy, false);
  },

  // two wavelet layers edited one after the other both survive a save in flight, failed or not
  async wavelets() {
    for (const fails of [false, true]) {
      const h = await harness();
      h.saver.change(wavelets([.06, 0, 0, 0]));
      await h.fire();
      h.saver.change(wavelets([.06, .11, 0, 0]));
      if (fails) { await h.fail(0, conflict()); await h.fire(); }
      else await h.accept(0);
      assert.deepEqual(h.requests[0].doc.chains.luminance[0].params.biases, [.06, 0, 0, 0]);
      assert.deepEqual(h.requests[1].doc.chains.luminance[0].params.biases, [.06, .11, 0, 0]);
      await h.accept(1);
      assert.deepEqual(h.saver.working.chains.luminance[0].params.biases, [.06, .11, 0, 0]);
    }
  },

  // the server's document (another window's edit) replaces the one shown only when nothing of ours waits
  async later_edit() {
    const h = await harness();
    const mine = doc({protect_space: false}), theirs = doc({chains: {...doc().chains, merge: {strength: .3}}});
    h.saver.adopt(doc());
    h.saver.change(mine);
    h.saver.adopt(theirs);
    assert.deepEqual(h.saver.working, mine, 'Pending: the server document waits');
    await h.fire();
    h.saver.adopt(theirs);
    assert.deepEqual(h.saver.working, mine, 'In flight: the server document waits');
    await h.accept(0);
    h.saver.adopt(theirs);
    assert.deepEqual(h.saver.working, theirs, 'Saved: the server document is shown');
  },

  // a document the server refuses waits for the next edit instead of being sent every 2 s
  async refused() {
    const h = await harness();
    const bad = doc({protect_space: 'yes'}), good = doc({protect_space: false});
    h.saver.change(bad);
    await h.fire();
    await h.fail(0, refused());
    assert.equal(h.timers.size, 0, 'Nothing is sent again on its own');
    assert.equal(h.last().retrying, false);
    assert.equal(h.last().failed, 'Block 1: black must be below white.');
    assert.ok(h.saver.busy, 'The refused document is still unsaved');
    assert.deepEqual(h.saver.working, bad, 'and still on screen');
    h.saver.change(good);
    await h.fire();
    assert.deepEqual(h.requests[1].doc, good);
    await h.accept(1);
    assert.equal(h.last().failed, null);
    assert.equal(h.saver.busy, false);
  },

  // refused, but the user has already made a newer document: that one is sent
  async refused_with_newer_edit() {
    const h = await harness();
    h.saver.change(doc({protect_space: 'yes'}));
    await h.fire();
    h.saver.change(doc({protect_space: false}));
    await h.fail(0, refused());
    assert.equal(h.timer().ms, 300);
    await h.fire();
    assert.deepEqual(h.requests[1].doc, doc({protect_space: false}));
    await h.accept(1);
    assert.equal(h.saver.busy, false);
  },

  // the timers are the browser's own when none are given: called as methods they would throw
  // "Illegal invocation" in a browser; here the default must at least schedule and cancel
  async default_timers() {
    const source = fs.readFileSync(path.join(__dirname, '../ui/save.js'), 'utf8');
    const {Autosave} = await import('data:text/javascript,' + encodeURIComponent(source));
    const sent = [];
    const saver = new Autosave({send: async d => { sent.push(d); }, delay: 5});
    saver.change(doc());
    await new Promise(resolve => setTimeout(resolve, 40));
    assert.equal(sent.length, 1);
    assert.equal(saver.busy, false);
    const schedule = saver.schedule.toString();
    assert.ok(!/^function setTimeout|\[native code\]/.test(schedule), 'The default timer is wrapped, not setTimeout itself');
  },
};

const name = process.argv[2];
if (!scenarios[name]) { console.error(`Unknown scenario ${name}`); process.exit(2); }
scenarios[name]().then(() => console.log(`PASS ${name}`), e => { console.error(e.stack || e); process.exit(1); });
