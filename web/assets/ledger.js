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
      const name = String(n).padStart(6, "0") + ".jsonl";
      const res = await fetch(base + name, { cache: "no-cache" });
      if (res.status === 404) break;
      if (!res.ok) throw new Error("could not fetch " + name + " (" + res.status + ")");
      for (const line of (await res.text()).split("\n")) if (line.trim()) entries.push(JSON.parse(line));
    }
    return entries;
  }

  const api = { canonical: canonical, entryId: entryId, verify: verify, verifySignature: verifySignature, load: load };
  root.PriorLedger = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
