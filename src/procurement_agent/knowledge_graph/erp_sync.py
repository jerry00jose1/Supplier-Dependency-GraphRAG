"""Import ERPNext purchase documents into a separate, ready graph snapshot."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from procurement_agent.config import Settings, get_settings
from procurement_agent.erp.client import ErpNextReadClient
from procurement_agent.knowledge_graph.models import Edge, Label, Node, Snapshot
from procurement_agent.knowledge_graph.store import GraphSettings, load_snapshot


def build_erp_snapshot(settings: Settings, erp: ErpNextReadClient) -> Snapshot:
    """Read all purchase-order statuses and all their lines; fail on incomplete mappings."""
    orders = erp.list_all("Purchase Order", [
        "name", "supplier", "transaction_date", "currency", "status", "docstatus", "company",
    ])
    lines = erp.list_all("Purchase Order Item", [
        "name", "parent", "item_code", "item_name", "qty", "received_qty", "rate", "uom",
        "schedule_date",
    ], parent="Purchase Order")
    scope = f"erpnext:{settings.erpnext_site}"
    items = {r["name"]: r for r in erp.list_all("Item", ["name", "item_name", "stock_uom"])}
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []

    def add(label: Label, code: str, **properties: Any) -> str:
        identifier = f"{scope}:{label}:{code}"
        doctype = {"Supplier": "Supplier", "Item": "Item",
                   "OrderLine": "Purchase Order Item"}.get(label, "Purchase Order")
        node = Node(id=identifier, label=label, origin="erpnext",
                    source=f"{settings.erpnext_base_url.rstrip('/')}/api/resource/"
                    f"{quote(doctype, safe='')}/"
                    f"{quote(code, safe='')}", properties=properties)
        if identifier in nodes and nodes[identifier] != node:
            raise ValueError(f"Inconsistent ERP record: {identifier}")
        nodes[identifier] = node
        return identifier

    order_ids = {}
    for row in orders:
        supplier = add("Supplier", row["supplier"], name=row["supplier"])
        order = add("PurchaseOrder", row["name"], status=row["status"],
                    order_date=row["transaction_date"], currency=row["currency"],
                    docstatus=int(row["docstatus"]), company=row["company"], name=row["name"])
        order_ids[row["name"]] = order
        edges.append(Edge(source=order, relation="FROM_SUPPLIER", target=supplier))
    for row in lines:
        if row["parent"] not in order_ids:
            raise ValueError("ERP returned an order line without its parent order")
        if not row.get("item_code"):
            raise ValueError("ERP order line has no item code")
        master = items[row["item_code"]]
        item = add("Item", row["item_code"], name=master["item_name"] or row["item_code"],
                   uom=master["stock_uom"])
        line = add("OrderLine", row["name"], quantity=float(row["qty"]),
                   received_quantity=float(row["received_qty"] or 0),
                   unit_price=float(row["rate"]), need_by=row["schedule_date"], uom=row["uom"])
        edges.extend([
            Edge(source=order_ids[row["parent"]], relation="CONTAINS", target=line),
            Edge(source=line, relation="ORDERS", target=item),
        ])
    return Snapshot(snapshot_date=datetime.now(UTC).date(),
                    nodes=list(nodes.values()), edges=edges)


def main() -> None:
    settings = get_settings()
    started = datetime.now(UTC).isoformat()
    with ErpNextReadClient(settings) as erp:
        snapshot = build_erp_snapshot(settings, erp)
    graph = GraphSettings()
    with graph.driver() as driver:
        result = load_snapshot(driver, graph.database, snapshot)
        # Publish only after the complete validated import succeeds.
        with driver.session(database=graph.database) as session:
            session.run(
                "MERGE (p:GraphProjection {id:$id}) SET p.snapshot=$snapshot, "
                "p.sync_started_at=$started, p.synced_at=$finished",
                id=f"erpnext:{settings.erpnext_site}", snapshot=snapshot.fingerprint,
                started=started, finished=datetime.now(UTC).isoformat(),
            ).consume()
    print(json.dumps({**result, "projection": f"erpnext:{settings.erpnext_site}"}))


if __name__ == "__main__":
    main()
