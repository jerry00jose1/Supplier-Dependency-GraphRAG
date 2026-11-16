# Supplier Dependency GraphRAG Architecture

Status: 2026-11-16. Neo4j is the single source of truth for operational procurement records, policy passages, supply-chain dependency claims, conversation context, and direct purchase order creation from Slack. ERPNext and SQLAlchemy/Postgres approval tables have been retired and removed.

## System Ownership & Components

```text
Slack User (/propose or channel/thread message)
   │
   ▼
scripts/slack_listen.py (Socket Mode + per-thread _KeyedLocks + ThreadStore deduplication)
   │
   ▼
src/procurement_agent/slack/router.py :: route_question()
   │  (Single LLM call: routes workflow AND extracts BuyerIntent when routing to ProcurementAssistant)
   ├──────────────────────────────────────────┬──────────────────────────────────────────┐
   ▼                                          ▼                                          ▼
1. "orders" Workflow                   2. "procurement" Workflow                  3. "graph" Workflow
(router.py :: order_answer)            (agent/graph.py + conversation.py)         (knowledge_graph/conversation.py)
   │                                          │                                          │
   ├─ Parameterized Cypher on                 ├─ Targeted Cypher queries per tool        ├─ Entity & alias resolution
   │  `procurement:active`                    │  call via `GraphEvidence`                ├─ 3 parameterized Cypher queries:
   └─ Paginated PO & OrderLine lookup         ├─ Read-only LangGraph ReAct loop          │  exposure, alternatives,
                                              │  (sellers, policy, invoices, POs)        │  dependencies
                                              └─ Validated Purchase Branch               └─ Audited answer with 100%
                                                 (`GraphVault.stage()` writes               [E1..En] citation coverage
                                                  `GRAPH-PO-...` atomically to Neo4j)
```

| Component | Responsibility |
|---|---|
| **Slack (`slack-bolt`)** | Socket Mode ingress for `/propose` commands and thread messages, scoped per thread via `_KeyedLocks`. |
| **Unified Router (`slack/router.py`)** | Single LLM tool call (`QueryOrders`, `InvestigateDependencies`, or `ProcurementAssistant` with embedded `BuyerIntent`) with regex grounding and strict `OrderStatus` validation. |
| **Neo4j (`procurement:active`)** | Single source of truth for `Supplier`, `Item`, `PurchaseOrder`, `OrderLine`, `Receipt`, `ReceiptLine`, `Invoice`, `InvoiceLine`, `Facility`, `Document`, `Passage`, `Claim`, and `:ProcurementContext`. |
| **LangGraph Agent (`agent/graph.py`)** | Bounded research loop (max 6 rounds / 10 tool calls) over targeted Neo4j Cypher tools (`GraphEvidence`) and local `FastEmbedder` policy vectors, plus deterministic purchase validation. |
| **Direct Order Writer (`GraphVault`)** | Stateless Neo4j persistence in `knowledge_graph/operations.py` that writes `GRAPH-PO-<SHA256[:20]>` orders and line items atomically in a single write transaction. |

## Graph Schema in `procurement:active`

```text
(PurchaseOrder)-[:FROM_SUPPLIER]->(Supplier)
(PurchaseOrder)-[:CONTAINS]->(OrderLine)-[:ORDERS]->(Item)
(OrderLine)-[:CHARGED_TO]->(CostCentre)
(Receipt)-[:CONTAINS]->(ReceiptLine)-[:MATCHES_ORDER_LINE]->(OrderLine)
(Invoice)-[:CONTAINS]->(InvoiceLine)-[:MATCHES_ORDER_LINE]->(OrderLine)

(Document)-[:CONTAINS]->(Passage)-[:SUPPORTS]->(Claim)
(Claim {predicate: 'USES_FACILITY'})-[:SUBJECT]->(Supplier)
(Claim {predicate: 'USES_FACILITY'})-[:OBJECT]->(Facility)
(Claim {predicate: 'USES_FACILITY'})-[:FOR_ITEM]->(Item)
(Claim {predicate: 'DISRUPTION'})-[:SUBJECT]->(Facility)
```

Composite indexes on `(i:Item {snapshot, code})`, `(o:PurchaseOrder {snapshot, name})`, `(sup:Supplier {snapshot, name})`, and `(inv:Invoice {snapshot, name})` back all targeted tool queries.

## Trust Boundaries & Safety Guarantees

1. **No LLM-Generated Cypher or Direct Mutation Tools:** Every Neo4j read and write uses parameterized Cypher in Python. The LLM never holds a tool that writes to Neo4j.
2. **Single-Call Routing & Intent Extraction:** `route_question()` extracts both the target workflow and the structured `BuyerIntent` in one LLM call, cutting latency and preventing router-vs-intent drift.
3. **Targeted Graph Queries (No Full-Graph Memory Scans):** `GraphEvidence` (`knowledge_graph/procurement.py`) pushes supplier aggregation, on-time delivery scoring, PO lookup, and 3-way invoice matching directly into Neo4j Cypher per tool call.
4. **Programmatic Citation Verification:** Both the procurement research loop (`ResearchAnswer`) and the dependency GraphRAG engine (`Explanation`) verify that every cited policy ID, invoice, PO, and `[E1..En]` evidence item was retrieved in that turn before sending text to Slack.
5. **Dual-Layer Idempotency:** Duplicate Slack events are filtered by `ThreadStore` (`.cache/unified_deliveries.sqlite3`) and by deterministic `GRAPH-PO-<SHA256[:20]>` keys inside a locked Neo4j write transaction.
