"""Domain models and vault protocol for direct Neo4j purchase orders."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


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


class OrderVault(Protocol):
    """Persistence interface for conversation state and direct order creation."""

    def conversation(self, conversation_id: str) -> str | None: ...

    def save_conversation(self, conversation_id: str, state_json: str) -> None: ...

    def get(self, proposal_id: str) -> StoredProposal | None: ...

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

    def get(self, proposal_id: str) -> StoredProposal | None:
        return None

    def stage(self, idempotency_key: str, order: DraftPurchaseOrder) -> StoredProposal:
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
