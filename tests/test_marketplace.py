"""Tests for the multi-source marketplace.

Network sources (``HttpRegistrySource``, ``GitHubReleasesSource``,
``OpenClawSource``) are exercised through stubbed httpx clients — no
real HTTP. The local hub source is exercised against a temp directory.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from myclaw.marketplace import (
    GitHubReleasesSource,
    HttpRegistrySource,
    LocalHubSource,
    Manifest,
    ManifestVerificationError,
    MarketplaceClient,
    OpenClawSource,
    SearchResult,
    SourceError,
    canonical_manifest_bytes,
    sign_manifest,
    verify_manifest,
)


SECRET = b"unit-test-secret-do-not-use-in-prod"


def _sample_manifest(**overrides: Any) -> Manifest:
    base = dict(
        name="redis-cache",
        version="1.2.3",
        description="A cache plugin",
        author="alice",
        artifact_url="https://example/artifact.zip",
        artifact_sha256="0" * 64,
        tags=["cache", "redis"],
    )
    base.update(overrides)
    return Manifest(**base)


# ── Manifest canonicalization + signing ──────────────────────────────────


def test_canonical_bytes_are_deterministic():
    a = _sample_manifest()
    b = _sample_manifest()
    assert canonical_manifest_bytes(a) == canonical_manifest_bytes(b)


def test_canonical_bytes_use_sorted_keys():
    """Two manifests with the same content must canonicalize identically
    even if dict construction order differed."""
    a = _sample_manifest(tags=["a", "b"])
    b = _sample_manifest()
    b.tags = ["a", "b"]
    assert canonical_manifest_bytes(a) == canonical_manifest_bytes(b)


def test_sign_then_verify_roundtrip():
    m = _sample_manifest()
    sig = sign_manifest(m, SECRET)
    verify_manifest(m, sig, SECRET)  # must not raise


def test_verify_rejects_tampered_manifest():
    m = _sample_manifest()
    sig = sign_manifest(m, SECRET)
    m.version = "9.9.9"  # tamper
    with pytest.raises(ManifestVerificationError):
        verify_manifest(m, sig, SECRET)


def test_verify_rejects_wrong_secret():
    m = _sample_manifest()
    sig = sign_manifest(m, SECRET)
    with pytest.raises(ManifestVerificationError):
        verify_manifest(m, sig, b"different-secret")


def test_verify_rejects_garbage_signature():
    m = _sample_manifest()
    with pytest.raises(ManifestVerificationError):
        verify_manifest(m, "not-base64!!!", SECRET)
    with pytest.raises(ManifestVerificationError):
        verify_manifest(m, "", SECRET)


def test_verify_supports_custom_verifier():
    """Asymmetric crypto users plug in their own verifier."""
    m = _sample_manifest()
    calls = {"n": 0}

    def fake_verifier(manifest, signature, secret):
        calls["n"] += 1
        assert manifest.name == "redis-cache"
        return signature == "ok"

    verify_manifest(m, "ok", b"unused", verifier=fake_verifier)
    assert calls["n"] == 1
    with pytest.raises(ManifestVerificationError):
        verify_manifest(m, "nope", b"unused", verifier=fake_verifier)


def test_sign_rejects_empty_secret():
    with pytest.raises(ValueError):
        sign_manifest(_sample_manifest(), b"")


# ── LocalHubSource ────────────────────────────────────────────────────────


def _write_local_hub(tmp_path: Path) -> Path:
    """Create a tiny local-hub index; return the index path."""
    hub = tmp_path / "hub"
    hub.mkdir()
    skill_path = hub / "redis-cache.py"
    skill_path.write_text("def redis_cache(): pass\n", encoding="utf-8")

    index = {
        "skills": {
            "redis-cache": {
                "name": "redis-cache",
                "version": "1.0.0",
                "description": "redis-backed cache",
                "tags": ["cache", "redis"],
                "author": "alice",
                "downloads": 7,
                "path": str(skill_path),
            },
            "other": {
                "name": "other",
                "version": "0.1.0",
                "description": "another tool",
                "tags": ["misc"],
                "downloads": 1,
                "path": str(skill_path),
            },
        }
    }
    idx = hub / "index.json"
    idx.write_text(json.dumps(index), encoding="utf-8")
    return idx


@pytest.mark.asyncio
async def test_local_hub_search_filters_and_orders(tmp_path):
    idx = _write_local_hub(tmp_path)
    source = LocalHubSource(hub_index_path=idx)
    results = await source.search("redis")
    assert len(results) == 1
    assert results[0].name == "redis-cache"
    assert results[0].source_name == "local-hub"


@pytest.mark.asyncio
async def test_local_hub_get_manifest_and_fetch(tmp_path):
    idx = _write_local_hub(tmp_path)
    source = LocalHubSource(hub_index_path=idx)
    m = await source.get_manifest("redis-cache")
    assert m.name == "redis-cache"
    assert m.version == "1.0.0"
    blob = await source.fetch_artifact(m)
    assert b"redis_cache" in blob


@pytest.mark.asyncio
async def test_local_hub_missing_plugin_raises(tmp_path):
    idx = _write_local_hub(tmp_path)
    source = LocalHubSource(hub_index_path=idx)
    with pytest.raises(SourceError):
        await source.get_manifest("ghost")


# ── HttpRegistrySource (stubbed httpx) ────────────────────────────────────


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self._json = json_data
        self.content = content
        self.text = json.dumps(json_data) if json_data is not None else ""
    def json(self):
        return self._json


class _FakeAsyncClient:
    """Minimal stand-in for httpx.AsyncClient — registers responses by URL."""
    def __init__(self, responses: Dict[str, _FakeResp], **_kw):
        self._responses = responses
        self.calls: List[str] = []
    async def get(self, url, **_kw):
        self.calls.append(url)
        if url in self._responses:
            return self._responses[url]
        return _FakeResp(status_code=404, json_data={"error": "not found"})
    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_http_registry_search_round_trip():
    index = {"plugins": {
        "redis-cache": {"version": "1.0.0", "description": "redis cache", "tags": ["cache"]},
        "logger":      {"version": "2.0.0", "description": "logging plugin", "tags": []},
    }}
    responses = {"https://reg/index.json": _FakeResp(json_data=index)}
    fake = _FakeAsyncClient(responses)

    with patch("myclaw.marketplace.sources.httpx") as m:
        m.AsyncClient = lambda **kw: fake
        m.AsyncClient.__call__ = lambda *a, **kw: fake
        src = HttpRegistrySource("https://reg")
        rows = await src.search("redis")
    assert [r.name for r in rows] == ["redis-cache"]


@pytest.mark.asyncio
async def test_http_registry_manifest_envelope_with_signature():
    """Manifest endpoint may wrap in {manifest, signature}; we unpack both shapes."""
    manifest_dict = {
        "name": "x", "version": "1", "description": "d", "author": "a",
        "artifact_url": "https://reg/x.zip", "artifact_sha256": "0" * 64,
    }
    envelope = {"manifest": manifest_dict, "signature": "sig-bytes-b64"}

    responses = {"https://reg/plugins/x/manifest.json": _FakeResp(json_data=envelope)}
    fake = _FakeAsyncClient(responses)
    with patch("myclaw.marketplace.sources.httpx") as m:
        m.AsyncClient = lambda **kw: fake
        src = HttpRegistrySource("https://reg")
        m_out = await src.get_manifest("x")
    assert m_out.name == "x"
    assert m_out.extra.get("__signature__") == "sig-bytes-b64"


@pytest.mark.asyncio
async def test_http_registry_404_raises():
    responses: Dict[str, _FakeResp] = {}
    fake = _FakeAsyncClient(responses)
    with patch("myclaw.marketplace.sources.httpx") as m:
        m.AsyncClient = lambda **kw: fake
        src = HttpRegistrySource("https://reg")
        with pytest.raises(SourceError):
            await src.get_manifest("nope")


# ── OpenClawSource ───────────────────────────────────────────────────────


def test_openclaw_default_base_url_and_headers():
    src = OpenClawSource(api_key="my-key", client_id="custom-client")
    assert src.name == "openclaw"
    assert src._headers["Authorization"] == "Bearer my-key"
    assert src._headers["X-OpenClaw-Client"] == "custom-client"


def test_openclaw_supports_custom_base_url_for_air_gap():
    src = OpenClawSource(base_url="https://internal.corp/openclaw-mirror")
    assert src._base_url == "https://internal.corp/openclaw-mirror"


# ── GitHubReleasesSource ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_github_releases_synthesizes_manifest_from_release():
    rel = {
        "tag_name": "v1.0.0",
        "body": "release notes",
        "author": {"login": "alice"},
        "assets": [
            {"name": "plugin.zip", "browser_download_url": "https://gh/asset.zip"},
        ],
    }
    responses = {
        "https://api.github.com/repos/acme/plug/releases/latest": _FakeResp(json_data=rel),
    }
    fake = _FakeAsyncClient(responses)
    with patch("myclaw.marketplace.sources.httpx") as m:
        m.AsyncClient = lambda **kw: fake
        src = GitHubReleasesSource("acme/plug")
        manifest = await src.get_manifest("acme/plug")
    assert manifest.name == "acme/plug"
    assert manifest.version == "v1.0.0"
    assert manifest.artifact_url == "https://gh/asset.zip"


def test_github_releases_rejects_bad_repo_format():
    with pytest.raises(ValueError):
        GitHubReleasesSource("just-a-name")


# ── MarketplaceClient ────────────────────────────────────────────────────


class _FakeSource:
    """Test double matching the MarketplaceSource protocol."""
    def __init__(self, name, manifests=None, fail_search=False):
        self.name = name
        self._manifests = manifests or {}
        self._fail_search = fail_search
        self.search_calls = 0
    async def search(self, query, limit=20):
        self.search_calls += 1
        if self._fail_search:
            raise SourceError("boom")
        return [
            SearchResult(
                name=name, version=m.version, description=m.description,
                source_name=self.name, extra={},
            )
            for name, m in self._manifests.items()
            if not query or query in name
        ]
    async def get_manifest(self, name, version=None):
        if name not in self._manifests:
            raise SourceError(f"not found: {name}")
        return self._manifests[name]
    async def fetch_artifact(self, manifest):
        return b"artifact-bytes"
    async def close(self):
        pass


@pytest.mark.asyncio
async def test_client_search_aggregates_and_preserves_order():
    a = _FakeSource("a", {"redis-cache": _sample_manifest()})
    b = _FakeSource("b", {"redis-cache": _sample_manifest(version="2.0.0")})
    client = MarketplaceClient([a, b])
    results = await client.search("redis")
    assert [r.source_name for r in results] == ["a", "b"]


@pytest.mark.asyncio
async def test_client_search_skips_failing_source():
    bad = _FakeSource("bad", fail_search=True)
    good = _FakeSource("good", {"x": _sample_manifest(name="x")})
    client = MarketplaceClient([bad, good])
    results = await client.search("")
    assert [r.source_name for r in results] == ["good"]


@pytest.mark.asyncio
async def test_client_install_writes_artifact(tmp_path):
    m = _sample_manifest(name="ok-plugin", artifact_url="https://example/ok.zip", artifact_sha256="")
    src = _FakeSource("a", {"ok-plugin": m})
    client = MarketplaceClient(
        [src],
        install_dir=tmp_path / "installs",
        # No HMAC secret => verification skipped; this is the "trusted source" path.
    )
    path = await client.install("ok-plugin", source_name="a")
    assert path.exists()
    assert path.read_bytes() == b"artifact-bytes"


@pytest.mark.asyncio
async def test_client_install_rejects_bad_artifact_hash(tmp_path):
    """Manifest claims one sha256, downloaded bytes hash to something else."""
    m = _sample_manifest(name="evil", artifact_sha256="ff" * 32)
    src = _FakeSource("a", {"evil": m})
    client = MarketplaceClient([src], install_dir=tmp_path / "i")
    with pytest.raises(ManifestVerificationError):
        await client.install("evil", source_name="a")


@pytest.mark.asyncio
async def test_client_install_requires_signature_when_configured(tmp_path):
    """With hmac_secret set, an unsigned manifest must be rejected."""
    m = _sample_manifest(name="unsigned", artifact_sha256="")
    src = _FakeSource("a", {"unsigned": m})
    client = MarketplaceClient(
        [src],
        hmac_secret=SECRET,
        install_dir=tmp_path / "i",
    )
    with pytest.raises(ManifestVerificationError):
        await client.install("unsigned", source_name="a")


@pytest.mark.asyncio
async def test_client_install_accepts_signed_manifest(tmp_path):
    m = _sample_manifest(name="signed", artifact_sha256="")
    sig = sign_manifest(m, SECRET)
    m.extra["__signature__"] = sig
    src = _FakeSource("a", {"signed": m})
    client = MarketplaceClient(
        [src], hmac_secret=SECRET, install_dir=tmp_path / "i",
    )
    path = await client.install("signed", source_name="a")
    assert path.exists()


@pytest.mark.asyncio
async def test_client_unknown_plugin_raises():
    src = _FakeSource("a", {})
    client = MarketplaceClient([src])
    with pytest.raises(SourceError):
        await client.install("nonexistent")


def test_client_requires_at_least_one_source():
    with pytest.raises(ValueError):
        MarketplaceClient([])
