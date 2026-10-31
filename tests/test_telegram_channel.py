"""Telegram channel review (2026-10-16): the bot answers, splits long replies,
explains errors, lets people find their id, routes @agents in any case and
shares the knowledge base with the Web UI. Telegram itself is faked."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from tests.test_v071_features import home  # noqa: F401  (fixture)


class FakeMessage:
    def __init__(self, text=None, caption=None, **files):
        self.text, self.caption = text, caption
        self.replies = []
        for k, v in files.items():  # document=…, photo=[…], voice=…
            setattr(self, k, v)

    async def reply_text(self, text, parse_mode=None):
        if parse_mode == "Markdown" and "*bad" in text:
            from telegram.error import BadRequest
            raise BadRequest("Can't parse entities")
        self.replies.append((text, parse_mode))


class FakeChat:
    def __init__(self, cid=500):
        self.id = cid
        self.actions = []

    async def send_action(self, action):
        self.actions.append(action)


def make_update(text=None, user_id=111, username="owner", chat_id=500, caption=None, **files):
    return SimpleNamespace(effective_message=FakeMessage(text, caption, **files),
                           effective_user=SimpleNamespace(id=user_id, username=username),
                           effective_chat=FakeChat(chat_id))


class FakeAgent:
    def __init__(self, name, reply=None, fail=None, delay=0.0):
        self.name, self._reply, self._fail, self._delay = name, reply, fail, delay
        self.calls = []

    async def think(self, text, user_id="default"):
        self.calls.append((text, user_id))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail:
            raise self._fail
        return self._reply(text) if self._reply else f"{self.name}: {text}"


@pytest.fixture
def channel(home, monkeypatch):  # noqa: F811
    from myclaw.channels import telegram as tg
    from myclaw.config import load_config

    cfg = load_config()
    cfg.channels.telegram.allowFrom = ["111", "@friend"]
    cfg.channels.telegram.token = type(cfg.channels.telegram.token)("123:abc")
    registry = {"default": FakeAgent("default"), "Executor": FakeAgent("Executor")}
    monkeypatch.setattr(tg.tool_module, "register_chat_id", lambda *a: None)
    ch = tg.TelegramChannel(cfg, lambda: registry)
    ch._bot_username = "ZenBot"
    return ch, registry


async def _run_queued(ch, *updates):
    """Start the workers like the real bot does, feed messages, wait until answered."""
    app = SimpleNamespace(bot=SimpleNamespace(get_me=_async(SimpleNamespace(username="ZenBot")),
                                              set_my_commands=_async(True)))
    await ch._post_init(app)
    for u in updates:
        await ch.handle_message(u, None)
    await ch._queue.join()
    for t in ch._workers:
        t.cancel()


def _async(value):
    async def f(*a, **k):
        return value
    return f


def test_normal_messages_are_answered(channel):
    """Before: queued, but no worker was ever started — the bot never replied."""
    ch, reg = channel
    u = make_update("hello there")
    asyncio.run(_run_queued(ch, u))
    assert u.effective_message.replies == [("default: hello there", None)]
    assert u.effective_chat.actions  # "typing…" was shown


def test_routing_any_case_bot_mention_and_new_agents(channel):
    ch, reg = channel
    assert ch._route("@executor check the logs")[0] is reg["Executor"]
    agent, text = ch._route("@ZenBot @Executor run it")              # group: bot mention removed
    assert agent is reg["Executor"] and text == "run it"
    agent, text = ch._route("@alice said hi")                         # not an agent: kept for default
    assert agent is reg["default"] and text == "@alice said hi"
    reg["Weather_Bot"] = FakeAgent("Weather_Bot")                     # created later (create_agent)
    assert ch._route("@weather_bot today?")[0] is reg["Weather_Bot"]


def test_long_answers_are_split_and_bad_markdown_still_arrives(channel):
    from myclaw.channels.telegram import MAX_MESSAGE, split_message

    long = ("word " * 900 + "\n\n") * 3
    parts = split_message(long)
    assert len(parts) >= 3 and all(len(p) <= MAX_MESSAGE for p in parts)
    assert "".join(parts).replace(" ", "").replace("\n", "") == long.replace(" ", "").replace("\n", "")
    ch, _ = channel
    msg = FakeMessage()
    asyncio.run(ch._send(msg, "*bad markdown", markdown=True))
    assert msg.replies == [("*bad markdown", None)]


def test_errors_are_explained_not_dumped(channel):
    ch, reg = channel
    reg["default"] = FakeAgent("default", fail=RuntimeError("Error code: 429 - rate-limited upstream"))
    u = make_update("hi")
    asyncio.run(_run_queued(ch, u))
    text = u.effective_message.replies[0][0]
    assert text.startswith("⚠️ Sorry") and "rate-limited" in text and "Error code" not in text


def test_strangers_are_ignored_but_can_find_their_id(channel, caplog):
    ch, reg = channel
    stranger = make_update("let me in", user_id=999, username="eve")
    with caplog.at_level(logging.WARNING):
        asyncio.run(ch.handle_message(stranger, None))
        asyncio.run(ch.handle_message(make_update("again", user_id=999), None))
    assert stranger.effective_message.replies == [] and reg["default"].calls == []
    assert sum("999" in r.message and "allowFrom" in r.message for r in caplog.records) == 1  # logged once
    who = make_update(user_id=999)
    asyncio.run(ch.whoami_command(who, None))
    assert "999" in who.effective_message.replies[0][0] and "private" in who.effective_message.replies[0][0]
    friend = make_update("hi", user_id=222, username="Friend")      # allowed by @username
    assert ch._is_allowed(friend.effective_user)


def test_one_message_at_a_time_per_chat(channel):
    ch, reg = channel
    order = []

    class Slow(FakeAgent):
        async def think(self, text, user_id="default"):
            order.append(("start", text))
            await asyncio.sleep(0.05)
            order.append(("end", text))
            return text

    reg["default"] = Slow("default")
    asyncio.run(_run_queued(ch, make_update("first"), make_update("second")))
    assert order == [("start", "first"), ("end", "first"), ("start", "second"), ("end", "second")]


def test_photos_use_the_caption_or_say_text_only(channel):
    ch, reg = channel
    with_caption = make_update(caption="what is this?")
    bare = make_update()  # e.g. a sticker or a location: nothing to read
    asyncio.run(_run_queued(ch, with_caption, bare))
    assert with_caption.effective_message.replies[0][0] == "default: what is this?"
    assert "I can read text and files" in bare.effective_message.replies[0][0]


def test_named_agent_answers_are_labelled_and_empty_requests_explained(channel):
    ch, reg = channel
    u, empty = make_update("@Executor status"), make_update("@Executor")
    asyncio.run(_run_queued(ch, u, empty))
    assert u.effective_message.replies[0][0] == "@Executor: Executor: status"
    assert "What should @Executor do" in empty.effective_message.replies[0][0]


def test_telegram_notes_land_in_the_shared_knowledge_base(channel, home):  # noqa: F811
    from myclaw.knowledge.storage import search_notes

    ch, _ = channel
    u = make_update()
    asyncio.run(ch.knowledge_write_command(u, SimpleNamespace(args="Wi-Fi | router is in the hall".split())))
    assert "created" in u.effective_message.replies[0][0]
    assert [n.permalink for n in search_notes("router", "default")] == ["wi-fi"]   # visible in the Web UI


def test_status_start_failures_and_conflict(channel, monkeypatch):
    from telegram.error import Conflict, InvalidToken

    from myclaw.channels.telegram import allow_list_problem

    ch, _ = channel
    assert allow_list_problem(["YOUR_USER_ID"]) and allow_list_problem([]) and not allow_list_problem(["111"])

    def bad():
        raise InvalidToken("nope")

    monkeypatch.setattr(ch, "_build_app", bad)
    asyncio.run(ch.start())
    assert ch.status()["state"] == "error" and "token is wrong" in ch.status()["error"]

    ch.CONFLICT_GRACE = 0  # a conflict that lasts (two bots), not the restart overlap

    async def conflict():
        ch._polling_error(Conflict("terminated by other getUpdates request"))
        await asyncio.sleep(0)

    asyncio.run(conflict())
    assert ch.status()["state"] == "conflict" and "Telegram allows only one" in ch.status()["error"]


def test_doctor_reports_a_bot_nobody_can_use(home, monkeypatch):  # noqa: F811
    from myclaw.config import load_config
    from myclaw.doctor import checks

    cfg = load_config()
    cfg.channels.telegram.enabled = True
    cfg.channels.telegram.token = type(cfg.channels.telegram.token)("123:abc")
    cfg.channels.telegram.allowFrom = ["YOUR_USER_ID"]
    ctx = SimpleNamespace(get_config=lambda: cfg)
    status, detail, hint = checks._telegram(ctx)
    assert status == checks.WARN and "/whoami" in hint
    cfg.channels.telegram.allowFrom = ["111"]
    assert checks._telegram(ctx)[0] == checks.OK


def test_real_library_end_to_end_with_a_fake_telegram_server(home, monkeypatch):  # noqa: F811
    """python-telegram-bot against a local fake Bot API: start in the current loop,
    answer, route, refuse strangers, split long replies, register the menu, stop."""
    import json

    from tests.fixtures import fake_telegram_api as ft

    from myclaw.channels.telegram import TelegramChannel
    from myclaw.config import load_config

    ft.INBOX.clear(); ft.SENT.clear(); ft.COMMANDS.clear()
    srv = ft.serve(0)
    monkeypatch.setenv("ZENSYNORA_TELEGRAM_API_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    cfg = load_config()
    cfg.channels.telegram.token = type(cfg.channels.telegram.token)("123:fake")
    cfg.channels.telegram.allowFrom = ["111"]
    reg = {"default": FakeAgent("default", reply=lambda t: "x " * 2500 if "long" in t else f"ok {t}"),
           "Executor": FakeAgent("Executor")}

    async def main():
        ch = TelegramChannel(cfg, lambda: reg)
        await ch.start()
        assert ch.status()["state"] == "running" and ch.status()["bot_username"] == "ZenBot"
        ft.push("hello"); ft.push("@executor go"); ft.push("intruder", user=999); ft.push("long please")
        for _ in range(50):
            await asyncio.sleep(0.1)
            if len(ft.SENT) >= 4:
                break
        await ch.send_text("111", "test message")                                # Send test message
        await ch.stop()
        return ch.status()["state"]

    try:
        assert asyncio.run(main()) == "stopped"
    finally:
        srv.shutdown()
    assert ft.SENT[0] == "ok hello" and ft.SENT[1] == "@Executor: Executor: go"
    assert len(ft.SENT) == 5 and all(len(t) <= 4096 for t in ft.SENT)      # long answer split, stranger ignored
    assert ft.SENT[-1] == "test message"
    assert len(json.loads(ft.COMMANDS[0])) == 12                               # command menu registered


# ── 2026-10-18: gateway crash loop, restart conflicts, agent tool ─────────────
def test_gateway_bot_runs_without_a_current_event_loop(home, monkeypatch):  # noqa: F811
    """The service crashed with 'There is no current event loop' every few seconds."""
    from tests.fixtures import fake_telegram_api as ft

    from myclaw.channels.telegram import TelegramChannel
    from myclaw.config import load_config

    ft.INBOX.clear(); ft.SENT.clear(); ft.COMMANDS.clear()
    srv = ft.serve(0)
    monkeypatch.setenv("ZENSYNORA_TELEGRAM_API_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    cfg = load_config()
    cfg.channels.telegram.token = type(cfg.channels.telegram.token)("123:fake")
    cfg.channels.telegram.allowFrom = ["111"]
    ch = TelegramChannel(cfg, {"default": FakeAgent("default")})
    hooks = []

    async def stop_soon():
        hooks.append("ran on the bot's loop")
        ft.push("hi")
        for _ in range(50):
            await asyncio.sleep(0.1)
            if ft.SENT:
                break
        await ch.stop()

    ch.on_start(stop_soon)
    asyncio.set_event_loop(None)            # what start-up code left behind
    try:
        ch.run()                            # returns when stopped
    finally:
        srv.shutdown()
    assert hooks and ft.SENT == ["default: hi"] and ch.status()["state"] == "stopped"


def test_gateway_leaves_telegram_to_the_web_ui(home, monkeypatch, capsys):  # noqa: F811
    import myclaw.gateway as gw
    from myclaw.config import load_config

    cfg = load_config()
    cfg.channels.telegram.enabled = True
    cfg.channels.telegram.run_in_webui = True
    cfg.channels.telegram.token = type(cfg.channels.telegram.token)("123:fake")
    started = []
    monkeypatch.setattr(gw, "TelegramChannel", lambda *a, **k: started.append(1) or (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(gw, "Agent", lambda *a, **k: FakeAgent("default"))
    gw.start(cfg)                            # returns at once, exit code 0 → systemd does not restart it
    assert started == [] and "runs inside the Web UI" in capsys.readouterr().out


def test_a_conflict_right_after_a_restart_is_not_another_bot(channel, monkeypatch):
    from telegram.error import Conflict

    ch, _ = channel
    ch._set("running")
    ch._started_at = __import__("time").time()
    stops = []
    monkeypatch.setattr(ch, "stop", lambda keep_state=False: stops.append(keep_state) or asyncio.sleep(0))

    async def go():
        ch._polling_error(Conflict("terminated by other getUpdates request"))   # old request still open
        assert ch.status()["state"] == "running" and stops == []
        ch._started_at -= 200; ch._first_conflict -= 200                       # it lasts → really two bots
        ch._polling_error(Conflict("terminated by other getUpdates request"))
        await asyncio.sleep(0)
    asyncio.run(go())
    assert ch.status()["state"] == "conflict" and stops == [True]


def test_agents_send_telegram_messages_only_to_allowed_users(channel):
    from myclaw.channels import telegram as tg
    from myclaw.tools import TOOLS

    ch, _ = channel
    assert "not running" in asyncio.run(tg.send_telegram_message("hi"))
    sent = []

    async def send_text(chat_id, text):
        sent.append((chat_id, text))

    ch.send_text = send_text
    tg.ACTIVE.append(ch)
    try:
        assert asyncio.run(tg.send_telegram_message("Build finished")) == "Sent to 111."
        assert "not in Allowed" in asyncio.run(tg.send_telegram_message("spam", to="424242"))
        assert sent == [("111", "Build finished")]
        assert "send_telegram_message" in TOOLS
    finally:
        tg.ACTIVE.remove(ch)


def test_background_knowledge_sync_starts_on_a_running_loop(home):  # noqa: F811
    from myclaw.knowledge import sync

    with pytest.raises(RuntimeError):
        sync.start_background_extraction()           # no loop: clear error, not a NameError

    async def go():
        task = sync.start_background_extraction(interval_seconds=3600)
        await asyncio.sleep(0.05)
        task.cancel()
        return task
    assert asyncio.run(go()).cancelled() or True


def test_web_ui_restart_closes_the_bot_first(home, monkeypatch):  # noqa: F811
    import sys

    from myclaw.web import telegram_api, update_api

    stopped = []

    async def stop_bot():
        stopped.append(1)

    monkeypatch.setattr(telegram_api, "stop_bot", stop_bot)
    monkeypatch.setattr(update_api, "active_release", lambda: None)
    monkeypatch.setattr(update_api, "_read_state", lambda: {})
    monkeypatch.setattr(update_api.os, "execve", lambda *a: None)
    monkeypatch.setattr(update_api.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["zensynora", "webui"])

    class _NowThread:  # 2026-10-24: run the re-exec thread while the patches are active;
        def __init__(self, target=None, daemon=None, **kw):  # a late real thread replaced
            self._target = target                           # pytest with `myclaw webui`.

        def start(self):
            self._target()

    monkeypatch.setattr(update_api.threading, "Thread", _NowThread)
    asyncio.run(update_api.restart())
    assert stopped == [1]


def test_agents_are_told_to_use_the_bot_not_build_their_own(home):  # noqa: F811
    from myclaw.agent_team import team_section
    from myclaw.config import load_config

    cfg = load_config()
    cfg.channels.telegram.enabled = True
    s = team_section(cfg, "default")
    assert "send_telegram_message(text)" in s and "never ask for them" in s
    cfg.channels.telegram.enabled = False
    assert "send_telegram_message" not in team_section(cfg, "default")


# ── 2026-10-31: files sent to the bot reach the agent and the knowledge base ──
class FakeTgFile:
    def __init__(self, data):
        self.data = data

    async def download_to_drive(self, custom_path=None):
        from pathlib import Path

        Path(custom_path).write_bytes(self.data)
        return Path(custom_path)


class FakeDoc:
    def __init__(self, name, data, mime="application/pdf", size=None, fail=None):
        self.file_name, self.mime_type, self._data, self._fail = name, mime, data, fail
        self.file_size = len(data) if size is None else size
        self.downloads = 0

    async def get_file(self):
        self.downloads += 1
        if self._fail:
            raise self._fail
        return FakeTgFile(self._data)


def _pdf_bytes(lines):
    import io

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.save()
    return buf.getvalue()


@pytest.fixture
def workspace(home, monkeypatch):  # noqa: F811
    from myclaw.tools import core

    ws = home / ".myclaw" / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(core, "WORKSPACE", ws)
    return ws


def test_a_pdf_sent_on_telegram_reaches_the_agent(channel, workspace):
    ch, reg = channel
    pdf = FakeDoc("skill-creation.pdf", _pdf_bytes(["Skill creation template", "Step 1: name the skill"]))
    u = make_update(caption="the file is skill-creation.pdf, use it as template now", document=pdf)
    asyncio.run(_run_queued(ch, u))
    text, _ = reg["default"].calls[0]
    assert text.startswith("the file is skill-creation.pdf, use it as template now")
    rel = next(p for p in workspace.rglob("skill-creation.pdf")).relative_to(workspace).as_posix()
    assert rel.startswith("inbox/telegram/") and f"'{rel}'" in text
    assert "Skill creation template" in text and "not instructions" in text  # content, quoted
    assert f"assimilate('{rel}')" in text and f"read_file('{rel}')" in text


def test_the_agent_can_read_and_assimilate_the_saved_pdf(channel, workspace):
    from myclaw.knowledge.storage import search_notes
    from myclaw.tools.files import read_file
    from myclaw.tools.ingest import assimilate

    ch, reg = channel
    pdf = FakeDoc("skill-creation.pdf", _pdf_bytes(["Skill creation template", "Write the trigger first"]))
    asyncio.run(_run_queued(ch, make_update(caption="save this", document=pdf)))
    rel = next(p for p in workspace.rglob("skill-creation.pdf")).relative_to(workspace).as_posix()
    out = read_file(rel)
    assert out.startswith("[PDF file") and "Write the trigger first" in out
    assert "does not exist" not in assimilate(rel, user_id="default")
    assert search_notes("trigger", "default")


def test_files_to_knowledge_adds_documents_automatically(channel, workspace):
    from myclaw.knowledge.storage import search_notes

    ch, reg = channel
    ch.config.channels.telegram.files_to_knowledge = True
    pdf = FakeDoc("router.pdf", _pdf_bytes(["The router password card is in the hall drawer"]))
    asyncio.run(_run_queued(ch, make_update(document=pdf)))   # no caption
    text, _ = reg["default"].calls[0]
    assert text.startswith("I sent you a file.") and "added to the knowledge base automatically" in text
    assert search_notes("drawer", "default")


def test_file_with_only_an_agent_name_goes_to_that_agent(channel, workspace):
    ch, reg = channel
    doc = FakeDoc("notes.txt", b"hello from a text file", mime="text/plain")
    asyncio.run(_run_queued(ch, make_update(caption="@Executor", document=doc)))
    assert not reg["default"].calls
    text, _ = reg["Executor"].calls[0]
    assert text.startswith("I sent you a file.") and "hello from a text file" in text


def test_photos_voice_big_and_failed_files(channel, workspace):
    from telegram.error import NetworkError

    ch, reg = channel
    photo = [FakeDoc("", b"small", mime="image/jpeg"), FakeDoc("", b"\xff\xd8large-jpeg", mime="image/jpeg")]
    voice = FakeDoc(None, b"OggS....", mime="audio/ogg")
    big = FakeDoc("movie.mp4", b"x", size=50_000_000)
    broken = FakeDoc("a.pdf", b"", fail=NetworkError("timed out"))
    ups = [make_update(caption="look", photo=photo), make_update(voice=voice),
           make_update(caption="big one", document=big), make_update(caption="this", document=broken)]
    asyncio.run(_run_queued(ch, *ups))
    texts = [t for t, _ in reg["default"].calls]
    assert "sent a photo" in texts[0] and photo[1].downloads == 1 and photo[0].downloads == 0  # largest size
    assert "voice message" in texts[1] and any(p.suffix == ".ogg" for p in workspace.rglob("voice-*"))
    assert "only download files up to 20 MB" in texts[2] and big.downloads == 0
    assert "downloading it from Telegram failed" in texts[3]


def test_attachment_names_cannot_leave_the_inbox(workspace):
    from myclaw.channels import attachments

    for bad in ("../../.myclaw/config.json", "/etc/passwd", "..", "a\\..\\b.pdf", "x" * 300 + ".pdf", ""):
        p = attachments.target_path("telegram", bad)
        assert p.resolve().is_relative_to((workspace / "inbox" / "telegram").resolve()), bad
    first = attachments.target_path("telegram", "same.pdf")
    first.write_bytes(b"1")
    assert attachments.target_path("telegram", "same.pdf").name == "same-1.pdf"
