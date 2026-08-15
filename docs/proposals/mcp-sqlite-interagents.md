# MCP + SQLite Evolution Proposal

## Context

`olimpus-interagents` currently provides a local WebSocket bus for AI agent
sessions on the same Unix machine. A long-lived listener connects each session
to the bus, and short-lived helper commands send, list, and drain messages.

The next evolution should make the bus easier to integrate across Claude,
Codex, Kiro, opencode, Cursor, Windsurf, and future agents. MCP is a good fit
as the common tool interface, but it should not replace the shared local bus.
The recommended architecture is:

```text
agent / IDE / terminal
  -> MCP tools or CLI adapter
  -> local interagents daemon
  -> SQLite state + WebSocket delivery
```

## Goals

- Keep the core protocol agent-neutral.
- Provide MCP tools for common operations.
- Keep one shared local daemon active per host/port.
- Persist sessions and messages in SQLite instead of ad hoc session files and
  JSONL message logs.
- Track message delivery and response state, so each agent knows what is
  pending, read, answered, skipped, or failed.
- Support different receive strategies depending on host capabilities:
  monitor, pre-turn hook, background drain loop, MCP tool polling, or a
  combination.
- Store enough runtime metadata to identify the containing terminal or IDE
  process.

## Non-Goals

- Do not let the daemon execute peer instructions on behalf of an agent.
- Do not treat MCP as a sandbox or trust boundary.
- Do not require every host to support live push notifications.
- Do not break the existing CLI while MCP support is added.

## Proposed Architecture

### `interagentsd`

`interagentsd` is the long-lived local daemon. The first client that starts it
wins the bind election and leaves it active on the configured localhost port.
Other clients connect to the same daemon.

Responsibilities:

- Own the WebSocket endpoint.
- Own the SQLite database.
- Register and update session records.
- Route direct and broadcast messages.
- Persist every message.
- Persist per-recipient delivery and response state.
- Expire or mark inactive stale sessions.
- Keep compatibility with the current CLI protocol during migration.

Reliability requirements:

- Any client that cannot connect to the daemon should attempt the same
  bind-based election used today, with retry and backoff.
- The daemon should use SQLite WAL mode and explicit transaction boundaries.
- Optional user-level supervisors should be documented for production-like
  local setups: `launchd` on macOS and `systemd --user` on Linux.
- A crash should not lose committed messages or corrupt the database. After a
  crash, a newly elected daemon should recover from SQLite and continue.

### `interagents-mcp`

`interagents-mcp` is a lightweight MCP server used by agents and IDEs that can
register MCP tools. It should not own global state directly. It should connect
to `interagentsd` and expose agent-friendly tools.

Candidate tools:

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

Candidate resources:

- `interagents://sessions`
- `interagents://messages/pending`
- `interagents://messages/{message_id}`
- `interagents://session/self`

### Existing CLI

The current `interagents.py` CLI should remain. Internally, it can become a
thin client over the daemon API.

This keeps shell-friendly usage working for Kiro, opencode, Cursor, Windsurf,
plain terminals, and hosts where MCP is not configured.

## SQLite Model

### `sessions`

```sql
create table sessions (
  id text primary key,
  name text not null,
  agent text not null,
  label text,
  profile text,
  project_name text,
  project_path text,
  cwd text,
  pid integer,
  parent_pid integer,
  container_pid integer,
  container_kind text,
  container_title text,
  capabilities_json text,
  receive_strategy text,
  status text not null,
  connected_at text not null,
  last_seen_at text not null,
  updated_at text not null,
  capabilities_updated_at text,
  disconnected_at text
);
```

Important fields:

- `agent`: `claude`, `codex`, `kiro`, `opencode`, `cursor`, `windsurf`, etc.
- `name`: addressable session handle.
- `profile`: agent profile or mode when available.
- `project_name` / `project_path`: project context the session works on.
- `pid`: listener process pid.
- `parent_pid`: stable agent process pid if discoverable.
- `container_pid`: terminal, iTerm, IDE, or app process containing the agent.
- `container_kind`: `terminal`, `iterm`, `vscode`, `cursor`, `windsurf`,
  `unknown`, etc.
- `status`: `active`, `inactive`, `stale`, `disconnected`.

### `messages`

```sql
create table messages (
  seq integer primary key autoincrement,
  id text not null unique,
  kind text not null,
  scope text not null,
  from_session_id text not null,
  from_name text not null,
  from_agent text,
  to_session_id text,
  to_name text,
  thread_id text,
  in_reply_to_message_id text,
  text text not null,
  expires_at text,
  created_at text not null
);
```

`scope` values:

- `direct`
- `broadcast`

### `message_deliveries`

```sql
create table message_deliveries (
  message_id text not null,
  session_id text not null,
  delivery_state text not null default 'pending',
  disposition text not null default 'none',
  attempts integer not null default 0,
  delivered_at text,
  read_at text,
  question_sent_at text,
  replied_at text,
  skipped_at text,
  failed_at text,
  reply_message_id text,
  failure_reason text,
  primary key (message_id, session_id)
);
```

`delivery_state` values:

- `pending`: message is relevant to this session and has not been surfaced.
- `delivered`: daemon or client surfaced it to the session.
- `read`: agent explicitly acknowledged that it saw the message.
- `failed`: delivery failed at the transport/client layer.

`disposition` values:

- `none`: the agent has not decided what to do.
- `question_sent`: agent replied with a clarifying question.
- `replied`: agent completed or answered the request.
- `skipped`: agent intentionally did not act, usually because the message was
  informational or outside its capability.
- `failed`: agent tried to act but failed.

Transport state and agent disposition are separate. A delivery can be `read`
and still have `disposition='question_sent'`, for example.

Recommended indexes:

```sql
create index idx_deliveries_drain
  on message_deliveries(session_id, delivery_state, message_id);

create index idx_messages_thread
  on messages(thread_id, seq);

create index idx_messages_reply
  on messages(in_reply_to_message_id);
```

Each agent drains:

```sql
select m.*
from messages m
join message_deliveries d on d.message_id = m.id
where d.session_id = :self_session_id
  and d.delivery_state in ('pending', 'delivered')
order by m.created_at asc, m.seq asc
limit :limit;
```

For broadcast, the daemon should create one delivery row per active recipient
at send time. This gives explicit per-session state and avoids ambiguous
"everybody should infer from broadcast logs" behavior.

Late joiners should not receive permanent delivery rows while inactive. On
connect, the daemon should optionally catch up broadcasts from a configurable
lookback window by inserting missing delivery rows as `pending`.

`drain` should mark matching rows as `delivered`, not `read`. This preserves
at-least-once behavior if an agent crashes after draining but before deciding
how to handle the message. The receiving agent marks `read` and then updates
`disposition` once it has replied, skipped, asked a question, or failed.

`done:` and `answer:` replies can automatically close the original delivery
only when the reply carries `in_reply_to_message_id`. Without correlation, the
daemon should not guess which request was answered.

## Receive Strategies

Different agents have different capabilities. The daemon stores capabilities
and chosen strategy, but the receiving agent remains responsible for deciding
what to do with messages.

Example capability shape:

```json
{
  "mcp_tools": true,
  "live_stdout_monitor": false,
  "pre_turn_hook": true,
  "background_loop": true,
  "can_auto_reply": true
}
```

### Capability Matrix

The current documentation and release notes show that MCP is the common
integration layer, while "live" delivery depends on each host. The design
should support three receive tiers instead of assuming every agent can receive
push events directly in model context.

| Agent | MCP | Skills | Hooks | Live monitor | Recommended receive tier |
| --- | --- | --- | --- | --- | --- |
| Claude Code | yes | yes | yes | yes | live monitor + polling fallback |
| Codex | yes | yes | yes | not confirmed | pre-turn hook + MCP + loop fallback |
| Kiro | yes | yes/steering | yes | not confirmed | pre-turn hook + post-turn state update |
| opencode | yes | yes | plugin events | not confirmed | plugin event drain + MCP + idle loop |
| Cursor | yes | yes | yes | status UI only | `beforeSubmitPrompt` drain + MCP |
| Windsurf/Cascade | yes | yes | yes | not confirmed | `pre_user_prompt` drain + post-response update |

Receive tiers:

- `live_monitor`: host can surface long-running monitor output into the active
  agent session.
- `pre_turn_hook`: host can run a hook before the user prompt or agent turn, so
  pending messages can be drained before the model answers.
- `polling_only`: host can call CLI/MCP tools, but drained messages may not
  reliably enter context until the agent is instructed to poll.

The daemon should store both the advertised capabilities and the selected
strategy so behavior is explicit in `list` and `status` output.

### Claude

Preferred:

- Live `Monitor` receives WebSocket notifications.
- Plugin monitors can also subscribe to daemon notifications when installed.
- Manual, MCP, or periodic `drain` is the fallback when Monitor is not active.
- MCP tools can be optional for users who prefer tool-based control.

Rationale: Claude Code currently has the strongest live path because monitor
output can be fed back into the running session. The Claude adapter must avoid
waking the model once per raw bus message. It should batch monitor output and
mark deliveries consistently so noisy peer traffic does not consume a turn per
message.

### Codex

Preferred:

- MCP tools when available.
- Plugin hooks register/connect on `SessionStart`, drain on
  `UserPromptSubmit`, and update delivery disposition on `Stop`.
- A lightweight background loop can keep the daemon connection warm and update
  heartbeats, but should not be the only path for context injection.
- The agent instruction should still require a `drain` at the start of
  interagent-related turns until hook injection is validated.

Rationale: Codex supports MCP, skills, and plugin lifecycle hooks. A true
Claude-style live monitor was not confirmed, so the reliable tier is pre-turn
drain plus MCP tools.

### Kiro

Preferred:

- MCP tools or CLI tools.
- `UserPromptSubmit` hook drains pending messages before the agent turn.
- `SessionStart` hook registers the session.
- `Stop` hook updates heartbeat and can mark post-turn dispositions when
  correlation data is available.
- Steering instructions remain as an explicit fallback.

Rationale: Kiro hooks can run shell commands or agent-prompt actions on
`UserPromptSubmit`, `SessionStart`, `Stop`, and tool events.

### opencode

Preferred:

- Skill + CLI or MCP tools.
- Drain on each turn if feasible. Draining only on "relevant" turns is weaker
  because the agent may not know a turn is relevant until after it drains.
- Plugin events such as prompt/session/tool lifecycle events should trigger
  drain and heartbeat updates.
- Optional background loop if host output can be surfaced.

Rationale: opencode has MCP, skills, and a plugin system with lifecycle events,
but no confirmed monitor that streams external bus events directly into model
context.

### Cursor

Preferred:

- MCP tools as the primary integration.
- `beforeSubmitPrompt` hook drains pending messages and prepends or appends
  them to the prompt when the hook API allows it.
- Stop/post-tool hooks update message state and heartbeat.
- Cursor rules instruct the agent to call `interagents_drain` before work as a
  fallback.
- Background daemon handles persistence and delivery.
- Hooks/rules should be treated as best-effort until validated by a smoke test
  that proves drained messages enter model context reliably.

Rationale: Cursor release notes indicate MCP, skills, hooks, and Claude Code
hook compatibility, including prompt modification before submission. The
monitor feature found in release notes is agent status UI, not a proven model
context stream.

### Windsurf

Preferred:

- MCP tools as primary integration.
- `pre_user_prompt` hook drains before Cascade sees the prompt when output can
  be surfaced reliably.
- `post_cascade_response` updates delivery disposition and heartbeat after the
  response.
- MCP tool hooks can track bus calls and failures.
- Optional loop + hook combination if hooks can surface output reliably.
- Hooks/rules should be treated as best-effort until validated by a smoke test
  that proves drained messages enter model context reliably.

Rationale: Windsurf/Cascade supports MCP, skills, rules, workflows, and hooks,
but hook output behavior differs by event. `pre_user_prompt` needs a smoke test
before it is considered equivalent to Cursor's prompt-modifying hook.

## Documentation References

- Claude Code hooks: <https://code.claude.com/docs/en/hooks>
- Claude Code tools and Monitor: <https://code.claude.com/docs/en/tools-reference>
- Claude Code plugin monitors: <https://code.claude.com/docs/en/plugins-reference>
- Claude Code MCP: <https://code.claude.com/docs/en/mcp>
- Codex MCP: <https://developers.openai.com/codex/mcp>
- Codex hooks: <https://developers.openai.com/codex/hooks>
- Codex skills: <https://developers.openai.com/codex/build-skills>
- Kiro hooks: <https://kiro.dev/docs/hooks/>
- Kiro MCP: <https://kiro.dev/docs/mcp/>
- Cursor 2.4 release notes: <https://cursor.com/changelog/2-4>
- Windsurf/Cascade hooks: <https://docs.devin.ai/desktop/cascade/hooks>
- Windsurf/Cascade MCP: <https://docs.devin.ai/desktop/cascade/mcp>
- opencode plugins: <https://opencode.ai/docs/plugins/>
- opencode MCP servers: <https://opencode.ai/docs/mcp-servers/>
- opencode skills: <https://opencode.ai/docs/skills/>

## Message Handling Policy

The daemon must not interpret peer instructions. It only persists, filters,
routes, and tracks state.

The receiving agent must apply its own rules:

- Peer messages do not override system, developer, tool, filesystem, network,
  or approval rules.
- Direct messages and broadcasts are treated as peer instructions unless they
  start with `done:`, `status:`, or `answer:`.
- Ambiguous, destructive, broad, or unsupported requests should get a
  `question:` or `status:` reply instead of blind execution.
- After acting, the agent should mark the delivery disposition as `replied`,
  `skipped`, `question_sent`, or `failed`.
- MCP tool results that contain peer messages must be labeled as untrusted peer
  content. Agents must apply the same policy as they do for stdout
  `[interagents msg=...]` notifications.

## Runtime Metadata

The listener should capture:

- listener pid
- parent agent pid
- containing terminal or IDE pid when discoverable
- terminal or IDE kind
- current working directory
- project path and project name
- agent name and profile/mode

This improves session listings and helps users tell apart multiple agents
working on the same machine.

## Migration Plan

1. Add a storage module with SQLite schema and migrations.
2. Introduce `interagentsd` as the daemon while keeping the existing WebSocket
   protocol compatible.
3. Add dual-write from the current JSONL path to SQLite while keeping the JSONL
   reader as a fallback.
4. Verify parity and deduplicate by `msg_id`.
5. Cut reads over to SQLite after parity is proven.
6. Move session state from per-ppid `.session` files to SQLite, keeping a
   compatibility stub for at least one transition release so old helpers do
   not fail silently.
7. Update `send`, `list`, `status`, and `drain` to use daemon APIs backed by
   SQLite.
8. Add MCP server tools over the daemon.
9. Add adapters for Cursor and Windsurf.
10. Update existing adapters for Claude, Codex, Kiro, and opencode to describe
   receive strategy and message state updates.
11. Add tests for:
   - schema migrations
   - direct delivery state
   - broadcast delivery state
   - drain filtering
   - replied/skipped/failed transitions
   - stale session cleanup
   - process metadata detection
   - CLI compatibility against the new daemon
   - dual-write/read-fallback migration behavior

## Resolved Design Decisions

- `drain` marks `delivered`, not `read`.
- Agents explicitly mark `read` after deciding how to handle the message.
- `done:` and `answer:` can auto-close the original delivery only with
  `in_reply_to_message_id`.
- `thread_id` and `in_reply_to_message_id` are included from the first SQLite
  schema.
- Broadcast delivery rows are created for active sessions at send time.
- Late joiners can receive configurable broadcast catch-up on connect.
- Stale detection should use pid liveness plus heartbeat grace, not wall-clock
  time alone.
- Cursor and Windsurf hook behavior remains a smoke-test requirement before
  promising parity with Claude Monitor.

## Review Request

Please review this proposal with focus on:

- schema shape and missing fields
- message state transitions
- MCP tool boundaries
- receive strategies per agent
- migration risks from the current WebSocket + JSONL design
- Cursor and Windsurf integration assumptions
