#!/usr/bin/env python3
"""Tell search engines about new pages the moment they are published (IndexNow).

Two steps, run by .github/workflows/pages.yml:

  plan    before the new site goes live: compare the live sitemap with the one just
          built, and write the pages that are new or changed to a file.
  submit  after it goes live: send that list to IndexNow, which shares it with Bing,
          Yandex, Seznam, Naver and others.

The IndexNow key is public by design: it sits at the site root (web/<key>.txt) to prove
the list comes from whoever controls the site. Notifying is best effort; a failure here
never stops a publish. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_static import read, subject_file  # noqa: E402

UA = "priorledger-indexnow (+https://priorledger.dev)"
ENDPOINT = "https://api.indexnow.org/indexnow"
LOC = re.compile(r"<loc>([^<]+)</loc>")
KEY_FILE = re.compile(r"^[0-9a-f]{32}\.txt$")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8")


def locs(text: str) -> list[str]:
    return [l.replace("&amp;", "&") for l in LOC.findall(text)]


def live_urls(base: str) -> set[str]:
    top = fetch(base + "/sitemap.xml")
    if "<sitemapindex" in top:
        return {u for part in locs(top) for u in locs(fetch(part))}
    return set(locs(top))


def built_urls(site: Path, base: str) -> set[str]:
    top = (site / "sitemap.xml").read_text(encoding="utf-8")
    if "<sitemapindex" in top:
        return {u for part in locs(top) for u in locs((site / part[len(base) + 1:]).read_text(encoding="utf-8"))}
    return set(locs(top))


def plan(ledger: Path, site: Path, base: str) -> list[str]:
    new = built_urls(site, base)
    try:
        old = live_urls(base)
    except Exception as exc:  # noqa: BLE001
        print(f"indexnow: could not read the live sitemap ({exc}); notifying nothing", file=sys.stderr)
        return []
    added = new - old
    if not added:
        return []
    by_page = {f"{base}/e/{e['id'][:12]}.html": e for e in read(ledger)}
    out = set(added)
    for u in added:
        e = by_page.get(u)
        if e:  # a new entry also changes its subject's page and its day's page
            out.add(f"{base}/s/{subject_file(e['subject'])}")
            out.add(f"{base}/d/{e['ts'][:10]}.html")
    out |= {f"{base}/", f"{base}/all/1.html", f"{base}/s/index.html", f"{base}/d/index.html"}
    return sorted(u for u in out if u in new or u == f"{base}/")


def find_key(web: Path) -> str:
    keys = [p for p in web.iterdir() if KEY_FILE.match(p.name)]
    if len(keys) != 1:
        raise SystemExit(f"indexnow: expected one key file in {web}, found {len(keys)}")
    return keys[0].stem


def submit(urls: list[str], base: str, key: str) -> int:
    if not urls:
        print("indexnow: nothing new to announce")
        return 0
    for i in range(0, len(urls), 10_000):
        body = json.dumps({"host": urlparse(base).netloc, "key": key, "keyLocation": f"{base}/{key}.txt",
                           "urlList": urls[i:i + 10_000]}).encode("utf-8")
        req = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
            "Content-Type": "application/json; charset=utf-8", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                print(f"indexnow: {len(urls[i:i + 10_000])} URLs sent, answer {r.status}")
        except Exception as exc:  # noqa: BLE001
            print(f"indexnow: not sent ({exc}); the sitemap still covers these pages", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("ledger", type=Path)
    p.add_argument("site", type=Path)
    p.add_argument("--base", required=True)
    p.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("submit")
    s.add_argument("urls", type=Path)
    s.add_argument("--base", required=True)
    s.add_argument("--web", type=Path, default=Path("web"))
    args = ap.parse_args(argv)
    base = args.base.rstrip("/")
    if args.cmd == "plan":
        urls = plan(args.ledger, args.site, base)
        args.out.write_text("".join(u + "\n" for u in urls), encoding="utf-8")
        print(f"indexnow: {len(urls)} pages to announce")
        return 0
    urls = [u for u in args.urls.read_text(encoding="utf-8").split() if u.startswith(base + "/")]
    return submit(urls, base, find_key(args.web))


if __name__ == "__main__":
    sys.exit(main())
