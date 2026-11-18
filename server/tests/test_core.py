import json

import pytest

from priorledger import core
from conftest import REPO


def test_repository_ledger_verifies():
    ledger = core.read_ledger(REPO / "ledger")
    assert ledger.entries[0]["id"] == "7aed23cb6098ed92029f22bc6af9b7e770b480990ed2b90873be24d4591cbaa1"
    assert ledger.entries[0]["prev"] is None


def test_canonical_matches_documented_rules():
    assert core.canonical({"b": 1, "a": [True, None, "é"]}) == '{"a":[true,null,"é"],"b":1}'.encode()


def content(**over):
    base = {"actor": {"type": "agent", "id": "t/1"}, "kind": "claim", "subject": "a-b", "body": "x",
            "basis": [], "provenance": "inferred"}
    base.update(over)
    return base


def test_signature_covers_content_not_placement():
    priv, pub = core.generate_key()
    c = content(actor={"type": "agent", "id": "t/1", "key": pub})
    sig = core.sign_content(c, priv)
    a = core.place(c, sig, prev=None, ts="2026-01-01T00:00:00Z")
    b = core.place(c, sig, prev="0" * 64, ts="2027-01-01T00:00:00Z")
    core.verify_signature(a)
    core.verify_signature(b)          # placement may differ; the author's words may not
    assert a["id"] != b["id"]
    with pytest.raises(core.LedgerError):
        core.verify_signature({**a, "body": "changed"})


@pytest.mark.parametrize("bad, why", [
    (dict(kind="opinion"), "kind"),
    (dict(subject="Has Caps"), "subject"),
    (dict(body="   "), "body"),
    (dict(basis=["xyz"]), "basis"),
    (dict(provenance="certain"), "provenance"),
    (dict(kind="correction"), "supersedes"),
    (dict(extra=1), "unexpected"),
])
def test_check_content_rejects(bad, why):
    with pytest.raises(core.LedgerError, match=why):
        core.check_content(content(**bad))


def test_chain_tampering_is_caught(repo):
    priv, pub = core.generate_key()
    head = core.read_ledger(repo / "ledger").head
    c = content(actor={"type": "agent", "id": "t/1", "key": pub}, basis=[head])
    core.append(repo / "ledger", core.place(c, core.sign_content(c, priv), head))
    assert len(core.read_ledger(repo / "ledger").entries) == 2

    path = repo / "ledger" / "000000.jsonl"
    lines = path.read_text().splitlines()
    first = json.loads(lines[0])
    first["body"] += " (edited)"
    path.write_text("\n".join([json.dumps(first)] + lines[1:]) + "\n")
    with pytest.raises(core.LedgerError, match="id does not match"):
        core.read_ledger(repo / "ledger")


def test_agents_cannot_state(repo):
    priv, pub = core.generate_key()
    head = core.read_ledger(repo / "ledger").head
    c = content(actor={"type": "agent", "id": "t/1", "key": pub}, provenance="stated")
    core.append(repo / "ledger", core.place(c, core.sign_content(c, priv), head))
    with pytest.raises(core.LedgerError, match="cannot author a 'stated'"):
        core.read_ledger(repo / "ledger")


def test_unsigned_agent_entries_are_refused(repo):
    head = core.read_ledger(repo / "ledger").head
    core.append(repo / "ledger", core.place(content(), None, head))
    with pytest.raises(core.LedgerError, match="must be signed"):
        core.read_ledger(repo / "ledger")


def test_files_roll_over(repo, monkeypatch):
    monkeypatch.setattr(core, "LINES_PER_FILE", 2)
    priv, pub = core.generate_key()
    for _ in range(3):
        head = core.read_ledger(repo / "ledger").head
        c = content(actor={"type": "agent", "id": "t/1", "key": pub}, body=f"n{_}")
        core.append(repo / "ledger", core.place(c, core.sign_content(c, priv), head))
    names = [p.name for p in core.ledger_files(repo / "ledger")]
    assert names == ["000000.jsonl", "000001.jsonl"]
    assert len(core.read_ledger(repo / "ledger").entries) == 4
