import json
import runpy
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda
from tests.support import component_research_model

from procurement_agent.agent.conversation import BuyerIntent, resolve_date
from procurement_agent.agent.recommendations import SupplierDecision
from procurement_agent.domain.orders import InMemoryVault
from procurement_agent.slack.item_reply import reply_to_text

TODAY = date(2026, 11, 13)
ROOT = Path(__file__).resolve().parents[2]


class IntentModel(GenericFakeChatModel):
    decisions: list[dict[str, Any]] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable[Any, AIMessage]:
        return component_research_model()

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable[Any, Any]:
        assert kwargs == {"method": "json_schema", "strict": True}
        if schema is BuyerIntent:
            parse = RunnableLambda(
                lambda message: schema.model_validate_json(message.content)
            )
            return self | parse
        assert schema is SupplierDecision
        decision = self.decisions.pop(0)
        return RunnableLambda(lambda _message: schema.model_validate(decision))


def intent(action: str, **values: Any) -> dict[str, Any]:
    return dict(action=action, item=None, supplier=None, qty=None, required_by=None) | values


class Demo:
    def __init__(self) -> None:
        self.vault = InMemoryVault()
        self.data = json.loads((ROOT / "tests/fixtures/demo_procurement.json").read_text())

    def say(
        self,
        text: str,
        parsed: dict[str, Any],
        conversation_id: str = "demo",
        decision: dict[str, Any] | None = None,
    ):
        model = IntentModel(messages=iter([AIMessage(content=json.dumps(parsed))]))
        model.decisions = [] if decision is None else [decision]
        return reply_to_text(
            self.vault,
            text,
            **self.data,
            model=model,
            today=TODAY,
            conversation_id=conversation_id,
        )

    def count(self) -> int:
        return len(self.vault.orders)

    def prepare(self):
        return self.say(
            "Prepare 20 ITEM-A from Supplier X next Friday",
            intent(
                "purchase", item="ITEM-A", supplier="Supplier X", qty=20, required_by="next Friday"
            ),
        )


@pytest.fixture
def demo():
    return Demo()


def _shortlist(first: str = "Supplier X", second: str = "Supplier Y") -> dict[str, Any]:
    return {
        "explanation": f"{first} matches the request more closely than {second}.",
        "options": [
            {"supplier": first, "reason": "Better recorded delivery.", "tradeoff": "Higher price."},
            {
                "supplier": second,
                "reason": "Lower historical price.",
                "tradeoff": "Weaker delivery.",
            },
        ],
    }


def test_direct_graph_order_creation_without_drafts_or_buttons(demo):
    comparison = demo.say(
        "Which suppliers can provide ITEM-A? Compare price and delivery history.",
        intent("compare", item="ITEM-A"),
        decision=_shortlist(),
    )
    assert "Supplier X: latest unit price 10 EUR" in comparison.text
    assert "Supplier Y: latest unit price 8 EUR" in comparison.text
    assert "100% on time across 2" in comparison.text
    assert "0% on time across 2" in comparison.text
    assert comparison.blocks is None and demo.count() == 0

    created = demo.say(
        "Order 20 units from Supplier X, needed next Friday.",
        intent("purchase", supplier="Supplier X", qty=20, required_by="next Friday"),
    )
    assert created.proposal_id is not None
    assert created.proposal_id.startswith("GRAPH-PO-")
    assert created.blocks is None
    assert f"Created order {created.proposal_id} in Neo4j." in created.text
    stored = demo.vault.orders[created.proposal_id]
    assert stored.payload.items[0].qty == 20
    assert stored.payload.schedule_date == date(2026, 11, 20)


def test_a_request_for_options_lists_them_and_does_not_order(demo):
    reply = demo.say(
        "I want ITEM-A. Give me the top options.",
        intent("compare", item="ITEM-A", option_count=2),
        decision=_shortlist(),
    )
    assert "1. Supplier X" in reply.text
    assert "2. Supplier Y" in reply.text
    assert "No proposal created." in reply.text
    assert reply.proposal_id is None and demo.count() == 0


def test_buying_the_cheapest_orders_from_that_supplier(demo):
    reply = demo.say(
        "Buy 20 of ITEM-A from the cheapest supplier, needed next Friday.",
        intent(
            "purchase",
            item="ITEM-A",
            qty=20,
            required_by="next Friday",
            objective="cheapest",
            choose_for_me=True,
        ),
        decision=_shortlist("Supplier Y", "Supplier X"),
    )
    assert reply.proposal_id is not None
    stored = demo.vault.orders[reply.proposal_id]
    assert stored.payload.supplier == "Supplier Y"
    assert stored.payload.items[0].rate == 8
    assert stored.payload.items[0].qty == 20


def test_choosing_a_listed_option_orders_from_that_supplier(demo):
    demo.say(
        "Show me options for ITEM-A.",
        intent("compare", item="ITEM-A", option_count=2),
        decision=_shortlist(),
    )
    reply = demo.say(
        "Choose the second one, 20 units, next Friday.",
        intent("purchase", selection_index=2, qty=20, required_by="next Friday"),
    )
    assert reply.proposal_id is not None
    stored = demo.vault.orders[reply.proposal_id]
    assert stored.payload.supplier == "Supplier Y"
    assert stored.payload.items[0].qty == 20


def test_packaging_asks_item_and_quantity_even_after_a_comparison(demo):
    demo.say("Compare ITEM-A", intent("compare", item="ITEM-A"), decision=_shortlist())
    reply = demo.say("Order some packaging", intent("purchase", item="packaging"))
    assert "Which item" in reply.text and "How many units" in reply.text
    assert reply.blocks is None and demo.count() == 0
    filled = demo.say(
        "20 ITEM-A from Supplier X next Friday",
        intent("update", item="ITEM-A", qty=20, supplier="Supplier X", required_by="next Friday"),
    )
    assert filled.proposal_id and demo.count() == 1


@pytest.mark.parametrize(
    "values, expected",
    [
        ({"qty": None}, "How many units"),
        ({"qty": 0}, "positive quantity"),
        ({"qty": -2}, "positive quantity"),
        ({"supplier": "Unknown"}, "Choose a supplier"),
        ({"supplier": "Supplier"}, "Choose a supplier"),
        ({"required_by": None}, "When do you need"),
        ({"required_by": "2026-11-01"}, "today or later"),
        ({"required_by": "sometime soon"}, "YYYY-MM-DD"),
        ({"item": "UNKNOWN"}, "Which item"),
    ],
)
def test_incomplete_or_invalid_purchase_never_stages(demo, values, expected):
    parsed = (
        intent("purchase", item="ITEM-A", supplier="Supplier X", qty=20, required_by="next Friday")
        | values
    )
    reply = demo.say("A purchase request", parsed)
    assert expected in reply.text
    assert reply.proposal_id is None and demo.count() == 0


def test_followup_is_scoped_to_conversation(demo):
    demo.prepare()
    reply = demo.say("Make that 30", intent("update", qty=30), conversation_id="another-thread")
    assert "no open purchase" in reply.text and demo.count() == 1


def test_new_order_for_same_item_after_execution(demo):
    first = demo.prepare()
    second = demo.prepare()
    assert first.proposal_id != second.proposal_id
    assert demo.count() == 2


@pytest.mark.parametrize(
    "phrase, expected",
    [
        ("next Friday", "2026-11-20"),
        ("Friday", "2026-11-13"),
        ("next Monday", "2026-11-16"),
        ("tomorrow", "2026-11-14"),
        ("2026-12-01", "2026-12-01"),
    ],
)
def test_dates_have_a_defined_calendar_interpretation(phrase, expected):
    assert resolve_date(phrase, TODAY).isoformat() == expected


def test_active_listener_has_no_approval_execution_handler():
    slack = runpy.run_path(str(ROOT / "scripts/slack_listen.py"))
    assert "_act" not in slack
    assert "ErpNextDraftWriter" not in slack
