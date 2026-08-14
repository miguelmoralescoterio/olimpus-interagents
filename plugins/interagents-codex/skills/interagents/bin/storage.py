"""SQLite persistence for interagents.

JSONL remains the compatibility source of truth during the transition. This
module provides best-effort structured persistence for sessions, messages, and
per-recipient delivery state.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from bin import shared

SCHEMA_VERSION = 1
SQLITE_ENABLED_ENV = "INTERAGENTS_SQLITE_ENABLED"
REDACTED_MESSAGE_TEXT = "<redacted; pass --include-text to export message text>"


def db_path() -> Path:
    return shared.data_dir() / "interagents.sqlite3"


def sqlite_enabled() -> bool:
    """Return whether runtime SQLite persistence is enabled.

    The current dual-write behavior remains the default. Invalid values fail
    open to that default so a typo cannot silently turn persistence off.
    """
    raw = os.environ.get(SQLITE_ENABLED_ENV)
    if raw is None:
        return True
    normalized = raw.strip().lower()
    if normalized in {"0", "false", "no", "off"}:
        return False
    if normalized in {"1", "true", "yes", "on"}:
        return True
    return True


def connect(path: Path | None = None, *, force: bool = False) -> sqlite3.Connection:
    if not force and not sqlite_enabled():
        raise RuntimeError(f"SQLite persistence disabled by {SQLITE_ENABLED_ENV}")
    target = path or db_path()
    shared.secure_dir(target.parent)
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    conn.execute("pragma journal_mode = wal")
    conn.execute("pragma synchronous = normal")
    migrate(conn)
    try:
        shared.secure_file(target)
    except OSError:
        pass
    return conn


def export_state(
    conn: sqlite3.Connection,
    *,
    table: str = "all",
    limit: int = 1000,
    include_text: bool = False,
) -> dict[str, object]:
    """Export persisted state in a stable, secret-free JSON-ready shape."""
    allowed = {"all", "sessions", "messages", "deliveries"}
    if table not in allowed:
        raise ValueError(f"invalid export table: {table}")
    row_limit = max(1, limit)
    result: dict[str, object] = {"schema_version": SCHEMA_VERSION}

    if table in {"all", "sessions"}:
        result["sessions"] = [
            dict(row)
            for row in conn.execute(
                "select * from sessions order by connected_at asc, id asc limit ?",
                (row_limit,),
            )
        ]
    if table in {"all", "messages"}:
        messages = [
            dict(row)
            for row in conn.execute(
                "select * from messages order by seq asc limit ?",
                (row_limit,),
            )
        ]
        if not include_text:
            for message in messages:
                message["text"] = REDACTED_MESSAGE_TEXT
        result["messages"] = messages
    if table in {"all", "deliveries"}:
        result["deliveries"] = [
            dict(row)
            for row in conn.execute(
                """
                select * from message_deliveries
                order by message_id asc, session_id asc
                limit ?
                """,
                (row_limit,),
            )
        ]
    return result


def migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("pragma user_version").fetchone()[0]
    if current < 1:
        _migrate_001(conn)
        conn.execute(f"pragma user_version = {SCHEMA_VERSION}")
    elif current > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {current} is newer than supported {SCHEMA_VERSION}"
        )


def _migrate_001(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table if not exists sessions (
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

        create table if not exists messages (
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

        create table if not exists message_deliveries (
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

        create index if not exists idx_deliveries_drain
          on message_deliveries(session_id, delivery_state, message_id);

        create index if not exists idx_messages_thread
          on messages(thread_id, seq);

        create index if not exists idx_messages_reply
          on messages(in_reply_to_message_id);
        """
    )


def infer_agent(label: str, name: str = "") -> str:
    for value in (label, name):
        cleaned = (value or "").strip().lower()
        if cleaned in {"claude", "codex", "kiro", "opencode", "cursor", "windsurf"}:
            return cleaned
    return "unknown"


def receive_strategy_for(agent: str) -> str:
    if agent == "claude":
        return "live_monitor"
    if agent in {"codex", "kiro", "cursor", "windsurf"}:
        return "pre_turn_hook"
    if agent == "opencode":
        return "polling_only"
    return "polling_only"


def capabilities_for(agent: str) -> dict[str, object]:
    return {
        "mcp_tools": True,
        "live_stdout_monitor": agent == "claude",
        "pre_turn_hook": agent in {"codex", "kiro", "cursor", "windsurf"},
        "background_loop": True,
        "can_auto_reply": True,
    }


def upsert_session(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    name: str,
    label: str,
    cwd: str,
    pid: int,
    connected_at: str,
    parent_pid: int = 0,
    container_pid: int = 0,
    container_kind: str = "unknown",
    container_title: str = "",
    last_seen_at: str | None = None,
) -> None:
    agent = infer_agent(label, name)
    project_path = cwd or ""
    project_name = Path(project_path).name if project_path else ""
    now = last_seen_at or connected_at
    conn.execute(
        """
        insert into sessions (
          id, name, agent, label, project_name, project_path, cwd, pid,
          parent_pid, container_pid, container_kind, container_title,
          capabilities_json, receive_strategy, status, connected_at,
          last_seen_at, updated_at, capabilities_updated_at, disconnected_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, null)
        on conflict(id) do update set
          name = excluded.name,
          agent = excluded.agent,
          label = excluded.label,
          project_name = excluded.project_name,
          project_path = excluded.project_path,
          cwd = excluded.cwd,
          pid = excluded.pid,
          parent_pid = excluded.parent_pid,
          container_pid = excluded.container_pid,
          container_kind = excluded.container_kind,
          container_title = excluded.container_title,
          capabilities_json = excluded.capabilities_json,
          receive_strategy = excluded.receive_strategy,
          status = 'active',
          last_seen_at = excluded.last_seen_at,
          updated_at = excluded.updated_at,
          capabilities_updated_at = excluded.capabilities_updated_at,
          disconnected_at = null
        """,
        (
            session_id,
            name,
            agent,
            label,
            project_name,
            project_path,
            cwd,
            pid,
            parent_pid,
            container_pid,
            container_kind,
            container_title,
            json.dumps(capabilities_for(agent), sort_keys=True),
            receive_strategy_for(agent),
            connected_at,
            now,
            now,
            now,
        ),
    )
    conn.commit()


def mark_session_disconnected(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    disconnected_at: str,
) -> None:
    conn.execute(
        """
        update sessions
        set status = 'disconnected',
            disconnected_at = ?,
            updated_at = ?,
            last_seen_at = ?
        where id = ?
        """,
        (disconnected_at, disconnected_at, disconnected_at, session_id),
    )
    conn.commit()


def touch_session(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    seen_at: str,
) -> bool:
    cur = conn.execute(
        """
        update sessions
        set last_seen_at = ?,
            updated_at = ?
        where id = ?
          and status = 'active'
        """,
        (seen_at, seen_at, session_id),
    )
    conn.commit()
    return cur.rowcount > 0


def mark_stale_sessions(
    conn: sqlite3.Connection,
    *,
    checked_at: str,
    grace_seconds: float,
) -> int:
    checked = datetime.fromisoformat(checked_at)
    stale_ids: list[str] = []
    for row in conn.execute(
        "select id, pid, last_seen_at from sessions where status = 'active'"
    ):
        try:
            pid = int(row["pid"] or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid > 0 and shared.safe_pid_alive(pid):
            continue
        try:
            last_seen = datetime.fromisoformat(row["last_seen_at"])
        except (TypeError, ValueError):
            last_seen = checked
        if (checked - last_seen).total_seconds() >= grace_seconds:
            stale_ids.append(row["id"])
    if not stale_ids:
        return 0
    with conn:
        conn.executemany(
            """
            update sessions
            set status = 'stale',
                updated_at = ?,
                disconnected_at = coalesce(disconnected_at, ?)
            where id = ?
            """,
            [(checked_at, checked_at, session_id) for session_id in stale_ids],
        )
    return len(stale_ids)


def store_message(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    kind: str,
    from_session_id: str,
    from_name: str,
    from_agent: str | None,
    text: str,
    created_at: str,
    recipients: Iterable[str],
    to_session_id: str = "",
    to_name: str = "",
    thread_id: str | None = None,
    in_reply_to_message_id: str | None = None,
    expires_at: str | None = None,
) -> None:
    scope = "broadcast" if kind == "broadcast" else "direct"
    with conn:
        conn.execute(
            """
            insert or ignore into messages (
              id, kind, scope, from_session_id, from_name, from_agent,
              to_session_id, to_name, thread_id, in_reply_to_message_id,
              text, expires_at, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                kind,
                scope,
                from_session_id,
                from_name,
                from_agent,
                to_session_id or None,
                to_name or None,
                thread_id,
                in_reply_to_message_id,
                text,
                expires_at,
                created_at,
            ),
        )
        for recipient in recipients:
            conn.execute(
                """
                insert or ignore into message_deliveries (
                  message_id, session_id, delivery_state, disposition
                ) values (?, ?, 'pending', 'none')
                """,
                (message_id, recipient),
            )


def drain_pending(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    limit: int = 50,
    include_delivered: bool = True,
) -> list[sqlite3.Row]:
    states = ("pending", "delivered") if include_delivered else ("pending",)
    placeholders = ",".join("?" for _ in states)
    return list(
        conn.execute(
            f"""
            select m.*
            from messages m
            join message_deliveries d on d.message_id = m.id
            where d.session_id = ?
              and d.delivery_state in ({placeholders})
            order by m.created_at asc, m.seq asc
            limit ?
            """,
            (session_id, *states, max(1, limit)),
        )
    )


def get_message(conn: sqlite3.Connection, *, message_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "select * from messages where id = ?",
        (message_id,),
    ).fetchone()


def mark_delivered(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    session_id: str,
    delivered_at: str,
) -> bool:
    cur = conn.execute(
        """
        update message_deliveries
        set delivery_state = case
              when delivery_state = 'pending' then 'delivered'
              else delivery_state
            end,
            delivered_at = coalesce(delivered_at, ?)
        where message_id = ?
          and session_id = ?
        """,
        (delivered_at, message_id, session_id),
    )
    conn.commit()
    return cur.rowcount > 0


def mark_transport_failed(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    session_id: str,
    failed_at: str,
    failure_reason: str,
) -> bool:
    cur = conn.execute(
        """
        update message_deliveries
        set delivery_state = 'failed',
            attempts = attempts + 1,
            failed_at = ?,
            failure_reason = ?
        where message_id = ?
          and session_id = ?
          and disposition = 'none'
        """,
        (failed_at, failure_reason, message_id, session_id),
    )
    conn.commit()
    return cur.rowcount > 0


def retry_transport_failures(
    conn: sqlite3.Connection,
    *,
    max_attempts: int,
) -> int:
    cur = conn.execute(
        """
        update message_deliveries
        set delivery_state = 'pending',
            failure_reason = null
        where delivery_state = 'failed'
          and disposition = 'none'
          and attempts < ?
        """,
        (max_attempts,),
    )
    conn.commit()
    return cur.rowcount


def mark_read(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    session_id: str,
    read_at: str,
) -> bool:
    cur = conn.execute(
        """
        update message_deliveries
        set delivery_state = 'read',
            read_at = coalesce(read_at, ?),
            delivered_at = coalesce(delivered_at, ?)
        where message_id = ?
          and session_id = ?
          and delivery_state in ('pending', 'delivered', 'read')
        """,
        (read_at, read_at, message_id, session_id),
    )
    conn.commit()
    return cur.rowcount > 0


def mark_disposition(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    session_id: str,
    disposition: str,
    changed_at: str,
    reply_message_id: str | None = None,
    failure_reason: str | None = None,
) -> bool:
    if disposition not in {"question_sent", "replied", "skipped", "failed"}:
        raise ValueError(f"invalid disposition: {disposition}")
    timestamp_column = {
        "question_sent": "question_sent_at",
        "replied": "replied_at",
        "skipped": "skipped_at",
        "failed": "failed_at",
    }[disposition]
    sql = f"""
        update message_deliveries
        set delivery_state = case
              when delivery_state in ('pending', 'delivered') then 'read'
              else delivery_state
            end,
            read_at = coalesce(read_at, ?),
            disposition = ?,
            {timestamp_column} = coalesce({timestamp_column}, ?),
            reply_message_id = coalesce(?, reply_message_id),
            failure_reason = coalesce(?, failure_reason)
        where message_id = ?
          and session_id = ?
    """
    cur = conn.execute(
        sql,
        (
            changed_at,
            disposition,
            changed_at,
            reply_message_id,
            failure_reason,
            message_id,
            session_id,
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def disposition_from_reply_text(text: str) -> str | None:
    lowered = text.strip().lower()
    if lowered.startswith(("done:", "answer:")):
        return "replied"
    if lowered.startswith("question:"):
        return "question_sent"
    return None


def apply_reply_disposition(
    conn: sqlite3.Connection,
    *,
    original_message_id: str,
    replying_session_id: str,
    reply_message_id: str,
    reply_text: str,
    changed_at: str,
) -> str | None:
    disposition = disposition_from_reply_text(reply_text)
    if disposition is None:
        return None
    changed = mark_disposition(
        conn,
        message_id=original_message_id,
        session_id=replying_session_id,
        disposition=disposition,
        changed_at=changed_at,
        reply_message_id=reply_message_id,
    )
    return disposition if changed else None


def catch_up_broadcasts(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    since: str,
) -> int:
    rows = list(
        conn.execute(
            """
            select id
            from messages
            where scope = 'broadcast'
              and created_at >= ?
              and from_session_id != ?
            order by created_at asc, seq asc
            """,
            (since, session_id),
        )
    )
    if not rows:
        return 0
    with conn:
        conn.executemany(
            """
            insert or ignore into message_deliveries (
              message_id, session_id, delivery_state, disposition
            ) values (?, ?, 'pending', 'none')
            """,
            [(row["id"], session_id) for row in rows],
        )
    return len(rows)


def purge_expired(
    conn: sqlite3.Connection,
    *,
    now: str,
    retention_days: float,
) -> dict[str, int]:
    """Garbage-collect terminal history older than the retention window.

    Deletes:
      (a) message_deliveries whose outcome is terminal — disposition in
          {replied, skipped, failed} or delivery_state == 'read' — once the
          terminal timestamp (`read_at`, always set on those paths) is older
          than `retention_days`.
      (b) messages whose own `expires_at` lapsed more than `retention_days`
          ago AND that no longer have any outstanding (pending/delivered)
          delivery.

    Never touches pending or delivered-but-unread rows regardless of age —
    those still need to be drained. `retention_days <= 0` disables the purge
    entirely (no-op), matching INTERAGENTS_RETENTION_DAYS' off-by-default
    posture during local dev.
    """
    if retention_days <= 0:
        return {"deliveries": 0, "messages": 0}
    cutoff = (datetime.fromisoformat(now) - timedelta(days=retention_days)).isoformat()
    with conn:
        deliveries_deleted = conn.execute(
            """
            delete from message_deliveries
            where (
                    disposition in ('replied', 'skipped', 'failed')
                    or delivery_state = 'read'
                  )
              and read_at is not null
              and read_at < ?
            """,
            (cutoff,),
        ).rowcount
        messages_deleted = conn.execute(
            """
            delete from messages
            where expires_at is not null
              and expires_at < ?
              and not exists (
                    select 1 from message_deliveries d
                    where d.message_id = messages.id
                      and d.delivery_state in ('pending', 'delivered')
                  )
            """,
            (cutoff,),
        ).rowcount
    return {"deliveries": deliveries_deleted, "messages": messages_deleted}
