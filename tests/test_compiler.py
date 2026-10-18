import pytest

from zigmo.compiler import MetricRequest, RequestError, compile_request
from zigmo.semantic_layer import load_model

MODEL = load_model()


def compile_(**kw):
    return compile_request(MODEL, MetricRequest(**kw))


def test_only_needed_joins():
    q = compile_(metrics=["gross_revenue"], dimensions=["country"])
    assert "dim_country" in q.sql
    assert "dim_product" not in q.sql and "dim_date" not in q.sql


def test_filter_values_are_parameters_not_sql():
    evil = "France') OR 1=1 --"
    q = compile_(metrics=["units"], filters={"country": [evil]})
    assert evil not in q.sql
    assert q.params == [evil]


def test_derived_metric_expands_to_base_metrics():
    q = compile_(metrics=["aov"])
    assert "COUNT(DISTINCT f.invoice)" in q.sql and "SUM(f.amount)" in q.sql


@pytest.mark.parametrize("kwargs, message", [
    ({"metrics": []}, "at least one metric"),
    ({"metrics": ["revenue"]}, "Unknown metrics"),
    ({"metrics": ["units"], "dimensions": ["region"]}, "Unknown dimensions"),
    ({"metrics": ["units"], "filters": {"region": ["x"]}}, "Unknown filter dimensions"),
    ({"metrics": ["units"], "dimensions": ["year", "month", "day", "country"]}, "At most 3"),
    ({"metrics": ["units"], "limit": 0}, "limit"),
    ({"metrics": ["units"], "limit": 5000}, "limit"),
    ({"metrics": ["units"], "order_by": "orders"}, "order_by"),
    ({"metrics": ["units"], "with_share": True}, "with_share"),
    ({"metrics": ["units"], "date_from": "last year"}, "ISO date"),
    ({"metrics": ["units"], "date_from": "2011-01-01", "date_to": "2010-01-01"}, "on or before"),
    ({"metrics": ["units", "units"]}, "Duplicated"),
    ({"metrics": ["units"] * 9}, "At most 8"),
    ({"metrics": ["units"], "filters": {"country": ["x"] * 51}}, "between 1 and 50"),
    ({"metrics": ["units"], "filters": {"country": []}}, "between 1 and 50"),
])
def test_invalid_requests_are_rejected(kwargs, message):
    with pytest.raises(RequestError, match=message):
        compile_(**kwargs)


def test_share_skips_ratio_metrics():
    q = compile_(metrics=["gross_revenue", "aov"], dimensions=["country"], with_share=True)
    assert "gross_revenue_share" in q.columns and "aov_share" not in q.columns
