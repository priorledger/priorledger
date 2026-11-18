"""The steward's side of the service: sign-in, the review queue, reviewer tokens.

Two kinds of caller reach these routes:
  - a steward signed in with Google (a session cookie, plus a CSRF header on writes);
  - a reviewer agent holding a token the steward issued (Authorization: Bearer plt_...).
Tokens carry scopes; only a signed-in steward can issue or revoke them, or write
human entries.
"""
from __future__ import annotations

import os

from mcp.server.mcpserver import Context
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from . import auth, core, db, publish
from .app import _run, mcp, settings

MANAGE = "steward:manage"


def _admin_host_ok(host: str | None) -> bool:
    allowed = [h.strip().lower() for h in os.environ.get("PRIORLEDGER_ADMIN_HOSTS", "").split(",") if h.strip()]
    return not allowed or (host or "").split(":")[0].lower() in allowed


def _token_principal(token: str) -> dict:
    def look(conn):
        return db.token_by_hash(conn, auth.hash_token(token))
    return look


async def principal(request: Request, write: bool = False) -> dict:
    if not _admin_host_ok(request.headers.get("host")):
        raise auth.AuthError("not found", 404)
    token = auth.bearer_of(request.headers)
    if token:
        row = await _run(_token_principal(token))
        if not row:
            raise auth.AuthError("token is invalid, expired or revoked")
        return {"kind": "token", "who": f"reviewer token '{row['label']}'", "scopes": row["scopes"], "tid": row["tid"]}
    session = auth.session_of(request.cookies.get(auth.SESSION_COOKIE))
    if not session:
        raise auth.AuthError("sign in as steward, or send a reviewer token")
    if write and request.headers.get("x-csrf-token") != session["csrf"]:
        raise auth.AuthError("missing or stale CSRF token; reload the page", 403)
    return {"kind": "session", "who": "steward", "email": session["email"], "csrf": session["csrf"],
            "scopes": [*auth.SCOPES, MANAGE]}


def need(p: dict, scope: str) -> None:
    if scope not in p["scopes"]:
        raise auth.AuthError(f"this needs the '{scope}' scope", 403)


def _err(exc: Exception) -> JSONResponse:
    status = exc.status if isinstance(exc, auth.AuthError) else 422
    return JSONResponse({"error": str(exc)}, status_code=status)


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        raise auth.AuthError("send a JSON body", 400) from None
    if not isinstance(data, dict):
        raise auth.AuthError("send a JSON object", 400)
    return data


def _repo():
    return settings.repo


# ---------------------------------------------------------------- sign-in

def _cookie(resp, name, value, max_age, samesite):
    resp.set_cookie(name, value, max_age=max_age, httponly=True, secure=auth.auth_settings.secure_cookies,
                    samesite=samesite, path="/")


@mcp.custom_route("/admin/login", methods=["GET"])
async def login(request: Request):
    if not _admin_host_ok(request.headers.get("host")):
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        url, flow = auth.begin_login()
    except auth.AuthError as exc:
        return _err(exc)
    resp = RedirectResponse(url, status_code=302)
    _cookie(resp, auth.FLOW_COOKIE, flow, 600, "lax")
    return resp


@mcp.custom_route("/admin/auth/callback", methods=["GET"])
async def callback(request: Request):
    if not _admin_host_ok(request.headers.get("host")):
        return JSONResponse({"error": "not found"}, status_code=404)
    q = request.query_params
    try:
        if q.get("error"):
            raise auth.AuthError("sign-in was cancelled")
        email = await auth.finish_login(q.get("code", ""), q.get("state", ""), request.cookies.get(auth.FLOW_COOKIE))
    except auth.AuthError as exc:
        return RedirectResponse("/admin/?error=" + str(exc).replace(" ", "+"), status_code=302)
    await _run(lambda conn: db.audit(conn, "steward", "sign-in"))
    resp = RedirectResponse("/admin/", status_code=302)
    _cookie(resp, auth.SESSION_COOKIE, auth.new_session(email), auth.SESSION_SECONDS, "strict")
    resp.delete_cookie(auth.FLOW_COOKIE, path="/")
    return resp


@mcp.custom_route("/admin/logout", methods=["POST"])
async def logout(request: Request):
    try:
        await principal(request, write=True)
    except auth.AuthError:
        pass
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


# ---------------------------------------------------------------- the queue

@mcp.custom_route("/api/steward/me", methods=["GET"])
async def me(request: Request):
    try:
        p = await principal(request)
    except auth.AuthError as exc:
        resp = _err(exc)
        if exc.status == 401:
            resp = JSONResponse({"error": str(exc), "signin": auth.auth_settings.configured}, status_code=401)
        return resp
    out = {k: p[k] for k in ("kind", "who", "scopes")}
    if p["kind"] == "session":
        out["csrf"] = p["csrf"]
        out["publish"] = publish.mode()
    return JSONResponse(out)


@mcp.custom_route("/api/steward/proposals", methods=["GET"])
async def queue(request: Request):
    try:
        p = await principal(request)
        need(p, "review:read")
        status = request.query_params.get("status", "pending")
        if status not in ("pending", "accepted", "rejected"):
            raise auth.AuthError("status must be pending, accepted or rejected", 400)
        return JSONResponse({"proposals": await _run(db.proposals_with_reviews, status)})
    except (auth.AuthError, core.LedgerError) as exc:
        return _err(exc)


@mcp.custom_route("/api/steward/proposals/{pid:int}/review", methods=["POST"])
async def review(request: Request):
    try:
        p = await principal(request, write=True)
        need(p, "review:annotate")
        body = await _body(request)
        pid = request.path_params["pid"]
        await _run(db.add_review, pid, p["who"], str(body.get("recommendation", "")), str(body.get("reasoning", "")))
        await _run(lambda conn: db.audit(conn, p["who"], "review", pid=pid, recommendation=body.get("recommendation")))
        return JSONResponse({"ok": True})
    except (auth.AuthError, core.LedgerError) as exc:
        return _err(exc)


@mcp.custom_route("/api/steward/proposals/{pid:int}/reject", methods=["POST"])
async def reject(request: Request):
    try:
        p = await principal(request, write=True)
        need(p, "review:reject")
        body = await _body(request)
        await _run(publish.reject, request.path_params["pid"], p["who"], str(body.get("note", "")))
        return JSONResponse({"ok": True, "status": "rejected"})
    except (auth.AuthError, core.LedgerError) as exc:
        return _err(exc)


@mcp.custom_route("/api/steward/proposals/{pid:int}/accept", methods=["POST"])
async def accept(request: Request):
    try:
        p = await principal(request, write=True)
        need(p, "review:accept")
        body = await _body(request)
        note = str(body.get("note", "")).strip() or None
        entry = await _run(publish.accept, _repo(), request.path_params["pid"], p["who"], note)
        return JSONResponse({"ok": True, "status": "accepted", "entry_id": entry["id"], "published": publish.mode() == "git"})
    except (auth.AuthError, core.LedgerError, publish.PublishError) as exc:
        return _err(exc)


# ---------------------------------------------------------------- steward only

@mcp.custom_route("/api/steward/tokens", methods=["GET", "POST"])
async def tokens(request: Request):
    try:
        p = await principal(request, write=request.method == "POST")
        need(p, MANAGE)
        if request.method == "GET":
            return JSONResponse({"tokens": await _run(db.list_tokens), "scopes": list(auth.SCOPES),
                                 "max_days": auth.MAX_TOKEN_DAYS})
        body = await _body(request)
        label = str(body.get("label", "")).strip()
        if not 0 < len(label) <= 60:
            raise auth.AuthError("label must be 1 to 60 characters", 400)
        scopes = auth.check_scopes(list(body.get("scopes", [])))
        days = int(body.get("days", 30))
        if not 1 <= days <= auth.MAX_TOKEN_DAYS:
            raise auth.AuthError(f"days must be 1 to {auth.MAX_TOKEN_DAYS}", 400)
        token, digest = auth.new_token()
        row = await _run(db.create_token, label, digest, scopes, days)
        await _run(lambda conn: db.audit(conn, "steward", "token-create", tid=row["tid"], label=label, scopes=scopes, days=days))
        return JSONResponse({"token": token, "tid": row["tid"], "label": label, "scopes": scopes, "expires": row["expires"]},
                            status_code=201)
    except (auth.AuthError, ValueError, TypeError) as exc:
        return _err(exc if isinstance(exc, auth.AuthError) else auth.AuthError("days must be a number", 400))


@mcp.custom_route("/api/steward/tokens/{tid:int}/revoke", methods=["POST"])
async def revoke(request: Request):
    try:
        p = await principal(request, write=True)
        need(p, MANAGE)
        tid = request.path_params["tid"]
        if not await _run(db.revoke_token, tid):
            raise auth.AuthError("no such active token", 404)
        await _run(lambda conn: db.audit(conn, "steward", "token-revoke", tid=tid))
        return JSONResponse({"ok": True})
    except auth.AuthError as exc:
        return _err(exc)


@mcp.custom_route("/api/steward/entries", methods=["POST"])
async def write_entry(request: Request):
    try:
        p = await principal(request, write=True)
        need(p, MANAGE)
        body = await _body(request)
        content = {
            "actor": {"type": "human", "id": os.environ.get("PRIORLEDGER_STEWARD_HANDLE", "first-prior")},
            "kind": body.get("kind"), "subject": body.get("subject"), "body": str(body.get("body", "")).strip(),
            "basis": body.get("basis") or [], "provenance": body.get("provenance", "stated"),
        }
        if body.get("supersedes"):
            content["supersedes"] = body["supersedes"]
        entry = await _run(publish.write, _repo(), content, "steward")
        return JSONResponse({"ok": True, "entry_id": entry["id"], "published": publish.mode() == "git"}, status_code=201)
    except (auth.AuthError, core.LedgerError, publish.PublishError) as exc:
        return _err(exc)


@mcp.custom_route("/api/steward/audit", methods=["GET"])
async def audit_view(request: Request):
    try:
        p = await principal(request)
        need(p, MANAGE)
        return JSONResponse({"audit": await _run(db.audit_log)})
    except auth.AuthError as exc:
        return _err(exc)


# ---------------------------------------------------------------- MCP tools for reviewer agents

async def _tool_principal(ctx: Context, scope: str) -> dict:
    token = auth.bearer_of(ctx.headers)
    if not token:
        raise auth.AuthError("send your reviewer token as 'Authorization: Bearer plt_…'")
    row = await _run(_token_principal(token))
    if not row:
        raise auth.AuthError("token is invalid, expired or revoked")
    p = {"who": f"reviewer token '{row['label']}'", "scopes": row["scopes"]}
    need(p, scope)
    return p


STEWARD_TOOL = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
async def review_queue(ctx: Context, status: str = "pending") -> dict:
    """Reviewer tokens only. Proposals waiting for a decision, with any reviews already recorded.
    Read REVIEWING.md in the repository before reviewing."""
    try:
        await _tool_principal(ctx, "review:read")
        return {"proposals": await _run(db.proposals_with_reviews, status)}
    except (auth.AuthError, core.LedgerError) as exc:
        return {"error": str(exc)}


@mcp.tool(annotations=STEWARD_TOOL)
async def review_proposal(ctx: Context, pid: int, recommendation: str, reasoning: str) -> dict:
    """Reviewer tokens only. Record a recommendation (accept, reject or needs-human) with your reasoning.
    Use needs-human whenever you are unsure; a steward will decide."""
    try:
        p = await _tool_principal(ctx, "review:annotate")
        await _run(db.add_review, pid, p["who"], recommendation, reasoning)
        await _run(lambda conn: db.audit(conn, p["who"], "review", pid=pid, recommendation=recommendation))
        return {"ok": True}
    except (auth.AuthError, core.LedgerError) as exc:
        return {"error": str(exc)}


@mcp.tool(annotations={**STEWARD_TOOL, "destructiveHint": True})
async def reject_proposal(ctx: Context, pid: int, note: str) -> dict:
    """Reviewer tokens with review:reject only. Decline a proposal. The note is shown to the proposer."""
    try:
        p = await _tool_principal(ctx, "review:reject")
        await _run(publish.reject, pid, p["who"], note)
        return {"ok": True, "status": "rejected"}
    except (auth.AuthError, core.LedgerError) as exc:
        return {"error": str(exc)}


@mcp.tool(annotations=STEWARD_TOOL)
async def accept_proposal(ctx: Context, pid: int, note: str | None = None) -> dict:
    """Reviewer tokens with review:accept only. Accept a proposal into the ledger. This is permanent:
    the entry can be superseded later but never removed. The commit names your token."""
    try:
        p = await _tool_principal(ctx, "review:accept")
        entry = await _run(publish.accept, _repo(), pid, p["who"], note)
        return {"ok": True, "entry_id": entry["id"], "published": publish.mode() == "git"}
    except (auth.AuthError, core.LedgerError, publish.PublishError) as exc:
        return {"error": str(exc)}
