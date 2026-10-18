"""Compile a metric request into one parameterized SQL query.

The compiler is the only place SQL is written. Names come from the semantic model
(allowlist) and filter values always travel as bound parameters, never as text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .semantic_layer import SemanticModel

MAX_METRICS = 8
MAX_DIMENSIONS = 3
MAX_FILTER_VALUES = 50
DEFAULT_LIMIT = 50
MAX_LIMIT = 1000


class RequestError(ValueError):
    """The request is not valid against the semantic model. Safe to show to the agent."""


@dataclass
class MetricRequest:
    metrics: list[str]
    dimensions: list[str] = field(default_factory=list)
    filters: dict[str, list[str]] = field(default_factory=dict)
    date_from: str | None = None
    date_to: str | None = None
    order_by: str | None = None
    descending: bool = True
    limit: int = DEFAULT_LIMIT
    with_share: bool = False


@dataclass
class CompiledQuery:
    sql: str
    params: list
    columns: list[str]
    limit: int


def _check_names(kind: str, names: list[str], allowed: dict) -> None:
    unknown = [n for n in names if n not in allowed]
    if unknown:
        raise RequestError(f"Unknown {kind}: {unknown}. Valid {kind}: {sorted(allowed)}")
    if len(set(names)) != len(names):
        raise RequestError(f"Duplicated {kind}: {names}")


def _parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise RequestError(f"{label} must be an ISO date (YYYY-MM-DD), got {value!r}") from None


def _metric_sql(model: SemanticModel, name: str) -> str:
    m = model.metrics[name]
    if m.derived:
        expr = m.derived
        for ref in m.references:
            expr = expr.replace("{" + ref + "}", f"({_metric_sql(model, ref)})")
        return f"CAST({expr} AS DOUBLE)"
    if m.filter:
        return f"{m.sql} FILTER (WHERE {m.filter})"
    return m.sql


def compile_request(model: SemanticModel, req: MetricRequest) -> CompiledQuery:
    if not req.metrics:
        raise RequestError("Ask for at least one metric.")
    if len(req.metrics) > MAX_METRICS:
        raise RequestError(f"At most {MAX_METRICS} metrics per query.")
    if len(req.dimensions) > MAX_DIMENSIONS:
        raise RequestError(f"At most {MAX_DIMENSIONS} dimensions per query.")
    _check_names("metrics", req.metrics, model.metrics)
    _check_names("dimensions", req.dimensions, model.dimensions)
    _check_names("filter dimensions", list(req.filters), model.dimensions)
    if not 1 <= req.limit <= MAX_LIMIT:
        raise RequestError(f"limit must be between 1 and {MAX_LIMIT}.")
    if req.order_by and req.order_by not in req.metrics + req.dimensions:
        raise RequestError("order_by must be one of the requested metrics or dimensions.")
    if req.with_share and not req.dimensions:
        raise RequestError("with_share needs at least one dimension to split the total.")

    params: list = []
    select: list[str] = []
    joins: set[str] = set()
    for d in req.dimensions:
        dim = model.dimensions[d]
        select.append(f"{dim.sql} AS {d}")
        joins.add(dim.join)
    for m in req.metrics:
        select.append(f"{_metric_sql(model, m)} AS {m}")
    columns = list(req.dimensions) + list(req.metrics)
    if req.with_share:
        # Share of total across the returned groups, e.g. market share by country.
        # Ratios (aov, return_rate) have no meaningful share, so they are skipped.
        for m in req.metrics:
            if model.metrics[m].derived:
                continue
            expr = _metric_sql(model, m)
            select.append(f"CAST(({expr}) AS DOUBLE) / NULLIF(SUM({expr}) OVER (), 0) AS {m}_share")
            columns.append(f"{m}_share")

    where: list[str] = []
    for d, values in req.filters.items():
        values = [values] if isinstance(values, str) else list(values)
        if not values or len(values) > MAX_FILTER_VALUES:
            raise RequestError(f"Filter '{d}' needs between 1 and {MAX_FILTER_VALUES} values.")
        dim = model.dimensions[d]
        joins.add(dim.join)
        where.append(f"CAST({dim.sql} AS VARCHAR) IN ({', '.join('?' for _ in values)})")
        params.extend(str(v) for v in values)
    if req.date_from:
        where.append(f"{model.time_column} >= ?")
        params.append(_parse_date(req.date_from, "date_from"))
    if req.date_to:
        where.append(f"{model.time_column} < CAST(? AS DATE) + INTERVAL 1 DAY")
        params.append(_parse_date(req.date_to, "date_to"))
    if req.date_from and req.date_to and params[-2] > params[-1]:
        raise RequestError("date_from must be on or before date_to.")

    f = model.fact
    sql = [f"SELECT {', '.join(select)}", f"FROM {f.table} {f.alias}"]
    for j in sorted(joins):  # deterministic order; only the joins the request needs
        jn = model.joins[j]
        sql.append(f"JOIN {jn.table} {jn.alias} ON {jn.condition}")
    if where:
        sql.append("WHERE " + " AND ".join(where))
    if req.dimensions:
        sql.append("GROUP BY ALL")
    order = req.order_by or req.metrics[0]
    sql.append(f"ORDER BY {order} {'DESC' if req.descending else 'ASC'} NULLS LAST")
    sql.append(f"LIMIT {req.limit}")
    return CompiledQuery(sql="\n".join(sql), params=params, columns=columns, limit=req.limit)
