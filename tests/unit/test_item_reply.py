import json
from datetime import date
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda

from procurement_agent.domain.orders import InMemoryVault
from procurement_agent.slack.item_reply import reply_to_text

TODAY = date(2026, 11, 12)


class _ScriptedModel(GenericFakeChatModel):
    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable[Any, Any]:
        return self | RunnableLambda(lambda message: schema.model_validate_json(message.content))


def _records() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    orders = [
        {
            "name": "PO-1",
            "supplier": "Good Co",
            "transaction_date": "2024-05-01",
            "company": "Northwind Components",
        },
        {
            "name": "PO-2",
            "supplier": "Late Co",
            "transaction_date": "2024-03-01",
            "company": "Northwind Components",
        },
    ]
    items = [
        {
            "name": "L1",
            "parent": "PO-1",
            "item_code": "ITEM-A",
            "qty": 8,
            "rate": 7,
            "warehouse": "Stores - NC",
        },
        {
            "name": "L2",
            "parent": "PO-2",
            "item_code": "ITEM-A",
            "qty": 12,
            "rate": 5,
            "warehouse": "Stores - NC",
        },
    ]
    return orders, items


def test_complete_order_request_creates_direct_neo4j_order_without_buttons() -> None:
    orders, items = _records()
    model = _ScriptedModel(
        messages=iter(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "action": "purchase",
                            "item": "ITEM-A",
                            "supplier": "Good Co",
                            "qty": 8,
                            "required_by": "next Friday",
                        }
                    )
                ),
            ]
        )
    )
    vault = InMemoryVault()
    answer = reply_to_text(vault, "I need ITEM-A", orders, items, [], [], model, today=TODAY)
    assert answer.proposal_id is not None
    assert answer.proposal_id.startswith("GRAPH-PO-")
    assert f"Created order {answer.proposal_id} in Neo4j." in answer.text
    assert "Good Co: 8 × ITEM-A, unit price 7 EUR" in answer.text
    assert answer.blocks is None


def test_a_reply_without_an_order_has_no_buttons() -> None:
    orders, items = _records()
    model = _ScriptedModel(
        messages=iter(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "action": "clarify",
                            "item": None,
                            "supplier": None,
                            "qty": None,
                            "required_by": None,
                        }
                    )
                )
            ]
        )
    )
    answer = reply_to_text(InMemoryVault(), "hello", orders, items, [], [], model, today=TODAY)
    assert answer.blocks is None
    assert answer.proposal_id is None
    assert "prepare one purchase" in answer.text
