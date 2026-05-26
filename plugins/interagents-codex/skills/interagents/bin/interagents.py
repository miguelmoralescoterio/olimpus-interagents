"""Generic CLI for the interagents bus.

This wrapper is intentionally agent-neutral. Claude can use the skill slash
command, while Codex, Kiro, or a plain terminal can run this script directly.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


BIN_DIR = Path(__file__).resolve().parent
SKILL_DIR = BIN_DIR.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from bin import shared

VENV = Path.home() / ".olimpus" / "interagents" / "venv"
VENV_PY = VENV / "bin" / "python"
REQS = BIN_DIR.parent / "requirements.txt"
KNOWN_COMMANDS = {
    "connect", "list", "status", "drain", "send", "broadcast", "install-deps",
}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def _exec_script(script: str, args: list[str]) -> int:
    python = str(VENV_PY) if VENV_PY.is_file() else sys.executable
    os.execv(python, [python, str(BIN_DIR / script), *args])
    return 127


def _install_deps() -> int:
    VENV.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    uv = shutil.which("uv")
    if uv:
        subprocess.check_call([uv, "venv", str(VENV)])
        subprocess.check_call([uv, "pip", "install", "-p", str(VENV), "-r", str(REQS)])
    else:
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
        subprocess.check_call([str(VENV / "bin" / "pip"), "install", "-r", str(REQS)])
    print(f"installed runtime deps in {VENV}")
    return 0


def _start_daemon(args: list[str]) -> int:
    """Start client.py detached and return after it writes this session state."""
    python = str(VENV_PY) if VENV_PY.is_file() else sys.executable
    shared.secure_dir(shared.data_dir())
    log_path = shared.data_dir() / "client.log"
    fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    shared.secure_file(log_path)
    log = os.fdopen(fd, "a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            [python, str(BIN_DIR / "client.py"), *args],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            text=True,
        )
    finally:
        log.close()

    state_path = shared.client_session_path(shared.resolve_listener_key())
    deadline = time.time() + 5
    while time.time() < deadline:
        if state_path.exists():
            print(f"started interagents daemon pid={proc.pid}")
            return 0
        code = proc.poll()
        if code is not None:
            print(f"interagents daemon exited with code {code}; see {log_path}",
                  file=sys.stderr)
            return code or 1
        time.sleep(0.1)
    print(f"interagents daemon did not write session state; see {log_path}",
          file=sys.stderr)
    return 1


def _normalize_argv(argv: list[str]) -> list[str]:
    """Accept `interagents <name>` as shorthand for `connect --name <name>`."""
    if argv and argv[0] not in KNOWN_COMMANDS and NAME_RE.match(argv[0]):
        return ["connect", "--name", argv[0], *argv[1:]]
    return argv


def main() -> int:
    parser = argparse.ArgumentParser(prog="interagents")
    sub = parser.add_subparsers(dest="cmd", required=True)

    connect = sub.add_parser("connect", help="connect this terminal/session to the bus")
    connect.add_argument("--name", default="", help="ASCII handle, e.g. codex-api")
    connect.add_argument("--label", default="", help="optional display label, e.g. codex")
    connect.add_argument("--host", default=None)
    connect.add_argument("--port", default=None)
    connect.add_argument("--verbose", action="store_true")
    connect.add_argument("--daemon", action="store_true",
                         help="start listener in the background and exit")

    sub.add_parser("list", help="list connected agent sessions")
    sub.add_parser("status", help="show this session listener state")
    drain = sub.add_parser("drain", help="print pending messages from the log cursor")
    drain.add_argument("--limit", type=int, default=50)
    drain.add_argument("--peek", action="store_true")

    send = sub.add_parser("send", help="send a direct message")
    send.add_argument("to", help="target name or unambiguous prefix")
    send.add_argument("text", nargs=argparse.REMAINDER, help="message text")

    broadcast = sub.add_parser("broadcast", help="send to every other session")
    broadcast.add_argument("text", nargs=argparse.REMAINDER, help="message text")

    sub.add_parser("install-deps", help="install runtime deps into ~/.olimpus/interagents/venv")

    args = parser.parse_args(_normalize_argv(sys.argv[1:]))
    if args.cmd == "connect":
        argv: list[str] = []
        if args.name:
            argv.extend(["--name", args.name])
        if args.label:
            argv.extend(["--label", args.label])
        if args.host:
            argv.extend(["--host", args.host])
        if args.port:
            argv.extend(["--port", args.port])
        if args.verbose:
            argv.append("--verbose")
        if args.daemon:
            return _start_daemon(argv)
        return _exec_script("client.py", argv)
    if args.cmd == "list":
        return _exec_script("list.py", [])
    if args.cmd == "status":
        return _exec_script("list.py", ["--self"])
    if args.cmd == "drain":
        argv = ["--limit", str(args.limit)]
        if args.peek:
            argv.append("--peek")
        return _exec_script("drain.py", argv)
    if args.cmd == "send":
        text = " ".join(args.text).strip()
        if not text:
            parser.error("send requires message text")
        return _exec_script("send.py", ["--to", args.to, "--text", text])
    if args.cmd == "broadcast":
        text = " ".join(args.text).strip()
        if not text:
            parser.error("broadcast requires message text")
        return _exec_script("send.py", ["--all", "--text", text])
    if args.cmd == "install-deps":
        return _install_deps()
    return 2


if __name__ == "__main__":
    sys.exit(main())
