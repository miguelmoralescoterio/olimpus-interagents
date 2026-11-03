# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Offline queue for direct messages (OLIMPUSSW-1360): a direct addressed to a
  known agent name with no live listener is stored in SQLite (`kind=queued`)
  and acknowledged with `op=queued` instead of failing with `unknown_peer`.
  It is pushed on the next reconnect under that name, tagged `queued`.
- launchd supervisor template now runs the listener in the foreground with
  `KeepAlive`, keeping the bus up from login.

### Changed
- Live websocket pushes now mark the delivery `read` for live stdout monitors
  (Claude) and `delivered` for hook/polling hosts.
- `catch_up_direct` is bounded by `INTERAGENTS_DIRECT_CATCHUP_SECONDS`
  (default 24h) and skips messages already read by any session; previously
  every reconnect re-attached the whole unread history.
- `SKILL.md`: Claude `Monitor` uses `timeout_ms=1800000` (hard 30-minute cap,
  no `persistent` option), `--label claude`, and re-arms with the same
  `--name` on expiry.
