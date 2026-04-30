"""Marketplace client — aggregates sources, verifies, installs.

Wires the abstractions in ``sources.py`` and ``manifest.py`` together
into the single object an operator actually uses:

    client = MarketplaceClient([
        OpenClawSource(api_key="..."),
        GitHubReleasesSource("acme/acme-plugins"),
        LocalHubSource(),
    ], hmac_secret=b"shared-secret")

    rows = await client.search("redis")
    await client.install("redis-cache", source_name="openclaw")
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from .manifest import (
    Manifest,
    ManifestVerificationError,
    hash_artifact,
    verify_manifest,
)
from .sources import MarketplaceSource, SearchResult, SourceError

logger = logging.getLogger(__name__)


DEFAULT_INSTALL_DIR = Path.home() / ".myclaw" / "plugins" / "installed"


class MarketplaceClient:
    """Aggregates multiple :class:`MarketplaceSource` instances.

    Args:
        sources: Ordered list of sources. Search results are concatenated
            in this order; install lookups try sources in order until one
            resolves the requested plugin.
        hmac_secret: HMAC secret used to verify manifest signatures.
            ``None`` means signature verification is **skipped** (use only
            for fully-trusted sources like the local hub).
        require_signature: If True, manifests without a signature are
            rejected even when ``hmac_secret`` is provided. Defaults to
            True when ``hmac_secret`` is set, False otherwise.
        install_dir: Where artifacts are written.
    """

    def __init__(
        self,
        sources: Sequence[MarketplaceSource],
        *,
        hmac_secret: Optional[bytes] = None,
        require_signature: Optional[bool] = None,
        install_dir: Optional[Path] = None,
    ) -> None:
        if not sources:
            raise ValueError("MarketplaceClient requires at least one source")
        self._sources: List[MarketplaceSource] = list(sources)
        self._hmac_secret = hmac_secret
        self._require_signature = (
            require_signature
            if require_signature is not None
            else (hmac_secret is not None)
        )
        self._install_dir = Path(install_dir or DEFAULT_INSTALL_DIR)

    @property
    def sources(self) -> List[MarketplaceSource]:
        return list(self._sources)

    async def close(self) -> None:
        for s in self._sources:
            try:
                await s.close()
            except Exception as e:
                logger.debug("Source %s close failed", s.name, exc_info=e)

    # ── Discovery ─────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        *,
        limit_per_source: int = 20,
        sources: Optional[Sequence[str]] = None,
    ) -> List[SearchResult]:
        """Search every configured source (or a subset) and concatenate.

        Returns results in the same order as ``self.sources`` so users
        always see their preferred source first. Failures in one source
        are logged but never block results from others.
        """
        targets = self._resolve_sources(sources)
        out: List[SearchResult] = []
        for src in targets:
            try:
                rows = await src.search(query, limit=limit_per_source)
                out.extend(rows)
            except SourceError as e:
                logger.warning("Source %s search failed: %s", src.name, e)
        return out

    async def get_manifest(
        self,
        plugin_name: str,
        version: Optional[str] = None,
        source_name: Optional[str] = None,
    ) -> Manifest:
        """Resolve a manifest from a specific source, or first match."""
        targets = self._resolve_sources([source_name] if source_name else None)
        last_err: Optional[Exception] = None
        for src in targets:
            try:
                return await src.get_manifest(plugin_name, version=version)
            except SourceError as e:
                last_err = e
                continue
        raise SourceError(
            f"Plugin {plugin_name!r} not found in any configured source"
            + (f" (last error: {last_err})" if last_err else "")
        )

    # ── Verification ──────────────────────────────────────────────────

    def verify(self, manifest: Manifest, artifact: bytes) -> None:
        """Verify a manifest's signature and that ``artifact`` matches.

        Skipped when no HMAC secret was configured. Caller controls
        strictness via the ``require_signature`` constructor flag.
        """
        signature = manifest.extra.get("__signature__")
        if self._hmac_secret is not None:
            if not signature:
                if self._require_signature:
                    raise ManifestVerificationError(
                        f"Manifest for {manifest.name!r} has no signature; "
                        "set require_signature=False to allow unsigned plugins"
                    )
            else:
                verify_manifest(manifest, signature, self._hmac_secret)
        elif self._require_signature:
            raise ManifestVerificationError(
                "require_signature=True but no hmac_secret configured"
            )

        # Artifact integrity: the manifest *claims* a sha256; we verify
        # what we downloaded matches. Empty claims (e.g. local sources)
        # opt out of this check.
        if manifest.artifact_sha256:
            actual = hash_artifact(artifact)
            if actual != manifest.artifact_sha256:
                raise ManifestVerificationError(
                    f"Artifact sha256 mismatch for {manifest.name!r}: "
                    f"manifest claims {manifest.artifact_sha256[:12]}…, got {actual[:12]}…"
                )

    # ── Install ───────────────────────────────────────────────────────

    async def install(
        self,
        plugin_name: str,
        *,
        version: Optional[str] = None,
        source_name: Optional[str] = None,
    ) -> Path:
        """Resolve, verify, and write a plugin artifact to disk.

        Returns the path of the installed artifact. The caller is
        responsible for whatever post-install steps the plugin needs
        (loading via ``plugin_system``, registering as a tool, etc.).
        """
        manifest = await self.get_manifest(plugin_name, version=version, source_name=source_name)
        # Find the source that produced the manifest so we use the same
        # one to fetch the artifact (matters for HTTP sources with auth).
        target_source = self._source_named(source_name) if source_name else None
        if target_source is None:
            for src in self._sources:
                try:
                    test_manifest = await src.get_manifest(plugin_name, version=version)
                    if test_manifest.name == manifest.name and test_manifest.version == manifest.version:
                        target_source = src
                        break
                except SourceError:
                    continue
        if target_source is None:
            raise SourceError(
                f"Could not resolve a source for {plugin_name!r}@{version!r}"
            )

        artifact = await target_source.fetch_artifact(manifest)
        self.verify(manifest, artifact)

        self._install_dir.mkdir(parents=True, exist_ok=True)
        # Filename layout: <name>-<version>-<sha8>.<ext>; ext defaults to
        # ``bin`` when the URL is opaque.
        ext = manifest.artifact_url.rsplit(".", 1)[-1] if "." in manifest.artifact_url[-8:] else "bin"
        sha8 = hashlib.sha256(artifact).hexdigest()[:8]
        target = self._install_dir / f"{manifest.name}-{manifest.version}-{sha8}.{ext}"
        target.write_bytes(artifact)
        logger.info("Installed plugin %s@%s → %s", manifest.name, manifest.version, target)
        return target

    # ── Helpers ───────────────────────────────────────────────────────

    def _resolve_sources(
        self, names: Optional[Sequence[Optional[str]]]
    ) -> List[MarketplaceSource]:
        """``names=None`` means all sources; otherwise filter & preserve order."""
        if not names:
            return list(self._sources)
        wanted = {n for n in names if n}
        return [s for s in self._sources if s.name in wanted]

    def _source_named(self, name: str) -> Optional[MarketplaceSource]:
        for s in self._sources:
            if s.name == name:
                return s
        return None
