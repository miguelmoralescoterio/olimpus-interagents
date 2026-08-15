"""SQLite storage tests."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from bin import storage

REPO = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO / "skills" / "interagents"


def test_migrate_initializes_schema(tmp_data_dir):
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")

    try:
        version = conn.execute("pragma user_version").fetchone()[0]
        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            )
        }
        indexes = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'index'"
            )
        }
    finally:
        conn.close()

    assert version == storage.SCHEMA_VERSION
    assert {"sessions", "messages", "message_deliveries"} <= tables
    assert {
        "idx_deliveries_drain",
        "idx_messages_thread",
        "idx_messages_reply",
    } <= indexes


def test_upsert_session_records_agent_capabilities_and_status(tmp_data_dir):
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        storage.upsert_session(
            conn,
            session_id="s1",
            name="codex-main",
            label="codex",
            cwd="/work/project",
            pid=123,
            parent_pid=100,
            container_pid=99,
            container_kind="cursor",
            container_title="",
            connected_at="2026-08-07T10:00:00+00:00",
        )
        row = conn.execute("select * from sessions where id = 's1'").fetchone()
    finally:
        conn.close()

    assert row["name"] == "codex-main"
    assert row["agent"] == "codex"
    assert row["project_name"] == "project"
    assert row["parent_pid"] == 100
    assert row["container_pid"] == 99
    assert row["container_kind"] == "cursor"
    assert row["receive_strategy"] == "pre_turn_hook"
    assert row["status"] == "active"
    assert '"mcp_tools": true' in row["capabilities_json"]


def test_mark_session_disconnected(tmp_data_dir):
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        storage.upsert_session(
            conn,
            session_id="s1",
            name="claude-main",
            label="claude",
            cwd="/work/project",
            pid=123,
            connected_at="2026-08-07T10:00:00+00:00",
        )
        storage.mark_session_disconnected(
            conn,
            session_id="s1",
            disconnected_at="2026-08-07T10:01:00+00:00",
        )
        row = conn.execute("select status, disconnected_at from sessions where id = 's1'").fetchone()
    finally:
        conn.close()

    assert row["status"] == "disconnected"
    assert row["disconnected_at"] == "2026-08-07T10:01:00+00:00"


def test_touch_session_updates_last_seen(tmp_data_dir):
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        storage.upsert_session(
            conn,
            session_id="s1",
            name="codex-main",
            label="codex",
            cwd="/work/project",
            pid=123,
            connected_at="2026-08-07T10:00:00+00:00",
        )
        assert storage.touch_session(
            conn,
            session_id="s1",
            seen_at="2026-08-07T10:01:00+00:00",
        )
        row = conn.execute("select last_seen_at from sessions where id = 's1'").fetchone()
    finally:
        conn.close()

    assert row["last_seen_at"] == "2026-08-07T10:01:00+00:00"


def test_mark_stale_sessions_uses_pid_liveness_and_grace(tmp_data_dir, monkeypatch):
    monkeypatch.setattr(storage.shared, "safe_pid_alive", lambda pid: False)
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        storage.upsert_session(
            conn,
            session_id="old",
            name="old",
            label="codex",
            cwd="/work/project",
            pid=999999,
            connected_at="2026-08-07T10:00:00+00:00",
            last_seen_at="2026-08-07T10:00:00+00:00",
        )
        storage.upsert_session(
            conn,
            session_id="fresh",
            name="fresh",
            label="codex",
            cwd="/work/project",
            pid=999998,
            connected_at="2026-08-07T10:00:55+00:00",
            last_seen_at="2026-08-07T10:00:55+00:00",
        )

        count = storage.mark_stale_sessions(
            conn,
            checked_at="2026-08-07T10:01:00+00:00",
            grace_seconds=30,
        )
        statuses = {
            row["id"]: row["status"]
            for row in conn.execute("select id, status from sessions")
        }
    finally:
        conn.close()

    assert count == 1
    assert statuses["old"] == "stale"
    assert statuses["fresh"] == "active"


def test_store_direct_message_creates_one_delivery(tmp_data_dir):
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
        msg = conn.execute("select * from messages where id = 'm1'").fetchone()
        deliveries = list(conn.execute("select * from message_deliveries"))
    finally:
        conn.close()

    assert msg["scope"] == "direct"
    assert msg["to_session_id"] == "s2"
    assert len(deliveries) == 1
    assert deliveries[0]["session_id"] == "s2"
    assert deliveries[0]["delivery_state"] == "pending"
    assert deliveries[0]["disposition"] == "none"


def test_store_broadcast_message_creates_per_recipient_deliveries(tmp_data_dir):
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        storage.store_message(
            conn,
            message_id="m1",
            kind="broadcast",
            from_session_id="s1",
            from_name="alpha",
            from_agent="codex",
            text="hello all",
            created_at="2026-08-07T10:00:00+00:00",
            recipients=["s2", "s3"],
        )
        recipients = [
            row["session_id"]
            for row in conn.execute(
                "select session_id from message_deliveries order by session_id"
            )
        ]
    finally:
        conn.close()

    assert recipients == ["s2", "s3"]


def test_drain_pending_uses_stable_seq_order(tmp_data_dir):
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        for msg_id, text in (("m1", "first"), ("m2", "second")):
            storage.store_message(
                conn,
                message_id=msg_id,
                kind="direct",
                from_session_id="s1",
                from_name="alpha",
                from_agent="codex",
                to_session_id="s2",
                to_name="beta",
                text=text,
                created_at="2026-08-07T10:00:00+00:00",
                recipients=["s2"],
            )
        rows = storage.drain_pending(conn, session_id="s2", limit=10)
    finally:
        conn.close()

    assert [row["id"] for row in rows] == ["m1", "m2"]
    assert [row["text"] for row in rows] == ["first", "second"]


def test_mark_delivery_transitions(tmp_data_dir):
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

        assert storage.mark_delivered(
            conn,
            message_id="m1",
            session_id="s2",
            delivered_at="2026-08-07T10:00:01+00:00",
        )
        row = conn.execute("select * from message_deliveries").fetchone()
        assert row["delivery_state"] == "delivered"
        assert row["delivered_at"] == "2026-08-07T10:00:01+00:00"

        assert storage.mark_read(
            conn,
            message_id="m1",
            session_id="s2",
            read_at="2026-08-07T10:00:02+00:00",
        )
        row = conn.execute("select * from message_deliveries").fetchone()
        assert row["delivery_state"] == "read"
        assert row["read_at"] == "2026-08-07T10:00:02+00:00"

        assert storage.mark_disposition(
            conn,
            message_id="m1",
            session_id="s2",
            disposition="replied",
            changed_at="2026-08-07T10:00:03+00:00",
            reply_message_id="m2",
        )
        row = conn.execute("select * from message_deliveries").fetchone()
    finally:
        conn.close()

    assert row["delivery_state"] == "read"
    assert row["disposition"] == "replied"
    assert row["replied_at"] == "2026-08-07T10:00:03+00:00"
    assert row["reply_message_id"] == "m2"


def test_get_message_returns_persisted_row(tmp_data_dir):
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        storage.store_message(
            conn,
            message_id="m1",
            kind="broadcast",
            from_session_id="s1",
            from_name="alpha",
            from_agent="codex",
            text="hello all",
            created_at="2026-08-07T10:00:00+00:00",
            recipients=["s2"],
        )
        row = storage.get_message(conn, message_id="m1")
    finally:
        conn.close()

    assert row is not None
    assert row["id"] == "m1"
    assert row["text"] == "hello all"


def test_apply_reply_disposition_marks_original_delivery(tmp_data_dir):
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
            text="please review",
            created_at="2026-08-07T10:00:00+00:00",
            recipients=["s2"],
        )

        disposition = storage.apply_reply_disposition(
            conn,
            original_message_id="m1",
            replying_session_id="s2",
            reply_message_id="m2",
            reply_text="answer: reviewed",
            changed_at="2026-08-07T10:01:00+00:00",
        )
        row = conn.execute("select * from message_deliveries").fetchone()
    finally:
        conn.close()

    assert disposition == "replied"
    assert row["delivery_state"] == "read"
    assert row["disposition"] == "replied"
    assert row["reply_message_id"] == "m2"


def test_apply_reply_disposition_marks_question_sent(tmp_data_dir):
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
            text="please review",
            created_at="2026-08-07T10:00:00+00:00",
            recipients=["s2"],
        )

        disposition = storage.apply_reply_disposition(
            conn,
            original_message_id="m1",
            replying_session_id="s2",
            reply_message_id="m2",
            reply_text="question: which file?",
            changed_at="2026-08-07T10:01:00+00:00",
        )
        row = conn.execute("select * from message_deliveries").fetchone()
    finally:
        conn.close()

    assert disposition == "question_sent"
    assert row["disposition"] == "question_sent"
    assert row["question_sent_at"] == "2026-08-07T10:01:00+00:00"


def test_catch_up_broadcasts_adds_missing_delivery_rows(tmp_data_dir):
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        storage.store_message(
            conn,
            message_id="old",
            kind="broadcast",
            from_session_id="s1",
            from_name="alpha",
            from_agent="codex",
            text="old broadcast",
            created_at="2026-08-07T09:00:00+00:00",
            recipients=[],
        )
        storage.store_message(
            conn,
            message_id="recent",
            kind="broadcast",
            from_session_id="s1",
            from_name="alpha",
            from_agent="codex",
            text="recent broadcast",
            created_at="2026-08-07T10:00:00+00:00",
            recipients=[],
        )

        count = storage.catch_up_broadcasts(
            conn,
            session_id="late",
            since="2026-08-07T09:30:00+00:00",
        )
        rows = list(conn.execute("select * from message_deliveries"))
    finally:
        conn.close()

    assert count == 1
    assert len(rows) == 1
    assert rows[0]["message_id"] == "recent"
    assert rows[0]["session_id"] == "late"
    assert rows[0]["delivery_state"] == "pending"


def test_failed_poison_message_does_not_block_queue(tmp_data_dir):
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        for msg_id, text in (("m1", "bad"), ("m2", "good")):
            storage.store_message(
                conn,
                message_id=msg_id,
                kind="direct",
                from_session_id="s1",
                from_name="alpha",
                from_agent="codex",
                to_session_id="s2",
                to_name="beta",
                text=text,
                created_at=f"2026-08-07T10:00:0{1 if msg_id == 'm1' else 2}+00:00",
                recipients=["s2"],
            )
        assert storage.mark_disposition(
            conn,
            message_id="m1",
            session_id="s2",
            disposition="failed",
            changed_at="2026-08-07T10:01:00+00:00",
            failure_reason="poison",
        )

        rows = storage.drain_pending(conn, session_id="s2", limit=10)
    finally:
        conn.close()

    assert [row["id"] for row in rows] == ["m2"]


def test_retry_transport_failures_only_retries_transport_failures(tmp_data_dir):
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        for msg_id in ("m1", "m2"):
            storage.store_message(
                conn,
                message_id=msg_id,
                kind="direct",
                from_session_id="s1",
                from_name="alpha",
                from_agent="codex",
                to_session_id="s2",
                to_name="beta",
                text=msg_id,
                created_at="2026-08-07T10:00:00+00:00",
                recipients=["s2"],
            )
        assert storage.mark_transport_failed(
            conn,
            message_id="m1",
            session_id="s2",
            failed_at="2026-08-07T10:01:00+00:00",
            failure_reason="websocket closed",
        )
        assert storage.mark_disposition(
            conn,
            message_id="m2",
            session_id="s2",
            disposition="skipped",
            changed_at="2026-08-07T10:01:00+00:00",
        )

        retried = storage.retry_transport_failures(conn, max_attempts=3)
        states = {
            row["message_id"]: (row["delivery_state"], row["disposition"])
            for row in conn.execute("select * from message_deliveries")
        }
    finally:
        conn.close()

    assert retried == 1
    assert states["m1"] == ("pending", "none")
    assert states["m2"] == ("read", "skipped")


def test_kill_9_mid_transaction_preserves_committed_data_without_corruption(tmp_data_dir):
    """WAL recovery: a hard kill (SIGKILL) mid-write must not corrupt the
    database, must keep everything already committed, and must not leave
    any trace of the transaction that was in flight when the process died.
    """
    db_path = tmp_data_dir / "interagents.sqlite3"
    script = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {str(SKILL_DIR)!r})
        from pathlib import Path
        from bin import storage

        conn = storage.connect(Path({str(db_path)!r}))

        # Committed before the kill — must survive.
        storage.upsert_session(
            conn, session_id="s1", name="alpha", label="codex", cwd="/work",
            pid=111, connected_at="2026-08-07T09:00:00+00:00",
        )
        storage.store_message(
            conn, message_id="committed-1", kind="direct",
            from_session_id="s1", from_name="alpha", from_agent="codex",
            to_session_id="s2", to_name="beta", text="safe",
            created_at="2026-08-07T10:00:00+00:00", recipients=["s2"],
        )

        # Started but never committed — the kill lands here. Must NOT survive.
        conn.execute(
            "insert into messages (id, kind, scope, from_session_id, "
            "from_name, to_session_id, to_name, text, created_at) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("uncommitted-1", "direct", "direct", "s1", "alpha", "s2",
             "beta", "mid-write", "2026-08-07T10:00:01+00:00"),
        )
        print("ready", flush=True)
        time.sleep(30)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        ready_line = proc.stdout.readline()
        assert ready_line.strip() == "ready", (
            f"child never signaled readiness; stderr={proc.stderr.read()}"
        )
        proc.kill()  # SIGKILL — no chance to flush/commit/close cleanly
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    conn = storage.connect(db_path)
    try:
        assert conn.execute("pragma journal_mode").fetchone()[0] == "wal"
        assert conn.execute("pragma integrity_check").fetchone()[0] == "ok"

        session = conn.execute("select * from sessions where id = 's1'").fetchone()
        assert session is not None
        assert session["name"] == "alpha"

        committed = storage.get_message(conn, message_id="committed-1")
        assert committed is not None
        assert committed["text"] == "safe"

        uncommitted = storage.get_message(conn, message_id="uncommitted-1")
        assert uncommitted is None
    finally:
        conn.close()


def test_purge_expired_deletes_old_terminal_deliveries_and_keeps_pending(tmp_data_dir):
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        for msg_id in ("old-replied", "recent-replied", "old-pending"):
            storage.store_message(
                conn,
                message_id=msg_id,
                kind="direct",
                from_session_id="s1",
                from_name="alpha",
                from_agent="codex",
                to_session_id="s2",
                to_name="beta",
                text=msg_id,
                created_at="2026-06-01T00:00:00+00:00",
                recipients=["s2"],
            )
        storage.mark_disposition(
            conn,
            message_id="old-replied",
            session_id="s2",
            disposition="replied",
            changed_at="2026-07-01T10:00:00+00:00",
            reply_message_id="r1",
        )
        storage.mark_disposition(
            conn,
            message_id="recent-replied",
            session_id="s2",
            disposition="replied",
            changed_at="2026-08-05T10:00:00+00:00",
            reply_message_id="r2",
        )
        # old-pending is left untouched: pending delivery, no disposition.

        result = storage.purge_expired(
            conn, now="2026-08-07T12:00:00+00:00", retention_days=7,
        )
        remaining = {
            row["message_id"]
            for row in conn.execute("select message_id from message_deliveries")
        }
    finally:
        conn.close()

    assert result["deliveries"] == 1
    assert result["messages"] == 0
    assert remaining == {"recent-replied", "old-pending"}


def test_purge_expired_deletes_expired_messages_without_pending_deliveries(tmp_data_dir):
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        # Expired long ago, no recipients ever recorded a delivery -> purge.
        storage.store_message(
            conn,
            message_id="expired-no-recipients",
            kind="broadcast",
            from_session_id="s1",
            from_name="alpha",
            from_agent="codex",
            text="old broadcast",
            created_at="2026-06-01T00:00:00+00:00",
            recipients=[],
            expires_at="2026-07-01T00:00:00+00:00",
        )
        # Expired long ago, but a recipient still hasn't drained it -> keep.
        storage.store_message(
            conn,
            message_id="expired-still-pending",
            kind="direct",
            from_session_id="s1",
            from_name="alpha",
            from_agent="codex",
            to_session_id="s2",
            to_name="beta",
            text="needs delivery",
            created_at="2026-06-01T00:00:00+00:00",
            recipients=["s2"],
            expires_at="2026-07-01T00:00:00+00:00",
        )
        # No expiry at all -> keep forever regardless of age.
        storage.store_message(
            conn,
            message_id="no-expiry",
            kind="direct",
            from_session_id="s1",
            from_name="alpha",
            from_agent="codex",
            to_session_id="s2",
            to_name="beta",
            text="permanent",
            created_at="2026-06-01T00:00:00+00:00",
            recipients=["s2"],
        )
        # Expiry set but still in the future -> keep.
        storage.store_message(
            conn,
            message_id="not-yet-expired",
            kind="direct",
            from_session_id="s1",
            from_name="alpha",
            from_agent="codex",
            to_session_id="s2",
            to_name="beta",
            text="still fresh",
            created_at="2026-08-06T00:00:00+00:00",
            recipients=["s2"],
            expires_at="2026-12-01T00:00:00+00:00",
        )

        result = storage.purge_expired(
            conn, now="2026-08-07T12:00:00+00:00", retention_days=7,
        )
        remaining_ids = {row["id"] for row in conn.execute("select id from messages")}
    finally:
        conn.close()

    assert result["deliveries"] == 0
    assert result["messages"] == 1
    assert remaining_ids == {"expired-still-pending", "no-expiry", "not-yet-expired"}


def test_purge_expired_is_noop_when_retention_disabled(tmp_data_dir):
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        storage.store_message(
            conn,
            message_id="old-replied",
            kind="direct",
            from_session_id="s1",
            from_name="alpha",
            from_agent="codex",
            to_session_id="s2",
            to_name="beta",
            text="x",
            created_at="2020-01-01T00:00:00+00:00",
            recipients=["s2"],
        )
        storage.mark_disposition(
            conn,
            message_id="old-replied",
            session_id="s2",
            disposition="replied",
            changed_at="2020-01-01T00:00:01+00:00",
            reply_message_id="r1",
        )

        result = storage.purge_expired(
            conn, now="2026-08-07T12:00:00+00:00", retention_days=0,
        )
        remaining = list(conn.execute("select 1 from message_deliveries"))
    finally:
        conn.close()

    assert result == {"deliveries": 0, "messages": 0}
    assert len(remaining) == 1


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off"])
def test_sqlite_toggle_disables_runtime_connect(tmp_data_dir, monkeypatch, value):
    monkeypatch.setenv("INTERAGENTS_SQLITE_ENABLED", value)

    with pytest.raises(RuntimeError, match="SQLite persistence disabled"):
        storage.connect(tmp_data_dir / "interagents.sqlite3")

    assert not (tmp_data_dir / "interagents.sqlite3").exists()


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "typo"])
def test_sqlite_toggle_preserves_default_for_enabled_or_invalid_values(
    tmp_data_dir, monkeypatch, value,
):
    monkeypatch.setenv("INTERAGENTS_SQLITE_ENABLED", value)

    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    conn.close()

    assert (tmp_data_dir / "interagents.sqlite3").exists()


def test_export_state_redacts_message_text_by_default(tmp_data_dir):
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    try:
        storage.store_message(
            conn,
            message_id="exported-message",
            kind="direct",
            from_session_id="s1",
            from_name="alpha",
            from_agent="codex",
            to_session_id="s2",
            to_name="beta",
            text="private payload",
            created_at="2026-08-14T12:00:00+00:00",
            recipients=["s2"],
        )

        redacted = storage.export_state(conn, table="messages")
        explicit = storage.export_state(conn, table="messages", include_text=True)
    finally:
        conn.close()

    assert redacted["schema_version"] == storage.SCHEMA_VERSION
    assert redacted["messages"][0]["text"] == storage.REDACTED_MESSAGE_TEXT
    assert explicit["messages"][0]["text"] == "private payload"
