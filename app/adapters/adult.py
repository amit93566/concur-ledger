"""Adult adapter: real OpenDP queries over the UCI Adult census data.

The opendp adapter runs on generated values; this one runs on a real dataset,
the `adult` table (data/adult.data, 32,561 people). A query is a noisy count or
a noisy bounded sum, optionally over the people matching one filter, at one of
four accuracy levels.

OpenDP does all of the privacy arithmetic. The transformation reports its own
sensitivity; the Laplace scale is that sensitivity times the level's factor;
the measurement reports epsilon (`map`), and OpenDP's accuracy helper reports
the 95% error bound. Nothing here computes a privacy quantity itself.

A count is an integer, so OpenDP adds discrete Laplace noise and every error is
a whole number. Its bound comes from OpenDP's discrete helper, rounded down to
a whole number: an integer error is within t exactly when it is within floor(t),
so rounding down keeps the 95% guarantee and drops the fraction. (The continuous
formula, scale x ln 20, gives +-1.5 at high and +-5.99 at low; the second
covers only 93.8%, because an error of exactly 6 falls outside it.) A sum runs
on floats with continuous Laplace noise and keeps the continuous helper.

    level      scale            epsilon   count error (95%)   coverage
    high       0.5 x sens       2         +-1                 96.8%
    medium     1   x sens       1         +-3                 97.3%
    low        2   x sens       0.5       +-6                 96.2%
    very_low   5   x sens       0.2       +-15                95.5%

The filter runs before OpenDP. Keeping or dropping each person by their own
row is 1-stable under the symmetric distance, so it does not change what one
person can contribute: the count's sensitivity stays 1, the sum's stays the
column's bound. The privacy unit is one row, one person.

The rows are read from Postgres once and kept in memory. The operation runs
between reserve and commit with no database connection held, like every other
adapter.
"""

import asyncio
import math
from decimal import ROUND_CEILING, Decimal
from typing import Any, Callable

from app.adapters.base import OperationResult, register
from app.adapters.opendp_adapter import _opendp

_QUANTUM = Decimal("0.000001")
CONFIDENCE = 0.95

# Columns a sum may use, with the bounds every value is clamped to.
SUM_COLUMNS: dict[str, tuple[str, tuple[float, float]]] = {
    "age": ("age", (17.0, 90.0)),
    "hours_per_week": ("hours worked per week", (1.0, 99.0)),
    "education_num": ("years of education", (1.0, 16.0)),
}

# key: (short label for a menu, phrase that follows "people", predicate)
FILTERS: dict[str, tuple[str, str, Callable[[dict[str, Any]], bool]]] = {
    "all": ("everyone", "", lambda r: True),
    "income_gt_50k": ("income above $50K", "with income above $50K", lambda r: r["income"] == ">50K"),
    "female": ("women", "who are women", lambda r: r["sex"] == "Female"),
    "male": ("men", "who are men", lambda r: r["sex"] == "Male"),
    "age_40_plus": ("aged 40+", "aged 40 or over", lambda r: r["age"] >= 40),
    "full_time": ("40+ hours a week", "working 40+ hours a week", lambda r: r["hours_per_week"] >= 40),
    "degree": ("bachelor's or higher", "with a bachelor's degree or higher", lambda r: r["education_num"] >= 13),
}

LEVELS: dict[str, tuple[str, float]] = {
    "high": ("High", 0.5),
    "medium": ("Medium", 1.0),
    "low": ("Low", 2.0),
    "very_low": ("Very low", 5.0),
}

# The published schema (UCI's adult.names), for the dataset card. These are
# documented facts about the columns, not values read from the rows, so
# showing them releases nothing about any person.
SCHEMA: list[dict[str, Any]] = [
    {"column": "age", "type": "number", "values": "whole years"},
    {"column": "workclass", "type": "category", "values": "8: Private, Self-emp-not-inc, Self-emp-inc, Federal-gov, Local-gov, State-gov, Without-pay, Never-worked"},
    {"column": "fnlwgt", "type": "number", "values": "census sampling weight"},
    {"column": "education", "type": "category", "values": "16 levels, Preschool to Doctorate"},
    {"column": "education_num", "type": "number", "values": "education level as a number, 1 to 16"},
    {"column": "marital_status", "type": "category", "values": "7: Married-civ-spouse, Divorced, Never-married, Separated, Widowed, Married-spouse-absent, Married-AF-spouse"},
    {"column": "occupation", "type": "category", "values": "14, e.g. Tech-support, Craft-repair, Sales, Exec-managerial, Prof-specialty"},
    {"column": "relationship", "type": "category", "values": "6: Wife, Own-child, Husband, Not-in-family, Other-relative, Unmarried"},
    {"column": "race", "type": "category", "values": "5: White, Asian-Pac-Islander, Amer-Indian-Eskimo, Other, Black"},
    {"column": "sex", "type": "category", "values": "Female, Male"},
    {"column": "capital_gain", "type": "number", "values": "dollars"},
    {"column": "capital_loss", "type": "number", "values": "dollars"},
    {"column": "hours_per_week", "type": "number", "values": "hours worked per week"},
    {"column": "native_country", "type": "category", "values": "41 countries"},
    {"column": "income", "type": "category", "values": "<=50K, >50K"},
]

_rows: list[dict[str, Any]] | None = None
_load_lock = asyncio.Lock()


async def rows() -> list[dict[str, Any]]:
    """The dataset, read once from Postgres."""
    global _rows
    if _rows is None:
        async with _load_lock:
            if _rows is None:
                from app.db import get_pool  # late: the pool opens at start-up

                async with get_pool().connection() as conn:
                    cur = await conn.execute(
                        "SELECT age, education_num, sex, hours_per_week, income FROM adult"
                    )
                    _rows = await cur.fetchall()
    return _rows


def parse(spec: dict[str, Any]) -> tuple[str, str | None, str, str]:
    kind = str(spec.get("kind", "count"))
    column = spec.get("column")
    filt = str(spec.get("filter", "all"))
    level = str(spec.get("accuracy", "high"))
    if kind not in ("count", "sum"):
        raise ValueError("spec.kind must be 'count' or 'sum'")
    if kind == "sum" and column not in SUM_COLUMNS:
        raise ValueError(f"spec.column must be one of {', '.join(SUM_COLUMNS)}")
    if filt not in FILTERS:
        raise ValueError(f"spec.filter must be one of {', '.join(FILTERS)}")
    if level not in LEVELS:
        raise ValueError(f"spec.accuracy must be one of {', '.join(LEVELS)}")
    return kind, (column if kind == "sum" else None), filt, level


def label(spec: dict[str, Any]) -> str:
    kind, column, filt, level = parse(spec)
    who = FILTERS[filt][1]
    if kind == "count":
        what = f"How many people {who}" if who else "How many people in total"
    else:
        what = f"Total {SUM_COLUMNS[column][0]} of people {who}" if who else f"Total {SUM_COLUMNS[column][0]} of everyone"
    return f"{what} ({LEVELS[level][0].lower()} accuracy)"


_cache: dict[tuple, tuple[Any, float]] = {}


def build(spec: dict[str, Any]) -> tuple[Any, float]:
    """The measurement and its Laplace scale. Cached: the same spec always
    has the same price."""
    key = parse(spec)
    if key in _cache:
        return _cache[key]
    dp = _opendp()
    kind, column, _, level = key
    if kind == "count":
        t = (dp.vector_domain(dp.atom_domain(T=int)), dp.symmetric_distance()) >> dp.t.then_count()
    else:
        t = (
            (dp.vector_domain(dp.atom_domain(T=float)), dp.symmetric_distance())
            >> dp.t.then_clamp(SUM_COLUMNS[column][1])
            >> dp.t.then_sum()
        )
    scale = float(t.map(1)) * LEVELS[level][1]
    meas = t >> dp.m.then_laplace(scale=scale)
    _cache[key] = (meas, scale)
    return meas, scale


def price(spec: dict[str, Any]) -> dict[str, Any]:
    """What a question costs and how accurate its answer is, before asking."""
    meas, scale = build(spec)
    dp = _opendp()
    if parse(spec)[0] == "count":
        # Whole-number errors: the largest whole number within OpenDP's bound.
        accuracy: float = math.floor(dp.discrete_laplacian_scale_to_accuracy(scale, 1 - CONFIDENCE))
    else:
        accuracy = round(dp.laplacian_scale_to_accuracy(scale, 1 - CONFIDENCE), 2)
    return {
        "question": label(spec),
        "epsilon": float(Decimal(str(meas.map(1))).quantize(_QUANTUM, rounding=ROUND_CEILING)),
        "accuracy": accuracy,
        "confidence": CONFIDENCE,
        "scale": scale,
    }


async def truth(spec: dict[str, Any]) -> float:
    """The exact answer, with no noise: for the data owner only.

    The same filter and the same clamp as the noisy query, without the Laplace
    step, so the gap between the two is exactly the noise OpenDP added.
    """
    kind, column, filt, _ = parse(spec)
    keep = FILTERS[filt][2]
    data = await rows()
    if kind == "count":
        return float(sum(1 for r in data if keep(r)))
    lo, hi = SUM_COLUMNS[column][1]
    return float(sum(min(max(float(r[column]), lo), hi) for r in data if keep(r)))


class AdultAdapter:
    name = "adult"
    is_stub = False

    def available(self) -> tuple[bool, str]:
        try:
            _opendp()
        except Exception as exc:
            return False, str(exc)
        return True, "OpenDP over the UCI Adult data (32,561 rows)"

    def epsilon_for(self, spec: dict[str, Any]) -> Decimal:
        meas, _ = build(spec)
        # Round UP to ledger precision: never charge less than OpenDP reported.
        return Decimal(str(meas.map(1))).quantize(_QUANTUM, rounding=ROUND_CEILING)

    async def run_operation(self, spec: dict[str, Any]) -> OperationResult:
        kind, column, filt, _ = parse(spec)
        meas, scale = build(spec)
        keep = FILTERS[filt][2]
        data = await rows()
        if kind == "count":
            values: list = [1 for r in data if keep(r)]
        else:
            values = [float(r[column]) for r in data if keep(r)]
        noisy = await asyncio.to_thread(meas, values)
        return OperationResult(
            epsilon=self.epsilon_for(spec),
            source="opendp",
            result=round(noisy) if kind == "count" else round(noisy, 1),
            detail={"question": label(spec), "scale": scale, "dataset": "UCI Adult"},
        )


register(AdultAdapter())
