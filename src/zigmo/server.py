"""zigmo MCP server (stdio).

Tools:
  list_metrics    the catalog: metrics and dimensions with their business meaning
  describe_metric how one metric is defined
  query_metrics   ask for metrics by dimensions, filters and dates; get rows + the SQL
  explain_query   get the optimized SQL and plan without running it (for dashboards)
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .compiler import MetricRequest, RequestError
from .engine import Engine, GuardrailError

mcp = MCPServer(
    "zigmo",
    instructions=(
        "zigmo answers business questions from a governed semantic layer. "
        "Call list_metrics first, then query_metrics with metric and dimension names. "
        "Never invent numbers: if a query is rejected, adjust it and try again."
    ),
)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
_engine: Engine | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = Engine()
    return _engine


def _request(metrics, dimensions, filters, date_from, date_to, order_by, descending, limit,
             with_share) -> MetricRequest:
    return MetricRequest(metrics=metrics, dimensions=dimensions or [], filters=filters or {},
                         date_from=date_from, date_to=date_to, order_by=order_by,
                         descending=descending, limit=limit, with_share=with_share)


def _safe(fn):
    try:
        return fn()
    except (RequestError, GuardrailError) as e:
        return {"error": str(e)}


@mcp.tool(annotations=READ_ONLY)
def list_metrics() -> dict:
    """List every metric and dimension available, with its business meaning."""
    return engine().model.catalog()


@mcp.tool(annotations=READ_ONLY)
def describe_metric(name: str) -> dict:
    """Explain how a metric is defined (formula, filter, format)."""
    m = engine().model.metrics.get(name)
    if m is None:
        return {"error": f"Unknown metric '{name}'. Call list_metrics."}
    return {"name": name, **m.model_dump(exclude_none=True)}


@mcp.tool(annotations=READ_ONLY)
def query_metrics(
    metrics: list[str],
    dimensions: list[str] | None = None,
    filters: dict[str, list[str]] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    order_by: str | None = None,
    descending: bool = True,
    limit: int = 50,
    with_share: bool = False,
) -> dict:
    """Compute metrics, optionally split by dimensions.

    Args:
        metrics: metric names from list_metrics, e.g. ["gross_revenue", "orders"].
        dimensions: up to 3 dimension names, e.g. ["country"] or ["month"].
        filters: dimension -> allowed values, e.g. {"country": ["France", "Germany"]}.
        date_from / date_to: inclusive ISO dates (YYYY-MM-DD) on the invoice date.
        order_by: a requested metric or dimension. Defaults to the first metric.
        descending: sort direction.
        limit: max rows (1-1000).
        with_share: add <metric>_share = share of the total across the returned groups.
    """
    req = _request(metrics, dimensions, filters, date_from, date_to, order_by, descending,
                   limit, with_share)
    return _safe(lambda: engine().query(req))


@mcp.tool(annotations=READ_ONLY)
def explain_query(
    metrics: list[str],
    dimensions: list[str] | None = None,
    filters: dict[str, list[str]] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    order_by: str | None = None,
    descending: bool = True,
    limit: int = 50,
    with_share: bool = False,
) -> dict:
    """Return the optimized SQL and DuckDB plan for a request, without running it.

    Same arguments as query_metrics. Useful to paste the query into a BI dashboard.
    """
    req = _request(metrics, dimensions, filters, date_from, date_to, order_by, descending,
                   limit, with_share)
    return _safe(lambda: engine().explain(req))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
