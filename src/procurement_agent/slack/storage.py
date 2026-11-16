"""Durable delivery receipts and graph chat context."""

import sqlite3
from pathlib import Path

from procurement_agent.knowledge_graph.conversation import Context, Reply


class ThreadStore:
    """Only graph conversations; no purchasing credentials or approval state."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with sqlite3.connect(path) as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS contexts (key TEXT PRIMARY KEY, body TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY, status TEXT NOT NULL, "
                "reply TEXT NOT NULL);"
            )

    def context(self, key: str) -> Context:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT body FROM contexts WHERE key=?", (key,)).fetchone()
        return Context.model_validate_json(row[0]) if row else Context()

    def seen(self, event_id: str) -> bool:
        with sqlite3.connect(self.path) as db:
            return (
                db.execute("SELECT 1 FROM deliveries WHERE id=?", (event_id,)).fetchone()
                is not None
            )

    def reserve(self, event_id: str, key: str, reply: Reply) -> bool:
        with sqlite3.connect(self.path) as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO deliveries VALUES (?, 'sending', ?)",
                (event_id, reply.model_dump_json()),
            )
            if cursor.rowcount == 0:
                return False
            db.execute(
                "INSERT INTO contexts VALUES (?, ?) ON CONFLICT(key) "
                "DO UPDATE SET body=excluded.body",
                (key, reply.context.model_dump_json()),
            )
            return True

    def sent(self, event_id: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE deliveries SET status='sent' WHERE id=?", (event_id,))


def slack_text(text: str) -> str:
    # Prevent entity names or generated prose from creating mentions or injected links.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
