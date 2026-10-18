# 4. Guardrails before every query

Status: accepted

## Context
An agent calls tools in a loop and will happily ask for "revenue by customer by product
by day". The database must stay responsive and nothing may be written.

## Decision
In order: allowlist and limits in the compiler (≤8 metrics, ≤3 dimensions, ≤1000 rows,
≤50 filter values) → read-only connection → pre-flight (`EXPLAIN` must pass, and the
worst-case group count, the product of each dimension's cardinality, must fit a budget)
→ timeout that interrupts the query → every call written to a JSONL audit log.

## Consequences
Rejections come back as plain-language errors the agent can act on ("use month instead of
day"). Tests cover each guardrail, including injection attempts and writes.
