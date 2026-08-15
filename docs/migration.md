# JSONL → SQLite Migration

Status: **dual-write, JSONL is the source of truth, cutover pending.**

This document tracks the phased evolution from a pure JSONL message log to a
SQLite-backed daemon, the current operational state, how to roll it back, how
to inspect the raw data, and per-adapter troubleshooting for the pieces this
migration touches. The full phase-by-phase design lives in
[`docs/proposals/mcp-sqlite-interagents-implementation-plan.md`](proposals/mcp-sqlite-interagents-implementation-plan.md);
this page is the operator-facing summary plus the rollback/export/troubleshoot
material that plan calls for in Phase 7.

## Where things stand today

| Phase | What it added | Status |
| :---- | :------------- | :----- |
| 0 – Baseline | Existing JSONL-only bus (`messages.log`, WebSocket server/client) | Shipped, unchanged |
| 1 – Schema | `interagents.sqlite3` schema (`sessions`, `messages`, `message_deliveries`) | Shipped (`skills/interagents/bin/storage.py`) |
| 2/3 – Dual-write | Every `send`/`broadcast` writes JSONL **and** SQLite from the shared server process | **Active** — see below |
| Drain read-with-fallback | `drain` reads SQLite pending deliveries first, falls back to JSONL cursor-based reads when SQLite has nothing pending | **Active** (`skills/interagents/bin/drain.py:132-181`) |
| MCP stdio | `mcp-stdio` tool surface (`interagents_*` tools) built on top of the same daemon | Shipped |
| 7 – Operational hardening | Stable export/debug path and reversible SQLite toggle, while retaining JSONL fallback | **Active** — export and toggle shipped; cutover intentionally deferred |

Nothing in the codebase implements "SQLite-only" mode. The design remains
deliberately reversible: JSONL is always written and is the compatibility
source of truth; SQLite is additive, best-effort state on top of it. Operators
can disable automatic SQLite access with `INTERAGENTS_SQLITE_ENABLED=false`.

### Who writes what, concretely

All persistence happens inside the single shared `server.py` process (one per
`host:port`, spawned lazily by the first `client.py` listener via
`skills/interagents/bin/spawn.py`). Per-session `client.py` processes are
WebSocket clients of that server; they do not write to disk themselves.

- `Server._log_message()` (`skills/interagents/bin/server.py:672-701`) is the
  **single writer** of `~/.olimpus/interagents/messages.log` (JSONL, one
  record per line, size-rotated at 50 MB / 5 backups —
  `shared.MESSAGES_LOG_MAX_BYTES`/`MESSAGES_LOG_BACKUPS` in
  `skills/interagents/bin/shared.py:206-207`). Called unconditionally from
  `_handle_send` and `_handle_broadcast`.
- `Server._persist_message()` / `_persist_session()` / `_touch_session()` /
  `_mark_session_disconnected()` (`server.py:773-865`) write the same events
  into `interagents.sqlite3` via `storage.py`. Each of these wraps its call in
  `try/except Exception` and only logs a warning on failure
  (`server.py:790-791`, `810-811`, `822-823`, `834-835`) — a SQLite write
  failure never blocks or fails the JSONL write or the message delivery.
- `Server.serve()` (`server.py:90-102`) opens the SQLite connection once at
  startup. If `storage.connect()` raises, the exception is caught, a warning
  `"sqlite persistence disabled: %s"` is logged, and `self._db` stays `None`
  for that server process's lifetime — every persistence call above becomes a
  no-op, and the bus keeps working purely on JSONL.

So today: SQLite persistence already fails open. The fallback the migration
plan calls for on the *read* side (`drain`) is implemented; the *write* side
degrades automatically if SQLite is unavailable.

## Rollback instructions

Use the explicit `INTERAGENTS_SQLITE_ENABLED` environment toggle when SQLite
must be disabled. It defaults to `true`; accepted false values are `0`,
`false`, `no`, and `off` (case-insensitive). Invalid values preserve the
current enabled default.

### 1. Stop supervisors (if installed)

Supervisors only exist if you opted into `docs/supervisors/`. If you never
installed one, skip to step 2.

```bash
# macOS launchd — label is "local.olimpus.interagents" (docs/supervisors/local.olimpus.interagents.plist:7)
launchctl stop local.olimpus.interagents
launchctl unload ~/Library/LaunchAgents/local.olimpus.interagents.plist
rm ~/Library/LaunchAgents/local.olimpus.interagents.plist   # optional, only if fully removing

# Linux systemd --user — unit is "olimpus-interagents.service"
systemctl --user disable --now olimpus-interagents.service
rm ~/.config/systemd/user/olimpus-interagents.service       # optional
systemctl --user daemon-reload
```

This only stops the supervisor from re-spawning a listener; it does not by
itself stop an already-running server or client.

### 2. Stop the running daemon/listener

Each AI agent session has its own `client.py` listener; there is one shared
`server.py` process per `host:port` that all listeners in that data dir talk
to and that owns the JSONL/SQLite writes.

Per-session listener (safe, only affects the current session):

```bash
python3 skills/interagents/bin/interagents.py disconnect
```

This resolves the listener's `client.py` pid from
`~/.olimpus/interagents/clients/<ppid>.session` and sends it `SIGTERM`
(`skills/interagents/bin/disconnect.py:37-67`); it refuses to kill anything
whose cmdline doesn't look like an interagents process
(`disconnect.py:25-34`).

Shared server process (stops the whole bus for that port — every connected
session loses its listener):

```bash
PORT=9474   # shared.DEFAULT_PORT; adjust if you run a non-default port
cat ~/.olimpus/interagents/server.$PORT.pid   # pid of the shared server (shared.py:88-95)
kill "$(cat ~/.olimpus/interagents/server.$PORT.pid)"
```

The server removes its own pidfile/meta on clean shutdown
(`server.py:148-170`). A fresh `connect` from any session will make
`spawn.ensure_server_running()` spawn a new server process on demand
(`skills/interagents/bin/spawn.py:35-93`) — the bus is not "gone", it restarts
lazily on next use.

### 3. Force SQLite persistence off for the shared server

The toggle controls automatic runtime persistence and SQLite-backed drains.
JSONL writes and cursor-based drains remain active.

```bash
# 1. Stop the shared server first (step 2 above); the toggle is read when the
#    replacement server starts.
kill "$(cat ~/.olimpus/interagents/server.9474.pid)" 2>/dev/null

# 2. Reconnect with the flag in the environment. spawn.py brings up a fresh
#    shared server in JSONL-only mode.
INTERAGENTS_SQLITE_ENABLED=false \
  python3 skills/interagents/bin/interagents.py connect \
  --name rollback-check --label test --daemon
sleep 2
grep "sqlite persistence disabled" ~/.olimpus/interagents/server.log
```

To undo: stop the shared server again and reconnect without the environment
variable (or with `INTERAGENTS_SQLITE_ENABLED=true`). The existing database
is preserved while the toggle is off.

### 4. Full code rollback (cleanest option)

Because SQLite persistence is purely additive (nothing removed the JSONL
path yet — that's what Phase 7 cutover would do, and it hasn't happened), the
cleanest rollback for a bad SQLite-related release is a normal git revert to
the commit/tag before `storage.py` was wired into `server.py`, `drain.py`, and
`state.py`, then reinstalling deps (`interagents.py install-deps`). This
avoids the manual file-shadowing trick above entirely and is the recommended
path for anything beyond a quick local test.

### 5. Verify the bus still works

After any of the above:

```bash
python3 skills/interagents/bin/interagents.py connect --name rollback-a --daemon
python3 skills/interagents/bin/interagents.py connect --name rollback-b --daemon
python3 skills/interagents/bin/interagents.py send rollback-b "ping"
# from rollback-b's session:
python3 skills/interagents/bin/interagents.py drain --limit 5
tail -n1 ~/.olimpus/interagents/messages.log   # the send always lands here regardless of SQLite state
python3 skills/interagents/bin/interagents.py disconnect
```

If `drain` still prints the message and `messages.log` has the record, the
bus is healthy — whether or not SQLite persistence is currently active.

## Inspecting state / export / debug

The dedicated export command emits stable JSON. It excludes runtime
credentials because they are not persisted and redacts untrusted message text
unless the operator explicitly opts in:

```bash
python3 skills/interagents/bin/interagents.py export
python3 skills/interagents/bin/interagents.py export --table sessions --limit 100
python3 skills/interagents/bin/interagents.py export --table messages --include-text
```

`--table` accepts `all`, `sessions`, `messages`, or `deliveries`; `--limit`
applies per selected table. Explicit export remains available while automatic
SQLite persistence is toggled off, so an existing database can be inspected
during rollback. Treat output produced with `--include-text` as sensitive.

### CLI helpers (SQLite-backed)

```bash
python3 skills/interagents/bin/interagents.py get-message <msg_id>      # full row as JSON, prefixed with an untrusted-content banner (state.py:46-49)
python3 skills/interagents/bin/interagents.py mark-read <msg_id>
python3 skills/interagents/bin/interagents.py mark-replied <msg_id> --reply-message-id <id>
python3 skills/interagents/bin/interagents.py mark-skipped <msg_id> --reason "..."
python3 skills/interagents/bin/interagents.py mark-failed <msg_id> --reason "..."
python3 skills/interagents/bin/interagents.py drain --peek --limit 50   # print pending without advancing the cursor / marking delivered
python3 skills/interagents/bin/interagents.py status                    # this session's name/session_id/host/port (list.py --self)
python3 skills/interagents/bin/interagents.py list                      # all connected sessions
```

`get-message` reads directly from `interagents.sqlite3`
(`skills/interagents/bin/state.py:52-62`) — it has no JSONL fallback, so it
only finds messages that were successfully dual-written.

### Reading the JSONL log directly

```bash
tail -f ~/.olimpus/interagents/messages.log
tail -n 20 ~/.olimpus/interagents/messages.log | jq .
grep '"kind": "broadcast"' ~/.olimpus/interagents/messages.log | jq .
```

Each line is one JSON record with `ts`, `msg_id`, `kind`, `from`, `from_name`,
`from_label`, `to`, `to_session_id`, `in_reply_to_message_id`, `text`
(`server.py:682-693`). Rotated backups are named `messages.log.1` .. `.5`
(oldest dropped first).

### Querying SQLite directly

```bash
DB=~/.olimpus/interagents/interagents.sqlite3
sqlite3 "$DB" ".tables"                       # sessions | messages | message_deliveries
sqlite3 -header -column "$DB" "select id, name, agent, status, receive_strategy, last_seen_at from sessions order by last_seen_at desc limit 20;"
sqlite3 -header -column "$DB" "select id, kind, from_name, to_name, created_at from messages order by seq desc limit 20;"
sqlite3 -header -column "$DB" "select message_id, session_id, delivery_state, disposition, delivered_at, read_at from message_deliveries order by rowid desc limit 20;"
```

Schema reference (`skills/interagents/bin/storage.py:52-121`):

- `sessions` — one row per connected/disconnected listener: `id`
  (session_id), `name`, `agent` (inferred from label/name — see
  `infer_agent`, `storage.py:125-130`), `receive_strategy`
  (`live_monitor`/`pre_turn_hook`/`polling_only`, `storage.py:133-140`),
  `status` (`active`/`disconnected`/`stale`), `capabilities_json`.
- `messages` — one row per direct/broadcast message; `scope` is `direct` or
  `broadcast`; `thread_id`/`in_reply_to_message_id` for correlation.
- `message_deliveries` — per-`(message_id, session_id)` delivery row:
  `delivery_state` (`pending`/`delivered`/`read`/`failed`), `disposition`
  (`none`/`question_sent`/`replied`/`skipped`/`failed`).

The DB uses WAL mode (`storage.py:31`), so you may also see `-wal`/`-shm`
sidecar files next to `interagents.sqlite3`; a plain `sqlite3` read (as above)
checkpoints transparently and does not need the server to be stopped.

## Troubleshooting by adapter

### Claude — `Monitor` doesn't start / no live notifications

- The plugin monitor is `interagents-client`
  (`monitors/monitors.json`), started per its `when` field: `always` or
  `on-skill-invoke:interagents` (lazy — only starts the first time
  `/interagents` runs in the session). Check current mode:
  ```bash
  python3 skills/interagents/bin/auto_start.py --status
  ```
  Toggle with `--on` (always) / `--off` (lazy); changes need
  `/reload-plugins` or a new session (`auto_start.py:107-119`).
- `${CLAUDE_PLUGIN_ROOT}` is a manifest-substitution token, **not** an
  exported env var — a literal `${CLAUDE_PLUGIN_ROOT}` inside a `Bash(...)`
  call expands to empty and silently breaks paths
  (`skills/interagents/SKILL.md:36-44`). Always resolve `<bin>` from the
  "Base directory for this skill" the harness prints instead.
- If `Monitor` was started but nothing prints, check whether the listener is
  actually connected: `Bash("python3 <bin>/list.py --self")`. `not connected`
  means the monitor process died or never registered — restart it via
  `TaskStop` + a fresh `Monitor(command="python3 <bin>/client.py ...")`.
- `install-deps` missing runtime deps (`websockets`/`psutil`) makes every
  entry point print `dependencies missing — run /interagents install-deps`
  and exit 1 (`list.py:142-146`); run `interagents.py install-deps` once.

### Codex / Kiro / opencode — messages aren't surfacing between turns

These hosts do not reliably keep monitor stdout attached to the model between
turns (`skills/interagents/bin/drain.py:1-6`), so a live listener receiving a
message is not enough — something has to call `drain` at the start of a turn.

- **Codex**: the adapter skill instructs running `drain --limit 50` at the
  start of any interagents-related turn
  (`adapters/codex/interagents/SKILL.md:29-31`). If a session never sees peer
  messages, confirm the daemon is actually up (`interagents.py status`) and
  that something (a hook, or the model itself per the skill instructions) is
  calling `drain`, not just `connect`.
- **Kiro**: relies on steering hooks — `SessionStart` to connect,
  `UserPromptSubmit` to drain, `Stop` to mark dispositions
  (`adapters/kiro/interagents.md:36-40`). If messages pile up, verify the
  `UserPromptSubmit` hook is registered and actually invokes `drain`; Kiro
  will otherwise behave like a connected-but-never-polled listener.
- **opencode**: default tier is `polling_only`
  (`adapters/opencode/interagents.md:6-9`); there is no hook path validated
  yet. Use explicit `drain` from the session, or run
  `interagents.py loop --interval-seconds 120` in the listener's terminal so
  pending messages get printed periodically without a manual `drain` call
  (`skills/interagents/bin/loop.py:25-30`). Don't run more than one `loop` per
  session — it's a plain polling cursor advance, not a fan-out.
- Common root cause for all three: the shared `server.py` process exits after
  `idle_shutdown_minutes` (default 10, `INTERAGENTS_IDLE_MINUTES` /
  `CLAUDE_PLUGIN_OPTION_IDLE_SHUTDOWN_MINUTES` env override,
  `client.py:424-427`) once no listener is connected. If a session reconnects
  after a long gap, the first `drain`/`send` triggers `spawn.py` to bring the
  server back up — expect a few seconds of latency on that first call.

### Cursor / Windsurf — hooks don't inject drained content → falls back to polling

Both adapters are MCP-stdio-only today, and both are explicitly documented as
**not yet validated** for hook-based delivery:

- **Cursor**: `beforeSubmitPrompt` is the candidate hook; until it's
  smoke-tested, treat Cursor as `polling_only` and call `interagents_drain`
  explicitly before interagent work (`adapters/cursor/interagents.md:6-9`).
- **Windsurf**: same pattern with `pre_user_prompt`
  (`adapters/windsurf/interagents.md:6-10`).

Symptom either way: the MCP server responds fine to `interagents_list_sessions`
/ `interagents_send`, but a peer message sent while the model is mid-turn
never appears without an explicit `interagents_drain` call. This is expected
given current adapter status, not a bug — the smoke test steps in each
adapter's `interagents.md` describe how to confirm whether the hook actually
injects drained content into model context versus only running as a
side-effect command; until that passes, keep calling `interagents_drain`
manually (or via `interagents_get_pending_count` first, to avoid an empty
drain call every turn).

Note that `storage.receive_strategy_for()` currently records `pre_turn_hook`
for both `cursor` and `windsurf` in the `sessions` table
(`skills/interagents/bin/storage.py:133-140`) — that's the aspirational
target, not confirmation the hook is wired up. Don't trust that column alone
when debugging these two hosts; follow the adapter doc's smoke test instead.

## What Phase 7 still needs

- Keep the JSONL read fallback until a separately approved cutover explicitly
  changes the compatibility contract.
- Collect operational evidence before considering SQLite as a source of truth.
