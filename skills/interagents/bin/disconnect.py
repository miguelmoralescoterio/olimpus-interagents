"""Disconnect the current interagents listener."""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

_VENV = Path.home() / ".olimpus" / "interagents" / "venv"
_VENV_PY = _VENV / "bin" / "python"
if (not os.environ.get("INTERAGENTS_NO_REEXEC")
        and _VENV_PY.is_file()
        and Path(sys.prefix).resolve() != _VENV.resolve()):
    os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])

_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from bin import discover, shared


def _looks_like_interagents_client(pid: int) -> bool:
    try:
        import psutil
    except ImportError:
        return True
    try:
        cmd = " ".join(psutil.Process(pid).cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    return "bin/client.py" in cmd or "interagents" in cmd


def disconnect(timeout: float = 3.0) -> int:
    state, state_path = discover.find_listener_state_with_path()
    if state is None:
        print("not connected")
        return 0
    try:
        pid = int(state.get("listener_pid", 0))
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0 or not shared.safe_pid_alive(pid):
        discover.unlink_if_matches(state_path, state)
        print("not connected (stale state cleaned up)")
        return 0
    if not _looks_like_interagents_client(pid):
        print(f"refusing to terminate non-interagents process pid={pid}", file=sys.stderr)
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        print(f"disconnect failed: {e}", file=sys.stderr)
        return 1

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not shared.safe_pid_alive(pid):
            discover.unlink_if_matches(state_path, state)
            print(f"disconnected listener_pid={pid}")
            return 0
        time.sleep(0.1)
    print(f"disconnect requested for listener_pid={pid}")
    return 0


def main() -> int:
    return disconnect()


if __name__ == "__main__":
    sys.exit(main())
