// Debounced autosave of one document kind (the chain, the geometry) with revision checks (plan phase 7, item 6).
//
// The editor shows `working`, the latest document the user made, until the server has it: a failed save never
// puts the server's older document back on screen (BUGS.md bug 11). After a failure the unsent document stays
// pending and is sent again — newer edits made meanwhile win — and onState says so, so the user is told. A
// conflict or a lost connection is retried on its own; a document the server refuses (an error with
// retry === false, such as levels with black above white) waits for the user's next edit.
export class Autosave {
  // schedule and cancel default to the timers, wrapped: called as this.schedule(), setTimeout itself would be
  // invoked on this object, which browsers refuse ("Illegal invocation")
  constructor({send, delay = 300, retry = 2000, schedule = (fn, ms) => setTimeout(fn, ms), cancel = id => clearTimeout(id),
               onState = () => {}}) {
    Object.assign(this, {send, delay, retry, schedule, cancel, onState});
    this.working = null;    // what the user sees and edits
    this.pending = null;    // not sent yet
    this.inflight = null;   // sent, not answered
    this.failed = null;     // the last failure's message, until a save succeeds
    this.retrying = false;  // whether the failed document is sent again without a new edit
    this.timer = null;
  }

  // A new document from the editor: shown at once, saved after `delay` ms of quiet.
  change(doc) {
    this.working = doc;
    this.pending = doc;
    this.report();
    this.arm(this.delay);
  }

  // The server's document, when nothing of ours is waiting (a refresh, another client's change).
  adopt(doc) {
    if (this.pending === null && this.inflight === null) this.working = doc;
  }

  get busy() { return this.pending !== null || this.inflight !== null; }

  arm(ms) {
    if (this.timer !== null) this.cancel(this.timer);
    this.timer = this.schedule(() => { this.timer = null; this.flush().catch(() => {}); }, ms);
  }

  async flush() {
    if (this.timer !== null) { this.cancel(this.timer); this.timer = null; }
    if (this.inflight !== null || this.pending === null) return;
    const doc = this.pending;
    this.pending = null;
    this.inflight = doc;
    this.report();
    try {
      await this.send(doc);
      this.failed = null;
      this.retrying = false;
    } catch (e) {
      // the rejected document goes back unless the user has made a newer one; either way it is sent again
      const newer = this.pending !== null;
      if (!newer) this.pending = doc;
      this.failed = e && e.message ? e.message : String(e);
      this.retrying = !(e && e.retry === false) && !newer;
      this.inflight = null;
      this.report();
      if (this.retrying) this.arm(this.retry);
      throw e;
    }
    this.inflight = null;
    this.report();
    if (this.pending !== null) await this.flush();
  }

  report() {
    this.onState({saving: this.busy, failed: this.failed, retrying: this.retrying, working: this.working});
  }
}
