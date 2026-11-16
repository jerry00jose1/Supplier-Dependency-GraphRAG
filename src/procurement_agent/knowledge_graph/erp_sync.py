"""Import ERPNext purchase documents into a separate, ready graph snapshot."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from urllib.parse import quote

from procurement_agent.config import Settings
from procurement_agent.knowledge_graph.models import Edge, Label, Node, Snapshot
from procurement_agent.policy.corpus import load_policy


class ErpReader(Protocol):
    def list_all(self, doctype: str, fields: list[str], **kwargs: Any) -> list[dict[str, Any]]: ...


def build_erp_snapshot(settings: Settings, erp: ErpReader) -> Snapshot:
    """Read all purchase-order statuses and all their lines; fail on incomplete mappings."""
    orders = erp.list_all("Purchase Order", [
        "name", "supplier", "transaction_date", "currency", "status", "docstatus", "company",
    ])
    lines = erp.list_all("Purchase Order Item", [
        "name", "parent", "item_code", "item_name", "qty", "received_qty", "rate", "uom",
        "schedule_date", "description", "warehouse",
    ], parent="Purchase Order")
    scope = f"erpnext:{settings.erpnext_site}"
    items = {r["name"]: r for r in erp.list_all("Item", ["name", "item_name", "stock_uom"])}
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []

    def add(label: Label, code: str, **properties: Any) -> str:
        identifier = f"{scope}:{label}:{code}"
        doctype = {"Supplier": "Supplier", "Item": "Item",
                   "OrderLine": "Purchase Order Item", "Receipt": "Purchase Receipt",
                   "ReceiptLine": "Purchase Receipt Item", "Invoice": "Purchase Invoice",
                   "InvoiceLine": "Purchase Invoice Item"}.get(label, "Purchase Order")
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
                    docstatus=int(row["docstatus"]), company=row["company"], name=row["name"],
                    record=json.dumps(row))
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
                   unit_price=float(row["rate"]), need_by=row["schedule_date"], uom=row["uom"],
                   record=json.dumps(row))
        edges.extend([
            Edge(source=order_ids[row["parent"]], relation="CONTAINS", target=line),
            Edge(source=line, relation="ORDERS", target=item),
        ])
    for header_type, line_type, doctype, link_field, fields in [
        ("Receipt", "ReceiptLine", "Purchase Receipt", "purchase_order_item",
         ["name", "parent", "purchase_order_item", "qty"]),
        ("Invoice", "InvoiceLine", "Purchase Invoice", "po_detail",
         ["name", "parent", "item_code", "qty", "rate", "purchase_order", "po_detail"]),
    ]:
        headers = erp.list_all(doctype, ["name", "posting_date", "docstatus"] +
                              (["supplier", "bill_no"] if header_type == "Invoice" else []))
        header_ids = {r["name"]: add(cast(Label, header_type), r["name"], record=json.dumps(r))
                      for r in headers}
        for row in erp.list_all(doctype + " Item", fields, parent=doctype):
            if row["parent"] not in header_ids:
                raise ValueError("Missing receipt/invoice parent")
            line_id = add(cast(Label, line_type), row["name"], record=json.dumps(row))
            edges.append(Edge(source=header_ids[row["parent"]], relation="CONTAINS",
                              target=line_id))
            target = f"{scope}:OrderLine:{row.get(link_field)}"
            if row.get(link_field):
                if target not in nodes:
                    raise ValueError("Missing linked purchase order line")
                edges.append(Edge(source=line_id, relation="MATCHES_ORDER_LINE", target=target))
    document = Node(id="policy:northwind", label="Document", origin="synthetic",
                    source="src/procurement_agent/policy/northwind.md",
                    properties={"name": "Policy"})
    nodes[document.id] = document
    for chunk in load_policy():
        passage = Node(id=f"policy:{chunk.id}", label="Passage", origin="synthetic",
                       source=document.source, properties={"text": chunk.text,
                       "heading": chunk.heading, "citation_id": chunk.id})
        nodes[passage.id] = passage
        edges.append(Edge(source=document.id, relation="CONTAINS", target=passage.id))
    return Snapshot(snapshot_date=datetime.now(UTC).date(),
                    nodes=list(nodes.values()), edges=edges)


def main() -> None:
    raise RuntimeError("ERP synchronization is retired. Neo4j is authoritative.")


if __name__ == "__main__":
    main()
