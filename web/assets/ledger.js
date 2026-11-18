/* Prior Ledger — the ledger's rules for the browser.
 *
 * This is a second implementation of server/priorledger/core.py's canonical form,
 * so a reader can check the chain without trusting any server. The two must
 * produce identical bytes; tests/test_parity.py runs both and compares them.
 */
(function (root) {
  "use strict";

  // JSON with sorted keys and no whitespace — byte-identical to Python's
  // json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).
  function canonical(value) {
    if (value === null || typeof value !== "object") return JSON.stringify(value);
    if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
    const keys = Object.keys(value).sort();
    return "{" + keys.map(function (k) { return JSON.stringify(k) + ":" + canonical(value[k]); }).join(",") + "}";
  }

  function without(entry, fields) {
    const out = {};
    for (const k of Object.keys(entry)) if (fields.indexOf(k) === -1) out[k] = entry[k];
    return out;
  }

  function hex(buffer) {
    return Array.from(new Uint8Array(buffer)).map(function (b) { return b.toString(16).padStart(2, "0"); }).join("");
  }

  function unhex(s) {
    const out = new Uint8Array(s.length / 2);
    for (let i = 0; i < out.length; i++) out[i] = parseInt(s.substr(i * 2, 2), 16);
    return out;
  }

  async function sha256(text) {
    return hex(await root.crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
  }

  async function entryId(entry) {
    return sha256(canonical(without(entry, ["id", "sig"])));
  }

  // Ed25519 in WebCrypto is recent; when a browser lacks it we say so instead of guessing.
  async function signaturesSupported() {
    try {
      await root.crypto.subtle.importKey("raw", new Uint8Array(32), { name: "Ed25519" }, false, ["verify"]);
      return true;
    } catch (e) {
      return false;
    }
  }

  async function verifySignature(entry) {
    const key = entry.actor && entry.actor.key;
    if (!key || !/^ed25519:[0-9a-f]{64}$/.test(key) || !/^[0-9a-f]{128}$/.test(entry.sig || "")) return false;
    const pub = await root.crypto.subtle.importKey("raw", unhex(key.slice(8)), { name: "Ed25519" }, false, ["verify"]);
    const content = canonical(without(entry, ["id", "sig", "prev", "ts"]));
    return root.crypto.subtle.verify({ name: "Ed25519" }, pub, unhex(entry.sig), new TextEncoder().encode(content));
  }

  // Check every id, the chain, references and (where the browser can) signatures.
  async function verify(entries) {
    const canSign = await signaturesSupported();
    const seen = new Set();
    let prev = null;
    let signed = 0;
    for (let i = 0; i < entries.length; i++) {
      const e = entries[i];
      const fail = function (why) { return { ok: false, at: i, reason: why, checkedSignatures: canSign }; };
      if ((await entryId(e)) !== e.id) return fail("its id does not match its content");
      if (e.prev !== prev) return fail("it does not point to the entry before it");
      const refs = (e.basis || []).concat(e.supersedes ? [e.supersedes] : []);
      for (const r of refs) if (!seen.has(r)) return fail("it refers to an entry that does not come before it");
      if (e.actor && e.actor.type === "agent" && !e.sig) return fail("an agent entry is unsigned");
      if (e.sig && canSign) {
        if (!(await verifySignature(e))) return fail("its signature does not match");
        signed++;
      }
      seen.add(e.id);
      prev = e.id;
    }
    return { ok: true, count: entries.length, head: prev, checkedSignatures: canSign, signed: signed };
  }

  async function load(base) {
    const entries = [];
    for (let n = 0; n < 100000; n++) {
      const res = await fetch(base + fileName(n), { cache: "no-cache" });
      if (res.status === 404) break;
      if (!res.ok) throw new Error("could not fetch " + fileName(n) + " (" + res.status + ")");
      for (const line of (await res.text()).split("\n")) if (line.trim()) entries.push(JSON.parse(line));
    }
    return entries;
  }

  // ---------------------------------------------------------------- reading a large ledger
  //
  // The ledger is split into files of LINES_PER_FILE entries (server/priorledger/core.py).
  // A Reader checks the newest file in full and older files only when they are opened.
  // Every file is checked on its own (ids, chain inside the file, signatures), and the
  // joints between files and references into earlier files are checked as soon as both
  // sides are loaded. verifyAll() loads everything and leaves nothing unchecked.

  const LINES_PER_FILE = 10000;

  function fileName(n) { return String(n).padStart(6, "0") + ".jsonl"; }

  function parseLines(text) {
    const out = [];
    for (const line of text.split("\n")) if (line.trim()) out.push(JSON.parse(line));
    return out;
  }

  async function exists(base, n) {
    let res = await fetch(base + fileName(n), { method: "HEAD", cache: "no-cache" });
    if (res.status === 405 || res.status === 501) res = await fetch(base + fileName(n), { cache: "no-cache" });
    if (res.status === 404) return false;
    if (!res.ok) throw new Error("could not reach " + fileName(n) + " (" + res.status + ")");
    return true;
  }

  // How many files there are, in about 2·log2(n) requests.
  async function fileCount(base) {
    if (!(await exists(base, 0))) return 0;
    let hi = 1;
    while (await exists(base, hi)) hi *= 2;
    let lo = hi === 1 ? 0 : hi / 2;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (await exists(base, mid)) lo = mid; else hi = mid;
    }
    return lo + 1;
  }

  // Check one file on its own. knownIds: ids of entries in earlier files that are loaded.
  async function verifySegment(entries, opts) {
    const file = opts.file, start = file * (opts.lines || LINES_PER_FILE);
    const seen = new Set();
    const pending = [];
    let prev = null, signed = 0;
    for (let i = 0; i < entries.length; i++) {
      const e = entries[i], n = start + i;
      const fail = function (why) { return { ok: false, at: n, reason: why }; };
      if ((await entryId(e)) !== e.id) return fail("its id does not match its content");
      if (i === 0) {
        if (file === 0 && e.prev !== null) return fail("the first entry points to an entry before it");
        if (file > 0 && !/^[0-9a-f]{64}$/.test(e.prev || "")) return fail("it does not point to the entry before it");
      } else if (e.prev !== prev) {
        return fail("it does not point to the entry before it");
      }
      const refs = (e.basis || []).concat(e.supersedes ? [e.supersedes] : []);
      for (const r of refs) {
        if (seen.has(r) || (opts.knownIds && opts.knownIds.has(r))) continue;
        if (file === 0) return fail("it refers to an entry that does not come before it");
        pending.push({ id: r, at: n });
      }
      if (e.actor && e.actor.type === "agent" && !e.sig) return fail("an agent entry is unsigned");
      if (e.sig && opts.canSign && !opts.skipSignatures) {
        if (!(await verifySignature(e))) return fail("its signature does not match");
        signed++;
      }
      seen.add(e.id);
      prev = e.id;
    }
    return { ok: true, count: entries.length, first: entries.length ? entries[0] : null, lastId: prev,
             ids: seen, pending: pending, signed: signed };
  }

  function Reader(base, options) {
    this.base = base;
    this.memory = (options && options.memory) || null;   // { has(hash), add(hash) } for files already checked
    this.files = [];
    this.count = 0;
    this.canSign = false;
    this.failure = null;
    this.signed = 0;
    this.lines = (options && options.linesPerFile) || LINES_PER_FILE;   // only tests change this
  }

  Reader.prototype.init = async function () {
    this.canSign = await signaturesSupported();
    this.count = await fileCount(this.base);
    this.files = [];
    for (let k = 0; k < this.count; k++) this.files.push({ file: k, state: "unloaded", entries: null, starts: null });
    return this.count;
  };

  Reader.prototype.total = function () {
    if (!this.count) return 0;
    const last = this.files[this.count - 1];
    return (this.count - 1) * this.lines + (last.entries ? last.entries.length : 0);
  };

  Reader.prototype.loadedRange = function () {
    let first = null;
    for (let k = this.count - 1; k >= 0 && this.files[k].state === "verified"; k--) first = k;
    return first;
  };

  Reader.prototype.allVerified = function () {
    return this.count > 0 && this.files.every(function (f) { return f.state === "verified"; }) && !this.failure;
  };

  Reader.prototype._fail = function (at, reason) {
    if (!this.failure) this.failure = { at: at, reason: reason };
    return this.failure;
  };

  // The timestamp each file starts at, read from its first line with a ranged request.
  Reader.prototype.starts = async function (k) {
    const f = this.files[k];
    if (f.entries) return f.entries.length ? f.entries[0].ts : null;
    if (f.starts) return f.starts;
    const res = await fetch(this.base + fileName(k), { headers: { Range: "bytes=0-65535" }, cache: "no-cache" });
    if (!res.ok) throw new Error("could not fetch " + fileName(k) + " (" + res.status + ")");
    const text = await res.text();
    const line = text.split("\n")[0];
    try { f.starts = JSON.parse(line).ts; } catch (e) { f.starts = null; }
    return f.starts;
  };

  Reader.prototype.load = async function (k) {
    const f = this.files[k];
    if (!f || f.state === "verified") return f;
    if (this.failure) return f;
    if (f.loading) return f.loading;
    const self = this;
    f.loading = (async function () {
      const res = await fetch(self.base + fileName(k), { cache: k === self.count - 1 ? "no-cache" : "default" });
      if (!res.ok) throw new Error("could not fetch " + fileName(k) + " (" + res.status + ")");
      const text = await res.text();
      const hash = await sha256(text);
      const entries = parseLines(text);
      if (k < self.count - 1 && entries.length !== self.lines) {
        self._fail(k * self.lines + entries.length, "the file " + fileName(k) + " is not full, so entries are missing");
        f.state = "failed"; return f;
      }
      const known = new Set();
      for (let j = 0; j < k; j++) if (self.files[j].ids) self.files[j].ids.forEach(function (id) { known.add(id); });
      const remembered = !!(self.memory && self.memory.has(hash));
      const r = await verifySegment(entries, { file: k, lines: self.lines, knownIds: known, canSign: self.canSign, skipSignatures: remembered });
      if (!r.ok) { self._fail(r.at, r.reason); f.state = "failed"; return f; }
      f.entries = entries; f.ids = r.ids; f.pending = r.pending; f.lastId = r.lastId; f.hash = hash;
      f.signed = remembered ? entries.filter(function (e) { return e.sig; }).length : r.signed;
      f.state = "verified";
      // Joints with neighbours that are already loaded.
      const before = self.files[k - 1], after = self.files[k + 1];
      if (before && before.state === "verified" && entries.length && entries[0].prev !== before.lastId)
        self._fail(k * self.lines, "it does not point to the last entry of the file before it");
      if (after && after.state === "verified" && after.entries.length && after.entries[0].prev !== f.lastId)
        self._fail((k + 1) * self.lines, "it does not point to the last entry of the file before it");
      // References from later files into this one are now confirmed.
      for (let j = k + 1; j < self.count; j++) {
        const g = self.files[j];
        if (g.pending) g.pending = g.pending.filter(function (p) { return !f.ids.has(p.id); });
      }
      if (f.pending) f.pending = f.pending.filter(function (p) { return !known.has(p.id); });
      self.signed = self.files.reduce(function (s, x) { return s + (x.signed || 0); }, 0);
      if (!self.failure && self.memory && !remembered && k < self.count - 1) self.memory.add(hash);
      return f;
    })();
    try { return await f.loading; } finally { f.loading = null; }
  };

  // Load and check everything from the founding to the head.
  Reader.prototype.verifyAll = async function (progress) {
    for (let k = 0; k < this.count; k++) {
      if (this.failure) break;
      if (progress && progress(k, this.count) === false) return { stopped: true };
      await this.load(k);
    }
    if (!this.failure) {
      for (const f of this.files) {
        if (f.pending && f.pending.length) { this._fail(f.pending[0].at, "it refers to an entry that does not come before it"); break; }
      }
    }
    return { ok: !this.failure, failure: this.failure };
  };

  // Entries of loaded files, in order, as { e, n }.
  Reader.prototype.items = function () {
    const out = [];
    for (const f of this.files) {
      if (f.state !== "verified") continue;
      const start = f.file * this.lines;
      for (let i = 0; i < f.entries.length; i++) out.push({ e: f.entries[i], n: start + i });
    }
    return out;
  };

  const api = { canonical: canonical, entryId: entryId, verify: verify, verifySignature: verifySignature, load: load,
                verifySegment: verifySegment, fileCount: fileCount, fileName: fileName, Reader: Reader,
                LINES_PER_FILE: LINES_PER_FILE, signaturesSupported: signaturesSupported };
  root.PriorLedger = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
