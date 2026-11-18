"""Accepting and writing entries, and publishing them.

Two modes, set by PRIORLEDGER_PUBLISH:

  none  Append to the working copy and stop. A steward commits and pushes by hand.
  git   Pull, append, verify, commit as the project identity, push. If the push
        fails, the local commit is undone and the proposal stays pending.

Every commit message names who accepted the entry, so the public history shows
whether a steward or a reviewer token made each decision.
"""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from . import core, db

_lock = threading.Lock()


class PublishError(RuntimeError):
    pass


def mode() -> str:
    return os.environ.get("PRIORLEDGER_PUBLISH", "none")


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=90, env=env)
    if res.returncode != 0:
        raise PublishError(f"git {args[0]} failed: {(res.stderr or res.stdout).strip()[:400]}")
    return res.stdout.strip()


def _identity() -> list[str]:
    name = os.environ.get("PRIORLEDGER_GIT_NAME", "first prior")
    email = os.environ.get("PRIORLEDGER_GIT_EMAIL", "priorledger@gmail.com")
    return ["-c", f"user.name={name}", "-c", f"user.email={email}"]


def _sync(repo: Path) -> None:
    if _git(repo, "status", "--porcelain", "--", "ledger"):
        raise PublishError("the working copy has uncommitted ledger changes; resolve them before publishing")
    _git(repo, "pull", "--ff-only", "--quiet")


def _commit_and_push(repo: Path, message: str) -> None:
    _git(repo, "add", "ledger")
    _git(repo, *_identity(), "commit", "--quiet", "-m", message)
    try:
        _git(repo, "push", "--quiet", "origin", "HEAD:main")
    except PublishError:
        # Someone else pushed first: drop our commit, catch up, and let the caller retry.
        _git(repo, "reset", "--hard", "--quiet", "HEAD~1")
        raise


def _undo_append(path: Path, sizes: dict) -> None:
    if path in sizes:
        with path.open("r+b") as fh:
            fh.truncate(sizes[path])
    else:
        path.unlink(missing_ok=True)


def _append_and_publish(repo: Path, build, message_for) -> dict:
    """build(ledger) -> entry. Appends it, verifies, and publishes per mode. Returns the entry."""
    ledger_dir = repo / "ledger"
    git = mode() == "git"
    attempts = 2 if git else 1
    last = None
    for _ in range(attempts):
        if git:
            _sync(repo)
        ledger = core.read_ledger(ledger_dir)
        entry = build(ledger)
        sizes = {f: f.stat().st_size for f in core.ledger_files(ledger_dir)}
        path = core.append(ledger_dir, entry)
        try:
            core.read_ledger(ledger_dir)
        except core.LedgerError as exc:
            _undo_append(path, sizes)
            raise PublishError(f"the ledger failed verification after appending to {path.name}: {exc}") from exc
        if not git:
            return entry
        try:
            _commit_and_push(repo, message_for(entry))
            return entry
        except PublishError as exc:
            last = exc
    raise PublishError(f"could not publish: {last}")


def accept(conn, repo: Path, pid: int, by: str, note: str | None = None) -> dict:
    """Accept a pending proposal into the ledger. `by` is shown publicly in the commit."""
    with _lock:
        p = db.proposal(conn, pid)
        if not p or p["status"] != "pending":
            raise core.LedgerError(f"proposal {pid} is not pending")

        def build(ledger):
            known = ledger.by_id()
            refs = p["content"]["basis"] + ([p["content"]["supersedes"]] if "supersedes" in p["content"] else [])
            missing = [r[:12] for r in refs if r not in known]
            if missing:
                raise core.LedgerError(f"refers to entries not in the ledger: {missing}")
            entry = core.place(p["content"], p["sig"], ledger.head)
            core.check_entry_shape(entry)
            core.verify_signature(entry)
            return entry

        def message(entry):
            c = p["content"]
            lines = [f"Accept proposal {pid}: {c['kind']} on {c['subject']}", "",
                     f"Entry {entry['id']}", f"Proposed by {c['actor']['id']} ({c['actor']['key']})",
                     f"Accepted by {by}"]
            if note:
                lines.append(f"Note: {note}")
            return "\n".join(lines)

        entry = _append_and_publish(repo, build, message)
        db.decide(conn, pid, "accepted", note, entry["id"], by)
        db.rebuild(conn, repo / "ledger")
        db.audit(conn, by, "accept", pid=pid, entry=entry["id"], published=mode() == "git")
        return entry


def reject(conn, pid: int, by: str, note: str) -> None:
    if not note or not note.strip():
        raise core.LedgerError("a rejection needs a note; the proposer can read it")
    db.decide(conn, pid, "rejected", note.strip(), None, by)
    db.audit(conn, by, "reject", pid=pid, note=note.strip())


def write(conn, repo: Path, content: dict, by: str) -> dict:
    """A human entry written by the steward (provenance stated or confirmed)."""
    if content.get("actor", {}).get("type") != "human":
        raise core.LedgerError("direct entries are human entries")
    if content.get("provenance") not in ("stated", "confirmed"):
        raise core.LedgerError("a human entry is 'stated' or 'confirmed'")
    core.check_content(content)
    with _lock:
        def build(ledger):
            known = ledger.by_id()
            for ref in content["basis"] + ([content["supersedes"]] if "supersedes" in content else []):
                if ref not in known:
                    raise core.LedgerError(f"unknown entry {ref[:12]}")
            return core.place(content, None, ledger.head)

        def message(entry):
            return (f"Write entry: {content['kind']} on {content['subject']}\n\n"
                    f"Entry {entry['id']}\nWritten by {by} as {content['actor']['id']} ({content['provenance']})")

        entry = _append_and_publish(repo, build, message)
        db.rebuild(conn, repo / "ledger")
        db.audit(conn, by, "write", entry=entry["id"], published=mode() == "git")
        return entry
