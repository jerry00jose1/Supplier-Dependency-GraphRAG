"""Read-only graph and Slack readiness check."""

import json

from slack_sdk import WebClient

from procurement_agent.config import get_settings
from procurement_agent.knowledge_graph.demo import ROOT, build_demo
from procurement_agent.knowledge_graph.retrieval import GraphReader, demo_aliases
from procurement_agent.knowledge_graph.store import GraphSettings


def main() -> None:
    settings = get_settings()
    if (
        not settings.slack_bot_token
        or not settings.slack_app_token
        or not settings.slack_channel_id
    ):
        raise RuntimeError("Set SLACK_BOT_TOKEN, SLACK_APP_TOKEN, and SLACK_CHANNEL_ID")
    graph = GraphSettings()
    with graph.driver() as driver:
        graph_reader = GraphReader(
            driver, graph.database, build_demo().fingerprint, demo_aliases(ROOT)
        )
        client = WebClient(token=settings.slack_bot_token.get_secret_value(), timeout=20)
        auth = client.auth_test()
        print(
            json.dumps(
                {
                    "slack_authenticated": bool(auth["ok"]),
                    "graph_ready": True,
                    "entities": len(graph_reader.catalog()),
                    "message_posted": False,
                }
            )
        )
