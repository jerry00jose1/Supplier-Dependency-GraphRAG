#!/usr/bin/env python3
"""Listen in Slack for an item request and post one proposal to approve."""

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
from slack_sdk.errors import SlackApiError

from procurement_agent.agent.model import openai_chat_model
from procurement_agent.approvals.service import ApprovalVault
from procurement_agent.config import Settings, get_settings
from procurement_agent.knowledge_graph.conversation import Context, GraphConversation, Reply
from procurement_agent.knowledge_graph.demo import ROOT, build_demo
from procurement_agent.knowledge_graph.operations import GraphVault
from procurement_agent.knowledge_graph.procurement import load_evidence
from procurement_agent.knowledge_graph.retrieval import GraphReader, demo_aliases
from procurement_agent.knowledge_graph.store import GraphSettings
from procurement_agent.slack.item_reply import ItemReply, reply_to_text
from procurement_agent.slack.router import order_answer, route_question
from procurement_agent.slack.storage import ThreadStore, slack_text


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.app_log_level)
    bot_token, app_token, signing_secret = _tokens(settings)
    model = openai_chat_model(settings)
    graph_settings = GraphSettings()
    driver = graph_settings.driver()
    vault = GraphVault(driver, graph_settings.database)
    receipts = ThreadStore(ROOT / ".cache/unified_deliveries.sqlite3")
    lock = threading.Lock()
    app = App(token=bot_token, signing_secret=signing_secret)

    @app.command("/propose")
    def propose(ack: Any, command: dict[str, Any], client: Any) -> None:
        ack()
        text = str(command.get("text") or "")
        channel = str(command["channel_id"])
        conversation_id = conversation_key(channel, str(command["user_id"]))
        vault.event_id = str(command.get("trigger_id") or command.get("command_ts") or "")
        _deliver(
            client,
            vault,
            channel,
            _unified_answer(settings, vault, model, driver, text, conversation_id),
            conversation_id=conversation_id,
        )

    @app.event("message")
    def on_message(event: dict[str, Any], client: Any) -> None:
        with lock:
            handle_message(event, client)

    def handle_message(event: dict[str, Any], client: Any) -> None:
        if event.get("channel") != settings.slack_channel_id:
            return
        if event.get("bot_id") or event.get("subtype") or not event.get("user"):
            return
        event_id = f"{event['channel']}:{event['ts']}"
        if receipts.seen(event_id):
            return
        channel = settings.slack_channel_id
        root_ts = str(event.get("thread_ts") or event["ts"])
        # Each Slack thread is a conversation. Root message and its replies share this key.
        conversation_id = conversation_key(channel, str(event.get("user") or ""), root_ts)
        if vault.conversation(conversation_id) is None:
            command_context = vault.conversation(conversation_key(channel, "", root_ts))
            if command_context:
                vault.save_conversation(conversation_id, command_context)
        vault.event_id = event_id
        reply = _unified_answer(
            settings, vault, model, driver, str(event.get("text") or ""), conversation_id,
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

    print("Unified Slack ready: ERP orders, procurement and graph investigations.", flush=True)
    try:
        socket: Any = SocketModeHandler(app, app_token)
        socket.start()
    finally:
        driver.close()


def _unified_answer(
    settings: Settings, vault: ApprovalVault, model: BaseChatModel,
    driver: Any, text: str, conversation_id: str,
) -> ItemReply:
    try:
        route, state = route_question(model, vault, conversation_id, text)
        if route.workflow == "procurement":
            reply = _answer(settings, vault, model, text, conversation_id=conversation_id)
        else:
            if route.workflow == "orders":
                answer = order_answer(driver, GraphSettings().database,
                                      "procurement:active", state)
            else:
                # Synthetic facility evidence remains scoped to the demo, never ERP identities.
                reader = GraphReader(driver, GraphSettings().database, build_demo().fingerprint,
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
                         "purchase order ID, or item code. No ERP action was taken by the router.",
                         None, None, None, None)


def _answer(
    settings: Settings,
    vault: ApprovalVault,
    model: BaseChatModel,
    text: str,
    *,
    conversation_id: str = "default",
) -> ItemReply:
    if not text.strip():
        return ItemReply("Name an item, for example: I need S-66100", None, None, None, None)
    try:
        groups, policy, refreshed = load_evidence(settings)
        orders, order_items, receipts, receipt_items, invoices, invoice_items = groups
    except Exception:
        logging.getLogger(__name__).exception("Graph evidence lookup failed")
        return ItemReply("Graph evidence is unavailable. Run make graph-sync or try again shortly.",
                         None, None, None, None)
    reply = reply_to_text(
        vault,
        text,
        orders,
        order_items,
        receipts,
        receipt_items,
        model,
        today=datetime.now(ZoneInfo(settings.app_timezone)).date(),
        conversation_id=conversation_id,
        invoices=invoices,
        invoice_items=invoice_items,
        embedder=policy,
        submit_limit=settings.order_submit_limit,
    )

    return ItemReply(f"Graph evidence refreshed {refreshed}.\n\n" + reply.text,
                     reply.blocks, reply.proposal_id, reply.slack_channel, reply.slack_ts)


def conversation_key(channel: str, user: str, thread_ts: str = "commands") -> str:
    return "slack:" + hashlib.sha256(f"{channel}:{user}:{thread_ts}".encode()).hexdigest()


def _deliver(
    client: Any,
    vault: ApprovalVault,
    channel: str,
    reply: ItemReply,
    *,
    thread_ts: str | None = None,
    conversation_id: str | None = None,
) -> None:
    if reply.slack_channel and reply.slack_ts:
        try:
            client.chat_update(
                channel=reply.slack_channel,
                ts=reply.slack_ts,
                text=reply.text,
                blocks=reply.blocks or [],
            )
            return
        except SlackApiError:
            logging.getLogger(__name__).exception("Card update failed; posting a new card")
    payload: dict[str, Any] = {"channel": channel, "text": reply.text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    if reply.blocks:
        payload["blocks"] = reply.blocks
    response = client.chat_postMessage(**payload)
    getter = getattr(response, "get", None)
    ts = getter("ts") if callable(getter) else None
    if reply.proposal_id and isinstance(ts, str):
        vault.remember_slack(reply.proposal_id, channel, ts)
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
