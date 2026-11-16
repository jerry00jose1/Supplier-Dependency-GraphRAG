import json
import runpy
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from langchain_core.messages import AIMessage

from procurement_agent.config import Settings
from procurement_agent.slack.item_reply import ItemReply
from procurement_agent.slack.router import order_answer, route_question


def test_order_followup_paginates_and_keeps_supplier() -> None:
    vault = Mock()
    vault.conversation.return_value = json.dumps({
        "workflow": "orders", "supplier": "Emirates", "offset": 0,
    })
    model = Mock()
    model.bind_tools.return_value.invoke.return_value = AIMessage(content="", tool_calls=[{
        "name": "QueryOrders", "args": {"followup": True, "next_page": True}, "id": "1",
    }])
    _, state = route_question(model, vault, "thread", "next page")
    assert state["supplier"] == "Emirates"
    assert state["offset"] == 20


def test_router_rejects_invented_supplier() -> None:
    vault = Mock()
    vault.conversation.return_value = None
    model = Mock()
    model.bind_tools.return_value.invoke.return_value = AIMessage(content="", tool_calls=[{
        "name": "QueryOrders", "args": {"supplier": "Invented"}, "id": "1",
    }])
    with pytest.raises(ValueError, match="grounded"):
        route_question(model, vault, "thread", "show my orders")


def test_missing_snapshot_does_not_query_orders() -> None:
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.return_value.single.return_value = None
    assert "not initialized" in order_answer(driver, "neo4j", "procurement:active", {})
    assert session.run.call_count == 1


def test_purchase_routes_to_existing_workflow_without_using_graph() -> None:
    module = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/slack_listen.py"))
    answer = module["_unified_answer"]
    vault, model, driver = Mock(), Mock(), Mock()
    vault.conversation.return_value = None
    model.bind_tools.return_value.invoke.return_value = AIMessage(content="")
    expected = ItemReply("Prepared", [{"type": "section"}], "proposal", None, None)
    with patch.dict(answer.__globals__, {"_answer": Mock(return_value=expected)}) as namespace:
        result = answer(Settings(_env_file=None), vault, model, driver,
                        "Prepare an order for 2 units of ITEM-A", "thread", event_id="C1:1.0")
        assert result is expected
        namespace["_answer"].assert_called_once()
    driver.session.assert_not_called()


def test_keyed_locks_scope_concurrency_per_conversation() -> None:
    module = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/slack_listen.py"))
    locks = module["_KeyedLocks"]()
    lock_a1 = locks.for_key("slack:thread-a")
    lock_a2 = locks.for_key("slack:thread-a")
    lock_b = locks.for_key("slack:thread-b")
    assert lock_a1 is lock_a2
    assert lock_a1 is not lock_b


def test_router_normalizes_conversational_status_words_and_keeps_valid_statuses() -> None:
    vault = Mock()
    vault.conversation.return_value = None
    model = Mock()
    model.bind_tools.return_value.invoke.return_value = AIMessage(
        content="",
        tool_calls=[{"name": "QueryOrders", "args": {"status": "current"}, "id": "1"}],
    )
    route, state = route_question(model, vault, "thread", "show me all of my current orders")
    assert route.workflow == "orders"
    assert route.status is None
    assert "status" not in state

    model.bind_tools.return_value.invoke.return_value = AIMessage(
        content="",
        tool_calls=[{"name": "QueryOrders", "args": {"status": "Open"}, "id": "2"}],
    )
    route_open, state_open = route_question(model, vault, "thread", "show me my open orders")
    assert route_open.status == "Open"
    assert state_open["status"] == "Open"


def test_router_extracts_buyer_intent_in_single_llm_call() -> None:
    vault = Mock()
    vault.conversation.return_value = None
    model = Mock()
    model.bind_tools.return_value.invoke.return_value = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "ProcurementAssistant",
                "args": {
                    "action": "purchase",
                    "item": "S-66100",
                    "supplier": "Jebel Enterprises LLC 120",
                    "qty": 2,
                    "required_by": "next Thursday",
                },
                "id": "1",
            }
        ],
    )
    route, _ = route_question(
        model,
        vault,
        "thread",
        "Order 2 units of S-66100 from Jebel Enterprises LLC 120 next Thursday",
    )
    assert route.workflow == "procurement"
    assert route.intent is not None
    assert route.intent.action == "purchase"
    assert route.intent.item == "S-66100"
    assert route.intent.qty == 2.0


