"""Opt in with RUN_NEO4J_TESTS=1 after make graph-up. Keeps immutable test snapshots."""

from __future__ import annotations

import os

import pytest

from procurement_agent.knowledge_graph.cli import investigate, verify_results
from procurement_agent.knowledge_graph.demo import build_demo
from procurement_agent.knowledge_graph.models import Snapshot
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
