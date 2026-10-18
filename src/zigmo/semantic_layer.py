"""Load and validate the semantic model (semantic/model.yaml)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

DEFAULT_MODEL = Path(__file__).resolve().parents[2] / "semantic" / "model.yaml"
_REF = re.compile(r"\{(\w+)\}")


class Join(BaseModel):
    table: str
    alias: str
    condition: str


class Metric(BaseModel):
    description: str
    sql: str | None = None
    filter: str | None = None
    derived: str | None = None
    format: Literal["currency", "integer", "percent", "number"] = "number"

    @model_validator(mode="after")
    def _one_kind(self) -> Metric:
        if (self.sql is None) == (self.derived is None):
            raise ValueError("a metric needs exactly one of 'sql' or 'derived'")
        if self.derived and self.filter:
            raise ValueError("derived metrics cannot have their own filter")
        return self

    @property
    def references(self) -> list[str]:
        return _REF.findall(self.derived or "")


class Dimension(BaseModel):
    sql: str
    join: str
    description: str


class Fact(BaseModel):
    table: str
    alias: str


class SemanticModel(BaseModel):
    name: str
    description: str
    fact: Fact
    joins: dict[str, Join]
    metrics: dict[str, Metric]
    dimensions: dict[str, Dimension]
    time_column: str

    @model_validator(mode="after")
    def _references_exist(self) -> SemanticModel:
        for name, m in self.metrics.items():
            for ref in m.references:
                if ref not in self.metrics or self.metrics[ref].derived:
                    raise ValueError(f"metric '{name}' references unknown base metric '{ref}'")
        for name, d in self.dimensions.items():
            if d.join not in self.joins:
                raise ValueError(f"dimension '{name}' uses unknown join '{d.join}'")
        return self

    def catalog(self) -> dict:
        """What an agent sees: names and meanings, never table internals."""
        return {
            "model": self.name,
            "description": self.description.strip(),
            "metrics": {k: {"description": v.description, "format": v.format}
                        for k, v in self.metrics.items()},
            "dimensions": {k: v.description for k, v in self.dimensions.items()},
        }


def load_model(path: Path = DEFAULT_MODEL) -> SemanticModel:
    return SemanticModel.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
