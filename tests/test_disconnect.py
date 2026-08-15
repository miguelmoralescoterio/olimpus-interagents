"""Disconnect helper tests."""

from __future__ import annotations

import json

from bin import disconnect, shared


def test_disconnect_without_state_is_noop(tmp_data_dir, capsys):
    code = disconnect.disconnect()

    assert code == 0
    assert "not connected" in capsys.readouterr().out


def test_disconnect_cleans_stale_state(tmp_data_dir, monkeypatch, capsys):
    monkeypatch.setenv("INTERAGENTS_PPID_OVERRIDE", "12345")
    shared.secure_dir(shared.clients_dir())
    path = shared.client_session_path(12345)
    state = {"session_id": "s1", "nonce": "n", "listener_pid": 999999}
    path.write_text(json.dumps(state))

    code = disconnect.disconnect()

    assert code == 0
    assert "stale state cleaned up" in capsys.readouterr().out
    assert not path.exists()


def test_disconnect_refuses_non_interagents_process(tmp_data_dir, monkeypatch, capsys):
    monkeypatch.setenv("INTERAGENTS_PPID_OVERRIDE", "12345")
    shared.secure_dir(shared.clients_dir())
    path = shared.client_session_path(12345)
    state = {"session_id": "s1", "nonce": "n", "listener_pid": 111}
    path.write_text(json.dumps(state))
    monkeypatch.setattr(disconnect.shared, "safe_pid_alive", lambda pid: True)
    monkeypatch.setattr(disconnect, "_looks_like_interagents_client", lambda pid: False)

    code = disconnect.disconnect()

    assert code == 1
    assert "refusing to terminate" in capsys.readouterr().err
    assert path.exists()
