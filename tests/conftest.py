"""A tiny star schema with hand-checkable numbers, so tests never need the real dataset."""

from pathlib import Path

import duckdb
import pytest

from zigmo.engine import Engine

FIXTURE_SQL = """
CREATE TABLE dim_date AS SELECT * FROM (VALUES
  (20100105, DATE '2010-01-05', 2010, 1, '2010-Q1', '2010-01', '2010-01-04', 'Tuesday'),
  (20100210, DATE '2010-02-10', 2010, 1, '2010-Q1', '2010-02', '2010-02-08', 'Wednesday'),
  (20100415, DATE '2010-04-15', 2010, 2, '2010-Q2', '2010-04', '2010-04-12', 'Thursday')
) t(date_key, date, year, quarter, year_quarter, year_month, week_start, day_of_week);

CREATE TABLE dim_country AS SELECT * FROM (VALUES (1, 'France'), (2, 'United Kingdom'))
  t(country_key, country);

CREATE TABLE dim_product AS SELECT * FROM (VALUES (1, '10001', 'MUG'), (2, '10002', 'LAMP'))
  t(product_key, stock_code, description);

CREATE TABLE dim_customer AS SELECT * FROM (VALUES
  (0, NULL::INTEGER, 'Unknown'), (1, 501, 'France'), (2, 502, 'United Kingdom')
) t(customer_key, customer_id, home_country);

-- Hand totals:
--   UK:     invoices A1 (2 mugs x 5 = 10) and A2 (1 lamp x 30 = 30)  -> gross 40, 2 orders
--   France: invoice A3 (4 mugs x 5 = 20, guest)                       -> gross 20, 1 order
--   Return: C1 (-1 lamp x 30 = -30, UK)
--   gross 60 | returns 30 | net 30 | orders 3 | units 7 | aov 20 | return_rate 0.5
CREATE TABLE fact_sales AS SELECT * FROM (VALUES
  ('A1', TIMESTAMP '2010-01-05 10:00', 20100105, 1, 2, 2,  2,  5.00,  10.00, false),
  ('A2', TIMESTAMP '2010-02-10 11:00', 20100210, 2, 2, 2,  1, 30.00,  30.00, false),
  ('A3', TIMESTAMP '2010-02-10 12:00', 20100210, 1, 0, 1,  4,  5.00,  20.00, false),
  ('C1', TIMESTAMP '2010-04-15 09:00', 20100415, 2, 2, 2, -1, 30.00, -30.00, true)
) t(invoice, invoice_ts, date_key, product_key, customer_key, country_key, quantity,
    unit_price, amount, is_return);
"""


@pytest.fixture(scope="session")
def db_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("db") / "fixture.duckdb"
    con = duckdb.connect(str(path))
    con.execute(FIXTURE_SQL)
    con.close()
    return path


@pytest.fixture
def engine(db_path, tmp_path) -> Engine:
    return Engine(db_path=db_path, audit_path=tmp_path / "audit.jsonl")
