"""Deterministic eval: the semantic layer must return the oracle's answer for every question.

Runs against the real database (make data). No API key needed; runs in CI.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import yaml

from zigmo.compiler import MetricRequest
from zigmo.engine import Engine

QUESTIONS = Path(__file__).with_name("questions.yaml")


def same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-6)
    return str(a) == str(b)


def rows_match(got: list[list], want: list[tuple]) -> bool:
    return len(got) == len(want) and all(
        len(g) == len(w) and all(same(x, y) for x, y in zip(g, w)) for g, w in zip(got, want))


def main() -> int:
    questions = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    engine = Engine(audit_path=None)
    oracle = engine.con  # same hardened read-only connection; oracle SQL only reads tables
    failed = 0
    for q in questions:
        got = engine.query(MetricRequest(**q["request"]))["rows"]
        want = [tuple(float(v) if hasattr(v, "as_tuple") else v for v in row)
                for row in oracle.sql(q["oracle"]).fetchall()]
        ok = rows_match(got, want)
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {q['id']}")
        if not ok:
            print(f"      got:  {got[:3]}\n      want: {want[:3]}")
    print(f"\n{len(questions) - failed}/{len(questions)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
