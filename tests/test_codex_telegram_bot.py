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
    assert cfg.auto_worker is True
    assert cfg.auto_worker_check_seconds == codex_telegram_bot.DEFAULT_AUTO_WORKER_CHECK_SECONDS
    assert cfg.auto_worker_result_chars == codex_telegram_bot.DEFAULT_AUTO_WORKER_RESULT_CHARS
    assert cfg.wake_phrases == ("codex", "assistant", "bot")


def test_init_config_writes_public_wake_phrases(tmp_path: Path) -> None:
    codex_telegram_bot.init_config(tmp_path)

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CODEX_TELEGRAM_WAKE_PHRASES=codex,assistant,bot" in env_text
    assert "CODEX_TELEGRAM_DESKTOP_SYNC=1" in env_text
    assert "CODEX_TELEGRAM_DESKTOP_OUTBOUND=1" in env_text
    assert "CODEX_TELEGRAM_DIRECT_BACKGROUND=1" in env_text
    assert "CODEX_TELEGRAM_AUTO_WORKER=1" in env_text


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


def test_group_name_call_from_unlisted_sender_enters_model_decide(tmp_path: Path) -> None:
    cfg = _config(tmp_path, wake_phrases=("序", "小序"), group_decision_source="model")
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    codex_telegram_bot.upsert_chat(conn, chat)
    chat_row = codex_telegram_bot.get_chat(conn, chat.chat_id)
    service = codex_telegram_bot.BotService(cfg)
    sender = codex_telegram_bot.Sender("222", "Friend", False)
    policy = _policy()

    assert service.should_call_codex(
        conn,
        chat,
        chat_row,
        sender,
        "序你看看这个",
        {"message_id": 10, "text": "序你看看这个"},
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


def test_group_prompt_includes_last_five_same_chat_messages_before_trigger(tmp_path: Path) -> None:
    cfg = _config(tmp_path, wake_phrases=("序",), group_decision_source="model")
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
    codex_telegram_bot.store_new_message(conn, 7, chat.chat_id, sender, "序 当前消息")

    prompt = codex_telegram_bot.build_prompt(conn, chat, sender, 7, "序 当前消息", cfg, allow_silent_reply=True)
    start = prompt.index("<recent_chat_window")
    end = prompt.index("</recent_chat_window>")
    recent_block = prompt[start:end]

    assert "history message 1" not in recent_block
    for message_id in range(2, 7):
        assert f"history message {message_id}" in recent_block
    assert "序 当前消息" not in recent_block


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


def test_direct_background_ack_unblocks_and_delivers_later(tmp_path: Path, monkeypatch) -> None:
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
    run_ids: list[str | None] = []

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
    ):
        timeouts.append(timeout_seconds)
        run_ids.append(run_id)
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
    expected_ack = codex_telegram_bot.direct_background_ack_text("做个大活")
    assert sent[0]["text"] == expected_ack
    assert sent[0]["text"] != codex_telegram_bot.DIRECT_BACKGROUND_ACK_TEXT
    assert timeouts == [60]

    deadline = time.monotonic() + 1
    while len(sent) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [item["text"] for item in sent] == [
        expected_ack,
        "完成了",
    ]
    ack_row = conn.execute(
        "SELECT run_id FROM channel_deliveries WHERE event_type = 'background_ack'"
    ).fetchone()
    assert ack_row["run_id"] == run_ids[0]

    lock = service.lock_for_chat(chat.chat_id)
    deadline = time.monotonic() + 1
    acquired = lock.acquire(blocking=False)
    while not acquired and time.monotonic() < deadline:
        time.sleep(0.01)
        acquired = lock.acquire(blocking=False)
    assert acquired
    lock.release()


def test_auto_worker_delegates_heavy_single_without_shared_codex(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path, direct_background=False)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)
    started: list[dict[str, str]] = []
    sent: list[str] = []

    def fake_start_worker(config, *, task, title="", cwd="", task_id=None, session_id=None, turn_count=1):
        started.append({"task": task, "title": title})
        return (
            {
                "version": codex_telegram_bot.WORKER_STATE_VERSION,
                "task_id": "auto-task",
                "title": title,
                "status": "running",
                "pid": 123,
                "session_id": "",
                "cwd": str(config.cwd),
                "model": config.model,
                "started_at": codex_telegram_bot.utc_now(),
                "finished_at": "",
                "turn_count": turn_count,
                "output_path": str(tmp_path / "workers" / "auto-task.last.txt"),
                "jsonl_path": str(tmp_path / "workers" / "auto-task.jsonl"),
                "stderr_path": str(tmp_path / "workers" / "auto-task.stderr.log"),
            },
            None,
        )

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(text)
        return [len(sent)]

    def fake_run_codex(*args, **kwargs):
        raise AssertionError("shared Codex thread should not run for heavy auto-worker tasks")

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

    assert len(started) == 1
    assert sent == [
        codex_telegram_bot.auto_worker_ack_text("debugging or runtime inspection task", started[0]["title"])
    ]
    assert sent[0] != codex_telegram_bot.AUTO_WORKER_ACK_TEXT
    assert "Telegram auto-worker task" in started[0]["task"]
    assert "TG 日志" in started[0]["task"]
    state = codex_telegram_bot.read_worker_state(cfg, "auto-task")
    assert state is not None
    assert state["auto_delivery"]["status"] == "supervised"
    assert state["auto_delivery"]["chat_id"] == "111"
    assert state["auto_delivery"]["alarm_id"]
    alarms = codex_telegram_bot.list_worker_alarms(cfg)
    assert len(alarms) == 1
    assert alarms[0]["task_id"] == "auto-task"
    assert alarms[0]["chat_id"] == "111"


def test_auto_worker_delegates_multi_step_single_task(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path, direct_background=False)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)
    started: list[dict[str, str]] = []
    sent: list[str] = []

    def fake_start_worker(config, *, task, title="", cwd="", task_id=None, session_id=None, turn_count=1):
        started.append({"task": task, "title": title})
        return (
            {
                "version": codex_telegram_bot.WORKER_STATE_VERSION,
                "task_id": "multi-step-task",
                "title": title,
                "status": "running",
                "pid": 123,
                "session_id": "",
                "cwd": str(config.cwd),
                "model": config.model,
                "started_at": codex_telegram_bot.utc_now(),
                "finished_at": "",
                "turn_count": turn_count,
                "output_path": str(tmp_path / "workers" / "multi-step-task.last.txt"),
                "jsonl_path": str(tmp_path / "workers" / "multi-step-task.jsonl"),
                "stderr_path": str(tmp_path / "workers" / "multi-step-task.stderr.log"),
            },
            None,
        )

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(text)
        return [len(sent)]

    def fake_run_codex(*args, **kwargs):
        raise AssertionError("multi-step execution tasks should be delegated")

    monkeypatch.setattr(codex_telegram_bot, "start_codex_worker", fake_start_worker)
    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)
    monkeypatch.setattr(codex_telegram_bot, "run_codex", fake_run_codex)

    service.run_single_message(
        conn,
        chat,
        sender,
        44,
        None,
        "先查现在逻辑，然后修掉问题，再跑验证",
        "先查现在逻辑，然后修掉问题，再跑验证",
        False,
        True,
    )

    assert len(started) == 1
    assert "reason: multi-step execution task" in started[0]["task"]
    assert sent == [codex_telegram_bot.auto_worker_ack_text("multi-step execution task", started[0]["title"])]


def test_auto_worker_delegates_investigation_task_without_file_hint(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path, direct_background=False)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)
    started: list[dict[str, str]] = []
    sent: list[str] = []

    def fake_start_worker(config, *, task, title="", cwd="", task_id=None, session_id=None, turn_count=1):
        started.append({"task": task, "title": title})
        return (
            {
                "version": codex_telegram_bot.WORKER_STATE_VERSION,
                "task_id": "investigation-task",
                "title": title,
                "status": "running",
                "pid": 123,
                "session_id": "",
                "cwd": str(config.cwd),
                "model": config.model,
                "started_at": codex_telegram_bot.utc_now(),
                "finished_at": "",
                "turn_count": turn_count,
                "output_path": str(tmp_path / "workers" / "investigation-task.last.txt"),
                "jsonl_path": str(tmp_path / "workers" / "investigation-task.jsonl"),
                "stderr_path": str(tmp_path / "workers" / "investigation-task.stderr.log"),
            },
            None,
        )

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(text)
        return [len(sent)]

    def fake_run_codex(*args, **kwargs):
        raise AssertionError("investigation tasks should be delegated")

    monkeypatch.setattr(codex_telegram_bot, "start_codex_worker", fake_start_worker)
    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)
    monkeypatch.setattr(codex_telegram_bot, "run_codex", fake_run_codex)

    prompt = "查一下为什么刚才那个群设置没生效"
    service.run_single_message(conn, chat, sender, 47, None, prompt, prompt, False, True)

    assert len(started) == 1
    assert "reason: execution or investigation task" in started[0]["task"]
    assert sent == [codex_telegram_bot.auto_worker_ack_text("execution or investigation task", started[0]["title"])]


def test_existing_worker_context_lets_resident_choose_continue_or_new_worker(tmp_path: Path) -> None:
    cfg = _config(tmp_path, direct_background=False)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)
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
    decision = service.auto_worker_decision_for_single(
        chat,
        sender,
        48,
        None,
        "查一下为什么刚才那个设置没生效",
        "查一下为什么刚才那个设置没生效",
        allow_silent_reply=False,
        explicitly_addressed=True,
    )

    assert decision is None
    assert '<worker_context purpose="telegram resident routing">' in prompt
    assert "task_id=active-task" in prompt
    assert "codex_worker_continue" in prompt
    assert "start a new worker when it is a separate task" in prompt


def test_auto_worker_delegates_group_setting_task(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path, direct_background=False)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)
    started: list[dict[str, str]] = []
    sent: list[str] = []

    def fake_start_worker(config, *, task, title="", cwd="", task_id=None, session_id=None, turn_count=1):
        started.append({"task": task, "title": title})
        return (
            {
                "version": codex_telegram_bot.WORKER_STATE_VERSION,
                "task_id": "group-setting-task",
                "title": title,
                "status": "running",
                "pid": 123,
                "session_id": "",
                "cwd": str(config.cwd),
                "model": config.model,
                "started_at": codex_telegram_bot.utc_now(),
                "finished_at": "",
                "turn_count": turn_count,
                "output_path": str(tmp_path / "workers" / "group-setting-task.last.txt"),
                "jsonl_path": str(tmp_path / "workers" / "group-setting-task.jsonl"),
                "stderr_path": str(tmp_path / "workers" / "group-setting-task.stderr.log"),
            },
            None,
        )

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(text)
        return [len(sent)]

    def fake_run_codex(*args, **kwargs):
        raise AssertionError("group setting implementation should be delegated")

    monkeypatch.setattr(codex_telegram_bot, "start_codex_worker", fake_start_worker)
    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)
    monkeypatch.setattr(codex_telegram_bot, "run_codex", fake_run_codex)

    prompt = "我想给你加到新群里，把这个群改成只有艾特你才说话，按群 id 设置。"
    service.run_single_message(conn, chat, sender, 45, None, prompt, prompt, False, True)

    assert len(started) == 1
    assert "Telegram auto-worker task" in started[0]["task"]
    assert "新群" in started[0]["task"]
    assert sent == [
        codex_telegram_bot.auto_worker_ack_text("debugging or runtime inspection task", started[0]["title"])
    ]


def test_auto_worker_keeps_light_small_edit_in_shared_thread(tmp_path: Path, monkeypatch) -> None:
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


def test_busy_lock_allows_parallel_setup_question(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path, direct_background=False)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("111", "private", "Owner")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)
    lock = service.lock_for_chat(chat.chat_id)
    assert lock.acquire(blocking=False)
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
        return [len(sent)]

    def fake_run_codex(*args, **kwargs):
        raise AssertionError("busy fallback should not start a second shared Codex turn")

    monkeypatch.setattr(codex_telegram_bot, "send_message", fake_send_message)
    monkeypatch.setattr(codex_telegram_bot, "run_codex", fake_run_codex)
    try:
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
    finally:
        lock.release()

    assert sent == [
        {
            "chat_id": "111",
            "text": "可以，先把我加进去。上一条我这边继续跑着；加好以后继续说，我会接上。",
            "reply_to_message_id": 46,
            "message_thread_id": None,
        }
    ]


def test_legacy_auto_worker_delivery_schedules_supervisor_alarm(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path, auto_worker_result_chars=1000)
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


def test_worker_process_cannot_start_telegram_channel_mcp(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_TELEGRAM_WORKER", "1")

    with pytest.raises(SystemExit) as exc:
        codex_telegram_bot.run_channel_mcp_server()

    assert "disabled inside Codex worker processes" in str(exc.value)


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
        codex_telegram_bot.DIRECT_BACKGROUND_ACK_TEXT,
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


def test_batch_direct_background_ack_fires_before_late_delivery(tmp_path: Path, monkeypatch) -> None:
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
    run_ids: list[str | None] = []

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(text)
        return [len(sent)]

    def fake_run_batch(chat_arg, items_arg, revision_arg, *, run_id=None):
        run_ids.append(run_id)
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

    assert sent == [codex_telegram_bot.direct_background_ack_text("做个大活"), "完成了"]
    ack_row = conn.execute(
        "SELECT run_id FROM channel_deliveries WHERE event_type = 'background_ack'"
    ).fetchone()
    assert ack_row["run_id"] == run_ids[0]


def test_auto_worker_delegates_heavy_batch_without_shared_codex(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path, direct_background=False)
    conn = _conn(tmp_path)
    chat = codex_telegram_bot.Chat("-100", "supergroup", "Release Room")
    sender = codex_telegram_bot.Sender("111", "Owner", False)
    codex_telegram_bot.upsert_chat(conn, chat)
    service = codex_telegram_bot.BotService(cfg)
    sent: list[str] = []
    started: list[str] = []

    def fake_start_worker(config, *, task, title="", cwd="", task_id=None, session_id=None, turn_count=1):
        started.append(task)
        return (
            {
                "version": codex_telegram_bot.WORKER_STATE_VERSION,
                "task_id": "batch-auto-task",
                "title": title,
                "status": "running",
                "pid": 123,
                "session_id": "",
                "cwd": str(config.cwd),
                "model": config.model,
                "started_at": codex_telegram_bot.utc_now(),
                "finished_at": "",
                "turn_count": turn_count,
                "output_path": str(tmp_path / "workers" / "batch-auto-task.last.txt"),
                "jsonl_path": str(tmp_path / "workers" / "batch-auto-task.jsonl"),
                "stderr_path": str(tmp_path / "workers" / "batch-auto-task.stderr.log"),
            },
            None,
        )

    def fake_send_message(config, chat_id, text, *, reply_to_message_id=None, message_thread_id=None):
        sent.append(text)
        return [len(sent)]

    def fake_run_codex(*args, **kwargs):
        raise AssertionError("heavy batches should be delegated before shared Codex runs")

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

    assert sent == [codex_telegram_bot.auto_worker_ack_text("multi-message task bundle", "")]
    assert len(started) == 1
    assert "reason: multi-message task bundle" in started[0]
    assert "message_id=77" in started[0]
    assert "message_id=78" in started[0]


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
