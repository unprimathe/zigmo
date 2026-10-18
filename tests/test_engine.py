import json

import duckdb
import pytest

from zigmo import engine as engine_mod
from zigmo.compiler import MetricRequest as R
from zigmo.compiler import RequestError
from zigmo.engine import GuardrailError


def as_dicts(result):
    return [dict(zip(result["columns"], row)) for row in result["rows"]]


def test_totals_match_hand_calculation(engine):
    r = engine.query(R(metrics=["gross_revenue", "returns_value", "net_revenue", "orders",
                                "units", "aov", "return_rate", "customers"]))
    assert as_dicts(r) == [{"gross_revenue": 60.0, "returns_value": 30.0, "net_revenue": 30.0,
                            "orders": 3, "units": 7, "aov": 20.0, "return_rate": 0.5,
                            "customers": 1}]


def test_split_by_country_with_share(engine):
    rows = as_dicts(engine.query(R(metrics=["gross_revenue"], dimensions=["country"],
                                   with_share=True)))
    assert rows[0] == {"country": "United Kingdom", "gross_revenue": 40.0,
                       "gross_revenue_share": pytest.approx(2 / 3)}
    assert sum(r["gross_revenue_share"] for r in rows) == pytest.approx(1.0)


def test_date_range_is_inclusive(engine):
    rows = as_dicts(engine.query(R(metrics=["orders"], date_from="2010-02-10",
                                   date_to="2010-02-10")))
    assert rows == [{"orders": 2}]


def test_injection_attempt_matches_nothing(engine):
    rows = as_dicts(engine.query(R(metrics=["orders"],
                                   filters={"country": ["x' OR '1'='1"]})))
    assert rows == [{"orders": 0}]


def test_connection_is_read_only(engine):
    with pytest.raises(duckdb.Error):
        engine.con.execute("DELETE FROM fact_sales")


def test_group_budget_rejects_explosive_requests(engine, monkeypatch):
    monkeypatch.setattr(engine_mod, "GROUP_BUDGET", 3)
    with pytest.raises(GuardrailError, match="budget"):
        engine.query(R(metrics=["units"], dimensions=["country", "product"]))


def test_explain_returns_sql_and_plan_without_running(engine):
    out = engine.explain(R(metrics=["units"], dimensions=["product"]))
    assert out["sql"].startswith("SELECT") and "GROUP" in out["plan"].upper()


def test_every_call_is_audited(engine):
    engine.query(R(metrics=["units"]))
    with pytest.raises(RequestError):
        engine.query(R(metrics=["profit"]))
    lines = [json.loads(x) for x in engine.audit_path.read_text().splitlines()]
    assert [e["status"] for e in lines] == ["ok", "rejected"]
    assert "sql" in lines[0] and "Unknown metrics" in lines[1]["reason"]


def test_timeout_interrupts_and_connection_survives(engine, monkeypatch):
    def slow(*args, **kwargs):
        raise duckdb.InterruptException("interrupted")
    real = engine.con.execute

    class Con:  # EXPLAIN goes through; the query itself is "interrupted"
        def execute(self, sql, params=None):
            return real(sql, params) if sql.startswith("EXPLAIN") else slow()

        def interrupt(self):
            pass

    monkeypatch.setattr(engine, "con", Con())
    with pytest.raises(GuardrailError, match="timeout"):
        engine.query(R(metrics=["units"]))
    monkeypatch.undo()
    assert engine.query(R(metrics=["units"]))["rows"] == [[7]]


def test_explain_failure_is_rejected_without_leaking_details(engine, monkeypatch):
    monkeypatch.setattr(engine.model.metrics["units"], "sql", "SUM(f.no_such_column)")
    with pytest.raises(GuardrailError, match=r"^The query did not pass EXPLAIN\.$"):
        engine.query(R(metrics=["units"]))


def test_no_access_to_files_outside_the_database(engine):
    with pytest.raises(duckdb.Error):
        engine.con.execute("SELECT * FROM read_text('README.md')").fetchall()
