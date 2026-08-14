"""Static validation for adapter config examples."""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_adapter_json_configs_load():
    for path in (
        REPO / "adapters" / "cursor" / "mcp.json",
        REPO / "adapters" / "windsurf" / "mcp.json",
        REPO / "adapters" / "kiro" / "mcp.json",
        REPO / "adapters" / "opencode" / "opencode.json",
    ):
        cfg = json.loads(path.read_text())
        assert "interagents" in json.dumps(cfg)
        assert "mcp-stdio" in json.dumps(cfg)
