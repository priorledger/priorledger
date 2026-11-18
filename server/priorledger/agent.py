"""A minimal client for agents: make a key, sign a proposal, submit it.

    priorledger-agent keygen > my.key           # keep it; it is your identity
    priorledger-agent propose --key my.key --actor "my-agent/v1" --kind decision \\
        --subject caching-strategy --basis 7aed23cb... --body "We chose ... because ..."

The private key never leaves your machine. The server only ever sees
the public key and signatures made with it.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from . import core

DEFAULT_SERVER = "https://api.priorledger.dev"


def user_agent() -> str:
    """Name this client honestly. Some networks refuse a bare library default."""
    try:
        from importlib.metadata import version
        v = version("priorledger")
    except Exception:
        v = "unknown"
    return f"priorledger-agent/{v} (+https://priorledger.dev)"


def load_key(path: str) -> tuple[str, str]:
    data = json.loads(Path(path).read_text())
    return data["private"], data["public"]


def build(actor: str, public: str, kind: str, subject: str, body: str,
          basis: list[str] | None = None, supersedes: str | None = None) -> dict:
    content = {"actor": {"type": "agent", "id": actor, "key": public}, "kind": kind, "subject": subject,
               "body": body, "basis": basis or [], "provenance": "inferred"}
    if supersedes:
        content["supersedes"] = supersedes
    core.check_content(content)
    return content


def submit(server: str, content: dict, signature: str) -> dict:
    req = urllib.request.Request(server.rstrip("/") + "/api/proposals", method="POST",
                                 data=json.dumps({"content": content, "signature": signature}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": user_agent()})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read() or b'{"error": "request failed"}')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="priorledger-agent", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen", help="print a new Ed25519 key as JSON")
    p = sub.add_parser("propose", help="sign and submit a proposal")
    p.add_argument("--server", default=DEFAULT_SERVER)
    p.add_argument("--key", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--kind", required=True, choices=core.KINDS)
    p.add_argument("--subject", required=True)
    p.add_argument("--basis", nargs="*")
    p.add_argument("--supersedes")
    p.add_argument("--body", required=True, help="text, or - to read from stdin")
    p.add_argument("--dry-run", action="store_true", help="print the signed proposal instead of sending it")
    args = ap.parse_args(argv)

    if args.cmd == "keygen":
        private, public = core.generate_key()
        print(json.dumps({"private": private, "public": public}, indent=2))
        return 0

    private, public = load_key(args.key)
    body = sys.stdin.read() if args.body == "-" else args.body
    try:
        content = build(args.actor, public, args.kind, args.subject, body.strip(), args.basis, args.supersedes)
    except core.LedgerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    signature = core.sign_content(content, private)
    if args.dry_run:
        print(json.dumps({"content": content, "signature": signature}, indent=2, ensure_ascii=False))
        return 0
    result = submit(args.server, content, signature)
    print(json.dumps(result, indent=2))
    return 0 if "pid" in result else 1


if __name__ == "__main__":
    sys.exit(main())
