"""Steward tools: review proposals, accept them into the ledger, write human entries.

Run on the server as the ledger's own user, e.g.
    sudo -u priorledger /srv/priorledger/venv/bin/priorledger-steward pending

Accepting appends to the ledger and rebuilds the index. With PRIORLEDGER_PUBLISH=git
it also commits and pushes, naming who accepted; otherwise the steward reviews
`git diff` and commits by hand. Nothing is part of the record until it is pushed.
"""
from __future__ import annotations

import argparse
import sys
import textwrap

from . import core, db, publish
from .app import settings


def _show(p: dict) -> None:
    c = p["content"]
    print(f"proposal {p['pid']}  [{p['status']}]  received {p['received']}")
    print(f"  {c['kind']} · {c['subject']} · {c['actor']['type']}:{c['actor']['id']}")
    print(f"  key {c['actor'].get('key', '-')}")
    if c["basis"]:
        print("  basis " + ", ".join(b[:12] + "…" for b in c["basis"]))
    if c.get("supersedes"):
        print(f"  supersedes {c['supersedes'][:12]}…")
    print(textwrap.indent(textwrap.fill(c["body"], 92), "    "))
    if p.get("note"):
        print(f"  note: {p['note']}")
    print()


def cmd_verify(args) -> int:
    ledger = core.read_ledger(settings.ledger_dir)
    print(f"OK: {len(ledger.entries)} entries, signatures and chain verified, head {ledger.head}")
    return 0


def cmd_reindex(args) -> int:
    with db.connect(settings.dsn) as conn:
        db.init(conn)
        ledger = db.rebuild(conn, settings.ledger_dir)
    print(f"index rebuilt: {len(ledger.entries)} entries, head {ledger.head}")
    return 0


def cmd_pending(args) -> int:
    with db.connect(settings.dsn) as conn:
        items = db.pending(conn, args.limit)
    if not items:
        print("no proposals waiting")
    for p in items:
        _show(p)
    return 0


def cmd_show(args) -> int:
    with db.connect(settings.dsn) as conn:
        p = db.proposal(conn, args.pid)
    if not p:
        print("no such proposal", file=sys.stderr)
        return 1
    _show(p)
    return 0


def cmd_accept(args) -> int:
    with db.connect(settings.dsn) as conn:
        entry = publish.accept(conn, settings.repo, args.pid, "steward", args.note)
    print(f"accepted proposal {args.pid} as entry {entry['id']}")
    if publish.mode() != "git":
        print("review with `git diff`, then commit and push as the project identity")
    return 0


def cmd_reject(args) -> int:
    with db.connect(settings.dsn) as conn:
        publish.reject(conn, args.pid, "steward", args.note)
    print(f"rejected proposal {args.pid}")
    return 0


def cmd_write(args) -> int:
    """A human entry, written directly by the steward (provenance stated or confirmed)."""
    body = sys.stdin.read() if args.body == "-" else args.body
    content = {"actor": {"type": "human", "id": args.actor}, "kind": args.kind, "subject": args.subject,
               "body": body.strip(), "basis": args.basis or [], "provenance": args.provenance}
    if args.supersedes:
        content["supersedes"] = args.supersedes
    with db.connect(settings.dsn) as conn:
        entry = publish.write(conn, settings.repo, content, "steward")
    print(f"wrote entry {entry['id']}")
    if publish.mode() != "git":
        print("review with `git diff`, then commit and push as the project identity")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="priorledger-steward", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify", help="verify chain and signatures").set_defaults(fn=cmd_verify)
    sub.add_parser("reindex", help="rebuild the database index from the ledger").set_defaults(fn=cmd_reindex)
    s = sub.add_parser("pending", help="list proposals waiting for a decision")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(fn=cmd_pending)
    s = sub.add_parser("show", help="show one proposal")
    s.add_argument("pid", type=int)
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("accept", help="append a proposal to the ledger")
    s.add_argument("pid", type=int)
    s.add_argument("--note")
    s.set_defaults(fn=cmd_accept)
    s = sub.add_parser("reject", help="decline a proposal")
    s.add_argument("pid", type=int)
    s.add_argument("--note", required=True, help="why; the proposer can read this")
    s.set_defaults(fn=cmd_reject)
    s = sub.add_parser("write", help="write a human entry directly")
    s.add_argument("--actor", default="first-prior")
    s.add_argument("--kind", required=True, choices=core.KINDS)
    s.add_argument("--subject", required=True)
    s.add_argument("--provenance", default="stated", choices=("stated", "confirmed"))
    s.add_argument("--basis", nargs="*")
    s.add_argument("--supersedes")
    s.add_argument("--body", required=True, help="text, or - to read from stdin")
    s.set_defaults(fn=cmd_write)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (core.LedgerError, publish.PublishError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
