# 2. Agents ask for metrics, never write SQL

Status: accepted

## Context
Letting an LLM write SQL against business data invites three problems: the same
question gets different definitions ("revenue" with or without returns), queries can be
arbitrarily expensive, and free text is an injection surface.

## Decision
A YAML semantic model defines every metric and dimension once. The MCP tools accept only
names from that model plus filter values; a compiler turns them into one SQL query with
bound parameters.

## Consequences
One meaning per metric across every answer. The agent cannot run a query the model does
not allow. New questions that need new metrics are a YAML change, reviewed like code.
The cost: questions outside the model get a clear "unknown metric" instead of a guess.

`semantic/model.yaml` is trusted code: its SQL fragments are interpolated verbatim into
queries. Review it like code and never load a model from an untrusted source. As defense
in depth, the engine's connection has external access disabled (no reading local files).
