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
    assert "No ERP snapshot" in order_answer(driver, "neo4j", "erpnext:test", {})
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
                        "Prepare an order for 2 units of ITEM-A", "thread")
        assert result is expected
        namespace["_answer"].assert_called_once()
    driver.session.assert_not_called()
