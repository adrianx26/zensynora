"""Tests for the JWT authenticator and OAuth callback handler."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from myclaw.auth import (
    AuthenticatedPrincipal,
    JWTAuthenticator,
    JWTVerificationError,
    OAuth2CallbackHandler,
    OAuth2Config,
    OAuthFlowError,
)

# Skip JWT-dependent tests cleanly when PyJWT isn't installed.
jwt = pytest.importorskip("jwt", reason="PyJWT not installed")


SHARED_SECRET = "test-secret"


def _make_token(claims: dict, secret: str = SHARED_SECRET, alg: str = "HS256") -> str:
    return jwt.encode(claims, secret, algorithm=alg)


# ── JWT happy paths ───────────────────────────────────────────────────────


def test_verify_valid_hs256_token():
    auth = JWTAuthenticator(secret=SHARED_SECRET, algorithms=["HS256"])
    token = _make_token({
        "sub": "user-1",
        "exp": int(time.time()) + 60,
        "scope": "kb.read kb.write",
    })
    principal = auth.verify(token)
    assert principal.user_id == "user-1"
    assert principal.scopes == {"kb.read", "kb.write"}
    assert principal.has_scope("kb.read") is True
    assert principal.has_scope("admin") is False


def test_verify_with_list_scope_claim():
    auth = JWTAuthenticator(
        secret=SHARED_SECRET, algorithms=["HS256"], scope_claim="permissions"
    )
    token = _make_token({
        "sub": "user-2",
        "exp": int(time.time()) + 60,
        "permissions": ["a", "b"],
    })
    p = auth.verify(token)
    assert p.scopes == {"a", "b"}


# ── JWT failure modes ────────────────────────────────────────────────────


def test_expired_token_rejected():
    auth = JWTAuthenticator(secret=SHARED_SECRET, algorithms=["HS256"])
    token = _make_token({"sub": "u", "exp": int(time.time()) - 60})
    with pytest.raises(JWTVerificationError, match="expired"):
        auth.verify(token)


def test_token_without_sub_rejected():
    auth = JWTAuthenticator(secret=SHARED_SECRET, algorithms=["HS256"])
    token = _make_token({"exp": int(time.time()) + 60})
    with pytest.raises(JWTVerificationError, match="sub"):
        auth.verify(token)


def test_wrong_signature_rejected():
    auth = JWTAuthenticator(secret=SHARED_SECRET, algorithms=["HS256"])
    token = _make_token({"sub": "u", "exp": int(time.time()) + 60}, secret="other")
    with pytest.raises(JWTVerificationError):
        auth.verify(token)


def test_audience_mismatch_rejected():
    auth = JWTAuthenticator(
        secret=SHARED_SECRET,
        algorithms=["HS256"],
        audience="api://expected",
    )
    token = _make_token({
        "sub": "u",
        "exp": int(time.time()) + 60,
        "aud": "api://other",
    })
    with pytest.raises(JWTVerificationError):
        auth.verify(token)


def test_empty_token_rejected():
    auth = JWTAuthenticator(secret=SHARED_SECRET, algorithms=["HS256"])
    with pytest.raises(JWTVerificationError):
        auth.verify("")


# ── Header extraction ────────────────────────────────────────────────────


def test_extract_bearer_header():
    auth = JWTAuthenticator(secret=SHARED_SECRET)
    assert auth.extract_bearer("Bearer abc.def.ghi") == "abc.def.ghi"
    assert auth.extract_bearer("bearer abc") == "abc"  # case-insensitive


@pytest.mark.parametrize("bad", ["", None, "Token abc", "abc"])
def test_extract_bearer_rejects_malformed(bad):
    auth = JWTAuthenticator(secret=SHARED_SECRET)
    with pytest.raises(JWTVerificationError):
        auth.extract_bearer(bad)


# ── Constructor invariants ────────────────────────────────────────────────


def test_authenticator_requires_exactly_one_credential():
    with pytest.raises(ValueError):
        JWTAuthenticator()  # neither secret nor jwks_url
    with pytest.raises(ValueError):
        JWTAuthenticator(secret="s", jwks_url="https://example/jwks")


# ── OAuth flow ────────────────────────────────────────────────────────────


def _make_handler():
    cfg = OAuth2Config(
        client_id="client-1",
        client_secret="shh",
        authorize_url="https://idp.example/authorize",
        token_url="https://idp.example/token",
        redirect_uri="https://app.example/cb",
    )
    return OAuth2CallbackHandler(cfg), cfg


def test_start_flow_returns_authorize_url_with_state():
    handler, cfg = _make_handler()
    url = handler.start_flow()
    assert url.startswith(cfg.authorize_url)
    assert "state=" in url
    assert "code_challenge=" in url  # PKCE on by default
    assert "code_challenge_method=S256" in url


@pytest.mark.asyncio
async def test_handle_callback_rejects_unknown_state():
    handler, _ = _make_handler()
    with pytest.raises(OAuthFlowError, match="Unknown"):
        await handler.handle_callback(code="abc", state="not-issued")


@pytest.mark.asyncio
async def test_handle_callback_rejects_replayed_state():
    """``state`` must be one-shot — once consumed it can't be reused."""
    handler, _ = _make_handler()
    url = handler.start_flow()
    state = url.split("state=")[1].split("&")[0]

    # First use mocked to succeed via patched httpx.
    class FakeResp:
        status_code = 200
        text = ""
        def json(self):
            return {"access_token": "tok", "token_type": "Bearer"}

    class FakeClient:
        def __init__(self, *_a, **_kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return None
        async def post(self, *_a, **_kw): return FakeResp()

    with patch("myclaw.auth.oauth.httpx") as mock_httpx:
        mock_httpx.AsyncClient = FakeClient
        first = await handler.handle_callback(code="abc", state=state)
    assert first["access_token"] == "tok"

    # Second use of the same state must fail.
    with pytest.raises(OAuthFlowError):
        await handler.handle_callback(code="abc", state=state)


def test_pkce_pair_format():
    """RFC 7636 requires base64url, no padding, S256 challenge."""
    v, c = OAuth2CallbackHandler._pkce_pair()
    assert "=" not in v
    assert "=" not in c
    assert len(v) >= 43 and len(v) <= 128


def test_provider_factory_classmethods():
    g = OAuth2Config.github("cid", "csecret", "https://app/cb")
    assert g.authorize_url.startswith("https://github.com/")
    g2 = OAuth2Config.google("cid", "csecret", "https://app/cb")
    assert g2.authorize_url.startswith("https://accounts.google.com/")
