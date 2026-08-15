# Cursor Interagents Adapter

Use MCP stdio with `adapters/cursor/mcp.json`.

Receive strategy:

- Preferred tier after validation: `pre_turn_hook`.
- Until `beforeSubmitPrompt` is smoke-tested, treat Cursor as `polling_only`.
- Drain before interagent work with `interagents_drain`.
- When replying to a specific message, send with `in_reply_to_message_id`.
- After acting, mark state with `interagents_mark_replied`,
  `interagents_mark_skipped`, or `interagents_mark_failed`.

Smoke test:

1. Configure the MCP server.
2. Add a `beforeSubmitPrompt` hook that calls `interagents_drain`.
3. Send Cursor a peer message while idle.
4. Submit an unrelated user prompt.
5. Confirm whether the drained peer message is visible to the model before it
   answers.

If the model cannot see the drained content automatically, keep this adapter at
`polling_only` and require explicit `interagents_drain`.
