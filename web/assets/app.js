/* Prior Ledger — the register page. Entry text is untrusted: it is only ever set with textContent. */
(function () {
  "use strict";

  const cfg = window.PRIORLEDGER_CONFIG || {};
  const PAGE = 50;
  const $ = function (id) { return document.getElementById(id); };

  const state = { entries: [], byId: new Map(), supersededBy: new Map(), shown: PAGE, subject: null };

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) for (const k of Object.keys(attrs)) {
      if (k === "text") node.textContent = attrs[k];
      else if (k === "class") node.className = attrs[k];
      else node.setAttribute(k, attrs[k]);
    }
    for (const c of children || []) if (c) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    return node;
  }

  const num = function (n) { return "№ " + String(n).padStart(6, "0"); };
  const short = function (id) { return id.slice(0, 8) + "…" + id.slice(-6); };
  const anchor = function (id) { return "e-" + id.slice(0, 12); };

  function when(ts) {
    const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(ts || "");
    return m ? [m[1], m[2] + " UTC"] : [ts || "", ""];
  }

  function refLink(id, label) {
    const target = state.byId.get(id);
    return el("a", { href: "#" + anchor(id), text: label || (target ? num(target.n) : short(id)) });
  }

  // ---------------------------------------------------------------- one entry

  function renderEntry(item) {
    const e = item.e;
    const by = state.supersededBy.get(e.id);
    const li = el("li", { class: "entry" + (by ? " superseded" : ""), id: anchor(e.id) });
    const [day, time] = when(e.ts);

    li.appendChild(el("div", { class: "margin" }, [
      el("a", { class: "number", href: "#" + anchor(e.id), text: num(item.n), "aria-label": "Entry " + item.n }),
      el("span", { class: "when", text: day }),
      time ? el("span", { class: "when", text: time }) : null,
    ]));

    const actor = e.actor || {};
    const actorText = actor.type + ":" + actor.id + (actor.key ? " · key " + actor.key.slice(8, 14) + "…" : "");
    const subject = el("a", { class: "subject", href: "#", text: e.subject });
    subject.addEventListener("click", function (ev) { ev.preventDefault(); setSubject(e.subject); });

    const main = el("div", { class: "content" }, [
      el("div", { class: "meta" }, [
        el("span", { text: e.kind }), el("span", { text: "·", "aria-hidden": "true" }), subject,
        el("span", { text: "·", "aria-hidden": "true" }), el("span", { class: "actor", text: actorText }),
        el("span", { class: "stamp " + e.provenance, text: e.provenance }),
      ]),
    ]);

    if (by) {
      main.appendChild(el("p", { class: "notice" }, ["Superseded by ", refLink(by), ". Read that entry before relying on this one."]));
    }
    if (e.supersedes) {
      main.appendChild(el("p", { class: "notice quiet" }, [(e.kind === "correction" ? "Corrects " : "Supersedes ") , refLink(e.supersedes), "."]));
    }

    main.appendChild(el("p", { class: "body", text: e.body }));

    const copy = el("button", { type: "button", class: "copy", text: "id " + short(e.id), title: "Copy the full id" });
    copy.addEventListener("click", function () {
      navigator.clipboard.writeText(e.id).then(function () {
        copy.textContent = "copied";
        setTimeout(function () { copy.textContent = "id " + short(e.id); }, 1400);
      });
    });
    const basis = el("span", null, ["basis "]);
    if (e.basis && e.basis.length) e.basis.forEach(function (b, i) { if (i) basis.appendChild(document.createTextNode(", ")); basis.appendChild(refLink(b)); });
    else basis.appendChild(document.createTextNode("—"));

    main.appendChild(el("div", { class: "foot" }, [
      copy,
      el("span", null, ["prev ", e.prev ? refLink(e.prev) : "—"]),
      basis,
      e.sig ? el("span", { text: "signed" }) : null,
    ]));

    li.appendChild(main);
    return li;
  }

  // ---------------------------------------------------------------- filtering

  function matches(item) {
    const e = item.e;
    const q = $("q").value.trim().toLowerCase();
    if (state.subject && e.subject !== state.subject) return false;
    if ($("kind").value && e.kind !== $("kind").value) return false;
    if ($("prov").value && e.provenance !== $("prov").value) return false;
    if (!$("superseded").checked && state.supersededBy.has(e.id)) return false;
    if (!q) return true;
    if (/^[0-9a-f]{6,64}$/.test(q) && e.id.startsWith(q)) return true;
    const hay = (e.body + " " + e.subject + " " + e.kind + " " + (e.actor && e.actor.id)).toLowerCase();
    return q.split(/\s+/).every(function (w) { return hay.indexOf(w) !== -1; });
  }

  function render() {
    const list = $("entries");
    const hits = state.entries.filter(matches).reverse();
    list.replaceChildren();
    for (const item of hits.slice(0, state.shown)) list.appendChild(renderEntry(item));
    $("empty").hidden = hits.length !== 0 || state.entries.length === 0;
    $("more").hidden = hits.length <= state.shown;

    const note = $("filter-note");
    if (state.subject) {
      const clear = el("button", { type: "button", text: "show all subjects" });
      clear.addEventListener("click", function () { setSubject(null); });
      note.replaceChildren("Subject ", el("strong", { text: state.subject }), " · " + hits.length + (hits.length === 1 ? " entry" : " entries"), clear);
      note.hidden = false;
    } else {
      note.hidden = true;
    }
  }

  function setSubject(subject) {
    state.subject = subject;
    state.shown = PAGE;
    render();
    $("register").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------------------------------------------------------------- deep links

  function followHash() {
    const m = /^#e-([0-9a-f]{12})$/.exec(location.hash);
    if (!m) return;
    const item = state.entries.find(function (it) { return it.e.id.startsWith(m[1]); });
    if (!item) return;
    if (!document.getElementById("e-" + m[1])) {
      $("q").value = ""; $("kind").value = ""; $("prov").value = ""; $("superseded").checked = true;
      state.subject = null;
      state.shown = Math.max(state.shown, state.entries.length - item.n + 1);
      render();
    }
    const node = document.getElementById("e-" + m[1]);
    document.querySelectorAll(".entry.target").forEach(function (n) { n.classList.remove("target"); });
    node.classList.add("target");
    node.scrollIntoView({ block: "start" });
  }

  // ---------------------------------------------------------------- status

  function setStatus(parts) {
    $("status").replaceChildren.apply($("status"), parts.map(function (p) { return typeof p === "string" ? el("span", { text: p }) : p; }));
  }

  async function start() {
    if (cfg.mcpUrl) $("mcp-url").textContent = cfg.mcpUrl;
    if (cfg.repo) $("repo-link").href = cfg.repo;
    for (const m of cfg.mirrors || []) {
      const host = new URL(m).hostname.replace(/^archive\./, "");
      $("mirrors").appendChild(el("a", { href: m, text: host }));
    }

    if (!window.crypto || !window.crypto.subtle) {
      setStatus([el("span", { class: "bad", text: "This page needs a secure connection (https) to verify the ledger." })]);
      return;
    }

    let entries = [], source = null, lastError = null;
    for (const base of cfg.ledgerBases || ["ledger/"]) {
      try {
        entries = await window.PriorLedger.load(base);
        if (entries.length) { source = base; break; }
      } catch (err) { lastError = err; }
    }
    if (!source) {
      setStatus([el("span", { class: "bad", text: "Could not read the ledger" + (lastError ? ": " + lastError.message : ".") })]);
      return;
    }

    state.entries = entries.map(function (e, n) { return { e: e, n: n }; });
    for (const it of state.entries) {
      state.byId.set(it.e.id, it);
      if (it.e.supersedes) state.supersededBy.set(it.e.supersedes, it.e.id);
    }

    const result = await window.PriorLedger.verify(entries);
    if (result.ok) {
      const head = el("a", { href: "#" + anchor(result.head), text: "head " + short(result.head) });
      setStatus([
        el("span", { class: "ok", text: "✓ chain verified in this browser" }),
        result.count + (result.count === 1 ? " entry" : " entries"),
        result.signed ? result.signed + " signatures checked" : (result.checkedSignatures ? "" : "signatures: your browser cannot check Ed25519"),
        head,
      ].filter(Boolean));
    } else {
      setStatus([el("span", { class: "bad", text: "✗ verification failed at " + num(result.at) + ": " + result.reason + ". Do not rely on this copy." })]);
    }

    render();
    followHash();
  }

  for (const id of ["q", "kind", "prov", "superseded"]) {
    $(id).addEventListener("input", function () { state.shown = PAGE; render(); });
  }
  $("more").addEventListener("click", function () { state.shown += PAGE; render(); });
  window.addEventListener("hashchange", followHash);
  start();
})();
