# Windsurf / Cascade Interagents Adapter

Use MCP stdio with `adapters/windsurf/mcp.json`.

Receive strategy:

- Preferred tier after validation: `pre_turn_hook`.
- Until `pre_user_prompt` proves context injection, treat Windsurf as
  `polling_only`.
- Use `interagents_drain` before interagent work.
- Use `post_cascade_response` to mark dispositions when the response handled a
  correlated message.

Smoke test:

1. Configure the MCP server.
2. Add a `pre_user_prompt` hook that drains pending messages.
3. Send Windsurf a peer message while idle.
4. Submit an unrelated prompt.
5. Confirm whether Cascade sees the drained content before answering.

If hook output is only a side effect and not model context, keep explicit drain
as the reliable path.
