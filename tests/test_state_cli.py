"""CLI tests for SQLite-backed message state helpers."""

from __future__ import annotations

import json

from bin import shared, state, storage


def _seed_message(tmp_data_dir):
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        storage.store_message(
            conn,
            message_id="m1",
            kind="direct",
            from_session_id="s1",
            from_name="alpha",
            from_agent="codex",
            to_session_id="s2",
            to_name="beta",
            text="hello",
            created_at="2026-08-07T10:00:00+00:00",
            recipients=["s2"],
        )
    finally:
        conn.close()


def _write_session(tmp_data_dir, monkeypatch):
    monkeypatch.setenv("INTERAGENTS_PPID_OVERRIDE", "12345")
    shared.secure_dir(shared.clients_dir())
    shared.client_session_path(12345).write_text(json.dumps({
        "session_id": "s2",
        "name": "beta",
        "nonce": "n",
        "listener_pid": 999999,
    }))


def test_get_message_prints_untrusted_json(tmp_data_dir, capsys):
    _seed_message(tmp_data_dir)

    code = state.main_from_args(["get-message", "m1"])

    out = capsys.readouterr().out
    assert code == 0
    assert '"id": "m1"' in out
    assert "UNTRUSTED PEER CONTENT" in out


def test_mark_read_updates_current_session_delivery(tmp_data_dir, monkeypatch, capsys):
    _seed_message(tmp_data_dir)
    _write_session(tmp_data_dir, monkeypatch)

    code = state.main_from_args(["mark-read", "m1"])

    assert code == 0
    assert "marked read" in capsys.readouterr().out
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        row = conn.execute("select * from message_deliveries").fetchone()
    finally:
        conn.close()
    assert row["delivery_state"] == "read"
    assert row["read_at"] is not None


def test_mark_failed_records_reason(tmp_data_dir, monkeypatch, capsys):
    _seed_message(tmp_data_dir)
    _write_session(tmp_data_dir, monkeypatch)

    code = state.main_from_args(["mark-failed", "m1", "--reason", "unsupported"])

    assert code == 0
    assert "marked failed" in capsys.readouterr().out
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        row = conn.execute("select * from message_deliveries").fetchone()
    finally:
        conn.close()
    assert row["delivery_state"] == "read"
    assert row["disposition"] == "failed"
    assert row["failure_reason"] == "unsupported"


def test_export_cli_is_redacted_by_default(tmp_data_dir, capsys):
    _seed_message(tmp_data_dir)

    code = state.main_from_args(["export", "--table", "messages"])

    assert code == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["messages"][0]["id"] == "m1"
    assert exported["messages"][0]["text"] == storage.REDACTED_MESSAGE_TEXT


def test_export_cli_can_include_text_while_runtime_sqlite_is_off(
    tmp_data_dir, monkeypatch, capsys,
):
    _seed_message(tmp_data_dir)
    monkeypatch.setenv("INTERAGENTS_SQLITE_ENABLED", "false")

    code = state.main_from_args([
        "export", "--table", "messages", "--include-text", "--limit", "1",
    ])

    assert code == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["messages"][0]["text"] == "hello"
