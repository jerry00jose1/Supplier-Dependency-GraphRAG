from __future__ import annotations

import math

import pytest

from procurement_agent.knowledge_graph.demo import build_demo
from procurement_agent.knowledge_graph.models import Snapshot


def test_history_and_synthetic_open_orders_have_distinct_provenance() -> None:
    graph = build_demo()
    historical = [n for n in graph.nodes if n.label == "PurchaseOrder" and n.origin == "mizan"]
    assert historical
    assert all(n.properties["status"] == "Closed" for n in historical)
    assert all(n.properties["currency"] == "USD" for n in historical)
    opened = [n for n in graph.nodes
              if n.label == "PurchaseOrder" and n.properties["status"] == "Open"]
    assert len(opened) == 1
    assert opened[0].origin == "synthetic"
    claims = [n for n in graph.nodes if n.label == "Claim"]
    assert len(claims) == 4
    assert all(n.origin == "synthetic" for n in claims)


def test_snapshot_identity_is_repeatable_and_order_independent() -> None:
    graph = build_demo()
    assert graph.fingerprint == build_demo().fingerprint
    reordered = Snapshot(snapshot_date=graph.snapshot_date,
                         nodes=list(reversed(graph.nodes)), edges=list(reversed(graph.edges)))
    assert reordered.fingerprint == graph.fingerprint
    changed = graph.model_dump()
    passage = next(n for n in changed["nodes"] if n["label"] == "Passage")
    passage["properties"]["text"] += " Updated."
    assert Snapshot.model_validate(changed).fingerprint != graph.fingerprint


@pytest.mark.parametrize("damage", ["duplicate", "dangling", "unsupported_claim", "nan", "date"])
def test_invalid_graph_is_rejected_before_database_access(damage: str) -> None:
    raw = build_demo().model_dump()
    if damage == "duplicate":
        raw["nodes"].append(raw["nodes"][0])
    elif damage == "dangling":
        raw["edges"][0]["target"] = "missing"
    elif damage == "unsupported_claim":
        raw["edges"] = [e for e in raw["edges"] if e["relation"] != "SUPPORTS"]
    else:
        line = next(n for n in raw["nodes"] if n["label"] == "OrderLine")
        if damage == "nan":
            line["properties"]["quantity"] = math.nan
        else:
            line["properties"]["need_by"] = "2026-02-30"
    with pytest.raises(ValueError):
        Snapshot.model_validate(raw)
