#!/usr/bin/env python3
"""Listen in Slack and create complete purchase requests directly in Neo4j."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.language_models.chat_models import BaseChatModel
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from procurement_agent.agent.model import openai_chat_model
from procurement_agent.config import Settings, get_settings
from procurement_agent.domain.orders import OrderVault
from procurement_agent.knowledge_graph.conversation import Context, GraphConversation, Reply
from procurement_agent.knowledge_graph.demo import ROOT
from procurement_agent.knowledge_graph.operations import ACTIVE, GraphVault
from procurement_agent.knowledge_graph.procurement import load_graph_evidence
from procurement_agent.knowledge_graph.retrieval import GraphReader, demo_aliases
from procurement_agent.knowledge_graph.store import GraphSettings
from procurement_agent.slack.item_reply import ItemReply, reply_to_text
from procurement_agent.slack.router import order_answer, route_question
from procurement_agent.slack.storage import ThreadStore, slack_text


class _KeyedLocks:
    """Serialize messages within the same Slack thread without blocking other threads."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def for_key(self, key: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.app_log_level)
    bot_token, app_token, signing_secret = _tokens(settings)
    model = openai_chat_model(settings)
    graph_settings = GraphSettings()
    driver = graph_settings.driver()
    vault = GraphVault(driver, graph_settings.database)
    receipts = ThreadStore(ROOT / ".cache/unified_deliveries.sqlite3")
    locks = _KeyedLocks()
    app = App(token=bot_token, signing_secret=signing_secret)

    @app.command("/propose")
    def propose(ack: Any, command: dict[str, Any], client: Any) -> None:
        ack()
        channel = str(command["channel_id"])
        conversation_id = conversation_key(channel, str(command["user_id"]))
        with locks.for_key(conversation_id):
            handle_command(command, client, channel, conversation_id)

    def handle_command(
        command: dict[str, Any], client: Any, channel: str, conversation_id: str
    ) -> None:
        text = str(command.get("text") or "")
        event_id = str(command.get("trigger_id") or command.get("command_ts") or "")
        _deliver(
            client,
            vault,
            channel,
            _unified_answer(
                settings, vault, model, driver, text, conversation_id, event_id=event_id
            ),
            conversation_id=conversation_id,
        )

    @app.event("message")
    def on_message(event: dict[str, Any], client: Any) -> None:
        if event.get("channel") != settings.slack_channel_id:
            return
        if event.get("bot_id") or event.get("subtype") or not event.get("user"):
            return
        channel = settings.slack_channel_id
        root_ts = str(event.get("thread_ts") or event["ts"])
        conversation_id = conversation_key(channel, str(event.get("user") or ""), root_ts)
        with locks.for_key(conversation_id):
            handle_message(event, client, channel, root_ts, conversation_id)

    def handle_message(
        event: dict[str, Any],
        client: Any,
        channel: str,
        root_ts: str,
        conversation_id: str,
    ) -> None:
        event_id = f"{event['channel']}:{event['ts']}"
        if receipts.seen(event_id):
            return
        if vault.conversation(conversation_id) is None:
            command_context = vault.conversation(conversation_key(channel, "", root_ts))
            if command_context:
                vault.save_conversation(conversation_id, command_context)
        reply = _unified_answer(
            settings,
            vault,
            model,
            driver,
            str(event.get("text") or ""),
            conversation_id,
            event_id=event_id,
        )
        if not receipts.reserve(event_id, conversation_id, Reply(
            text=reply.text, context=Context(), status="handled",
        )):
            return
        _deliver(
            client,
            vault,
            settings.slack_channel_id,
            reply,
            thread_ts=root_ts,
            conversation_id=conversation_id,
        )
        receipts.sent(event_id)

    print("Graph-only Slack ready: direct Neo4j orders and procurement research.", flush=True)
    try:
        socket: Any = SocketModeHandler(app, app_token)
        socket.start()
    finally:
        driver.close()


def _unified_answer(
    settings: Settings,
    vault: OrderVault,
    model: BaseChatModel,
    driver: Any,
    text: str,
    conversation_id: str,
    *,
    event_id: str | None = None,
) -> ItemReply:
    try:
        route, state = route_question(model, vault, conversation_id, text)
        if route.workflow == "procurement":
            reply = _answer(
                settings,
                vault,
                model,
                text,
                conversation_id=conversation_id,
                driver=driver,
                event_id=event_id,
                intent=route.intent,
            )
        else:
            if route.workflow == "orders":
                answer = order_answer(driver, GraphSettings().database,
                                      ACTIVE, state)
            else:
                reader = GraphReader(driver, GraphSettings().database, ACTIVE,
                                     demo_aliases(ROOT))
                previous = Context.model_validate_json(
                    vault.conversation("graph:" + conversation_id) or "{}"
                )
                result = GraphConversation(reader, model).answer(text, previous)
                vault.save_conversation("graph:" + conversation_id,
                                        result.context.model_dump_json())
                answer = "Synthetic dependency demo investigation:\n" + result.text
            escaped = slack_text(answer)
            chunks = [escaped[i:i + 2900] for i in range(0, len(escaped), 2900)]
            reply = ItemReply(escaped[:3900], [
                {"type": "section", "text": {"type": "plain_text", "text": chunk}}
                for chunk in chunks[:45]
            ], None, None, None)
        vault.save_conversation("router:" + conversation_id, json.dumps(state))
        return reply
    except Exception:
        logging.getLogger(__name__).exception("Unified Slack request failed")
        return ItemReply("I could not complete that request. Please retry with a supplier name, "
                         "purchase order ID, or item code. Check order history before retrying.",
                         None, None, None, None)


def _answer(
    settings: Settings,
    vault: OrderVault,
    model: BaseChatModel,
    text: str,
    *,
    conversation_id: str = "default",
    driver: Any | None = None,
    event_id: str | None = None,
    intent: Any | None = None,
) -> ItemReply:
    if not text.strip():
        return ItemReply("Name an item, for example: I need S-66100", None, None, None, None)
    try:
        source = load_graph_evidence(driver)
    except Exception:
        logging.getLogger(__name__).exception("Graph evidence lookup failed")
        return ItemReply("Graph evidence is unavailable. Run make graph-sync or try again shortly.",
                         None, None, None, None)
    reply = reply_to_text(
        vault,
        text,
        model=model,
        today=datetime.now(ZoneInfo(settings.app_timezone)).date(),
        conversation_id=conversation_id,
        embedder=source.embedder,
        source=source,
        event_id=event_id,
        intent=intent,
    )

    return ItemReply(f"Graph evidence refreshed {source.refreshed}.\n\n" + reply.text,
                     reply.blocks, reply.proposal_id, reply.slack_channel, reply.slack_ts)


def conversation_key(channel: str, user: str, thread_ts: str = "commands") -> str:
    return "slack:" + hashlib.sha256(f"{channel}:{user}:{thread_ts}".encode()).hexdigest()


def _deliver(
    client: Any,
    vault: OrderVault,
    channel: str,
    reply: ItemReply,
    *,
    thread_ts: str | None = None,
    conversation_id: str | None = None,
) -> None:
    payload: dict[str, Any] = {"channel": channel, "text": reply.text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    if reply.blocks:
        payload["blocks"] = reply.blocks
    response = client.chat_postMessage(**payload)
    getter = getattr(response, "get", None)
    ts = getter("ts") if callable(getter) else None
    # A /propose reply can become a normal Slack thread. Copy its durable context.
    if conversation_id and not thread_ts and isinstance(ts, str):
        saved = vault.conversation(conversation_id)
        if saved:
            # Resolve this alias before user-specific keys in on_message.
            vault.save_conversation(conversation_key(channel, "", ts), saved)


def _tokens(settings: Settings) -> tuple[str, str, str]:
    bot = settings.slack_bot_token
    app = settings.slack_app_token
    secret = settings.slack_signing_secret
    if bot is None or app is None or secret is None:
        raise SystemExit("Set SLACK_BOT_TOKEN, SLACK_APP_TOKEN, and SLACK_SIGNING_SECRET in .env.")
    return (
        bot.get_secret_value(),
        app.get_secret_value(),
        secret.get_secret_value(),
    )


if __name__ == "__main__":
    main()
