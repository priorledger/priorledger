/* Steward desk. All proposal text is untrusted and only ever set with textContent. */
(function () {
  "use strict";

  const $ = function (id) { return document.getElementById(id); };
  let csrf = "";

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

  function flash(text, bad) {
    const f = $("flash");
    f.textContent = text;
    f.className = "flash" + (bad ? " bad" : "");
    f.hidden = false;
  }

  async function api(path, body) {
    const opts = { credentials: "same-origin", headers: {} };
    if (body !== undefined) {
      opts.method = "POST";
      opts.headers["Content-Type"] = "application/json";
      opts.headers["X-CSRF-Token"] = csrf;
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw Object.assign(new Error(data.error || ("request failed (" + res.status + ")")), { status: res.status, data: data });
    return data;
  }

  const short = function (id) { return id ? id.slice(0, 8) + "…" + id.slice(-6) : "—"; };
  const day = function (iso) { return iso ? iso.slice(0, 16).replace("T", " ") : ""; };

  // ---------------------------------------------------------------- one proposal

  function proposalItem(p, pending) {
    const c = p.content;
    const li = el("li", { class: "entry", id: "p-" + p.pid });
    li.appendChild(el("div", { class: "margin" }, [
      el("span", { class: "number", text: "Proposal " + p.pid }),
      el("span", { class: "when", text: day(p.received) }),
    ]));

    const main = el("div", { class: "content" });
    main.appendChild(el("div", { class: "meta" }, [
      el("span", { text: c.kind }), el("span", { text: "·", "aria-hidden": "true" }),
      el("span", { class: "subject", text: c.subject }), el("span", { text: "·", "aria-hidden": "true" }),
      el("span", { class: "actor", text: "agent:" + c.actor.id + " · key " + c.actor.key.slice(8, 14) + "…" }),
      el("span", { class: "stamp inferred", text: c.provenance }),
    ]));
    if (c.supersedes) main.appendChild(el("p", { class: "notice quiet" }, ["Would supersede ", el("a", { href: "/#e-" + c.supersedes.slice(0, 12), text: short(c.supersedes) }), "."]));
    main.appendChild(el("p", { class: "body", text: c.body }));

    const basis = el("span", null, ["basis "]);
    if (c.basis.length) c.basis.forEach(function (b, i) {
      if (i) basis.appendChild(document.createTextNode(", "));
      basis.appendChild(el("a", { href: "/#e-" + b.slice(0, 12), text: short(b) }));
    });
    else basis.appendChild(document.createTextNode("—"));
    main.appendChild(el("div", { class: "foot" }, [basis, el("span", { text: "signed" })]));

    if (p.reviews && p.reviews.length) {
      const list = el("ul", { class: "reviews", "aria-label": "Reviews" });
      for (const r of p.reviews) {
        list.appendChild(el("li", null, [
          el("span", { class: "stamp rec-" + r.recommendation, text: r.recommendation.replace("-", " ") }),
          el("span", { class: "by", text: r.reviewer + " · " + day(r.created) }),
          el("p", { text: r.reasoning }),
        ]));
      }
      main.appendChild(list);
    }

    if (pending) {
      const note = el("input", { type: "text", placeholder: "Note (required to reject)", "aria-label": "Decision note for proposal " + p.pid });
      const accept = el("button", { type: "button", class: "button accept", text: "Accept" });
      const reject = el("button", { type: "button", class: "button reject", text: "Reject" });
      async function decide(kind) {
        if (kind === "reject" && !note.value.trim()) { note.focus(); flash("A rejection needs a note — the proposer can read it.", true); return; }
        if (kind === "accept" && !window.confirm("Accept proposal " + p.pid + " into the ledger? This is permanent.")) return;
        accept.disabled = reject.disabled = true;
        try {
          const res = await api("/api/steward/proposals/" + p.pid + "/" + kind, { note: note.value });
          flash(kind === "accept"
            ? "Accepted as " + short(res.entry_id) + (res.published ? " and published." : ". Commit and push it from the server.")
            : "Rejected proposal " + p.pid + ".");
          loadQueue();
        } catch (err) {
          flash(err.message, true);
          accept.disabled = reject.disabled = false;
        }
      }
      accept.addEventListener("click", function () { decide("accept"); });
      reject.addEventListener("click", function () { decide("reject"); });
      main.appendChild(el("div", { class: "decide" }, [note, accept, reject]));
    } else {
      const outcome = p.status === "accepted"
        ? ["Accepted as ", el("a", { href: "/#e-" + (p.entry_id || "").slice(0, 12), text: short(p.entry_id) })]
        : ["Rejected"];
      main.appendChild(el("p", { class: "notice " + (p.status === "accepted" ? "quiet" : "") },
        outcome.concat([" by " + (p.decided_by || "steward") + " · " + day(p.decided_at) + (p.note ? " · “" + p.note + "”" : "")])));
    }

    li.appendChild(main);
    return li;
  }

  async function loadQueue() {
    const data = await api("/api/steward/proposals?status=pending");
    $("queue").replaceChildren.apply($("queue"), data.proposals.map(function (p) { return proposalItem(p, true); }));
    $("queue-empty").hidden = data.proposals.length !== 0;
    $("queue-count").textContent = data.proposals.length ? "(" + data.proposals.length + ")" : "";
  }

  async function loadDecided() {
    const data = await api("/api/steward/proposals?status=" + $("decided-status").value);
    $("decided").replaceChildren.apply($("decided"), data.proposals.map(function (p) { return proposalItem(p, false); }));
    $("decided-empty").hidden = data.proposals.length !== 0;
  }

  // ---------------------------------------------------------------- tokens

  async function loadTokens() {
    const data = await api("/api/steward/tokens");
    $("tokens").replaceChildren.apply($("tokens"), data.tokens.map(function (t) {
      const li = el("li", { class: t.revoked_at ? "revoked" : "" }, [
        el("strong", { text: t.label }),
        el("span", { text: t.scopes.join(" · ") }),
        el("span", { text: t.revoked_at ? "revoked " + day(t.revoked_at) : "expires " + day(t.expires) }),
        el("span", { text: t.last_used ? "last used " + day(t.last_used) : "never used" }),
      ]);
      if (!t.revoked_at) {
        const b = el("button", { type: "button", class: "button reject push", text: "Revoke" });
        b.addEventListener("click", async function () {
          if (!window.confirm("Revoke '" + t.label + "'? Agents using it stop working immediately.")) return;
          try { await api("/api/steward/tokens/" + t.tid + "/revoke", {}); flash("Revoked " + t.label + "."); loadTokens(); }
          catch (err) { flash(err.message, true); }
        });
        li.appendChild(b);
      }
      return li;
    }));
  }

  $("token-form").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    const scopes = Array.from(document.querySelectorAll('input[name="scope"]:checked')).map(function (i) { return i.value; });
    try {
      const res = await api("/api/steward/tokens", { label: $("token-label").value.trim(), scopes: scopes, days: Number($("token-days").value) });
      $("token-value").textContent = res.token;
      $("token-reveal").hidden = false;
      $("token-form").reset();
      flash("Issued '" + res.label + "'.");
      loadTokens();
    } catch (err) { flash(err.message, true); }
  });
  $("token-copy").addEventListener("click", function () {
    navigator.clipboard.writeText($("token-value").textContent).then(function () { flash("Copied. Hand it to your agent, then close this page."); });
  });

  // ---------------------------------------------------------------- write

  $("write-form").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    const ids = function (s) { return s.trim() ? s.trim().split(/\s+/) : []; };
    if (!window.confirm("Publish this entry? It can be superseded later, never removed.")) return;
    try {
      const res = await api("/api/steward/entries", {
        kind: $("w-kind").value, provenance: $("w-prov").value, subject: $("w-subject").value.trim(),
        basis: ids($("w-basis").value), supersedes: $("w-supersedes").value.trim() || undefined, body: $("w-body").value,
      });
      flash("Written as " + short(res.entry_id) + (res.published ? " and published." : ". Commit and push it from the server."));
      $("write-form").reset();
    } catch (err) { flash(err.message, true); }
  });

  // ---------------------------------------------------------------- log

  async function loadLog() {
    const data = await api("/api/steward/audit");
    $("log").replaceChildren.apply($("log"), data.audit.map(function (a) {
      const detail = Object.keys(a.detail).map(function (k) { return k + " " + JSON.stringify(a.detail[k]); }).join(" · ");
      return el("li", null, [el("span", { text: day(a.at) }), el("strong", { text: a.action }), el("span", { text: a.actor }), el("span", { text: detail })]);
    }));
  }

  // ---------------------------------------------------------------- tabs and start

  const loaders = { queue: loadQueue, decided: loadDecided, tokens: loadTokens, log: loadLog, write: function () {} };

  function show(tab) {
    document.querySelectorAll(".tabs button").forEach(function (b) {
      if (b.dataset.tab === tab) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
    document.querySelectorAll(".tab").forEach(function (s) { s.hidden = s.id !== "tab-" + tab; });
    Promise.resolve(loaders[tab]()).catch(function (err) { flash(err.message, true); });
  }

  document.querySelectorAll(".tabs button").forEach(function (b) { b.addEventListener("click", function () { show(b.dataset.tab); }); });
  $("decided-status").addEventListener("input", function () { loadDecided().catch(function (e) { flash(e.message, true); }); });
  $("signout").addEventListener("click", async function () {
    try { await api("/admin/logout", {}); } catch (e) { /* signed out either way */ }
    location.href = "/admin/";
  });

  (async function start() {
    const err = new URLSearchParams(location.search).get("error");
    if (err) flash(err, true);
    try {
      const me = await api("/api/steward/me");
      if (me.kind !== "session") throw Object.assign(new Error("Reviewer tokens work through the API, not this page."), { status: 403 });
      csrf = me.csrf;
      $("who").textContent = "signed in as steward" + (me.publish === "git" ? " · decisions publish immediately" : " · decisions wait for a manual push");
      $("desk").hidden = false;
      $("signout").hidden = false;
      show("queue");
    } catch (e) {
      $("signin").hidden = false;
      if (e.data && e.data.signin === false) {
        $("signin-link").hidden = true;
        $("signin-note").textContent = "Sign-in isn't configured on this server yet. See infra/README.md, section 7.";
      }
    }
  })();
})();
