"""Validated, source-scoped graph snapshots for the first ingestion milestone."""

from __future__ import annotations

import hashlib
import math
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

Label = Literal[
    "Supplier", "Item", "PurchaseOrder", "OrderLine", "CostCentre", "Facility",
    "Document", "Passage", "Claim", "Receipt", "ReceiptLine", "Invoice", "InvoiceLine",
]
Relation = Literal[
    "FROM_SUPPLIER", "CONTAINS", "ORDERS", "CHARGED_TO", "SUPPORTS",
    "SUBJECT", "OBJECT", "FOR_ITEM", "MATCHES_ORDER_LINE",
]


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: Label
    origin: Literal["mizan", "synthetic", "erpnext"]
    source: str
    properties: dict[str, str | float | int]

    @model_validator(mode="after")
    def validate_properties(self) -> Node:
        if not self.id or not self.source:
            raise ValueError("Every node needs an ID and source")
        reserved = {"key", "snapshot", "id", "origin", "source", "snapshot_date"}
        if reserved.intersection(self.properties):
            raise ValueError("Properties cannot overwrite provenance or identity")
        if any(isinstance(v, float) and not math.isfinite(v) for v in self.properties.values()):
            raise ValueError("Numeric properties must be finite")
        required = {
            "Supplier": {"name"}, "Item": {"name", "uom"},
            "PurchaseOrder": {"status", "order_date", "currency"},
            "OrderLine": {"quantity", "received_quantity", "need_by", "unit_price", "uom"},
            "Claim": {"predicate", "status", "evidence_kind", "recorded_at"},
            "Passage": {"text"},
        }.get(self.label, set())
        if not required.issubset(self.properties):
            raise ValueError(f"Missing required properties for {self.label}")
        for key in ("need_by", "order_date", "start", "end", "valid_from", "valid_to"):
            if key in self.properties:
                date.fromisoformat(str(self.properties[key]))
        if self.label == "OrderLine":
            qty = float(self.properties["quantity"])
            received = float(self.properties["received_quantity"])
            if qty <= 0 or received < 0 or float(self.properties["unit_price"]) < 0:
                raise ValueError("Invalid order quantity or price")
        if self.properties.get("predicate") == "DISRUPTION":
            start = date.fromisoformat(str(self.properties["start"]))
            end = date.fromisoformat(str(self.properties["end"]))
            if end < start:
                raise ValueError("Disruption ends before it starts")
        return self


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    relation: Relation
    target: str


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_date: date
    nodes: list[Node]
    edges: list[Edge]

    @model_validator(mode="after")
    def validate_graph(self) -> Snapshot:
        nodes = {node.id: node for node in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError("Duplicate node IDs")
        triples = {(e.source, e.relation, e.target) for e in self.edges}
        if len(triples) != len(self.edges):
            raise ValueError("Duplicate relationships")
        allowed = {
            "FROM_SUPPLIER": {("PurchaseOrder", "Supplier")},
            "CONTAINS": {("PurchaseOrder", "OrderLine"), ("Document", "Passage"),
                         ("Receipt", "ReceiptLine"), ("Invoice", "InvoiceLine")},
            "MATCHES_ORDER_LINE": {("ReceiptLine", "OrderLine"), ("InvoiceLine", "OrderLine")},
            "ORDERS": {("OrderLine", "Item")},
            "CHARGED_TO": {("OrderLine", "CostCentre")},
            "SUPPORTS": {("Passage", "Claim")},
            "SUBJECT": {("Claim", "Supplier"), ("Claim", "Facility")},
            "OBJECT": {("Claim", "Facility")},
            "FOR_ITEM": {("Claim", "Item")},
        }
        for edge in self.edges:
            if edge.source not in nodes or edge.target not in nodes:
                raise ValueError("Relationship references an unknown node")
            if (nodes[edge.source].label, nodes[edge.target].label) not in allowed[edge.relation]:
                raise ValueError("Invalid relationship endpoints")
        for node in self.nodes:
            outgoing = [e for e in self.edges if e.source == node.id]
            incoming = [e for e in self.edges if e.target == node.id]
            required = {"PurchaseOrder": ["FROM_SUPPLIER"],
                        "OrderLine": ["ORDERS"],
                        "Claim": ["SUBJECT"]}.get(node.label, [])
            if any(sum(e.relation == rel for e in outgoing) != 1 for rel in required):
                raise ValueError(f"Missing or ambiguous links for {node.id}")
            if (node.label in {"OrderLine", "Passage"}
                    and sum(e.relation == "CONTAINS" for e in incoming) != 1):
                raise ValueError("A line or passage must have exactly one parent")
            if node.label == "Claim":
                if not any(e.relation == "SUPPORTS" for e in incoming):
                    raise ValueError("Claim has no supporting passage")
                if (node.properties["predicate"] == "USES_FACILITY"
                        and sum(e.relation == "OBJECT" for e in outgoing) != 1):
                    raise ValueError("Facility claim requires one facility")
        return self

    @property
    def fingerprint(self) -> str:
        """Content-addressed snapshots never silently replace earlier evidence."""
        import json

        payload = self.model_dump(mode="json")
        payload["nodes"] = sorted(payload["nodes"], key=lambda n: n["id"])
        payload["edges"] = sorted(
            payload["edges"], key=lambda e: (e["source"], e["relation"], e["target"])
        )
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
