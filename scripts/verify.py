#!/usr/bin/env python3
"""Verify the Prior Ledger: recompute every entry id and check the hash chain.

Usage: python3 scripts/verify.py ledger/
Exits 0 if the ledger verifies, 1 otherwise. No dependencies beyond stdlib.
"""
import hashlib
import json
import sys
from pathlib import Path

REQUIRED = {"id", "prev", "ts", "actor", "kind", "subject", "body", "basis", "provenance"}
KINDS = {"claim", "decision", "correction", "dispute", "supersession", "succession"}
PROVENANCE = {"stated", "inferred", "confirmed"}


def canonical_id(entry: dict) -> str:
    stripped = {k: v for k, v in entry.items() if k not in ("id", "sig")}
    blob = json.dumps(stripped, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def main(ledger_dir: str) -> int:
    files = sorted(Path(ledger_dir).glob("*.jsonl"))
    if not files:
        print(f"no ledger files found in {ledger_dir}")
        return 1
    prev_id = None
    seen = set()
    count = 0
    for path in files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            where = f"{path}:{lineno}"
            entry = json.loads(line)
            missing = REQUIRED - entry.keys()
            if missing:
                print(f"FAIL {where}: missing fields {sorted(missing)}")
                return 1
            if entry["kind"] not in KINDS:
                print(f"FAIL {where}: unknown kind {entry['kind']!r}")
                return 1
            if entry["provenance"] not in PROVENANCE:
                print(f"FAIL {where}: unknown provenance {entry['provenance']!r}")
                return 1
            if entry["actor"].get("type") == "agent":
                if "sig" not in entry or "key" not in entry["actor"]:
                    print(f"FAIL {where}: agent entries must be signed (actor.key and sig)")
                    return 1
                if entry["provenance"] == "stated":
                    print(f"FAIL {where}: an agent entry cannot have provenance 'stated'")
                    return 1
            expect = canonical_id(entry)
            if entry["id"] != expect:
                print(f"FAIL {where}: id mismatch\n  stored   {entry['id']}\n  computed {expect}")
                return 1
            if entry["prev"] != prev_id:
                print(f"FAIL {where}: chain break\n  stored prev   {entry['prev']}\n  expected prev {prev_id}")
                return 1
            for ref in entry["basis"] + ([entry["supersedes"]] if "supersedes" in entry else []):
                if ref not in seen:
                    print(f"FAIL {where}: refers to {ref[:12]}..., which is not an earlier entry")
                    return 1
            seen.add(entry["id"])
            prev_id = entry["id"]
            count += 1
    print(f"OK: {count} entries verified, chain intact, head {prev_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "ledger/"))
