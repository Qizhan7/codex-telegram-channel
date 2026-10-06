"""Persistent maintenance budget for newly appended conversation content.

Text uses o200k_base; generation uses reported output tokens (including reasoning).
This is a content maintenance budget, not billed input or context-window usage.
Replayed history, cache changes, compaction output and base64 media are not text.

`tiktoken` is optional. Without it, or when its encoding file cannot be loaded,
text is estimated at four UTF-8 bytes per token.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

try:
    import tiktoken
except ImportError:
    tiktoken = None

_encoding: Any = None
_encoding_unavailable = tiktoken is None


def init(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS session_content_events (
            session_id TEXT NOT NULL, event_key TEXT NOT NULL,
            kind TEXT NOT NULL, tokens INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(session_id, event_key)
        );
        CREATE TABLE IF NOT EXISTS session_content_state (
            session_id TEXT PRIMARY KEY, output_total INTEGER NOT NULL DEFAULT 0,
            compacting INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS session_content_logs (
            path TEXT PRIMARY KEY, offset INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS session_handovers (
            old_session_id TEXT PRIMARY KEY, reason TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '', new_session_id TEXT,
            status TEXT NOT NULL, updated_at TEXT NOT NULL
        );
    """)


def count_text_tokens(text: str) -> int:
    global _encoding, _encoding_unavailable
    if _encoding is None and not _encoding_unavailable:
        try:
            _encoding = tiktoken.get_encoding("o200k_base")
        except Exception:
            # Offline first use cannot download the encoding; do not retry per message.
            _encoding_unavailable = True
    if _encoding is not None:
        return len(_encoding.encode(text, disallowed_special=()))
    return (len(text.encode("utf-8")) + 3) // 4


def text_tokens(value: object) -> int:
    def strings(obj: object):
        if isinstance(obj, str):
            if not obj.startswith("data:"):
                yield obj
        elif isinstance(obj, list):
            for child in obj:
                yield from strings(child)
        elif isinstance(obj, dict):
            for key, child in obj.items():
                if key not in {"data", "url", "image_url", "mimeType", "type"}:
                    yield from strings(child)
    return count_text_tokens("\n".join(strings(value)))


def record(conn: sqlite3.Connection, obj: dict) -> None:
    params = obj.get("params") or {}
    sid = params.get("threadId")
    if not sid:
        return
    method = obj.get("method")
    item = params.get("item") or {}
    kind = item.get("type")
    conn.execute("INSERT OR IGNORE INTO session_content_state(session_id) VALUES (?)", (sid,))
    state = conn.execute("SELECT output_total, compacting FROM session_content_state WHERE session_id=?", (sid,)).fetchone()
    if kind == "contextCompaction":
        if method == "item/started":
            conn.execute("UPDATE session_content_state SET compacting=1 WHERE session_id=?", (sid,))
        elif method == "item/completed":
            conn.execute("INSERT OR IGNORE INTO session_content_events VALUES (?, ?, 'compaction', 0)",
                         (sid, "compaction:" + str(item["id"])))
            conn.execute("UPDATE session_content_state SET compacting=0 WHERE session_id=?", (sid,))
        return
    if method == "turn/completed":
        conn.execute("UPDATE session_content_state SET compacting=0 WHERE session_id=?", (sid,))
    if method == "thread/tokenUsage/updated":
        usage = params.get("tokenUsage") or {}
        total = usage.get("total") or {}
        last = usage.get("last") or {}
        raw = json.dumps([params.get("turnId"), total, last], sort_keys=True)
        key = "usage:" + hashlib.sha256(raw.encode()).hexdigest()
        if conn.execute("SELECT 1 FROM session_content_events WHERE session_id=? AND event_key=?", (sid, key)).fetchone():
            return
        new_total = int(total.get("outputTokens") or 0)
        delta = new_total - int(state[0])
        if delta < 0:
            delta = int(last.get("outputTokens") or 0)
        conn.execute("INSERT INTO session_content_events VALUES (?, ?, 'output', ?)",
                     (sid, key, 0 if state[1] else delta))
        conn.execute("UPDATE session_content_state SET output_total=? WHERE session_id=?", (new_total, sid))
        return
    if method != "item/completed" or state[1] or not item.get("id"):
        return
    fields = {
        "userMessage": "content", "functionCallOutput": "output",
        "commandExecution": "aggregatedOutput", "mcpToolCall": "result",
        "dynamicToolCall": "contentItems", "webSearch": "action",
    }
    if kind not in fields:
        return
    conn.execute("INSERT OR IGNORE INTO session_content_events VALUES (?, ?, 'input', ?)",
                 (sid, "item:" + item["id"], text_tokens(item.get(fields[kind]))))


def ingest(conn: sqlite3.Connection, path: Path) -> None:
    if not path.exists():
        return
    row = conn.execute("SELECT offset FROM session_content_logs WHERE path=?", (str(path),)).fetchone()
    offset = int(row[0]) if row else 0
    if offset > path.stat().st_size:
        offset = 0
    with path.open("rb") as handle:
        handle.seek(offset)
        while True:
            line = handle.readline()
            if not line or not line.endswith(b"\n"):
                break
            offset = handle.tell()
            try:
                obj = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(obj, dict):
                record(conn, obj)
    conn.execute("INSERT INTO session_content_logs VALUES (?, ?) ON CONFLICT(path) DO UPDATE SET offset=excluded.offset", (str(path), offset))
    conn.commit()


def stats(conn: sqlite3.Connection, sid: str) -> dict[str, int]:
    values = {str(row[0]): int(row[1]) for row in conn.execute(
        "SELECT kind, CASE WHEN kind='compaction' THEN COUNT(*) ELSE SUM(tokens) END "
        "FROM session_content_events WHERE session_id=? GROUP BY kind", (sid,)
    )}
    return {"new_input_tokens": values.get("input", 0), "new_output_tokens": values.get("output", 0),
            "new_content_tokens": values.get("input", 0) + values.get("output", 0),
            "compactions": values.get("compaction", 0)}
