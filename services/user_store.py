"""
SQLite persistence for users and per-user chat messages.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import DATABASE_PATH


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)


class UserStore:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or DATABASE_PATH
        _ensure_parent_dir(self.db_path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chat_user_created
                    ON chat_messages (user_id, created_at);
                """
            )

    def create_user(self, email: str, password_hash: str) -> Dict[str, Any]:
        uid = str(uuid.uuid4())
        created = _utc_now()
        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (uid, email, password_hash, created),
                )
            except sqlite3.IntegrityError:
                raise ValueError("This email is already registered.") from None
        return {"id": uid, "email": email, "created_at": created}

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, email, password_hash, created_at FROM users WHERE email = ?",
                (email,),
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, email, created_at FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return {"id": row["id"], "email": row["email"], "created_at": row["created_at"]}

    def add_chat_message(self, user_id: str, role: str, content: str) -> Dict[str, Any]:
        if role not in ("user", "assistant"):
            raise ValueError("role must be user or assistant")
        text = (content or "").strip()
        if not text:
            raise ValueError("content required")
        if len(text) > 100_000:
            raise ValueError("content too long")
        created = _utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO chat_messages (user_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, role, text, created),
            )
            mid = cur.lastrowid
        return {"id": mid, "user_id": user_id, "role": role, "content": text, "created_at": created}

    def list_chat_messages(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        lim = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, created_at
                FROM chat_messages
                WHERE user_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (user_id, lim),
            ).fetchall()
        return [dict(r) for r in rows]
