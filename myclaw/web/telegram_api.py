"""Telegram bot inside the Web UI (status, token test, restart).

The bot used to run only in ``zensynora gateway``. With
``channels.telegram.run_in_webui`` (default on) the Web UI starts it in its own
event loop at startup, restarts it when the Telegram settings change and stops
it at shutdown. The token is never logged or returned.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from myclaw.config import load_config
from myclaw.web.settings_api import guard

logger = logging.getLogger(__name__)
router = APIRouter()

_PLACEHOLDERS = ("YOUR_USER_ID", "YOUR_TELEGRAM_ID", "YOUR_ID", "USER_ID", "CHANGE_ME", "CHANGEME")

_channel = None                     # the running TelegramChannel (or None)
_fingerprint: Optional[tuple] = None  # Telegram settings the bot was started with
_registry_getter: Optional[Callable[[], dict]] = None
_last_error: Optional[Dict[str, Any]] = None  # why the last start failed


def set_registry_getter(fn: Callable[[], dict]) -> None:
    global _registry_getter
    _registry_getter = fn


def _load_channel_class():
    """Imported lazily: python-telegram-bot is only needed when the bot runs."""
    from myclaw.channels.telegram import TelegramChannel

    return TelegramChannel


def _tg(cfg=None):
    return (cfg or load_config()).channels.telegram


def _token(tg) -> str:
    try:
        return (tg.token.get_secret_value() or "").strip()
    except AttributeError:  # pragma: no cover - plain string
        return str(tg.token or "").strip()


def _settings_fingerprint(tg) -> tuple:
    return (bool(tg.enabled), _token(tg), tuple(tg.allowFrom or []), bool(tg.run_in_webui),
            bool(getattr(tg, "shared_knowledge", True)), int(getattr(tg, "max_workers", 0) or 0),
            bool(getattr(tg, "files_to_knowledge", False)))


def should_run(tg=None) -> bool:
    tg = tg or _tg()
    return bool(tg.enabled and tg.run_in_webui and _token(tg))


def is_placeholder(allow_from) -> bool:
    items = [str(a).strip() for a in (allow_from or []) if str(a).strip()]
    if not items:
        return True
    return any(a.upper().lstrip("@") in _PLACEHOLDERS or "YOUR" in a.upper() for a in items)


def bot_status() -> Dict[str, Any]:
    if _channel is None:
        if _last_error is not None:
            return dict(_last_error)
        return {"state": "stopped", "bot_username": None, "error": None, "since": None}
    try:
        st = dict(_channel.status() or {})
    except Exception as exc:  # pragma: no cover - defensive
        return {"state": "error", "bot_username": None, "error": str(exc), "since": None}
    st.setdefault("state", "stopped")
    return st


def bot_state() -> str:
    """Short state string for the sidebar health ("running", "stopped", …)."""
    return bot_status().get("state") or "stopped"


async def stop_bot() -> None:
    global _channel, _fingerprint, _last_error
    _last_error = None
    ch, _channel, _fingerprint = _channel, None, None
    if ch is None:
        return
    try:
        await ch.stop()
    except Exception as exc:  # never let the bot break the Web UI
        logger.warning("Could not stop the Telegram bot cleanly: %s", exc)


async def start_bot() -> bool:
    """Start the bot if the settings say so. Returns True when it was started."""
    global _channel, _fingerprint, _last_error
    if _channel is not None:
        return True
    cfg = load_config()
    tg = _tg(cfg)
    if not should_run(tg):
        return False
    _last_error = None
    ch = None
    try:
        cls = _load_channel_class()
        registry = _registry_getter if _registry_getter is not None else {}
        ch = cls(cfg, registry)
        await ch.start()
    except Exception as exc:  # the bot must never stop the Web UI from starting
        msg = _scrub(str(exc) or exc.__class__.__name__, tg)
        logger.warning("Could not start the Telegram bot: %s", msg)
        _last_error = {"state": "error", "bot_username": None, "error": msg, "since": time.time()}
        if ch is not None:
            try:
                await ch.stop()
            except Exception:  # pragma: no cover - best effort
                pass
        return False
    _channel, _fingerprint = ch, _settings_fingerprint(tg)
    logger.info("Telegram bot started inside the Web UI")
    return True


async def restart_bot() -> bool:
    await stop_bot()
    return await start_bot()


async def on_settings_changed() -> None:
    """Restart the bot only when the Telegram settings changed."""
    try:
        tg = _tg()
        fp = _settings_fingerprint(tg)
        if _channel is None:
            if should_run(tg):
                await start_bot()
            elif _last_error is not None:
                await stop_bot()  # clears the old error once the bot is turned off
            return
        if fp != _fingerprint:
            await restart_bot()
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Could not apply the Telegram settings: %s", exc)


def _scrub(text: str, tg=None, token: str = "") -> str:
    for t in {token, _token(tg) if tg is not None else ""}:
        if t:
            text = text.replace(t, "***")
    return text


# ── Endpoints ────────────────────────────────────────────────────────────────
@router.get("/api/telegram/status", dependencies=guard)
async def telegram_status():
    tg = _tg()
    return {
        "enabled": bool(tg.enabled),
        "run_in_webui": bool(tg.run_in_webui),
        "token_set": bool(_token(tg)),
        "allow_from": list(tg.allowFrom or []),
        "allow_from_placeholder": is_placeholder(tg.allowFrom),
        "bot": bot_status(),
    }


class TestRequest(BaseModel):
    token: Optional[str] = None  # unsaved value from the form, optional


@router.post("/api/telegram/test", dependencies=guard)
async def telegram_test(req: TestRequest):
    token = (req.token or "").strip() or _token(_tg())
    if not token:
        return {"ok": False, "error": "No bot token yet — get one from @BotFather and paste it here."}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            import os

            api = os.environ.get("ZENSYNORA_TELEGRAM_API_URL", "").rstrip("/") or "https://api.telegram.org"
            resp = await client.get(f"{api}/bot{token}/getMe")
    except Exception:
        return {"ok": False, "error": "Cannot reach Telegram — check the internet connection."}
    if resp.status_code in (401, 404):
        return {"ok": False, "error": "The token is wrong — copy it again from @BotFather."}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"Telegram answered with an error (HTTP {resp.status_code})."}
    try:
        result = (resp.json() or {}).get("result") or {}
    except Exception:
        return {"ok": False, "error": "Telegram sent an answer that could not be read."}
    username = result.get("username")
    return {"ok": True, "bot_username": username, "bot_name": result.get("first_name") or username}


@router.post("/api/telegram/restart", dependencies=guard)
async def telegram_restart():
    tg = _tg()
    if not tg.enabled:
        raise HTTPException(status_code=409, detail="The Telegram bot is turned off. Turn on 'Telegram bot enabled' and save first.")
    if not tg.run_in_webui:
        raise HTTPException(status_code=409, detail="'Run the bot inside the Web UI' is off, so the bot runs in zensynora gateway. Restart the gateway instead.")
    if not _token(tg):
        raise HTTPException(status_code=409, detail="No bot token saved yet — paste it from @BotFather and save first.")
    await restart_bot()
    return {"ok": True, "bot": bot_status()}



class SendTestIn(BaseModel):
    chat_id: Optional[str] = None  # empty = every numeric id in Allowed Telegram users


@router.post("/api/telegram/send-test", dependencies=guard)
async def telegram_send_test(req: SendTestIn = SendTestIn()):
    """Let the running bot send a short message, to check it can reach you."""
    if _channel is None or bot_status().get("state") != "running":
        raise HTTPException(status_code=409, detail="The bot is not running — check its status above first.")
    targets = [req.chat_id] if req.chat_id else [a for a in (_tg().allowFrom or []) if str(a).strip().isdigit()]
    if not targets:
        raise HTTPException(status_code=400, detail="No numeric user id in Allowed Telegram users to send to.")
    text = ("✅ ZenSynora is connected. Write me anything and an agent answers; "
            "start with @name to ask a specific agent. /help lists the commands.")
    results = []
    for chat in targets:
        try:
            await _channel.send_text(str(chat).strip(), text)
            results.append({"chat_id": str(chat), "ok": True})
        except Exception as exc:  # e.g. the person never pressed Start
            msg = str(exc)
            if "chat not found" in msg.lower() or "forbidden" in msg.lower() or "blocked" in msg.lower():
                msg = ("Telegram does not let the bot write first: open the bot in Telegram "
                       "and press Start (or send /start), then try again.")
            results.append({"chat_id": str(chat), "ok": False, "error": _scrub(msg, _tg())})
    return {"ok": all(r["ok"] for r in results), "results": results}
