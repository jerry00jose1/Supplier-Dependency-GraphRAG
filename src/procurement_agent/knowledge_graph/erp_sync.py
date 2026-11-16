"""Build a normalized graph snapshot from procurement document records."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote

from procurement_agent.config import Settings
from procurement_agent.knowledge_graph.models import Edge, Label, Node, Snapshot
from procurement_agent.policy.corpus import load_policy

_NEED_BY = re.compile(r"need_by:(\d{4}-\d{2}-\d{2})")


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

    def add(label: Label, entity_code: str, **properties: Any) -> str:
        identifier = f"{scope}:{label}:{entity_code}"
        doctype = {"Supplier": "Supplier", "Item": "Item",
                   "OrderLine": "Purchase Order Item", "Receipt": "Purchase Receipt",
                   "ReceiptLine": "Purchase Receipt Item", "Invoice": "Purchase Invoice",
                   "InvoiceLine": "Purchase Invoice Item"}.get(label, "Purchase Order")
        node = Node(id=identifier, label=label, origin="erpnext",
                    source=f"{settings.erpnext_base_url.rstrip('/')}/api/resource/"
                    f"{quote(doctype, safe='')}/"
                    f"{quote(entity_code, safe='')}", properties=properties)
        if identifier in nodes and nodes[identifier] != node:
            raise ValueError(f"Inconsistent ERP record: {identifier}")
        nodes[identifier] = node
        return identifier

    order_ids = {}
    for row in orders:
        supplier = add("Supplier", row["supplier"], name=row["supplier"])
        order = add(
            "PurchaseOrder",
            row["name"],
            status=row["status"],
            order_date=row["transaction_date"],
            currency=row["currency"],
            docstatus=int(row["docstatus"]),
            company=row["company"],
            name=row["name"],
            supplier=row["supplier"],
        )
        order_ids[row["name"]] = order
        edges.append(Edge(source=order, relation="FROM_SUPPLIER", target=supplier))
    for row in lines:
        if row["parent"] not in order_ids:
            raise ValueError("ERP returned an order line without its parent order")
        if not row.get("item_code"):
            raise ValueError("ERP order line has no item code")
        master = items[row["item_code"]]
        item = add(
            "Item",
            row["item_code"],
            code=row["item_code"],
            name=master["item_name"] or row["item_code"],
            uom=master["stock_uom"],
        )
        desc = str(row.get("description") or "")
        match = _NEED_BY.search(desc)
        eff_need_by = match.group(1) if match else str(row.get("schedule_date") or "")
        line = add(
            "OrderLine",
            row["name"],
            name=row["name"],
            parent=row["parent"],
            item_code=row["item_code"],
            item_name=row.get("item_name") or master["item_name"] or row["item_code"],
            quantity=float(row["qty"]),
            received_quantity=float(row["received_qty"] or 0),
            unit_price=float(row["rate"]),
            need_by=row["schedule_date"],
            effective_need_by=eff_need_by,
            uom=row["uom"],
            warehouse=str(row.get("warehouse") or ""),
            description=desc,
        )
        edges.extend([
            Edge(source=order_ids[row["parent"]], relation="CONTAINS", target=line),
            Edge(source=line, relation="ORDERS", target=item),
        ])
    receipt_headers = {
        r["name"]: add(
            "Receipt",
            r["name"],
            name=r["name"],
            posting_date=str(r.get("posting_date") or ""),
            docstatus=int(r.get("docstatus") or 0),
        )
        for r in erp.list_all("Purchase Receipt", ["name", "posting_date", "docstatus"])
    }
    for row in erp.list_all(
        "Purchase Receipt Item",
        ["name", "parent", "purchase_order_item", "qty"],
        parent="Purchase Receipt",
    ):
        if row["parent"] not in receipt_headers:
            raise ValueError("Missing receipt/invoice parent")
        line_id = add(
            "ReceiptLine",
            row["name"],
            name=row["name"],
            parent=row["parent"],
            purchase_order_item=str(row.get("purchase_order_item") or ""),
            quantity=float(row.get("qty") or 0),
        )
        edges.append(
            Edge(source=receipt_headers[row["parent"]], relation="CONTAINS", target=line_id)
        )
        target = f"{scope}:OrderLine:{row.get('purchase_order_item')}"
        if row.get("purchase_order_item"):
            if target not in nodes:
                raise ValueError("Missing linked purchase order line")
            edges.append(Edge(source=line_id, relation="MATCHES_ORDER_LINE", target=target))

    invoice_headers = {
        r["name"]: add(
            "Invoice",
            r["name"],
            name=r["name"],
            posting_date=str(r.get("posting_date") or ""),
            docstatus=int(r.get("docstatus") or 0),
            supplier=str(r.get("supplier") or ""),
            bill_no=str(r.get("bill_no") or ""),
        )
        for r in erp.list_all(
            "Purchase Invoice", ["name", "posting_date", "docstatus", "supplier", "bill_no"]
        )
    }
    for row in erp.list_all(
        "Purchase Invoice Item",
        ["name", "parent", "item_code", "qty", "rate", "purchase_order", "po_detail"],
        parent="Purchase Invoice",
    ):
        if row["parent"] not in invoice_headers:
            raise ValueError("Missing receipt/invoice parent")
        line_id = add(
            "InvoiceLine",
            row["name"],
            name=row["name"],
            parent=row["parent"],
            item_code=str(row.get("item_code") or ""),
            quantity=float(row.get("qty") or 0),
            unit_price=float(row.get("rate") or 0),
            purchase_order=str(row.get("purchase_order") or ""),
            po_detail=str(row.get("po_detail") or ""),
        )
        edges.append(
            Edge(source=invoice_headers[row["parent"]], relation="CONTAINS", target=line_id)
        )
        target = f"{scope}:OrderLine:{row.get('po_detail')}"
        if row.get("po_detail"):
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
