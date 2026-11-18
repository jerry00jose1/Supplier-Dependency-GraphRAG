"""Domain models, seller aggregation, and vault protocol for direct Neo4j purchase orders."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from procurement_agent.domain.exceptions import _as_date, supplier_performance

_LEAD_TIME_DAYS = 14


class PurchaseOrderLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_code: str
    qty: float = Field(gt=0)
    rate: float = Field(ge=0)


class DraftPurchaseOrder(BaseModel):
    """Validated purchase order parameters recorded in Neo4j."""

    model_config = ConfigDict(extra="forbid")

    supplier: str
    company: str
    warehouse: str
    transaction_date: date
    schedule_date: date
    currency: str = "EUR"
    items: list[PurchaseOrderLine] = Field(min_length=1)

    def line_total(self) -> float:
        line = self.items[0]
        return line.qty * line.rate

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


@dataclass(frozen=True)
class SellerFact:
    supplier: str
    company: str
    warehouse: str
    qty: float
    rate: float
    when: date
    on_time_rate: float | None
    scored_lines: int
    currency: str = "EUR"
    uom: str | None = None


@dataclass(frozen=True)
class StoredProposal:
    id: str
    idempotency_key: str
    status: str
    payload: DraftPurchaseOrder
    payload_hash: str
    approved_by: str | None
    erp_document_name: str | None
    slack_channel: str | None
    slack_ts: str | None


@dataclass(frozen=True)
class FiledPurchase:
    stored: StoredProposal
    posted_anew: bool
    notice: str | None = None


@dataclass(frozen=True)
class _Seller:
    supplier: str
    company: str
    warehouse: str
    qty: float
    rate: float
    when: date
    order_name: str


def seller_facts(
    item_code: str,
    orders: list[dict[str, Any]],
    order_items: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    receipt_items: list[dict[str, Any]],
) -> list[SellerFact]:
    """One row per supplier who has a recorded purchase line for this item."""
    supplier_of = {row["name"]: str(row.get("supplier") or "") for row in orders}
    order_of = {row["name"]: row for row in orders}
    performance = {
        row.supplier: row
        for row in supplier_performance(order_items, orders, receipt_items, receipts)
    }
    facts: list[SellerFact] = []
    for seller in _sellers(item_code, order_items, supplier_of, order_of):
        record = performance.get(seller.supplier)
        facts.append(
            SellerFact(
                supplier=seller.supplier,
                company=seller.company,
                warehouse=seller.warehouse,
                qty=seller.qty,
                rate=seller.rate,
                when=seller.when,
                on_time_rate=None if record is None else record.on_time_rate,
                scored_lines=0 if record is None else record.lines,
                currency=str(order_of[seller.order_name].get("currency") or "EUR"),
                uom=next(
                    (
                        str(item["uom"])
                        for item in order_items
                        if item.get("parent") == seller.order_name
                        and item.get("item_code") == item_code
                        and item.get("uom")
                    ),
                    None,
                ),
            )
        )
    facts.sort(key=lambda fact: fact.supplier)
    return facts


def order_for(fact: SellerFact, item_code: str, qty: float, today: date) -> DraftPurchaseOrder:
    """Build a purchase order from one seller row using its latest recorded rate."""
    return DraftPurchaseOrder(
        supplier=fact.supplier,
        company=fact.company,
        warehouse=fact.warehouse,
        currency=fact.currency,
        transaction_date=today,
        schedule_date=today + timedelta(days=_LEAD_TIME_DAYS),
        items=[PurchaseOrderLine(item_code=item_code, qty=qty, rate=fact.rate)],
    )


def _sellers(
    item_code: str,
    order_items: list[dict[str, Any]],
    supplier_of: dict[str, str],
    order_of: dict[str, dict[str, Any]],
) -> list[_Seller]:
    latest: dict[str, _Seller] = {}
    for item in order_items:
        if item.get("item_code") != item_code:
            continue
        parent = str(item.get("parent") or "")
        supplier = supplier_of.get(parent) or ""
        order = order_of.get(parent) or {}
        company = str(order.get("company") or "")
        warehouse = str(item.get("warehouse") or "")
        when = _as_date(order.get("transaction_date"))
        qty = float(item.get("qty") or 0)
        rate = float(item.get("rate") or 0)
        if not supplier or not company or not warehouse or when is None or qty <= 0 or rate <= 0:
            continue
        seller = _Seller(supplier, company, warehouse, qty, rate, when, parent)
        previous = latest.get(supplier)
        if previous is None or (seller.when, seller.order_name) > (
            previous.when,
            previous.order_name,
        ):
            latest[supplier] = seller
    return list(latest.values())


class OrderVault(Protocol):
    """Stateless persistence interface for conversation context and direct order creation."""

    def conversation(self, conversation_id: str) -> str | None: ...

    def save_conversation(self, conversation_id: str, state_json: str) -> None: ...

    def stage(self, idempotency_key: str, order: DraftPurchaseOrder) -> StoredProposal: ...


class InMemoryVault:
    """In-memory OrderVault with direct GRAPH-PO execution semantics for unit tests."""

    def __init__(self) -> None:
        self.conversations: dict[str, str] = {}
        self.orders: dict[str, StoredProposal] = {}
        self.by_key: dict[str, StoredProposal] = {}

    def conversation(self, conversation_id: str) -> str | None:
        return self.conversations.get(conversation_id)

    def save_conversation(self, conversation_id: str, state_json: str) -> None:
        state = json.loads(state_json)
        if str(state.get("proposal_id", "")).startswith("GRAPH-PO-"):
            state["purchasing"] = False
            state["proposal_id"] = None
            state_json = json.dumps(state)
        self.conversations[conversation_id] = state_json

    def stage(self, idempotency_key: str, order: DraftPurchaseOrder) -> StoredProposal:
        if not idempotency_key.strip():
            raise ValueError("A stable event or request identifier is required")
        existing = self.by_key.get(idempotency_key)
        if existing is not None:
            return existing
        name = "GRAPH-PO-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:20].upper()
        stored = StoredProposal(
            id=name,
            idempotency_key=idempotency_key,
            status="executed",
            payload=order,
            payload_hash=order.fingerprint(),
            approved_by=None,
            erp_document_name=name,
            slack_channel=None,
            slack_ts=None,
        )
        self.orders[name] = stored
        self.by_key[idempotency_key] = stored
        return stored
