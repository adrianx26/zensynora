"""OAuth 2.0 authorization-code flow with PKCE.

This is a *callback handler*, not a full OAuth client. The flow:

  1. Caller (e.g., the WebUI) calls ``OAuth2CallbackHandler.start_flow()``
     to get an ``authorize_url``. Browser sends the user there.
  2. The provider redirects to your registered callback URL with
     ``?code=…&state=…``.
  3. Callback handler calls ``handle_callback(code, state)`` which
     exchanges the code for tokens and returns them.

Why minimal: we don't ship a frontend, and we don't pretend to be
``authlib``. This handles the server-side bits we actually need.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# Optional dep — only needed for the token exchange step.
try:  # pragma: no cover - import guard
    import httpx
    _HTTPX_AVAILABLE = True
except Exception:
    httpx = None  # type: ignore[assignment]
    _HTTPX_AVAILABLE = False


class OAuthFlowError(RuntimeError):
    """Raised on any oauth-flow misuse or provider error."""


@dataclass
class OAuth2Config:
    """Per-provider OAuth configuration.

    Pre-baked for the common providers via the classmethods below.
    """
    client_id: str
    client_secret: Optional[str]  # None ⇒ PKCE-only public client
    authorize_url: str
    token_url: str
    redirect_uri: str
    scopes: list = field(default_factory=lambda: ["openid", "profile", "email"])
    use_pkce: bool = True
    audience: Optional[str] = None  # Auth0-style; ignored when None

    @classmethod
    def github(cls, client_id: str, client_secret: str, redirect_uri: str) -> "OAuth2Config":
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            authorize_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            redirect_uri=redirect_uri,
            scopes=["read:user", "user:email"],
            use_pkce=False,  # GitHub has limited PKCE support; secret is fine for confidential clients
        )

    @classmethod
    def google(cls, client_id: str, client_secret: str, redirect_uri: str) -> "OAuth2Config":
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri=redirect_uri,
            scopes=["openid", "email", "profile"],
        )


@dataclass
class _PendingFlow:
    state: str
    pkce_verifier: Optional[str]
    created_at: float


class OAuth2CallbackHandler:
    """Server-side OAuth 2.0 helper with state + PKCE bookkeeping.

    Pending flows are kept in memory keyed by ``state``. For multi-process
    deployments swap this for a Redis-backed store; the contract here
    isolates that change to one method.
    """

    #: Time to wait before evicting an unfinished flow.
    PENDING_TTL_SECONDS = 600

    def __init__(self, config: OAuth2Config) -> None:
        self._config = config
        self._pending: Dict[str, _PendingFlow] = {}

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _pkce_pair() -> tuple:
        """Return ``(verifier, challenge)`` per RFC 7636 §4.2 (S256)."""
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return verifier, challenge

    def _evict_expired(self) -> None:
        now = time.time()
        stale = [
            s for s, p in self._pending.items()
            if now - p.created_at > self.PENDING_TTL_SECONDS
        ]
        for s in stale:
            self._pending.pop(s, None)

    # ── Step 1: authorize URL ──────────────────────────────────────────

    def start_flow(self, extra_params: Optional[Dict[str, str]] = None) -> str:
        """Generate the URL the user's browser should visit.

        Stores the ``state`` and (if PKCE) the verifier so ``handle_callback``
        can complete the flow.
        """
        self._evict_expired()
        state = secrets.token_urlsafe(32)
        params: Dict[str, str] = {
            "response_type": "code",
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "scope": " ".join(self._config.scopes),
            "state": state,
        }
        if self._config.audience:
            params["audience"] = self._config.audience

        verifier: Optional[str] = None
        if self._config.use_pkce:
            verifier, challenge = self._pkce_pair()
            params["code_challenge"] = challenge
            params["code_challenge_method"] = "S256"

        if extra_params:
            params.update(extra_params)

        self._pending[state] = _PendingFlow(
            state=state, pkce_verifier=verifier, created_at=time.time()
        )
        return f"{self._config.authorize_url}?{urlencode(params)}"

    # ── Step 2: callback ───────────────────────────────────────────────

    async def handle_callback(self, code: str, state: str) -> Dict[str, Any]:
        """Exchange ``code`` for tokens. Verifies the ``state`` was issued by us.

        Returns the provider's token response verbatim (typically
        ``access_token``, ``token_type``, optional ``id_token`` and
        ``refresh_token``).
        """
        if not _HTTPX_AVAILABLE:
            raise OAuthFlowError(
                "httpx is required for the OAuth token exchange. "
                "Install with `pip install httpx`."
            )

        pending = self._pending.pop(state, None)
        if pending is None:
            raise OAuthFlowError("Unknown or expired `state` value")

        data: Dict[str, Any] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._config.redirect_uri,
            "client_id": self._config.client_id,
        }
        if self._config.client_secret:
            data["client_secret"] = self._config.client_secret
        if pending.pkce_verifier:
            data["code_verifier"] = pending.pkce_verifier

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                self._config.token_url,
                data=data,
                headers={"Accept": "application/json"},
            )
        if resp.status_code >= 400:
            raise OAuthFlowError(
                f"Token endpoint returned {resp.status_code}: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except Exception as e:
            raise OAuthFlowError(f"Token endpoint returned non-JSON: {e}") from e
