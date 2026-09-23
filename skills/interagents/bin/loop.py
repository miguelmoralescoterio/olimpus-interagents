"""Periodic drain loop for hosts without reliable hook/monitor delivery."""

from __future__ import annotations

import argparse
import os
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

from bin import drain


def run_loop(*, interval_seconds: float, limit: int, once: bool = False) -> int:
    while True:
        code = drain.drain(limit=limit, mark_read=True)
        if once or code != 0:
            return code
        time.sleep(interval_seconds)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=float, default=120)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--once", action="store_true",
                        help="run one drain cycle and exit")
    args = parser.parse_args()
    return run_loop(
        interval_seconds=max(1, args.interval_seconds),
        limit=max(1, args.limit),
        once=args.once,
    )


if __name__ == "__main__":
    sys.exit(main())
