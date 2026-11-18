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


def test_markup_in_a_body_is_escaped(tmp_path):
    sys.path.insert(0, str(REPO / "scripts"))
    import build_static
    e = {"id": "a" * 64, "prev": None, "ts": "2026-01-01T00:00:00Z", "kind": "claim", "subject": "x",
         "body": "<script>alert(1)</script> & more", "basis": [], "provenance": "stated", "actor": {"type": "human", "id": "<b>me</b>"}}
    out = build_static.Site([e], "https://example.test").entry_page(e)
    assert "<script>alert" not in out and "&lt;script&gt;alert(1)&lt;/script&gt; &amp; more" in out
    assert "<b>me</b>" not in out
