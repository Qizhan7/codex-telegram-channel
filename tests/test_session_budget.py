import json
import sqlite3
import threading

import session_budget as budget
from test_codex_telegram_bot import _config, _conn, codex_telegram_bot as bridge


def item(sid, identity, text, kind="userMessage", method="item/completed"):
    return {"method": method, "params": {"threadId": sid, "item": {
        "id": identity, "type": kind, "content": [{"type": "text", "text": text}],
    }}}


def usage(sid, turn, inputs, outputs, cached=0):
    return {"method": "thread/tokenUsage/updated", "params": {"threadId": sid, "turnId": turn,
        "tokenUsage": {"total": {"inputTokens": inputs, "outputTokens": outputs},
                       "last": {"inputTokens": inputs, "outputTokens": outputs, "cachedInputTokens": cached}}}}


def test_new_content_does_not_count_history_or_cache_misses(tmp_path):
    conn = _conn(tmp_path)
    a = item("s", "a", "新消息")
    budget.record(conn, a)
    budget.record(conn, a)
    budget.record(conn, {"result": {"thread": {"turns": [a]}}})
    budget.record(conn, usage("s", "t1", 200000, 10, 190000))
    budget.record(conn, usage("s", "t1", 200000, 10, 0))
    budget.record(conn, usage("s", "t2", 400000, 15, 0))
    assert budget.stats(conn, "s")["new_content_tokens"] == budget.text_tokens("新消息") + 15


def test_compaction_success_failure_and_replay(tmp_path):
    conn = _conn(tmp_path)
    budget.record(conn, usage("s", "t1", 200000, 10))
    budget.record(conn, item("s", "c1", "", "contextCompaction", "item/started"))
    budget.record(conn, usage("s", "compact", 400000, 210))
    budget.record(conn, item("s", "c1", "", "contextCompaction"))
    budget.record(conn, item("s", "c1", "", "contextCompaction"))
    budget.record(conn, item("s", "c2", "", "contextCompaction", "item/started"))
    budget.record(conn, {"method": "turn/completed", "params": {"threadId": "s", "turn": {"status": "failed"}}})
    budget.record(conn, usage("s", "t2", 420000, 220))
    assert budget.stats(conn, "s") == {"new_input_tokens": 0, "new_output_tokens": 20, "new_content_tokens": 20, "compactions": 1}


def test_ingest_survives_restart_and_partial_line(tmp_path):
    conn = _conn(tmp_path)
    path = tmp_path / "turn.jsonl"
    record = json.dumps(item("s", "a", "hello"))
    path.write_text(record + "\n" + record[:20])
    budget.ingest(conn, path)
    first = budget.stats(conn, "s")
    conn.close()
    conn = _conn(tmp_path)
    path.write_text(record + "\n" + record + "\n")
    budget.ingest(conn, path)
    assert budget.stats(conn, "s") == first
    assert budget.text_tokens({"type": "image", "data": "x" * 100000, "url": "data:image/png;base64," + "x" * 100000}) == 0


def test_text_tokens_estimates_without_tiktoken_encoding(monkeypatch):
    monkeypatch.setattr(budget, "_encoding", None)
    monkeypatch.setattr(budget, "_encoding_unavailable", True)
    assert budget.text_tokens("abcd") == 1
    assert budget.text_tokens([{"type": "text", "text": "新消息"}]) == 3
    assert budget.text_tokens({"data": "x" * 100}) == 0


class Client:
    def __init__(self):
        self.quiet = True
        self.fail_start = False
        self.summaries = 0
        self.starts = 0
        self.released = []

    def thread_is_quiescent(self, old):
        return self.quiet

    def summarize_for_handover(self, old, evidence):
        self.summaries += 1
        return "私聊：约定明天验收。未完成：等用户确认图片。"

    def start_handover_thread(self):
        self.starts += 1
        if self.fail_start:
            raise RuntimeError("start failed")
        return "new"

    def release_handover_thread(self, old):
        self.released.append(old)


def setup_window(tmp_path):
    conn = _conn(tmp_path)
    cfg = _config(tmp_path, rollover_new_content_tokens=5)
    bridge.upsert_chat(conn, bridge.Chat("111", "private", "Owner"))
    bridge.set_session_for_config(conn, "111", "old", cfg)
    budget.record(conn, item("old", "a", "这是一段足够长的新消息。"))
    conn.commit()
    return conn, cfg, bridge.get_chat(conn, "111")


def test_rollover_waits_for_tools_and_preserves_summary_on_failure(tmp_path):
    conn, cfg, row = setup_window(tmp_path)
    client = Client()
    client.quiet = False
    assert bridge.prepare_session_for_turn(conn, cfg, row, client) == "old"
    assert client.summaries == 0
    client.quiet = True
    client.fail_start = True
    assert bridge.prepare_session_for_turn(conn, cfg, row, client) == "old"
    assert bridge.shared_session_for_engine(conn, cfg.engine) == "old"
    client.fail_start = False
    conn.execute("UPDATE session_handovers SET updated_at='2000-01-01T00:00:00Z'")
    conn.commit()
    assert bridge.prepare_session_for_turn(conn, cfg, row, client) == "new"
    assert client.summaries == 1
    assert "未完成" in bridge.shared_handoff_for_engine(conn, cfg.engine)
    assert client.released == ["old"]


def test_prepared_candidate_survives_restart_without_another_new_window(tmp_path):
    conn, cfg, row = setup_window(tmp_path)
    conn.execute("INSERT INTO session_handovers VALUES ('old','budget','已有交接','prepared-new','prepared','2000-01-01T00:00:00Z')")
    conn.commit()
    client = Client()
    assert bridge.prepare_session_for_turn(conn, cfg, row, client) == "prepared-new"
    assert client.starts == client.summaries == 0


def test_bad_old_chat_mapping_is_not_resurrected_and_new_is_saved_before_inference(tmp_path):
    conn, cfg, row = setup_window(tmp_path)
    bridge.persist_ready_shared_thread(conn, cfg, "111", "old", "new")
    assert bridge.shared_session_for_engine(conn, cfg.engine) == "new"
    conn.execute("DELETE FROM meta WHERE key=?", (bridge.shared_session_meta_key(cfg.engine),))
    conn.commit()
    assert bridge.session_for_engine(conn, row, cfg) is None


def test_large_context_alone_does_not_trigger_budget_rollover(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    cfg = _config(tmp_path)
    monkeypatch.setattr(bridge, "latest_session_token_usage", lambda *a: {"last_input_tokens": 250000})
    assert not bridge.should_rollover_shared_session(conn, cfg, "s")[0]


def test_app_server_run_never_rewrites_native_rollout(tmp_path, monkeypatch):
    cfg = _config(tmp_path, engine="app-server", desktop_sync=True)
    home = tmp_path / "codex-home"
    home.mkdir()
    rollout = tmp_path / "rollout.jsonl"
    original = b'{"ordinal":8,"type":"response_item","payload":{}}\n'
    rollout.write_bytes(original)
    with sqlite3.connect(home / "state_5.sqlite") as state:
        state.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)")
        state.execute("INSERT INTO threads(id, rollout_path) VALUES(?, ?)", ("thread", str(rollout)))
    monkeypatch.setattr(bridge, "codex_home", lambda: home)

    class FakeAppServer:
        def run_turn(self, *args, **kwargs):
            return "thread", "", None, [], args[1]

    prompt = '<channel source="telegram" chat_id="111" user="Owner">\nhello\n</channel>'
    with bridge.closing(bridge.connect_db(cfg)) as conn:
        result = bridge.run_codex_app_server(
            conn, cfg, FakeAppServer(), "111", "thread", prompt, 9,
            desktop_title="Telegram Codex - Owner", desktop_preview="hello", run_id="native-rollout",
        )
    for thread in threading.enumerate():
        if thread.name == "desktop-finalize-native-rollout":
            thread.join(1)

    assert result.status == "ok"
    assert rollout.read_bytes() == original


def test_existing_database_is_migrated_without_losing_session(tmp_path):
    conn, cfg, _ = setup_window(tmp_path)
    for name in ("session_content_events", "session_content_state", "session_content_logs", "session_handovers"):
        conn.execute(f"DROP TABLE {name}")
    conn.commit()
    assert not bridge.db_schema_ready(conn)
    conn.close()
    conn = bridge.connect_db(cfg)
    assert bridge.db_schema_ready(conn)
    assert bridge.shared_session_for_engine(conn, cfg.engine) == "old"
    assert budget.stats(conn, "old")["new_content_tokens"] == 0
