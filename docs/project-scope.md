# Project scope and acceptance criteria

## User and decision

User: a manufacturing procurement analyst investigating a supplier disruption.
Decision: which purchases to investigate first and which potential alternatives
to contact. The output is an evidence-backed investigation.

## First demonstration

Use a small subset of Mizan records plus explicitly synthetic supplier profiles,
facility mappings, and disruption notices. Include a case where two apparent
alternatives share the affected facility and one with unknown dependencies.

Return potentially exposed order lines, relevant departments, historically used
alternatives, dependency paths, source citations, and unresolved facts. Associate
exposure with the notice period and snapshot date rather than silently treating
historical purchases as current open orders.

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

## Later extensions

Text-to-Cypher with read-only access; read-only ERPNext snapshots; temporal claim
updates; approved-substitution records; and an independently evaluated ML model.
A late-delivery model needs a time split and features computed only from facts
available at prediction time.

## Inherited code

The `procurement_agent` package retains its import name to preserve working code
and tests. Its LangGraph workflow describes execution steps; the planned Neo4j
graph describes business entities and relationships. The new distribution name
is `supplier-dependency-graphrag`.
