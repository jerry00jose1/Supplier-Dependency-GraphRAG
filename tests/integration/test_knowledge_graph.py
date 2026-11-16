"""Opt in with RUN_NEO4J_TESTS=1 after make graph-up. Keeps immutable test snapshots."""

from __future__ import annotations

import os

import pytest

from procurement_agent.knowledge_graph.cli import investigate, verify_results
from procurement_agent.knowledge_graph.conversation import Context, GraphConversation, Plan
from procurement_agent.knowledge_graph.demo import ROOT, build_demo
from procurement_agent.knowledge_graph.evaluate import EvidenceEcho, cases
from procurement_agent.knowledge_graph.models import Snapshot
from procurement_agent.knowledge_graph.procurement import load_graph_evidence
from procurement_agent.knowledge_graph.retrieval import GraphReader, demo_aliases
from procurement_agent.knowledge_graph.store import GraphSettings, load_snapshot

pytestmark = pytest.mark.skipif(os.getenv("RUN_NEO4J_TESTS") != "1", reason="Needs local Neo4j")


def test_import_queries_and_changed_snapshot_isolation() -> None:
    settings = GraphSettings()
    original = build_demo()
    with settings.driver() as driver:
        first = load_snapshot(driver, settings.database, original)
        assert load_snapshot(driver, settings.database, original) == first
        results = investigate(driver, settings.database, original.fingerprint)
        verify_results(results)
        # Change the notice period: no line is now inside the event window.
        changed = original.model_dump()
        notice = next(n for n in changed["nodes"] if n["id"] == "synthetic:Claim:shutdown-x")
        notice["properties"]["start"] = "2027-01-01"
        notice["properties"]["end"] = "2027-01-10"
        newer = Snapshot.model_validate(changed)
        load_snapshot(driver, settings.database, newer)
        modified = investigate(driver, settings.database, newer.fingerprint)
        assert len(modified["exposure"]) == 2
        assert not any(row["within_period"] for row in modified["exposure"])
        # Querying the original snapshot still reproduces its original evidence.
        assert investigate(driver, settings.database, original.fingerprint) == results


def test_ten_conversation_retrieval_scenarios() -> None:
    settings = GraphSettings()
    snapshot = build_demo()
    with settings.driver() as driver:
        load_snapshot(driver, settings.database, snapshot)
        reader = GraphReader(driver, settings.database, snapshot.fingerprint, demo_aliases(ROOT))
        service = GraphConversation(reader, EvidenceEcho())
        previous = Context()
        for case in cases():
            plan = Plan(
                intent=case["intent"],
                followup=case.get("followup", False),
                **{f: case.get(f) for f in ("supplier", "facility", "item", "notice")},
            )
            result = service.execute(
                case["question"], plan, previous if case.get("followup") else Context()
            )
            if case["name"] == "facility disruption":
                previous = result.context
            assert result.status == case["status"], case["name"]
            assert set(case["required_assessments"]).issubset(
                {e.facts.get("assessment") for e in result.evidence}
            ), case["name"]


def test_targeted_graph_evidence_queries_without_full_table_scan() -> None:
    settings = GraphSettings()
    with settings.driver() as driver:
        evidence = load_graph_evidence(driver, settings.database)
        codes = evidence.item_codes()
        assert "S-66100" in codes
        sellers = evidence.seller_facts("S-66100")
        assert len(sellers) == 5
        assert any(s.supplier == "Jebel Enterprises LLC 120" for s in sellers)
        po = evidence.purchase_order("PUR-ORD-2026-00059")
        assert po is not None and po["order"]["supplier"] == "Jebel Services LLC 075"
        mismatches = evidence.invoice_mismatches(
            supplier="Jebel Services LLC 075", invoice_name="ACC-PINV-2026-00068"
        )
        assert len(mismatches) == 2
        assert any("quantity" in m.reasons and "price" in m.reasons for m in mismatches)

