"""Plugin manifest schema + HMAC-SHA256 signing/verification.

A manifest describes a publishable plugin: name, version, author, the
URL or content hash of the artifact, declared permissions, etc.
Publishers compute an HMAC over the canonical manifest bytes using a
shared secret; installers verify before any code touches disk.

We use HMAC (symmetric) rather than asymmetric signatures because:

* The secret is per-source, not per-publisher. OpenClaw or your own
  registry holds the publishing key; clients hold the verification key.
* It's standard library only — no ``cryptography`` dep.
* For supply-chain protection at the *transport* layer, HTTPS already
  authenticates the registry. This protects integrity of the manifest
  artifact through redirects, mirrors, and local caches.

If you need per-publisher Ed25519 signatures, add a parallel field —
the verify path is pluggable via ``verify_manifest(verifier=...)``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Mapping, Optional


class ManifestVerificationError(Exception):
    """Raised when a manifest's signature is missing, malformed, or wrong."""


@dataclass
class Manifest:
    """Canonical plugin manifest record.

    Fields are intentionally narrow — a manifest is metadata + a pointer
    to the artifact, not the artifact itself. Anything provider-specific
    goes in ``extra`` so the canonical form stays stable across sources.
    """

    name: str
    version: str
    description: str
    author: str
    artifact_url: str
    artifact_sha256: str
    api_version: str = "1"
    tags: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    homepage: Optional[str] = None
    license: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Manifest":
        # Tolerate unknown forward-compatible fields by stuffing them into ``extra``.
        known = {
            "name", "version", "description", "author", "artifact_url",
            "artifact_sha256", "api_version", "tags", "permissions",
            "homepage", "license", "extra",
        }
        kwargs = {k: data[k] for k in known if k in data}
        unknown = {k: v for k, v in data.items() if k not in known and k != "signature"}
        extra = dict(kwargs.get("extra", {}))
        extra.update(unknown)
        kwargs["extra"] = extra
        return cls(**kwargs)


# ── Canonicalization ─────────────────────────────────────────────────────

# We sign a deterministic byte representation of the manifest. Two
# manifests with the same content but different key ordering, whitespace,
# or unicode escaping must produce the same bytes — otherwise signatures
# would be brittle. ``json.dumps`` with ``sort_keys=True``,
# ``separators=(",", ":")`` (no whitespace), and ``ensure_ascii=False``
# (preserve UTF-8) is the standard recipe.

def canonical_manifest_bytes(manifest: Manifest) -> bytes:
    """Return the deterministic byte representation used for signing.

    Strips the reserved ``__signature__`` slot from ``extra`` before
    serializing — otherwise a manifest that carries its own signature
    would self-invalidate (signing changes the bytes, which changes the
    signature, etc.).
    """
    payload = manifest.to_dict()
    extra = payload.get("extra")
    if isinstance(extra, dict) and "__signature__" in extra:
        # Make a shallow copy so we don't mutate the caller's manifest.
        scrubbed = dict(extra)
        scrubbed.pop("__signature__", None)
        payload = dict(payload)
        payload["extra"] = scrubbed
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


# ── Signing & verification ──────────────────────────────────────────────


def sign_manifest(manifest: Manifest, secret: bytes) -> str:
    """Return a base64-encoded HMAC-SHA256 signature.

    The signature is meant to live in a sidecar field (``manifest +
    {"signature": "..."}``) and excluded from the canonical bytes.
    Callers are responsible for serializing the wrapper.
    """
    if not isinstance(secret, (bytes, bytearray)) or not secret:
        raise ValueError("HMAC secret must be non-empty bytes")
    mac = hmac.new(secret, canonical_manifest_bytes(manifest), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("ascii")


def verify_manifest(
    manifest: Manifest,
    signature: str,
    secret: bytes,
    *,
    verifier: Optional[Callable[[Manifest, str, bytes], bool]] = None,
) -> None:
    """Verify ``signature`` over ``manifest`` with ``secret``.

    Raises :class:`ManifestVerificationError` on any failure. The
    optional ``verifier`` lets callers swap in asymmetric crypto without
    forking this module — when supplied it must return True/False and
    is consulted *instead of* the HMAC check.
    """
    if not signature or not isinstance(signature, str):
        raise ManifestVerificationError("Empty or non-string signature")

    if verifier is not None:
        try:
            ok = bool(verifier(manifest, signature, secret))
        except Exception as e:
            raise ManifestVerificationError(f"Custom verifier raised: {e}") from e
        if not ok:
            raise ManifestVerificationError("Custom verifier rejected the signature")
        return

    if not isinstance(secret, (bytes, bytearray)) or not secret:
        raise ManifestVerificationError("HMAC secret must be non-empty bytes")

    try:
        sig_bytes = base64.b64decode(signature, validate=True)
    except Exception as e:
        raise ManifestVerificationError(f"Signature is not valid base64: {e}") from e

    expected = hmac.new(
        secret, canonical_manifest_bytes(manifest), hashlib.sha256
    ).digest()

    # ``compare_digest`` keeps the timing constant — important even for
    # short-lived process tokens because timing leaks compose across
    # mirrors / CDNs.
    if not hmac.compare_digest(sig_bytes, expected):
        raise ManifestVerificationError("Signature mismatch")


def hash_artifact(data: bytes) -> str:
    """Compute the SHA-256 hex digest of an artifact's bytes.

    Pair with ``Manifest.artifact_sha256`` to detect tampering of the
    plugin payload itself (not just the manifest). Verifies what the
    manifest *claims* the artifact is.
    """
    return hashlib.sha256(data).hexdigest()
