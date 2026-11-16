# ERP graph synchronization

Run `make graph-sync` to read all accessible ERPNext purchase orders (including
drafts and cancellations), their lines, and referenced item masters into Neo4j.
This command only reads ERPNext. It publishes a `GraphProjection` pointer after
the complete graph validates and loads successfully. A failure leaves the prior
pointer intact. Previous snapshots remain available; synthetic demo claims are
not merged into ERP identities.

Order-line cost centres are optional until their ERP mapping is verified. Missing
cost-centre data is not replaced with a fabricated department. Receipts and
invoices are not yet included in this importer.

Refresh manually before demonstrations initially. Five-minute polling and an
immediate refresh after confirmed ERP writes are the intended next synchronization
policy; neither a scheduler nor webhook consumer is installed yet. API reads
across multiple requests do not provide a transactional ERP-wide snapshot.

Inspect the published snapshot:

```cypher
MATCH (p:GraphProjection {id:'erpnext:frontend'})
RETURN p.snapshot, p.sync_started_at, p.synced_at;
```

The existing graph Slack listener remains pinned to the synthetic demo. Unified
Slack routing and ERP snapshot selection are still required before these imported
orders can be queried conversationally.

The inherited procurement Slack entry point now uses `GRAPH_APP_DATABASE_URL`,
defaulting to `graph_app.sqlite3` in this repository, for its conversations,
proposals and audit records. It no longer uses the original application's
`DATABASE_URL` for that listener. Override with a dedicated PostgreSQL database
when needed. Other inherited entry points have not been migrated.
