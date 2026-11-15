# Project scope and acceptance criteria

## User and decision

User: a manufacturing procurement analyst investigating a supplier disruption.
Decision: which purchases to investigate first and which potential alternatives
to contact, followed by an explicitly requested replacement purchase. The output
is an evidence-backed investigation and, after buyer confirmation, an ERPNext
draft routed through ERPNext's approval workflow. Slack provides the conversation
and status links. See [architecture and mappings](architecture.md).

## First demonstration

Use a small subset of Mizan records plus explicitly synthetic supplier profiles,
facility mappings, and disruption notices. Include a case where two apparent
alternatives share the affected facility and one with unknown dependencies.

Return potentially exposed order lines, relevant departments, historically used
alternatives, dependency paths, source citations, and unresolved facts. Associate
exposure with the notice period and snapshot date rather than silently treating
historical purchases as current open orders.

All Mizan source orders are marked closed. Use explicitly synthetic open orders
for the disruption scenario described in the architecture. Preserve the documented
differences between the CSV data and seeded ERPNext records.

After the buyer confirms quotation and delivery details, demonstrate creating one
draft, approval or rejection in ERPNext, and a status reply in Slack. Neo4j stores
the investigation projection; ERPNext owns purchasing and approval state.

## Modeling requirements

- Use stable supplier and order-line identifiers; resolve aliases explicitly.
- Preserve quantities, dates, and item identity at purchase-line level.
- Preserve source IDs, passages, extraction time, and claim status for
  relationships extracted from documents.
- Distinguish structured facts, extracted claims, and synthetic fixtures.
- Repeated ingestion must not duplicate entities or relationships.
- Missing edges are not evidence that two suppliers are independent.

## Acceptance criteria

1. Documented fixtures can be ingested repeatedly without duplicates.
2. Three saved Cypher queries return expected dependency paths and records.
3. Every reported exposure and candidate alternative has inspectable evidence.
4. An alias resolves correctly; an ambiguous alias is surfaced.
5. Unknown facility or capacity information produces a qualified answer.
6. Ten evaluation cases cover direct facts, multiple relationships, shared
   facilities, conflicting claims, and missing evidence.
7. GraphRAG and vector-only retrieval use the same underlying information;
   correctness, evidence support, omissions, latency, and cost are recorded.
8. A five-minute demo explains the decision, schema, retrieval path, result,
   and one failure case.
9. A read-only ERPNext snapshot uses verified field mappings, source identifiers,
   and a visible snapshot time; it does not silently merge transformed ERP records
   with the raw CSV source.
10. Investigation alone never creates a purchase. Confirmed purchase requests
    create at most one draft across retries, including uncertain API outcomes.
11. Authorized approval and rejection occur in ERPNext and are reported correctly
    in Slack. The draft-creation integration cannot bypass required approval.

## Later extensions

Text-to-Cypher with read-only access; incremental ERP synchronization; temporal
claim updates; approvals directly from Slack; approved-substitution records; and
an independently evaluated ML model.
A late-delivery model needs a time split and features computed only from facts
available at prediction time.

## Inherited code

The `procurement_agent` package retains its import name to preserve working code
and tests. Its LangGraph workflow describes execution steps; the planned Neo4j
graph describes business entities and relationships. The new distribution name
is `supplier-dependency-graphrag`.

Keep reusable purchase validation, draft creation, Slack transport, and their
relevant tests. The inherited Python approval vault is not the target approval
authority; its conversation storage and duplicate-prevention roles must be
preserved or replaced before removing it. Cleanup follows the architecture's
component responsibilities rather than deleting the old package wholesale.
