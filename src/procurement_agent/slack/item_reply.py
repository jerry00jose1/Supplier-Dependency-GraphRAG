"""Turn a Slack message into the buyer's decision and record direct Neo4j orders."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from procurement_agent.agent.graph import run_conversation
from procurement_agent.domain.orders import OrderVault
from procurement_agent.knowledge_graph.procurement import ProcurementEvidence
from procurement_agent.policy.retrieve import Embedder


@dataclass(frozen=True)
class ItemReply:
    text: str
    blocks: list[dict[str, Any]] | None
    proposal_id: str | None
    slack_channel: str | None
    slack_ts: str | None


def reply_to_text(
    vault: OrderVault,
    text: str,
    orders: list[dict[str, Any]] | None = None,
    order_items: list[dict[str, Any]] | None = None,
    receipts: list[dict[str, Any]] | None = None,
    receipt_items: list[dict[str, Any]] | None = None,
    model: BaseChatModel | Any = None,
    *,
    today: date,
    conversation_id: str = "default",
    invoices: list[dict[str, Any]] | None = None,
    invoice_items: list[dict[str, Any]] | None = None,
    embedder: Embedder | None = None,
    source: ProcurementEvidence | None = None,
    event_id: str | None = None,
) -> ItemReply:
    if not text.strip():
        return _say("Name an item, for example: I need S-66100")
    try:
        decision, filed = run_conversation(
            model,
            vault,
            text,
            orders,
            order_items,
            receipts,
            receipt_items,
            today=today,
            conversation_id=conversation_id,
            invoices=invoices,
            invoice_items=invoice_items,
            embedder=embedder,
            source=source,
            event_id=event_id,
        )
    except Exception:
        logging.getLogger(__name__).exception("Buyer request failed")
        return _say("I could not complete that request. Check order history before retrying.")
    if filed is None:
        return _say(decision or "Name an item this company has purchased, for example S-66100.")
    stored = filed.stored
    if stored.erp_document_name and stored.erp_document_name.startswith("GRAPH-PO-"):
        return ItemReply(
            f"Created order {stored.erp_document_name} in Neo4j.\n"
            f"{stored.payload.supplier}: "
            f"{stored.payload.items[0].qty:g} × "
            f"{stored.payload.items[0].item_code}, "
            f"unit price {stored.payload.items[0].rate:g} "
            f"{stored.payload.currency}, "
            f"due {stored.payload.schedule_date}.\n"
            "Recorded in this application; not sent to a supplier.",
            None,
            stored.id,
            None,
            None,
        )
    if stored.erp_document_name:
        return _say(f"Already created {stored.erp_document_name}. No second purchase order.")
    return _say(f"This item already has a {stored.status} order.")


def _say(text: str) -> ItemReply:
    return ItemReply(text, None, None, None, None)
