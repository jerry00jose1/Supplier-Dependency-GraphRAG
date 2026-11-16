from typing import Any

import pytest

from procurement_agent.config import Settings
from procurement_agent.knowledge_graph.erp_sync import build_erp_snapshot


class ERP:
    def __init__(self, parent: str = "PO-1") -> None:
        self.parent = parent

    def list_all(self, doctype: str, fields: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        return {
            "Purchase Receipt": [{"name": "R1", "posting_date": "2026-11-20", "docstatus": 1}],
            "Purchase Receipt Item": [{"name": "RL1", "parent": "R1",
                                       "purchase_order_item": "LINE-1", "qty": 2}],
            "Purchase Invoice": [{"name": "I1", "posting_date": "2026-11-20", "docstatus": 1,
                                  "supplier": "Supplier A", "bill_no": "B1"}],
            "Purchase Invoice Item": [{"name": "IL1", "parent": "I1", "po_detail": "LINE-1",
                                       "purchase_order": "PO-1", "qty": 2, "rate": 13,
                                       "item_code": "ITEM-1"}],
            "Purchase Order": [{"name": "PO-1", "supplier": "Supplier A",
                                "transaction_date": "2026-11-16", "currency": "EUR",
                                "status": "Cancelled", "docstatus": 2, "company": "Demo"}],
            "Item": [{"name": "ITEM-1", "item_name": "Pellets", "stock_uom": "Kg"}],
            "Purchase Order Item": [{"name": "LINE-1", "parent": self.parent,
                                     "item_code": "ITEM-1", "qty": 5, "received_qty": 2,
                                     "rate": 12, "uom": "Kg", "schedule_date": "2026-11-20"}],
        }[doctype]


def test_preserves_status_and_real_provenance_without_inventing_cost_centre() -> None:
    graph = build_erp_snapshot(Settings(_env_file=None), ERP())  # type: ignore[arg-type]
    assert len([n for n in graph.nodes if n.origin == "erpnext"]) == 8
    assert sum(e.relation == "MATCHES_ORDER_LINE" for e in graph.edges) == 2
    assert any(n.label == "Passage" for n in graph.nodes)
    order = next(n for n in graph.nodes if n.label == "PurchaseOrder")
    assert order.properties["status"] == "Cancelled"
    assert order.properties["docstatus"] == 2
    assert not any(n.label == "CostCentre" for n in graph.nodes)


def test_rejects_incomplete_parent_mapping() -> None:
    with pytest.raises(ValueError, match="without its parent"):
        build_erp_snapshot(Settings(_env_file=None), ERP("missing"))  # type: ignore[arg-type]
