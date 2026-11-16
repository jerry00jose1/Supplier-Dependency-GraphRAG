"""Parameterized graph retrieval for conversational investigations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from neo4j import Driver
from pydantic import BaseModel, Field


class Entity(BaseModel):
    id: str
    kind: str
    name: str
    aliases: list[str] = Field(default_factory=list)


class GraphReader:
    def __init__(
        self,
        driver: Driver,
        database: str,
        snapshot: str,
        aliases: dict[str, list[str]] | None = None,
    ) -> None:
        self.driver, self.database, self.snapshot = driver, database, snapshot
        self.aliases = aliases or {}
        with driver.session(database=database) as session:
            row = session.run(
                "MATCH (s:GraphSnapshot {id:$id, ready:true}) RETURN s.snapshot_date AS date",
                id=snapshot,
            ).single()
            if row is None:
                raise ValueError("Selected graph snapshot is not loaded and ready")
            self.snapshot_date = str(row["date"])

    def catalog(self) -> list[Entity]:
        with self.driver.session(database=self.database) as session:
            rows = session.run(
                "MATCH (n:GraphEntity {snapshot:$snapshot}) "
                "WHERE n:Supplier OR n:Item OR n:Facility OR "
                "(n:Claim AND n.predicate='DISRUPTION' AND n.status='accepted') "
                "RETURN n.id AS id, coalesce(n.name,n.id) AS name, "
                "[label IN labels(n) WHERE label <> 'GraphEntity'][0] AS kind "
                "ORDER BY id LIMIT 501",
                snapshot=self.snapshot,
            ).data()
        if len(rows) > 500:
            raise ValueError("Entity catalog exceeds demo limit; use indexed resolution")
        return [Entity(**row, aliases=self.aliases.get(str(row["id"]), [])) for row in rows]

    def notices(self, facility: str | None, supplier: str | None) -> list[dict[str, Any]]:
        with self.driver.session(database=self.database) as session:
            return session.run(
                "MATCH (n:Claim {snapshot:$snapshot, predicate:'DISRUPTION', status:'accepted'})"
                "-[:SUBJECT]->(f:Facility) "
                "WHERE ($facility IS NULL OR f.id=$facility) AND ($supplier IS NULL OR EXISTS {"
                " MATCH (c:Claim {snapshot:$snapshot, "
                "predicate:'USES_FACILITY', status:'accepted'})"
                "-[:SUBJECT]->(s:Supplier {id:$supplier}) MATCH (c)-[:OBJECT]->(f) "
                "WHERE (c.valid_from IS NULL OR date(c.valid_from)<=date(n.end)) "
                "AND (properties(c).valid_to IS NULL OR "
                "date(properties(c).valid_to)>=date(n.start)) }) "
                "RETURN n.id AS notice_id, f.id AS facility_id, n.start AS start, n.end AS end "
                "ORDER BY notice_id LIMIT 26",
                snapshot=self.snapshot,
                facility=facility,
                supplier=supplier,
            ).data()

    def query(self, name: str, **parameters: Any) -> list[dict[str, Any]]:
        if name not in {"exposure", "alternatives", "dependencies"}:
            raise ValueError("Unknown investigation query")
        query = (Path(__file__).parent / "queries" / f"{name}.cypher").read_text()
        with self.driver.session(database=self.database) as session:
            return session.run(
                query,
                {
                    "snapshot": self.snapshot,
                    "notice_id": "",
                    "item_id": "",
                    "excluded_supplier": "",
                    "facility_id": "",
                    "limit": 26,
                    "supplier_filter": "",
                    "item_filter": "",
                    **parameters,
                },
            ).data()


def demo_aliases(root: Path) -> dict[str, list[str]]:
    return dict(json.loads((root / "data/graph_demo/aliases.json").read_text()))
