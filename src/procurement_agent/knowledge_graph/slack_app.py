"""Dedicated graph-investigation Slack listener with durable, isolated thread context."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

from procurement_agent.agent.model import openai_chat_model
from procurement_agent.config import get_settings
from procurement_agent.knowledge_graph.conversation import Context, GraphConversation, Reply
from procurement_agent.knowledge_graph.demo import ROOT, build_demo
from procurement_agent.knowledge_graph.retrieval import GraphReader, demo_aliases
from procurement_agent.knowledge_graph.store import GraphSettings


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


def thread_key(channel: str, root_ts: str, user: str) -> str:
    return hashlib.sha256(f"{channel}\0{root_ts}\0{user}".encode()).hexdigest()


def slack_text(text: str) -> str:
    # Prevent entity names or generated prose from creating mentions or injected links.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class SlackInvestigator:
    def __init__(self, service: GraphConversation, store: ThreadStore, channel: str) -> None:
        self.service, self.store, self.channel = service, store, channel
        self.lock = threading.Lock()

    def handle(self, event: dict[str, Any], client: Any) -> None:
        if (
            event.get("channel") != self.channel
            or event.get("bot_id")
            or event.get("subtype")
            or not event.get("user")
            or not event.get("ts")
        ):
            return
        root = str(event.get("thread_ts") or event["ts"])
        key = thread_key(self.channel, root, str(event["user"]))
        event_id = f"{self.channel}:{event['ts']}"
        # Serialize this small demo listener; no overlapping context updates.
        with self.lock:
            if self.store.seen(event_id):
                return
            context = self.store.context(key)
            try:
                reply = self.service.answer(str(event.get("text") or ""), context)
            except Exception:
                logging.getLogger(__name__).exception("Graph investigation failed")
                reply = Reply(
                    text="The graph or model is unavailable. Please try again shortly.",
                    context=context,
                    status="error",
                )
            if not self.store.reserve(event_id, key, reply):
                return
            # Block sections preserve a full evidence answer without Slack truncating one section.
            escaped = slack_text(reply.text)
            chunks = [escaped[i : i + 2900] for i in range(0, len(escaped), 2900)]
            if len(chunks) > 45:
                chunks = chunks[:44] + ["Answer exceeds Slack limits. Narrow the investigation."]
            try:
                client.chat_postMessage(
                    channel=self.channel,
                    thread_ts=root,
                    text=escaped[:3900],
                    blocks=[
                        {"type": "section", "text": {"type": "plain_text", "text": chunk}}
                        for chunk in chunks
                    ],
                    unfurl_links=False,
                    unfurl_media=False,
                )
                self.store.sent(event_id)
            except Exception:
                # A timeout can follow a successful post. Do not auto-post a duplicate on replay.
                logging.getLogger(__name__).exception(
                    "Slack delivery uncertain for %s; resend the question as a new message",
                    event_id,
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Check graph and Slack auth without posting"
    )
    args = parser.parse_args()
    settings = get_settings()
    if (
        not settings.slack_bot_token
        or not settings.slack_app_token
        or not settings.slack_channel_id
    ):
        raise RuntimeError("Set SLACK_BOT_TOKEN, SLACK_APP_TOKEN, and SLACK_CHANNEL_ID")
    logging.basicConfig(level=settings.app_log_level)
    graph = GraphSettings()
    with graph.driver() as driver:
        graph_reader = GraphReader(
            driver, graph.database, build_demo().fingerprint, demo_aliases(ROOT)
        )
        if args.check:
            client = WebClient(token=settings.slack_bot_token.get_secret_value(), timeout=20)
            auth = client.auth_test()
            print(
                json.dumps(
                    {
                        "slack_authenticated": bool(auth["ok"]),
                        "graph_ready": True,
                        "entities": len(graph_reader.catalog()),
                        "message_posted": False,
                    }
                )
            )
            return
        handler = SlackInvestigator(
            GraphConversation(graph_reader, openai_chat_model(settings)),
            ThreadStore(ROOT / ".cache/graph_threads.sqlite3"),
            settings.slack_channel_id,
        )
        app = App(
            token=settings.slack_bot_token.get_secret_value(),
            signing_secret=(
                settings.slack_signing_secret.get_secret_value()
                if settings.slack_signing_secret
                else None
            ),
        )

        @app.event("message")
        def on_message(event: dict[str, Any], client: Any) -> None:
            handler.handle(event, client)

        print("Graph investigations ready. Send a question in the configured Slack channel.")
        socket: Any = SocketModeHandler(app, settings.slack_app_token.get_secret_value())
        socket.start()


if __name__ == "__main__":
    main()
