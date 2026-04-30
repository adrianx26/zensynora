"""Marketplace source abstractions.

A *source* is anywhere plugins can be discovered. Every source implements
the same async contract — search, fetch a manifest, fetch the artifact —
and ``MarketplaceClient`` aggregates queries across many.

Concrete sources shipped here:

* :class:`LocalHubSource` — wraps the existing ``myclaw.hub`` registry.
  Always present so ZenHub keeps working alongside remote sources.
* :class:`HttpRegistrySource` — generic REST registry. Expects a JSON
  index endpoint and per-plugin manifest endpoints. Use for self-hosted
  registries or third-party alternatives.
* :class:`GitHubReleasesSource` — uses GitHub Releases as a registry.
  Each release is one plugin version; the release notes are the
  description; the asset is the artifact.
* :class:`OpenClawSource` — preset configuration of
  ``HttpRegistrySource`` for the OpenClaw marketplace. The base URL is
  configurable so air-gapped deployments can point at a private mirror.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from .manifest import Manifest

logger = logging.getLogger(__name__)


class SourceError(RuntimeError):
    """Raised on transport / parse errors. Sub-classed by source type."""


# Optional dep — used by HttpRegistrySource and friends.
try:
    import httpx
    _HTTPX_AVAILABLE = True
except Exception:
    httpx = None  # type: ignore[assignment]
    _HTTPX_AVAILABLE = False


def _require_httpx() -> None:
    if not _HTTPX_AVAILABLE:
        raise SourceError(
            "httpx is required for remote marketplace sources. "
            "Install with `pip install httpx`."
        )


@dataclass
class SearchResult:
    """One row in a search response. ``source_name`` lets callers
    disambiguate when two registries publish the same plugin name."""
    name: str
    version: str
    description: str
    source_name: str
    extra: Dict[str, Any]


# ── Abstract contract ─────────────────────────────────────────────────────


class MarketplaceSource(ABC):
    """Async abstract base. Implementations should be cheap to construct
    and not touch the network until a method is called — config-time
    failures are friendlier than first-request crashes."""

    #: Friendly name used in search results and logs.
    name: str = "marketplace-source"

    @abstractmethod
    async def search(self, query: str, limit: int = 20) -> List[SearchResult]: ...

    @abstractmethod
    async def get_manifest(self, plugin_name: str, version: Optional[str] = None) -> Manifest:
        """Return the parsed manifest. ``version=None`` ⇒ latest."""

    @abstractmethod
    async def fetch_artifact(self, manifest: Manifest) -> bytes:
        """Return the raw artifact bytes referenced by ``manifest``."""

    async def close(self) -> None:
        """Optional resource cleanup. Default no-op."""


# ── LocalHubSource ────────────────────────────────────────────────────────


class LocalHubSource(MarketplaceSource):
    """Read-only adapter over the existing ``myclaw.hub`` local registry.

    The local hub doesn't carry a real manifest in the Sprint-9 sense
    (no signature, no artifact_sha256), so we synthesize a Manifest from
    the index entry. Callers requesting strict signature verification
    against a local-hub source will need to pass a per-source policy.
    """
    name = "local-hub"

    def __init__(self, hub_index_path: Optional[Any] = None) -> None:
        from pathlib import Path
        self._hub_index = Path(hub_index_path) if hub_index_path else None

    def _load(self) -> Dict[str, Any]:
        from ..hub import HUB_INDEX  # late import to avoid cycle
        path = self._hub_index or HUB_INDEX
        if not path.exists():
            return {"skills": {}}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            raise SourceError(f"Failed to read local hub index: {e}") from e

    async def search(self, query: str, limit: int = 20) -> List[SearchResult]:
        index = self._load()
        q = query.lower()
        rows: List[SearchResult] = []
        for name, info in index.get("skills", {}).items():
            haystack = " ".join([
                name,
                info.get("description", ""),
                " ".join(info.get("tags", [])),
            ]).lower()
            if not q or q in haystack:
                rows.append(SearchResult(
                    name=name,
                    version=info.get("version", "0.0.0"),
                    description=info.get("description", ""),
                    source_name=self.name,
                    extra={"tags": info.get("tags", []), "downloads": info.get("downloads", 0)},
                ))
        rows.sort(key=lambda r: r.extra.get("downloads", 0), reverse=True)
        return rows[:limit]

    async def get_manifest(self, plugin_name: str, version: Optional[str] = None) -> Manifest:
        index = self._load()
        info = index.get("skills", {}).get(plugin_name)
        if info is None:
            raise SourceError(f"Plugin not in local hub: {plugin_name!r}")
        return Manifest(
            name=plugin_name,
            version=info.get("version", "0.0.0"),
            description=info.get("description", ""),
            author=info.get("author", "unknown"),
            artifact_url=f"file://{info.get('path', '')}",
            artifact_sha256="",  # Local hub doesn't track this; verify path skipped.
            tags=list(info.get("tags", [])),
            extra={"local_path": info.get("path")},
        )

    async def fetch_artifact(self, manifest: Manifest) -> bytes:
        from pathlib import Path
        path = manifest.extra.get("local_path") or manifest.artifact_url.replace("file://", "", 1)
        p = Path(path)
        if not p.exists():
            raise SourceError(f"Local artifact missing: {p}")
        return p.read_bytes()


# ── HttpRegistrySource ────────────────────────────────────────────────────


class HttpRegistrySource(MarketplaceSource):
    """Generic REST source. Expects:

    * ``GET {base_url}/index.json`` → ``{"plugins": {name: {version, description, ...}, ...}}``
    * ``GET {base_url}/plugins/{name}/manifest.json`` → ``Manifest`` payload (optionally
      with a sibling ``signature`` field used by the client).
    * ``GET {base_url}/plugins/{name}/{artifact}`` (URL from manifest) → bytes.

    Most public registries can be adapted to this shape with a thin static
    site; the index is just JSON.
    """
    name = "http-registry"

    def __init__(
        self,
        base_url: str,
        *,
        name: Optional[str] = None,
        timeout: float = 15.0,
        headers: Optional[Mapping[str, str]] = None,
        verify_tls: bool = True,
    ) -> None:
        if not base_url:
            raise ValueError("HttpRegistrySource requires base_url")
        self._base_url = base_url.rstrip("/")
        if name:
            self.name = name
        self._timeout = timeout
        self._headers = dict(headers or {})
        self._verify_tls = verify_tls
        self._client: Optional[Any] = None

    def _client_or_raise(self) -> Any:
        _require_httpx()
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers=self._headers,
                verify=self._verify_tls,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as e:
                logger.debug("Error closing http client", exc_info=e)
            self._client = None

    async def _get_json(self, path: str) -> Any:
        client = self._client_or_raise()
        url = f"{self._base_url}/{path.lstrip('/')}"
        resp = await client.get(url)
        if resp.status_code == 404:
            raise SourceError(f"Not found: {url}")
        if resp.status_code >= 400:
            raise SourceError(f"GET {url} → {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except Exception as e:
            raise SourceError(f"Non-JSON response from {url}: {e}") from e

    async def search(self, query: str, limit: int = 20) -> List[SearchResult]:
        index = await self._get_json("index.json")
        plugins: Dict[str, Any] = index.get("plugins", {})
        q = query.lower()
        rows: List[SearchResult] = []
        for name, info in plugins.items():
            haystack = " ".join([
                name,
                info.get("description", ""),
                " ".join(info.get("tags", [])),
            ]).lower()
            if not q or q in haystack:
                rows.append(SearchResult(
                    name=name,
                    version=info.get("version", "0.0.0"),
                    description=info.get("description", ""),
                    source_name=self.name,
                    extra=dict(info),
                ))
        return rows[:limit]

    async def get_manifest(self, plugin_name: str, version: Optional[str] = None) -> Manifest:
        path = f"plugins/{plugin_name}/manifest.json"
        if version:
            path = f"plugins/{plugin_name}/{version}/manifest.json"
        envelope = await self._get_json(path)
        # The envelope may be either the manifest itself OR
        # {"manifest": {...}, "signature": "..."}; we handle both shapes.
        if "manifest" in envelope and isinstance(envelope["manifest"], dict):
            manifest_dict = envelope["manifest"]
            signature = envelope.get("signature")
        else:
            manifest_dict = envelope
            signature = envelope.get("signature")
        manifest = Manifest.from_dict(manifest_dict)
        if signature:
            # Stash signature for the client to verify; it lives in extra
            # so it doesn't affect canonicalization on a re-sign.
            manifest.extra.setdefault("__signature__", signature)
        return manifest

    async def fetch_artifact(self, manifest: Manifest) -> bytes:
        client = self._client_or_raise()
        url = manifest.artifact_url
        # Allow relative artifact URLs by joining against base_url.
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"{self._base_url}/{url.lstrip('/')}"
        resp = await client.get(url)
        if resp.status_code >= 400:
            raise SourceError(f"GET {url} → {resp.status_code}")
        return resp.content


# ── GitHubReleasesSource ──────────────────────────────────────────────────


class GitHubReleasesSource(MarketplaceSource):
    """Adapter that treats GitHub Releases as a registry.

    Index = the list of releases on a single repo. Each release tag is
    one version. The first asset attached to the release is the artifact.
    Manifest data comes from a ``manifest.json`` asset (preferred) or is
    synthesized from the release metadata when no manifest is attached.
    """
    name = "github-releases"

    def __init__(
        self,
        repo: str,
        *,
        token: Optional[str] = None,
        manifest_asset_name: str = "manifest.json",
        timeout: float = 15.0,
    ) -> None:
        if "/" not in repo:
            raise ValueError(f"Expected `owner/repo`, got: {repo!r}")
        self._repo = repo
        self._token = token
        self._manifest_asset = manifest_asset_name
        self._timeout = timeout
        self._client: Optional[Any] = None

    def _client_or_raise(self) -> Any:
        _require_httpx()
        if self._client is None:
            headers = {"Accept": "application/vnd.github+json"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            self._client = httpx.AsyncClient(timeout=self._timeout, headers=headers)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    async def _list_releases(self) -> List[Dict[str, Any]]:
        client = self._client_or_raise()
        resp = await client.get(f"https://api.github.com/repos/{self._repo}/releases")
        if resp.status_code >= 400:
            raise SourceError(f"GitHub releases list failed: {resp.status_code}")
        return resp.json()

    async def search(self, query: str, limit: int = 20) -> List[SearchResult]:
        releases = await self._list_releases()
        q = query.lower()
        rows: List[SearchResult] = []
        for r in releases:
            tag = r.get("tag_name", "")
            body = r.get("body", "") or ""
            if not q or q in tag.lower() or q in body.lower():
                rows.append(SearchResult(
                    name=self._repo,
                    version=tag,
                    description=body[:200],
                    source_name=self.name,
                    extra={"published_at": r.get("published_at"), "draft": r.get("draft")},
                ))
        return rows[:limit]

    async def get_manifest(self, plugin_name: str, version: Optional[str] = None) -> Manifest:
        # ``plugin_name`` is ignored — a GitHubReleasesSource serves one repo.
        # Callers are expected to namespace by source.
        client = self._client_or_raise()
        if version:
            resp = await client.get(
                f"https://api.github.com/repos/{self._repo}/releases/tags/{version}"
            )
        else:
            resp = await client.get(
                f"https://api.github.com/repos/{self._repo}/releases/latest"
            )
        if resp.status_code >= 400:
            raise SourceError(f"GitHub release fetch failed: {resp.status_code}")
        rel = resp.json()
        assets = rel.get("assets", [])

        # Try to find a sidecar manifest.
        manifest_asset = next(
            (a for a in assets if a.get("name") == self._manifest_asset), None
        )
        if manifest_asset is not None:
            mresp = await client.get(manifest_asset["browser_download_url"])
            if mresp.status_code >= 400:
                raise SourceError(
                    f"GitHub manifest asset fetch failed: {mresp.status_code}"
                )
            envelope = mresp.json()
            if "manifest" in envelope and isinstance(envelope["manifest"], dict):
                m = Manifest.from_dict(envelope["manifest"])
                sig = envelope.get("signature")
            else:
                m = Manifest.from_dict(envelope)
                sig = envelope.get("signature")
            if sig:
                m.extra["__signature__"] = sig
            return m

        # Synthesize when no manifest asset.
        if not assets:
            raise SourceError(f"Release {rel.get('tag_name')} has no assets")
        primary = assets[0]
        return Manifest(
            name=self._repo,
            version=rel.get("tag_name", "0.0.0"),
            description=(rel.get("body") or "")[:1000],
            author=rel.get("author", {}).get("login", "unknown"),
            artifact_url=primary["browser_download_url"],
            artifact_sha256="",  # GitHub doesn't expose per-asset sha256 reliably
            tags=[],
        )

    async def fetch_artifact(self, manifest: Manifest) -> bytes:
        client = self._client_or_raise()
        # GitHub release-asset downloads need the binary Accept header
        # to bypass JSON envelope.
        resp = await client.get(
            manifest.artifact_url,
            headers={"Accept": "application/octet-stream"},
            follow_redirects=True,
        )
        if resp.status_code >= 400:
            raise SourceError(f"Artifact fetch failed: {resp.status_code}")
        return resp.content


# ── OpenClawSource ────────────────────────────────────────────────────────


class OpenClawSource(HttpRegistrySource):
    """Preset configuration for the OpenClaw plugin marketplace.

    The base URL is configurable so air-gapped deployments can point at a
    private mirror. Default targets the public OpenClaw registry layout.

    OpenClaw follows the same JSON-index conventions as
    :class:`HttpRegistrySource`, plus an optional ``X-OpenClaw-Client``
    header used by the registry for usage telemetry.
    """
    name = "openclaw"

    DEFAULT_BASE_URL = "https://registry.openclaw.ai"

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        client_id: str = "zensynora",
        timeout: float = 15.0,
    ) -> None:
        headers = {
            "X-OpenClaw-Client": client_id,
            "User-Agent": f"zensynora-marketplace/{client_id}",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        super().__init__(
            base_url=base_url or self.DEFAULT_BASE_URL,
            name="openclaw",
            timeout=timeout,
            headers=headers,
        )
