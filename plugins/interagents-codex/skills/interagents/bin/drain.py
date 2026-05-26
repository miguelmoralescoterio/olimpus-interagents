"""Drain pending messages for the current listener from messages.log.

This is intentionally log-based. Some hosts, notably Codex, do not attach a
long-lived monitor stdout stream to the model between turns, so the websocket
listener can receive messages but the agent still needs a turn-level drain.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_VENV = Path.home() / ".olimpus" / "interagents" / "venv"
_VENV_PY = _VENV / "bin" / "python"
if (not os.environ.get("INTERAGENTS_NO_REEXEC")
        and _VENV_PY.is_file()
        and Path(sys.prefix).resolve() != _VENV.resolve()):
    os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bin import discover, shared


def _cursor_path(state: dict) -> Path:
    session_id = state.get("session_id", "unknown")
    safe_id = "".join(ch for ch in session_id if ch.isalnum() or ch == "-")[:64]
    return shared.clients_dir() / f"{safe_id}.cursor"


def _read_cursor(path: Path) -> int:
    try:
        return max(0, int(path.read_text().strip()))
    except (OSError, ValueError):
        return 0


def _write_cursor(path: Path, pos: int) -> None:
    shared.secure_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(f"{pos}\n")
    shared.secure_file(tmp)
    os.replace(tmp, path)


def _message_matches_self(rec: dict, state: dict) -> bool:
    self_sid = state.get("session_id", "")
    self_name = state.get("name", "")
    if rec.get("from") == self_sid:
        return False
    if rec.get("to_session_id"):
        return rec.get("to_session_id") == self_sid
    if rec.get("kind") == "broadcast":
        return True
    return rec.get("to") == self_name and bool(self_name)


def _payload_from_record(rec: dict) -> dict:
    return {
        "op": "msg",
        "msg_id": rec.get("msg_id", ""),
        "from": rec.get("from", ""),
        "from_name": rec.get("from_name", ""),
        "from_label": rec.get("from_label", ""),
        "text": rec.get("text", ""),
    }


def _format_msg(msg: dict) -> str:
    sanitized = shared.sanitize_for_stdout(msg.get("text", ""))
    truncated, was_truncated, full_len = shared.truncate_for_stdout(sanitized)
    from_name = msg.get("from_name") or msg.get("from", "?")[:8]
    from_label = msg.get("from_label", "")
    msg_id = msg.get("msg_id", "")
    label_part = f' "{from_label}"' if from_label else ""
    if was_truncated:
        prefix = f'[interagents msg={msg_id} from="{from_name}"{label_part} truncated={full_len}]'
    else:
        prefix = f'[interagents msg={msg_id} from="{from_name}"{label_part}]'
    return f"{prefix} {truncated}"


def drain(limit: int, mark_read: bool = True) -> int:
    state, state_path = discover.find_listener_state_with_path()
    if state is None:
        print("not connected; run interagents connect first", file=sys.stderr)
        return 1

    listener_pid = state.get("listener_pid", 0)
    if listener_pid and not shared.safe_pid_alive(int(listener_pid)):
        discover.unlink_if_matches(state_path, state)
        print("not connected (stale state cleaned up)", file=sys.stderr)
        return 1

    log_path = shared.messages_log_path()
    if not log_path.exists():
        return 0

    cursor = _cursor_path(state)
    pos = _read_cursor(cursor)
    size = log_path.stat().st_size
    if pos > size:
        pos = 0

    emitted = 0
    with open(log_path, "r", encoding="utf-8") as f:
        f.seek(pos)
        while emitted < limit:
            line = f.readline()
            if not line:
                break
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or not _message_matches_self(rec, state):
                continue
            print(_format_msg(_payload_from_record(rec)), flush=True)
            emitted += 1
        new_pos = f.tell()

    if mark_read:
        _write_cursor(cursor, new_pos)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--peek", action="store_true",
                        help="print pending messages without advancing the cursor")
    args = parser.parse_args()
    return drain(limit=max(1, args.limit), mark_read=not args.peek)


if __name__ == "__main__":
    sys.exit(main())
