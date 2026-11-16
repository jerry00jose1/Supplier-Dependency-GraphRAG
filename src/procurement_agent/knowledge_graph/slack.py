"""Socket Mode interface for the demo graph assistant. No ERP write handlers."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

from procurement_agent.agent.model import openai_chat_model
from procurement_agent.config import get_settings
from procurement_agent.knowledge_graph.assistant import GraphAssistant
from procurement_agent.knowledge_graph.cli import investigate
from procurement_agent.knowledge_graph.demo import build_demo
from procurement_agent.knowledge_graph.store import GraphSettings

LOG = logging.getLogger(__name__)
FAILURE = "I couldn't retrieve or explain the graph evidence. Please ask again in this thread."


class SlackConversations:
    """Local demo state: durable deduplication and bounded per-thread context."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, status TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, history TEXT)")

    def respond(self, event_id: str, thread: str, question: str,
                answer: Callable[[str, list[dict[str, str]]], str],
                send: Callable[[str], None]) -> None:
        # Single local listener: serialize processing to keep follow-up context ordered.
        with self.lock, sqlite3.connect(self.path) as db:
            inserted = db.execute("INSERT OR IGNORE INTO events VALUES (?, 'processing')",
                                  (event_id,)).rowcount
            db.commit()
            if not inserted:
                return
            stored = db.execute("SELECT history FROM threads WHERE id=?", (thread,)).fetchone()
            history = json.loads(stored[0]) if stored else []
            try:
                response = answer(question, history)
            except Exception as exc:
                # Exception messages may include provider responses; don't log their contents.
                LOG.warning("Graph answer failed (%s)", type(exc).__name__)
                response = FAILURE
            try:
                send(response)
            except Exception as exc:
                db.execute("UPDATE events SET status='delivery_uncertain' WHERE id=?", (event_id,))
                db.commit()
                LOG.warning("Slack delivery uncertain (%s); not replaying event", type(exc).__name__)
                return
            if response != FAILURE:
                history += [{"role": "user", "text": question[:4000]},
                            {"role": "assistant", "text": response[:5000]}]
                db.execute("INSERT OR REPLACE INTO threads VALUES (?,?)",
                           (thread, json.dumps(history[-6:])))
            db.execute("UPDATE events SET status='sent' WHERE id=?", (event_id,))
            db.commit()


def handle_message(event: dict[str, Any], body: dict[str, Any], client: Any,
                   channel: str, state: SlackConversations, assistant: GraphAssistant) -> None:
    if event.get("channel") != channel or event.get("bot_id") or event.get("subtype"):
        return
    if not event.get("user") or not event.get("ts") or not str(event.get("text", "")).strip():
        return
    root = str(event.get("thread_ts") or event["ts"])
    event_id = str(body.get("event_id") or f"{channel}:{event['ts']}")
    state.respond(event_id, f"{channel}:{root}", str(event["text"]), assistant.answer,
                  lambda text: client.chat_postMessage(
                      channel=channel, thread_ts=root, text=text,
                      mrkdwn=False, unfurl_links=False, unfurl_media=False,
                  ))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check graph and Slack auth; do not post")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.slack_bot_token or not settings.slack_app_token or not settings.slack_channel_id:
        raise SystemExit("Configure SLACK_BOT_TOKEN, SLACK_APP_TOKEN, and SLACK_CHANNEL_ID in .env")
    logging.basicConfig(level=logging.INFO)
    graph = GraphSettings()
    snapshot = build_demo().fingerprint
    client = WebClient(token=settings.slack_bot_token.get_secret_value(), timeout=20)
    auth = client.auth_test()
    with graph.driver() as driver:
        investigate(driver, graph.database, snapshot)
        model = openai_chat_model(settings)
        if args.check:
            print(json.dumps({"slack_authenticated": bool(auth["ok"]),
                              "graph_ready": True, "model": settings.llm_model,
                              "snapshot": snapshot, "message_posted": False}))
            return
        assistant = GraphAssistant(model, lambda: investigate(driver, graph.database, snapshot))
        state = SlackConversations(Path(".cache/graph-slack.sqlite3"))
        app = App(client=client, signing_secret=(settings.slack_signing_secret.get_secret_value()
                                                if settings.slack_signing_secret else None))

        @app.event("message")
        def on_message(event: dict[str, Any], body: dict[str, Any], client: Any) -> None:
            handle_message(event, body, client, settings.slack_channel_id, state, assistant)

        print("GraphRAG listener ready for the configured Slack channel; demo snapshot 2026-11-15.",
              flush=True)
        SocketModeHandler(app, settings.slack_app_token.get_secret_value()).start()


if __name__ == "__main__":
    main()
