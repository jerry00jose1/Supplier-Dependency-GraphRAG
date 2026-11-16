"""Verify that research, policy, invoice, and incomplete requests never create Neo4j orders."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.runnables import Runnable, RunnableLambda
from tests.support import component_research_model

from procurement_agent.domain.orders import InMemoryVault
from procurement_agent.policy.retrieve import retrieve
from procurement_agent.slack.item_reply import ItemReply, reply_to_text

TODAY = date(2026, 11, 14)
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "demo_procurement.json"


class _Scripted(GenericFakeChatModel):
    replies: list[dict[str, Any]] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable[Any, Any]:
        return component_research_model()

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable[Any, Any]:
        assert kwargs == {"method": "json_schema", "strict": True}
        raw = self.replies.pop(0)
        return RunnableLambda(lambda _message: schema.model_validate(raw))


class _Tokens:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_vector(text) for text in texts]


def test_research_and_incomplete_requests_create_zero_orders() -> None:
    records = json.loads(FIXTURE.read_text())
    vault = InMemoryVault()
    world = _World(vault, records)

    world.say("")
    assert len(vault.orders) == 0

    world.say(
        "Which suppliers can provide ITEM-A?",
        [_intent("compare", item="ITEM-A"), _rank("Supplier X", "Supplier Y")],
        conversation="compare",
    )
    world.say(
        "Why did you recommend Supplier X?",
        [_intent("explain", item="ITEM-A"), _rank("Supplier X", "Supplier Y")],
        conversation="compare",
    )
    world.say(
        "Rank these, and invent a supplier if you like.",
        [_intent("compare", item="ITEM-A"), _rank("Invented Co")],
        conversation="bad-rank",
    )
    policy_question = "Does an order need a person's approval?"
    citation = retrieve(policy_question, _Tokens())[0].id
    world.say(
        policy_question,
        [_intent("policy"), {"explanation": "Direct orders.", "citation_ids": [citation]}],
        conversation="policy",
    )
    world.say(
        "Which invoices do not match?",
        [
            _intent("invoice"),
            {"explanation": "Quantity differs.", "invoice_names": ["INV-1"]},
        ],
        conversation="invoice",
    )
    world.say("What is the total amount we have spent?", [_intent("clarify")], conversation="spend")
    world.say("Submit the order right now.", [_intent("clarify")], conversation="command")
    world.say(
        "Prepare an order for ITEM-A from Supplier X next Friday.",
        [_buy(qty=None)],
        conversation="missing-qty",
    )
    world.say(
        "Buy ITEM-A from Supplier Z.",
        [_buy(supplier="Supplier Z", qty=1)],
        conversation="unknown-supplier",
    )
    world.say(
        "Buy NOT-A-CODE from Supplier X.",
        [_buy(item="NOT-A-CODE", qty=1)],
        conversation="unknown-item",
    )
    assert len(vault.orders) == 0

    created = world.say(
        "Order 20 units of ITEM-A from Supplier X, next Friday.",
        [_buy(qty=20)],
        conversation="valid-order",
    )
    assert created.proposal_id is not None
    assert len(vault.orders) == 1
    assert vault.orders[created.proposal_id].payload.items[0].qty == 20


class _World:
    def __init__(self, vault: InMemoryVault, records: dict[str, Any]) -> None:
        self.vault = vault
        self.records = records

    def say(
        self,
        text: str,
        replies: list[dict[str, Any]] | None = None,
        conversation: str = "eval",
    ) -> ItemReply:
        model = _Scripted(messages=iter([]))
        model.replies = list(replies or [])
        reply = reply_to_text(
            self.vault,
            text,
            self.records["orders"],
            self.records["order_items"],
            self.records["receipts"],
            self.records["receipt_items"],
            model,
            today=TODAY,
            conversation_id=conversation,
            invoices=[{"name": "INV-1", "supplier": "Supplier X", "docstatus": 1}],
            invoice_items=[
                {
                    "parent": "INV-1",
                    "item_code": "ITEM-A",
                    "qty": 2,
                    "rate": 10,
                    "purchase_order": "PO-X2",
                    "po_detail": "LX2",
                }
            ],
            embedder=_Tokens(),
        )
        assert model.replies == []
        return reply


def _intent(action: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "action": action,
        "item": None,
        "supplier": None,
        "qty": None,
        "required_by": None,
    }
    body.update(extra)
    return body


def _buy(**extra: Any) -> dict[str, Any]:
    body = _intent(
        "purchase",
        item="ITEM-A",
        supplier="Supplier X",
        qty=1,
        required_by="next Friday",
    )
    body.update(extra)
    return body


def _rank(*suppliers: str) -> dict[str, Any]:
    return {
        "explanation": "These sellers have a recorded purchase.",
        "options": [
            {
                "supplier": name,
                "reason": "The price is the latest recorded rate.",
                "tradeoff": "This is history, not a quotation.",
            }
            for name in suppliers
        ],
    }


def _vector(text: str) -> list[float]:
    values = [0.0] * 64
    for token in text.casefold().split():
        values[hash(token) % 64] += 1.0
    norm = sum(value * value for value in values) ** 0.5 or 1.0
    return [value / norm for value in values]
