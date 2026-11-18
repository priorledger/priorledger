"""The ledger's rules, in one place: canonical form, ids, the chain, signatures.

Everything else in this package — the database, the API, the MCP server, the
steward tools — calls into this module and never re-implements these rules.
The browser has its own copy of the canonical form in web/assets/ledger.js;
tests/test_parity.py keeps the two byte-identical.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

KINDS = ("claim", "decision", "correction", "dispute", "supersession", "succession")
PROVENANCE = ("stated", "inferred", "confirmed")
ACTOR_TYPES = ("human", "agent")

REQUIRED = ("id", "prev", "ts", "actor", "kind", "subject", "body", "basis", "provenance")
# Placement fields are set when an entry is accepted into the ledger, not by its author.
PLACEMENT = ("id", "sig", "prev", "ts")

ID_RE = re.compile(r"^[0-9a-f]{64}$")
SUBJECT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
KEY_RE = re.compile(r"^ed25519:[0-9a-f]{64}$")
SIG_RE = re.compile(r"^[0-9a-f]{128}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

MAX_BODY = 20_000
MAX_ACTOR_ID = 120
MAX_BASIS = 32
LINES_PER_FILE = 10_000


class LedgerError(ValueError):
    """An entry or a chain that breaks the ledger's rules."""


# ---------------------------------------------------------------- canonical form

def canonical(obj: dict) -> bytes:
    """Sorted keys, no insignificant whitespace, UTF-8, non-ASCII left as is."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def entry_id(entry: dict) -> str:
    """SHA-256 of the entry without its id and sig."""
    return hashlib.sha256(canonical({k: v for k, v in entry.items() if k not in ("id", "sig")})).hexdigest()


def content_form(entry: dict) -> dict:
    """What an author signs: the entry minus the fields the steward sets on acceptance."""
    return {k: v for k, v in entry.items() if k not in PLACEMENT}


def now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- signatures

def _ed25519():
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:  # pragma: no cover
        raise LedgerError("signature checks need the 'cryptography' package") from exc
    return ed25519


def generate_key() -> tuple[str, str]:
    """Return (private_hex, public_key_string) for a new Ed25519 key."""
    from cryptography.hazmat.primitives import serialization

    ed = _ed25519()
    priv = ed.Ed25519PrivateKey.generate()
    raw_priv = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                  serialization.NoEncryption())
    raw_pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return raw_priv.hex(), "ed25519:" + raw_pub.hex()


def sign_content(content: dict, private_hex: str) -> str:
    ed = _ed25519()
    priv = ed.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    return priv.sign(canonical(content_form(content))).hex()


def verify_signature(entry: dict) -> None:
    """Raise LedgerError unless entry['sig'] is a valid signature by entry['actor']['key']."""
    from cryptography.exceptions import InvalidSignature

    key = entry.get("actor", {}).get("key")
    sig = entry.get("sig")
    if not key or not KEY_RE.match(key):
        raise LedgerError("signed entry needs actor.key of the form ed25519:<64 hex>")
    if not sig or not SIG_RE.match(sig):
        raise LedgerError("sig must be 128 hex characters")
    ed = _ed25519()
    pub = ed.Ed25519PublicKey.from_public_bytes(bytes.fromhex(key.split(":", 1)[1]))
    try:
        pub.verify(bytes.fromhex(sig), canonical(content_form(entry)))
    except InvalidSignature as exc:
        raise LedgerError("signature does not match the content") from exc


# ---------------------------------------------------------------- validation

def check_content(content: dict) -> None:
    """Validate the author-supplied part of an entry (no placement fields)."""
    allowed = {"actor", "kind", "subject", "body", "basis", "provenance", "supersedes"}
    extra = set(content) - allowed
    if extra:
        raise LedgerError(f"unexpected fields: {sorted(extra)}")
    actor = content.get("actor")
    if not isinstance(actor, dict) or set(actor) - {"type", "id", "key"}:
        raise LedgerError("actor must be an object with type, id and optional key")
    if actor.get("type") not in ACTOR_TYPES:
        raise LedgerError(f"actor.type must be one of {ACTOR_TYPES}")
    if not isinstance(actor.get("id"), str) or not 0 < len(actor["id"]) <= MAX_ACTOR_ID:
        raise LedgerError(f"actor.id must be a non-empty string of at most {MAX_ACTOR_ID} characters")
    if "key" in actor and not (isinstance(actor["key"], str) and KEY_RE.match(actor["key"])):
        raise LedgerError("actor.key must be ed25519:<64 hex>")
    if content.get("kind") not in KINDS:
        raise LedgerError(f"kind must be one of {KINDS}")
    if not isinstance(content.get("subject"), str) or not SUBJECT_RE.match(content["subject"]):
        raise LedgerError("subject must be lowercase letters, digits and hyphens, at most 64 characters")
    body = content.get("body")
    if not isinstance(body, str) or not body.strip():
        raise LedgerError("body must be non-empty text")
    if len(body) > MAX_BODY:
        raise LedgerError(f"body is longer than {MAX_BODY} characters")
    basis = content.get("basis")
    if not isinstance(basis, list) or len(basis) > MAX_BASIS or not all(isinstance(b, str) and ID_RE.match(b) for b in basis):
        raise LedgerError(f"basis must be a list of at most {MAX_BASIS} entry ids")
    if len(set(basis)) != len(basis):
        raise LedgerError("basis lists an entry twice")
    if content.get("provenance") not in PROVENANCE:
        raise LedgerError(f"provenance must be one of {PROVENANCE}")
    if "supersedes" in content and not (isinstance(content["supersedes"], str) and ID_RE.match(content["supersedes"])):
        raise LedgerError("supersedes must be an entry id")
    if content["kind"] in ("correction", "supersession") and "supersedes" not in content:
        raise LedgerError(f"a {content['kind']} entry must name the entry it supersedes")


def check_entry_shape(entry: dict) -> None:
    missing = [f for f in REQUIRED if f not in entry]
    if missing:
        raise LedgerError(f"missing fields {missing}")
    if not ID_RE.match(entry["id"]):
        raise LedgerError("id must be 64 hex characters")
    if entry["prev"] is not None and not ID_RE.match(entry["prev"]):
        raise LedgerError("prev must be an entry id or null")
    if not isinstance(entry["ts"], str) or not TS_RE.match(entry["ts"]):
        raise LedgerError("ts must look like 2026-11-18T01:15:00Z")
    check_content(content_form(entry))


# ---------------------------------------------------------------- the chain

@dataclass
class Ledger:
    entries: list[dict]

    @property
    def head(self) -> str | None:
        return self.entries[-1]["id"] if self.entries else None

    def by_id(self) -> dict[str, dict]:
        return {e["id"]: e for e in self.entries}


def ledger_files(ledger_dir: Path) -> list[Path]:
    return sorted(Path(ledger_dir).glob("[0-9][0-9][0-9][0-9][0-9][0-9].jsonl"))


def read_ledger(ledger_dir: Path, check_signatures: bool = True) -> Ledger:
    """Load and fully verify the ledger. Raises LedgerError at the first problem."""
    entries: list[dict] = []
    prev = None
    seen: set[str] = set()
    for path in ledger_files(ledger_dir):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            where = f"{path.name}:{lineno}"
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LedgerError(f"{where}: not valid JSON") from exc
            try:
                check_entry_shape(entry)
                if entry["id"] != entry_id(entry):
                    raise LedgerError("id does not match the entry's content")
                if entry["prev"] != prev:
                    raise LedgerError("chain break: prev is not the previous entry's id")
                for ref in entry["basis"] + ([entry["supersedes"]] if "supersedes" in entry else []):
                    if ref not in seen:
                        raise LedgerError(f"refers to {ref[:12]}…, which is not an earlier entry")
                if "sig" in entry and check_signatures:
                    verify_signature(entry)
                if entry["actor"]["type"] == "agent":
                    if "sig" not in entry:
                        raise LedgerError("agent entries must be signed")
                    if entry["provenance"] == "stated":
                        raise LedgerError("an agent cannot author a 'stated' entry")
            except LedgerError as exc:
                raise LedgerError(f"{where}: {exc}") from None
            entries.append(entry)
            seen.add(entry["id"])
            prev = entry["id"]
    return Ledger(entries)


def place(content: dict, sig: str | None, prev: str | None, ts: str | None = None) -> dict:
    """Turn accepted content into a full ledger entry."""
    entry = dict(content)
    entry["prev"] = prev
    entry["ts"] = ts or now_ts()
    if sig is not None:
        entry["sig"] = sig
    entry["id"] = entry_id(entry)
    return entry


def append(ledger_dir: Path, entry: dict) -> Path:
    """Append one entry to the newest ledger file, starting a new file when it is full."""
    ledger_dir = Path(ledger_dir)
    files = ledger_files(ledger_dir)
    target = files[-1] if files else ledger_dir / "000000.jsonl"
    if target.exists() and sum(1 for line in target.read_text(encoding="utf-8").splitlines() if line.strip()) >= LINES_PER_FILE:
        target = ledger_dir / f"{int(target.stem) + 1:06d}.jsonl"
    with target.open("a", encoding="utf-8") as fh:
        fh.write(canonical(entry).decode("utf-8") + "\n")
    return target


def supersession_map(entries: list[dict]) -> dict[str, str]:
    """Map each superseded entry id to the id of the latest entry that supersedes it."""
    out: dict[str, str] = {}
    for e in entries:
        if "supersedes" in e:
            out[e["supersedes"]] = e["id"]
    return out
