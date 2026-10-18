# 3. Returns, guest checkouts and non-merchandise lines

Status: accepted

## Context
Profiling Online Retail II (1,067,371 lines) found: 19,494 credit notes (invoice "C..."),
243,007 lines without a customer, 3,457 negative quantities without a credit note,
postage/fee/adjustment codes (POST, DOT, M, BANK CHARGES, AMAZONFEE...), and exact
duplicate lines, including the Dec 2010 overlap between the two source sheets.

## Decision
- Credit notes are returns: kept, with negative quantity and amount (`is_return`).
- Lines without a customer are kept as customer 0 ("Unknown"): they count for revenue,
  not for customer metrics.
- Negative quantities without a credit note are stock adjustments: dropped.
- Non-merchandise codes and zero/negative prices: dropped.
- Exact duplicates: dropped.

## Consequences
`fact_sales` holds 1,021,252 merchandise lines. `gross_revenue`, `returns_value` and
`return_rate` have unambiguous definitions, written once in `semantic/model.yaml`.
