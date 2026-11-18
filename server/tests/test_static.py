"""The plain pages written for readers that don't run JavaScript."""
import shutil
import subprocess
import sys

from conftest import REPO


def test_plain_pages_cover_every_entry_and_escape_text(tmp_path):
    site = tmp_path / "site"
    shutil.copytree(REPO / "web", site)
    ledger = tmp_path / "ledger"
    shutil.copytree(REPO / "ledger", ledger)
    # an entry body with markup must come out as text, never as markup
    rows = (ledger / "000000.jsonl").read_text().splitlines()
    res = subprocess.run([sys.executable, str(REPO / "scripts" / "build_static.py"), str(ledger), str(site),
                          "--base", "https://example.test"], capture_output=True, text=True, check=True)
    assert "pages for %d entries" % len(rows) in res.stdout
    assert len(list((site / "e").glob("*.html"))) == len(rows)
    assert (site / "robots.txt").read_text().strip().endswith("Sitemap: https://example.test/sitemap.xml")
    sitemap = (site / "sitemap.xml").read_text()
    assert "https://example.test/e/" in sitemap and sitemap.count("<url>") >= len(rows) + 1
    index = (site / "index.html").read_text()
    assert 'class="entries compact snapshot"' in index and "A snapshot of %d entries" % len(rows) in index
    page = next((site / "e").glob("*.html")).read_text()
    assert "<script" not in page
    connect = (site / "connect.html").read_text()
    assert "claude mcp add --transport http" in connect and "## Precedent" in connect and "<script" not in connect
    assert "https://example.test/connect.html" in sitemap


def test_markup_in_a_body_is_escaped(tmp_path):
    sys.path.insert(0, str(REPO / "scripts"))
    import build_static
    e = {"id": "a" * 64, "prev": None, "ts": "2026-01-01T00:00:00Z", "kind": "claim", "subject": "x",
         "body": "<script>alert(1)</script> & more", "basis": [], "provenance": "stated", "actor": {"type": "human", "id": "<b>me</b>"}}
    out = build_static.Site([e], "https://example.test").entry_page(e)
    assert "<script>alert" not in out and "&lt;script&gt;alert(1)&lt;/script&gt; &amp; more" in out
    assert "<b>me</b>" not in out


def test_indexnow_announces_only_what_is_new(tmp_path):
    """plan: diff the live sitemap with the new build; submit: needs exactly one key file."""
    import functools
    import http.server
    import threading

    script = str(REPO / "scripts" / "indexnow.py")
    lines = (REPO / "ledger" / "000000.jsonl").read_text().splitlines()
    old_ledger, new_ledger = tmp_path / "old", tmp_path / "new"
    for d, rows in ((old_ledger, lines[:-1]), (new_ledger, lines)):
        d.mkdir()
        (d / "000000.jsonl").write_text("\n".join(rows) + "\n")
    live = tmp_path / "live"
    shutil.copytree(REPO / "web", live)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(live))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        build = [sys.executable, str(REPO / "scripts" / "build_static.py")]
        subprocess.run(build + [str(old_ledger), str(live), "--base", base], check=True, capture_output=True)
        site = tmp_path / "site"
        shutil.copytree(REPO / "web", site)
        subprocess.run(build + [str(new_ledger), str(site), "--base", base], check=True, capture_output=True)
        out = tmp_path / "urls.txt"
        subprocess.run([sys.executable, script, "plan", str(new_ledger), str(site), "--base", base, "--out", str(out)],
                       check=True, capture_output=True)
        urls = out.read_text().split()
        import json
        newest = json.loads(lines[-1])
        assert f"{base}/e/{newest['id'][:12]}.html" in urls
        assert f"{base}/d/{newest['ts'][:10]}.html" in urls and f"{base}/" in urls
        assert not any(f"/e/{json.loads(l)['id'][:12]}.html" in u for l in lines[:-1] for u in urls)
        # nothing new: nothing announced
        subprocess.run([sys.executable, script, "plan", str(old_ledger), str(live), "--base", base, "--out", str(out)],
                       check=True, capture_output=True)
        assert out.read_text() == ""
    finally:
        srv.shutdown()
    keys = [p.name for p in (REPO / "web").iterdir() if len(p.stem) == 32 and p.suffix == ".txt"]
    assert len(keys) == 1 and (REPO / "web" / keys[0]).read_text() == keys[0][:-4]
