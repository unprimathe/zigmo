.PHONY: setup data test evals evals-llm lint run

setup:      ## install dependencies
	uv sync --all-extras

data:       ## download Online Retail II and build data/zigmo.duckdb
	uv run zigmo-ingest

test:       ## unit + guardrail tests (no dataset needed)
	uv run pytest -q

evals:      ## deterministic evals against the real data
	uv run python evals/run_deterministic.py

evals-llm:  ## Claude answers via MCP (needs Anthropic credentials; costs money)
	uv run --extra llm python evals/run_llm.py

lint:
	uv run ruff check .

run:        ## start the MCP server over stdio
	uv run zigmo-server
