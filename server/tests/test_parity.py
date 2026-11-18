"""The browser's canonical form must be byte-identical to the server's. Needs node."""
import json
import shutil
import subprocess

import pytest

from priorledger import core
from conftest import REPO

TRICKY = [
    {"actor": {"type": "agent", "id": "ü/✓/日本"}, "body": "line\nbreak\ttab \"quote\" back\\slash \u0001 \u007f — 𝄞",
     "basis": [], "kind": "claim", "provenance": "inferred", "subject": "x"},
    {"z": None, "a": [True, False, {"y": "1", "b": []}], "m": "</script>"},
]

NODE = """
const L = require(process.argv[2]);
const items = JSON.parse(require('fs').readFileSync(0, 'utf8'));
(async () => {
  const out = [];
  for (const it of items) out.push({ canonical: L.canonical(it), id: await L.entryId(it) });
  const ledger = items.filter(e => e.prev !== undefined && e.id);
  out.push(await L.verify(ledger));
  process.stdout.write(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_python_and_browser_agree(tmp_path):
    genesis = core.read_ledger(REPO / "ledger").entries
    priv, pub = core.generate_key()
    c = {"actor": {"type": "agent", "id": "parity/✓", "key": pub}, "kind": "claim", "subject": "parity",
         "body": "Signed — ünïcode survives.", "basis": [genesis[0]["id"]], "provenance": "inferred"}
    signed = core.place(c, core.sign_content(c, priv), genesis[-1]["id"])
    items = TRICKY + genesis + [signed]
    script = tmp_path / "parity.js"
    script.write_text(NODE)
    res = subprocess.run(["node", str(script), str(REPO / "web" / "assets" / "ledger.js")],
                         input=json.dumps(items, ensure_ascii=False), capture_output=True, text=True, check=True)
    out = json.loads(res.stdout)
    for item, got in zip(items, out):
        assert got["canonical"].encode() == core.canonical(item)
        assert got["id"] == core.entry_id(item)
    verdict = out[-1]
    assert verdict["ok"], verdict
    assert verdict["count"] == len(genesis) + 1
    assert verdict["signed"] == sum(1 for e in genesis + [signed] if "sig" in e), "node should check every signature"
