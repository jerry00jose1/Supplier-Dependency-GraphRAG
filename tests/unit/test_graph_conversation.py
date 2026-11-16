from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage

from procurement_agent.knowledge_graph.conversation import (
    Context,
    Evidence,
    GraphConversation,
    Plan,
    Reply,
    resolve,
)
from procurement_agent.knowledge_graph.retrieval import Entity
from procurement_agent.knowledge_graph.slack_app import SlackInvestigator, ThreadStore, thread_key


def plan(**kwargs: Any) -> Plan:
    return Plan(
        **{
            "intent": "exposure",
            "followup": False,
            "supplier": None,
            "facility": None,
            "item": None,
            "notice": None,
            **kwargs,
        }
    )


def reader() -> Mock:
    backend = Mock(snapshot="snapshot-1", snapshot_date="2026-11-15")
    backend.catalog.return_value = [
        Entity(id="s:A", kind="Supplier", name="Alpha", aliases=["backup supplier"]),
        Entity(id="s:B", kind="Supplier", name="Beta", aliases=["backup supplier"]),
        Entity(id="f:X", kind="Facility", name="Facility X"),
    ]
    return backend


def test_resolution_requires_exact_alias_and_surfaces_ambiguity() -> None:
    catalog = reader().catalog()
    assert len(resolve(" BACKUP supplier ", "Supplier", catalog)) == 2
    assert resolve("Al", "Supplier", catalog) == []
    assert resolve("Alpha", "Supplier", catalog)[0].id == "s:A"


@pytest.mark.parametrize("mention", ["Unknown", "backup supplier"])
def test_unresolved_names_never_run_a_query(mention: str) -> None:
    backend = reader()
    reply = GraphConversation(backend, Mock()).execute(
        f"Investigate {mention}", plan(supplier=mention), Context()
    )
    assert reply.status == "clarify"
    backend.query.assert_not_called()


def test_planner_cannot_substitute_a_name_not_in_question() -> None:
    backend = reader()
    reply = GraphConversation(backend, Mock()).execute(
        "Investigate Unknown", plan(supplier="Alpha"), Context()
    )
    assert reply.status == "clarify"
    backend.notices.assert_not_called()


def test_unsupported_request_never_retrieves_or_writes() -> None:
    backend = reader()
    reply = GraphConversation(backend, Mock()).execute(
        "Approve the purchase", plan(intent="unsupported"), Context()
    )
    assert reply.status == "unsupported"
    backend.query.assert_not_called()


def test_snapshot_change_clears_followup_context() -> None:
    backend = reader()
    reply = GraphConversation(backend, Mock()).execute(
        "Which alternatives?",
        plan(intent="alternatives", followup=True),
        Context(snapshot="old", item="old:item"),
    )
    assert reply.status == "clarify"
    assert reply.context.item is None
    backend.query.assert_not_called()


def test_citation_and_coverage_validation_falls_back_to_actual_facts() -> None:
    evidence = [
        Evidence(
            id="E1",
            kind="dependency",
            facts={"supplier_id": "D", "assessment": "facility_unknown", "evidence": []},
        )
    ]
    model = Mock()
    model.invoke.return_value = AIMessage(
        content=json.dumps(
            {"statements": [{"text": "Invented explanation", "citations": ["E999"]}]}
        )
    )
    reply = GraphConversation(reader(), model).explain("What about D?", Context(), evidence, False)
    assert reply.status == "fallback"
    assert "Invented explanation" not in reply.text
    assert "facility_unknown" in reply.text


def test_malformed_plan_requests_clarification() -> None:
    model = Mock()
    model.invoke.return_value = AIMessage(content="not JSON")
    reply = GraphConversation(reader(), model).answer("Investigate Facility X")
    assert reply.status == "clarify"


def test_alias_cannot_match_only_prefix_of_unknown_name() -> None:
    backend = reader()
    reply = GraphConversation(backend, Mock()).execute(
        "Investigate AlphaUnknown", plan(supplier="Alpha"), Context()
    )
    assert reply.status == "clarify"
    backend.notices.assert_not_called()


def test_clarification_retains_other_explicit_entities() -> None:
    backend = reader()
    backend.catalog.return_value.append(Entity(id="i:H", kind="Item", name="HDPE"))
    service = GraphConversation(backend, Mock())
    reply = service.execute(
        "Does the backup supplier share Facility X for HDPE?",
        plan(intent="dependencies", supplier="backup supplier", facility="Facility X", item="HDPE"),
        Context(),
    )
    assert reply.status == "clarify"
    assert reply.context.facility == "f:X"
    assert reply.context.item == "i:H"
    backend.query.return_value = []
    resolved = service.execute(
        "Beta", plan(intent="dependencies", supplier="Beta", followup=True), reply.context
    )
    assert resolved.status == "no_evidence"
    assert resolved.context.focus_supplier == "s:B"
    assert backend.query.call_args.kwargs["supplier_filter"] == "s:B"


def test_omitted_critical_evidence_triggers_fallback() -> None:
    facts = {"supplier_id": "D", "assessment": "facility_unknown", "evidence": []}
    evidence = [
        Evidence(id="E1", kind="dependency", facts=facts),
        Evidence(id="E2", kind="dependency", facts=facts),
    ]
    model = Mock()
    model.invoke.return_value = AIMessage(
        content=json.dumps({"statements": [{"text": "Only one finding", "citations": ["E1"]}]})
    )
    reply = GraphConversation(reader(), model).explain("Explain", Context(), evidence, False)
    assert reply.status == "fallback"
    assert "[E2]" in reply.text


def test_slack_context_is_durable_and_scoped_and_replays_do_not_post(tmp_path: Path) -> None:
    path = tmp_path / "threads.sqlite3"
    service = Mock()
    service.answer.return_value = Reply(
        text="Finding <@USER>", status="answered", context=Context(snapshot="s", item="item:1")
    )
    client = Mock()
    handler = SlackInvestigator(service, ThreadStore(path), "CHANNEL")
    event = {"channel": "CHANNEL", "ts": "1", "user": "USER", "text": "Facility X"}
    handler.handle(event, client)
    handler.handle(event, client)
    assert client.chat_postMessage.call_count == 1
    payload = client.chat_postMessage.call_args.kwargs
    assert payload["thread_ts"] == "1"
    assert "<@USER>" not in payload["text"]
    resumed = SlackInvestigator(service, ThreadStore(path), "CHANNEL")
    resumed.handle({**event, "ts": "2", "thread_ts": "1", "text": "Which alternatives?"}, client)
    assert service.answer.call_args.args[1].item == "item:1"
    assert ThreadStore(path).context(thread_key("CHANNEL", "1", "ANOTHER")).item is None
    assert ThreadStore(path).context(thread_key("CHANNEL", "OTHER", "USER")).item is None


@pytest.mark.parametrize(
    "changes", [{"bot_id": "BOT"}, {"subtype": "message_changed"}, {"channel": "OTHER"}]
)
def test_slack_ignores_bots_edits_and_other_channels(
    tmp_path: Path, changes: dict[str, str]
) -> None:
    service, client = Mock(), Mock()
    handler = SlackInvestigator(service, ThreadStore(tmp_path / "s.sqlite3"), "C")
    handler.handle({"channel": "C", "user": "U", "ts": "1", "text": "hi", **changes}, client)
    service.answer.assert_not_called()
    client.chat_postMessage.assert_not_called()


def test_uncertain_slack_delivery_is_not_blindly_repeated(tmp_path: Path) -> None:
    service, client = Mock(), Mock()
    service.answer.return_value = Reply(text="result", context=Context(), status="answered")
    client.chat_postMessage.side_effect = TimeoutError()
    handler = SlackInvestigator(service, ThreadStore(tmp_path / "s.sqlite3"), "C")
    event = {"channel": "C", "ts": "1", "user": "U", "text": "hi"}
    handler.handle(event, client)
    handler.handle(event, client)
    assert client.chat_postMessage.call_count == 1
