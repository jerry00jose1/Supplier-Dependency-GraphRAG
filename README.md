# Supplier Dependency GraphRAG

A Slack-based procurement and supply-chain GraphRAG agent backed by **Neo4j as the single source of truth**.

## Architecture & Capabilities

`make graph-slack` starts the unified Slack listener (`scripts/slack_listen.py`), which routes messages across three workflows over the active operational graph (`procurement:active` in Neo4j):

1. **Operational Procurement & Direct Order Creation (`procurement` workflow):**
   - **Supplier Comparison & Recommendations:** Queries Neo4j via targeted, indexed Cypher (`GraphEvidence.seller_facts`) to rank eligible suppliers by historical unit price and receipt on-time delivery rate, honoring buyer exclusions and saving selectable shortlists (`"choose option 2"`).
   - **Three-Way Invoice Matching:** Queries `Invoice`, `InvoiceLine`, and linked `OrderLine` nodes in Neo4j (`GraphEvidence.invoice_mismatches`) to detect billed price and received quantity discrepancies.
   - **Written Policy Retrieval:** Uses local `FastEmbedder` (`BAAI/bge-small-en-v1.5`) over `(:Document {id: "policy:northwind"})-[:CONTAINS]->(:Passage)` nodes in Neo4j (`GraphPolicy`) with verified section citations.
   - **Direct Neo4j Order Creation:** Complete purchase requests create `(:PurchaseOrder {name: "GRAPH-PO-...", status: "Open"})` and `(:OrderLine)` nodes atomically in Neo4j via `GraphVault` (`src/procurement_agent/knowledge_graph/operations.py`) with no drafts, no approval buttons, and no ERPNext dependency.

2. **Direct Order History Inspection (`orders` workflow):**
   - Queries `PurchaseOrder` and `OrderLine` nodes in `procurement:active` by supplier, order ID, or valid `OrderStatus` (`Open`, `Draft`, `Completed`, `To Bill`, `To Receive`, `To Receive and Bill`, `Cancelled`) with 20-row pagination.

3. **Supply-Chain Disruption & Dependency GraphRAG (`graph` workflow):**
   - Traverses multi-hop upstream facility claims (`Document -> Passage -> Claim -> Facility / Supplier`) attached to `procurement:active` via three parameterized Cypher investigations (`exposure.cypher`, `alternatives.cypher`, `dependencies.cypher`).
   - Automatically evaluates both historical orders and newly Slack-created `GRAPH-PO-...` open orders against active facility shutdown notices, requiring 100% citation coverage (`[E1..En]`) in the synthesized answer.

## Local Setup & Commands

Prerequisites: Python 3.11+, `uv`, and Docker (for local Neo4j).

```sh
make install       # Install locked dependencies into .venv
make test          # Run offline unit and write-boundary tests
make typecheck     # Run strict mypy type checking
make lint          # Run ruff lint checks
make graph-up      # Start local Neo4j container
make graph-verify  # Load and verify the dependency GraphRAG snapshot
make graph-slack   # Start the unified Slack Socket Mode listener
```

To run the full test suite including live Neo4j integration tests:

```sh
RUN_NEO4J_TESTS=1 uv run pytest -q
```

## Data & Provenance

- **Operational Procurement Records:** Migrated into `snapshot = "procurement:active"` from the Mizan dataset (attributed to findi360 under CC BY 4.0; see [`data/mizan/README.md`](data/mizan/README.md) and [`data/mizan/PROVENANCE.txt`](data/mizan/PROVENANCE.txt)).
- **Synthetic Dependency Fixtures:** Facility nodes (`FAC-X`, `FAC-Y`), supplier facility claims (`profile-a.md`, `profile-b.md`, `profile-c.md`), and the shutdown notice (`shutdown.md`) in [`data/graph_demo/`](data/graph_demo/README.md) carry `origin: "synthetic"` so structured transactional records and authored dependency claims remain auditable and distinct.
