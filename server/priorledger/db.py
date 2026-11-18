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

ALTER TABLE proposals ADD COLUMN IF NOT EXISTS decided_by text;

CREATE TABLE IF NOT EXISTS reviews (
    rid            bigserial PRIMARY KEY,
    pid            bigint NOT NULL REFERENCES proposals(pid),
    reviewer       text NOT NULL,
    recommendation text NOT NULL CHECK (recommendation IN ('accept', 'reject', 'needs-human')),
    reasoning      text NOT NULL,
    created        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS reviews_pid ON reviews (pid);

CREATE TABLE IF NOT EXISTS tokens (
    tid         bigserial PRIMARY KEY,
    label       text NOT NULL,
    token_hash  text UNIQUE NOT NULL,
    scopes      text[] NOT NULL,
    created     timestamptz NOT NULL DEFAULT now(),
    expires     timestamptz NOT NULL,
    last_used   timestamptz,
    revoked_at  timestamptz
);

-- How often each public tool is used, per day. Counts only: no address, no user-agent, no query.
-- Like the queue, this lives here alone; losing it loses history, never any part of the record.
CREATE TABLE IF NOT EXISTS usage (
    day   date NOT NULL,
    name  text NOT NULL,
    n     bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (day, name)
);

CREATE TABLE IF NOT EXISTS audit (
    aid     bigserial PRIMARY KEY,
    at      timestamptz NOT NULL DEFAULT now(),
    actor   text NOT NULL,
    action  text NOT NULL,
    detail  jsonb NOT NULL DEFAULT '{}'::jsonb
);
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


def count_use(conn, name: str) -> None:
    """Add one to today's count for a public tool or endpoint. Nothing about the caller is kept."""
    conn.execute(
        "INSERT INTO usage (day, name, n) VALUES ((now() AT TIME ZONE 'utc')::date, %s, 1) "
        "ON CONFLICT (day, name) DO UPDATE SET n = usage.n + 1", (name,))
    conn.commit()


def usage(conn, days: int = 30) -> list[dict]:
    """Daily counts, newest day first, for the last `days` days (UTC)."""
    days = max(1, min(int(days), 400))
    rows = conn.execute(
        "SELECT day, name, n FROM usage WHERE day > (now() AT TIME ZONE 'utc')::date - %s ORDER BY day DESC, name",
        (days,)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["day"].isoformat(), {})[r["name"]] = r["n"]
    return [{"day": d, "counts": c} for d, c in out.items()]


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
    out = {k: row[k] for k in ("pid", "status", "note", "entry_id", "decided_by")}
    out["received"] = row["received"].isoformat()
    out["decided_at"] = row["decided_at"].isoformat() if row["decided_at"] else None
    out["content"] = row["content"]
    out["sig"] = row["sig"]
    return out


def pending(conn, limit: int = 50) -> list[dict]:
    rows = conn.execute("SELECT pid FROM proposals WHERE status='pending' ORDER BY pid LIMIT %s", (limit,)).fetchall()
    return [proposal(conn, r["pid"]) for r in rows]


def decide(conn, pid: int, status: str, note: str | None = None, entry_id: str | None = None,
           by: str = "steward") -> None:
    with conn.transaction():
        n = conn.execute(
            "UPDATE proposals SET status=%s, note=%s, entry_id=%s, decided_by=%s, decided_at=now() "
            "WHERE pid=%s AND status='pending'",
            (status, note, entry_id, by, pid),
        ).rowcount
    if n != 1:
        raise core.LedgerError(f"proposal {pid} is not pending")


# ---------------------------------------------------------------- reviews, tokens, audit

def proposals_with_reviews(conn, status: str = "pending", limit: int = 100) -> list[dict]:
    rows = conn.execute("SELECT pid FROM proposals WHERE status=%s ORDER BY pid DESC LIMIT %s",
                        (status, max(1, min(limit, 500)))).fetchall()
    out = []
    for r in rows:
        p = proposal(conn, r["pid"])
        p["reviews"] = [
            {"reviewer": v["reviewer"], "recommendation": v["recommendation"], "reasoning": v["reasoning"],
             "created": v["created"].isoformat()}
            for v in conn.execute("SELECT * FROM reviews WHERE pid=%s ORDER BY rid", (r["pid"],)).fetchall()
        ]
        out.append(p)
    return out


def add_review(conn, pid: int, reviewer: str, recommendation: str, reasoning: str) -> None:
    if recommendation not in ("accept", "reject", "needs-human"):
        raise core.LedgerError("recommendation must be accept, reject or needs-human")
    if not reasoning or not reasoning.strip() or len(reasoning) > 5000:
        raise core.LedgerError("reasoning must be 1 to 5000 characters")
    with conn.transaction():
        p = conn.execute("SELECT status FROM proposals WHERE pid=%s", (pid,)).fetchone()
        if not p:
            raise core.LedgerError("no such proposal")
        if p["status"] != "pending":
            raise core.LedgerError(f"proposal {pid} is already {p['status']}")
        conn.execute("INSERT INTO reviews (pid, reviewer, recommendation, reasoning) VALUES (%s,%s,%s,%s)",
                     (pid, reviewer, recommendation, reasoning.strip()))


def audit(conn, actor: str, action: str, **detail) -> None:
    with conn.transaction():
        conn.execute("INSERT INTO audit (actor, action, detail) VALUES (%s,%s,%s)", (actor, action, Jsonb(detail)))


def audit_log(conn, limit: int = 200) -> list[dict]:
    rows = conn.execute("SELECT * FROM audit ORDER BY aid DESC LIMIT %s", (limit,)).fetchall()
    return [{"at": r["at"].isoformat(), "actor": r["actor"], "action": r["action"], "detail": r["detail"]} for r in rows]


def create_token(conn, label: str, token_hash: str, scopes: list[str], days: int) -> dict:
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO tokens (label, token_hash, scopes, expires) VALUES (%s,%s,%s, now() + make_interval(days => %s)) "
            "RETURNING tid, expires", (label, token_hash, scopes, days)).fetchone()
    return {"tid": row["tid"], "expires": row["expires"].isoformat()}


def token_by_hash(conn, token_hash: str) -> dict | None:
    with conn.transaction():
        row = conn.execute(
            "UPDATE tokens SET last_used=now() WHERE token_hash=%s AND revoked_at IS NULL AND expires > now() "
            "RETURNING tid, label, scopes", (token_hash,)).fetchone()
    return dict(row) if row else None


def list_tokens(conn) -> list[dict]:
    rows = conn.execute("SELECT tid, label, scopes, created, expires, last_used, revoked_at FROM tokens "
                        "ORDER BY tid DESC").fetchall()
    return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in rows]


def revoke_token(conn, tid: int) -> bool:
    with conn.transaction():
        return conn.execute("UPDATE tokens SET revoked_at=now() WHERE tid=%s AND revoked_at IS NULL",
                            (tid,)).rowcount == 1


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
