"""Authoritative graph state and atomic direct purchase creation."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from procurement_agent.approvals.orders import DraftPurchaseOrder
from procurement_agent.approvals.service import ApprovalVault, StoredProposal
from procurement_agent.knowledge_graph.store import GraphSettings

ACTIVE = "procurement:active"


class GraphVault(ApprovalVault):
    """Reuse conversation interfaces without creating approval proposals."""

    def __init__(self, driver: Any, database: str) -> None:
        self.driver, self.database = driver, database
        self.event_id = ""

    def conversation(self, conversation_id: str) -> str | None:
        with self.driver.session(database=self.database) as s:
            row = s.run(
                "MATCH (c:ProcurementContext {id:$id}) RETURN c.body AS body", id=conversation_id
            ).single()
            return row["body"] if row else None

    def save_conversation(self, conversation_id: str, state_json: str) -> None:
        state = json.loads(state_json)
        if str(state.get("proposal_id", "")).startswith("GRAPH-PO-"):
            state["purchasing"] = False
            state["proposal_id"] = None
            state_json = json.dumps(state)
        with self.driver.session(database=self.database) as s:
            s.run(
                "MERGE (c:ProcurementContext {id:$id}) SET c.body=$body",
                id=conversation_id,
                body=state_json,
            ).consume()

    def get(self, proposal_id: str) -> StoredProposal | None:
        return None

    def stage(self, idempotency_key: str, order: DraftPurchaseOrder) -> StoredProposal:
        if not self.event_id:
            raise ValueError("A stable Slack event identifier is required")
        name = "GRAPH-PO-" + hashlib.sha256(self.event_id.encode()).hexdigest()[:20].upper()
        with self.driver.session(database=self.database) as s:
            s.execute_write(self._create, name, order)
        return StoredProposal(
            name, self.event_id, "executed", order, order.fingerprint(), None, name, None, None
        )

    def _create(self, tx: Any, name: str, order: DraftPurchaseOrder) -> None:
        marker = tx.run(
            "MATCH (p:GraphProjection {id:$id}) "
            "SET p.lock=coalesce(p.lock,0)+1 RETURN p.snapshot AS snapshot",
            id=ACTIVE,
        ).single(strict=True)
        snapshot = marker["snapshot"]
        existing = tx.run(
            "MATCH (o:PurchaseOrder {snapshot:$s,name:$name}) RETURN o.name", s=snapshot, name=name
        ).single()
        if existing:
            return
        supplier = tx.run(
            "MATCH (s:Supplier {snapshot:$s,name:$name}) RETURN s.key AS key",
            s=snapshot,
            name=order.supplier,
        ).single(strict=True)
        header = dict(
            name=name,
            supplier=order.supplier,
            transaction_date=str(order.transaction_date),
            status="Open",
            docstatus=1,
            company=order.company,
            currency=order.currency,
        )
        key = snapshot + "/graph:PurchaseOrder:" + name
        tx.run(
            "MATCH (s:Supplier {key:$supplier}) "
            "CREATE (o:GraphEntity:PurchaseOrder {key:$key,id:$id,snapshot:$snapshot,"
            "name:$name,origin:'graph',source:'Slack',status:'Open',docstatus:1,"
            "order_date:$date,currency:$currency,record:$record,created_at:$created}) "
            "CREATE (o)-[:FROM_SUPPLIER]->(s)",
            supplier=supplier["key"],
            key=key,
            id="graph:PurchaseOrder:" + name,
            snapshot=snapshot,
            name=name,
            date=str(order.transaction_date),
            currency=order.currency,
            record=json.dumps(header),
            created=datetime.now(UTC).isoformat(),
        ).consume()
        for index, line in enumerate(order.items):
            item = tx.run(
                "MATCH (i:Item {snapshot:$s}) WHERE i.id ENDS WITH $suffix "
                "RETURN i.key AS key,i.uom AS uom",
                s=snapshot,
                suffix=":Item:" + line.item_code,
            ).single(strict=True)
            line_name = f"{name}-{index + 1}"
            record = dict(
                name=line_name,
                parent=name,
                item_code=line.item_code,
                qty=line.qty,
                rate=line.rate,
                uom=item["uom"],
                received_qty=0,
                schedule_date=str(order.schedule_date),
                warehouse=order.warehouse,
                description=line.item_code,
            )
            tx.run(
                "MATCH (o {key:$order}),(i {key:$item}) "
                "CREATE (l:GraphEntity:OrderLine {key:$key,id:$id,snapshot:$s,"
                "quantity:$qty,received_quantity:0,unit_price:$rate,uom:$uom,need_by:$due,"
                "record:$record,origin:'graph',source:'Slack'}) "
                "CREATE (o)-[:CONTAINS]->(l) CREATE (l)-[:ORDERS]->(i)",
                order=key,
                item=item["key"],
                key=snapshot + "/graph:OrderLine:" + line_name,
                id="graph:OrderLine:" + line_name,
                s=snapshot,
                qty=line.qty,
                rate=line.rate,
                uom=item["uom"],
                due=str(order.schedule_date),
                record=json.dumps(record),
            ).consume()


def main() -> None:
    """Adopt a copy of existing evidence, preserving historical snapshots."""
    graph = GraphSettings()
    with graph.driver() as driver, driver.session(database=graph.database) as s:
        s.run(
            "CREATE CONSTRAINT procurement_context_id IF NOT EXISTS "
            "FOR (c:ProcurementContext) REQUIRE c.id IS UNIQUE"
        ).consume()

        def migrate(tx: Any) -> None:
            if tx.run("MATCH (p:GraphProjection {id:$id}) RETURN p", id=ACTIVE).single():
                return
            rows = tx.run(
                "MATCH (p:GraphProjection) WHERE p.id STARTS WITH 'erpnext:' "
                "RETURN p.snapshot AS snapshot"
            ).data()
            if len(rows) != 1:
                raise ValueError("Expected exactly one imported procurement projection")
            old = rows[0]["snapshot"]
            for row in tx.run(
                "MATCH (n:GraphEntity {snapshot:$s}) "
                "RETURN labels(n) AS labels,properties(n) AS props",
                s=old,
            ).data():
                props = row["props"]
                props["snapshot"] = ACTIVE
                props["key"] = ACTIVE + "/" + props["id"]
                labels = ":".join(row["labels"])
                tx.run(f"CREATE (n:{labels}) SET n=$props", props=props).consume()
            for row in tx.run(
                "MATCH (a:GraphEntity {snapshot:$s})-[r]->"
                "(b:GraphEntity {snapshot:$s}) "
                "RETURN a.id AS a,b.id AS b,type(r) AS type",
                s=old,
            ).data():
                tx.run(
                    f"MATCH (a {{key:$a}}),(b {{key:$b}}) CREATE (a)-[:{row['type']}]->(b)",
                    a=ACTIVE + "/" + row["a"],
                    b=ACTIVE + "/" + row["b"],
                ).consume()
            tx.run(
                "CREATE (:GraphSnapshot {id:$id,ready:true}) "
                "CREATE (:GraphProjection {id:$id,snapshot:$id,synced_at:$now})",
                id=ACTIVE,
                now=datetime.now(UTC).isoformat(),
            ).consume()

        s.execute_write(migrate)
    print("Neo4j operational graph ready; historical snapshots preserved.")


if __name__ == "__main__":
    main()
