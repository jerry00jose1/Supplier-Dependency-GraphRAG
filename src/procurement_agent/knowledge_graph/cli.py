"""Run with python -m procurement_agent.knowledge_graph.cli."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from neo4j import Driver

from procurement_agent.knowledge_graph.demo import build_demo
from procurement_agent.knowledge_graph.store import GraphSettings, load_snapshot

QUERY_NAMES = ("exposure", "alternatives", "dependencies")


def investigate(driver: Driver, database: str, snapshot: str) -> dict[str, Any]:
    parameters = {
        "snapshot": snapshot, "notice_id": "synthetic:Claim:shutdown-x",
        "item_id": "mizan:Item:M01-01", "excluded_supplier": "mizan:Supplier:SUP0065",
        "facility_id": "synthetic:Facility:FAC-X", "limit": 100,
    }
    results = {}
    with driver.session(database=database) as session:
        marker = session.run("MATCH (s:GraphSnapshot {id:$snapshot, ready:true}) RETURN s.id",
                             snapshot=snapshot).single()
        if marker is None:
            raise ValueError("Demo snapshot is not loaded. Run the load command first.")
        for name in QUERY_NAMES:
            query = (Path(__file__).parent / "queries" / f"{name}.cypher").read_text()
            results[name] = session.run(query, parameters).data()
    return results


def verify_results(results: dict[str, Any]) -> None:
    exposure = {row["line_id"]: row["within_period"] for row in results["exposure"]}
    expected = {"synthetic:OrderLine:DEMO-PO-001-1": True,
                "synthetic:OrderLine:DEMO-PO-001-2": False}
    if exposure != expected or len(results["exposure"]) != 2:
        raise ValueError(f"Unexpected exposure: {exposure}")
    expected_dependencies = {
        "mizan:Supplier:SUP0066": "other_facility_documented_independence_unconfirmed",
        "mizan:Supplier:SUP0067": "shared_affected_facility",
        "synthetic:Supplier:DEMO-SUP-D": "facility_unknown",
    }
    alternatives = {row["supplier_id"] for row in results["alternatives"]}
    if alternatives != set(expected_dependencies) or len(results["alternatives"]) != 3:
        raise ValueError(f"Unexpected alternatives: {alternatives}")
    dependencies = {row["supplier_id"]: row["assessment"] for row in results["dependencies"]}
    if dependencies != expected_dependencies or len(results["dependencies"]) != 3:
        raise ValueError(f"Unexpected dependency assessment: {dependencies}")
    if not all(row["history"] for row in results["alternatives"]):
        raise ValueError("Historical supplier is missing source evidence")
    if not all(row["dependency_passage"] and row["notice_passage"]
               for row in results["exposure"]):
        raise ValueError("Exposure is missing document evidence")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "load", "query", "verify"))
    args = parser.parse_args()
    snapshot = build_demo()
    summary = {"snapshot": snapshot.fingerprint, "snapshot_date": str(snapshot.snapshot_date),
               "nodes": len(snapshot.nodes), "relationships": len(snapshot.edges)}
    if args.command == "validate":
        print(json.dumps(summary, indent=2))
        return
    settings = GraphSettings()
    with settings.driver() as driver:
        driver.verify_connectivity()
        if args.command in {"load", "verify"}:
            summary = load_snapshot(driver, settings.database, snapshot)
        if args.command == "load":
            output: dict[str, Any] = summary
        else:
            if args.command == "verify":
                second = load_snapshot(driver, settings.database, snapshot)
                if summary != second:
                    raise ValueError("Repeated import changed graph counts")
            results = investigate(driver, settings.database, snapshot.fingerprint)
            if args.command == "verify":
                verify_results(results)
            output = {**summary, "results": results}
            if args.command == "verify":
                output["verification"] = "passed: repeat import and all three investigations"
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
