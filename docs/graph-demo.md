# Run and inspect the first knowledge graph

This milestone implements validated ingestion and three saved Cypher queries.
LLM answers, Slack graph retrieval, live ERP synchronization, and the new approval
handoff remain subsequent work.

## Start and verify

From the repository root with Docker Desktop running:

```sh
make install
make graph-up
make graph-load
make graph-verify
make graph-query
```

`graph-verify` loads the same snapshot twice, checks node and relationship counts,
and checks all three query results against the demo's expected answers. It returns
JSON including evidence passages and source record references. Offline fixture
validation is also available:

```sh
uv run python -m procurement_agent.knowledge_graph.cli validate
```

Neo4j Community 2026.10.2 runs in its own Docker Compose project and named volume,
bound to localhost. Open [Neo4j Browser](http://localhost:7474/browser/), connect
to `bolt://localhost:7687`, and use the local-demo credentials:

- User: `neo4j`
- Password: `supplier-demo-local`
- Database: `neo4j`

These credentials are for this local demo. For a separately managed Neo4j instance,
the Python client reads `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, and
`NEO4J_DATABASE` from environment variables or `.env`. The bundled Compose service
uses the fixed local credentials above.

`make graph-down` stops the service while preserving the volume. The existing
ERPNext services are independent. Linux users can use `DOCKER=docker`; the Makefile
also detects Docker Desktop's macOS executable directory.

## Expected answers

| Query | Expected result |
|---|---|
| `exposure.cypher` | DEMO-PO-001-1: 100 kg due November 20, potential exposure within the notice period. Line 2: 50 kg due December 10, outside the notice period with impact unconfirmed. |
| `alternatives.cypher` | SUP0066, SUP0067, and DEMO-SUP-D, each with historical order-line evidence; no claim of current capacity or qualification. |
| `dependencies.cypher` | SUP0066: Facility Y documented, independence unconfirmed. SUP0067: shares affected Facility X. DEMO-SUP-D: facility unknown. |

The query files live in `src/procurement_agent/knowledge_graph/queries/`. They use
fixed relationship patterns, parameters, and a result limit of 100. This is a
small demo; bounding aggregate evidence sizes for larger datasets is later work.

## Inspect paths in Neo4j Browser

Copy the snapshot hash printed by `graph-load` into the parameter below. Explicit
snapshot selection prevents queries from mixing different versions of the data.

```cypher
:param snapshot => 'PASTE_HASH_FROM_GRAPH_LOAD'
```

Show facility claims, suppliers, and supporting passages:

```cypher
MATCH (c:Claim {snapshot:$snapshot, predicate:'USES_FACILITY'})
MATCH p=(c)-[:SUBJECT]->(:Supplier)
MATCH q=(c)-[:OBJECT]->(:Facility)
MATCH r=(:Passage)-[:SUPPORTS]->(c)
RETURN p,q,r LIMIT 20;
```

Show the synthetic open order's paths:

```cypher
MATCH (o:PurchaseOrder {snapshot:$snapshot, id:'synthetic:PurchaseOrder:DEMO-PO-001'})
MATCH p=(o)-[:CONTAINS]->(:OrderLine)-[:ORDERS]->(:Item)
MATCH q=(o)-[:FROM_SUPPLIER]->(:Supplier)
RETURN p,q;
```

To run the three saved investigation files in Browser, set their other parameters:

```cypher
:params {notice_id: 'synthetic:Claim:shutdown-x', item_id: 'mizan:Item:M01-01', excluded_supplier: 'mizan:Supplier:SUP0065', facility_id: 'synthetic:Facility:FAC-X', limit: 100}
```

## Snapshot and test behavior

Each node has a source-scoped ID and a key scoped to a hash of the entire validated
snapshot. Imports use uniqueness constraints and one managed transaction. The
snapshot becomes ready only after all nodes/edges and expected counts are present.
Reimporting unchanged content reuses it. Changed content creates a separate immutable
snapshot; old snapshots remain for audit. This milestone has no automatic latest
pointer, incremental synchronization, or retention/deletion job.

```sh
make test
RUN_NEO4J_TESTS=1 uv run pytest tests/integration/test_knowledge_graph.py
```

The integration test verifies repeat imports, expected answers, and isolation of
a second snapshot with a different disruption period. It retains both snapshots.
Offline tests cover provenance, stable identity, and rejection of invalid graphs.

## Verification recorded on 2026-11-15

The demo snapshot contains 194 business/evidence nodes and 365 relationships,
plus its snapshot metadata node. It includes 84 Mizan HDPE order lines and three
synthetic lines (two open, one historical). Reimporting the snapshot preserved
the counts. All three investigation results matched the table above, and a live
integration test confirmed that a changed notice period did not alter the original
snapshot's answers. The 68 offline tests, live integration test, type checks, and
lint checks passed. Neo4j Browser visual inspection could not be completed because
the in-app browser failed to attach; the queries were verified through the driver.

Implementation references: [Neo4j Docker setup](https://neo4j.com/docs/operations-manual/current/docker/introduction/)
and [Python managed transactions](https://neo4j.com/docs/python-manual/current/transactions/).
