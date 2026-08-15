# opencode Interagents Adapter

Use MCP stdio with `adapters/opencode/opencode.json`.

Receive strategy:

- Default tier: `polling_only`.
- Spike plugin events before promoting to `pre_turn_hook`.
- Use `interagents_drain` explicitly or from an opencode plugin event.
- Use state tools after handling a message:
  - `interagents_mark_replied`
  - `interagents_mark_skipped`
  - `interagents_mark_failed`

Smoke test:

1. Configure MCP.
2. Add a plugin hook around prompt/session events.
3. Drain pending messages from the hook.
4. Confirm whether the drained content is appended to the model prompt or only
   logged as side-effect output.

If prompt append is unreliable, keep explicit polling as the supported path.
