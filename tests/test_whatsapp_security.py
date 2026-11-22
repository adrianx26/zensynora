"""WhatsApp webhook signature and configuration regressions."""

from __future__ import annotations

import hashlib
import hmac


def test_webhook_signature_is_fail_closed_and_validates_body(monkeypatch):
    from myclaw.channels.whatsapp import WhatsAppChannel
    from myclaw.config import AppConfig
    from pydantic import SecretStr

    cfg = AppConfig()
    cfg.channels.whatsapp.app_secret = SecretStr("")
    channel = WhatsAppChannel(cfg, {})
    body = b'{"object":"whatsapp_business_account"}'
    assert channel._verify_signature(body, None) is False

    cfg.channels.whatsapp.app_secret = SecretStr("app-secret")
    channel = WhatsAppChannel(cfg, {})
    digest = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    assert channel._verify_signature(body, "sha256=" + digest) is True
    assert channel._verify_signature(body, None) is False
    assert channel._verify_signature(body + b" ", "sha256=" + digest) is False


def test_whatsapp_app_secret_environment_override(monkeypatch):
    from myclaw.config import AppConfig, ENV_OVERRIDES

    assert ENV_OVERRIDES["channels.whatsapp.app_secret"] == "MYCLAW_WHATSAPP_APP_SECRET"
    monkeypatch.setenv("MYCLAW_WHATSAPP_APP_SECRET", "from-env")
    cfg = AppConfig()
    from myclaw.config import _apply_env_overrides

    overridden = _apply_env_overrides(cfg.model_dump(mode="python"))
    assert overridden["channels"]["whatsapp"]["app_secret"] == "from-env"


def test_whatsapp_app_secret_is_exposed_in_settings():
    from pathlib import Path

    view = Path("webui/src/components/SettingsView.tsx").read_text(encoding="utf-8")
    assert "channels.whatsapp.app_secret" in view
    assert "WhatsApp app secret" in view
