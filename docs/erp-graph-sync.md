# Active Operational Graph & Slack Workflows

Neo4j (`snapshot = "procurement:active"`) is the authoritative operational database for all Slack workflows. ERPNext synchronization (`make graph-sync`) is retired.

## Inspecting the Operational Graph

```cypher
MATCH (p:GraphProjection {id:'procurement:active'})
RETURN p.snapshot, p.synced_at;
```

## Supported Slack Prompts in `make graph-slack`

1. **Order History Lookup (`orders` workflow):**
   - `Show me all of my current orders`
   - `Show me my open orders`
   - `Show all orders for Emirates Supplies LLC 111`
   - `Show purchase order PUR-ORD-2026-00059`
   - `Next page` (inside the same Slack thread)

2. **Supplier Comparison, Policy & Invoice Research (`procurement` research branch):**
   - `Which suppliers can provide S-66100? Compare price and delivery history.`
   - `Compare the top 3 suppliers for S-63002 prioritizing reliability, excluding Atlas Trading LLC 110.`
   - `Check invoice ACC-PINV-2026-00068 from Jebel Services LLC 075 and inspect purchase order PUR-ORD-2026-00059.`
   - `What does our purchasing policy say about historical prices and invoice matching?`

3. **Direct Neo4j Order Creation (`procurement` purchase branch):**
   - `Order 2 units of S-66100 from Jebel Enterprises LLC 120 needed by 2026-11-25`
   - `Choose option 2, 2 units, for next Thursday` (after a supplier comparison in the same thread)
   - `Buy 20 units of M01-01 from the cheapest supplier needed next Friday`

4. **Supply-Chain Disruption & Dependency GraphRAG (`graph` workflow):**
   - `Facility X has a shutdown notice. Which of our open purchase order lines for HDPE pellets are exposed during the shutdown window, who are the alternative suppliers for M01-01, and what are the upstream facility dependency risks for each alternative?`
   - `Why is Marina Supplies LLC 067 risky for this item?` (follow-up in the same thread)
