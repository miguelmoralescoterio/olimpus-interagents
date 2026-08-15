"""Periodic drain loop tests."""

from __future__ import annotations

from bin import loop


def test_loop_once_runs_one_drain(monkeypatch):
    calls = []

    def fake_drain(*, limit, mark_read):
        calls.append((limit, mark_read))
        return 0

    monkeypatch.setattr(loop.drain, "drain", fake_drain)

    code = loop.run_loop(interval_seconds=120, limit=25, once=True)

    assert code == 0
    assert calls == [(25, True)]
