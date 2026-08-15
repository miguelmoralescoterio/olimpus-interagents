# MCP + SQLite Interagents Implementation Plan

## Status

In progress. Milestone 1 is mostly implemented in the repo, with JSONL still
kept as a compatibility fallback.

Implemented so far:

- Poetry development setup.
- MCP stdio server with Content-Length framing.
- Periodic `loop --interval-seconds 120` drain fallback for hosts without
  reliable monitor/hook delivery.
- SQLite schema, WAL, indexes, sessions, messages, deliveries.
- Dual-write JSONL + SQLite from the WebSocket server.
- SQLite-first `drain` with JSONL fallback.
- CLI and MCP tools for message state transitions.
- `in_reply_to_message_id` propagation from CLI/MCP through WebSocket to
  SQLite.
- Automatic `replied` / `question_sent` disposition updates for correlated
  `answer:`, `done:`, and `question:` replies.
- `disconnect` CLI/MCP helper.
- Heartbeat and pid-based stale marking in SQLite.
- Configurable late-joiner broadcast catch-up through
  `INTERAGENTS_BROADCAST_CATCHUP_SECONDS`.
- Transport failure retry helpers that do not retry agent dispositions such as
  `skipped`.
- Runtime metadata capture for parent pid and terminal/IDE container hints.
- Supervisor examples for `launchd` and `systemd --user`.
- Adapter config examples for Kiro, Cursor, Windsurf, and opencode.

## Objective

Evolve `olimpus-interagents` from a WebSocket + JSONL helper into a shared
local daemon with SQLite persistence, MCP tools, and agent-specific receive
adapters for Claude, Codex, Kiro, opencode, Cursor, and Windsurf.

## Design Inputs

- Proposal: `docs/proposals/mcp-sqlite-interagents.md`
- Peer review: `docs/proposals/mcp-sqlite-interagents-review-milk.md`
- Existing CLI and WebSocket behavior must remain compatible during migration.
- MCP should expose tools over the daemon; it should not own global state.
- Receive behavior must be capability-based:
  - `live_monitor`
  - `pre_turn_hook`
  - `polling_only`

## Phase 0 - Baseline and Safety

Deliverables:

- Document current CLI commands and expected outputs:
  - `connect`
  - `status`
  - `list`
  - `send`
  - `broadcast`
  - `drain`
- Add or update regression tests for existing JSONL/WebSocket behavior before
  introducing SQLite.
- Add fixtures for direct and broadcast message flows.

Acceptance:

- Existing tests pass.
- A manual two-session smoke test works with the current CLI.
- No existing command semantics change.

## Phase 1 - SQLite Storage

Deliverables:

- Add storage module with migrations.
- Create tables:
  - `sessions`
  - `messages`
  - `message_deliveries`
- Enable SQLite WAL mode.
- Add indexes for drain, thread lookup, and reply correlation.
- Add storage tests for:
  - direct message delivery rows
  - broadcast delivery rows
  - late joiner catch-up
  - `delivered` versus `read`
  - `disposition` transitions
  - `in_reply_to_message_id`
  - stale session cleanup

Acceptance:

- Schema can initialize from an empty data directory.
- Migrations are idempotent.
- Drain query returns only direct messages for the current session and
  broadcast messages addressed to all active recipients.

## Phase 2 - Daemon Persistence

Deliverables:

- Introduce `interagentsd` as the state owner.
- Keep existing localhost WebSocket protocol compatible.
- Move session registration and message persistence behind daemon APIs.
- Start dual-write to JSONL and SQLite as soon as the daemon performs the
  first SQLite write. JSONL remains the source of truth until the final cutover.
- Add heartbeat and stale detection using pid liveness plus grace timeout.
- Add automatic leader re-election: when a client cannot connect to the daemon,
  it attempts to win the bind with retry and backoff.
- Add user-level supervisor examples for daemon respawn:
  - `launchd` on macOS
  - `systemd --user` on Linux
- Add crash recovery tests around committed SQLite state.

Acceptance:

- First process binds the daemon port.
- Later clients connect to the existing daemon.
- If the daemon dies, the next client can elect a replacement.
- Supervisor examples can restart the daemon without changing CLI behavior.
- A daemon restart preserves committed sessions/messages.
- Stale sessions are marked without deleting historical message records.
- JSONL and SQLite contain equivalent message records while dual-write is
  enabled.

## Phase 3 - CLI Migration

Deliverables:

- Convert current CLI commands to daemon-backed storage.
- Keep JSONL read fallback until parity is proven.
- Keep compatibility session stubs for old helper behavior.
- Add dedupe by message id.

Acceptance:

- Existing CLI scripts continue to work.
- SQLite and JSONL contain equivalent messages during dual-write.
- Repeated drains do not duplicate message handling.

## Phase 4 - MCP Server

Deliverables:

- Add `interagents-mcp` server.
- Support stdio first. Streamable HTTP is deferred until there is a real
  multi-host use case.
- Expose tools:
  - `interagents_connect`
  - `interagents_disconnect`
  - `interagents_status`
  - `interagents_list_sessions`
  - `interagents_send`
  - `interagents_broadcast`
  - `interagents_drain`
  - `interagents_mark_read`
  - `interagents_mark_replied`
  - `interagents_mark_skipped`
  - `interagents_get_message`
  - `interagents_get_pending_count`
- Expose resources:
  - `interagents://sessions`
  - `interagents://messages/pending`
  - `interagents://messages/{message_id}`
  - `interagents://session/self`
- Label peer message content as untrusted in tool responses.

Acceptance:

- MCP tools work against the same daemon as the CLI.
- Tool responses include correlation fields needed for replies.
- MCP cannot execute peer instructions automatically.

## Phase 5 - Adapter Spikes and Implementations

Deliverables:

- Claude:
  - plugin monitor or Monitor command adapter
  - polling fallback when Monitor is unavailable
  - batching so Monitor traffic does not wake the model once per raw bus
    message
  - optional MCP config
- Codex:
  - spike hook behavior before relying on prompt/context injection
  - plugin MCP config
  - plugin hooks for `SessionStart`, `UserPromptSubmit`, and `Stop`
  - skill instruction updates
- Kiro:
  - spike `UserPromptSubmit` behavior before relying on agent-prompt injection
  - `.kiro/hooks` examples for `SessionStart`, `UserPromptSubmit`, `Stop`
  - MCP config example
  - steering update
- opencode:
  - spike plugin event behavior before writing the full adapter
  - plugin event drain
  - MCP config example
  - skill/rules update
- Cursor:
  - spike `beforeSubmitPrompt` before writing the full adapter
  - MCP config example
  - hook/rule examples, especially `beforeSubmitPrompt`
- Windsurf:
  - spike `pre_user_prompt` before writing the full adapter
  - MCP config example
  - hook/rule examples for `pre_user_prompt` and `post_cascade_response`

Acceptance:

- Spike results are recorded before adapter implementation for uncertain hosts.
- Each adapter declares its capability tier.
- Each adapter can register session metadata:
  - agent
  - name
  - profile
  - project path
  - pid
  - container pid/kind when discoverable
- Each adapter has at least one documented drain path.

## Phase 6 - Live Behavior Regression Tests

Deliverables:

- Claude live monitor smoke test.
- Codex pre-turn hook smoke test.
- Kiro `UserPromptSubmit` hook smoke test.
- Cursor `beforeSubmitPrompt` hook smoke test.
- Windsurf `pre_user_prompt` hook smoke test.
- opencode plugin event smoke test.

Acceptance:

- Test proves whether drained content reaches model context.
- Test result updates the adapter capability tier.
- Any failed live path falls back to `polling_only` or explicit MCP drain.

## Phase 7 - Cleanup and Cutover

Deliverables:

- Remove JSONL read fallback after a transition release.
- Keep optional export/debug command for JSONL-style inspection.
- Update README, plugin docs, skills, and adapter docs.
- Add migration notes and rollback instructions.

Acceptance:

- CLI and MCP both operate only from SQLite-backed daemon state.
- Documentation explains how to install, start, test, and troubleshoot each
  agent adapter.
- Rollback path is documented before removing transitional code.

## Open Questions for Review

- Do Cursor and Windsurf hooks reliably inject drained messages into model
  context, or only run side-effect commands?

## Resolved Scope Decisions

- Background loops belong to adapters or host plugins, not the daemon.
- MCP starts with stdio only. Streamable HTTP is deferred until a multi-host use
  case exists.
- Cursor, Windsurf, and opencode adapters require spikes before full
  implementation.
- JSONL dual-write starts in Phase 2 and JSONL remains the transition source of
  truth until cutover.
- Daemon leader re-election and supervisor examples are part of the first
  daemon milestone, not optional cleanup.
- CLI `drain` preserves the previous "emit once" behavior: it reads pending
  SQLite deliveries first, marks them `delivered`, advances the JSONL cursor,
  and falls back to JSONL when no SQLite pending rows exist.
- Broadcast catch-up is opt-in through
  `INTERAGENTS_BROADCAST_CATCHUP_SECONDS`; default is `0`.
- Reply correlation is implemented with `in_reply_to_message_id`; automatic
  disposition updates only run when correlation is present.

## Additional Required Tests

- Race test for two clients starting simultaneously and competing for the
  daemon bind.
- Reconnection with the same `name` after disconnect, including pending
  message recovery.
- Stable ordering for messages with identical timestamps using `seq`.
- Poison message handling so one failing message does not block the recipient
  queue.
- Redelivery of transport-level `failed` messages without retrying agent
  disposition failures such as `skipped`.
- `kill -9` daemon recovery during or immediately after a write, validating WAL
  recovery and no database corruption.

## Milestone 1

The smallest useful and reversible milestone is:

```text
Phase 0 + Phase 1 + Phase 2 partial:
baseline tests, SQLite schema, daemon persistence, leader re-election,
dual-write JSONL+SQLite, and unchanged observable CLI behavior.
```

Milestone 1 intentionally excludes MCP and new adapters. It proves the schema
and delivery state with real traffic while JSONL remains the safety net.

## Requested Peer Contributions

- Validate the phase order.
- Identify missing tests.
- Challenge any incorrect assumptions about a specific agent.
- Add implementation details for the agent you are running in.
- Propose the smallest useful milestone that can be shipped first.
