"""Plugin marketplace — discover and install plugins/skills from multiple sources.

The existing ``myclaw/hub/`` is a *local-only* registry: it lives entirely
under ``~/.myclaw/hub/`` and has no concept of a remote source. This
package adds the missing layer:

  * **Multiple sources** — OpenClaw (or any HTTP registry), GitHub releases,
    custom URLs, plus the local ZenHub. ``MarketplaceClient`` aggregates
    queries across all configured sources.
  * **Signed manifests** — every published plugin has an HMAC-SHA256
    signature over the canonical manifest JSON. Installation refuses
    tampered manifests by default.
  * **Pluggable** — adding a new source is one new ``MarketplaceSource``
    subclass plus an entry in the config-driven factory.

Public surface::

    from myclaw.marketplace import (
        MarketplaceClient,
        Manifest, ManifestVerificationError,
        sign_manifest, verify_manifest,
        OpenClawSource, GitHubReleasesSource,
        HttpRegistrySource, LocalHubSource,
    )
"""

from .manifest import (
    Manifest,
    ManifestVerificationError,
    canonical_manifest_bytes,
    sign_manifest,
    verify_manifest,
)
from .sources import (
    MarketplaceSource,
    SourceError,
    SearchResult,
    LocalHubSource,
    HttpRegistrySource,
    GitHubReleasesSource,
    OpenClawSource,
)
from .client import MarketplaceClient

__all__ = [
    "Manifest",
    "ManifestVerificationError",
    "canonical_manifest_bytes",
    "sign_manifest",
    "verify_manifest",
    "MarketplaceSource",
    "SourceError",
    "SearchResult",
    "LocalHubSource",
    "HttpRegistrySource",
    "GitHubReleasesSource",
    "OpenClawSource",
    "MarketplaceClient",
]
