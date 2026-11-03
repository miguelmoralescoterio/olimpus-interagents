"""Re-armed listeners: offline queue, replay on reconnect and bounded catch-up.

A Claude `Monitor` expires every 30 minutes and is re-armed with the same
name but a fresh session_id. These tests pin the bus behavior across that gap.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
import websockets

from bin import client, shared, storage
from bin.server import Server

OLD_TS = "2026-09-01T00:00:00+00:00"
RECENT_TS = "2026-11-02T10:00:00+00:00"
WINDOW_START = "2026-11-01T10:00:00+00:00"
SILENCE_S = 0.5


def _store_direct(conn, message_id, *, to_name="luffy", session="luffy-old",
                  created_at=RECENT_TS, kind="direct"):
    storage.store_message(
        conn,
        message_id=message_id,
        kind=kind,
        from_session_id="sender",
        from_name="milk",
        from_agent="claude",
        text=f"text {message_id}",
        created_at=created_at,
        recipients=[session],
        to_session_id=session,
        to_name=to_name,
    )


def _upsert(conn, session_id, name, seen_at):
    storage.upsert_session(
        conn, session_id=session_id, name=name, label="claude", cwd="/tmp",
        pid=1, connected_at=seen_at, last_seen_at=seen_at,
    )
    conn.commit()


@pytest.fixture
def db(tmp_data_dir):
    conn = storage.connect(tmp_data_dir / "interagents.sqlite3")
    yield conn
    conn.close()


class TestBoundedDirectCatchUp:
    def test_messages_older_than_window_are_not_reattached(self, db):
        _store_direct(db, "stale", created_at=OLD_TS)
        _store_direct(db, "fresh", created_at=RECENT_TS)

        count = storage.catch_up_direct(
            db, session_id="luffy-new", name="luffy", since=WINDOW_START,
        )
        attached = {r["message_id"] for r in db.execute(
            "select message_id from message_deliveries where session_id = 'luffy-new'"
        )}

        assert count == 1
        assert attached == {"fresh"}

    def test_queued_message_outlives_the_window(self, db):
        _store_direct(db, "queued-old", created_at=OLD_TS, kind=storage.QUEUED_KIND)

        count = storage.catch_up_direct(
            db, session_id="luffy-new", name="luffy", since=WINDOW_START,
        )

        assert count == 1, "An offline-queued direct must not expire with the catch-up window"

    def test_message_read_by_any_session_is_not_reattached(self, db):
        _store_direct(db, "consumed", session="luffy-a")
        db.execute(
            "insert into message_deliveries (message_id, session_id, delivery_state, disposition)"
            " values ('consumed', 'luffy-b', 'pending', 'none')"
        )
        storage.mark_read(db, message_id="consumed", session_id="luffy-a", read_at=RECENT_TS)

        count = storage.catch_up_direct(db, session_id="luffy-c", name="luffy", since=WINDOW_START)

        assert count == 0, "A stale pending row must not resurrect a message already read"


class TestQueueLookups:
    def test_latest_session_for_name_picks_most_recently_seen(self, db):
        _upsert(db, "luffy-1", "luffy", OLD_TS)
        _upsert(db, "luffy-2", "luffy", RECENT_TS)

        assert storage.latest_session_for_name(db, name="luffy") == "luffy-2"

    @pytest.mark.parametrize("name", ["", "never-seen"])
    def test_latest_session_for_unknown_name_is_none(self, db, name):
        assert storage.latest_session_for_name(db, name=name) is None

    def test_pending_queued_only_returns_queued_pending_rows(self, db):
        _store_direct(db, "q-pending", session="s1", kind=storage.QUEUED_KIND)
        _store_direct(db, "q-read", session="s1", kind=storage.QUEUED_KIND)
        _store_direct(db, "live-direct", session="s1")
        storage.mark_read(db, message_id="q-read", session_id="s1", read_at=RECENT_TS)

        rows = storage.pending_queued_for_session(db, session_id="s1")

        assert [r["id"] for r in rows] == ["q-pending"]


@pytest.fixture
async def bus(tmp_data_dir, free_port):
    shared.secure_dir(tmp_data_dir)
    token = shared.ensure_token(shared.token_path())
    srv = Server(host="127.0.0.1", port=free_port, idle_shutdown_minutes=10)
    task = asyncio.create_task(srv.serve())
    await srv.wait_ready()
    yield srv, free_port, token
    srv.stop()
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except asyncio.TimeoutError:
        task.cancel()


async def _agent(port, token, name, label="claude"):
    ws = await websockets.connect(f"ws://127.0.0.1:{port}/", max_size=shared.WS_FRAME_CAP)
    sid = str(uuid.uuid4())
    await ws.send(json.dumps({
        "op": "hello", "session_id": sid, "name": name, "label": label,
        "cwd": "/tmp", "pid": 1, "role": shared.Role.AGENT.value, "token": token,
    }))
    welcome = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
    assert welcome["op"] == "welcome", welcome
    return ws, sid


async def _next(ws, op, timeout=2.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
        if frame.get("op") in (op, "error"):
            return frame


async def _no_msg(ws):
    try:
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=SILENCE_S))
            if frame.get("op") == "msg":
                return frame
    except asyncio.TimeoutError:
        return None


def _delivery_state(srv, message_id, session_id):
    row = srv._db.execute(
        "select delivery_state from message_deliveries where message_id = ? and session_id = ?",
        (message_id, session_id),
    ).fetchone()
    return row["delivery_state"] if row else None


class TestOfflineQueue:
    async def test_direct_to_rearmed_agent_is_queued_and_replayed_once(self, bus):
        srv, port, token = bus
        sender, _ = await _agent(port, token, "milk")
        luffy, _ = await _agent(port, token, "luffy")
        await luffy.close()
        await asyncio.sleep(0.1)
        try:
            await sender.send(json.dumps({"op": "send", "to": "luffy", "text": "review #12"}))
            ack = await _next(sender, "queued")

            rearmed, new_sid = await _agent(port, token, "luffy")
            replayed = await _next(rearmed, "msg")
            await rearmed.close()
            await asyncio.sleep(0.1)
            rearmed_again, _ = await _agent(port, token, "luffy")
            duplicate = await _no_msg(rearmed_again)
            await rearmed_again.close()
        finally:
            await sender.close()

        assert ack == {"op": "queued", "msg_id": ack["msg_id"], "to": "luffy"}
        assert (replayed["msg_id"], replayed["text"], replayed["queued"]) == (
            ack["msg_id"], "review #12", True,
        )
        assert _delivery_state(srv, ack["msg_id"], new_sid) == "read"
        assert duplicate is None, "A replayed message must not be pushed again on the next re-arm"

    async def test_direct_to_never_seen_name_still_errors(self, bus):
        _, port, token = bus
        sender, _ = await _agent(port, token, "milk")
        try:
            await sender.send(json.dumps({"op": "send", "to": "ghost", "text": "hi"}))
            err = await _next(sender, "queued")
        finally:
            await sender.close()

        assert (err["op"], err["code"]) == ("error", shared.ErrorCode.UNKNOWN_PEER)

    async def test_queue_is_disabled_without_sqlite(self, bus):
        srv, port, token = bus
        sender, _ = await _agent(port, token, "milk")
        luffy, _ = await _agent(port, token, "luffy")
        await luffy.close()
        await asyncio.sleep(0.1)
        srv._db.close()
        srv._db = None
        try:
            await sender.send(json.dumps({"op": "send", "to": "luffy", "text": "hi"}))
            err = await _next(sender, "queued")
        finally:
            await sender.close()

        assert err["code"] == shared.ErrorCode.UNKNOWN_PEER


class TestLiveDeliveryMarking:
    @pytest.mark.parametrize(
        ("label", "expected_state"),
        [("claude", "read"), ("codex", "delivered")],
    )
    async def test_live_push_marks_delivery_by_receive_strategy(self, bus, label, expected_state):
        srv, port, token = bus
        sender, _ = await _agent(port, token, "milk")
        target, target_sid = await _agent(port, token, "luffy", label=label)
        try:
            await sender.send(json.dumps({"op": "send", "to": "luffy", "text": "ping"}))
            msg = await _next(target, "msg")
        finally:
            await sender.close()
            await target.close()

        assert _delivery_state(srv, msg["msg_id"], target_sid) == expected_state


class TestQueuedNotificationFormat:
    @pytest.mark.parametrize(
        ("queued", "expected_prefix"),
        [
            (True, '[interagents msg=ab12cd34 from="milk" "claude" queued]'),
            (False, '[interagents msg=ab12cd34 from="milk" "claude"]'),
        ],
    )
    def test_prefix_flags_messages_delivered_after_reconnect(self, queued, expected_prefix):
        line = client._format_msg({
            "msg_id": "ab12cd34", "from_name": "milk", "from_label": "claude",
            "text": "review #12", "queued": queued,
        })

        assert line == f"{expected_prefix} review #12"
