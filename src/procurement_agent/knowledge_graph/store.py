"""Atomic immutable snapshots; prior snapshots remain available for audit."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from neo4j import Driver, GraphDatabase, ManagedTransaction
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from procurement_agent.knowledge_graph.models import Snapshot


class GraphSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEO4J_", env_file=".env", extra="ignore")
    uri: str = "bolt://127.0.0.1:7687"
    user: str = "neo4j"
    password: SecretStr = SecretStr("supplier-demo-local")
    database: str = "neo4j"

    def driver(self) -> Driver:
        return GraphDatabase.driver(
            self.uri, auth=(self.user, self.password.get_secret_value()),
            connection_timeout=10, max_transaction_retry_time=15,
        )


def load_snapshot(driver: Driver, database: str, snapshot: Snapshot) -> dict[str, Any]:
    # Validate even if a caller mutated a previously validated model.
    snapshot = Snapshot.model_validate(snapshot.model_dump())
    with driver.session(database=database) as session:
        session.run("CREATE CONSTRAINT graph_entity_key IF NOT EXISTS "
                    "FOR (n:GraphEntity) REQUIRE n.key IS UNIQUE").consume()
        session.run("CREATE CONSTRAINT graph_snapshot_id IF NOT EXISTS "
                    "FOR (n:GraphSnapshot) REQUIRE n.id IS UNIQUE").consume()
        return session.execute_write(_write, snapshot)


def _write(tx: ManagedTransaction, snapshot: Snapshot) -> dict[str, Any]:
    fingerprint = snapshot.fingerprint
    marker = tx.run(
        "MERGE (s:GraphSnapshot {id:$id}) "
        "ON CREATE SET s.ready=false, s.snapshot_date=$date "
        "SET s.lock_version=coalesce(s.lock_version,0)+1 RETURN s.ready AS ready",
        id=fingerprint, date=snapshot.snapshot_date.isoformat(),
    ).single(strict=True)
    if not marker["ready"]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in snapshot.nodes:
            groups[node.label].append({
                "key": f"{fingerprint}/{node.id}", "id": node.id, "snapshot": fingerprint,
                "snapshot_date": snapshot.snapshot_date.isoformat(),
                "origin": node.origin, "source": node.source, **node.properties,
            })
        # Labels/types come only from validated Literal enums, never external text.
        for label, rows in groups.items():
            tx.run(f"UNWIND $rows AS row MERGE (n:GraphEntity:{label} {{key:row.key}}) "
                   "SET n = row", rows=rows).consume()
        links: dict[str, list[dict[str, str]]] = defaultdict(list)
        for edge in snapshot.edges:
            links[edge.relation].append({"source": f"{fingerprint}/{edge.source}",
                                         "target": f"{fingerprint}/{edge.target}"})
        for relation, rows in links.items():
            tx.run("UNWIND $rows AS row MATCH (a:GraphEntity {key:row.source}), "
                   "(b:GraphEntity {key:row.target}) "
                   f"MERGE (a)-[:{relation}]->(b)", rows=rows).consume()
    counts = _counts(tx, fingerprint)
    if counts != {"nodes": len(snapshot.nodes), "relationships": len(snapshot.edges)}:
        raise ValueError(f"Snapshot counts differ from validated input: {counts}")
    tx.run("MATCH (s:GraphSnapshot {id:$id}) SET s.ready=true", id=fingerprint).consume()
    return {"snapshot": fingerprint, "snapshot_date": snapshot.snapshot_date.isoformat(), **counts}


def _counts(tx: ManagedTransaction, fingerprint: str) -> dict[str, int]:
    nodes = tx.run("MATCH (n:GraphEntity {snapshot:$snapshot}) RETURN count(n) AS count",
                   snapshot=fingerprint).single(strict=True)
    edges = tx.run("MATCH (a:GraphEntity {snapshot:$snapshot})-[r]->"
                   "(b:GraphEntity {snapshot:$snapshot}) RETURN count(r) AS count",
                   snapshot=fingerprint).single(strict=True)
    return {"nodes": int(nodes["count"]), "relationships": int(edges["count"])}
