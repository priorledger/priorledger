"""The browser's file-by-file reader, run in node against a real multi-file ledger."""
import json
import shutil
import subprocess

import pytest

from priorledger import core
from conftest import REPO

NODE = r"""
const fs = require('fs'), path = require('path');
const L = require(process.argv[1]);
const dir = process.argv[2], lines = +process.argv[3];
global.fetch = async function (url, opts) {
  const p = path.join(dir, url.replace(/^ledger\//, ''));
  if (!fs.existsSync(p)) return { ok: false, status: 404, text: async () => '' };
  const text = fs.readFileSync(p, 'utf8');
  return { ok: true, status: 200, text: async () => (opts && opts.method === 'HEAD') ? '' : text };
};
(async () => {
  const r = new L.Reader('ledger/', { linesPerFile: lines });
  const files = await r.init();
  await r.load(files - 1);
  const newest = { total: r.total(), loaded: r.items().length, all: r.allVerified() };
  const res = await r.verifyAll();
  process.stdout.write(JSON.stringify({ files, newest, ok: res.ok, failure: res.failure, all: r.allVerified(), signed: r.signed, items: r.items().length }));
})();
"""


def build(tmp_path, monkeypatch, n=12, lines=5):
    monkeypatch.setattr(core, "LINES_PER_FILE", lines)
    d = tmp_path / "ledger"
    d.mkdir()
    priv, pub = core.generate_key()
    g = {"actor": {"type": "human", "id": "steward"}, "kind": "decision", "subject": "start", "body": "Start.", "basis": [], "provenance": "stated"}
    e = core.place(g, None, None)
    core.append(d, e)
    ids = [e["id"]]
    for i in range(1, n):
        c = {"actor": {"type": "agent", "id": "t/1", "key": pub}, "kind": "claim", "subject": "s%d" % (i % 3),
             "body": "Entry %d." % i, "basis": [ids[0], ids[-1]] if i > 1 else [ids[0]], "provenance": "inferred"}
        e = core.place(c, core.sign_content(c, priv), ids[-1])
        core.append(d, e)
        ids.append(e["id"])
    return d


def run(d, lines=5):
    res = subprocess.run(["node", "-e", NODE, str(REPO / "web" / "assets" / "ledger.js"), str(d), str(lines)],
                         capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


@needs_node
def test_reader_checks_newest_first_then_everything(tmp_path, monkeypatch):
    d = build(tmp_path, monkeypatch)
    assert sorted(p.name for p in d.iterdir()) == ["000000.jsonl", "000001.jsonl", "000002.jsonl"]
    out = run(d)
    assert out["files"] == 3
    assert out["newest"] == {"total": 12, "loaded": 2, "all": False}
    assert out["ok"] and out["all"] and out["items"] == 12 and out["signed"] == 11


@needs_node
def test_reader_refuses_an_altered_middle_file(tmp_path, monkeypatch):
    d = build(tmp_path, monkeypatch)
    p = d / "000001.jsonl"
    rows = p.read_text().splitlines()
    rows[2] = rows[2].replace("Entry 7.", "Entry seven.")
    p.write_text("\n".join(rows) + "\n")
    out = run(d)
    assert not out["ok"] and out["failure"]["at"] == 7


@needs_node
def test_reader_refuses_a_missing_entry(tmp_path, monkeypatch):
    d = build(tmp_path, monkeypatch)
    p = d / "000000.jsonl"
    rows = p.read_text().splitlines()
    p.write_text("\n".join(rows[:-1]) + "\n")
    out = run(d)
    assert not out["ok"] and "not full" in out["failure"]["reason"]


@needs_node
def test_reader_refuses_a_swapped_file(tmp_path, monkeypatch):
    d = build(tmp_path, monkeypatch)
    other = build(tmp_path / "other", monkeypatch) if (tmp_path / "other").mkdir() is None else None
    shutil.copy(other / "000001.jsonl", d / "000001.jsonl")
    out = run(d)
    assert not out["ok"]


def test_browser_and_server_agree_on_file_size():
    text = (REPO / "web" / "assets" / "ledger.js").read_text()
    assert "const LINES_PER_FILE = %d;" % core.LINES_PER_FILE in text
