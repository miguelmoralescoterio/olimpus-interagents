"""Private, durable monitor identity and delivery checkpoints."""

import json
import secrets
import sqlite3
import uuid


class Journal:
    def __init__(self, path, cwd):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            create table if not exists settings (key text primary key, value text);
            create table if not exists jobs (
                id text primary key, status text not null, reply text
            );
        """)
        if self.get("cwd") not in (None, cwd):
            self.db.close()
            raise ValueError("Monitor name already belongs to another cwd")
        for key, value in (("cwd", cwd), ("session_id", str(uuid.uuid4())),
                           ("nonce", secrets.token_urlsafe(16))):
            if self.get(key) is None:
                self.set(key, value)
        self.db.execute("""
            update jobs set status='reply', reply=? where status='running'
        """, ("answer: El monitor se interrumpió durante esta tarea; no se repetirá automáticamente. Revisa la conversación antes de reenviarla.",))
        self.db.commit()

    def get(self, key):
        row = self.db.execute("select value from settings where key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, key, value):
        with self.db:
            self.db.execute("insert or replace into settings values (?,?)", (key, json.dumps(value)))

    def job(self, message_id):
        return self.db.execute("select * from jobs where id=?", (message_id,)).fetchone()

    def save(self, message_id, status, reply=None):
        with self.db:
            self.db.execute("insert or replace into jobs values (?,?,?)", (message_id, status, reply))
