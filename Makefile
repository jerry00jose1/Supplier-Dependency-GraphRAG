UV := $(HOME)/.local/bin/uv
ifeq ($(wildcard $(UV)),)
UV := uv
endif

.PHONY: help install test lint typecheck

ifneq ($(wildcard /Applications/Docker.app/Contents/Resources/bin/docker),)
export PATH := /Applications/Docker.app/Contents/Resources/bin:$(PATH)
endif
DOCKER ?= docker
GRAPH_COMPOSE := docker/neo4j/docker-compose.yml

help:
	@echo "Supplier Dependency GraphRAG — project foundation"
	@echo "  make install    Create a separate Python environment from the lockfile"
	@echo "  make test       Run inherited offline tests"
	@echo "  make typecheck  Check inherited Python types"
	@echo "  make lint       Run the same scoped lint check as CI"
	@echo "  make graph-up   Start local Neo4j"
	@echo "  make graph-load Load the demo snapshot"
	@echo "  make graph-query Run the three investigations"
	@echo "  make graph-verify Verify repeat imports and expected answers"
	@echo "  make graph-down Stop Neo4j (preserves data)"
	@echo "  make graph-slack Start the GraphRAG Slack listener"
	@echo "  make graph-ask   Ask a question interactively using the configured LLM"
	@echo "  make graph-eval  Run ten retrieval scenarios against Neo4j"

install:
	$(UV) sync --locked --all-extras

test:
	$(UV) run --locked pytest -p no:capture

typecheck:
	$(UV) run --locked mypy src

lint:
	$(UV) run --locked ruff check src tests scripts/slack_listen.py scripts/eval_demo.py

.PHONY: graph-up graph-down graph-load graph-query graph-verify
graph-up:
	$(DOCKER) compose -f $(GRAPH_COMPOSE) up -d --wait --wait-timeout 180

graph-down:
	$(DOCKER) compose -f $(GRAPH_COMPOSE) down

graph-load:
	$(UV) run --locked python -m procurement_agent.knowledge_graph.cli load

graph-query:
	$(UV) run --locked python -m procurement_agent.knowledge_graph.cli query

graph-verify:
	$(UV) run --locked python -m procurement_agent.knowledge_graph.cli verify

.PHONY: graph-slack
graph-slack:
	$(UV) run --locked python scripts/slack_listen.py

.PHONY: graph-ask graph-eval graph-slack-check
graph-ask:
	@read -r -p "Question: " question; $(UV) run --locked python -m procurement_agent.knowledge_graph.chat "$$question"

graph-eval:
	$(UV) run --locked python -m procurement_agent.knowledge_graph.evaluate

graph-slack-check:
	$(UV) run --locked python -m procurement_agent.knowledge_graph.slack --check

.PHONY: graph-sync
graph-sync:
	$(UV) run --locked python -m procurement_agent.knowledge_graph.erp_sync
