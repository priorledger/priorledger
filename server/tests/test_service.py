"""The API, the MCP server and the steward tools, against a real Postgres."""
import json

import anyio
import httpx
import pytest

from priorledger import app as appmod
from priorledger import core, db, steward


@pytest.fixture
def service(repo, dsn, monkeypatch):
    monkeypatch.setattr(appmod.settings, "repo", repo)
    monkeypatch.setattr(appmod.settings, "dsn", dsn)
    monkeypatch.setattr(appmod, "write_limit", appmod.RateLimit(per_minute=100, per_day=1000))
    with db.connect(dsn) as conn:
        db.init(conn)
        db.rebuild(conn, repo / "ledger")
    return repo


def call(method, path, **kw):
    async def go():
        transport = httpx.ASGITransport(app=appmod.create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8400") as c:
            return await c.request(method, path, **kw)
    return anyio.run(go)


def signed(head, **over):
    priv, pub = core.generate_key()
    c = {"actor": {"type": "agent", "id": "test/1", "key": pub}, "kind": "decision", "subject": "caching",
         "body": "We cache the index because rebuilding is cheap.", "basis": [head], "provenance": "inferred"}
    c.update(over)
    return c, core.sign_content(c, priv)


def test_read_api(service):
    head = call("GET", "/api/head").json()
    assert head["count"] == 1
    hits = call("GET", "/api/entries", params={"q": "forkability"}).json()["results"]
    assert hits and hits[0]["id"] == head["head"]
    assert call("GET", f"/api/entries/{head['head'][:10]}").json()["subject"] == "founding"
    assert call("GET", "/api/entries/zzzz").status_code == 404
    assert call("GET", "/.well-known/priorledger.json").json()["mcp"]["url"].endswith("/mcp")
    assert call("GET", "/ledger/000000.jsonl").status_code == 200
    assert "Prior Ledger" in call("GET", "/").text


def test_proposal_lifecycle(service, dsn):
    head = call("GET", "/api/head").json()["head"]
    c, sig = signed(head)
    r = call("POST", "/api/proposals", json={"content": c, "signature": sig})
    assert r.status_code == 202, r.text
    pid = r.json()["pid"]

    assert call("POST", "/api/proposals", json={"content": c, "signature": sig}).status_code == 422  # duplicate
    tampered = call("POST", "/api/proposals", json={"content": {**c, "body": "other"}, "signature": sig})
    assert "signature" in tampered.json()["error"]
    stated = signed(head, provenance="stated")
    assert "inferred" in call("POST", "/api/proposals", json={"content": stated[0], "signature": stated[1]}).json()["error"]
    ghost = signed("f" * 64)
    assert "not in the ledger" in call("POST", "/api/proposals", json={"content": ghost[0], "signature": ghost[1]}).json()["error"]

    assert steward.main(["accept", str(pid), "--note", "ok"]) == 0
    status = call("GET", f"/api/proposals/{pid}").json()
    assert status["status"] == "accepted" and len(status["entry_id"]) == 64
    assert call("GET", "/api/head").json()["count"] == 2
    ledger = core.read_ledger(service / "ledger")
    assert ledger.entries[-1]["sig"] == sig and ledger.entries[-1]["prev"] == head


def test_supersession_is_indexed(service):
    head = call("GET", "/api/head").json()["head"]
    c, sig = signed(head)
    pid = call("POST", "/api/proposals", json={"content": c, "signature": sig}).json()["pid"]
    steward.main(["accept", str(pid)])
    first = core.read_ledger(service / "ledger").head
    assert steward.main(["write", "--kind", "correction", "--subject", "caching", "--supersedes", first,
                         "--body", "The index is cheap to rebuild only while the ledger is small."]) == 0
    thread = call("GET", "/api/subjects/caching").json()["entries"]
    assert thread[0]["superseded_by"] == thread[1]["id"]
    current = call("GET", "/api/entries", params={"subject": "caching", "include_superseded": "0"}).json()["results"]
    assert [e["kind"] for e in current] == ["correction"]


def test_rate_limit(service, monkeypatch):
    monkeypatch.setattr(appmod, "write_limit", appmod.RateLimit(per_minute=2, per_day=10))
    codes = [call("POST", "/api/proposals", content=b"{}").status_code for _ in range(3)]
    assert codes == [400, 400, 429]


def test_mcp_tools(service):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    def payload(res):
        return getattr(res, "structured_content", None) or json.loads(res.content[0].text)

    async def go():
        transport = httpx.ASGITransport(app=appmod.create_app())
        app = transport.app
        async with app.router.lifespan_context(app):
            client = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8400", timeout=30)
            async with streamable_http_client("http://127.0.0.1:8400/mcp", http_client=client) as streams:
                async with ClientSession(streams[0], streams[1]) as s:
                    await s.initialize()
                    names = {t.name for t in (await s.list_tools()).tools}
                    assert {"search_precedent", "prepare_proposal", "submit_proposal"} <= names
                    hit = payload(await s.call_tool("search_precedent", {"query": "case law"}))["results"][0]
                    priv, pub = core.generate_key()
                    prep = payload(await s.call_tool("prepare_proposal", {
                        "actor_id": "mcp/test", "public_key": pub, "kind": "claim", "subject": "mcp-test",
                        "body": "Signed through MCP.", "basis": [hit["id"]]}))
                    sig = core.sign_content(prep["content"], priv)
                    assert core.canonical(prep["content"]).decode() == prep["signing_input"]
                    sub = payload(await s.call_tool("submit_proposal", {"content": prep["content"], "signature": sig}))
                    assert sub["status"] == "pending"
                    st = payload(await s.call_tool("proposal_status", {"pid": sub["pid"]}))
                    assert st["status"] == "pending"
    anyio.run(go)
