# 1. DuckDB as the engine

Status: accepted

## Context
zigmo is a portfolio POC. Anyone should be able to clone it and get answers in minutes,
on a laptop, with no services to run.

## Decision
Store the star schema in a single DuckDB file built from the public dataset (`make data`).
The data is ~1M rows; DuckDB answers every eval question in milliseconds.

## Consequences
No Docker, no credentials, no server. Swapping the engine (ClickHouse, Snowflake,
Databricks SQL) means a new connection and possibly dialect tweaks in the compiler; the
semantic model and the MCP surface stay the same.
