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
from procurement_agent.slack.storage import ThreadStore, slack_text


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


def test_durable_context_and_uncertain_delivery_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "threads.sqlite3"
    store = ThreadStore(path)
    reply = Reply(text="Finding", status="answered", context=Context(snapshot="s", item="item:1"))
    assert store.reserve("event-1", "thread-user", reply)
    resumed = ThreadStore(path)
    assert resumed.context("thread-user").item == "item:1"
    assert resumed.context("another-user").item is None
    assert resumed.seen("event-1")
    assert not resumed.reserve("event-1", "thread-user", reply)
    resumed.sent("event-1")
    assert resumed.seen("event-1")


def test_slack_mentions_are_escaped() -> None:
    assert slack_text("<@USER> & text") == "&lt;@USER&gt; &amp; text"
