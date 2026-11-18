"""Postgres: a disposable index over the ledger, plus the proposal queue.

The index can be dropped at any time and rebuilt from the repository. The
proposal queue is the only state that lives here alone, and it holds nothing
the ledger depends on: an unaccepted proposal is not part of the record.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import core

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    seq         integer PRIMARY KEY,
    id          text UNIQUE NOT NULL,
    prev        text,
    ts          timestamptz NOT NULL,
    actor_type  text NOT NULL,
    actor_id    text NOT NULL,
    actor_key   text,
    kind        text NOT NULL,
    subject     text NOT NULL,
    body        text NOT NULL,
    basis       text[] NOT NULL,
    provenance  text NOT NULL,
    supersedes  text,
    superseded_by text,
    raw         jsonb NOT NULL,
    tsv         tsvector GENERATED ALWAYS AS
                (setweight(to_tsvector('english', replace(subject, '-', ' ')), 'A') ||
                 setweight(to_tsvector('english', body), 'B')) STORED
);
CREATE INDEX IF NOT EXISTS entries_tsv ON entries USING gin (tsv);
CREATE INDEX IF NOT EXISTS entries_subject ON entries (subject);

CREATE TABLE IF NOT EXISTS proposals (
    pid         bigserial PRIMARY KEY,
    received    timestamptz NOT NULL DEFAULT now(),
    content     jsonb NOT NULL,
    sig         text NOT NULL,
    actor_key   text NOT NULL,
    status      text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected')),
    decided_at  timestamptz,
    note        text,
    entry_id    text,
    source_hash text
);
CREATE INDEX IF NOT EXISTS proposals_status ON proposals (status);
CREATE INDEX IF NOT EXISTS proposals_key ON proposals (actor_key, status);
CREATE UNIQUE INDEX IF NOT EXISTS proposals_once ON proposals (sig);

CREATE TABLE IF NOT EXISTS meta (k text PRIMARY KEY, v text NOT NULL);
"""

INDEX_LOCK = 7_340_001  # advisory lock key for rebuilds


@contextmanager
def connect(dsn: str):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=False) as conn:
        yield conn


def init(conn) -> None:
    conn.execute(SCHEMA)
    conn.commit()


def rebuild(conn, ledger_dir: Path) -> core.Ledger:
    """Verify the ledger and replace the index with it, atomically."""
    ledger = core.read_ledger(ledger_dir)
    sup = core.supersession_map(ledger.entries)
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (INDEX_LOCK,))
        conn.execute("DELETE FROM entries")
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO entries (seq, id, prev, ts, actor_type, actor_id, actor_key, kind, subject,
                                         body, basis, provenance, supersedes, superseded_by, raw)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [
                    (i, e["id"], e["prev"], e["ts"], e["actor"]["type"], e["actor"]["id"], e["actor"].get("key"),
                     e["kind"], e["subject"], e["body"], e["basis"], e["provenance"], e.get("supersedes"),
                     sup.get(e["id"]), Jsonb(e))
                    for i, e in enumerate(ledger.entries)
                ],
            )
        for k, v in (("head", ledger.head or ""), ("count", str(len(ledger.entries))), ("indexed_at", core.now_ts())):
            conn.execute("INSERT INTO meta (k, v) VALUES (%s, %s) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v", (k, v))
    return ledger


# ---------------------------------------------------------------- reads

def _entry_out(row: dict) -> dict:
    e = dict(row["raw"])
    out = {"number": row["seq"], "entry": e}
    if row.get("superseded_by"):
        out["superseded_by"] = row["superseded_by"]
    return out


def head(conn) -> dict:
    rows = {r["k"]: r["v"] for r in conn.execute("SELECT k, v FROM meta").fetchall()}
    return {"head": rows.get("head") or None, "count": int(rows.get("count", 0)), "indexed_at": rows.get("indexed_at")}


def get_entry(conn, ident: str) -> dict | None:
    if not ident or len(ident) < 8 or not all(c in "0123456789abcdef" for c in ident):
        return None
    rows = conn.execute("SELECT * FROM entries WHERE id LIKE %s ORDER BY seq LIMIT 2", (ident + "%",)).fetchall()
    return _entry_out(rows[0]) if len(rows) == 1 else None


def search(conn, query: str | None = None, subject: str | None = None, kind: str | None = None,
           provenance: str | None = None, include_superseded: bool = True, limit: int = 20, offset: int = 0) -> list[dict]:
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    where, args = [], []
    if query:
        where.append("tsv @@ websearch_to_tsquery('english', %s)")
        args.append(query)
    if subject:
        where.append("subject = %s")
        args.append(subject)
    if kind:
        where.append("kind = %s")
        args.append(kind)
    if provenance:
        where.append("provenance = %s")
        args.append(provenance)
    if not include_superseded:
        where.append("superseded_by IS NULL")
    sql = "SELECT * FROM entries"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if query:
        sql += " ORDER BY ts_rank(tsv, websearch_to_tsquery('english', %s)) DESC, seq DESC"
        args.append(query)
    else:
        sql += " ORDER BY seq DESC"
    sql += " LIMIT %s OFFSET %s"
    args += [limit, offset]
    return [_entry_out(r) for r in conn.execute(sql, args).fetchall()]


def thread(conn, subject: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM entries WHERE subject = %s ORDER BY seq", (subject,)).fetchall()
    return [_entry_out(r) for r in rows]


def subjects(conn, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        "SELECT subject, count(*) AS entries, max(seq) AS latest FROM entries GROUP BY subject ORDER BY latest DESC LIMIT %s",
        (max(1, min(int(limit), 1000)),),
    ).fetchall()
    return [dict(r) for r in rows]


def known_ids(conn, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    return {r["id"] for r in conn.execute("SELECT id FROM entries WHERE id = ANY(%s)", (ids,)).fetchall()}


# ---------------------------------------------------------------- proposals

PENDING_PER_KEY = 20
PENDING_TOTAL = 2000


def submit_proposal(conn, content: dict, sig: str, source: str | None = None) -> dict:
    """Validate and queue a signed proposal. Raises core.LedgerError on any problem."""
    core.check_content(content)
    actor = content["actor"]
    if actor["type"] != "agent":
        raise core.LedgerError("proposals come from agents; human entries are written by a steward")
    if "key" not in actor:
        raise core.LedgerError("actor.key is required: proposals are signed")
    if content["provenance"] != "inferred":
        raise core.LedgerError("agent proposals carry provenance 'inferred'; only a human can state or confirm")
    core.verify_signature({**content, "sig": sig})
    refs = content["basis"] + ([content["supersedes"]] if "supersedes" in content else [])
    missing = set(refs) - known_ids(conn, refs)
    if missing:
        raise core.LedgerError(f"basis or supersedes names entries not in the ledger: {sorted(m[:12] for m in missing)}")
    with conn.transaction():
        n_key = conn.execute("SELECT count(*) AS n FROM proposals WHERE status='pending' AND actor_key=%s",
                             (actor["key"],)).fetchone()["n"]
        if n_key >= PENDING_PER_KEY:
            raise core.LedgerError(f"this key already has {PENDING_PER_KEY} proposals waiting; wait for decisions")
        n_all = conn.execute("SELECT count(*) AS n FROM proposals WHERE status='pending'").fetchone()["n"]
        if n_all >= PENDING_TOTAL:
            raise core.LedgerError("the proposal queue is full; try again later")
        source_hash = hashlib.sha256(source.encode()).hexdigest()[:16] if source else None
        try:
            row = conn.execute(
                "INSERT INTO proposals (content, sig, actor_key, source_hash) VALUES (%s,%s,%s,%s) RETURNING pid, received",
                (Jsonb(content), sig, actor["key"], source_hash),
            ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise core.LedgerError("this exact proposal was already submitted") from None
    return {"pid": row["pid"], "status": "pending", "received": row["received"].isoformat()}


def proposal(conn, pid: int) -> dict | None:
    row = conn.execute("SELECT * FROM proposals WHERE pid=%s", (pid,)).fetchone()
    if not row:
        return None
    out = {k: row[k] for k in ("pid", "status", "note", "entry_id")}
    out["received"] = row["received"].isoformat()
    out["decided_at"] = row["decided_at"].isoformat() if row["decided_at"] else None
    out["content"] = row["content"]
    out["sig"] = row["sig"]
    return out


def pending(conn, limit: int = 50) -> list[dict]:
    rows = conn.execute("SELECT pid FROM proposals WHERE status='pending' ORDER BY pid LIMIT %s", (limit,)).fetchall()
    return [proposal(conn, r["pid"]) for r in rows]


def decide(conn, pid: int, status: str, note: str | None = None, entry_id: str | None = None) -> None:
    with conn.transaction():
        n = conn.execute(
            "UPDATE proposals SET status=%s, note=%s, entry_id=%s, decided_at=now() WHERE pid=%s AND status='pending'",
            (status, note, entry_id, pid),
        ).rowcount
    if n != 1:
        raise core.LedgerError(f"proposal {pid} is not pending")


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
