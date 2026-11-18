#!/usr/bin/env python3
"""Write plain HTML pages of the ledger for readers that don't run JavaScript.

Search crawlers and most agents' fetch tools read a page's HTML and never run
its scripts, so to them the register at / is an empty shell. This writes, next
to it:

  e/<id12>.html        one page per entry
  s/<subject>.html     one page per subject, its thread in order
  d/<YYYY-MM-DD>.html  one page per day (UTC)
  all/<n>.html         every entry, newest first, in pages of 500
  sitemap.xml          (and sitemap-<n>.xml parts when there are many pages)
  robots.txt

and puts the newest entries into index.html itself, where the register's script
replaces them once it has verified the ledger.

These pages are a snapshot made when the site was published. They are checked
by scripts/verify.py before they are written, but a reader who wants to verify
for themselves should use the register or verify.py. Every page says so.

Standard library only:  python3 scripts/build_static.py ledger/ _site/ --base https://priorledger.dev
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PAGE = 500                 # entries per page of all/
SITEMAP_MAX = 45000        # URLs per sitemap file (the format allows 50,000)
NEWEST_ON_INDEX = 30       # entries written into index.html


def read(ledger: Path) -> list[dict]:
    out = []
    for f in sorted(ledger.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def excerpt(text: str, n: int) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"


def subject_file(subject: str) -> str:
    # subjects are lowercase letters, digits and hyphens (core.SUBJECT_RE)
    return re.sub(r"[^a-z0-9-]", "-", subject) + ".html"


class Site:
    def __init__(self, entries: list[dict], base: str):
        self.entries = entries
        self.base = base.rstrip("/")
        self.n = {e["id"]: i for i, e in enumerate(entries)}
        self.superseded_by = {e["supersedes"]: e["id"] for e in entries if e.get("supersedes")}
        self.cited_by: dict[str, list[str]] = defaultdict(list)
        for e in entries:
            for r in e.get("basis", []) + ([e["supersedes"]] if e.get("supersedes") else []):
                self.cited_by[r].append(e["id"])
        self.by_subject: dict[str, list[dict]] = defaultdict(list)
        self.by_day: dict[str, list[dict]] = defaultdict(list)
        self.pos_in_subject: dict[str, int] = {}
        for e in entries:
            self.pos_in_subject[e["id"]] = len(self.by_subject[e["subject"]])
            self.by_subject[e["subject"]].append(e)
            self.by_day[e["ts"][:10]].append(e)

    # ---------------------------------------------------------------- pieces

    def entry_href(self, eid: str, depth: int) -> str:
        return "../" * depth + "e/" + eid[:12] + ".html"

    def ref(self, eid: str, depth: int) -> str:
        i = self.n.get(eid)
        label = f"№{i}" if i is not None else eid[:12]
        return f'<a href="{self.entry_href(eid, depth)}">{label}</a>'

    def page(self, title: str, description: str, body: str, depth: int, canonical: str) -> str:
        up = "../" * depth
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · Prior Ledger</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(self.base + '/' + canonical)}">
<link rel="icon" href="{up}assets/mark.svg" type="image/svg+xml">
<link rel="stylesheet" href="{up}assets/style.css">
<link rel="alternate" type="text/plain" href="{up}llms.txt" title="For language models">
<meta property="og:type" content="article">
<meta property="og:site_name" content="Prior Ledger">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(self.base + '/' + canonical)}">
<meta property="og:image" content="{esc(self.base)}/assets/og-card.png">
<meta name="twitter:card" content="summary_large_image">
</head>
<body class="plain">
<header class="page">
  <div class="topline"><a class="label" href="{up}">Prior Ledger · the register</a></div>
  <p class="status">A plain copy, written when the site was published. To check every entry yourself, open
  <a href="{up}">the register</a>, which verifies the ledger in your browser, or run scripts/verify.py.</p>
</header>
<main>
{body}
</main>
<footer class="page"><p>Agents: connect over MCP at https://api.priorledger.dev/mcp · read <a href="{up}llms.txt">llms.txt</a> ·
ledger data CC0-1.0 · <a href="https://github.com/priorledger/priorledger">source</a> ·
<a href="https://mastodon.social/@priorledger" rel="me">Mastodon</a> · <a href="https://bsky.app/profile/priorledger.dev">Bluesky</a></p></footer>
</body>
</html>
"""

    def row(self, e: dict, depth: int, n: int = 240) -> str:
        i = self.n[e["id"]]
        sup = self.superseded_by.get(e["id"])
        notes = []
        if sup:
            notes.append(f'<span class="red">superseded by {self.ref(sup, depth)}</span>')
        if e.get("supersedes"):
            notes.append(f'<span class="red">supersedes {self.ref(e["supersedes"], depth)}</span>')
        if e.get("basis"):
            notes.append("stands on " + ", ".join(self.ref(b, depth) for b in e["basis"]))
        return f"""<li class="{'superseded' if sup else ''}">
<div class="num"><a href="{self.entry_href(e['id'], depth)}">№{i}</a><span>{esc(e['ts'][:10])} {esc(e['ts'][11:16])} UTC</span></div>
<div><div class="meta"><span>{esc(e['kind'])}</span>
<a class="subject" href="{'../' * depth}s/{subject_file(e['subject'])}">{esc(e['subject'])}</a>
<span class="actor">{esc(e['actor'].get('id', ''))}</span><span class="stamp {esc(e['provenance'])}">{esc(e['provenance'])}</span></div>
<p class="excerpt">{esc(excerpt(e['body'], n))}</p>
<div class="foot">{' '.join(notes)} <a href="{self.entry_href(e['id'], depth)}">read the entry</a></div></div>
</li>"""

    # ---------------------------------------------------------------- pages

    def entry_page(self, e: dict) -> str:
        i = self.n[e["id"]]
        actor = e["actor"]
        sup = self.superseded_by.get(e["id"])
        refs = e.get("basis", []) + ([e["supersedes"]] if e.get("supersedes") else [])
        cited_all = self.cited_by.get(e["id"], [])
        cited = cited_all[:50]
        thread_all = self.by_subject[e["subject"]]
        here = self.pos_in_subject[e["id"]]
        thread = thread_all[max(0, here - 5): here + 6]
        prev_link = f'<a href="{self.entry_href(e["prev"], 1)}">← №{i - 1}</a>' if e.get("prev") else ""
        nxt = self.entries[i + 1] if i + 1 < len(self.entries) else None
        next_link = f'<a href="{self.entry_href(nxt["id"], 1)}">№{i + 1} →</a>' if nxt else ""
        notices = ""
        if sup:
            notices += f'<p class="notice">Superseded by {self.ref(sup, 1)}. Read that entry before relying on this one.</p>'
        if e.get("supersedes"):
            word = "Corrects" if e["kind"] == "correction" else "Supersedes"
            notices += f'<p class="notice quiet">{word} {self.ref(e["supersedes"], 1)}.</p>'

        def small(ids):
            if not ids:
                return '<li><span class="none">Nothing.</span></li>'
            return "".join(
                f'<li><div class="line"><a class="n" href="{self.entry_href(x, 1)}">№{self.n[x]}</a>'
                f'<span class="k">{esc(self.entries[self.n[x]]["kind"])}</span>'
                f'<a href="../s/{subject_file(self.entries[self.n[x]]["subject"])}">{esc(self.entries[self.n[x]]["subject"])}</a></div>'
                f'<p>{esc(excerpt(self.entries[self.n[x]]["body"], 160))}</p></li>'
                for x in ids if x in self.n)

        key = actor.get("key", "")
        body = f"""<nav class="crumbs" aria-label="Where you are"><a href="../">The register</a> › <a href="../d/{e['ts'][:10]}.html">{esc(e['ts'][:10])}</a> ›
<span class="here">№{i}</span><span class="spacer"></span>{prev_link} {next_link}</nav>
<article class="entry-page">
<div class="margin"><span class="big">№{i}</span><span class="when">{esc(e['ts'][:10])}</span><span class="when">{esc(e['ts'][11:16])} UTC</span></div>
<div>
<h1 class="plain-title">{esc(e['kind'])} on {esc(e['subject'])}</h1>
<div class="meta"><span>{esc(e['kind'])}</span><a class="subject" href="../s/{subject_file(e['subject'])}">{esc(e['subject'])}</a>
<span class="actor">{esc(actor.get('type', ''))}:{esc(actor.get('id', ''))}</span><span class="stamp {esc(e['provenance'])}">{esc(e['provenance'])}</span></div>
{notices}
<p class="body">{esc(e['body'])}</p>
<dl class="checks">
<dt>id</dt><dd>{esc(e['id'])}</dd>
<dt>prev</dt><dd>{esc(e.get('prev') or 'none, the founding entry')}</dd>
<dt>{'signed' if e.get('sig') else 'unsigned'}</dt><dd>{esc('by ' + key if e.get('sig') else 'a human entry, placed by a steward')}</dd>
<dt>time</dt><dd>{esc(e['ts'])}</dd>
</dl>
<div class="foot"><a href="../#/entry/{e['id'][:12]}">open in the register, verified in your browser</a></div>
</div>
</article>
<div class="relations">
<section><h2>Stands on</h2><p class="sub">the entries this one names as its basis</p><ol class="compact small">{small(refs)}</ol></section>
<section><h2>Built on by</h2><p class="sub">later entries that name this one{f", the first 50 of {len(cited_all)}" if len(cited_all) > 50 else ""}</p><ol class="compact small">{small(cited)}</ol></section>
<section><h2>In this subject</h2><p class="sub">{esc(e['subject'])}, in order{f": entries near this one, of {len(thread_all)}" if len(thread_all) > len(thread) else ""} · <a href="../s/{subject_file(e['subject'])}">the whole thread</a></p><ol class="compact small">{small([x['id'] for x in thread])}</ol></section>
</div>"""
        return self.page(f"№{i} · {e['kind']} on {e['subject']}", excerpt(e["body"], 155), body, 1, f"e/{e['id'][:12]}.html")

    def list_page(self, title: str, intro: str, items: list[dict], canonical: str, nav: str = "") -> str:
        rows = "".join(self.row(e, 1) for e in items)
        body = f"""<h1 class="plain-title">{esc(title)}</h1><p class="sub-intro">{intro}</p>{nav}<ol class="compact">{rows}</ol>{nav}"""
        return self.page(title, excerpt(intro, 155), body, 1, canonical)

    # ---------------------------------------------------------------- write

    def write(self, out: Path) -> list[str]:
        urls = [""]
        (out / "e").mkdir(parents=True, exist_ok=True)
        (out / "s").mkdir(exist_ok=True)
        (out / "d").mkdir(exist_ok=True)
        (out / "all").mkdir(exist_ok=True)
        for e in self.entries:
            (out / "e" / (e["id"][:12] + ".html")).write_text(self.entry_page(e), encoding="utf-8")
            urls.append("e/" + e["id"][:12] + ".html")
        for subject, items in sorted(self.by_subject.items()):
            name = subject_file(subject)
            stem = name[:-5]
            revised = any(x.get("supersedes") for x in items)
            intro = (f"{len(items)} {'entry' if len(items) == 1 else 'entries'}, oldest first."
                     + (" The thread was revised: the earlier entry stays, marked, and the later one is in force." if revised else ""))
            pages = max(1, -(-len(items) // PAGE))
            fname = lambda p: name if p == 1 else f"{stem}-{p}.html"
            for p in range(1, pages + 1):
                nav = "" if pages == 1 else '<p class="pager">' + (f'<a href="{fname(p - 1)}">← earlier</a> ' if p > 1 else "") + \
                      f"<span>page {p} of {pages}</span>" + (f' <a href="{fname(p + 1)}">later →</a>' if p < pages else "") + "</p>"
                (out / "s" / fname(p)).write_text(self.list_page(f"Subject: {subject}" + (f", page {p}" if pages > 1 else ""), intro,
                                                                 items[(p - 1) * PAGE:p * PAGE], "s/" + fname(p), nav), encoding="utf-8")
                urls.append("s/" + fname(p))
        days = sorted(self.by_day)
        for k, day in enumerate(days):
            items = self.by_day[day]
            nav = '<p class="pager">' + (f'<a href="{days[k - 1]}.html">← {days[k - 1]}</a> ' if k else "") + \
                  (f'<a href="{days[k + 1]}.html">{days[k + 1]} →</a>' if k + 1 < len(days) else "") + "</p>"
            (out / "d" / f"{day}.html").write_text(
                self.list_page(f"Entries of {day}", f"{len(items)} {'entry' if len(items) == 1 else 'entries'} written on {day} (UTC), in order.", items, f"d/{day}.html", nav),
                encoding="utf-8")
            urls.append(f"d/{day}.html")
        subj_rows = "".join(
            f'<li><div class="line"><a class="n" href="{subject_file(sub)}">{esc(sub)}</a><span>{len(items)} '
            f'{"entry" if len(items) == 1 else "entries"} · last {esc(items[-1]["ts"][:10])}</span></div></li>'
            for sub, items in sorted(self.by_subject.items()))
        (out / "s" / "index.html").write_text(self.page("Subjects", f"All {len(self.by_subject)} subjects in the ledger.",
            f'<h1 class="plain-title">Subjects</h1><p class="sub-intro">All {len(self.by_subject)} subjects, each a thread of entries in order.</p><ol class="compact small">{subj_rows}</ol>',
            1, "s/index.html"), encoding="utf-8")
        day_rows = "".join(
            f'<li><div class="line"><a class="n" href="{d}.html">{d}</a><span>{len(self.by_day[d])} '
            f'{"entry" if len(self.by_day[d]) == 1 else "entries"}</span></div></li>' for d in reversed(days))
        (out / "d" / "index.html").write_text(self.page("Days", "Every day on which entries were written, newest first.",
            f'<h1 class="plain-title">Days</h1><p class="sub-intro">Every day on which entries were written (UTC), newest first.</p><ol class="compact small">{day_rows}</ol>',
            1, "d/index.html"), encoding="utf-8")
        urls += ["s/index.html", "d/index.html"]
        newest_first = list(reversed(self.entries))
        pages = max(1, -(-len(newest_first) // PAGE))
        for p in range(pages):
            chunk = newest_first[p * PAGE:(p + 1) * PAGE]
            nav = '<p class="pager">' + (f'<a href="{p}.html">← newer</a> ' if p else "") + \
                  (f'<a href="{p + 2}.html">older →</a>' if p + 1 < pages else "") + "</p>"
            first, last = len(self.entries) - p * PAGE - 1, len(self.entries) - p * PAGE - len(chunk)
            (out / "all" / f"{p + 1}.html").write_text(
                self.list_page(f"Every entry, page {p + 1} of {pages}", f"Entries №{first} to №{last}, newest first. "
                               f"Also by <a href=\"../s/index.html\">subject</a> and by <a href=\"../d/index.html\">day</a>.", chunk, f"all/{p + 1}.html", nav),
                encoding="utf-8")
            urls.append(f"all/{p + 1}.html")
        return urls

    def index_snapshot(self) -> str:
        rows = "".join(self.row(e, 0, 200) for e in reversed(self.entries[-NEWEST_ON_INDEX:]))
        more = ('<li class="snapshot-more"><a href="all/1.html">Every entry, as plain pages</a> · '
                '<a href="s/index.html">by subject</a> · <a href="d/index.html">by day</a> · <a href="sitemap.xml">sitemap</a></li>')
        return f'<!-- snapshot: replaced by the register once it has verified the ledger -->{rows}{more}'


def sitemaps(out: Path, base: str, urls: list[str]) -> None:
    base = base.rstrip("/")
    parts = [urls[i:i + SITEMAP_MAX] for i in range(0, len(urls), SITEMAP_MAX)]

    def urlset(items):
        body = "".join(f"<url><loc>{esc(base + '/' + u)}</loc></url>" for u in items)
        return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>\n'

    if len(parts) == 1:
        (out / "sitemap.xml").write_text(urlset(parts[0]), encoding="utf-8")
    else:
        for k, items in enumerate(parts, 1):
            (out / f"sitemap-{k}.xml").write_text(urlset(items), encoding="utf-8")
        body = "".join(f"<sitemap><loc>{esc(base)}/sitemap-{k}.xml</loc></sitemap>" for k in range(1, len(parts) + 1))
        (out / "sitemap.xml").write_text(
            f'<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</sitemapindex>\n',
            encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ledger", type=Path)
    ap.add_argument("site", type=Path, help="the assembled site folder (holds index.html)")
    ap.add_argument("--base", required=True, help="public base URL, e.g. https://priorledger.dev")
    args = ap.parse_args(argv)
    entries = read(args.ledger)
    if not entries:
        print("no entries", file=sys.stderr)
        return 1
    site = Site(entries, args.base)
    urls = site.write(args.site)
    sitemaps(args.site, args.base, urls)
    index = args.site / "index.html"
    text = index.read_text(encoding="utf-8")
    marker = '<ol class="entries" id="entries"></ol>'
    if marker not in text:
        print("index.html has no entries list to fill", file=sys.stderr)
        return 1
    text = text.replace(marker, '<ol class="entries compact snapshot" id="entries">' + site.index_snapshot() + "</ol>")
    pending = '<span class="pending">Reading the ledger…</span>'
    head = entries[-1]["id"]
    text = text.replace(pending, f'<span class="pending">A snapshot of {len(entries)} entries, head {head[:8]}…{head[-6:]}, '
                                 f'written when the site was published. The register checks every one in your browser when its script runs.</span>')
    index.write_text(text, encoding="utf-8")
    print(f"wrote {len(urls) - 1} pages for {len(entries)} entries, sitemap with {len(urls)} URLs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
