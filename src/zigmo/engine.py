"""Run compiled queries behind guardrails, and audit every call.

Guardrails, in the order they apply:
1. Allowlist and limits: the compiler only accepts names from the semantic model.
2. Read-only connection: the database file is opened with read_only=True.
3. Pre-flight: EXPLAIN must succeed, and the worst-case number of groups
   (product of the requested dimensions' cardinalities) must fit a budget.
4. Timeout: a query that runs too long is interrupted.
5. Row cap: results are truncated to the requested limit.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb

from .compiler import CompiledQuery, MetricRequest, RequestError, compile_request
from .semantic_layer import SemanticModel, load_model

DEFAULT_DB = Path(os.environ.get("ZIGMO_DB", "data/zigmo.duckdb"))
DEFAULT_AUDIT = Path(os.environ.get("ZIGMO_AUDIT", "audit/zigmo.jsonl"))
GROUP_BUDGET = int(os.environ.get("ZIGMO_GROUP_BUDGET", "2000000"))
TIMEOUT_S = float(os.environ.get("ZIGMO_TIMEOUT_S", "10"))


class GuardrailError(RuntimeError):
    """The request is valid but too expensive or too slow. Safe to show to the agent."""


def _jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


class Engine:
    def __init__(self, db_path: Path = DEFAULT_DB, model: SemanticModel | None = None,
                 audit_path: Path | None = DEFAULT_AUDIT):
        if not Path(db_path).exists():
            raise FileNotFoundError(f"{db_path} not found. Run `make data` first.")
        self.model = model or load_model()
        # Read-only, no access to files or network outside the database, settings locked.
        self.con = duckdb.connect(str(db_path), read_only=True, config={
            "enable_external_access": False, "lock_configuration": True})
        # One query at a time on the shared connection, so a timeout never interrupts
        # another tool call running in parallel.
        self._lock = threading.Lock()
        self.audit_path = audit_path
        self._cardinality = {
            name: self.con.sql(
                f"SELECT count(DISTINCT {dim.sql}) FROM {self.model.joins[dim.join].table} "
                f"{self.model.joins[dim.join].alias}"
            ).fetchone()[0]
            for name, dim in self.model.dimensions.items()
        }

    # -- guardrails -----------------------------------------------------------------
    def _preflight(self, req: MetricRequest, q: CompiledQuery) -> str:
        groups = 1
        for d in req.dimensions:
            groups *= max(1, self._cardinality[d])
        if groups > GROUP_BUDGET:
            raise GuardrailError(
                f"Up to {groups:,} groups for {req.dimensions}; the budget is {GROUP_BUDGET:,}. "
                "Use fewer or coarser dimensions (e.g. month instead of day) or add filters.")
        try:
            plan = self.con.execute("EXPLAIN " + q.sql, q.params).fetchall()
        except duckdb.Error as e:
            raise GuardrailError("The query did not pass EXPLAIN.") from e
        return "\n".join(row[1] for row in plan)

    def _run_with_timeout(self, q: CompiledQuery) -> list[tuple]:
        timer = threading.Timer(TIMEOUT_S, self.con.interrupt)
        timer.start()
        try:
            return self.con.execute(q.sql, q.params).fetchall()
        except duckdb.InterruptException:
            raise GuardrailError(f"Query exceeded the {TIMEOUT_S:g}s timeout.") from None
        finally:
            timer.cancel()

    # -- public API ----------------------------------------------------------------
    def explain(self, req: MetricRequest) -> dict:
        with self._lock:
            return self._audited("explain_query", req, lambda: self._explain(req))

    def query(self, req: MetricRequest) -> dict:
        with self._lock:
            return self._audited("query_metrics", req, lambda: self._query(req))

    def _explain(self, req: MetricRequest) -> dict:
        q = compile_request(self.model, req)
        plan = self._preflight(req, q)
        return {"sql": q.sql, "params": [_jsonable(p) for p in q.params], "plan": plan}

    def _query(self, req: MetricRequest) -> dict:
        q = compile_request(self.model, req)
        self._preflight(req, q)
        rows = self._run_with_timeout(q)
        return {
            "columns": q.columns,
            "rows": [[_jsonable(v) for v in r] for r in rows],
            "row_count": len(rows),
            "truncated": len(rows) == q.limit,
            "sql": q.sql,
            "params": [_jsonable(p) for p in q.params],
        }

    def _audited(self, tool: str, req: MetricRequest, fn) -> dict:
        start = time.perf_counter()
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "tool": tool,
                 "request": req.__dict__}
        try:
            result = fn()
            entry.update(status="ok", rows=result.get("row_count"), sql=result.get("sql"))
            return result
        except (RequestError, GuardrailError) as e:
            cause = f" ({e.__cause__})" if e.__cause__ else ""
            entry.update(status="rejected", reason=f"{e}{cause}")
            raise
        except Exception as e:  # unexpected: record it, never leak a stack trace to the agent
            entry.update(status="error", reason=type(e).__name__)
            raise
        finally:
            entry["ms"] = round((time.perf_counter() - start) * 1000, 1)
            self._write_audit(entry)

    def _write_audit(self, entry: dict) -> None:
        if not self.audit_path:
            return
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
