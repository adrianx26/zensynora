"""JWT (RS256/HS256) verification with optional JWKS rotation.

Purpose: replace the static API-key check on admin endpoints with a real
identity flow. Callers extract a ``Bearer`` token from the
``Authorization`` header, hand it to ``JWTAuthenticator.verify``, and
get back an ``AuthenticatedPrincipal`` (or an exception).

Optional dependency: ``PyJWT``. The classes import cleanly without it;
the first method call raises ``RuntimeError`` with install instructions.
This way config validation can run on machines that don't yet have the
extras installed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

# ── Optional dependency probing ──────────────────────────────────────────

try:  # pragma: no cover - import guard
    import jwt as _jwt
    from jwt import PyJWKClient

    _JWT_AVAILABLE = True
except Exception:
    _jwt = None  # type: ignore[assignment]
    PyJWKClient = None  # type: ignore[assignment]
    _JWT_AVAILABLE = False


def _require_jwt() -> None:
    if not _JWT_AVAILABLE:
        raise RuntimeError(
            "PyJWT is not installed. Install with `pip install PyJWT[crypto]` "
            "or use the API-key authenticator."
        )


class JWTVerificationError(Exception):
    """Raised when a JWT fails verification (signature, expiry, audience…)."""


@dataclass
class AuthenticatedPrincipal:
    """The verified identity attached to a request after auth succeeds.

    ``user_id`` is the token's ``sub`` claim. ``scopes`` comes from
    whichever claim the issuer uses (``scope`` space-separated, ``scp``
    list, or a custom claim — controlled via ``scope_claim``). ``raw``
    is the full decoded token for callers that need extra claims.
    """
    user_id: str
    scopes: Set[str] = field(default_factory=set)
    issuer: Optional[str] = None
    audience: Optional[str] = None
    expires_at: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


class JWTAuthenticator:
    """Verifies bearer tokens against either a shared secret or a JWKS endpoint.

    Args:
        secret: HS256/HS384/HS512 shared secret. Mutually exclusive with
            ``jwks_url``. Suitable for internal tokens.
        jwks_url: URL of the issuer's JWKS endpoint. Used for RS256/ES256
            verification. The PyJWKClient handles key rotation and caching.
        algorithms: Allowed signing algorithms. Defaults to a permissive
            list — narrow it in production.
        issuer: Expected ``iss`` claim. Empty ⇒ unchecked.
        audience: Expected ``aud`` claim. Empty ⇒ unchecked.
        scope_claim: Which claim to read scopes from. Defaults to
            ``"scope"`` (space-separated string).
        leeway: Clock-skew tolerance in seconds.
    """

    def __init__(
        self,
        secret: Optional[str] = None,
        jwks_url: Optional[str] = None,
        algorithms: Optional[Sequence[str]] = None,
        issuer: Optional[str] = None,
        audience: Optional[str] = None,
        scope_claim: str = "scope",
        leeway: float = 0.0,
    ) -> None:
        if (secret is None) == (jwks_url is None):
            raise ValueError(
                "JWTAuthenticator requires exactly one of `secret` or `jwks_url`"
            )
        self._secret = secret
        self._jwks_url = jwks_url
        self._algorithms = list(algorithms or ["RS256", "HS256"])
        self._issuer = issuer
        self._audience = audience
        self._scope_claim = scope_claim
        self._leeway = leeway
        # Lazy: don't construct the JWKS client until first use, so
        # offline boots don't pay a network round-trip.
        self._jwks_client: Optional[Any] = None

    def _get_jwks_client(self) -> Any:
        _require_jwt()
        if self._jwks_client is None and self._jwks_url:
            self._jwks_client = PyJWKClient(self._jwks_url)
        return self._jwks_client

    def _extract_scopes(self, payload: Dict[str, Any]) -> Set[str]:
        """Read scopes flexibly — providers vary on encoding."""
        raw = payload.get(self._scope_claim)
        if raw is None:
            return set()
        if isinstance(raw, str):
            return {s for s in raw.split() if s}
        if isinstance(raw, (list, tuple, set)):
            return {str(s) for s in raw}
        return set()

    def verify(self, token: str) -> AuthenticatedPrincipal:
        """Verify ``token`` and return the principal. Raises on any failure."""
        _require_jwt()
        if not token:
            raise JWTVerificationError("Empty token")

        try:
            if self._secret is not None:
                payload = _jwt.decode(
                    token,
                    self._secret,
                    algorithms=self._algorithms,
                    audience=self._audience,
                    issuer=self._issuer,
                    leeway=self._leeway,
                )
            else:
                signing_key = self._get_jwks_client().get_signing_key_from_jwt(token).key
                payload = _jwt.decode(
                    token,
                    signing_key,
                    algorithms=self._algorithms,
                    audience=self._audience,
                    issuer=self._issuer,
                    leeway=self._leeway,
                )
        except _jwt.ExpiredSignatureError as e:
            raise JWTVerificationError("Token expired") from e
        except _jwt.InvalidTokenError as e:
            raise JWTVerificationError(str(e)) from e

        sub = payload.get("sub")
        if not sub:
            raise JWTVerificationError("Token missing `sub` claim")

        return AuthenticatedPrincipal(
            user_id=str(sub),
            scopes=self._extract_scopes(payload),
            issuer=payload.get("iss"),
            audience=payload.get("aud"),
            expires_at=payload.get("exp"),
            raw=payload,
        )

    def extract_bearer(self, authorization_header: Optional[str]) -> str:
        """Pull the token out of an ``Authorization: Bearer <token>`` header.

        Whitespace-tolerant; case-insensitive on the ``Bearer`` literal.
        """
        if not authorization_header:
            raise JWTVerificationError("Missing Authorization header")
        parts = authorization_header.strip().split(maxsplit=1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise JWTVerificationError("Authorization header must be `Bearer <token>`")
        return parts[1].strip()
