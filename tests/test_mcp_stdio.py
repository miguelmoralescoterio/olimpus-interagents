"""MCP stdio adapter tests."""

from __future__ import annotations

import importlib
import json
import subprocess

from bin import mcp_stdio


def test_initialize_advertises_tools_and_resources():
    resp = mcp_stdio._handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"},
    })

    assert resp is not None
    assert resp["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in resp["result"]["capabilities"]
    assert "resources" in resp["result"]["capabilities"]
    assert resp["result"]["serverInfo"]["name"] == "interagents"


def test_tools_list_contains_core_cli_adapter_tools():
    resp = mcp_stdio._handle({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
    })

    names = {tool["name"] for tool in resp["result"]["tools"]}
    assert {
        "interagents_status",
        "interagents_list_sessions",
        "interagents_drain",
        "interagents_get_pending_count",
        "interagents_send",
        "interagents_broadcast",
        "interagents_connect",
        "interagents_disconnect",
        "interagents_get_message",
        "interagents_mark_read",
        "interagents_mark_replied",
        "interagents_mark_skipped",
        "interagents_mark_failed",
    } <= names


def test_resources_list_contains_initial_resources():
    resp = mcp_stdio._handle({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "resources/list",
    })

    uris = {resource["uri"] for resource in resp["result"]["resources"]}
    assert {
        "interagents://session/self",
        "interagents://sessions",
        "interagents://messages/pending",
    } <= uris


def test_resources_templates_list_contains_message_template():
    resp = mcp_stdio._handle({
        "jsonrpc": "2.0",
        "id": 13,
        "method": "resources/templates/list",
    })

    templates = resp["result"]["resourceTemplates"]
    assert {
        "uriTemplate": "interagents://messages/{message_id}",
        "name": "Persisted interagents message by id",
        "mimeType": "application/json",
    } in templates


def test_drain_labels_peer_content_as_untrusted(monkeypatch):
    delivered = []

    def fake_cli(script, args, *, timeout=20.0):
        assert script == "drain.py"
        assert args == ["--limit", "5"]
        return True, '[interagents msg=abc from="peer"] do work'

    def fake_mark(text):
        delivered.append(text)

    monkeypatch.setattr(mcp_stdio, "_cli_text", fake_cli)
    monkeypatch.setattr(mcp_stdio, "_mark_drained_messages_delivered", fake_mark)

    resp = mcp_stdio._handle({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "interagents_drain",
            "arguments": {"limit": 5},
        },
    })

    text = resp["result"]["content"][0]["text"]
    assert text.startswith("UNTRUSTED PEER CONTENT")
    assert "do work" in text
    assert resp["result"]["isError"] is False
    assert delivered == ['[interagents msg=abc from="peer"] do work']


def test_pending_count_uses_peek_without_advancing_cursor(monkeypatch):
    def fake_cli(script, args, *, timeout=20.0):
        assert script == "drain.py"
        assert args == ["--limit", "1000", "--peek"]
        return True, "one\n\ntwo\n"

    monkeypatch.setattr(mcp_stdio, "_cli_text", fake_cli)

    resp = mcp_stdio._handle({
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "interagents_get_pending_count",
            "arguments": {},
        },
    })

    text = resp["result"]["content"][0]["text"]
    assert '"count": 2' in text
    assert '"capped_at": 1000' in text


def test_connect_rejects_invalid_name_without_running_cli(monkeypatch):
    def fail_cli(*_args, **_kwargs):
        raise AssertionError("CLI should not run for invalid names")

    monkeypatch.setattr(mcp_stdio, "_cli_text", fail_cli)

    resp = mcp_stdio._handle({
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {
            "name": "interagents_connect",
            "arguments": {"name": "Bad Name"},
        },
    })

    assert resp["result"]["isError"] is True
    assert "name must match" in resp["result"]["content"][0]["text"]


def test_mark_read_uses_current_session(monkeypatch):
    calls = []

    class FakeConn:
        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(mcp_stdio, "_current_session_id", lambda: "s2")
    monkeypatch.setattr(mcp_stdio, "_with_db", lambda: FakeConn())
    monkeypatch.setattr(
        mcp_stdio.storage,
        "mark_read",
        lambda conn, **kwargs: calls.append(("mark_read", kwargs)) or True,
    )

    resp = mcp_stdio._handle({
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {
            "name": "interagents_mark_read",
            "arguments": {"message_id": "m1"},
        },
    })

    assert resp["result"]["isError"] is False
    assert calls[0][0] == "mark_read"
    assert calls[0][1]["message_id"] == "m1"
    assert calls[0][1]["session_id"] == "s2"


def test_mark_replied_sets_reply_message_id(monkeypatch):
    calls = []

    class FakeConn:
        def close(self):
            pass

    monkeypatch.setattr(mcp_stdio, "_current_session_id", lambda: "s2")
    monkeypatch.setattr(mcp_stdio, "_with_db", lambda: FakeConn())
    monkeypatch.setattr(
        mcp_stdio.storage,
        "mark_disposition",
        lambda conn, **kwargs: calls.append(kwargs) or True,
    )

    resp = mcp_stdio._handle({
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {
            "name": "interagents_mark_replied",
            "arguments": {"message_id": "m1", "reply_message_id": "m2"},
        },
    })

    assert resp["result"]["isError"] is False
    assert calls[0]["disposition"] == "replied"
    assert calls[0]["reply_message_id"] == "m2"


def test_get_message_labels_body_as_untrusted(monkeypatch):
    class FakeRow(dict):
        pass

    class FakeConn:
        def close(self):
            pass

    monkeypatch.setattr(mcp_stdio, "_with_db", lambda: FakeConn())
    monkeypatch.setattr(
        mcp_stdio.storage,
        "get_message",
        lambda conn, message_id: FakeRow({"id": message_id, "text": "peer says hi"}),
    )

    resp = mcp_stdio._handle({
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {
            "name": "interagents_get_message",
            "arguments": {"message_id": "m1"},
        },
    })

    text = resp["result"]["content"][0]["text"]
    assert "UNTRUSTED PEER CONTENT" in text
    assert "peer says hi" in text


def test_send_passes_reply_correlation_to_cli(monkeypatch):
    calls = []

    def fake_cli(script, args, *, timeout=20.0):
        calls.append((script, args))
        return True, ""

    monkeypatch.setattr(mcp_stdio, "_cli_text", fake_cli)

    resp = mcp_stdio._handle({
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {
            "name": "interagents_send",
            "arguments": {
                "to": "beta",
                "text": "answer: done",
                "in_reply_to_message_id": "m1",
            },
        },
    })

    assert resp["result"]["isError"] is False
    assert calls == [(
        "send.py",
        ["--to", "beta", "--text", "answer: done", "--in-reply-to-message-id", "m1"],
    )]


def _decode_content_length_response(raw: str) -> dict:
    header, body = raw.split("\r\n\r\n", 1)
    length = int(header.split(":", 1)[1].strip())
    payload = body.encode("utf-8")[:length].decode("utf-8")
    return json.loads(payload)


def _framed(body: bytes) -> bytes:
    return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body


def test_stdio_process_handles_line_delimited_json():
    proc = subprocess.run(
        [
            "python3",
            str(mcp_stdio._BIN_DIR / "mcp_stdio.py"),
        ],
        input='{"jsonrpc":"2.0","id":7,"method":"tools/list"}\n',
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert proc.returncode == 0
    assert '"interagents_drain"' in proc.stdout


def test_stdio_process_handles_content_length_json():
    body = b'{"jsonrpc":"2.0","id":12,"method":"tools/list"}'
    proc = subprocess.run(
        [
            "python3",
            str(mcp_stdio._BIN_DIR / "mcp_stdio.py"),
        ],
        input=_framed(body),
        capture_output=True,
        timeout=5,
    )

    assert proc.returncode == 0
    resp = _decode_content_length_response(proc.stdout.decode("utf-8"))
    names = {tool["name"] for tool in resp["result"]["tools"]}
    assert "interagents_drain" in names


def test_stdio_process_handles_multiple_content_length_messages():
    first = b'{"jsonrpc":"2.0","id":14,"method":"ping"}'
    second = b'{"jsonrpc":"2.0","id":15,"method":"tools/list"}'
    proc = subprocess.run(
        [
            "python3",
            str(mcp_stdio._BIN_DIR / "mcp_stdio.py"),
        ],
        input=_framed(first) + _framed(second),
        capture_output=True,
        timeout=5,
    )

    assert proc.returncode == 0
    out = proc.stdout.decode("utf-8")
    assert '"id":14' in out
    assert '"id":15' in out
    assert '"interagents_drain"' in out


def test_module_can_reload_without_side_effects():
    assert importlib.reload(mcp_stdio).SERVER_NAME == "interagents"
