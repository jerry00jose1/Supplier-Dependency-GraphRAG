# Supplier Dependency GraphRAG architecture

Status: agreed implementation direction, 2026-11-15. The graph, synchronization,
and ERPNext-owned approval handoff are planned, not implemented.

## Customer workflow and ownership

Slack question → dependency investigation → cited findings → buyer confirms
replacement purchase details → ERPNext draft → ERPNext approval → Slack status.

| Component | Responsibility |
|---|---|
| Slack | Questions, threaded answers, confirmed purchase requests, and status links |
| Python application | Ingestion, entity resolution, bounded retrieval, validation, and ERP integration |
| Neo4j | Procurement relationships, document evidence, and source snapshots for investigation |
| Embedding model and LLM | Retrieve relevant passages, extract candidate claims, and explain retrieved evidence |
| ERPNext | Authoritative purchase documents, approval permissions, workflow transitions, and final status |

ERPNext retains its database. Python reads relevant records through its API and
maintains a graph projection. Purchase writes go through ERPNext; graph edits
cannot approve purchases. Initially Slack links to ERPNext for formal approval.
The proposed configuration must be verified against the installed ERPNext instance.

## Initial graph and field mappings

The first loader uses CSV fixtures; the ERPNext reader later produces the same
normalized entities. These are proposed mappings. CSV columns were inspected;
ERPNext fields beyond the checks recorded in [discovery notes](erpnext-discovery.md)
are **UNVERIFIED**, including child-line and cost-centre mappings. Existing code
using a field is useful implementation evidence, not proof of the live schema.

| Entity | CSV source / identifier | Proposed ERPNext source / identifier | Graph properties |
|---|---|---|---|
| Supplier | `DimSupplier.SupplierCode` | `Supplier.name` | Display name and explicitly resolved aliases |
| Item | `DimItem.ItemCode` | `Item.name` | Display name, category, unit of measure |
| PurchaseOrder | `FactPOLine.PONumber` | `Purchase Order.name` | Supplier reference, order date, status, company, currency where supplied |
| OrderLine | `FactPOLine.POLineID` | `Purchase Order Item.name` | Parent order, item, quantity, received quantity, unit price, unit, requested date |
| CostCentre | `FactPOLine.CostCentreCode` | Cost-centre reference on order line, if available | Code and verified display name |
| Facility | Explicit synthetic dependency fixture | No ERPNext source assumed | Stable fixture ID and name |
| Document / Passage | Synthetic supplier profiles and disruption notices | No ERPNext source assumed | Document ID/version, passage ID, text, source location |
| Claim | Extracted or authored assertion with a stable ID | No ERPNext source assumed | Predicate, status, applicable dates, extraction time, evidence IDs |

CSV line properties map from `OrderedQty`, `ReceivedQty`, `POUnitPrice`, and
`NeedByDate`; proposed ERP equivalents are `qty`, `received_qty`, `rate`, and
`schedule_date`. CSV status comes from `POStatus`; ERP status and `docstatus`
must both be considered. A submitted document is not necessarily an open order.
Cost centres are presented as cost centres unless a department mapping is supplied.

```text
(PurchaseOrder)-[:FROM_SUPPLIER]->(Supplier)
(PurchaseOrder)-[:CONTAINS]->(OrderLine)
(OrderLine)-[:ORDERS]->(Item)
(OrderLine)-[:CHARGED_TO]->(CostCentre)

(Document)-[:CONTAINS]->(Passage)
(Passage)-[:SUPPORTS]->(Claim)
(Claim)-[:SUBJECT]->(Supplier or Facility)
(Claim)-[:OBJECT]->(Facility)
```

For a supplier/facility claim, the predicate is `USES_FACILITY`. For a disruption
claim, the subject is the facility and the claim carries the affected period;
an object edge is unnecessary. Claims can optionally identify the affected item.
The initial queries traverse claim nodes so provenance remains visible. Supplier
purchase history is derived through orders and lines; it does not assert present
capacity or qualification. Location and ownership modeling are later additions.

## Identifiers, evidence, and ingestion

- Scope every record ID by source dataset or ERP site and record type. Preserve
  source record IDs and URLs separately from display names.
- Use an explicit crosswalk to connect CSV identifiers to ERPNext identifiers.
  Never merge records merely because their names match. Surface ambiguous aliases.
- Keep `origin` (Mizan, synthetic fixture, ERP snapshot) separate from evidence
  kind (structured record, extracted claim) and claim status (unreviewed, accepted,
  rejected, disputed). Synthetic data can still be a structured record.
- Preserve source version, supporting passage, extraction time, applicable dates,
  and snapshot time. Contradictory claims remain inspectable.
- Use uniqueness constraints and repeatable upserts. Validate references, counts,
  and dates before marking a snapshot ready for retrieval. Reconcile removed
  records on complete imports; a partial import must not appear to be current.
- Start with explicit snapshot imports. Incremental updates and deletion/change
  reconciliation follow after the snapshot loader works.

The [Mizan provenance](../data/mizan/PROVENANCE.txt) records that all source orders
are closed. It also describes changes made during ERP seeding: amounts labeled
USD became EUR figures without conversion, and some quantities and dates changed.
Keep raw CSV and ERP snapshots distinct; neither silent merging nor currency
conversion assumptions are acceptable. Missing facility edges mean unknown
dependencies, not independence.

## Investigation and purchase handoff

1. Resolve the question to a supplier, facility, or relevant passage. Ask for
   clarification when the entity is ambiguous.
2. Use saved, parameterized Cypher queries with bounded paths and result limits
   to retrieve potentially exposed lines and historical alternative suppliers.
3. Return paths, record IDs, passages, snapshot dates, and uncertainty to the LLM.
   Its answer must cite that evidence and distinguish claims from structured facts.
4. A buyer explicitly requests a replacement purchase and confirms supplier, item,
   quantity/unit, quoted price/currency, delivery date, company, and destination.
   Historical prices or supplier history alone cannot fill in confirmed terms.
5. Python validates the request and creates one ERPNext draft, tracking a durable
   request ID and ERP document ID. Retries must reconcile uncertain outcomes before
   creating another document. An investigation question cannot initiate this write.
6. An authorized user approves or rejects through the configured ERPNext workflow.
   The application reads the authoritative result and updates the Slack thread.
   Changed commercial details require renewed review under that workflow.

No LLM decides approval authority. ERP integration credentials and workflow rules
must be configured so the draft-creation path cannot bypass required approval.

## Concrete demo fixture

All identifiers and events below are planned synthetic fixtures, not findings in
Mizan or the live ERP. Use a fixed snapshot date of 2026-11-15.

- Supplier A and Supplier C have supported claims linking them to Facility X.
  A notice states that X is shut from November 16 through November 30.
- Open order `DEMO-PO-001`, line 1, requests 100 units of Item I from A by
  November 20 for Cost Centre Assembly. Line 2 is due December 10 and falls
  outside the notice period; the demo must not automatically mark it unaffected.
- Historical orders show that B, C, and D supplied Item I. B has evidence of using
  Facility Y for this item; C shares X; D's facility is unknown. B's other
  dependencies and all candidates' current capacity remain unconfirmed.
- Expected answer: investigate line 1 first, explain C's shared dependency,
  identify B as a candidate to contact, and disclose D's unknown dependency.
  A supplier-level facility link indicates potential exposure, not proof that
  this specific order is produced at that facility.
- After a mock quotation confirms B can deliver 100 units at EUR 12 each by
  November 20, the buyer requests a draft. ERPNext approval and rejection are
  demonstrated separately; repeating the request must not create another draft.

The first three saved queries cover (1) potential exposure within a notice
period, (2) historical suppliers for the affected item, and (3) shared or unknown
facility dependencies with supporting passages.

## Implementation sequence and inherited code

1. Verify unused dependencies and remove only those without a retained purpose.
2. Implement the fixture schema, Neo4j loader, and three queries above.
3. Add document extraction, alias handling, and the cited Slack answer.
4. Verify ERP mappings and import a read-only procurement snapshot.
5. Adapt purchase preparation to ERPNext-owned approval and status reporting.
6. Evaluate ten investigation cases against vector-only retrieval using the same
   source information, plus purchase authorization and duplicate-request checks.

Retain the ERP reader, draft models/writer, Slack transport, model/embedding helpers,
and tests for retained behavior. The existing approval vault also stores conversation
state and duplicate-prevention information; preserve these responsibilities while
migrating formal approval to ERPNext. Replace old retrieval and approval handlers
incrementally. Remove invoice-exception features and old policy fixtures only after
their callers are decoupled and their exclusion from the new workflow is confirmed.
