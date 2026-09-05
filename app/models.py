"""Request/response models.

Money-like values are Decimal in the database and in the enforcement code; they
are surfaced as floats in JSON purely for readability at the demo terminal.
NUMERIC(12,6) survives the round trip intact at these magnitudes.
"""

from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CreateDataset(BaseModel):
    name: str
    epsilon_cap: Decimal = Field(gt=0)
    epsilon_spent: Decimal = Decimal(0)  # seed a near-cap precondition directly
    delta: Decimal = Decimal(0)


class SeedDataset(BaseModel):
    """Test-only. Forces the running columns for an experiment precondition."""

    epsilon_spent: Decimal = Decimal(0)
    epsilon_reserved: Decimal = Decimal(0)
    delete_records: bool = True


class ReserveRequestBody(BaseModel):
    job_id: str
    idempotency_key: str
    epsilon_cost: Decimal = Field(gt=0)
    strategy: str = "atomic"
    actor: str | None = None
    epsilon_source: str = "passed_in"


class CommitBody(BaseModel):
    actual_cost: Decimal = Field(ge=0)


class RunBody(BaseModel):
    """Full reserve -> run_operation -> commit cycle (plan section 7).

    `fault` injects a failure in the gap between reserve and commit, which is
    the window Experiment 4 exists to test.
    """

    job_id: str
    idempotency_key: str
    strategy: str = "atomic"
    adapter: str = "opendp"
    spec: dict[str, Any] = Field(default_factory=lambda: {"kind": "count", "scale": 0.5})
    actor: str | None = None
    fault: Literal["none", "abort_before_commit", "crash_before_commit"] = "none"


class ConstraintBody(BaseModel):
    enabled: bool


class EpsilonQuery(BaseModel):
    adapter: str = "opendp"
    spec: dict[str, Any] = Field(default_factory=lambda: {"kind": "count", "scale": 0.5})


def dataset_out(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = {
        "id": str(row["id"]),
        "name": row.get("name"),
        "epsilon_cap": float(row["epsilon_cap"]),
        "epsilon_spent": float(row["epsilon_spent"]),
        "epsilon_reserved": float(row["epsilon_reserved"]),
    }
    out["total"] = out["epsilon_spent"] + out["epsilon_reserved"]
    out["free"] = out["epsilon_cap"] - out["total"]
    if "delta" in row:
        out["delta"] = float(row["delta"])
    if "created_at" in row:
        out["created_at"] = row["created_at"].isoformat()
    return out


def record_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "dataset_id": str(row["dataset_id"]),
        "job_id": row["job_id"],
        "idempotency_key": row["idempotency_key"],
        "epsilon_reserved": float(row["epsilon_reserved"]),
        "epsilon_committed": (
            float(row["epsilon_committed"])
            if row["epsilon_committed"] is not None
            else None
        ),
        "status": row["status"],
        "strategy": row["strategy"],
        "epsilon_source": row.get("epsilon_source"),
        "actor": row.get("actor"),
        "reserved_at": row["reserved_at"].isoformat(),
        "settled_at": row["settled_at"].isoformat() if row["settled_at"] else None,
    }


def invariants_out(inv: dict[str, Any]) -> dict[str, Any]:
    numeric = {
        "epsilon_cap",
        "epsilon_spent",
        "epsilon_reserved",
        "total",
        "overshoot",
        "records_committed",
        "records_reserved",
    }
    return {
        k: (float(v) if k in numeric else v)
        for k, v in inv.items()
    }


def uuid_str(value: UUID | None) -> str | None:
    return str(value) if value is not None else None
