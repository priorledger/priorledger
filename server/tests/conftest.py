"""Shared fixtures. Database tests need PRIORLEDGER_TEST_DSN pointing at a disposable database."""
import os
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def repo(tmp_path):
    """A scratch repository holding only the genesis entry, so tests don't depend on the ledger's growth."""
    root = tmp_path / "repo"
    (root / "ledger").mkdir(parents=True)
    genesis = (REPO / "ledger" / "000000.jsonl").read_text(encoding="utf-8").splitlines()[0]
    (root / "ledger" / "000000.jsonl").write_text(genesis + "\n", encoding="utf-8")
    if (REPO / "web").is_dir():
        shutil.copytree(REPO / "web", root / "web")
    return root


@pytest.fixture
def dsn():
    value = os.environ.get("PRIORLEDGER_TEST_DSN")
    if not value:
        pytest.skip("set PRIORLEDGER_TEST_DSN to run database tests")
    import psycopg

    with psycopg.connect(value, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS entries, proposals, meta")
    return value
