"""Compatibility entry point for the unified Slack listener."""

import sys

from procurement_agent.slack.listener import main as listen


def main() -> None:
    if "--check" in sys.argv:
        from procurement_agent.slack.check import main as check

        check()
    else:
        listen()


if __name__ == "__main__":
    main()
