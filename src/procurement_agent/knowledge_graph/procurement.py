"""Graph-backed evidence adapter for the existing procurement reasoning tools."""

import json
from typing import Any

from procurement_agent.config import Settings
from procurement_agent.knowledge_graph.store import GraphSettings
from procurement_agent.policy.corpus import PolicyChunk
from procurement_agent.policy.retrieve import retrieve, shared_embedder


class GraphPolicy:
    def __init__(self, chunks: list[PolicyChunk]) -> None:
        self.chunks = chunks

    def embed(self, texts: list[str]) -> list[list[float]]:
        return shared_embedder().embed(texts)

    def retrieve(self, query: str, limit: int) -> list[PolicyChunk]:
        return retrieve(query, shared_embedder(), chunks=self.chunks, limit=limit)


def load_evidence(settings: Settings) -> tuple[list[list[dict[str, Any]]], GraphPolicy, str]:
    graph = GraphSettings()
    with graph.driver() as driver, driver.session(database=graph.database) as session:
        marker = session.run(
            "MATCH (p:GraphProjection {id:$id}) "
            "MATCH (s:GraphSnapshot {id:p.snapshot,ready:true}) "
            "RETURN p.snapshot AS snapshot,p.synced_at AS refreshed",
            id="procurement:active",
        ).single()
        if marker is None:
            raise ValueError("Import ERP evidence with make graph-sync first")
        snapshot = marker["snapshot"]
        groups = []
        for header, child in [("PurchaseOrder", "OrderLine"), ("Receipt", "ReceiptLine"),
                              ("Invoice", "InvoiceLine")]:
            rows = session.run(
                f"MATCH (h:{header} {{snapshot:$snapshot}}) "
                f"OPTIONAL MATCH (h)-[:CONTAINS]->(l:{child}) "
                "RETURN h.record AS header, collect(l.record) AS lines", snapshot=snapshot,
            ).data()
            headers: list[dict[str, Any]] = []
            lines: list[dict[str, Any]] = []
            for row in rows:
                if row["header"] is None:
                    raise ValueError("Snapshot lacks procurement records; run make graph-sync")
                record = json.loads(row["header"])
                if record["docstatus"] == 1:
                    headers.append(record)
                    lines.extend(json.loads(line) for line in row["lines"])
            groups.extend([headers, lines])
        sections = session.run(
            "MATCH (:Document {snapshot:$snapshot,id:'policy:northwind'})"
            "-[:CONTAINS]->(p:Passage) "
            "RETURN p.citation_id AS id,p.heading AS heading,p.text AS text",
            snapshot=snapshot,
        ).data()
        if not sections:
            raise ValueError("Snapshot lacks policy evidence; run make graph-sync")
        return groups, GraphPolicy([PolicyChunk(**row) for row in sections]), marker["refreshed"]
