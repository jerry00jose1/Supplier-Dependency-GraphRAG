"""Ten graph conversation scenarios; --live also exercises natural-language interpretation."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from procurement_agent.agent.model import openai_chat_model
from procurement_agent.config import get_settings
from procurement_agent.knowledge_graph.conversation import Context, GraphConversation, Plan
from procurement_agent.knowledge_graph.demo import ROOT, build_demo
from procurement_agent.knowledge_graph.retrieval import GraphReader, demo_aliases
from procurement_agent.knowledge_graph.store import GraphSettings


class EvidenceEcho:
    """Deterministic synthesis stub for retrieval tests, not an LLM quality measurement."""

    def invoke(self, messages: Any) -> AIMessage:
        payload = json.loads(messages[-1].content)
        return AIMessage(
            content=json.dumps(
                {
                    "statements": [
                        {"text": e["facts"].get("assessment", e["kind"]), "citations": [e["id"]]}
                        for e in payload["evidence"]
                    ]
                }
            )
        )


def cases() -> list[dict[str, Any]]:
    return list(json.loads((ROOT / "data/graph_demo/questions.json").read_text()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use the configured LLM (API charges)")
    parser.add_argument("--report", type=Path, help="Save full answers to a local JSON report")
    args = parser.parse_args()
    graph = GraphSettings()
    snapshot = build_demo().fingerprint
    results = []
    prior = Context()
    with graph.driver() as driver:
        reader = GraphReader(driver, graph.database, snapshot, demo_aliases(ROOT))
        model = openai_chat_model(get_settings()) if args.live else EvidenceEcho()
        service = GraphConversation(reader, model)
        for case in cases():
            started = time.monotonic()
            previous = prior if case.get("followup") else Context()
            if args.live:
                reply = service.answer(case["question"], previous)
            else:
                plan = Plan(
                    intent=case["intent"],
                    followup=case.get("followup", False),
                    **{
                        field: case.get(field)
                        for field in ("supplier", "facility", "item", "notice")
                    },
                )
                reply = service.execute(case["question"], plan, previous)
            if case["name"] == "facility disruption":
                prior = reply.context
            assessments = {e.facts.get("assessment") for e in reply.evidence}
            passed = reply.status == case["status"] and set(case["required_assessments"]).issubset(
                assessments
            )
            results.append(
                {
                    "case": case["name"],
                    "passed": passed,
                    "status": reply.status,
                    "seconds": round(time.monotonic() - started, 2),
                    "evidence_count": len(reply.evidence),
                    "answer": reply.text,
                }
            )
    report = {
        "mode": "live" if args.live else "scripted retrieval",
        "passed": sum(r["passed"] for r in results),
        "total": len(results),
        "results": results,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    summary = {
        **report,
        "results": [{k: v for k, v in row.items() if k != "answer"} for row in results],
    }
    print(json.dumps(summary, indent=2))
    if not all(r["passed"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
