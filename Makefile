UV := $(HOME)/.local/bin/uv
ifeq ($(wildcard $(UV)),)
UV := uv
endif

.PHONY: help install test lint typecheck

help:
	@echo "Supplier Dependency GraphRAG — project foundation"
	@echo "  make install    Create a separate Python environment from the lockfile"
	@echo "  make test       Run inherited offline tests"
	@echo "  make typecheck  Check inherited Python types"
	@echo "  make lint       Run the same scoped lint check as CI"

install:
	$(UV) sync --locked --all-extras

test:
	$(UV) run --locked pytest -p no:capture

typecheck:
	$(UV) run --locked mypy src

lint:
	$(UV) run --locked ruff check src tests scripts/slack_listen.py scripts/eval_demo.py
