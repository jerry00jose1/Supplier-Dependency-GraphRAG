"""Compatibility entry point for the unified Slack listener."""

import runpy
import sys

from procurement_agent.knowledge_graph.demo import ROOT


def main() -> None:
    if "--check" in sys.argv:
        from procurement_agent.slack.check import main as check

        check()
    else:
        runpy.run_path(str(ROOT / "scripts/slack_listen.py"), run_name="__main__")

if __name__ == "__main__":
    main()
