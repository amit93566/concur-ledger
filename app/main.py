"""Budget enforcement service.

Endpoints: reserve / commit / release, plus a `run` endpoint that performs the
whole cycle the way a real client would, and read-only views used by the demo
and the experiment harnesses.

The rule this file exists to make visible: the cap check lives in `reserve` and
nowhere else, and the operation runs BETWEEN reserve and commit with no database
transaction open. See `run_job` below -- the adapter call sits between two
awaits that each own their own transaction.
"""

import os
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from app.adapters import AdapterUnavailable, get_adapter, registry
from app.adapters import opendp_adapter, passthrough  # noqa: F401  (registration)
from app.config import settings
from app.db import close_pool, get_pool, open_pool
from app.enforcement import atomic, naive  # noqa: F401  (registration)
from app.enforcement.base import (
    DatasetNotFound,
    ReserveRequest,
    StrategyNotImplemented,
    get_strategy,
)
from app.enforcement.settle import (
    OverCommit,
    ReservationNotFound,
    commit as do_commit,
    release as do_release,
)
from app.ledger import queries as q
from app.ledger.init_db import apply_schema, constraint_enabled, set_constraint
from app.models import (
    CommitBody,
    ConstraintBody,
    CreateDataset,
    EpsilonQuery,
    ReserveRequestBody,
    RunBody,
    SeedDataset,
    dataset_out,
    invariants_out,
    record_out,
)


# Identifies this process instance. The crash test uses it to tell that the
# service really died and came back: inside a container the PID is always 1,
# both before and after a restart, so a PID comparison silently proves nothing.
INSTANCE_ID = str(uuid4())
STARTED_AT = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await open_pool()
    async with pool.connection() as conn:
        await apply_schema(conn)

    try:
        async with pool.connection() as conn:
            await set_constraint(conn, settings.enforce_db_constraint)
    except Exception as exc:
        # Re-adding the CHECK fails if a previous Experiment 1 run left the
        # ledger over its cap. Do not die on it: the CHECK is a safety net, not
        # the enforcement mechanism, and refusing to boot after a successful
        # breach experiment would be absurd. Report it loudly instead.
        async with pool.connection() as conn:
            breaching = await q.breaching_datasets(conn)
        print(f"!! could not enable the within_cap CHECK: {exc}")
        print("!! the service is running WITHOUT the database safety net.")
        for row in breaching:
            print(
                f"!!   breached: {row['name']} ({row['id']}) "
                f"cap={row['epsilon_cap']} spent={row['epsilon_spent']} "
                f"reserved={row['epsilon_reserved']} overshoot={row['overshoot']}"
            )
        print("!! this is expected after Experiment 1; reset with `make reset-db`.")

    yield
    await close_pool()


app = FastAPI(title="concur-ledger", version="0.1.0", lifespan=lifespan)


def _admin_only() -> None:
    if not settings.allow_admin:
        raise HTTPException(403, "admin/test endpoints are disabled")


# --------------------------------------------------------------------------
# introspection
# --------------------------------------------------------------------------


@app.get("/health")
async def health():
    async with get_pool().connection() as conn:
        await conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/config")
async def config():
    """What mode is this run in? Shown at the demo so the audience can see the
    CHECK constraint really is off during the breach."""
    async with get_pool().connection() as conn:
        db_constraint = await constraint_enabled(conn)
    return {
        "enforce_db_constraint_env": settings.enforce_db_constraint,
        "within_cap_constraint_present": db_constraint,
        "naive_race_delay_ms": settings.naive_race_delay_ms,
        "pool_max_size": settings.pool_max_size,
        "implemented_strategies": ["naive", "atomic"],
        "phase_2_strategies": ["for_update", "serializable"],
        "pid": os.getpid(),
        "instance_id": INSTANCE_ID,
        "uptime_s": round(time.time() - STARTED_AT, 3),
    }


@app.get("/adapters")
async def adapters():
    out = []
    for name, ad in sorted(registry.items()):
        ok, detail = ad.available()
        out.append(
            {"name": name, "available": ok, "is_stub": ad.is_stub, "detail": detail}
        )
    return out


@app.post("/adapters/epsilon")
async def adapter_epsilon(body: EpsilonQuery):
    """The deterministic cost of a spec, without running anything.

    This is what makes a real epsilon source compatible with a reproducible
    near-cap scenario: the harness can ask the price up front.
    """
    try:
        eps = get_adapter(body.adapter).epsilon_for(body.spec)
    except AdapterUnavailable as exc:
        raise HTTPException(503, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"adapter": body.adapter, "spec": body.spec, "epsilon": float(eps)}


# --------------------------------------------------------------------------
# datasets
# --------------------------------------------------------------------------


@app.post("/datasets", status_code=201)
async def create_dataset(body: CreateDataset):
    async with get_pool().connection() as conn:
        row = await q.create_dataset(
            conn, body.name, body.epsilon_cap, body.epsilon_spent, body.delta
        )
    return dataset_out(row)


@app.get("/datasets")
async def list_datasets():
    async with get_pool().connection() as conn:
        return [dataset_out(r) for r in await q.list_datasets(conn)]


@app.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: UUID):
    async with get_pool().connection() as conn:
        row = await q.get_dataset(conn, dataset_id)
    if row is None:
        raise HTTPException(404, "dataset not found")
    return dataset_out(row)


@app.post("/datasets/{dataset_id}/seed")
async def seed_dataset(dataset_id: UUID, body: SeedDataset):
    _admin_only()
    async with get_pool().connection() as conn:
        if body.delete_records:
            await conn.execute(
                "DELETE FROM spend_records WHERE dataset_id = %s", (dataset_id,)
            )
        row = await q.seed_dataset(
            conn, dataset_id, body.epsilon_spent, body.epsilon_reserved
        )
    if row is None:
        raise HTTPException(404, "dataset not found")
    return dataset_out(row)


@app.get("/datasets/{dataset_id}/invariants")
async def invariants(dataset_id: UUID):
    async with get_pool().connection() as conn:
        inv = await q.check_invariants(conn, dataset_id)
        if inv is None:
            raise HTTPException(404, "dataset not found")
        counts = await q.status_counts(conn, dataset_id)
    out = invariants_out(inv)
    out["status_counts"] = counts
    return out


@app.get("/datasets/{dataset_id}/records")
async def records(dataset_id: UUID, limit: int = Query(500, ge=1, le=5000)):
    async with get_pool().connection() as conn:
        rows = await q.list_records(conn, dataset_id, limit)
    return [record_out(r) for r in rows]


@app.post("/admin/db-constraint")
async def db_constraint(body: ConstraintBody):
    """Add or drop the within_cap CHECK at runtime.

    Dropping it is required for Experiment 1. Experiment 2 also runs with it
    dropped, so that safety is attributable to the reserve strategy rather than
    to the database backstopping it.
    """
    _admin_only()
    async with get_pool().connection() as conn:
        try:
            state = await set_constraint(conn, body.enabled)
        except Exception as exc:
            # Re-adding fails if the table already breaches the cap. That is
            # evidence, not a bug -- surface it.
            raise HTTPException(409, f"could not set constraint: {exc}")
    return {"within_cap_constraint_present": state}


# --------------------------------------------------------------------------
# the enforcement protocol
# --------------------------------------------------------------------------


@app.post("/datasets/{dataset_id}/reserve")
async def reserve(dataset_id: UUID, body: ReserveRequestBody):
    req = ReserveRequest(
        dataset_id=dataset_id,
        job_id=body.job_id,
        idempotency_key=body.idempotency_key,
        epsilon_cost=body.epsilon_cost,
        strategy=body.strategy,
        epsilon_source=body.epsilon_source,
        actor=body.actor,
    )
    try:
        strategy = get_strategy(body.strategy)
    except StrategyNotImplemented as exc:
        raise HTTPException(501, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    try:
        outcome = await strategy(get_pool(), req)
    except DatasetNotFound:
        raise HTTPException(404, "dataset not found")

    payload = {
        "reservation_id": str(outcome.reservation_id),
        "status": outcome.status,
        "epsilon_reserved": float(outcome.epsilon_reserved),
        "strategy": outcome.strategy,
        "replayed": outcome.replayed,
        "retries": outcome.retries,
    }
    # A denial is a normal, correct outcome under contention -- 429, not an error.
    return JSONResponse(payload, status_code=429 if outcome.status == "denied" else 200)


@app.post("/reservations/{reservation_id}/commit")
async def commit(reservation_id: UUID, body: CommitBody):
    try:
        out = await do_commit(get_pool(), reservation_id, body.actual_cost)
    except ReservationNotFound:
        raise HTTPException(404, "reservation not found")
    except OverCommit as exc:
        raise HTTPException(400, str(exc))
    return _settle_out(out)


@app.post("/reservations/{reservation_id}/release")
async def release(reservation_id: UUID):
    try:
        out = await do_release(get_pool(), reservation_id)
    except ReservationNotFound:
        raise HTTPException(404, "reservation not found")
    return _settle_out(out)


@app.get("/reservations/{reservation_id}")
async def get_reservation(reservation_id: UUID):
    async with get_pool().connection() as conn:
        row = await q.get_record(conn, reservation_id)
    if row is None:
        raise HTTPException(404, "reservation not found")
    return record_out(row)


def _settle_out(out) -> dict:
    return {
        "reservation_id": str(out.reservation_id),
        "status": out.status,
        "epsilon_held": float(out.epsilon_held),
        "epsilon_committed": (
            float(out.epsilon_committed) if out.epsilon_committed is not None else None
        ),
        "already_settled": out.already_settled,
        "dataset": dataset_out(out.dataset),
    }


# --------------------------------------------------------------------------
# the full cycle, with fault injection
# --------------------------------------------------------------------------


@app.post("/datasets/{dataset_id}/run")
async def run_job(dataset_id: UUID, body: RunBody):
    """reserve -> run_operation -> commit, exactly as plan section 7 specifies.

    Note what is NOT here: any `async with pool.connection()` wrapping the
    adapter call. Reserve opens and closes its own transaction; the operation
    runs with nothing held; commit opens a new one.
    """
    try:
        adapter = get_adapter(body.adapter)
        cost = adapter.epsilon_for(body.spec)  # deterministic, known up front
    except AdapterUnavailable as exc:
        raise HTTPException(503, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    try:
        strategy = get_strategy(body.strategy)
    except StrategyNotImplemented as exc:
        raise HTTPException(501, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    req = ReserveRequest(
        dataset_id=dataset_id,
        job_id=body.job_id,
        idempotency_key=body.idempotency_key,
        epsilon_cost=cost,
        strategy=body.strategy,
        epsilon_source=body.adapter,
        actor=body.actor,
    )
    try:
        reserved = await strategy(get_pool(), req)
    except DatasetNotFound:
        raise HTTPException(404, "dataset not found")

    if reserved.status == "denied":
        return JSONResponse(
            {
                "reservation_id": str(reserved.reservation_id),
                "status": "denied",
                "epsilon_cost": float(cost),
            },
            status_code=429,
        )

    # ---- no transaction is held for the remainder of this block ----
    try:
        if body.fault == "crash_before_commit":
            _admin_only()
            # Hard-kill the process in the reserve->commit gap. The reservation
            # is already durable; nothing has been committed. Docker restarts
            # the service and crash_test.py then checks the invariants.
            os._exit(3)
        if body.fault == "abort_before_commit":
            _admin_only()
            raise RuntimeError("injected fault: aborted between reserve and commit")

        result = await adapter.run_operation(body.spec)
        settled = await do_commit(get_pool(), reserved.reservation_id, result.epsilon)
    except Exception as exc:
        # The compensating action. The hold is returned; spent is untouched.
        await do_release(get_pool(), reserved.reservation_id)
        raise HTTPException(500, f"operation failed, reservation released: {exc}")

    return {
        "reservation_id": str(reserved.reservation_id),
        "status": settled.status,
        "epsilon_cost": float(cost),
        "epsilon_committed": float(settled.epsilon_committed),
        "epsilon_source": result.source,
        "result": result.result,
        "detail": result.detail,
        "dataset": dataset_out(settled.dataset),
    }


@app.post("/debug/reserve-and-abort/{dataset_id}")
async def reserve_and_abort(dataset_id: UUID, body: ReserveRequestBody):
    """Reserve, then deliberately fail before commit WITHOUT releasing.

    This is the controlled-abort fault of plan section 8: it leaves the
    reservation in the `reserved` state exactly as a crash in that window would,
    which is what Experiment 4 needs to inspect. It tests the same protocol
    property as `docker kill` and is far more reproducible.
    """
    _admin_only()
    req = ReserveRequest(
        dataset_id=dataset_id,
        job_id=body.job_id,
        idempotency_key=body.idempotency_key,
        epsilon_cost=body.epsilon_cost,
        strategy=body.strategy,
        epsilon_source=body.epsilon_source,
        actor=body.actor,
    )
    try:
        outcome = await get_strategy(body.strategy)(get_pool(), req)
    except DatasetNotFound:
        raise HTTPException(404, "dataset not found")

    return JSONResponse(
        {
            "reservation_id": str(outcome.reservation_id),
            "status": outcome.status,
            "epsilon_reserved": float(outcome.epsilon_reserved),
            "aborted_before_commit": outcome.status == "reserved",
        },
        status_code=200,
    )
