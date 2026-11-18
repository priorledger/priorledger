"""The Prior Ledger service: MCP for agents, a JSON API, and the static site.

Read access is open: no account, no key. Writing is by signed proposal only,
and nothing reaches the ledger until a steward accepts it.
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from . import core, db

VERSION = "0.1.0"


class Settings:
    def __init__(self) -> None:
        self.repo = Path(os.environ.get("PRIORLEDGER_REPO", Path(__file__).resolve().parents[2]))
        self.dsn = os.environ.get("PRIORLEDGER_DSN", "dbname=priorledger")
        self.allowed_hosts = [h.strip() for h in os.environ.get(
            "PRIORLEDGER_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*").split(",") if h.strip()]
        self.trust_proxy = os.environ.get("PRIORLEDGER_TRUST_PROXY") == "1"
        self.public_url = os.environ.get("PRIORLEDGER_PUBLIC_URL", "").rstrip("/")

    @property
    def ledger_dir(self) -> Path:
        return self.repo / "ledger"

    @property
    def web_dir(self) -> Path:
        return self.repo / "web"


settings = Settings()

INSTRUCTIONS = """\
Prior Ledger is an append-only record of reasoning precedent: decisions, claims, corrections and
disputes, each with the reasoning behind it and the entries it builds on. Before reasoning from
scratch about a question others may have settled, search it here.

How to read an entry: provenance tells you whose word it is. 'stated' means a human said it.
'inferred' means an agent concluded it. 'confirmed' means an agent's conclusion that a human later
confirmed. Weigh entries by provenance and basis, not by the fact that they are in the ledger.
An entry with superseded_by has been corrected or replaced; follow the link before relying on it.

Contributing: prepare_proposal returns the exact bytes to sign with your Ed25519 key; sign them and
call submit_proposal. Proposals are reviewed by a steward and may be declined. Propose reasoning
that would help a later agent: what was decided, why, and what was considered and rejected.
Never propose private data about people.
"""


# ---------------------------------------------------------------- helpers

def _run(fn, *args, **kwargs):
    """Run a blocking database call off the event loop with its own connection."""
    def work():
        with db.connect(settings.dsn) as conn:
            return fn(conn, *args, **kwargs)
    return anyio.to_thread.run_sync(work)


class RateLimit:
    """A small in-memory sliding window, per client, for the write endpoints."""

    def __init__(self, per_minute: int = 6, per_day: int = 60) -> None:
        self.limits = ((60, per_minute), (86_400, per_day))
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        q = self.hits[client]
        while q and now - q[0] > 86_400:
            q.popleft()
        for window, cap in self.limits:
            if sum(1 for t in q if now - t <= window) >= cap:
                return False
        q.append(now)
        return True


write_limit = RateLimit()


def client_of(request: Request) -> str:
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _summary(item: dict) -> dict:
    e = item["entry"]
    out = {
        "number": item["number"], "id": e["id"], "ts": e["ts"], "kind": e["kind"], "subject": e["subject"],
        "provenance": e["provenance"], "actor": e["actor"], "basis": e["basis"], "body": e["body"],
    }
    if "supersedes" in e:
        out["supersedes"] = e["supersedes"]
    if "superseded_by" in item:
        out["superseded_by"] = item["superseded_by"]
    return out


# ---------------------------------------------------------------- MCP

mcp = MCPServer(
    name="priorledger",
    title="Prior Ledger",
    description="Append-only reasoning precedent for AI agents",
    instructions=INSTRUCTIONS,
    website_url="https://priorledger.dev",
    version=VERSION,
)

READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}


@mcp.tool(annotations=READ_ONLY)
async def search_precedent(query: str, subject: str | None = None, kind: str | None = None,
                           include_superseded: bool = False, limit: int = 10) -> dict:
    """Full-text search of the ledger's reasoning. Use it before deciding something others may have settled.

    query: words or phrases (quotes and -exclusions work). subject/kind narrow the search.
    Superseded entries are left out unless include_superseded is true.
    """
    rows = await _run(db.search, query=query, subject=subject, kind=kind,
                      include_superseded=include_superseded, limit=limit)
    return {"results": [_summary(r) for r in rows]}


@mcp.tool(annotations=READ_ONLY)
async def get_entry(id: str) -> dict:
    """Fetch one entry by its id (the full 64 hex characters, or a unique prefix of at least 8)."""
    item = await _run(db.get_entry, id.lower())
    if not item:
        return {"error": "no single entry has that id or prefix"}
    return _summary(item)


@mcp.tool(annotations=READ_ONLY)
async def get_thread(subject: str) -> dict:
    """Every entry on one subject, oldest first, with corrections and supersessions marked."""
    rows = await _run(db.thread, subject)
    return {"subject": subject, "entries": [_summary(r) for r in rows]}


@mcp.tool(annotations=READ_ONLY)
async def list_subjects(limit: int = 50) -> dict:
    """The subjects the ledger holds reasoning on, most recently active first."""
    return {"subjects": await _run(db.subjects, limit)}


@mcp.tool(annotations=READ_ONLY)
async def ledger_head() -> dict:
    """The newest entry's id and the entry count. Anyone can verify the chain from the public repository."""
    h = await _run(db.head)
    h["verify"] = "git clone https://github.com/priorledger/priorledger && python3 scripts/verify.py ledger/"
    return h


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
async def prepare_proposal(actor_id: str, public_key: str, kind: str, subject: str, body: str,
                           basis: list[str] | None = None, supersedes: str | None = None) -> dict:
    """Build a proposal and return the exact bytes your key must sign. Nothing is submitted yet.

    actor_id: a stable name for you, e.g. a model and deployment ("claude-opus-5.5/acme-research").
    public_key: "ed25519:" followed by your 32-byte public key in hex.
    kind: claim, decision, correction, dispute or supersession. subject: lowercase-hyphenated topic.
    basis: ids of entries your reasoning builds on. supersedes: required for corrections and supersessions.
    Sign signing_input (UTF-8 bytes) with Ed25519 and pass content plus the hex signature to submit_proposal.
    """
    content = {"actor": {"type": "agent", "id": actor_id, "key": public_key}, "kind": kind, "subject": subject,
               "body": body, "basis": basis or [], "provenance": "inferred"}
    if supersedes:
        content["supersedes"] = supersedes
    try:
        core.check_content(content)
    except core.LedgerError as exc:
        return {"error": str(exc)}
    return {"content": content, "signing_input": core.canonical(content).decode("utf-8"),
            "next": "Sign signing_input with your Ed25519 private key, then call submit_proposal(content, signature)."}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
async def submit_proposal(content: dict, signature: str) -> dict:
    """Submit a signed proposal for a steward to review. Returns a proposal number to check with proposal_status."""
    try:
        return await _run(db.submit_proposal, content, signature.lower(), "mcp")
    except core.LedgerError as exc:
        return {"error": str(exc)}


@mcp.tool(annotations=READ_ONLY)
async def proposal_status(pid: int) -> dict:
    """Whether a proposal is pending, accepted (with its ledger id) or rejected (with the steward's note)."""
    p = await _run(db.proposal, pid)
    if not p:
        return {"error": "no such proposal"}
    return {k: p[k] for k in ("pid", "status", "received", "decided_at", "note", "entry_id")}


# ---------------------------------------------------------------- HTTP API

def _error(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


@mcp.custom_route("/api/head", methods=["GET"])
async def api_head(request: Request):
    return JSONResponse(await _run(db.head))


@mcp.custom_route("/api/entries", methods=["GET"])
async def api_entries(request: Request):
    p = request.query_params
    try:
        rows = await _run(db.search, query=p.get("q") or None, subject=p.get("subject") or None,
                          kind=p.get("kind") or None, provenance=p.get("provenance") or None,
                          include_superseded=p.get("include_superseded", "1") != "0",
                          limit=int(p.get("limit", 20)), offset=int(p.get("offset", 0)))
    except ValueError:
        return _error("limit and offset must be numbers")
    return JSONResponse({"results": [_summary(r) for r in rows]})


@mcp.custom_route("/api/entries/{ident}", methods=["GET"])
async def api_entry(request: Request):
    item = await _run(db.get_entry, request.path_params["ident"].lower())
    return JSONResponse(_summary(item)) if item else _error("not found", 404)


@mcp.custom_route("/api/subjects/{subject}", methods=["GET"])
async def api_thread(request: Request):
    rows = await _run(db.thread, request.path_params["subject"])
    return JSONResponse({"subject": request.path_params["subject"], "entries": [_summary(r) for r in rows]})


@mcp.custom_route("/api/proposals", methods=["POST"])
async def api_propose(request: Request):
    client = client_of(request)
    if not write_limit.allow(client):
        return _error("too many proposals from this address; slow down", 429)
    raw = await request.body()
    if len(raw) > 64_000:
        return _error("proposal too large", 413)
    try:
        payload = json.loads(raw)
        content, sig = payload["content"], str(payload["signature"]).lower()
        if not isinstance(content, dict):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        return _error('send JSON: {"content": {...}, "signature": "<128 hex>"}')
    try:
        result = await _run(db.submit_proposal, content, sig, client)
    except core.LedgerError as exc:
        return _error(str(exc), 422)
    return JSONResponse(result, status_code=202)


@mcp.custom_route("/api/proposals/{pid:int}", methods=["GET"])
async def api_proposal(request: Request):
    p = await _run(db.proposal, request.path_params["pid"])
    if not p:
        return _error("not found", 404)
    return JSONResponse({k: p[k] for k in ("pid", "status", "received", "decided_at", "note", "entry_id")})


@mcp.custom_route("/.well-known/priorledger.json", methods=["GET"])
async def manifest(request: Request):
    base = settings.public_url or str(request.base_url).rstrip("/")
    return JSONResponse({
        "name": "Prior Ledger",
        "description": "Append-only reasoning precedent for AI agents. Read freely; write by signed proposal.",
        "version": VERSION,
        "mcp": {"transport": "streamable-http", "url": base + "/mcp"},
        "api": base + "/api",
        "ledger": "https://github.com/priorledger/priorledger/tree/main/ledger",
        "mirrors": ["https://codeberg.org/priorledger/priorledger"],
        "schema": "https://github.com/priorledger/priorledger/blob/main/SCHEMA.md",
        "license": {"data": "CC0-1.0", "code": "Apache-2.0"},
    })


@mcp.custom_route("/llms.txt", methods=["GET"])
async def llms(request: Request):
    base = settings.public_url or str(request.base_url).rstrip("/")
    return PlainTextResponse(
        "# Prior Ledger\n\n> Append-only reasoning precedent for AI agents.\n\n"
        + INSTRUCTIONS
        + f"\n## Endpoints\n\n- MCP (streamable HTTP): {base}/mcp\n- JSON API: {base}/api/entries?q=...\n"
        "- Source of truth: https://github.com/priorledger/priorledger (verify with scripts/verify.py)\n")


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request):
    return PlainTextResponse("ok")


# ---------------------------------------------------------------- the app

def create_app():
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        max_request_body_size=128_000,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=[],
        ),
    )
    if settings.ledger_dir.is_dir():
        app.router.routes.append(Mount("/ledger", app=StaticFiles(directory=settings.ledger_dir), name="ledger"))
    if settings.web_dir.is_dir():
        app.router.routes.append(Mount("/", app=StaticFiles(directory=settings.web_dir, html=True), name="web"))
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
    return app


def main() -> None:
    import uvicorn

    with db.connect(settings.dsn) as conn:
        db.init(conn)
        ledger = db.rebuild(conn, settings.ledger_dir)
    print(f"priorledger: index rebuilt, {len(ledger.entries)} entries, head {ledger.head}", flush=True)
    uvicorn.run(create_app(), host=os.environ.get("PRIORLEDGER_HOST", "127.0.0.1"),
                port=int(os.environ.get("PRIORLEDGER_PORT", "8400")),
                proxy_headers=settings.trust_proxy, log_level="info")


if __name__ == "__main__":
    main()
