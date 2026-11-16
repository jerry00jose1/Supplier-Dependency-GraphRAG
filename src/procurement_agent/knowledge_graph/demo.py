"""Build a small Mizan projection plus explicitly synthetic dependency evidence."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

from procurement_agent.knowledge_graph.models import Edge, Label, Node, Relation, Snapshot

ROOT = Path(__file__).resolve().parents[3]


def build_demo(root: Path = ROOT) -> Snapshot:
    folder = root / "data/graph_demo"
    scenario = json.loads((folder / "scenario.json").read_text())
    nodes: dict[str, Node] = {}
    edges: set[tuple[str, Relation, str]] = set()

    def node(label: Label, code: str, origin: Literal["mizan", "synthetic"],
             source: str, **props: Any) -> str:
        identifier = f"{origin}:{label}:{code}"
        candidate = Node(id=identifier, label=label, origin=origin, source=source, properties=props)
        if identifier in nodes and nodes[identifier] != candidate:
            raise ValueError(f"Conflicting records for {identifier}")
        nodes[identifier] = candidate
        return identifier

    def edge(source: str, relation: Relation, target: str) -> None:
        edges.add((source, relation, target))

    def rows(filename: str) -> list[dict[str, str]]:
        with (root / "data/mizan" / filename).open(newline="") as handle:
            return list(csv.DictReader(handle))

    supplier_codes = scenario["supplier_codes"]
    suppliers = {}
    for row in rows("DimSupplier.csv"):
        if row["SupplierCode"] in supplier_codes:
            suppliers[row["SupplierCode"]] = node(
                "Supplier", row["SupplierCode"], "mizan",
                f"data/mizan/DimSupplier.csv#SupplierCode={row['SupplierCode']}",
                name=row["SupplierName"],
            )
    item_row = next(r for r in rows("DimItem.csv") if r["ItemCode"] == scenario["item_code"])
    item = node("Item", item_row["ItemCode"], "mizan",
                f"data/mizan/DimItem.csv#ItemCode={item_row['ItemCode']}",
                name=item_row["ItemName"], uom=item_row["UnitOfMeasure"])
    uom = item_row["UnitOfMeasure"]
    # Preserve source line identity and prices. Each projected order may be partial.
    history = [r for r in rows("FactPOLine.csv")
               if r["ItemCode"] == scenario["item_code"] and r["SupplierCode"] in suppliers]
    for row in history:
        order = node("PurchaseOrder", row["PONumber"], "mizan",
                     f"data/mizan/FactPOLine.csv#PONumber={row['PONumber']}",
                     status=row["POStatus"], order_date=row["PODate"], currency="USD",
                     projection="selected item lines only")
        line = node("OrderLine", row["POLineID"], "mizan",
                    f"data/mizan/FactPOLine.csv#POLineID={row['POLineID']}",
                    quantity=float(row["OrderedQty"]), received_quantity=float(row["ReceivedQty"]),
                    unit_price=float(row["POUnitPrice"]), need_by=row["NeedByDate"], uom=uom)
        centre = node("CostCentre", row["CostCentreCode"], "mizan",
                      "data/mizan/FactPOLine.csv", name=row["CostCentreCode"])
        edge(order, "FROM_SUPPLIER", suppliers[row["SupplierCode"]])
        edge(order, "CONTAINS", line)
        edge(line, "ORDERS", item)
        edge(line, "CHARGED_TO", centre)

    fixture_source = "data/graph_demo/scenario.json"
    centre = node("CostCentre", "ASSEMBLY", "synthetic", fixture_source, name="Demo Assembly")
    unknown_code = scenario["synthetic_supplier"]
    suppliers[unknown_code] = node("Supplier", unknown_code, "synthetic", fixture_source,
                                   name="Demo supplier D (facility unknown)")
    unknown_order = node("PurchaseOrder", "DEMO-HISTORY-D", "synthetic", fixture_source,
                         status="Closed", order_date="2026-10-01", currency="EUR")
    unknown_line = node("OrderLine", "DEMO-HISTORY-D-1", "synthetic", fixture_source,
                        quantity=100, received_quantity=100, unit_price=12, uom=uom,
                        need_by="2026-10-15")
    edge(unknown_order, "FROM_SUPPLIER", suppliers[unknown_code])
    edge(unknown_order, "CONTAINS", unknown_line)
    edge(unknown_line, "ORDERS", item)
    edge(unknown_line, "CHARGED_TO", centre)
    order = node("PurchaseOrder", "DEMO-PO-001", "synthetic", fixture_source,
                 status="Open", order_date=scenario["snapshot_date"], currency="EUR")
    edge(order, "FROM_SUPPLIER", suppliers[supplier_codes[0]])
    for row in scenario["open_lines"]:
        line = node("OrderLine", row["id"], "synthetic", fixture_source,
                    quantity=row["quantity"], received_quantity=0, unit_price=12,
                    need_by=row["need_by"], uom=uom)
        edge(order, "CONTAINS", line)
        edge(line, "ORDERS", item)
        edge(line, "CHARGED_TO", centre)
    facilities = {code: node("Facility", code, "synthetic", fixture_source, name=code)
                  for code in ("FAC-X", "FAC-Y")}

    def claim(code: str, filename: str, subject: str, **props: Any) -> str:
        source = f"data/graph_demo/{filename}"
        text = (folder / filename).read_text().strip()
        doc = node("Document", filename, "synthetic", source, version="1")
        passage = node("Passage", filename + "#1", "synthetic", source, text=text)
        assertion = node("Claim", code, "synthetic", source,
                         status="accepted", evidence_kind="authored_fixture",
                         recorded_at=scenario["snapshot_date"], **props)
        edge(doc, "CONTAINS", passage)
        edge(passage, "SUPPORTS", assertion)
        edge(assertion, "SUBJECT", subject)
        return assertion

    for mapping in scenario["facility_claims"]:
        assertion = claim(f"facility-{mapping['supplier']}", mapping["document"],
                          suppliers[mapping["supplier"]], predicate="USES_FACILITY",
                          valid_from=scenario["snapshot_date"])
        edge(assertion, "OBJECT", facilities[mapping["facility"]])
        edge(assertion, "FOR_ITEM", item)
    disruption = scenario["disruption"]
    claim(disruption["id"], disruption["document"], facilities[disruption["facility"]],
          predicate="DISRUPTION", start=disruption["start"], end=disruption["end"])
    return Snapshot(
        snapshot_date=date.fromisoformat(scenario["snapshot_date"]),
        nodes=sorted(nodes.values(), key=lambda n: n.id),
        edges=[Edge(source=a, relation=r, target=b) for a, r, b in sorted(edges)],
    )
