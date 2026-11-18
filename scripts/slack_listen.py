#!/usr/bin/env python3
"""CLI entry point for the unified Slack Socket Mode listener."""

from procurement_agent.slack.listener import (
    _answer,
    _deliver,
    _KeyedLocks,
    _tokens,
    _unified_answer,
    conversation_key,
    main,
)

__all__ = [
    "_KeyedLocks",
    "_answer",
    "_deliver",
    "_tokens",
    "_unified_answer",
    "conversation_key",
    "main",
]

if __name__ == "__main__":
    main()
