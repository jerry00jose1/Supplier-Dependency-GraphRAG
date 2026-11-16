#!/usr/bin/env python3
"""Create the approval tables in the app database."""

from procurement_agent.approvals.service import init_database
from procurement_agent.config import get_settings


def main() -> None:
    settings = get_settings()
    init_database(settings.graph_app_database_url)
    print("Project conversation and approval tables are ready.")


if __name__ == "__main__":
    main()
