from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "codex_telegram_bot.py"

spec = importlib.util.spec_from_file_location("codex_telegram_bot", SCRIPT)
codex_telegram_bot = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = codex_telegram_bot
spec.loader.exec_module(codex_telegram_bot)


def _config(tmp_path: Path, **overrides):
    values = {
        "state_dir": tmp_path,
        "env_file": tmp_path / ".env",
        "access_file": tmp_path / "access.json",
        "db_path": tmp_path / "chats.sqlite",
        "logs_dir": tmp_path / "logs",
        "out_dir": tmp_path / "out",
        "token": "123456789:ABC",
        "owner_ids": {"111"},
        "model": "gpt-5.5",
        "engine": "app-server",
        "effort": "high",
        "task_effort": "xhigh",
        "session_scope": "shared",
        "cwd": ROOT,
        "sandbox": "danger-full-access",
        "approval": "never",
        "reply_timeout_seconds": 300,
        "poll_timeout_seconds": 30,
        "context_messages": 24,
        "shared_context_messages": 8,
        "steady_context_messages": 0,
        "context_text_chars": 800,
        "rollover_input_tokens": 200000,
        "batch_delay_seconds": 2.5,
        "deny_unknown": False,
        "ignore_user_config": True,
        "bypass_permissions": True,
        "channel_tools": True,
        "desktop_sync": True,
        "desktop_outbound": True,
        "wake_phrases": ("codex", "assistant", "bot"),
        "watch_phrases_path": tmp_path / "watch_phrases.txt",
        "codex_bin": "codex",
    }
    values.update(overrides)
    return codex_telegram_bot.Config(**values)


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "chats.sqlite")
    conn.row_factory = sqlite3.Row
    codex_telegram_bot.init_db(conn)
    return conn


def _policy() -> object:
    return codex_telegram_bot.AccessPolicy(
        dm_policy="allowlist",
        group_policy="decide",
        allowed_users={"111"},
        allowed_chats={"-100"},
        allowed_bots=set(),
        bot_policy="ai-decide",
    )


def _clear_wake_window(chat_id: str) -> None:
    with codex_telegram_bot._WAKE_WINDOW_LOCK:
        codex_telegram_bot._WAKE_WINDOW.pop(chat_id, None)


def _write_rollout_record(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def test_load_config_uses_public_defaults(tmp_path: Path, monkeypatch) -> None:
    for name in (
        "CODEX_TELEGRAM_ENGINE",
        "CODEX_TELEGRAM_SESSION_SCOPE",
        "CODEX_TELEGRAM_DESKTOP_SYNC",
        "CODEX_TELEGRAM_DESKTOP_OUTBOUND",
        "CODEX_TELEGRAM_WAKE_PHRASES",
        "CODEX_TELEGRAM_GROUP_DECISION_SOURCE",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = codex_telegram_bot.load_config(tmp_path, require_ready=False)

    assert cfg.state_dir == tmp_path
    assert cfg.engine == "app-server"
    assert cfg.session_scope == "shared"
    assert cfg.desktop_sync is True
    assert cfg.desktop_outbound is True
    assert cfg.direct_background is True
    assert cfg.direct_background_after_seconds == codex_telegram_bot.DEFAULT_DIRECT_BACKGROUND_AFTER_SECONDS
    assert cfg.direct_background_timeout_seconds == codex_telegram_bot.DEFAULT_DIRECT_BACKGROUND_TIMEOUT_SECONDS
    assert cfg.auto_worker is False
    assert cfg.auto_worker_check_seconds == codex_telegram_bot.DEFAULT_AUTO_WORKER_CHECK_SECONDS
    assert cfg.auto_worker_result_chars == codex_telegram_bot.DEFAULT_AUTO_WORKER_RESULT_CHARS
    assert cfg.wake_phrases == ("codex", "assistant", "bot")
    assert cfg.group_decision_source == "model"


def test_init_config_writes_public_wake_phrases(tmp_path: Path) -> None:
    codex_telegram_bot.init_config(tmp_path)

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CODEX_TELEGRAM_WAKE_PHRASES=codex,assistant,bot" in env_text
    assert "CODEX_TELEGRAM_DESKTOP_SYNC=1" in env_text
    assert "CODEX_TELEGRAM_DESKTOP_OUTBOUND=1" in env_text
    assert "CODEX_TELEGRAM_DIRECT_BACKGROUND=1" in env_text
    assert "CODEX_TELEGRAM_AUTO_WORKER=0" in env_text
    assert "CODEX_TELEGRAM_GROUP_DECISION_SOURCE" not in env_text
    access = json.loads((tmp_path / "access.json").read_text(encoding="utf-8"))
    assert access == {
        "dmPolicy": "allowlist",
        "groupPolicy": "decide",
        "allowedUsers": [],
        "allowedChats": [],
    }


def test_parse_command_uses_public_namespace() -> None:
    command = codex_telegram_bot.parse_command("/codex_status@codex_test_bot now", "codex_test_bot")

    assert command is not None
    assert command.name == "codex_status"
    assert command.args == ["now"]
    assert codex_telegram_bot.parse_command("/codex_status@other_bot", "codex_test_bot") is None
    assert codex_telegram_bot.parse_command("/start", "codex_test_bot").name == "start"
    shape = codex_telegram_bot.parse_command("/codex single", "codex_test_bot")
    assert shape.name == "codex"
    assert shape.args == ["single"]
    assert codex_telegram_bot.parse_command("/status", "codex_test_bot") is None


def test_group_run_errors_stay_local_instead_of_template_reply() -> None:
    result = codex_telegram_bot.RunResult(
        run_id="run-error",
        status="error",
        reply="diagnostic details",
        session_id_after=None,
        error="app-server bridge failed",
        channel_events=[],
    )
    group = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    private = codex_telegram_bot.Chat("111", "private", "Owner")

    assert (
        codex_telegram_bot.visible_error_reply_for_result(
            group,
            result,
            allow_silent_reply=False,
            explicitly_addressed=True,
        )
        == ""
    )
    assert (
        codex_telegram_bot.visible_error_reply_for_result(
            group,
            result,
            allow_silent_reply=True,
            explicitly_addressed=False,
        )
        == ""
    )
    assert (
        codex_telegram_bot.visible_error_reply_for_result(
            private,
            result,
            allow_silent_reply=False,
            explicitly_addressed=True,
        )
        == "diagnostic details"
    )


def test_exec_prompt_uses_neutral_public_identity(tmp_path: Path) -> None:
    cfg = _config(tmp_path, engine="exec")
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)

    prompt = codex_telegram_bot.build_prompt(conn, chat, sender, 1, "Can you check this?", cfg)

    assert "You are a Codex collaborator reached through Telegram." in prompt
    assert "Channel contract" in prompt
    assert "reply, send_photos, send_files, react, edit_message" in prompt
    assert "private transcript output for Codex Desktop" in prompt
    assert "Telegram chat stance" in prompt
    assert "Telegram shared-session" not in prompt


def test_app_server_base_instructions_are_public_and_include_tools(tmp_path: Path) -> None:
    instructions = codex_telegram_bot.app_server_base_instructions(_config(tmp_path))

    assert "You are a Codex collaborator reached through Telegram." in instructions
    assert "reply, send_photos, send_files, react, edit_message" in instructions
    assert "one shared Codex thread" in instructions
    assert "owner_private/dm" in instructions
    assert "Telegram chat stance" in instructions
    assert "Telegram channel administration" in instructions
    assert "owner in private chat" in instructions
    assert "leave one clearly identified chat" in instructions
    assert "non-owner messages, group chatter" in instructions
    assert "looks likely to take a while" in instructions
    assert "do not use a stock phrase" in instructions


def test_desktop_titles_include_merged_shared_thread(tmp_path: Path) -> None:
    shared_cfg = _config(tmp_path, session_scope="shared")
    per_chat_cfg = _config(tmp_path, session_scope="per-chat")
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")

    assert codex_telegram_bot.desktop_title_for_context(shared_cfg, chat) == "Telegram Codex - All Chats"
    assert codex_telegram_bot.desktop_title_for_context(per_chat_cfg, chat) == "Telegram Codex - Release Room"
    assert codex_telegram_bot.desktop_preview_for_context(shared_cfg, chat, "hello") == "[群 Release Room] hello"


def test_desktop_outbound_filters_non_user_records() -> None:
    assert codex_telegram_bot.is_desktop_outbound_user_text("Message typed in Desktop")
    assert not codex_telegram_bot.is_desktop_outbound_user_text("<environment_context>\n...\n</environment_context>")
    assert not codex_telegram_bot.is_desktop_outbound_user_text(
        '<channel source="telegram" chat_id="111">\nhello\n</channel>'
    )

    assert codex_telegram_bot.is_desktop_outbound_agent_text("Assistant final answer")
    assert not codex_telegram_bot.is_desktop_outbound_agent_text("TG sent: hello")
    assert not codex_telegram_bot.is_desktop_outbound_agent_text("TG skipped: hello")
    assert not codex_telegram_bot.is_desktop_outbound_agent_text("(silent)")


def test_sync_desktop_state_updates_recency_columns(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "codex-home"
    home.mkdir()
    thread_id = "22222222-2222-2222-2222-222222222222"
    with sqlite3.connect(home / "state_5.sqlite") as state:
        state.execute(
            """
            CREATE TABLE threads(
              id TEXT PRIMARY KEY,
              title TEXT,
              preview TEXT,
              cwd TEXT,
              updated_at INTEGER,
              updated_at_ms INTEGER,
              recency_at INTEGER,
              recency_at_ms INTEGER
            )
            """
        )
        state.execute(
            """
            INSERT INTO threads(
              id, title, preview, cwd, updated_at, updated_at_ms, recency_at, recency_at_ms
            ) VALUES(?, '', '', '', 1, 1000, 1, 1000)
            """,
            (thread_id,),
        )
    monkeypatch.setattr(codex_telegram_bot.time, "time", lambda: 1234.9)

    codex_telegram_bot.sync_desktop_state(home, thread_id, "Telegram Codex", " latest ", ROOT)

    with sqlite3.connect(home / "state_5.sqlite") as state:
        row = state.execute(
            "SELECT title, preview, cwd, updated_at, updated_at_ms, recency_at, recency_at_ms FROM threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
    assert row == ("Telegram Codex", "latest", str(ROOT), 1234, 1234000, 1234, 1234000)


def test_sync_desktop_state_closes_state_connection(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "state_5.sqlite").write_text("", encoding="utf-8")
    connections = []

    class FakeConnection:
        def __init__(self) -> None:
            self.closed = False

        def execute(self, *_args, **_kwargs):
            return None

        def commit(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    def fake_connect(*_args, **_kwargs):
        conn = FakeConnection()
        connections.append(conn)
        return conn

    monkeypatch.setattr(codex_telegram_bot.sqlite3, "connect", fake_connect)

    codex_telegram_bot.sync_desktop_state(home, "thread-1", "Telegram Codex", "latest", ROOT)

    assert len(connections) == 1
    assert connections[0].closed


def test_codex_thread_rollout_path_closes_state_connection(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "state_5.sqlite").write_text("", encoding="utf-8")
    rollout = tmp_path / "rollout.jsonl"
    connections = []

    class FakeCursor:
        def fetchone(self):
            return (str(rollout),)

    class FakeConnection:
        def __init__(self) -> None:
            self.closed = False

        def execute(self, *_args, **_kwargs):
            return FakeCursor()

        def close(self) -> None:
            self.closed = True

    def fake_connect(*_args, **_kwargs):
        conn = FakeConnection()
        connections.append(conn)
        return conn

    monkeypatch.setattr(codex_telegram_bot.sqlite3, "connect", fake_connect)

    assert codex_telegram_bot.codex_thread_rollout_path(home, "thread-1") == rollout
    assert len(connections) == 1
    assert connections[0].closed


def test_replace_rollout_user_prompt_removes_raw_prompt_with_same_display_text(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout.jsonl"
    run_id = "run-live"
    display = "[群 Release Room] Owner: /status"
    raw_prompt = (
        "<context>\n"
        "- earlier context that should stay out of Desktop\n"
        "</context>\n\n"
        '<channel source="telegram" chat_id="-100" chat_type="supergroup" chat_title="Release Room" user="Owner">\n'
        "/status\n"
        "</channel>"
    )
    local_prompt = (
        '<channel source="telegram" chat_id="-100" chat_type="supergroup" chat_title="Release Room" user="Owner">\n'
        "/status\n"
        "</channel>"
    )

    _write_rollout_record(
        rollout,
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": display}],
                "telegram_live_mirror_run_id": run_id,
            },
        },
    )
    _write_rollout_record(
        rollout,
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": raw_prompt}],
            },
        },
    )
    _write_rollout_record(
        rollout,
        {
            "type": "event_msg",
            "payload": {"type": "user_message", "message": raw_prompt},
        },
    )

    assert codex_telegram_bot.replace_rollout_user_prompt_display(
        rollout,
        local_prompt,
        display,
        live_mirror_run_id=run_id,
    )

    text = rollout.read_text(encoding="utf-8")
    assert "<context>" not in text
    records = [json.loads(line) for line in text.splitlines()]
    assert len(records) == 1
    assert records[0]["payload"]["telegram_live_mirror_run_id"] == run_id


def test_mark_superseded_run_updates_delivery_and_rollout(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    codex_telegram_bot.upsert_chat(conn, chat)
    home = tmp_path / "codex-home"
    home.mkdir()
    rollout = tmp_path / "rollout.jsonl"
    thread_id = "22222222-2222-2222-2222-222222222222"
    run_id = "20260619T150306Z--100-313-test"
    turn_id = "turn-313"
    log_path = tmp_path / "run.app-server.jsonl"
    prompt_path = tmp_path / "run.prompt.txt"
    reply_path = tmp_path / "run.reply.txt"
    log_path.write_text(json.dumps({"result": {"turn": {"id": turn_id}}}) + "\n", encoding="utf-8")
    prompt_path.write_text("", encoding="utf-8")
    reply_path.write_text("", encoding="utf-8")
    with sqlite3.connect(home / "state_5.sqlite") as state:
        state.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)")
        state.execute("INSERT INTO threads(id, rollout_path) VALUES(?, ?)", (thread_id, str(rollout)))
    _write_rollout_record(rollout, {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn_id}})
    _write_rollout_record(
        rollout,
        {
            "type": "event_msg",
            "payload": {
                "type": "agent_message",
                "message": "TG sent: 我在",
                "phase": "final_answer",
            },
        },
    )
    _write_rollout_record(
        rollout,
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "TG sent: 我在"}],
                "phase": "final_answer",
                "metadata": {"turn_id": turn_id},
            },
        },
    )
    _write_rollout_record(
        rollout,
        {
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": turn_id,
                "last_agent_message": "TG sent: 我在",
            },
        },
    )
    codex_telegram_bot.create_run(conn, run_id, chat.chat_id, thread_id, prompt_path, reply_path, log_path)
    codex_telegram_bot.finish_run(conn, run_id, "ok", thread_id, None)
    monkeypatch.setattr(codex_telegram_bot, "codex_home", lambda: home)

    reason = "newer Telegram message arrived before delivery"
    codex_telegram_bot.record_superseded_channel_deliveries(
        conn,
        cfg,
        chat.chat_id,
        [{"type": "reply", "chat_id": "current", "text": "我在"}],
        run_id,
        reason=reason,
    )
    codex_telegram_bot.mark_run_superseded(conn, run_id, reason)
    assert codex_telegram_bot.mark_desktop_run_superseded(conn, cfg, run_id, thread_id, reason=reason)

    run_row = conn.execute("SELECT status, error FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert (run_row["status"], run_row["error"]) == ("superseded", reason)
    delivery = conn.execute(
        "SELECT chat_id, delivery_status, text_preview, telegram_message_id, error FROM channel_deliveries WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    assert delivery["chat_id"] == chat.chat_id
    assert delivery["delivery_status"] == "superseded"
    assert delivery["text_preview"] == "我在"
    assert delivery["telegram_message_id"] is None
    assert delivery["error"] == reason
    rollout_text = rollout.read_text(encoding="utf-8")
    assert "TG skipped: newer Telegram message arrived before delivery. Draft not sent: 我在" in rollout_text
    assert "TG sent: 我在" not in rollout_text
    assert codex_telegram_bot.get_meta(conn, codex_telegram_bot.desktop_outbound_offset_key(thread_id)) == str(
        rollout.stat().st_size
    )


def test_public_batch_and_message_shape_commands_control_delivery(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)

    assert codex_telegram_bot.group_response_mode(conn, chat.chat_id) == "single"
    assert not service.should_batch_codex(conn, chat, allow_silent_reply=True)

    reply = codex_telegram_bot.handle_command(
        conn,
        cfg,
        _policy(),
        chat,
        sender,
        codex_telegram_bot.Command("codex_batch", ["batch"]),
    )
    assert "batch" in reply
    assert codex_telegram_bot.group_response_mode(conn, chat.chat_id) == "batch"
    assert service.should_batch_codex(conn, chat, allow_silent_reply=True)

    reply = codex_telegram_bot.handle_command(
        conn,
        cfg,
        _policy(),
        chat,
        sender,
        codex_telegram_bot.Command("codex_batch", ["single"]),
    )
    assert "single" in reply
    assert codex_telegram_bot.group_response_mode(conn, chat.chat_id) == "single"
    assert not service.should_batch_codex(conn, chat, allow_silent_reply=True)

    reply = codex_telegram_bot.handle_command(
        conn,
        cfg,
        _policy(),
        chat,
        sender,
        codex_telegram_bot.Command("codex", ["single"]),
    )
    assert "single" in reply
    assert codex_telegram_bot.message_shape(conn, chat.chat_id) == "single"

    sent: list[tuple[str, str, int | None]] = []

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append((str(chat_id), text, reply_to_message_id))
        return [123]

    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)

    assert service.send_channel_events(
        chat.chat_id,
        [
            {"type": "reply", "chat_id": chat.chat_id, "text": "first"},
            {"type": "reply", "chat_id": chat.chat_id, "text": "second"},
        ],
        77,
    )
    assert sent == [("-100", "first\n\nsecond", None)]


def test_traditional_mention_mode_only_wakes_on_identity_call(tmp_path: Path) -> None:
    cfg = _config(tmp_path, wake_phrases=("codex", "assistant", "project alpha"), group_decision_source="model")
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    codex_telegram_bot.upsert_chat(conn, chat)
    codex_telegram_bot.set_chat_mode(conn, chat.chat_id, "mention")
    chat_row = codex_telegram_bot.get_chat(conn, chat.chat_id)
    service = codex_telegram_bot.BotService(cfg)
    sender = codex_telegram_bot.Sender("222", "Friend", False)
    policy = _policy()

    assert service.should_call_codex(
        conn,
        chat,
        chat_row,
        sender,
        "codex你看看这个",
        {"message_id": 10, "text": "codex你看看这个"},
        policy,
    )
    assert service.should_call_codex(
        conn,
        chat,
        chat_row,
        sender,
        "我家codex今天怎么了",
        {"message_id": 12, "text": "我家codex今天怎么了"},
        policy,
    )
    assert service.should_call_codex(
        conn,
        chat,
        chat_row,
        sender,
        "codexbot 今天上线了吗",
        {"message_id": 13, "text": "codexbot 今天上线了吗"},
        policy,
    )
    assert not service.should_call_codex(
        conn,
        chat,
        chat_row,
        sender,
        "今天天气不错",
        {"message_id": 11, "text": "今天天气不错"},
        policy,
    )
    assert not service.should_call_codex(
        conn,
        chat,
        chat_row,
        sender,
        "project alpha开局了吗",
        {"message_id": 14, "text": "project alpha开局了吗"},
        policy,
    )
    assert not codex_telegram_bot.wake_window_active(chat.chat_id)


def test_unlisted_group_humans_follow_chat_modes(tmp_path: Path) -> None:
    cfg = _config(tmp_path, wake_phrases=("codex",), group_decision_source="model")
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    sender = codex_telegram_bot.Sender("222", "Friend", False)
    policy = _policy()
    service = codex_telegram_bot.BotService(cfg)
    codex_telegram_bot.upsert_chat(conn, chat)

    codex_telegram_bot.set_chat_mode(conn, chat.chat_id, "decide")
    row = codex_telegram_bot.get_chat(conn, chat.chat_id)
    assert service.should_call_codex(conn, chat, row, sender, "普通闲聊一句", {"message_id": 1}, policy)

    _clear_wake_window(chat.chat_id)
    codex_telegram_bot.set_chat_mode(conn, chat.chat_id, "smart")
    row = codex_telegram_bot.get_chat(conn, chat.chat_id)
    assert not service.should_call_codex(conn, chat, row, sender, "普通闲聊一句", {"message_id": 2}, policy)
    assert service.should_call_codex(conn, chat, row, sender, "codex 在吗", {"message_id": 3, "text": "codex 在吗"}, policy)

    _clear_wake_window(chat.chat_id)
    codex_telegram_bot.set_chat_mode(conn, chat.chat_id, "mention")
    row = codex_telegram_bot.get_chat(conn, chat.chat_id)
    assert not service.should_call_codex(conn, chat, row, sender, "普通闲聊一句", {"message_id": 4}, policy)
    assert service.should_call_codex(conn, chat, row, sender, "codex 在吗", {"message_id": 5, "text": "codex 在吗"}, policy)


def test_group_modes_route_as_decide_smart_or_mention(tmp_path: Path) -> None:
    cfg = _config(tmp_path, wake_phrases=("codex", "project alpha"), group_decision_source="model")
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    policy = _policy()
    service = codex_telegram_bot.BotService(cfg)
    codex_telegram_bot.upsert_chat(conn, chat)

    codex_telegram_bot.set_chat_mode(conn, chat.chat_id, "decide")
    row = codex_telegram_bot.get_chat(conn, chat.chat_id)
    assert service.should_call_codex(conn, chat, row, sender, "普通闲聊一句", {"message_id": 1}, policy)
    assert service.should_allow_silent_reply(chat, row, sender, policy)

    _clear_wake_window(chat.chat_id)
    codex_telegram_bot.set_chat_mode(conn, chat.chat_id, "smart")
    row = codex_telegram_bot.get_chat(conn, chat.chat_id)
    assert not service.should_call_codex(conn, chat, row, sender, "普通闲聊一句", {"message_id": 2}, policy)
    assert service.should_allow_silent_reply(chat, row, sender, policy)
    assert service.should_call_codex(conn, chat, row, sender, "codexbot 怎么回事", {"message_id": 3, "text": "codexbot 怎么回事"}, policy)
    assert codex_telegram_bot.wake_window_active(chat.chat_id)
    assert service.should_call_codex(conn, chat, row, sender, "普通跟一句", {"message_id": 4, "text": "普通跟一句"}, policy)

    _clear_wake_window(chat.chat_id)
    codex_telegram_bot.set_chat_mode(conn, chat.chat_id, "mention")
    row = codex_telegram_bot.get_chat(conn, chat.chat_id)
    assert not service.should_allow_silent_reply(chat, row, sender, policy)
    assert not service.should_call_codex(conn, chat, row, sender, "project alpha开局了吗", {"message_id": 5, "text": "project alpha开局了吗"}, policy)
    assert service.should_call_codex(conn, chat, row, sender, "codex 在吗", {"message_id": 6, "text": "codex 在吗"}, policy)
    assert not codex_telegram_bot.wake_window_active(chat.chat_id)


def test_auto_is_not_a_chat_mode_alias() -> None:
    assert codex_telegram_bot.valid_chat_mode("auto") is None


def test_group_bots_follow_chat_modes_like_humans(tmp_path: Path) -> None:
    cfg = _config(tmp_path, wake_phrases=("codex", "assistant"), group_decision_source="model")
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Bot Room")
    bot_sender = codex_telegram_bot.Sender("999", "Other Bot", True)
    policy = _policy()
    service = codex_telegram_bot.BotService(cfg)
    codex_telegram_bot.upsert_chat(conn, chat)

    codex_telegram_bot.set_chat_mode(conn, chat.chat_id, "decide")
    row = codex_telegram_bot.get_chat(conn, chat.chat_id)

    assert policy.allowed_bots == set()
    assert service.should_store_message(chat, bot_sender, policy)
    assert service.should_call_codex(
        conn,
        chat,
        row,
        bot_sender,
        "普通 bot 消息",
        {"message_id": 1, "text": "普通 bot 消息"},
        policy,
    )


    _clear_wake_window(chat.chat_id)
    codex_telegram_bot.set_chat_mode(conn, chat.chat_id, "smart")
    row = codex_telegram_bot.get_chat(conn, chat.chat_id)
    assert not service.should_call_codex(
        conn,
        chat,
        row,
        bot_sender,
        "普通 bot 消息",
        {"message_id": 2, "text": "普通 bot 消息"},
        policy,
    )
    assert service.should_call_codex(
        conn,
        chat,
        row,
        bot_sender,
        "codexbot 在吗",
        {"message_id": 3, "text": "codexbot 在吗"},
        policy,
    )
    assert codex_telegram_bot.wake_window_active(chat.chat_id)

    _clear_wake_window(chat.chat_id)
    codex_telegram_bot.set_chat_mode(conn, chat.chat_id, "mention")
    row = codex_telegram_bot.get_chat(conn, chat.chat_id)
    assert not service.should_call_codex(
        conn,
        chat,
        row,
        bot_sender,
        "普通 bot 消息",
        {"message_id": 4, "text": "普通 bot 消息"},
        policy,
    )
    assert service.should_call_codex(
        conn,
        chat,
        row,
        bot_sender,
        "codex 在吗",
        {"message_id": 5, "text": "codex 在吗"},
        policy,
    )
    assert not codex_telegram_bot.wake_window_active(chat.chat_id)


def test_legacy_mention_strict_alias_maps_to_traditional_mention(tmp_path: Path) -> None:
    cfg = _config(tmp_path, wake_phrases=("codex",))
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-9001", "supergroup", "Strict Room")
    sender = codex_telegram_bot.Sender("222", "Friend", False)
    policy = _policy()
    codex_telegram_bot.upsert_chat(conn, chat)
    codex_telegram_bot.set_chat_mode(conn, chat.chat_id, "mention_strict")
    row = codex_telegram_bot.get_chat(conn, chat.chat_id)
    assert row["mode"] == "mention"

    # 被直接叫到 → 触发，但传统档不开唤醒窗口
    assert codex_telegram_bot.should_trigger_group_reply(
        "codex 在吗", {"message_id": 1, "text": "codex 在吗"}, row, policy, cfg, None, None, sender,
    )
    assert not codex_telegram_bot.wake_window_active(chat.chat_id)

    # 普通消息（没点名）→ 不触发
    assert not codex_telegram_bot.should_trigger_group_reply(
        "今天天气不错", {"message_id": 2, "text": "今天天气不错"}, row, policy, cfg, None, None, sender,
    )


def test_smart_mode_opens_window_on_name(tmp_path: Path) -> None:
    cfg = _config(tmp_path, wake_phrases=("codex",))
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-9002", "supergroup", "Smart Room")
    sender = codex_telegram_bot.Sender("222", "Friend", False)
    policy = _policy()
    codex_telegram_bot.upsert_chat(conn, chat)
    codex_telegram_bot.set_chat_mode(conn, chat.chat_id, "smart")
    row = codex_telegram_bot.get_chat(conn, chat.chat_id)

    assert codex_telegram_bot.should_trigger_group_reply(
        "codex 在吗", {"message_id": 1, "text": "codex 在吗"}, row, policy, cfg, None, None, sender,
    )
    assert codex_telegram_bot.wake_window_active(chat.chat_id)


def test_smart_mode_watch_phrase_opens_window_and_allows_following_message(tmp_path: Path) -> None:
    (tmp_path / "watch_phrases.txt").write_text("project alpha\n", encoding="utf-8")
    cfg = _config(tmp_path, wake_phrases=("codex",))
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-9003", "supergroup", "Smart Watch Room")
    sender = codex_telegram_bot.Sender("222", "Friend", False)
    policy = _policy()
    codex_telegram_bot.upsert_chat(conn, chat)
    codex_telegram_bot.set_chat_mode(conn, chat.chat_id, "smart")
    row = codex_telegram_bot.get_chat(conn, chat.chat_id)
    _clear_wake_window(chat.chat_id)

    assert codex_telegram_bot.should_trigger_group_reply(
        "project alpha刚说了这句", {"message_id": 1, "text": "project alpha刚说了这句"}, row, policy, cfg, None, None, sender,
    )
    assert codex_telegram_bot.wake_window_active(chat.chat_id)

    assert codex_telegram_bot.should_trigger_group_reply(
        "然后呢", {"message_id": 2, "text": "然后呢"}, row, policy, cfg, None, None, sender,
    )


def test_addressed_quiet_request_reaches_model_in_smart_mode(tmp_path: Path) -> None:
    cfg = _config(tmp_path, wake_phrases=("codex",))
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Smart Room")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    policy = _policy()
    service = codex_telegram_bot.BotService(cfg)
    codex_telegram_bot.upsert_chat(conn, chat)
    codex_telegram_bot.set_chat_mode(conn, chat.chat_id, "smart")
    row = codex_telegram_bot.get_chat(conn, chat.chat_id)
    _clear_wake_window(chat.chat_id)

    assert not service.should_call_codex(
        conn,
        chat,
        row,
        sender,
        "普通闲聊一句",
        {"message_id": 1, "text": "普通闲聊一句"},
        policy,
    )
    assert service.should_call_codex(
        conn,
        chat,
        row,
        sender,
        "codex先别回",
        {"message_id": 2, "text": "codex先别回"},
        policy,
    )
    assert codex_telegram_bot.wake_window_active(chat.chat_id)


def test_wake_window_extends_when_bot_sends_message(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    chat_id = "-9004"
    _clear_wake_window(chat_id)
    now = {"value": 100.0}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps({"ok": True, "result": {"message_id": 123}}).encode("utf-8")

    monkeypatch.setattr(codex_telegram_bot.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(codex_telegram_bot.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    codex_telegram_bot.open_wake_window(chat_id, seconds=180)
    now["value"] = 200.0
    assert codex_telegram_bot.send_message(cfg, chat_id, "我接一句") == [123]

    now["value"] = 379.0
    assert codex_telegram_bot.wake_window_active(chat_id)
    now["value"] = 381.0
    assert not codex_telegram_bot.wake_window_active(chat_id)


def test_group_prompt_includes_last_five_same_chat_messages_before_trigger(tmp_path: Path) -> None:
    cfg = _config(tmp_path, wake_phrases=("codex",), group_decision_source="model")
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    for message_id in range(1, 7):
        codex_telegram_bot.store_new_message(
            conn,
            message_id,
            chat.chat_id,
            sender,
            f"history message {message_id}",
        )
    codex_telegram_bot.store_new_message(conn, 7, chat.chat_id, sender, "codex 当前消息")

    prompt = codex_telegram_bot.build_prompt(conn, chat, sender, 7, "codex 当前消息", cfg, allow_silent_reply=True)
    start = prompt.index("<recent_chat_window")
    end = prompt.index("</recent_chat_window>")
    recent_block = prompt[start:end]

    assert "history message 1" not in recent_block
    for message_id in range(2, 7):
        assert f"history message {message_id}" in recent_block
    assert "codex 当前消息" not in recent_block


def test_wake_trigger_names_phrase_when_directly_addressed(tmp_path: Path) -> None:
    cfg = _config(tmp_path, wake_phrases=("codex", "helper"))
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)

    prompt = codex_telegram_bot.build_prompt(conn, chat, sender, 1, "codex 在吗", cfg)

    assert "<wake_trigger>" in prompt
    assert "【codex】" in prompt


def test_wake_trigger_absent_for_ordinary_message(tmp_path: Path) -> None:
    cfg = _config(tmp_path, wake_phrases=("codex",))
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)

    prompt = codex_telegram_bot.build_prompt(conn, chat, sender, 1, "今天天气不错", cfg)

    assert "<wake_trigger>" not in prompt


def test_direct_wake_suppresses_mention_block(tmp_path: Path) -> None:
    (tmp_path / "watch_phrases.txt").write_text("project alpha\n", encoding="utf-8")
    cfg = _config(tmp_path, wake_phrases=("codex",))
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)

    # 只是提到关注对象（没被直接叫）→ 出"提及"块
    mention_only = codex_telegram_bot.build_prompt(conn, chat, sender, 1, "刚才project alpha说得对", cfg)
    assert "<watch_trigger>" in mention_only
    assert "<wake_trigger>" not in mention_only

    # 既被直接叫(codex)又提到关注对象(project alpha) → 只出"叫你"块，提及块被抑制
    both = codex_telegram_bot.build_prompt(conn, chat, sender, 2, "codex project alpha说得对", cfg)
    assert "<wake_trigger>" in both
    assert "<watch_trigger>" not in both


def test_chat_sender_relationships_track_first_seen_senders(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    sender = codex_telegram_bot.Sender("222", "Alice", False)
    codex_telegram_bot.upsert_chat(conn, chat)

    assert codex_telegram_bot.store_new_message(conn, 1, chat.chat_id, sender, "first")
    assert not codex_telegram_bot.store_new_message(conn, 1, chat.chat_id, sender, "duplicate")
    assert codex_telegram_bot.store_new_message(conn, 2, chat.chat_id, sender, "second")

    row = conn.execute(
        """
        SELECT sender_name, sender_kind, first_message_id, last_message_id, message_count
        FROM chat_sender_relationships
        WHERE chat_id = ? AND sender_id = ?
        """,
        (chat.chat_id, sender.user_id),
    ).fetchone()
    assert row is not None
    assert row["sender_name"] == "Alice"
    assert row["sender_kind"] == "user"
    assert row["first_message_id"] == 1
    assert row["last_message_id"] == 2
    assert row["message_count"] == 2


def test_prompt_includes_known_chat_sender_relationships(tmp_path: Path) -> None:
    cfg = _config(tmp_path, session_scope="per-chat")
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    prior_sender = codex_telegram_bot.Sender("222", "Alice", False)
    current_sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    codex_telegram_bot.store_new_message(conn, 1, chat.chat_id, prior_sender, "previous context")

    prompt = codex_telegram_bot.build_prompt(conn, chat, current_sender, 2, "codex 当前消息", cfg)

    assert "<telegram_relationships>" in prompt
    assert "[supergroup -100 Release Room] Alice (user, id=222); messages=1;" in prompt


def test_app_server_prompt_omits_telegram_outputs_for_ordinary_chat(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    codex_telegram_bot.record_channel_delivery(
        conn,
        "run-1",
        chat.chat_id,
        0,
        10,
        None,
        None,
        "previous visible reply",
        event_type="reply",
    )

    prompt = codex_telegram_bot.build_prompt(conn, chat, sender, 11, "普通聊天一句", cfg)

    assert "<telegram_outputs>" not in prompt
    assert "previous visible reply" not in prompt


def test_app_server_prompt_includes_telegram_outputs_for_edit_reference(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    codex_telegram_bot.record_channel_delivery(
        conn,
        "run-1",
        chat.chat_id,
        0,
        10,
        None,
        None,
        "previous visible reply",
        event_type="reply",
    )

    prompt = codex_telegram_bot.build_prompt(conn, chat, sender, 11, "把上一条改成：新的说法", cfg)

    assert "<telegram_outputs>" in prompt
    assert "bot_message_id=10" in prompt
    assert "previous visible reply" in prompt


def test_private_status_like_chat_enters_codex_instead_of_local_fast_reply(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = _config(tmp_path)
    conn = _conn(tmp_path)
    service = codex_telegram_bot.BotService(cfg)
    service.bot_id = "999"
    service.bot_username = "codex_bot"
    calls: list[dict[str, object]] = []

    assert codex_telegram_bot.looks_like_channel_status_question(
        "这样叫你你干活会更有劲吗",
        {"text": "这样叫你你干活会更有劲吗"},
    )

    def fake_run_single_message(
        conn_arg,
        chat,
        sender,
        message_id,
        message_thread_id,
        prompt_text,
        trigger_text,
        allow_silent_reply,
        explicitly_addressed,
    ):
        calls.append(
            {
                "chat_id": chat.chat_id,
                "message_id": message_id,
                "trigger_text": trigger_text,
                "explicitly_addressed": explicitly_addressed,
            }
        )

    monkeypatch.setattr(service, "run_single_message", fake_run_single_message)

    service.handle_update(
        conn,
        {
            "update_id": 1,
            "message": {
                "message_id": 42,
                "date": 1,
                "chat": {"id": 111, "type": "private", "first_name": "Owner"},
                "from": {"id": 111, "is_bot": False, "first_name": "Owner"},
                "text": "这样叫你你干活会更有劲吗",
            },
        },
    )

    assert calls == [
        {
            "chat_id": "111",
            "message_id": 42,
            "trigger_text": "这样叫你你干活会更有劲吗",
            "explicitly_addressed": True,
        }
    ]
    assert not conn.execute(
        "SELECT 1 FROM channel_deliveries WHERE run_id LIKE 'local-%' LIMIT 1"
    ).fetchone()


def test_public_debug_command_toggles_desktop_prompt_visibility(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)

    reply = codex_telegram_bot.handle_command(
        conn,
        cfg,
        _policy(),
        chat,
        sender,
        codex_telegram_bot.Command("codex_debug", ["on"]),
    )
    assert "已打开" in reply
    assert codex_telegram_bot.desktop_prompt_debug_enabled(conn)
    assert "desktopPromptDebug: True" in codex_telegram_bot.status_for_chat(conn, cfg, _policy(), chat.chat_id)

    reply = codex_telegram_bot.handle_command(
        conn,
        cfg,
        _policy(),
        chat,
        sender,
        codex_telegram_bot.Command("codex_debug", ["off"]),
    )
    assert "已关闭" in reply
    assert not codex_telegram_bot.desktop_prompt_debug_enabled(conn)


def test_shared_new_clears_chat_rows_and_desktop_outbound_offset(tmp_path: Path) -> None:
    cfg = _config(tmp_path, engine="app-server", session_scope="shared")
    conn = _conn(tmp_path)
    dm = codex_telegram_bot.Chat("111", "private", "Owner")
    group = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    old_session = "11111111-1111-1111-1111-111111111111"
    codex_telegram_bot.upsert_chat(conn, dm)
    codex_telegram_bot.upsert_chat(conn, group)
    codex_telegram_bot.set_session_for_config(conn, dm.chat_id, old_session, cfg)
    codex_telegram_bot.set_session_for_config(conn, group.chat_id, old_session, cfg)
    codex_telegram_bot.set_meta(conn, codex_telegram_bot.shared_handoff_meta_key(cfg.engine), "old handoff")
    codex_telegram_bot.set_meta(conn, codex_telegram_bot.desktop_outbound_offset_key(old_session), "123")

    codex_telegram_bot.set_session_for_config(conn, group.chat_id, None, cfg)

    assert codex_telegram_bot.shared_session_for_engine(conn, cfg.engine) is None
    assert codex_telegram_bot.shared_handoff_for_engine(conn, cfg.engine) is None
    assert codex_telegram_bot.latest_chat_session_for_engine(conn, cfg.engine) is None
    assert codex_telegram_bot.get_chat(conn, dm.chat_id)["codex_session_id"] is None
    assert codex_telegram_bot.get_chat(conn, group.chat_id)["codex_session_id"] is None
    assert codex_telegram_bot.get_meta(conn, codex_telegram_bot.desktop_outbound_offset_key(old_session)) is None


def test_desktop_outbound_forwards_user_and_agent_to_active_chat(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path, engine="app-server", session_scope="shared")
    home = tmp_path / "codex-home"
    home.mkdir()
    rollout = tmp_path / "rollout.jsonl"
    thread_id = "22222222-2222-2222-2222-222222222222"
    with sqlite3.connect(home / "state_5.sqlite") as state:
        state.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)")
        state.execute("INSERT INTO threads(id, rollout_path) VALUES(?, ?)", (thread_id, str(rollout)))
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    codex_telegram_bot.upsert_chat(conn, chat)
    codex_telegram_bot.set_session_for_config(conn, chat.chat_id, thread_id, cfg)
    codex_telegram_bot.set_meta(conn, codex_telegram_bot.desktop_outbound_offset_key(thread_id), "0")
    codex_telegram_bot.set_meta(conn, f"last_message_thread_id:{chat.chat_id}", "55")
    _write_rollout_record(rollout, {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}})
    _write_rollout_record(
        rollout,
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Desktop user text"}],
            },
        },
    )
    _write_rollout_record(
        rollout,
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "Desktop assistant text"}},
    )
    service = codex_telegram_bot.BotService(cfg)
    service.bot_id = "999"
    sent: list[tuple[str, str, int | None]] = []

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append((str(chat_id), text, message_thread_id))
        return [900 + len(sent)]

    monkeypatch.setattr(codex_telegram_bot, "codex_home", lambda: home)
    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)

    assert service.poll_desktop_outbound_once() == 2
    assert sent == [
        ("-100", "Desktop user text", 55),
        ("-100", "Desktop assistant text", 55),
    ]
    rows = conn.execute(
        "SELECT sender_name, text FROM messages WHERE chat_id = ? ORDER BY telegram_message_id",
        (chat.chat_id,),
    ).fetchall()
    assert [(row["sender_name"], row["text"]) for row in rows] == [
        ("Desktop", "Desktop user text"),
        ("Codex Desktop", "Desktop assistant text"),
    ]


def test_send_channel_events_omits_reply_to_unless_explicit(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    service = codex_telegram_bot.BotService(cfg)
    sent: list[tuple[str, str, int | None]] = []

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append((str(chat_id), text, reply_to_message_id))
        return [88]

    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)

    assert service.send_channel_events(
        "111",
        [{"type": "reply", "chat_id": "111", "text": "normal bubble"}],
        77,
        "run-default",
    )
    assert service.send_channel_events(
        "111",
        [{"type": "reply", "chat_id": "111", "text": "quoted bubble", "reply_to": "77"}],
        None,
        "run-quote",
    )
    assert sent == [
        ("111", "normal bubble", None),
        ("111", "quoted bubble", 77),
    ]


def test_send_channel_events_skips_already_immediate_reply_but_sends_later_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = _config(tmp_path)
    service = codex_telegram_bot.BotService(cfg)
    sent: list[tuple[str, str, int | None]] = []

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append((str(chat_id), text, message_thread_id))
        return [88]

    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)

    assert service.send_channel_events(
        "111",
        [
            {"type": "reply", "chat_id": "111", "text": "我看下这个群的 mode。", "delivered_immediately": True},
            {"type": "reply", "chat_id": "111", "text": "查完了，现在是 mention。"},
        ],
        77,
        "run-immediate",
        fallback_message_thread_id=9,
    )
    assert sent == [("111", "查完了，现在是 mention。", 9)]


def test_app_server_reply_tool_uses_turn_immediate_sender(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    client = codex_telegram_bot.CodexAppServerClient(cfg)
    delivered: list[list[dict[str, object]]] = []

    def immediate_sender(events: list[dict[str, object]]) -> None:
        delivered.append([dict(event) for event in events])
        for event in events:
            event["delivered_immediately"] = True

    client.current_turn_chat_id = "111"
    client.current_turn_immediate_channel_event_sender = immediate_sender
    events: list[dict[str, object]] = []

    result = client.record_dynamic_tool_call(
        {"tool": "reply", "arguments": {"text": "我看下这个群的 mode。"}},
        events,
    )

    assert result["success"] is True
    assert len(delivered) == 1
    assert delivered[0][0]["type"] == "reply"
    assert delivered[0][0]["chat_id"] == "current"
    assert delivered[0][0]["text"] == "我看下这个群的 mode。"
    assert events[0]["delivered_immediately"] is True


def test_run_codex_forwards_immediate_sender_to_app_server(tmp_path: Path) -> None:
    cfg = _config(tmp_path, engine="app-server", desktop_sync=False)
    conn = _conn(tmp_path)
    captured: dict[str, object] = {}

    class FakeAppServer:
        def run_turn(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return "session-after", "", None, [], args[1]

    def immediate_sender(events: list[dict[str, object]]) -> None:
        captured["events"] = events

    result = codex_telegram_bot.run_codex(
        conn,
        cfg,
        "111",
        None,
        "prompt text",
        123,
        "high",
        "Desktop title",
        "Desktop preview",
        FakeAppServer(),
        timeout_seconds=30,
        run_id="run-immediate-forward",
        immediate_channel_event_sender=immediate_sender,
    )

    assert result.status == "ok"
    assert captured["kwargs"]["timeout_seconds"] == 30
    assert captured["kwargs"]["immediate_channel_event_sender"] is immediate_sender


def test_send_message_logs_slow_delivery(tmp_path: Path, monkeypatch, capsys) -> None:
    cfg = _config(tmp_path)
    ticks = iter([100.0, 106.25])

    def fake_telegram_api(token, method, params, timeout=35):
        return {"ok": True, "result": {"message_id": 42}}

    monkeypatch.setattr(codex_telegram_bot.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(codex_telegram_bot, "telegram_api", fake_telegram_api)

    assert codex_telegram_bot.send_message(cfg, "111", "slow send") == [42]
    err = capsys.readouterr().err
    assert "telegram sendMessage slow status=ok elapsed=6.2s" in err
    assert "chat_id=111" in err


def test_direct_background_continues_silently_and_delivers_later(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(
        tmp_path,
        direct_background=True,
        direct_background_after_seconds=0.01,
        direct_background_timeout_seconds=60,
        auto_worker=False,
    )
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)

    sent: list[dict[str, object]] = []
    timeouts: list[int | None] = []

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "message_thread_id": message_thread_id,
            }
        )
        return [len(sent)]

    def fake_run_codex(
        conn_arg,
        config,
        chat_id,
        session_id_before,
        prompt,
        message_id,
        effort,
        desktop_title,
        desktop_preview,
        app_client,
        timeout_seconds=None,
        run_id=None,
        immediate_channel_event_sender=None,
    ):
        timeouts.append(timeout_seconds)
        time.sleep(0.1)
        return codex_telegram_bot.RunResult(
            run_id=run_id or codex_telegram_bot.safe_run_id(chat_id, message_id),
            status="ok",
            reply=codex_telegram_bot.NO_REPLY_SENTINEL,
            session_id_after=session_id_before,
            error=None,
            channel_events=[{"type": "reply", "chat_id": "current", "text": "完成了"}],
        )

    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)
    monkeypatch.setattr(codex_telegram_bot, "run_codex", fake_run_codex)
    monkeypatch.setattr(
        codex_telegram_bot,
        "start_typing_feedback",
        lambda *args, **kwargs: threading.Event(),
    )

    started = time.monotonic()
    service.run_single_message(
        conn,
        chat,
        sender,
        42,
        None,
        "做个大活",
        "做个大活",
        False,
        True,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.08
    assert sent == []
    assert timeouts == [60]

    deadline = time.monotonic() + 1
    while len(sent) < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [item["text"] for item in sent] == ["完成了"]
    assert (
        conn.execute("SELECT COUNT(*) FROM channel_deliveries WHERE event_type = 'background_ack'").fetchone()[0]
        == 0
    )

    lock = service.lock_for_chat(chat.chat_id)
    deadline = time.monotonic() + 1
    acquired = lock.acquire(blocking=False)
    while not acquired and time.monotonic() < deadline:
        time.sleep(0.01)
        acquired = lock.acquire(blocking=False)
    assert acquired
    lock.release()


def test_direct_background_waits_for_model_reply_without_bridge_ack(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(
        tmp_path,
        direct_background=True,
        direct_background_after_seconds=0.01,
        direct_background_timeout_seconds=60,
        auto_worker=False,
    )
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)
    sent: list[str] = []

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(text)
        return [len(sent)]

    def fake_run_codex(*args, **kwargs):
        time.sleep(0.1)
        return codex_telegram_bot.RunResult(
            run_id=kwargs.get("run_id") or "run-casual",
            status="ok",
            reply="会有一点。",
            session_id_after=None,
            error=None,
            channel_events=[],
        )

    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)
    monkeypatch.setattr(codex_telegram_bot, "run_codex", fake_run_codex)
    monkeypatch.setattr(
        codex_telegram_bot,
        "start_typing_feedback",
        lambda *args, **kwargs: threading.Event(),
    )

    service.run_single_message(
        conn,
        chat,
        sender,
        43,
        None,
        "这样叫你你干活会更有劲吗？",
        "这样叫你你干活会更有劲吗？",
        False,
        True,
    )

    deadline = time.monotonic() + 1
    while len(sent) < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sent == ["会有一点。"]
    assert (
        conn.execute("SELECT COUNT(*) FROM channel_deliveries WHERE event_type = 'background_ack'").fetchone()[0]
        == 0
    )


def test_user_visible_task_ack_copy_keeps_internal_routing_private() -> None:
    visible = [
        codex_telegram_bot.INTERRUPTED_BACKGROUND_NOTICE_TEXT,
    ]

    for text in visible:
        assert "worker" not in text.lower()
        assert "主线程" not in text
        assert "后台" not in text
        assert "路由" not in text


def test_worker_prompt_requires_confirmation_before_start(tmp_path: Path) -> None:
    cfg = _config(tmp_path, direct_background=False)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)

    prompt = codex_telegram_bot.app_server_base_instructions(cfg)

    assert "Do not delegate from keyword matches" in prompt
    assert "Only call codex_worker_start after the owner confirms" in prompt
    assert "a clear owner execution request can be that confirmation" in prompt


def test_heavy_single_turn_enters_resident_instead_of_auto_worker(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path, direct_background=False, auto_worker=True)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)
    sent: list[str] = []
    prompts: list[str] = []

    def fake_start_worker(*args, **kwargs):
        raise AssertionError("bridge must not start a worker before resident judgment and owner confirmation")

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(text)
        return [len(sent)]

    def fake_run_codex(
        conn_arg,
        config,
        chat_id,
        session_id_before,
        prompt,
        message_id,
        effort,
        desktop_title,
        desktop_preview,
        app_client,
        timeout_seconds=None,
        run_id=None,
        immediate_channel_event_sender=None,
    ):
        prompts.append(prompt)
        return codex_telegram_bot.RunResult(
            run_id="resident-run",
            status="ok",
            reply=codex_telegram_bot.NO_REPLY_SENTINEL,
            session_id_after=session_id_before,
            error=None,
            channel_events=[{"type": "reply", "chat_id": "current", "text": "我先看这块。"}],
        )

    monkeypatch.setattr(codex_telegram_bot, "start_codex_worker", fake_start_worker)
    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)
    monkeypatch.setattr(codex_telegram_bot, "run_codex", fake_run_codex)

    service.run_single_message(
        conn,
        chat,
        sender,
        42,
        None,
        "你看看 TG 日志为什么不回消息了，修一下",
        "你看看 TG 日志为什么不回消息了，修一下",
        False,
        True,
    )

    assert prompts
    assert "TG 日志" in prompts[0]
    assert sent == ["我先看这块。"]
    assert codex_telegram_bot.list_worker_states(cfg) == []
    assert codex_telegram_bot.list_worker_alarms(cfg) == []


def test_multi_step_single_turn_enters_resident_instead_of_auto_worker(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path, direct_background=False, auto_worker=True)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)
    sent: list[str] = []
    prompts: list[str] = []

    def fake_start_worker(*args, **kwargs):
        raise AssertionError("bridge must not start a worker from multi-step keywords")

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(text)
        return [len(sent)]

    def fake_run_codex(*args, **kwargs):
        prompts.append(args[4])
        return codex_telegram_bot.RunResult(
            run_id="resident-run",
            status="ok",
            reply=codex_telegram_bot.NO_REPLY_SENTINEL,
            session_id_after=args[3],
            error=None,
            channel_events=[{"type": "reply", "chat_id": "current", "text": "我在同一个线程里处理。"}],
        )

    monkeypatch.setattr(codex_telegram_bot, "start_codex_worker", fake_start_worker)
    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)
    monkeypatch.setattr(codex_telegram_bot, "run_codex", fake_run_codex)

    prompt = "先查现在逻辑，然后修掉问题，再跑验证"
    service.run_single_message(conn, chat, sender, 47, None, prompt, prompt, False, True)

    assert prompts
    assert sent == ["我在同一个线程里处理。"]
    assert codex_telegram_bot.list_worker_states(cfg) == []


def test_existing_worker_context_lets_resident_choose_continue_or_new_worker(tmp_path: Path) -> None:
    cfg = _config(tmp_path, direct_background=False)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    state = {
        "version": codex_telegram_bot.WORKER_STATE_VERSION,
        "task_id": "active-task",
        "title": "TG worker: group setup",
        "status": "needs_input",
        "pid": 0,
        "session_id": "worker-session-1",
        "cwd": str(ROOT),
        "model": cfg.model,
        "started_at": codex_telegram_bot.utc_now(),
        "finished_at": "",
        "turn_count": 1,
        "output_path": str(tmp_path / "workers" / "active-task.last.txt"),
        "jsonl_path": str(tmp_path / "workers" / "active-task.jsonl"),
        "stderr_path": str(tmp_path / "workers" / "active-task.stderr.log"),
        "auto_delivery": {
            "status": "supervised",
            "chat_id": "111",
            "message_id": 45,
            "message_thread_id": None,
            "reason": "execution or investigation task",
            "created_at": codex_telegram_bot.utc_now(),
            "alarm_id": "alarm-1",
            "alarm_due_at": codex_telegram_bot.utc_now(),
        },
    }
    codex_telegram_bot.write_worker_state(cfg, state)

    prompt = codex_telegram_bot.build_prompt(conn, chat, sender, 48, "查一下为什么刚才那个设置没生效", cfg)

    assert '<worker_context purpose="telegram resident routing">' in prompt
    assert "task_id=active-task" in prompt
    assert "codex_worker_continue" in prompt
    assert "Ask the owner for natural confirmation before starting" in prompt
    assert "start a new worker only after confirmation" in prompt


def test_group_setting_task_enters_resident_instead_of_auto_worker(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path, direct_background=False, auto_worker=True)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)
    sent: list[str] = []
    prompts: list[str] = []

    def fake_start_worker(*args, **kwargs):
        raise AssertionError("bridge must not start a worker from group-setting keywords")

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(text)
        return [len(sent)]

    def fake_run_codex(*args, **kwargs):
        prompts.append(args[4])
        return codex_telegram_bot.RunResult(
            run_id="resident-run",
            status="ok",
            reply=codex_telegram_bot.NO_REPLY_SENTINEL,
            session_id_after=args[3],
            error=None,
            channel_events=[{"type": "reply", "chat_id": "current", "text": "我先判断怎么处理。"}],
        )

    monkeypatch.setattr(codex_telegram_bot, "start_codex_worker", fake_start_worker)
    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)
    monkeypatch.setattr(codex_telegram_bot, "run_codex", fake_run_codex)

    prompt = "我想给你加到新群里，把这个群改成只有艾特你才说话，按群 id 设置。"
    service.run_single_message(conn, chat, sender, 45, None, prompt, prompt, False, True)

    assert prompts
    assert "新群" in prompts[0]
    assert sent == ["我先判断怎么处理。"]
    assert codex_telegram_bot.list_worker_states(cfg) == []


def test_small_edit_stays_in_shared_resident_thread(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path, direct_background=False)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)
    sent: list[str] = []
    run_ids: list[str | None] = []
    calls: list[str] = []

    def fake_start_worker(*args, **kwargs):
        raise AssertionError("tiny edits should stay in the shared Codex turn")

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(text)
        return [len(sent)]

    def fake_run_codex(
        conn_arg,
        config,
        chat_id,
        session_id_before,
        prompt,
        message_id,
        effort,
        desktop_title,
        desktop_preview,
        app_client,
        timeout_seconds=None,
        run_id=None,
        immediate_channel_event_sender=None,
    ):
        calls.append(prompt)
        return codex_telegram_bot.RunResult(
            run_id="tiny-run",
            status="ok",
            reply=codex_telegram_bot.NO_REPLY_SENTINEL,
            session_id_after=session_id_before,
            error=None,
            channel_events=[{"type": "reply", "chat_id": "current", "text": "好了"}],
        )

    monkeypatch.setattr(codex_telegram_bot, "start_codex_worker", fake_start_worker)
    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)
    monkeypatch.setattr(codex_telegram_bot, "run_codex", fake_run_codex)

    service.run_single_message(
        conn,
        chat,
        sender,
        43,
        None,
        "帮我改一下 README.md 里一行文案",
        "帮我改一下 README.md 里一行文案",
        False,
        True,
    )

    assert calls
    assert sent == ["好了"]


def test_busy_chat_message_waits_for_lock_and_runs_without_template(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path, direct_background=False)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)
    lock = service.lock_for_chat(chat.chat_id)
    assert lock.acquire(blocking=False)
    sent: list[str] = []
    prompts: list[str] = []

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(text)
        return [len(sent)]

    def fake_run_codex(*args, **kwargs):
        prompts.append(args[4])
        return codex_telegram_bot.RunResult(
            run_id="queued-run",
            status="ok",
            reply=codex_telegram_bot.NO_REPLY_SENTINEL,
            session_id_after=args[3],
            error=None,
            channel_events=[{"type": "reply", "chat_id": "current", "text": "接着处理完了。"}],
        )

    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)
    monkeypatch.setattr(codex_telegram_bot, "run_codex", fake_run_codex)
    threading.Timer(0.05, lock.release).start()
    started = time.monotonic()

    service.run_single_message(
        conn,
        chat,
        sender,
        46,
        None,
        "需要我先把你加进去吗",
        "需要我先把你加进去吗",
        False,
        True,
    )

    assert time.monotonic() - started >= 0.04
    assert prompts
    assert sent == ["接着处理完了。"]


def test_legacy_auto_worker_delivery_schedules_supervisor_alarm(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path, auto_worker=True, auto_worker_result_chars=1000)
    service = codex_telegram_bot.BotService(cfg)
    output_path = tmp_path / "workers" / "auto-task.last.txt"
    codex_telegram_bot.write_private_text(output_path, "changed files: scripts/foo.py\nchecks: pytest")
    state = {
        "version": codex_telegram_bot.WORKER_STATE_VERSION,
        "task_id": "auto-task",
        "title": "TG worker: test",
        "status": "complete",
        "pid": 0,
        "session_id": "",
        "cwd": str(ROOT),
        "model": cfg.model,
        "started_at": codex_telegram_bot.utc_now(),
        "finished_at": codex_telegram_bot.utc_now(),
        "turn_count": 1,
        "output_path": str(output_path),
        "jsonl_path": str(tmp_path / "workers" / "auto-task.jsonl"),
        "stderr_path": str(tmp_path / "workers" / "auto-task.stderr.log"),
        "auto_delivery": {
            "status": "pending",
            "chat_id": "111",
            "message_id": 42,
            "message_thread_id": None,
            "reason": "debugging or runtime inspection task",
            "created_at": codex_telegram_bot.utc_now(),
            "attempts": 0,
            "next_after_epoch": 0,
        },
    }
    codex_telegram_bot.write_worker_state(cfg, state)

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        raise AssertionError("auto-worker completion must be inspected by the Telegram supervisor, not sent directly")

    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)

    assert service.poll_auto_worker_supervision_once() == 1
    updated = codex_telegram_bot.read_worker_state(cfg, "auto-task")
    assert updated is not None
    assert updated["auto_delivery"]["status"] == "supervised"
    assert updated["auto_delivery"]["alarm_id"]
    alarms = codex_telegram_bot.list_worker_alarms(cfg)
    assert len(alarms) == 1
    assert alarms[0]["task_id"] == "auto-task"
    assert alarms[0]["chat_id"] == "111"


def test_manual_worker_start_tool_schedules_supervisor_alarm(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    client = codex_telegram_bot.CodexAppServerClient(cfg)
    client.current_turn_chat_id = "111"
    client.current_turn_message_thread_id = 9
    started: list[dict[str, str]] = []

    def fake_start_worker(config, *, task, title="", cwd="", task_id=None, session_id=None, turn_count=1):
        started.append({"task": task, "title": title, "cwd": cwd})
        return (
            {
                "version": codex_telegram_bot.WORKER_STATE_VERSION,
                "task_id": "manual-task",
                "title": title,
                "status": "running",
                "pid": 123,
                "session_id": "",
                "cwd": str(config.cwd),
                "model": config.model,
                "started_at": codex_telegram_bot.utc_now(),
                "finished_at": "",
                "turn_count": turn_count,
                "output_path": str(tmp_path / "workers" / "manual-task.last.txt"),
                "jsonl_path": str(tmp_path / "workers" / "manual-task.jsonl"),
                "stderr_path": str(tmp_path / "workers" / "manual-task.stderr.log"),
            },
            None,
        )

    monkeypatch.setattr(codex_telegram_bot, "start_codex_worker", fake_start_worker)

    result = client.record_worker_tool_call(
        "codex_worker_start",
        {"task": "修一下 TG worker 策略", "title": "worker strategy"},
    )

    assert result["success"] is True
    text = result["contentItems"][0]["text"]
    assert "Codex worker started" in text
    assert "Supervisor alarm scheduled" in text
    assert started == [{"task": "修一下 TG worker 策略", "title": "worker strategy", "cwd": ""}]
    alarms = codex_telegram_bot.list_worker_alarms(cfg)
    assert len(alarms) == 1
    assert alarms[0]["task_id"] == "manual-task"
    assert alarms[0]["chat_id"] == "111"
    assert alarms[0]["message_thread_id"] == 9


def test_leave_chat_tool_requires_owner_private_context(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    client = codex_telegram_bot.CodexAppServerClient(cfg)
    called: list[dict[str, object]] = []

    def fake_telegram_api(token, method, params, timeout=35):
        called.append({"token": token, "method": method, "params": params, "timeout": timeout})
        return {"ok": True, "result": True}

    monkeypatch.setattr(codex_telegram_bot, "telegram_api", fake_telegram_api)

    result = client.record_dynamic_tool_call(
        {"tool": "leave_chat", "arguments": {"chat_id": "-100"}},
        [],
    )

    assert result["success"] is False
    assert "owner in private chat" in result["contentItems"][0]["text"]
    assert called == []


def test_owner_private_leave_chat_tool_leaves_and_updates_local_state(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    conn = _conn(tmp_path)
    codex_telegram_bot.upsert_chat(conn, codex_telegram_bot.Chat("-100", "supergroup", "Release Room"))
    assert codex_telegram_bot.get_chat(conn, "-100")["bot_active"] == 1
    cfg.access_file.write_text(
        json.dumps({"allowedChats": ["-100", "-200"], "allowedUsers": [], "allowedBots": []}),
        encoding="utf-8",
    )
    (tmp_path / "mention-toggle.json").write_text(
        json.dumps({"state": "smart", "mention_groups": ["-100", "-200"]}),
        encoding="utf-8",
    )
    called: list[dict[str, object]] = []

    def fake_telegram_api(token, method, params, timeout=35):
        called.append({"token": token, "method": method, "params": params, "timeout": timeout})
        return {"ok": True, "result": True}

    monkeypatch.setattr(codex_telegram_bot, "telegram_api", fake_telegram_api)
    client = codex_telegram_bot.CodexAppServerClient(cfg)
    client.current_turn_chat_id = "111"
    client.current_turn_owner_private = True

    result = client.record_dynamic_tool_call(
        {"tool": "leave_chat", "arguments": {"chat_id": "-100", "reason": "test"}},
        [],
    )

    assert result["success"] is True
    assert called == [{"token": cfg.token, "method": "leaveChat", "params": {"chat_id": "-100"}, "timeout": 35}]
    with codex_telegram_bot.closing(codex_telegram_bot.connect_db(cfg)) as check_conn:
        assert codex_telegram_bot.get_chat(check_conn, "-100")["bot_active"] == 0
    access = json.loads(cfg.access_file.read_text(encoding="utf-8"))
    mention = json.loads((tmp_path / "mention-toggle.json").read_text(encoding="utf-8"))
    assert access["allowedChats"] == ["-200"]
    assert mention["mention_groups"] == ["-200"]


def test_worker_process_cannot_start_telegram_channel_mcp(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_TELEGRAM_WORKER", "1")

    with pytest.raises(SystemExit) as exc:
        codex_telegram_bot.run_channel_mcp_server()

    assert "disabled inside Codex worker processes" in str(exc.value)


def test_refresh_worker_state_marks_missing_process_without_output_failed(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    state = {
        "version": codex_telegram_bot.WORKER_STATE_VERSION,
        "task_id": "stale-worker",
        "title": "stale worker",
        "status": "running",
        "pid": 0,
        "session_id": "",
        "cwd": str(ROOT),
        "model": cfg.model,
        "started_at": codex_telegram_bot.utc_now(),
        "finished_at": "",
        "turn_count": 1,
        "output_path": str(tmp_path / "workers" / "stale-worker.last.txt"),
        "jsonl_path": str(tmp_path / "workers" / "stale-worker.jsonl"),
        "stderr_path": str(tmp_path / "workers" / "stale-worker.stderr.log"),
    }
    codex_telegram_bot.write_worker_state(cfg, state)

    refreshed = codex_telegram_bot.refresh_worker_state(cfg, state)

    assert refreshed["status"] == "failed"
    assert refreshed["finished_at"]
    assert refreshed["error"] == "worker process ended before writing a final result"
    persisted = codex_telegram_bot.read_worker_state(cfg, "stale-worker")
    assert persisted is not None
    assert persisted["status"] == "failed"
    assert "worker process ended" in codex_telegram_bot.format_worker_state(refreshed)


def test_interrupted_background_run_notifies_chat(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    conn = _conn(tmp_path)
    service = codex_telegram_bot.BotService(cfg)
    run_id = "run-with-background-ack"
    codex_telegram_bot.create_run(
        conn,
        run_id,
        "-100",
        "thread-before",
        tmp_path / "prompt.txt",
        tmp_path / "reply.txt",
        tmp_path / "run.jsonl",
    )
    codex_telegram_bot.record_channel_delivery(
        conn,
        run_id,
        "-100",
        -2,
        501,
        None,
        12,
        "我还在处理，跑完回来给你结论。",
        event_type="background_ack",
    )
    rows = codex_telegram_bot.running_runs_with_background_ack(conn)
    sent: list[dict[str, object]] = []

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "message_thread_id": message_thread_id,
            }
        )
        return [777]

    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)

    assert codex_telegram_bot.mark_running_runs_interrupted(conn, "daemon restarted before run completed") == 1
    assert service.notify_interrupted_background_runs(conn, rows, "daemon restarted before run completed") == 1

    assert sent == [
        {
            "chat_id": "-100",
            "text": codex_telegram_bot.INTERRUPTED_BACKGROUND_NOTICE_TEXT,
            "reply_to_message_id": None,
            "message_thread_id": 12,
        }
    ]
    row = conn.execute(
        """
        SELECT event_type, telegram_message_id, delivery_status, message_thread_id
        FROM channel_deliveries
        WHERE run_id = ? AND event_type = 'interrupted_notice'
        """,
        (run_id,),
    ).fetchone()
    assert row["event_type"] == "interrupted_notice"
    assert row["telegram_message_id"] == 777
    assert row["delivery_status"] == "sent"
    assert row["message_thread_id"] == 12


def test_batch_direct_background_continues_silently_before_late_delivery(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(
        tmp_path,
        direct_background=True,
        direct_background_after_seconds=0.01,
        direct_background_timeout_seconds=60,
        auto_worker=False,
    )
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)
    sent: list[str] = []

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(text)
        return [len(sent)]

    def fake_run_batch(chat_arg, items_arg, revision_arg, *, run_id=None):
        time.sleep(0.1)
        return codex_telegram_bot.RunResult(
            run_id=run_id or "batch-run",
            status="ok",
            reply=codex_telegram_bot.NO_REPLY_SENTINEL,
            session_id_after="thread-1",
            error=None,
            channel_events=[{"type": "reply", "chat_id": "current", "text": "完成了"}],
        )

    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)
    monkeypatch.setattr(
        codex_telegram_bot,
        "start_typing_feedback",
        lambda *args, **kwargs: threading.Event(),
    )
    monkeypatch.setattr(service, "run_batch", fake_run_batch)
    with service.batch_lock:
        service.batches[chat.chat_id] = codex_telegram_bot.BatchState(
            chat=chat,
            items=[
                codex_telegram_bot.BatchItem(
                    message_id=77,
                    message_thread_id=None,
                    sender=sender,
                    text="做个大活",
                    explicitly_addressed=True,
                    created_at=codex_telegram_bot.utc_now(),
                )
            ],
            revision=1,
        )

    service.flush_batch(chat.chat_id, 1)

    deadline = time.monotonic() + 1
    while sent != ["完成了"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sent == ["完成了"]
    assert (
        conn.execute("SELECT COUNT(*) FROM channel_deliveries WHERE event_type = 'background_ack'").fetchone()[0]
        == 0
    )


def test_heavy_batch_enters_resident_instead_of_auto_worker(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path, direct_background=False, auto_worker=True)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)
    sent: list[str] = []
    prompts: list[str] = []

    def fake_start_worker(*args, **kwargs):
        raise AssertionError("bridge must not start a worker from addressed batch keywords")

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(text)
        return [len(sent)]

    def fake_run_codex(*args, **kwargs):
        prompts.append(args[4])
        return codex_telegram_bot.RunResult(
            run_id="batch-run",
            status="ok",
            reply=codex_telegram_bot.NO_REPLY_SENTINEL,
            session_id_after=args[3],
            error=None,
            channel_events=[{"type": "reply", "chat_id": "current", "text": "这几条我一起判断。"}],
        )

    monkeypatch.setattr(codex_telegram_bot, "start_codex_worker", fake_start_worker)
    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)
    monkeypatch.setattr(codex_telegram_bot, "run_codex", fake_run_codex)
    monkeypatch.setattr(
        codex_telegram_bot,
        "start_typing_feedback",
        lambda *args, **kwargs: threading.Event(),
    )
    with service.batch_lock:
        service.batches[chat.chat_id] = codex_telegram_bot.BatchState(
            chat=chat,
            items=[
                codex_telegram_bot.BatchItem(
                    message_id=77,
                    message_thread_id=None,
                    sender=sender,
                    text="你看看 TG worker 的日志，为什么不回消息了",
                    explicitly_addressed=True,
                    created_at=codex_telegram_bot.utc_now(),
                ),
                codex_telegram_bot.BatchItem(
                    message_id=78,
                    message_thread_id=None,
                    sender=sender,
                    text="顺手修一下代码",
                    explicitly_addressed=True,
                    created_at=codex_telegram_bot.utc_now(),
                ),
            ],
            revision=1,
        )

    service.flush_batch(chat.chat_id, 1)

    assert sent == ["这几条我一起判断。"]
    assert len(prompts) == 1
    assert 'message_id="77"' in prompts[0]
    assert 'message_id="78"' in prompts[0]
    assert codex_telegram_bot.list_worker_states(cfg) == []
    assert codex_telegram_bot.list_worker_alarms(cfg) == []


def test_batch_ok_result_delivers_when_newer_message_arrives(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path, direct_background=False)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)
    sent: list[str] = []

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(text)
        return [len(sent)]

    def fake_run_batch(chat_arg, items_arg, revision_arg):
        with service.batch_lock:
            state = service.batches[chat.chat_id]
            state.items.append(
                codex_telegram_bot.BatchItem(
                    message_id=78,
                    message_thread_id=None,
                    sender=sender,
                    text="newer message",
                    explicitly_addressed=True,
                    created_at=codex_telegram_bot.utc_now(),
                )
            )
            state.revision += 1
        return codex_telegram_bot.RunResult(
            run_id="batch-run",
            status="ok",
            reply=codex_telegram_bot.NO_REPLY_SENTINEL,
            session_id_after="thread-1",
            error=None,
            channel_events=[{"type": "reply", "chat_id": "current", "text": "完成了"}],
        )

    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)
    monkeypatch.setattr(
        codex_telegram_bot,
        "start_typing_feedback",
        lambda *args, **kwargs: threading.Event(),
    )
    monkeypatch.setattr(service, "run_batch", fake_run_batch)
    with service.batch_lock:
        service.batches[chat.chat_id] = codex_telegram_bot.BatchState(
            chat=chat,
            items=[
                codex_telegram_bot.BatchItem(
                    message_id=77,
                    message_thread_id=None,
                    sender=sender,
                    text="first message",
                    explicitly_addressed=True,
                    created_at=codex_telegram_bot.utc_now(),
                )
            ],
            revision=1,
        )

    service.flush_batch(chat.chat_id, 1)

    assert sent == ["完成了"]
    row = conn.execute(
        "SELECT delivery_status, telegram_message_id FROM channel_deliveries WHERE run_id = ?",
        ("batch-run",),
    ).fetchone()
    assert row["delivery_status"] == "sent"
    assert row["telegram_message_id"] == 1
    with service.batch_lock:
        state = service.batches[chat.chat_id]
        assert [item.message_id for item in state.items] == [78]
        if state.timer is not None:
            state.timer.cancel()


def test_public_sources_do_not_expose_private_prompt_names() -> None:
    checked_paths = [
        ROOT / "README.md",
        ROOT / "docs" / "CODEX_TELEGRAM_BOT.md",
        ROOT / "plans" / "codex-telegram-bot-plan.md",
        ROOT / "scripts" / "codex_telegram_bot.py",
        ROOT / "config" / "telegram.env.example",
        ROOT / "launchd" / "com.codex.telegram.plist",
    ]
    legacy_suffix = "x" + "u"
    forbidden = [
        f"telegram-{legacy_suffix}",
        f"com.codex.telegram-{legacy_suffix}",
        f"you are {legacy_suffix}",
        f"tg {legacy_suffix}",
    ]

    for path in checked_paths:
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for needle in forbidden:
            assert needle.lower() not in lowered, f"{needle!r} leaked in {path}"
