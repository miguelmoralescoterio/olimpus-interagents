"""Delivery and failure semantics for the Codex adapter; no external services."""

import asyncio
from contextlib import nullcontext
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bin import storage

ADAPTER = Path(__file__).resolve().parents[1] / "adapters/codex/monitor.py"
spec = importlib.util.spec_from_file_location("codex_monitor", ADAPTER)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


@pytest.fixture
def bridge(tmp_data_dir, tmp_path):
    journal = monitor.Journal(tmp_path / "journal.sqlite3", str(tmp_path))
    journal.set("thread_id", "thread-test")
    rpc = SimpleNamespace(run_turn=AsyncMock(return_value="Listo"))
    args = SimpleNamespace(name="codex-test", port=9474, allow_peer=[], turn_timeout=60)
    instance = monitor.Monitor(args, journal, rpc)
    yield instance
    instance.db.close()
    journal.db.close()


def incoming(bridge, text="Revisa el código", message_id="request-1"):
    storage.store_message(
        bridge.db, message_id=message_id, kind="direct", from_session_id="peer-id",
        from_name="milk", from_agent="claude", text=text, created_at="2026-09-20",
        recipients=[bridge.listener.session_id],
    )
    return storage.get_message(bridge.db, message_id=message_id)


@pytest.fixture
def persisted_send(monkeypatch, bridge):
    async def send(args):
        storage.store_message(
            bridge.db, message_id="response-1", kind="direct",
            from_session_id=bridge.listener.session_id, from_name="codex-test",
            from_agent="codex", text=args.text, created_at="2026-09-20",
            recipients=[args.to], in_reply_to_message_id=args.in_reply_to_message_id,
        )
        return 0
    sender = AsyncMock(side_effect=send)
    monkeypatch.setattr(monitor.send, "_run", sender)
    return sender


async def test_request_executes_once_and_reply_is_correlated(bridge, persisted_send):
    row = incoming(bridge)
    await bridge.process(row)
    await bridge.process(row)
    bridge.rpc.run_turn.assert_awaited_once()
    sent = persisted_send.call_args.args[0]
    assert (sent.to, sent.in_reply_to_message_id, sent.text) == ("peer-id", "request-1", "answer: [reply-to:request-1] Listo"), "Reply must return to the original peer and request"
    assert bridge.journal.job(row["id"])["status"] == "done", "Only acknowledged replies complete the job"


@pytest.mark.parametrize("text", ["done: listo", "status: trabajando", " ANSWER: listo"])
async def test_informational_messages_do_not_trigger_reply_loops(bridge, text):
    row = incoming(bridge, text)
    await bridge.process(row)
    bridge.rpc.run_turn.assert_not_awaited()
    disposition = bridge.db.execute("select disposition from message_deliveries").fetchone()[0]
    assert disposition == "skipped", "Informational delivery must be consumed without generating a turn"


async def test_non_allowlisted_sender_is_skipped(bridge):
    bridge.args.allow_peer = ["vegeta"]
    await bridge.process(incoming(bridge))
    bridge.rpc.run_turn.assert_not_awaited()


async def test_unconfirmed_send_stays_in_outbox_without_reexecution(bridge, monkeypatch):
    sender = AsyncMock(return_value=0)
    monkeypatch.setattr(monitor.send, "_run", sender)
    row = incoming(bridge)
    await bridge.process(row)
    await bridge.process(row)
    bridge.rpc.run_turn.assert_awaited_once()
    assert bridge.journal.job(row["id"])["status"] == "reply", "Silence from the bus is not a delivery acknowledgement"


async def test_restart_after_send_detects_existing_reply(bridge, persisted_send):
    row = incoming(bridge)
    await bridge.process(row)
    bridge.journal.save(row["id"], "reply", "answer: [reply-to:request-1] Listo")
    await bridge.process(row)
    persisted_send.assert_awaited_once()


@pytest.mark.parametrize("failure", [RuntimeError("disconnected"), asyncio.TimeoutError()])
async def test_execution_failure_is_not_retried(bridge, failure):
    bridge.rpc.run_turn.side_effect = failure
    row = incoming(bridge)
    with pytest.raises(RuntimeError, match="reply saved"):
        await bridge.process(row)
    assert bridge.journal.job(row["id"])["status"] == "reply", "Failure must leave a durable response instead of retrying the task"


def test_restart_preserves_identity_and_quarantines_uncertain_execution(tmp_path):
    path = tmp_path / "journal.sqlite3"
    journal = monitor.Journal(path, str(tmp_path))
    identity = journal.get("session_id")
    journal.save("interrupted", "running")
    journal.db.close()
    restarted = monitor.Journal(path, str(tmp_path))
    try:
        assert restarted.get("session_id") == identity, "Reconnect must retain destination identity"
        assert restarted.job("interrupted")["status"] == "reply", "An uncertain execution must not run twice"
        assert path.stat().st_mode & 0o777 == 0o600, "Journal contains private state"
    finally:
        restarted.db.close()


def test_monitor_name_cannot_switch_workspace(tmp_path):
    path = tmp_path / "journal.sqlite3"
    monitor.Journal(path, "first").db.close()
    with pytest.raises(ValueError, match="another cwd"):
        monitor.Journal(path, "second")


@pytest.mark.parametrize("method,result", [
    ("item/commandExecution/requestApproval", {"decision": "decline"}),
    ("item/fileChange/requestApproval", {"decision": "decline"}),
    ("item/permissions/requestApproval", {"permissions": {}, "scope": "turn"}),
    ("item/tool/requestUserInput", {"answers": {}}),
    ("mcpServer/elicitation/request", {"action": "decline"}),
])
async def test_interactive_requests_never_auto_approve(method, result):
    rpc = monitor.CodexRPC()
    rpc.write = AsyncMock()
    await rpc.dispatch({"id": "approval", "method": method})
    rpc.write.assert_awaited_once_with({"id": "approval", "result": result})
    assert rpc.blocked, "Caller must be informed that interaction was required"


async def test_unknown_server_request_returns_protocol_error():
    rpc = monitor.CodexRPC()
    rpc.write = AsyncMock()
    await rpc.dispatch({"id": "unknown", "method": "unsupported"})
    assert rpc.write.call_args.args[0]["error"]["code"] == -32601, "Unsupported requests must not hang the server"


@pytest.mark.parametrize("response,expected,outcome", [
    ({"result": {"thread": {"id": "thread"}}}, {"thread": {"id": "thread"}}, nullcontext()),
    ({"error": {"message": "private detail"}}, None, pytest.raises(RuntimeError, match="^app-server rejected request$")),
])
async def test_rpc_correlates_responses_without_leaking_errors(response, expected, outcome):
    rpc = monitor.CodexRPC()
    future = asyncio.get_running_loop().create_future()
    rpc.pending[1] = future
    await rpc.dispatch({"id": 1, **response})
    with outcome:
        assert await future == expected, "Request must receive its own response"


async def test_turn_filters_other_threads_and_returns_final_answer():
    rpc = monitor.CodexRPC()
    rpc.request = AsyncMock(return_value={"turn": {"id": "turn"}})
    for thread, phase, text in [("other", "final_answer", "wrong"), ("thread", "commentary", "working"), ("thread", "final_answer", "result")]:
        await rpc.dispatch({"method": "item/completed", "params": {
            "threadId": thread, "turnId": "turn", "item": {"type": "agentMessage", "phase": phase, "text": text},
        }})
    await rpc.dispatch({"method": "turn/completed", "params": {
        "threadId": "thread", "turn": {"id": "turn", "status": "completed"},
    }})
    assert await rpc.run_turn("thread", "request") == "result", "Only the matching turn's final response should be sent"


async def test_disconnected_server_ends_waiting_turn():
    rpc = monitor.CodexRPC()
    rpc.request = AsyncMock(return_value={"turn": {"id": "turn"}})
    await rpc.events.put({"method": "bridge/disconnected"})
    with pytest.raises(RuntimeError, match="disconnected"):
        await rpc.run_turn("thread", "request")


async def test_legacy_bus_payload_is_persisted_without_sqlite(bridge):
    await bridge.receive({"msg_id": "old-bus", "from": "peer-id", "from_name": "milk",
                          "text": "original request", "ts": "2026-09-20"})
    row = storage.get_message(bridge.db, message_id="old-bus")
    assert row["text"] == "original request", "The adapter must persist legacy websocket messages itself"
    assert bridge.wakeup.is_set(), "Message receipt must wake the worker"


def test_legacy_log_recovers_direct_messages_and_confirms_reply(bridge):
    import json
    path = monitor.shared.messages_log_path()
    request = {"msg_id": "old-request", "from": "peer-id", "from_name": "milk",
               "text": "request", "ts": "2026-09-20", "to_session_id": bridge.listener.session_id}
    reply = "answer: [reply-to:old-request] listo"
    response = {"from": bridge.listener.session_id, "to_session_id": "peer-id", "text": reply}
    path.write_text(json.dumps(request) + "\n" + json.dumps(response) + "\n")
    bridge.recover_log()
    row = storage.get_message(bridge.db, message_id="old-request")
    assert bridge.acknowledged(row, reply), "Legacy JSONL confirms a uniquely correlated response"


def test_log_recovery_ignores_other_recipients(bridge):
    import json
    monitor.shared.messages_log_path().write_text(json.dumps({"to_session_id": "someone-else"}) + "\n")
    bridge.recover_log()
    assert bridge.db.execute("select count(*) from messages").fetchone()[0] == 0, "Recovery must not consume another session's messages"


async def test_empty_unpersisted_thread_can_be_recreated(bridge):
    bridge.args.cwd = bridge.journal.get("cwd")
    bridge.rpc.request = AsyncMock(side_effect=[RuntimeError("not persisted"), {"thread": {"id": "replacement"}}])
    await bridge.initialize_thread()
    assert bridge.journal.get("thread_id") == "replacement", "A never-used thread can be replaced after restart"
    assert bridge.rpc.request.call_args.args[0] == "thread/start", "Replacement must create a fresh dedicated thread"


async def test_thread_with_prior_work_is_not_silently_replaced(bridge):
    bridge.args.cwd = bridge.journal.get("cwd")
    bridge.journal.save("previous", "done")
    bridge.rpc.request = AsyncMock(side_effect=RuntimeError("resume rejected"))
    with pytest.raises(RuntimeError, match="resume rejected"):
        await bridge.initialize_thread()
    bridge.rpc.request.assert_awaited_once()
