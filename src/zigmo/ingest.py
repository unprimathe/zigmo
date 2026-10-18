"""Download Online Retail II (UCI, CC BY 4.0) and build the dimensional model in DuckDB.

Usage: zigmo-ingest [--db data/zigmo.duckdb]
"""

from __future__ import annotations

import argparse
import hashlib
import urllib.request
import zipfile
from pathlib import Path

import duckdb

DATA_URL = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
DATA_SHA256 = "572e36277c2390fbfde10664750731e0a86f55e33470d91919085f0408e67bfb"
SHEETS = ("Year 2009-2010", "Year 2010-2011")

DATA_DIR = Path("data")
ZIP_PATH = DATA_DIR / "online_retail_ii.zip"
XLSX_PATH = DATA_DIR / "online_retail_II.xlsx"
RAW_PATH = DATA_DIR / "raw.parquet"
DEFAULT_DB = DATA_DIR / "zigmo.duckdb"

# Product codes are 5 digits plus an optional variant suffix (e.g. 85123A), or the
# "DCGS" range. Everything else (POST, DOT, M, BANK CHARGES, AMAZONFEE, gift vouchers...)
# is postage, fees or manual adjustments, not merchandise.
PRODUCT_CODE_REGEX = r"^([0-9]{5}[A-Za-z]*|DCGS.*)$"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if not ZIP_PATH.exists():
        print(f"Downloading {DATA_URL}")
        urllib.request.urlretrieve(DATA_URL, ZIP_PATH)
    digest = _sha256(ZIP_PATH)
    if digest != DATA_SHA256:
        raise SystemExit(f"Checksum mismatch for {ZIP_PATH}: {digest}")
    if not XLSX_PATH.exists():
        with zipfile.ZipFile(ZIP_PATH) as z:
            z.extractall(DATA_DIR)


def to_parquet(con: duckdb.DuckDBPyConnection) -> None:
    """Read both sheets as text (the Invoice column mixes numbers and 'C123')."""
    if RAW_PATH.exists():
        return
    con.sql("INSTALL excel; LOAD excel;")
    union = " UNION ALL ".join(
        f"SELECT * FROM read_xlsx('{XLSX_PATH}', sheet='{s}', all_varchar=true)" for s in SHEETS
    )
    con.sql(f"COPY ({union}) TO '{RAW_PATH}' (FORMAT parquet)")


MODEL_SQL = f"""
-- 1. Typed, deduplicated lines. Exact duplicates include the Dec-2010 overlap between sheets.
CREATE OR REPLACE TEMP TABLE lines AS
SELECT DISTINCT
    trim(Invoice)                                             AS invoice,
    trim(StockCode)                                           AS stock_code,
    nullif(trim(Description), '')                             AS description,
    CAST(Quantity AS INTEGER)                                 AS quantity,
    COALESCE(
        TRY_STRPTIME(InvoiceDate, '%Y-%m-%d %H:%M:%S'),
        TIMESTAMP '1899-12-30' + to_milliseconds(
            CAST(round(TRY_CAST(InvoiceDate AS DOUBLE) * 86400000) AS BIGINT))
    )                                                         AS invoice_ts,
    CAST(Price AS DECIMAL(12, 2))                             AS unit_price,
    CAST(TRY_CAST("Customer ID" AS DOUBLE) AS INTEGER)        AS customer_id,
    trim(Country)                                             AS country
FROM read_parquet('{RAW_PATH}');

-- 2. Keep merchandise sales and returns. Drop postage/fees, zero-price lines and
--    stock adjustments (negative quantity without a 'C' credit note).
CREATE OR REPLACE TEMP TABLE kept AS
SELECT *, invoice LIKE 'C%' AS is_return
FROM lines
WHERE regexp_matches(stock_code, '{PRODUCT_CODE_REGEX}')
  AND unit_price > 0
  AND (quantity > 0 OR invoice LIKE 'C%');

-- 3. Dimensions.
CREATE OR REPLACE TABLE dim_date AS
SELECT
    CAST(strftime(d, '%Y%m%d') AS INTEGER) AS date_key,
    CAST(d AS DATE)                        AS date,
    year(d)                                AS year,
    quarter(d)                             AS quarter,
    strftime(d, '%Y-Q') || quarter(d)      AS year_quarter,
    strftime(d, '%Y-%m')                   AS year_month,
    strftime(date_trunc('week', d), '%Y-%m-%d') AS week_start,
    dayname(d)                             AS day_of_week
FROM range(
    (SELECT CAST(min(invoice_ts) AS DATE) FROM kept),
    (SELECT CAST(max(invoice_ts) AS DATE) FROM kept) + INTERVAL 1 DAY,
    INTERVAL 1 DAY) t(d);

CREATE OR REPLACE TABLE dim_country AS
SELECT row_number() OVER (ORDER BY country) AS country_key, country
FROM (SELECT DISTINCT country FROM kept);

CREATE OR REPLACE TABLE dim_product AS
SELECT row_number() OVER (ORDER BY stock_code) AS product_key,
       stock_code,
       mode(upper(description)) AS description
FROM kept
GROUP BY stock_code;

-- customer_key 0 = unknown customer (guest checkout).
CREATE OR REPLACE TABLE dim_customer AS
SELECT 0 AS customer_key, NULL::INTEGER AS customer_id, 'Unknown' AS home_country
UNION ALL
SELECT row_number() OVER (ORDER BY customer_id), customer_id, mode(country)
FROM kept WHERE customer_id IS NOT NULL
GROUP BY customer_id;

-- 4. Fact: one row per invoice line. Returns carry negative quantity and amount.
CREATE OR REPLACE TABLE fact_sales AS
SELECT
    k.invoice,
    k.invoice_ts,
    CAST(strftime(k.invoice_ts, '%Y%m%d') AS INTEGER) AS date_key,
    p.product_key,
    COALESCE(c.customer_key, 0)                       AS customer_key,
    co.country_key,
    k.quantity,
    k.unit_price,
    CAST(k.quantity * k.unit_price AS DECIMAL(14, 2)) AS amount,
    k.is_return
FROM kept k
JOIN dim_product p USING (stock_code)
JOIN dim_country co USING (country)
LEFT JOIN dim_customer c ON c.customer_id = k.customer_id;
"""


def build(db_path: Path) -> dict[str, int]:
    db_path.parent.mkdir(exist_ok=True)
    tmp = db_path.with_suffix(".tmp.duckdb")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    try:
        to_parquet(con)
        con.execute(MODEL_SQL)
        counts = {
            t: con.sql(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("fact_sales", "dim_date", "dim_product", "dim_customer", "dim_country")
        }
        counts["raw_lines"] = con.sql(f"SELECT count(*) FROM read_parquet('{RAW_PATH}')").fetchone()[0]
    finally:
        con.close()
    tmp.replace(db_path)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    download()
    counts = build(args.db)
    for table, n in counts.items():
        print(f"{table:>14}: {n:,}")
    print(f"Built {args.db}")


if __name__ == "__main__":
    main()
