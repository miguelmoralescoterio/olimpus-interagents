# MCP Configuration Examples

These examples run the local MCP server over stdio. The MCP process is only an
adapter; the shared bus state still lives in the interagents WebSocket server
and SQLite database.

Repo path used below:

```text
/Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents
```

## Generic Command

```bash
python3 /Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents/skills/interagents/bin/interagents.py mcp-stdio
```

## Codex

Add an MCP server entry to the active Codex MCP config:

```toml
[mcp_servers.interagents]
command = "python3"
args = [
  "/Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents/skills/interagents/bin/interagents.py",
  "mcp-stdio",
]
```

Suggested receive strategy:

- Use `interagents_drain` at the start of interagents work.
- Use `interagents_mark_replied`, `interagents_mark_skipped`, or
  `interagents_mark_failed` after acting.
- Keep the CLI/monitor fallback active until Codex hook injection is proven in
  a smoke test.

## Claude Code

Claude can keep using Monitor/plugin monitors for live delivery. MCP is useful
as an explicit tool layer:

```json
{
  "mcpServers": {
    "interagents": {
      "command": "python3",
      "args": [
        "/Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents/skills/interagents/bin/interagents.py",
        "mcp-stdio"
      ]
    }
  }
}
```

Suggested receive strategy:

- Prefer Monitor for live messages.
- Use MCP tools for explicit `send`, `drain`, `get_message`, and state marks.
- Batch monitor output before waking the model when possible.

## Kiro

Example MCP JSON:

```json
{
  "mcpServers": {
    "interagents": {
      "command": "python3",
      "args": [
        "/Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents/skills/interagents/bin/interagents.py",
        "mcp-stdio"
      ]
    }
  }
}
```

Suggested receive strategy:

- `SessionStart`: call `interagents_connect`.
- `UserPromptSubmit`: call `interagents_drain`.
- `Stop`: mark dispositions for messages handled in the turn.

## Cursor

Example `.cursor/mcp.json` shape:

```json
{
  "mcpServers": {
    "interagents": {
      "command": "python3",
      "args": [
        "/Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents/skills/interagents/bin/interagents.py",
        "mcp-stdio"
      ]
    }
  }
}
```

Suggested receive strategy:

- Spike `beforeSubmitPrompt` before relying on automatic context injection.
- If the spike fails, keep Cursor at `polling_only` and use explicit
  `interagents_drain`.

## Windsurf / Cascade

Example MCP JSON:

```json
{
  "mcpServers": {
    "interagents": {
      "command": "python3",
      "args": [
        "/Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents/skills/interagents/bin/interagents.py",
        "mcp-stdio"
      ]
    }
  }
}
```

Suggested receive strategy:

- Spike `pre_user_prompt` before relying on automatic context injection.
- Use `post_cascade_response` to mark messages as replied/skipped/failed when
  correlation is available.

## opencode

Example `opencode.json` MCP shape:

```json
{
  "mcp": {
    "interagents": {
      "type": "local",
      "command": [
        "python3",
        "/Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents/skills/interagents/bin/interagents.py",
        "mcp-stdio"
      ]
    }
  }
}
```

Suggested receive strategy:

- Use plugin events or explicit tool calls to drain.
- If prompt injection is not available, stay at `polling_only`.
- Mark dispositions explicitly after handling messages.
