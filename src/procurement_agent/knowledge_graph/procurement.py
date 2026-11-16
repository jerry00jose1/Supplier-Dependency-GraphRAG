"""Targeted Neo4j Cypher queries and evidence interface for procurement reasoning."""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from procurement_agent.agent.item_request import SellerFact, seller_facts
from procurement_agent.domain.exceptions import InvoiceMismatch, invoice_mismatches
from procurement_agent.knowledge_graph.store import GraphSettings
from procurement_agent.policy.corpus import PolicyChunk
from procurement_agent.policy.retrieve import Embedder, retrieve, shared_embedder


class GraphPolicy:
    def __init__(self, chunks: list[PolicyChunk]) -> None:
        self.chunks = chunks

    def embed(self, texts: list[str]) -> list[list[float]]:
        return shared_embedder().embed(texts)

    def retrieve(self, query: str, limit: int) -> list[PolicyChunk]:
        return retrieve(query, shared_embedder(), chunks=self.chunks, limit=limit)


class ProcurementEvidence(Protocol):
    embedder: Embedder | None
    refreshed: str

    def item_codes(self) -> list[str]: ...

    def seller_facts(self, item_code: str) -> list[SellerFact]: ...

    def purchase_order(self, name: str) -> dict[str, Any] | None: ...

    def has_invoices(self) -> bool: ...

    def invoice_mismatches(
        self, supplier: str = "", invoice_name: str = ""
    ) -> list[InvoiceMismatch]: ...


class InMemoryEvidence:
    """In-memory evidence adapter used when explicit record lists are passed in tests."""

    def __init__(
        self,
        orders: list[dict[str, Any]],
        order_items: list[dict[str, Any]],
        receipts: list[dict[str, Any]],
        receipt_items: list[dict[str, Any]],
        invoices: list[dict[str, Any]] | None = None,
        invoice_items: list[dict[str, Any]] | None = None,
        embedder: Embedder | None = None,
        refreshed: str = "",
    ) -> None:
        self.orders = orders
        self.order_items = order_items
        self.receipts = receipts
        self.receipt_items = receipt_items
        self.invoices = invoices
        self.invoice_items = invoice_items
        self.embedder = embedder
        self.refreshed = refreshed
        self._order_by_name = {str(row["name"]): row for row in orders if "name" in row}

    def item_codes(self) -> list[str]:
        return sorted({str(row["item_code"]) for row in self.order_items if row.get("item_code")})

    def seller_facts(self, item_code: str) -> list[SellerFact]:
        return seller_facts(
            item_code, self.orders, self.order_items, self.receipts, self.receipt_items
        )

    def purchase_order(self, name: str) -> dict[str, Any] | None:
        header = self._order_by_name.get(name)
        if header is None:
            return None
        lines = [row for row in self.order_items if row.get("parent") == name]
        return {"order": header, "items": lines[:20], "total_lines": len(lines)}

    def has_invoices(self) -> bool:
        return self.invoices is not None and self.invoice_items is not None

    def invoice_mismatches(
        self, supplier: str = "", invoice_name: str = ""
    ) -> list[InvoiceMismatch]:
        if self.invoices is None or self.invoice_items is None:
            return []
        rows = invoice_mismatches(self.invoice_items, self.invoices, self.order_items)
        return [
            row
            for row in rows
            if (not supplier or row.supplier.casefold() == supplier.casefold())
            and (not invoice_name or row.invoice == invoice_name)
        ]


class GraphEvidence:
    """Query Neo4j directly per tool call without loading the graph into Python RAM."""

    def __init__(
        self,
        driver: Any,
        database: str,
        snapshot: str,
        embedder: Embedder | None,
        refreshed: str,
    ) -> None:
        self.driver = driver
        self.database = database
        self.snapshot = snapshot
        self.embedder = embedder
        self.refreshed = refreshed

    def item_codes(self) -> list[str]:
        with self.driver.session(database=self.database) as session:
            rows = session.run(
                "MATCH (o:PurchaseOrder {snapshot:$s, docstatus:1})"
                "-[:CONTAINS]->(:OrderLine)-[:ORDERS]->(i:Item) "
                "RETURN DISTINCT coalesce(i.code, split(i.id, ':')[-1]) AS code "
                "ORDER BY code",
                s=self.snapshot,
            ).data()
        return [str(r["code"]) for r in rows if r.get("code")]

    def seller_facts(self, item_code: str) -> list[SellerFact]:
        with self.driver.session(database=self.database) as session:
            rows = session.run(
                "MATCH (i:Item {snapshot:$s})<-[:ORDERS]-(l:OrderLine)<-[:CONTAINS]-"
                "(o:PurchaseOrder {snapshot:$s, docstatus:1})-[:FROM_SUPPLIER]->(sup:Supplier) "
                "WHERE coalesce(i.code, split(i.id, ':')[-1]) = $code "
                "AND l.quantity > 0 AND l.unit_price > 0 "
                "AND coalesce(l.warehouse, '') <> '' "
                "AND coalesce(o.company, '') <> '' "
                "AND coalesce(o.order_date, '') <> '' "
                "WITH sup, o, l ORDER BY o.order_date DESC, o.name DESC "
                "WITH sup, collect({order: o.name, date: o.order_date, company: o.company, "
                "currency: o.currency, warehouse: l.warehouse, qty: l.quantity, "
                "rate: l.unit_price, uom: l.uom})[0] AS latest "
                "OPTIONAL MATCH (sup)<-[:FROM_SUPPLIER]-"
                "(:PurchaseOrder {snapshot:$s, docstatus:1})"
                "-[:CONTAINS]->(ol:OrderLine)<-[:MATCHES_ORDER_LINE]-(:ReceiptLine)<-[:CONTAINS]-"
                "(rec:Receipt {snapshot:$s, docstatus:1}) "
                "WHERE coalesce(ol.effective_need_by, ol.need_by, '') <> '' "
                "AND coalesce(rec.posting_date, '') <> '' "
                "WITH sup, latest, ol, max(rec.posting_date) AS received_date "
                "WITH sup.name AS supplier, latest, "
                "count(ol) AS scored_lines, "
                "sum(CASE WHEN received_date <= coalesce(ol.effective_need_by, ol.need_by) "
                "THEN 1 ELSE 0 END) AS on_time_lines "
                "RETURN supplier, latest.company AS company, latest.warehouse AS warehouse, "
                "latest.qty AS qty, latest.rate AS rate, latest.date AS when, "
                "CASE WHEN scored_lines > 0 THEN toFloat(on_time_lines) / scored_lines "
                "ELSE null END AS on_time_rate, "
                "scored_lines, coalesce(latest.currency, 'EUR') AS currency, latest.uom AS uom "
                "ORDER BY supplier",
                s=self.snapshot,
                code=item_code,
            ).data()
        return [
            SellerFact(
                supplier=str(r["supplier"]),
                company=str(r["company"]),
                warehouse=str(r["warehouse"]),
                qty=float(r["qty"]),
                rate=float(r["rate"]),
                when=date.fromisoformat(str(r["when"])[:10]),
                on_time_rate=None if r["on_time_rate"] is None else float(r["on_time_rate"]),
                scored_lines=int(r["scored_lines"]),
                currency=str(r["currency"]),
                uom=str(r["uom"]) if r.get("uom") else None,
            )
            for r in rows
        ]

    def purchase_order(self, name: str) -> dict[str, Any] | None:
        with self.driver.session(database=self.database) as session:
            row = session.run(
                "MATCH (o:PurchaseOrder {snapshot:$s, docstatus:1, name:$name})"
                "-[:FROM_SUPPLIER]->(sup:Supplier) "
                "OPTIONAL MATCH (o)-[:CONTAINS]->(l:OrderLine)-[:ORDERS]->(i:Item) "
                "WITH o, sup, l, i ORDER BY l.name "
                "RETURN o.name AS name, sup.name AS supplier, o.order_date AS transaction_date, "
                "o.currency AS currency, o.status AS status, o.docstatus AS docstatus, "
                "o.company AS company, "
                "collect(CASE WHEN l IS NULL THEN null ELSE {"
                "name: coalesce(l.name, split(l.id, ':')[-1]), "
                "parent: o.name, "
                "item_code: coalesce(l.item_code, i.code, split(i.id, ':')[-1]), "
                "item_name: coalesce(l.item_name, i.name), "
                "qty: l.quantity, "
                "received_qty: l.received_quantity, "
                "rate: l.unit_price, "
                "uom: l.uom, "
                "schedule_date: l.need_by, "
                "description: coalesce(l.description, ''), "
                "warehouse: coalesce(l.warehouse, '')"
                "} END) AS lines",
                s=self.snapshot,
                name=name,
            ).single()
        if row is None:
            return None
        header = {
            "name": row["name"],
            "supplier": row["supplier"],
            "transaction_date": row["transaction_date"],
            "currency": row["currency"],
            "status": row["status"],
            "docstatus": row["docstatus"],
            "company": row["company"],
        }
        lines = [line for line in row["lines"] if line is not None]
        return {"order": header, "items": lines[:20], "total_lines": len(lines)}

    def has_invoices(self) -> bool:
        return True

    def invoice_mismatches(
        self, supplier: str = "", invoice_name: str = ""
    ) -> list[InvoiceMismatch]:
        with self.driver.session(database=self.database) as session:
            rows = session.run(
                "MATCH (inv:Invoice {snapshot:$s, docstatus:1})-[:CONTAINS]->(il:InvoiceLine) "
                "WHERE ($supplier = '' OR toLower(inv.supplier) = toLower($supplier)) "
                "AND ($invoice = '' OR inv.name = $invoice) "
                "OPTIONAL MATCH (il)-[:MATCHES_ORDER_LINE]->(ol:OrderLine)"
                "<-[:CONTAINS]-(:PurchaseOrder {snapshot:$s, docstatus:1}) "
                "WITH inv, il, ol "
                "WHERE ol IS NULL "
                "OR abs(il.quantity - ol.received_quantity) > 0.001 "
                "OR abs(il.unit_price - ol.unit_price) > 0.01 "
                "RETURN inv.name AS invoice, coalesce(inv.supplier, '') AS supplier, "
                "coalesce(il.item_code, '') AS item_code, "
                "coalesce(il.purchase_order, '') AS purchase_order, "
                "il.quantity AS invoice_qty, "
                "coalesce(ol.received_quantity, 0.0) AS received_qty, "
                "il.unit_price AS invoice_rate, "
                "coalesce(ol.unit_price, 0.0) AS order_rate, "
                "ol IS NULL AS missing_po "
                "ORDER BY invoice, item_code",
                s=self.snapshot,
                supplier=supplier,
                invoice=invoice_name,
            ).data()
        mismatches: list[InvoiceMismatch] = []
        for r in rows:
            reasons: list[str] = []
            invoice_qty = float(r["invoice_qty"] or 0)
            received_qty = float(r["received_qty"] or 0)
            invoice_rate = float(r["invoice_rate"] or 0)
            order_rate = float(r["order_rate"] or 0)
            if r["missing_po"]:
                reasons.append("no_purchase_order")
            else:
                if abs(invoice_qty - received_qty) > 0.001:
                    reasons.append("quantity")
                if abs(invoice_rate - order_rate) > 0.01:
                    reasons.append("price")
            mismatches.append(
                InvoiceMismatch(
                    invoice=str(r["invoice"]),
                    supplier=str(r["supplier"]),
                    item_code=str(r["item_code"]),
                    purchase_order=str(r["purchase_order"]),
                    invoice_qty=invoice_qty,
                    received_qty=received_qty,
                    invoice_rate=invoice_rate,
                    order_rate=order_rate,
                    reasons=tuple(reasons),
                )
            )
        return mismatches


def load_graph_evidence(
    driver: Any | None = None, database: str | None = None
) -> GraphEvidence:
    """Validate the active snapshot and return a lazy Neo4j-backed evidence source."""
    graph = GraphSettings()
    db = database or graph.database
    own_driver = driver is None
    active_driver = driver if driver is not None else graph.driver()
    try:
        with active_driver.session(database=db) as session:
            marker = session.run(
                "MATCH (p:GraphProjection {id:$id}) "
                "MATCH (s:GraphSnapshot {id:p.snapshot,ready:true}) "
                "RETURN p.snapshot AS snapshot,p.synced_at AS refreshed",
                id="procurement:active",
            ).single()
            if marker is None:
                raise ValueError("Import ERP evidence with make graph-sync first")
            snapshot = str(marker["snapshot"])
            sections = session.run(
                "MATCH (:Document {snapshot:$snapshot,id:'policy:northwind'})"
                "-[:CONTAINS]->(p:Passage) "
                "RETURN p.citation_id AS id,p.heading AS heading,p.text AS text",
                snapshot=snapshot,
            ).data()
            if not sections:
                raise ValueError("Snapshot lacks policy evidence; run make graph-sync")
            policy = GraphPolicy([PolicyChunk(**row) for row in sections])
            return GraphEvidence(active_driver, db, snapshot, policy, str(marker["refreshed"]))
    except Exception:
        if own_driver:
            active_driver.close()
        raise
