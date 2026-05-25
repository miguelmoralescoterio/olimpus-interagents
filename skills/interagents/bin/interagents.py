"""Generic CLI for the interagents bus.

This wrapper is intentionally agent-neutral. Claude can use the skill slash
command, while Codex, Kiro, or a plain terminal can run this script directly.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


BIN_DIR = Path(__file__).resolve().parent
VENV = Path.home() / ".olimpus" / "interagents" / "venv"
VENV_PY = VENV / "bin" / "python"
REQS = BIN_DIR.parent / "requirements.txt"


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


def main() -> int:
    parser = argparse.ArgumentParser(prog="interagents")
    sub = parser.add_subparsers(dest="cmd", required=True)

    connect = sub.add_parser("connect", help="connect this terminal/session to the bus")
    connect.add_argument("--name", default="", help="ASCII handle, e.g. codex-api")
    connect.add_argument("--label", default="", help="optional display label, e.g. codex")
    connect.add_argument("--host", default=None)
    connect.add_argument("--port", default=None)
    connect.add_argument("--verbose", action="store_true")

    sub.add_parser("list", help="list connected agent sessions")
    sub.add_parser("status", help="show this session listener state")

    send = sub.add_parser("send", help="send a direct message")
    send.add_argument("to", help="target name or unambiguous prefix")
    send.add_argument("text", nargs=argparse.REMAINDER, help="message text")

    broadcast = sub.add_parser("broadcast", help="send to every other session")
    broadcast.add_argument("text", nargs=argparse.REMAINDER, help="message text")

    sub.add_parser("install-deps", help="install runtime deps into ~/.olimpus/interagents/venv")

    args = parser.parse_args()
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
        return _exec_script("client.py", argv)
    if args.cmd == "list":
        return _exec_script("list.py", [])
    if args.cmd == "status":
        return _exec_script("list.py", ["--self"])
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
