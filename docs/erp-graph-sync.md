# ERP graph synchronization

Run `make graph-sync` to read all accessible ERPNext purchase orders (including
drafts and cancellations), their lines, and referenced item masters into Neo4j.
This command only reads ERPNext. It publishes a `GraphProjection` pointer after
the complete graph validates and loads successfully. A failure leaves the prior
pointer intact. Previous snapshots remain available; synthetic demo claims are
not merged into ERP identities.

Order-line cost centres are optional until their ERP mapping is verified. Missing
cost-centre data is not replaced with a fabricated department. Receipts and
invoices, their lines and order-line links, and configured policy passages are imported.

Refresh manually before demonstrations initially. Five-minute polling and an
immediate refresh after confirmed ERP writes are the intended next synchronization
policy; neither a scheduler nor webhook consumer is installed yet. API reads
across multiple requests do not provide a transactional ERP-wide snapshot.

Inspect the published snapshot:

```cypher
MATCH (p:GraphProjection {id:'erpnext:frontend'})
RETURN p.snapshot, p.sync_started_at, p.synced_at;
```

Run `make graph-slack` for the unified listener. Existing order questions select
the latest published ERP projection on each request. Supplier comparisons,
policy/invoice research and purchase proposals now load evidence exclusively from
Neo4j. The adapter traverses document-to-line relationships and returns the existing
typed record shapes to the original calculation and conversation logic. Policy
passages come from the same graph snapshot; embedding similarity is calculated in
Python. ERP reads are confined to synchronization, and ERP writes to approval handlers.
Facility/disruption investigations explicitly use the synthetic demo snapshot;
its authored claims are not mapped to actual ERP suppliers.

Examples in the configured Slack channel:

- Show all orders for Emirates Supplies LLC 111.
- Show purchase order PUR-ORD-2026-00912.
- Next page. (Continue an order listing in its thread.)
- Which suppliers can provide S-66100? Compare price and delivery history.
- Prepare an order for 1 unit of S-66100 from Emirates Supplies LLC 111, needed next Friday.
- Make that 2 units. (Continue the purchase thread.)
- What does the purchasing policy require?
- Which invoices do not match their orders or receipts?
- Does Supplier C share Facility X for HDPE? (Synthetic graph demonstration.)

Only one listener should run for the app. Purchase approval, submission and rejection
buttons retain the original allowlist, revision checks, and duplicate prevention.
This restores the original Slack approval behavior; migrating formal approval to
ERPNext-owned workflows remains future work. Showing orders reads recorded orders
and does not establish that they were emailed to a supplier. Order lists show 20
records per page and carry the snapshot refresh timestamp. Use `make graph-sync`
to refresh after ERP changes; automatic synchronization is not installed yet.

The inherited procurement Slack entry point now uses `GRAPH_APP_DATABASE_URL`,
defaulting to `graph_app.sqlite3` in this repository, for its conversations,
proposals and audit records. It no longer uses the original application's
`DATABASE_URL` for that listener. Override with a dedicated PostgreSQL database
when needed. `scripts/init_approval_db.py` uses the same project database.
The legacy `DATABASE_URL` setting is no longer consumed. Unused server, alternate
API-key, embedding-provider and approval-toggle settings have been removed from
the configuration template; approval enforcement remains in the execution handlers.
Both graph Slack compatibility entry points now forward to the unified listener.
