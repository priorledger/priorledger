"""Who may act as steward, and how they prove it.

Stewards sign in with Google, and only the addresses in PRIORLEDGER_STEWARD_EMAILS
are let in. The project never stores a password. A signed-in steward can issue
reviewer tokens: random bearer strings with scopes and an expiry, of which only
a SHA-256 hash is stored.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

import httpx

SCOPES = ("review:read", "review:annotate", "review:reject", "review:accept")
TOKEN_PREFIX = "plt_"
MAX_TOKEN_DAYS = 90
SESSION_SECONDS = 12 * 3600
SESSION_COOKIE = "pl_session"
FLOW_COOKIE = "pl_oidc"


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


class AuthSettings:
    def __init__(self) -> None:
        self.client_id = os.environ.get("PRIORLEDGER_GOOGLE_CLIENT_ID", "")
        self.client_secret = os.environ.get("PRIORLEDGER_GOOGLE_CLIENT_SECRET", "")
        self.stewards = {e.strip().lower() for e in os.environ.get("PRIORLEDGER_STEWARD_EMAILS", "").split(",") if e.strip()}
        secret = os.environ.get("PRIORLEDGER_SECRET", "")
        self.secret = secret.encode() if len(secret) >= 32 else b""
        self.admin_url = os.environ.get("PRIORLEDGER_ADMIN_URL", "http://localhost:8400").rstrip("/")
        self.auth_endpoint = os.environ.get("PRIORLEDGER_OIDC_AUTH_URL", "https://accounts.google.com/o/oauth2/v2/auth")
        self.token_endpoint = os.environ.get("PRIORLEDGER_OIDC_TOKEN_URL", "https://oauth2.googleapis.com/token")
        self.issuers = {"https://accounts.google.com", "accounts.google.com"}

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.stewards and self.secret)

    @property
    def redirect_uri(self) -> str:
        return self.admin_url + "/admin/auth/callback"

    @property
    def secure_cookies(self) -> bool:
        return self.admin_url.startswith("https://")


auth_settings = AuthSettings()


def http_client() -> httpx.AsyncClient:
    """The client used to reach Google; tests replace it."""
    return httpx.AsyncClient(timeout=15)


# ---------------------------------------------------------------- signed values

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def seal(payload: dict, purpose: str) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    mac = hmac.new(auth_settings.secret, f"{purpose}.{body}".encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def unseal(value: str | None, purpose: str) -> dict | None:
    if not value or "." not in value or not auth_settings.secret:
        return None
    body, mac = value.rsplit(".", 1)
    expect = hmac.new(auth_settings.secret, f"{purpose}.{body}".encode(), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(expect, _unb64(mac)):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


# ---------------------------------------------------------------- Google sign-in

def begin_login() -> tuple[str, str]:
    """Return (redirect URL, flow cookie value) for the authorization-code flow with PKCE."""
    if not auth_settings.configured:
        raise AuthError("steward sign-in is not configured on this server", 503)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    params = {
        "client_id": auth_settings.client_id, "redirect_uri": auth_settings.redirect_uri,
        "response_type": "code", "scope": "openid email", "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account",
    }
    flow = seal({"state": state, "nonce": nonce, "verifier": verifier, "exp": time.time() + 600}, "flow")
    return auth_settings.auth_endpoint + "?" + urlencode(params), flow


async def finish_login(code: str, state: str, flow_cookie: str | None) -> str:
    """Exchange the code, check the ID token, and return the steward's email."""
    flow = unseal(flow_cookie, "flow")
    if not flow or not hmac.compare_digest(flow["state"], state or ""):
        raise AuthError("sign-in expired or was not started here; try again")
    async with http_client() as client:
        res = await client.post(auth_settings.token_endpoint, data={
            "code": code, "client_id": auth_settings.client_id, "client_secret": auth_settings.client_secret,
            "redirect_uri": auth_settings.redirect_uri, "grant_type": "authorization_code",
            "code_verifier": flow["verifier"],
        })
    if res.status_code != 200:
        raise AuthError("Google did not accept the sign-in")
    id_token = res.json().get("id_token", "")
    try:
        claims = json.loads(_unb64(id_token.split(".")[1]))
    except (IndexError, ValueError, json.JSONDecodeError):
        raise AuthError("Google returned an unreadable identity") from None
    # The token came straight from Google's token endpoint over TLS, which OpenID Connect
    # accepts in place of a signature check (Core 1.0, 3.1.3.7). The claims are still checked.
    if claims.get("iss") not in auth_settings.issuers or claims.get("aud") != auth_settings.client_id:
        raise AuthError("the identity was not issued for this server")
    if claims.get("exp", 0) < time.time() or not hmac.compare_digest(claims.get("nonce", ""), flow["nonce"]):
        raise AuthError("the identity is stale; try again")
    email = str(claims.get("email", "")).lower()
    if not claims.get("email_verified") or email not in auth_settings.stewards:
        raise AuthError("this account is not a steward of this ledger", 403)
    return email


def new_session(email: str) -> str:
    return seal({"email": email, "csrf": secrets.token_urlsafe(24), "exp": time.time() + SESSION_SECONDS}, "session")


def session_of(cookie: str | None) -> dict | None:
    s = unseal(cookie, "session")
    return s if s and s.get("email") in auth_settings.stewards else None


# ---------------------------------------------------------------- reviewer tokens

def new_token() -> tuple[str, str]:
    """Return (the token to show once, its hash to store)."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_scopes(scopes: list[str]) -> list[str]:
    bad = [s for s in scopes if s not in SCOPES]
    if bad or not scopes:
        raise AuthError(f"scopes must be some of {SCOPES}", 400)
    if "review:read" not in scopes:
        scopes = ["review:read", *scopes]
    return sorted(set(scopes), key=SCOPES.index)


def bearer_of(headers) -> str | None:
    value = (headers or {}).get("authorization", "") or ""
    if value.lower().startswith("bearer "):
        token = value[7:].strip()
        if token.startswith(TOKEN_PREFIX):
            return token
    return None
