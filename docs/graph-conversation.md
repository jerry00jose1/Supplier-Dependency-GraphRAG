# Conversational GraphRAG and Slack

The graph service now interprets questions, resolves entities, runs saved Cypher
queries, and gives the resulting records and passages to the configured LLM.
Every generated statement must cite returned evidence. The application checks
citation IDs and coverage of retrieved exposure/dependency outcomes. Invalid
explanations fall back to the actual retrieved facts. These checks do not prove
that every natural-language claim is entailed by its citation.

## Try it from the terminal

```sh
make graph-up
make graph-load
make graph-ask
```

At the prompt, ask:

> Facility X is shutting down. Which purchases might be affected, and which alternatives should we investigate?

For explicit conversation sessions:

```sh
uv run python -m procurement_agent.knowledge_graph.chat --conversation demo "Investigate the Facility X disruption"
uv run python -m procurement_agent.knowledge_graph.chat --conversation demo "Which alternatives should we investigate?"
uv run python -m procurement_agent.knowledge_graph.chat --conversation demo "Does Supplier C share that facility?"
```

Terminal sessions persist in `.cache/graph_threads.sqlite3`. Choose a new
`--conversation` value to start afresh. `--snapshot HASH` selects another ready
snapshot and disables demo-specific aliases. A changed snapshot clears old
conversation context. The existing `knowledge_graph.assistant` command forwards
to this same service.

## Slack setup and operation

The listener uses the existing `.env` settings: `SLACK_BOT_TOKEN`,
`SLACK_APP_TOKEN`, `SLACK_CHANNEL_ID`, and optionally `SLACK_SIGNING_SECRET`.
The configured LLM requires its existing `OPENAI_API_KEY` and `LLM_MODEL` settings.
Neo4j settings are documented in [the graph demo guide](graph-demo.md).

Enable Socket Mode and subscribe the Slack app to the appropriate message event
for the configured channel (`message.channels` for public channels or
`message.groups` for private channels). The bot must be in the channel, with
the corresponding history permission and `chat:write`; the app token needs
`connections:write`. This implementation uses ordinary messages, not a new
slash command. Slack app configuration was not changed by this implementation.

```sh
make graph-slack-check
make graph-slack
```

The check verifies the bot's authentication and graph catalog without posting.
It does not prove event subscriptions or app-token Socket Mode access are configured.
Run only one listener for this bot/channel. Stop the old procurement listener
before switching to the graph listener; the graph implementation does not start
or stop the old listener automatically.

Send the example question in the configured channel. Replies go into the original
message thread. Each channel/thread/user has isolated durable context, allowing
that user to ask follow-ups without another thread or user's context leaking in.
Bot messages, edits, and other channels are ignored. Replayed message timestamps
are deduplicated. Slack text uses plain-text blocks to avoid generated mentions.

Delivery is deliberately conservative: an interrupted or uncertain send remains
recorded and is not automatically posted again. Resend the question as a new
message after a failure. This local demo serializes requests and is not a
distributed queue or an exactly-once delivery system.

## Resolution and retrieval

The model classifies intent and extracts names verbatim from the current question.
Python resolves exact IDs, display names, and explicit aliases. Unknown or
ambiguous names cause clarification. The demo's [aliases](../data/graph_demo/aliases.json)
include Supplier A/B/C/D, Facility X/Y, HDPE, and the intentionally ambiguous
"backup supplier" (B or C). They are authored demo vocabulary, not discovered facts.

Intent selects an allowlisted query; the LLM cannot supply Cypher. Supplier/item
filters apply before database result limits. Conversational results are limited
to 25 rows per query, with a 26th row used to flag truncation. Historical evidence
is sampled to five source records per supplier while retaining the total count.
The demo entity catalog is limited to 500 entities. Larger corpora need indexed
entity resolution and stricter aggregate evidence budgets.

This is entity-based graph retrieval with document passages attached to claims.
It does not yet provide embedding-based document search or automated extraction
from new profiles. The purchase/approval handoff remains a separate milestone;
this listener has no ERP write handlers.

## Evaluation

```sh
make test
RUN_NEO4J_TESTS=1 uv run pytest tests/integration/test_knowledge_graph.py
make graph-eval
uv run python -m procurement_agent.knowledge_graph.evaluate --live --report .cache/graphrag-live-eval.json
```

The ten [cases](../data/graph_demo/questions.json) cover disruption exposure,
supplier-specific questions, item history, shared and unknown dependencies,
missing notices, unknown suppliers, ambiguous aliases, purchase requests, and a
thread follow-up. Scripted mode tests retrieval using explicit plans and a stub
writer. Live mode also exercises the configured model's natural-language planning
and synthesis. It checks response status, required retrieved assessments, valid
citation references, and coverage; it is not a general semantic accuracy benchmark.

On 2026-11-16, all ten cases passed against live Neo4j with `gpt-4.1-mini`.
Recorded per-question latency ranged from 1.12 to 8.71 seconds. The generated
answers were also inspected. Slack bot authentication and graph readiness checks
passed; no live Slack message was sent and the persistent listener was not started.
Token cost accounting, conflicting-claim cases, and comparison against vector-only
retrieval remain pending.

Slack implementation references: [message replies](https://docs.slack.dev/tools/bolt-python/concepts/message-sending/)
and [Bolt app setup](https://docs.slack.dev/tools/bolt-python/creating-an-app/).
