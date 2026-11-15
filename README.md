# Supplier Dependency GraphRAG

A new project for investigating supplier disruptions using a procurement knowledge
graph and evidence-backed LLM answers.

## Customer outcome

When a supplier disruption notice arrives, help a procurement analyst identify
potentially affected purchases, investigate alternative suppliers, and inspect
the records and document passages behind each finding.

After the buyer confirms replacement purchase details, prepare an ERPNext draft,
route it through ERPNext approval, and report the status in Slack. See the
[architecture, graph mappings, and demo scenario](docs/architecture.md).

> A supplier facility has shut down. Which purchases might be exposed, and do
> our alternative suppliers share that dependency?

## Current status

**Project foundation created; the knowledge graph and GraphRAG workflow are not
implemented yet.** The inherited procurement application and tests are reusable
starting components, not a completed supplier dependency product.

This repository starts from [Agentic Procurement Automation](https://github.com/jerry00jose1/Agentic-Procurement-Automation),
commit `3489faed9e926d2850ddc952f1adc972bd861138`, with independent Git history.
It retains the Python dependencies and lockfile, procurement package, tests,
synthetic Mizan data, and reference infrastructure. See
[inherited application documentation](docs/inherited-procurement.md).

## Local setup

Prerequisites: Python 3.11+ and uv. Offline tests do not require Slack, ERPNext,
an LLM API, or Neo4j.

```sh
make install
make test
make typecheck
make lint
```

`make install` creates this project's own `.venv` using the inherited locked
dependency versions. The environment is separate from the original project.

The local `.env`, when copied from the original project, remains ignored by Git.
It can reuse the existing LLM credentials and ERPNext URL. Otherwise copy
`.env.example` to `.env` and configure only the values required for your stage.
Do not start a second Slack listener or reseed ERPNext as part of this setup.

Existing ERPNext containers and embedding model caches can be reused. The inherited
application currently uses an approval store for proposals and conversation state.
Its responsibilities will be adapted as formal approval moves to ERPNext; a
separate approval database is not required just to load and query the graph.
Neo4j, its driver, and GraphRAG dependencies will be added during implementation;
they are not installed by this foundation.

`Makefile.procurement` and the existing Docker files are inherited reference
infrastructure. Their service targets operate on the original procurement stack.

## Implementation sequence

1. Define supplier, facility, item, order-line, department, document, and claim
   identifiers and relationship meanings.
2. Load a small Mizan subset and explicitly synthetic dependency fixtures into
   Neo4j with repeatable Python ingestion and uniqueness constraints.
3. Write three Cypher investigations and verify the returned paths manually.
4. Extract relationships from short supplier profiles and disruption notices,
   resolve aliases, and preserve supporting passages and claim status.
5. Retrieve a relevant supplier or passage, traverse bounded graph paths, and
   generate an answer citing structured records and document evidence.
6. Verify ERP field mappings, import a read-only snapshot, and connect confirmed
   purchase requests to draft creation, ERPNext approval, and Slack status updates.
7. Evaluate ten scenarios against a vector-only baseline using the same source
   information, then record a five-minute customer demo.

See [scope and acceptance criteria](docs/project-scope.md).

## Data and limitations

Mizan provides synthetic historical procurement records. It does not provide
current capacity, approved substitutions, supplier facility dependencies, bills
of materials, or production plans. Added dependency profiles and notices must
be explicitly marked synthetic and distinguishable from the Mizan records.
All source purchase orders are marked closed; the demo's open orders must also be
explicitly synthetic. Raw CSV data and transformed ERP seed records are separate
sources whose differences are documented in the provenance file.

Historical purchases identify candidates for investigation, not confirmed
availability or approved alternatives. Shared ownership alone does not prove
shared operational risk. Potential exposure is not confirmed production impact.

The Mizan dataset is attributed to findi360 under CC BY 4.0. Preserve its
[dataset documentation](data/mizan/README.md) and
[transformation provenance](data/mizan/PROVENANCE.txt).
