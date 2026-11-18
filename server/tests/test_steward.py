"""Steward sign-in, reviewer tokens, the review flow, and publishing to git."""
import base64
import json
import subprocess
import time
from urllib.parse import parse_qs, urlparse

import anyio
import httpx
import pytest

from priorledger import app as appmod
from priorledger import auth, core, db, publish

STEWARD = "steward@example.org"


@pytest.fixture
def svc(repo, dsn, monkeypatch):
    monkeypatch.setattr(appmod.settings, "repo", repo)
    monkeypatch.setattr(appmod.settings, "dsn", dsn)
    monkeypatch.setattr(appmod, "write_limit", appmod.RateLimit(per_minute=100, per_day=1000))
    s = auth.auth_settings
    monkeypatch.setattr(s, "client_id", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(s, "client_secret", "shh")
    monkeypatch.setattr(s, "stewards", {STEWARD})
    monkeypatch.setattr(s, "secret", b"x" * 40)
    monkeypatch.setattr(s, "admin_url", "http://localhost:8400")
    monkeypatch.setenv("PRIORLEDGER_PUBLISH", "none")
    with db.connect(dsn) as conn:
        db.init(conn)
        db.rebuild(conn, repo / "ledger")
    return repo


class Client:
    """One browser or agent: keeps cookies between calls."""

    def __init__(self, headers=None):
        self.cookies = httpx.Cookies()
        self.headers = headers or {}

    def __call__(self, method, path, **kw):
        async def go():
            transport = httpx.ASGITransport(app=appmod.create_app())
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8400",
                                         cookies=self.cookies, headers=self.headers) as c:
                r = await c.request(method, path, **kw)
                self.cookies.update(r.cookies)
                return r
        return anyio.run(go)


def fake_google(monkeypatch, email=STEWARD, verified=True, nonce=None, aud=None):
    """Replace Google's token endpoint with one that returns an ID token for `email`."""
    seen = {}

    def handler(request):
        form = parse_qs(request.content.decode())
        seen["verifier"] = form["code_verifier"][0]
        claims = {"iss": "https://accounts.google.com", "aud": aud or auth.auth_settings.client_id,
                  "exp": time.time() + 300, "email": email, "email_verified": verified, "nonce": nonce or seen["nonce"]}
        body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        return httpx.Response(200, json={"id_token": f"h.{body}.s"})

    monkeypatch.setattr(auth, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return seen


def sign_in(browser, monkeypatch, **kw):
    seen = fake_google(monkeypatch, **kw)
    r = browser("GET", "/admin/login", follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    seen["nonce"] = q["nonce"][0]
    assert q["code_challenge_method"] == ["S256"] and q["scope"] == ["openid email"]
    r = browser("GET", "/admin/auth/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False)
    return r


def propose(head, body="We cache the index because rebuilding is cheap."):
    priv, pub = core.generate_key()
    c = {"actor": {"type": "agent", "id": "test/1", "key": pub}, "kind": "decision", "subject": "caching",
         "body": body, "basis": [head], "provenance": "inferred"}
    return {"content": c, "signature": core.sign_content(c, priv)}


# ---------------------------------------------------------------- sign-in

def test_steward_signs_in_and_gets_csrf(svc, monkeypatch):
    browser = Client()
    r = sign_in(browser, monkeypatch)
    assert r.status_code == 302 and r.headers["location"] == "/admin/"
    me = browser("GET", "/api/steward/me").json()
    assert me["kind"] == "session" and me["csrf"] and "steward:manage" in me["scopes"]


@pytest.mark.parametrize("kw, why", [
    (dict(email="someone@else.org"), "not a steward"),
    (dict(verified=False), "not a steward"),
    (dict(nonce="wrong"), "stale"),
    (dict(aud="other-client"), "not issued for this server"),
])
def test_sign_in_refusals(svc, monkeypatch, kw, why):
    browser = Client()
    r = sign_in(browser, monkeypatch, **kw)
    assert why.replace(" ", "+") in r.headers["location"]
    assert browser("GET", "/api/steward/me").status_code == 401


def test_forged_state_is_refused(svc, monkeypatch):
    fake_google(monkeypatch)
    browser = Client()
    browser("GET", "/admin/login", follow_redirects=False)
    r = browser("GET", "/admin/auth/callback", params={"code": "c", "state": "forged"}, follow_redirects=False)
    assert "expired" in r.headers["location"]


def test_writes_need_csrf(svc, monkeypatch):
    browser = Client()
    sign_in(browser, monkeypatch)
    r = browser("POST", "/api/steward/tokens", json={"label": "x", "scopes": ["review:annotate"]})
    assert r.status_code == 403 and "CSRF" in r.json()["error"]


def test_admin_hosts_restriction(svc, monkeypatch):
    monkeypatch.setenv("PRIORLEDGER_ADMIN_HOSTS", "steward.internal")
    assert Client()("GET", "/api/steward/me").status_code == 404


# ---------------------------------------------------------------- tokens and the review flow

def issue(browser, scopes, label="nightly"):
    csrf = browser("GET", "/api/steward/me").json()["csrf"]
    r = browser("POST", "/api/steward/tokens", json={"label": label, "scopes": scopes, "days": 7},
                headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201, r.text
    return r.json(), csrf


def test_reviewer_token_flow(svc, monkeypatch):
    browser = Client()
    sign_in(browser, monkeypatch)
    tok, csrf = issue(browser, ["review:annotate", "review:reject"])
    assert tok["token"].startswith("plt_") and tok["scopes"] == ["review:read", "review:annotate", "review:reject"]

    head = Client()("GET", "/api/head").json()["head"]
    pid = Client()("POST", "/api/proposals", json=propose(head)).json()["pid"]
    agent = Client({"Authorization": "Bearer " + tok["token"]})

    queue = agent("GET", "/api/steward/proposals").json()["proposals"]
    assert [p["pid"] for p in queue] == [pid]
    r = agent("POST", f"/api/steward/proposals/{pid}/review", json={"recommendation": "accept", "reasoning": "Clear and useful."})
    assert r.json() == {"ok": True}
    assert agent("POST", f"/api/steward/proposals/{pid}/accept", json={}).status_code == 403   # no accept scope
    assert agent("GET", "/api/steward/tokens").status_code == 403                             # tokens can't manage tokens

    pid2 = Client()("POST", "/api/proposals", json=propose(head, "Buy cheap watches here.")).json()["pid"]
    assert agent("POST", f"/api/steward/proposals/{pid2}/reject", json={"note": ""}).status_code == 422
    assert agent("POST", f"/api/steward/proposals/{pid2}/reject", json={"note": "Advertising."}).json()["status"] == "rejected"
    st = Client()("GET", f"/api/proposals/{pid2}").json()
    assert st["status"] == "rejected" and st["note"] == "Advertising."

    # the steward sees the agent's review and accepts
    pending = browser("GET", "/api/steward/proposals").json()["proposals"]
    assert pending[0]["reviews"][0]["reviewer"] == "reviewer token 'nightly'"
    r = browser("POST", f"/api/steward/proposals/{pid}/accept", json={"note": "ok"}, headers={"X-CSRF-Token": csrf})
    assert r.json()["status"] == "accepted"
    assert Client()("GET", "/api/head").json()["count"] == 2

    # revoke: the agent is locked out at once
    tid = tok["tid"]
    assert browser("POST", f"/api/steward/tokens/{tid}/revoke", json={}, headers={"X-CSRF-Token": csrf}).json()["ok"]
    assert agent("GET", "/api/steward/proposals").status_code == 401
    actions = [a["action"] for a in browser("GET", "/api/steward/audit").json()["audit"]]
    assert {"sign-in", "token-create", "review", "reject", "accept", "token-revoke"} <= set(actions)


def test_steward_writes_a_human_entry(svc, monkeypatch):
    browser = Client()
    sign_in(browser, monkeypatch)
    csrf = browser("GET", "/api/steward/me").json()["csrf"]
    r = browser("POST", "/api/steward/entries", headers={"X-CSRF-Token": csrf},
                json={"kind": "claim", "subject": "palette", "body": "The register uses paper, ink and one amber."})
    assert r.status_code == 201
    entry = core.read_ledger(svc / "ledger").entries[-1]
    assert entry["actor"] == {"type": "human", "id": "first-prior"} and entry["provenance"] == "stated" and "sig" not in entry


def test_mcp_steward_tools(svc, monkeypatch):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    browser = Client()
    sign_in(browser, monkeypatch)
    tok, _ = issue(browser, ["review:annotate", "review:accept"], label="mcp-reviewer")
    head = Client()("GET", "/api/head").json()["head"]
    pid = Client()("POST", "/api/proposals", json=propose(head)).json()["pid"]

    def payload(res):
        return getattr(res, "structured_content", None) or json.loads(res.content[0].text)

    async def go(headers):
        transport = httpx.ASGITransport(app=appmod.create_app())
        app = transport.app
        async with app.router.lifespan_context(app):
            client = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8400", headers=headers, timeout=30)
            async with streamable_http_client("http://127.0.0.1:8400/mcp", http_client=client) as streams:
                async with ClientSession(streams[0], streams[1]) as s:
                    await s.initialize()
                    out = {"queue": payload(await s.call_tool("review_queue", {}))}
                    if "error" not in out["queue"]:
                        out["review"] = payload(await s.call_tool("review_proposal", {"pid": pid, "recommendation": "accept", "reasoning": "Sound."}))
                        out["accept"] = payload(await s.call_tool("accept_proposal", {"pid": pid}))
                    return out

    anon = anyio.run(go, {})
    assert "reviewer token" in anon["queue"]["error"]
    out = anyio.run(go, {"Authorization": "Bearer " + tok["token"]})
    assert out["queue"]["proposals"][0]["pid"] == pid
    assert out["review"] == {"ok": True} and len(out["accept"]["entry_id"]) == 64


# ---------------------------------------------------------------- publishing

def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def test_accept_publishes_to_git_and_names_the_reviewer(svc, dsn, tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
    git(svc, "init", "-q", "-b", "main")
    git(svc, "-c", "user.name=t", "-c", "user.email=t@t", "add", "ledger")
    git(svc, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed")
    git(svc, "remote", "add", "origin", str(origin))
    git(svc, "push", "-q", "-u", "origin", "main")
    monkeypatch.setenv("PRIORLEDGER_PUBLISH", "git")

    head = core.read_ledger(svc / "ledger").head
    with db.connect(dsn) as conn:
        pid = db.submit_proposal(conn, **dict(zip(("content", "sig"), propose(head).values())))["pid"]
        entry = publish.accept(conn, svc, pid, "reviewer token 'nightly'", "looks right")

    msg = git(origin, "log", "-1", "--format=%an <%ae>%n%B", "main")
    assert msg.startswith("first prior <priorledger@gmail.com>")
    assert f"Accept proposal {pid}: decision on caching" in msg and "Accepted by reviewer token 'nightly'" in msg
    assert entry["id"] in git(origin, "show", "main:ledger/000000.jsonl")


def test_failed_push_leaves_proposal_pending(svc, dsn, tmp_path, monkeypatch):
    git(svc, "init", "-q", "-b", "main")
    git(svc, "-c", "user.name=t", "-c", "user.email=t@t", "add", "ledger")
    git(svc, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed")
    git(svc, "remote", "add", "origin", str(tmp_path / "missing.git"))
    monkeypatch.setenv("PRIORLEDGER_PUBLISH", "git")
    before = (svc / "ledger" / "000000.jsonl").read_text()
    head = core.read_ledger(svc / "ledger").head
    with db.connect(dsn) as conn:
        pid = db.submit_proposal(conn, **dict(zip(("content", "sig"), propose(head).values())))["pid"]
        with pytest.raises(publish.PublishError):
            publish.accept(conn, svc, pid, "steward")
        assert db.proposal(conn, pid)["status"] == "pending"
    assert (svc / "ledger" / "000000.jsonl").read_text() == before
