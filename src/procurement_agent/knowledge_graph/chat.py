"""Ask the graph from the terminal, using the same service as Slack."""

from __future__ import annotations

import argparse

from procurement_agent.agent.model import openai_chat_model
from procurement_agent.config import get_settings
from procurement_agent.knowledge_graph.conversation import GraphConversation
from procurement_agent.knowledge_graph.demo import ROOT, build_demo
from procurement_agent.knowledge_graph.retrieval import GraphReader, demo_aliases
from procurement_agent.knowledge_graph.store import GraphSettings
from procurement_agent.slack.storage import ThreadStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--conversation", default="terminal")
    parser.add_argument("--snapshot", help="Defaults to the current demo fixture snapshot")
    args = parser.parse_args()
    graph = GraphSettings()
    snapshot = args.snapshot or build_demo().fingerprint
    store = ThreadStore(ROOT / ".cache/graph_threads.sqlite3")
    with graph.driver() as driver:
        reader = GraphReader(
            driver, graph.database, snapshot, demo_aliases(ROOT) if not args.snapshot else None
        )
        service = GraphConversation(reader, openai_chat_model(get_settings()))
        key = f"terminal:{args.conversation}"
        reply = service.answer(args.question, store.context(key))
        # Use a fresh receipt ID so terminal questions always run.
        from uuid import uuid4

        store.reserve(f"terminal:{uuid4()}", key, reply)
        print(reply.text)


if __name__ == "__main__":
    main()
