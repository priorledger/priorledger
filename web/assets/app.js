/* Prior Ledger — the register page. Entry text is untrusted: it is only ever set with textContent. */
(function () {
  "use strict";

  const cfg = window.PRIORLEDGER_CONFIG || {};
  const L = window.PriorLedger;
  const PAGE = 50;
  const KINDS = ["decision", "claim", "correction", "dispute", "supersession", "succession"];
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const DOWS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const $ = function (id) { return document.getElementById(id); };

  const state = {
    reader: null,
    items: [], byId: new Map(), supersededBy: new Map(), citedBy: new Map(),
    shown: PAGE, view: "stream",
    cal: { y: null, m: null, day: null, kind: "" },
    subjects: { sel: null, sort: "az" },
    busy: null,              // shown while older files are being opened
    verifyingAll: null,      // { k, n } during "verify everything"
    stopAll: false,
  };

  // ---------------------------------------------------------------- small helpers

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) for (const k of Object.keys(attrs)) {
      const v = attrs[k];
      if (v === null || v === undefined || v === false) continue;
      if (k === "text") node.textContent = v;
      else if (k === "class") node.className = v;
      else if (k === "on") for (const ev of Object.keys(v)) node.addEventListener(ev, v[ev]);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children || []) if (c !== null && c !== undefined && c !== false) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    return node;
  }

  const num = function (n) { return "№" + n; };
  const short = function (id) { return id.slice(0, 8) + "…" + id.slice(-6); };
  const pad = function (n) { return String(n).padStart(2, "0"); };
  const entryHref = function (id) { return "#/entry/" + id.slice(0, 12); };
  const subjectHref = function (s) { return "#/subjects/" + encodeURIComponent(s); };
  const dayHref = function (key) { return "#/calendar/" + key; };

  // Days and times are shown in the reader's own time zone; entries are stamped in UTC.
  function local(ts) { const d = new Date(ts); return isNaN(d) ? null : d; }
  function dayKey(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  function timeOf(d) { return pad(d.getHours()) + ":" + pad(d.getMinutes()); }
  function longDay(d) { return DOWS[d.getDay()] + " " + d.getDate() + " " + MONTHS[d.getMonth()] + " " + d.getFullYear(); }
  function shortDay(d) { return d.getDate() + " " + MONTHS[d.getMonth()].slice(0, 3) + " " + d.getFullYear(); }
  function parseKey(key) { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(key); return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null; }
  function excerpt(text, n) { const t = (text || "").replace(/\s+/g, " ").trim(); return t.length <= n ? t : t.slice(0, n).replace(/\s+\S*$/, "") + "…"; }
  function plural(n, one, many) { return n.toLocaleString() + " " + (n === 1 ? one : many); }

  function stamp(prov) { return el("span", { class: "stamp " + prov, text: prov }); }

  function refLink(id) {
    const t = state.byId.get(id);
    return el("a", { href: entryHref(id), text: t ? num(t.n) : short(id), title: t ? t.e.subject : "in an earlier file" });
  }

  function joinLinks(ids) {
    const out = [];
    ids.forEach(function (id, i) { if (i) out.push(", "); out.push(refLink(id)); });
    return out;
  }

  function allLoaded() { return !!(state.reader && state.reader.allVerified()); }
  function loadedFrom() { return state.items.length ? state.items[0].n : 0; }

  // ---------------------------------------------------------------- the index of loaded entries

  function reindex() {
    state.items = state.reader.items();
    state.byId = new Map(); state.supersededBy = new Map(); state.citedBy = new Map();
    for (const it of state.items) {
      it.d = local(it.e.ts);
      it.key = it.d ? dayKey(it.d) : "";
      state.byId.set(it.e.id, it);
      if (it.e.supersedes) state.supersededBy.set(it.e.supersedes, it.e.id);
      for (const b of (it.e.basis || []).concat(it.e.supersedes ? [it.e.supersedes] : [])) {
        if (!state.citedBy.has(b)) state.citedBy.set(b, []);
        state.citedBy.get(b).push(it.e.id);
      }
    }
  }

  function findByPrefix(prefix) {
    for (const it of state.items) if (it.e.id.startsWith(prefix)) return it;
    return null;
  }

  // Open older files, newest first, until test() is satisfied or the founding is reached.
  async function openBackwards(test, label) {
    const r = state.reader;
    for (let k = r.count - 1; k >= 0; k--) {
      if (test()) break;
      if (r.files[k].state === "verified") continue;
      state.busy = (label || "Opening") + " " + L.fileName(k) + "…";
      renderStatus();
      await r.load(k);
      reindex();
      if (r.failure) break;
    }
    state.busy = null;
    renderStatus();
    return test();
  }

  // Open older files until the given month is fully covered. Loaded files always run
  // unbroken from the newest back, so every view can say "from №n onward".
  async function openMonth(y, m) {
    const from = new Date(y, m, 1);
    await openBackwards(function () { return allLoaded() || (state.items.length && state.items[0].d < from); },
                        "Opening and checking, for " + MONTHS[m] + " " + y + ",");
  }

  async function verifyEverything() {
    const r = state.reader;
    state.stopAll = false;
    state.verifyingAll = { k: 0, n: r.count };
    renderStatus();
    await r.verifyAll(function (k, n) {
      state.verifyingAll = { k: k + 1, n: n };
      reindex();
      renderStatus();
      return !state.stopAll;
    });
    state.verifyingAll = null;
    reindex();
    renderStatus();
    if (!r.failure) route();
  }

  // ---------------------------------------------------------------- status line

  function setStatus(parts) {
    $("status").replaceChildren.apply($("status"), parts.filter(Boolean).map(function (p) { return typeof p === "string" ? el("span", { text: p }) : p; }));
  }

  function renderStatus() {
    const r = state.reader;
    if (!r) return;
    if (r.failure) {
      setStatus([el("span", { class: "bad", text: "✗ " + num(r.failure.at) + ": " + r.failure.reason + ". The copy of the ledger this page received has been altered or damaged. Nothing here is shown as verified." })]);
      for (const v of ["stream", "calendar", "subjects", "entry"]) $("view-" + v).hidden = true;
      partial(null);
      return;
    }
    const sig = r.canSign ? (r.signed ? plural(r.signed, "signature checked", "signatures checked") : "") : "signatures: your browser cannot check Ed25519";
    const last = state.items.length ? state.items[state.items.length - 1] : null;
    const head = last ? el("a", { href: entryHref(last.e.id), text: "head " + short(last.e.id) }) : null;
    if (state.verifyingAll) {
      const stop = el("button", { type: "button", text: "Stop", on: { click: function () { state.stopAll = true; } } });
      setStatus(["Verifying everything… file " + state.verifyingAll.k + " of " + state.verifyingAll.n, plural(state.items.length, "entry", "entries") + " so far, all good", stop]);
      return;
    }
    if (r.allVerified()) {
      setStatus([el("span", { class: "ok", text: "✓ every entry verified in this browser" }), plural(r.total(), "entry", "entries"), sig, head]);
    } else {
      const earlier = loadedFrom();
      const all = el("button", { type: "button", text: "Verify everything", on: { click: verifyEverything } });
      setStatus([
        el("span", { class: "ok", text: "✓ newest " + plural(r.total() - earlier, "entry", "entries") + " verified in this browser" }),
        plural(earlier, "earlier entry", "earlier entries") + " checked as you open them",
        sig, head, all,
      ]);
    }
    if (state.busy) $("status").appendChild(el("span", { class: "pending", text: state.busy }));
  }

  function partial(message, action) {
    const p = $("partial");
    if (!message) { p.hidden = true; p.replaceChildren(); return; }
    p.replaceChildren(el("span", { text: message }), action ? el("button", { type: "button", text: action, on: { click: verifyEverything } }) : null);
    p.hidden = false;
  }

  function needAllNote(what) {
    if (allLoaded()) { partial(null); return; }
    partial(what + " from " + num(loadedFrom()) + " onward. Earlier files are opened and checked when you ask.", "Open and check everything");
  }

  function copyButton(text, label) {
    const b = el("button", { type: "button", class: "copy", text: label, title: "Copy" });
    b.addEventListener("click", function () {
      navigator.clipboard.writeText(text).then(function () {
        b.textContent = "copied";
        setTimeout(function () { b.textContent = label; }, 1400);
      });
    });
    return b;
  }

  // ---------------------------------------------------------------- stream

  function renderEntry(item) {
    const e = item.e;
    const by = state.supersededBy.get(e.id);
    const li = el("li", { class: "entry" + (by ? " superseded" : ""), id: "e-" + e.id.slice(0, 12) });
    li.appendChild(el("div", { class: "margin" }, [
      el("a", { class: "number", href: entryHref(e.id), text: num(item.n), "aria-label": "Entry " + item.n }),
      item.d ? el("a", { class: "when", href: dayHref(item.key), text: shortDay(item.d) }) : null,
      item.d ? el("span", { class: "when", text: timeOf(item.d) }) : null,
    ]));
    const actor = e.actor || {};
    const main = el("div", { class: "content" }, [
      el("div", { class: "meta" }, [
        el("span", { text: e.kind }), el("span", { text: "·", "aria-hidden": "true" }),
        el("a", { class: "subject", href: subjectHref(e.subject), text: e.subject }),
        el("span", { text: "·", "aria-hidden": "true" }),
        el("span", { class: "actor", text: actor.type + ":" + actor.id + (actor.key ? " · key " + actor.key.slice(8, 14) + "…" : "") }),
        stamp(e.provenance),
      ]),
    ]);
    if (by) main.appendChild(el("p", { class: "notice" }, ["Superseded by ", refLink(by), ". Read that entry before relying on this one."]));
    if (e.supersedes) main.appendChild(el("p", { class: "notice quiet" }, [(e.kind === "correction" ? "Corrects " : "Supersedes "), refLink(e.supersedes), "."]));
    main.appendChild(el("p", { class: "body", text: e.body }));
    main.appendChild(el("div", { class: "foot" }, [
      copyButton(e.id, "id " + short(e.id)),
      el("span", null, ["prev ", e.prev ? refLink(e.prev) : "—"]),
      el("span", null, ["basis "].concat(e.basis && e.basis.length ? joinLinks(e.basis) : ["—"])),
      e.sig ? el("span", { text: "signed" }) : null,
      el("a", { href: entryHref(e.id), text: "open entry" }),
    ]));
    li.appendChild(main);
    return li;
  }

  function matches(item) {
    const e = item.e;
    const q = $("q").value.trim().toLowerCase();
    if ($("kind").value && e.kind !== $("kind").value) return false;
    if ($("prov").value && e.provenance !== $("prov").value) return false;
    if (!$("superseded").checked && state.supersededBy.has(e.id)) return false;
    if (!q) return true;
    if (/^[0-9a-f]{6,64}$/.test(q) && e.id.startsWith(q)) return true;
    const hay = (e.body + " " + e.subject + " " + e.kind + " " + (e.actor && e.actor.id)).toLowerCase();
    return q.split(/\s+/).every(function (w) { return hay.indexOf(w) !== -1; });
  }

  function filtering() { return !!($("q").value.trim() || $("kind").value || $("prov").value || !$("superseded").checked); }

  function renderStream() {
    const hits = state.items.filter(matches).reverse();
    const list = $("entries");
    list.className = "entries";     // drops the published snapshot's styling
    list.replaceChildren();
    for (const item of hits.slice(0, state.shown)) list.appendChild(renderEntry(item));
    $("empty").hidden = hits.length !== 0 || state.items.length === 0;
    $("more").hidden = hits.length <= state.shown && (allLoaded() || filtering());
    const note = $("filter-note");
    if (filtering()) { note.textContent = plural(hits.length, "entry matches", "entries match") + "."; note.hidden = false; needAllNote("Searching the entries"); }
    else { note.hidden = true; partial(null); }
  }

  // ---------------------------------------------------------------- calendar

  function foundingDate() {
    const f0 = state.reader.files[0];
    const ts = f0 && f0.entries && f0.entries.length ? f0.entries[0].ts : (f0 && f0.starts);
    const d = ts ? local(ts) : null;
    return d ? new Date(d.getFullYear(), d.getMonth(), d.getDate()) : null;
  }

  function compactRow(it, n) {
    const e = it.e, by = state.supersededBy.get(e.id);
    return el("li", { class: by ? "superseded" : "" }, [
      el("div", { class: "num" }, [el("a", { href: entryHref(e.id), text: num(it.n) }), el("span", { text: it.d ? timeOf(it.d) : "" })]),
      el("div", null, [
        el("div", { class: "meta" }, [el("span", { text: e.kind }), el("a", { class: "subject", href: subjectHref(e.subject), text: e.subject }),
          el("span", { class: "actor", text: (e.actor || {}).id }), stamp(e.provenance)]),
        el("p", { class: "excerpt", text: excerpt(e.body, n) }),
        el("div", { class: "foot" }, [
          e.basis && e.basis.length ? el("span", null, ["stands on "].concat(joinLinks(e.basis))) : null,
          by ? el("span", { class: "red" }, ["superseded by ", refLink(by)]) : null,
          e.supersedes ? el("span", { class: "red" }, [(e.kind === "correction" ? "corrects " : "supersedes "), refLink(e.supersedes)]) : null,
          el("a", { href: entryHref(e.id), text: "open entry" }),
        ]),
      ]),
    ]);
  }

  function renderCalendar() {
    const c = state.cal, today = new Date(), todayKey = dayKey(today);
    const founded = foundingDate() || new Date(today.getFullYear(), today.getMonth(), today.getDate());
    const kindOk = function (it) { return !c.kind || it.e.kind === c.kind; };

    const chips = $("cal-kinds");
    chips.replaceChildren();
    for (const k of [""].concat(KINDS)) {
      const n = state.items.filter(function (it) { return !k || it.e.kind === k; }).length;
      chips.appendChild(el("button", { type: "button", "aria-pressed": c.kind === k ? "true" : "false", class: n ? "" : "zero",
        on: { click: function () { c.kind = k; renderCalendar(); } } },
        [k ? k[0].toUpperCase() + k.slice(1) : "All", el("span", { class: "n", text: n.toLocaleString() })]));
    }

    const first = new Date(c.y, c.m, 1), days = new Date(c.y, c.m + 1, 0).getDate();
    $("cal-title").replaceChildren(MONTHS[c.m] + " ", el("em", { text: String(c.y) }));
    const minMonth = new Date(founded.getFullYear(), founded.getMonth(), 1), maxMonth = new Date(today.getFullYear(), today.getMonth(), 1);
    $("cal-prev").disabled = first <= minMonth;
    $("cal-next").disabled = first >= maxMonth;
    const prefix = c.y + "-" + pad(c.m + 1);
    const counts = {};
    for (const it of state.items) if (kindOk(it) && it.key.startsWith(prefix)) counts[it.key] = (counts[it.key] || 0) + 1;
    const grid = $("cal-grid");
    grid.replaceChildren();
    for (const w of ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) grid.appendChild(el("div", { class: "dow", "aria-hidden": "true", text: w }));
    const lead = (first.getDay() + 6) % 7;
    for (let i = 0; i < lead; i++) grid.appendChild(el("div", { "aria-hidden": "true" }));
    for (let d = 1; d <= days; d++) {
      const date = new Date(c.y, c.m, d), key = dayKey(date), n = counts[key] || 0;
      const quiet = date < founded || key > todayKey;
      grid.appendChild(el("button", {
        type: "button", disabled: quiet, "aria-pressed": c.day === key ? "true" : "false",
        class: ((n ? "has" : "") + (key === todayKey ? " today" : "")) || null,
        "aria-label": d + " " + MONTHS[c.m] + " " + c.y + (n ? ", " + plural(n, "entry", "entries") : ", no entries"),
        on: { click: function () { location.hash = dayHref(key); } },
      }, [el("span", { class: "d", text: String(d) }), n ? el("span", { class: "badge", text: n.toLocaleString() }) : null]));
    }

    $("year-title").textContent = String(c.y);
    $("year-prev").disabled = c.y <= founded.getFullYear();
    $("year-next").disabled = c.y >= today.getFullYear();
    const per = new Array(12).fill(0);
    for (const it of state.items) if (kindOk(it) && it.d && it.d.getFullYear() === c.y) per[it.d.getMonth()]++;
    const max = Math.max.apply(null, per.concat([1]));
    const months = $("cal-months");
    months.replaceChildren();
    for (let m = 0; m < 12; m++) {
      const mdate = new Date(c.y, m, 1), out = mdate < minMonth || mdate > maxMonth;
      months.appendChild(el("button", {
        type: "button", disabled: out, class: m === c.m ? "current" : null,
        "aria-label": MONTHS[m] + " " + c.y + (out ? "" : ": " + plural(per[m], "entry", "entries") + " opened"),
        on: { click: function () { location.hash = "#/calendar/" + c.y + "-" + pad(m + 1); } },
      }, [el("span", { class: "bar" + (per[m] ? " on" : ""), style: "height:" + (per[m] ? Math.max(6, Math.round(48 * per[m] / max)) : 2) + "px" }), el("span", { text: MONTHS[m][0] })]));
    }

    const list = $("day-list");
    list.replaceChildren();
    if (!c.day) {
      const total = Object.values(counts).reduce(function (a, b) { return a + b; }, 0);
      $("day-title").textContent = MONTHS[c.m] + " " + c.y;
      $("day-summary").textContent = plural(total, "entry", "entries") + " on " + plural(Object.keys(counts).length, "day", "days");
      $("day-empty").textContent = total ? "Pick a day to see what was written on it." : "Nothing was written this month.";
      $("day-empty").hidden = false;
      return;
    }
    const date = parseKey(c.day);
    const all = state.items.filter(function (it) { return it.key === c.day; });
    const shown = all.filter(kindOk).reverse();
    $("day-title").textContent = DOWS[date.getDay()] + " " + date.getDate() + " " + MONTHS[date.getMonth()];
    $("day-summary").textContent = (shown.length === all.length ? plural(all.length, "entry", "entries") : shown.length + " of " + plural(all.length, "entry", "entries"))
      + (c.day === todayKey ? " · today" : "") + (c.day === dayKey(founded) ? " · the founding day" : "");
    $("day-empty").textContent = all.length ? "No entries of this kind on this day." : "Nothing was written on this day.";
    $("day-empty").hidden = shown.length !== 0;
    for (const it of shown) list.appendChild(compactRow(it, 160));
  }

  // ---------------------------------------------------------------- subjects

  function renderSubjects() {
    const by = new Map(), s = state.subjects;
    for (const it of state.items) {
      if (!by.has(it.e.subject)) by.set(it.e.subject, []);
      by.get(it.e.subject).push(it);
    }
    const names = Array.from(by.keys());
    const lastN = function (n) { const a = by.get(n); return a[a.length - 1].n; };
    if (s.sort === "az") names.sort();
    if (s.sort === "recent") names.sort(function (a, b) { return lastN(b) - lastN(a); });
    if (s.sort === "most") names.sort(function (a, b) { return by.get(b).length - by.get(a).length || a.localeCompare(b); });
    if (!s.sel || !by.has(s.sel)) s.sel = names.slice().sort(function (a, b) { return lastN(b) - lastN(a); })[0] || null;

    $("subj-count").textContent = plural(names.length, "subject", "subjects");
    const sorts = $("subj-sorts");
    sorts.replaceChildren();
    for (const pair of [["az", "A–Z"], ["recent", "Recent"], ["most", "Most"]]) {
      sorts.appendChild(el("button", { type: "button", "aria-pressed": s.sort === pair[0] ? "true" : "false", text: pair[1],
        on: { click: function () { s.sort = pair[0]; renderSubjects(); } } }));
    }
    const ul = $("subj-items");
    ul.replaceChildren();
    for (const n of names) {
      const a = by.get(n), last = a[a.length - 1];
      const revised = a.some(function (it) { return it.e.supersedes; });
      ul.appendChild(el("li", null, [el("a", { href: subjectHref(n), "aria-current": n === s.sel ? "true" : null }, [
        el("span", { class: "name", text: n }),
        el("span", { class: "facts" }, [revised ? el("span", { class: "rev", text: "revised" }) : null,
          el("span", { text: last.d ? shortDay(last.d) : "" }), el("span", { text: a.length.toLocaleString() })]),
      ])]));
    }

    const t = s.sel ? by.get(s.sel) : [];
    $("thread-title").textContent = s.sel || "";
    const revised = t.some(function (it) { return it.e.supersedes; });
    $("thread-note").textContent = plural(t.length, "entry", "entries") + ", oldest first" + (revised ? ". The thread was revised: the earlier entry stays, marked, and the later one is in force." : ".");
    const list = $("thread-list");
    list.replaceChildren();
    t.forEach(function (it, i) {
      const row = compactRow(it, 260);
      row.insertBefore(el("div", { class: "rail", "aria-hidden": "true" }, [el("span", { class: "dot" }), i < t.length - 1 ? el("span", { class: "line-down" }) : null]), row.firstChild);
      list.appendChild(row);
    });
    needAllNote("Subjects and threads cover the entries");
  }

  // ---------------------------------------------------------------- one entry

  async function renderEntryPage(prefix) {
    let it = findByPrefix(prefix);
    if (!it && !allLoaded()) {
      await openBackwards(function () { return !!findByPrefix(prefix); }, "Looking for the entry in");
      it = findByPrefix(prefix);
    }
    const art = $("entry-article");
    if (!it) {
      $("crumbs").replaceChildren(el("a", { href: "#/", text: "The register" }));
      art.replaceChildren(el("p", { class: "empty", text: "No entry in this ledger has an id starting " + prefix + "." }));
      for (const id of ["stands-list", "built-list", "entry-thread"]) $(id).replaceChildren();
      return;
    }
    const e = it.e, r = state.reader, actor = e.actor || {};
    const nextIt = state.byId.size ? state.items.find(function (x) { return x.n === it.n + 1; }) : null;
    $("crumbs").replaceChildren(
      el("a", { href: "#/", text: "The register" }), el("span", { "aria-hidden": "true", text: "›" }),
      it.d ? el("a", { href: dayHref(it.key), text: longDay(it.d) }) : null, el("span", { "aria-hidden": "true", text: "›" }),
      el("span", { class: "here", text: num(it.n) }), el("span", { class: "spacer" }),
      e.prev ? el("a", { href: entryHref(e.prev), text: "← " + num(it.n - 1) }) : null,
      nextIt ? el("a", { href: entryHref(nextIt.e.id), text: num(it.n + 1) + " →" }) : null
    );
    const by = state.supersededBy.get(e.id);
    const sigLine = e.sig
      ? (r.canSign ? [el("dt", { class: "ok", text: "✓ signed" }), el("dd", { text: "by " + actor.key.slice(0, 16) + "…" + actor.key.slice(-7) + ", checked here against the words above" })]
                   : [el("dt", { text: "signed" }), el("dd", { text: "by " + actor.key.slice(0, 16) + "…; this browser cannot check Ed25519 signatures" })])
      : [el("dt", { text: "unsigned" }), el("dd", { text: "a human entry, placed by a steward; its id and its place in the chain are what is checked" })];
    const chainText = (it.n === 0 ? "the founding entry" : "follows " + num(it.n - 1) + ", " + short(e.prev)) + (nextIt ? "; followed by " + num(it.n + 1) : "; the newest entry");
    const json = {};
    for (const k of Object.keys(e).sort()) json[k] = e[k];
    art.replaceChildren(
      el("div", { class: "margin" }, [el("span", { class: "big", text: num(it.n) }),
        it.d ? el("span", { class: "when", text: shortDay(it.d) }) : null,
        it.d ? el("span", { class: "when", text: timeOf(it.d) + " your time" }) : null]),
      el("div", null, [
        el("div", { class: "meta" }, [el("span", { text: e.kind }), el("a", { class: "subject", href: subjectHref(e.subject), text: e.subject }),
          el("span", { class: "actor", text: actor.type + ":" + actor.id }), stamp(e.provenance)]),
        by ? el("p", { class: "notice" }, ["Superseded by ", refLink(by), ". Read that entry before relying on this one."]) : null,
        e.supersedes ? el("p", { class: "notice quiet" }, [(e.kind === "correction" ? "Corrects " : "Supersedes "), refLink(e.supersedes), "."]) : null,
        el("p", { class: "body", text: e.body }),
        el("dl", { class: "checks" }, [el("dt", { class: "ok", text: "✓ id" }), el("dd", { text: e.id + ", recomputed here, matches" })]
          .concat(sigLine, [el("dt", { class: "ok", text: "✓ chain" }), el("dd", { text: chainText })])),
        el("div", { class: "foot" }, [copyButton(location.origin + location.pathname + entryHref(e.id), "copy link"), copyButton(e.id, "copy id"),
          it.d ? el("a", { href: dayHref(it.key), text: "everything from " + shortDay(it.d) }) : null]),
        el("details", null, [el("summary", { text: "raw JSON" }), el("pre", { text: JSON.stringify(json, null, 2) })]),
      ])
    );

    const smallRow = function (x) {
      return el("li", null, [el("div", { class: "line" }, [el("a", { class: "n", href: entryHref(x.e.id), text: num(x.n) }), el("span", { class: "k", text: x.e.kind }),
        el("a", { href: subjectHref(x.e.subject), text: x.e.subject }), el("span", { text: x.d ? shortDay(x.d) + " · " + timeOf(x.d) : "" })]),
        el("p", { text: excerpt(x.e.body, 160) })]);
    };
    const missingRow = function (id) {
      return el("li", null, [el("div", { class: "line" }, [el("a", { class: "n", href: entryHref(id), text: short(id) }), el("span", { text: "in an earlier file: open it to check and read it" })])]);
    };
    const none = function (text) { return el("li", null, [el("span", { class: "none", text: text })]); };
    const refs = (e.basis || []).concat(e.supersedes ? [e.supersedes] : []);
    $("stands-list").replaceChildren.apply($("stands-list"), refs.length ? refs.map(function (id) { const x = state.byId.get(id); return x ? smallRow(x) : missingRow(id); })
      : [none(it.n === 0 ? "Nothing. This is the founding entry." : "Nothing named.")]);
    const cited = (state.citedBy.get(e.id) || []).map(function (id) { return state.byId.get(id); });
    $("built-list").replaceChildren.apply($("built-list"), cited.length ? cited.map(smallRow) : [none("Nothing builds on this yet.")]);
    const thread = state.items.filter(function (x) { return x.e.subject === e.subject; });
    $("entry-subject-sub").textContent = e.subject + ", in order";
    $("entry-thread").replaceChildren.apply($("entry-thread"), thread.map(function (x) {
      return x === it ? el("li", null, [el("div", { class: "line" }, [el("span", { class: "n", text: num(x.n) }), el("span", { text: "this entry" + (thread.length === 1 ? ", the only one so far" : "") })])]) : smallRow(x);
    }));
    if (!allLoaded()) partial("Built on by and In this subject cover the entries from " + num(loadedFrom()) + " onward.", "Open and check everything");
    else partial(null);
  }

  // ---------------------------------------------------------------- routing

  function show(view) {
    state.view = view;
    document.body.classList.toggle("reading", view !== "stream");
    for (const v of ["stream", "calendar", "subjects", "entry"]) $("view-" + v).hidden = v !== view;
    for (const a of document.querySelectorAll("#views a")) {
      if (a.dataset.view === view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    }
  }

  async function route() {
    if (!state.reader || state.reader.failure) return;
    const h = location.hash;
    let m;
    if ((m = /^#e-([0-9a-f]{12})$/.exec(h))) { location.replace("#/entry/" + m[1]); return; }
    if ((m = /^#\/entry\/([0-9a-f]{6,64})$/.exec(h))) {
      show("entry");
      await renderEntryPage(m[1]);
      window.scrollTo(0, 0);
      return;
    }
    if ((m = /^#\/calendar(?:\/(\d{4})-(\d{2})(?:-(\d{2}))?)?$/.exec(h))) {
      show("calendar");
      let y, mo, day = null;
      if (m[1]) { y = +m[1]; mo = +m[2] - 1; if (m[3]) day = m[1] + "-" + m[2] + "-" + m[3]; }
      else {
        const last = state.items.length ? state.items[state.items.length - 1] : null;
        const d = last && last.d ? last.d : new Date();
        y = d.getFullYear(); mo = d.getMonth(); day = last ? last.key : null;
      }
      Object.assign(state.cal, { y: y, m: mo, day: day });
      const firstLoaded = state.items.length ? state.items[0].d : null;
      if (!allLoaded() && firstLoaded && new Date(y, mo, 1) < firstLoaded) {
        renderCalendar();
        await openMonth(y, mo);
      }
      renderCalendar();
      if (!allLoaded()) partial("The calendar shows the entries opened so far, from " + num(loadedFrom()) + ". Moving to an earlier month opens and checks the files it needs.");
      else partial(null);
      return;
    }
    if ((m = /^#\/subjects(?:\/(.+))?$/.exec(h))) {
      show("subjects");
      if (m[1]) state.subjects.sel = decodeURIComponent(m[1]);
      renderSubjects();
      return;
    }
    if (h === "" || h === "#" || h === "#/") {
      show("stream");
      renderStream();
    }
    // Any other hash is an ordinary anchor on the page (the notes, the skip link): the view stays as it is.
  }

  // ---------------------------------------------------------------- start

  // Remember which complete files this browser has already checked, by their hash.
  const memory = {
    has: function (h) { try { return localStorage.getItem("pl:checked:" + h) === "1"; } catch (e) { return false; } },
    add: function (h) { try { localStorage.setItem("pl:checked:" + h, "1"); } catch (e) { /* storage may be unavailable */ } },
  };

  async function start() {
    if (cfg.mcpUrl) $("mcp-url").textContent = cfg.mcpUrl;
    if (cfg.repo) $("repo-link").href = cfg.repo;
    for (const mi of cfg.mirrors || []) {
      const host = new URL(mi).hostname.replace(/^archive\./, "");
      $("mirrors").appendChild(el("a", { href: mi, text: host }));
    }
    if (!window.crypto || !window.crypto.subtle) {
      setStatus([el("span", { class: "bad", text: "This page needs a secure connection (https) to verify the ledger." })]);
      return;
    }
    let reader = null, lastError = null;
    for (const base of cfg.ledgerBases || ["ledger/"]) {
      try {
        const r = new L.Reader(base, { memory: memory });
        if (await r.init()) { reader = r; break; }
      } catch (err) { lastError = err; }
    }
    if (!reader) {
      setStatus([el("span", { class: "bad", text: "Could not read the ledger" + (lastError ? ": " + lastError.message : ".") })]);
      return;
    }
    state.reader = reader;
    try {
      if (reader.count === 1) await reader.verifyAll();
      else { await reader.load(reader.count - 1); await reader.starts(0); }
    } catch (err) {
      setStatus([el("span", { class: "bad", text: "Could not read the ledger: " + err.message })]);
      return;
    }
    reindex();
    renderStatus();
    if (reader.failure) return;
    route();
  }

  for (const id of ["q", "kind", "prov", "superseded"]) {
    $(id).addEventListener("input", function () { state.shown = PAGE; renderStream(); });
  }
  $("more").addEventListener("click", async function () {
    state.shown += PAGE;
    if (state.items.filter(matches).length < state.shown && !allLoaded() && !filtering()) {
      await openBackwards(function () { return state.items.length >= state.shown; }, "Opening");
    }
    renderStream();
  });
  $("cal-prev").addEventListener("click", function () { const d = new Date(state.cal.y, state.cal.m - 1, 1); location.hash = "#/calendar/" + d.getFullYear() + "-" + pad(d.getMonth() + 1); });
  $("cal-next").addEventListener("click", function () { const d = new Date(state.cal.y, state.cal.m + 1, 1); location.hash = "#/calendar/" + d.getFullYear() + "-" + pad(d.getMonth() + 1); });
  $("year-prev").addEventListener("click", function () { location.hash = "#/calendar/" + (state.cal.y - 1) + "-12"; });
  $("year-next").addEventListener("click", function () {
    const today = new Date(), y = state.cal.y + 1;
    location.hash = "#/calendar/" + y + "-" + pad(y === today.getFullYear() ? today.getMonth() + 1 : 1);
  });
  window.addEventListener("hashchange", route);
  start();
})();
