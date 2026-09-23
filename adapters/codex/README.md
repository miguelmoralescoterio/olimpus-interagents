# Persistent Codex monitor

The monitor connects to the existing Interagents WebSocket bus as an agent,
executes incoming requests in a dedicated Codex app-server thread, and sends
the final answer to the originating peer. It uses the existing Codex login.
It does not inject messages into an already-open desktop chat.

```bash
python3 adapters/codex/monitor.py --name codex-monitor --cwd /absolute/workspace
```

From a connected Interagents session:

```text
/interagents send codex-monitor Revisa los tests del proyecto y reporta el resultado
```

Inspect the saved conversation and delivery counters:

```bash
python3 adapters/codex/monitor.py --name codex-monitor --cwd /absolute/workspace --status
```

State is private to `~/.olimpus/interagents/codex-monitors/<name>/`.
`--status` reports persisted state, not process liveness. Only one instance of
each monitor name can run. Use a different name for a different workspace.
`--allow-peer milk` optionally restricts incoming requests by registered peer
name; repeat the option for additional peers. By default authenticated local
peers can send requests, consistent with the Interagents skill policy.

Messages are handled serially. Incoming `done:`, `status:`, and `answer:` are
informational and never trigger another reply. All replies carry a textual
`[reply-to:<id>]` marker as well as `in_reply_to_message_id`, since older bus
versions discard the protocol field. Older versions also lack SQLite: the
adapter persists incoming WebSocket payloads and checks the server's JSONL
log to confirm outgoing messages. Confirmation means the bus accepted and
recorded the reply, not that the receiving agent read it.

The adapter inherits Codex permissions. Interactive approvals are declined,
and the reply reports that intervention is needed. No automatic permission
escalation occurs. A failed or timed-out turn stops the process, leaving a
durable error reply for the next start. The default timeout is 1800 seconds.

After a crash, an uncertain execution is reported and never automatically
repeated. Review the saved Codex conversation before re-sending the task.
Replies queued for an offline sender remain pending until that same sender
session reconnects. Direct-message recovery on legacy buses uses the current
JSONL file: messages lost across log rotation or broadcasts missed while
disconnected are not guaranteed to recover. This is not exactly-once delivery.

## macOS service installed locally

The local service is `com.olimpus.interagents.codex-monitor`. It starts at login
and restarts after exit. Logs contain operational IDs, not message bodies.

```bash
launchctl print gui/$(id -u)/com.olimpus.interagents.codex-monitor
launchctl bootout gui/$(id -u)/com.olimpus.interagents.codex-monitor
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/com.olimpus.interagents.codex-monitor.plist"
```

`bootout` stops the current service. To also prevent starting at next login:

```bash
launchctl disable gui/$(id -u)/com.olimpus.interagents.codex-monitor
```

Use `launchctl enable` with the same target before starting it again.

Protocol reference: https://developers.openai.com/es-419/docs/app-server
