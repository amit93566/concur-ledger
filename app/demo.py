"""Demo console: the admin's setup, experiment runs, and the access log.

None of this is on the reserve path, and none of it changes a strategy. It
drives the same endpoints the harnesses drive, so that a run started from the
console can be watched from any other window: its state lives in the database
(demo_runs), not in the browser that started it.

Who does what, as the console presents it:
    admin    sets the cap, the epsilon per query and the number of teams, then
             only watches. Starting a run is refused for the admin role.
    team-N   analyst keys. Any team may start a run; the run then sends one
             request per team, each signed with that team's own key, so the
             access log and the ledger show every team by name.
    viewer   reads.

Runs, by experiment:
    1, 2   rounds: every team runs the admin's standard question at the same
           moment, as a real OpenDP query on the Adult data (reserve, query,
           commit), until the cap is breached (naive) or a round grants nothing.

The Shared dataset: team-1 and team-2 ask their own questions of the Adult
data, from their own browsers, against one budget the admin set. OpenDP prices
each question before it is asked (/adult/price).
    3      a short cost sweep, in-process (no HTTP), over all four strategies.
    4      `work`: each team runs 3 s jobs back to back, so a restart catches
           some in flight. `abort`: each team reserves and never commits.
"""

import asyncio
import csv
import statistics
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.adapters import adult, get_adapter
from app.auth import KEYS, Principal, _lookup, require
from app.db import get_pool
from app.enforcement.base import ReserveRequest, RetriesExhausted, get_strategy
from app.enforcement.settle import commit as do_commit
from app.ledger import queries as q
from app.ledger.init_db import set_constraint
from app.models import dataset_out

router = APIRouter()

SELF = "http://127.0.0.1:8000"  # the service calls itself, as each team
ROUND_PAUSE_S = 0.8  # between rounds, so a watcher sees the budget fill
MAX_ROUNDS = 400
EXP4_JOB_MS = 3000
SWEEP = {
    "strategies": ["naive", "atomic", "for_update", "serializable"],
    "ns": [1, 8, 32],
    "seconds": 3.0,
    "duration_ms": 10,
    "cap": 900000,
    "epsilon": 0.001,
}
SAFE = ("atomic", "for_update", "serializable")

_tasks: dict[str, asyncio.Task] = {}
_stop: set[str] = set()


# --------------------------------------------------------------------------
# teams
# --------------------------------------------------------------------------


def team_keys() -> list[tuple[str, str | None]]:
    """(name, key) for every team login, in order. With RBAC off there are no
    keys; the team name then travels as the request's `actor`."""
    if not KEYS:
        return [(f"team-{i}", None) for i in range(1, 9)]

    def order(name: str) -> int:
        tail = name.split("-", 1)[1]
        return int(tail) if tail.isdigit() else 10**6

    teams = [(p.name, k) for k, p in KEYS.items() if p.name.startswith("team-") and p.role == "analyst"]
    return sorted(teams, key=lambda t: order(t[0]))


def _headers(key: str | None) -> dict[str, str]:
    return {"X-API-Key": key} if key else {}


def _stamp() -> str:
    return datetime.now().strftime("%H%M%S")


# --------------------------------------------------------------------------
# setup
# --------------------------------------------------------------------------


DEFAULT_QUERY = {"kind": "count", "filter": "income_gt_50k", "accuracy": "high"}
SHARED_TEAMS = 2  # team-1 and team-2 share the Adult budget on the Shared dataset page


class SetupBody(BaseModel):
    epsilon_cap: Decimal = Field(gt=0, le=900000)
    teams: int = Field(ge=2, le=8)  # how many teams race in Exp 1 and 2
    query: dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_QUERY))


def _setup_out(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    query = row["query"] or dict(DEFAULT_QUERY)
    return {
        "epsilon_cap": float(row["epsilon_cap"]),
        "epsilon": float(row["epsilon"]),
        "teams": row["teams"],
        "team_names": [n for n, _ in team_keys()[: row["teams"]]],
        "query": query,
        "question": adult.label(query),
        "shared_dataset_id": str(row["shared_dataset_id"]) if row["shared_dataset_id"] else None,
        "shared_teams": [n for n, _ in team_keys()[:SHARED_TEAMS]],
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"].isoformat(),
    }


async def _load_setup(conn) -> dict[str, Any] | None:
    cur = await conn.execute(
        "SELECT epsilon_cap, epsilon, teams, query, shared_dataset_id, updated_by, updated_at "
        "FROM demo_setup WHERE id = 1"
    )
    return await cur.fetchone()


@router.get("/demo/setup", dependencies=[Depends(require("viewer"))])
async def get_setup():
    async with get_pool().connection() as conn:
        row = await _load_setup(conn)
    return {"setup": _setup_out(row), "teams_available": len(team_keys())}


@router.post("/demo/setup")
async def put_setup(body: SetupBody, principal: Principal | None = Depends(require("admin"))):
    available = len(team_keys())
    if body.teams > available:
        raise HTTPException(400, f"only {available} team logins exist")
    try:
        priced = adult.price(body.query)  # epsilon comes from OpenDP, not the admin
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    async with get_pool().connection() as conn:
        old = await _load_setup(conn)
        shared = old["shared_dataset_id"] if old else None
        if shared is not None and await q.get_dataset(conn, shared) is None:
            shared = None
        # A new cap is a new budget: the two teams start again from zero.
        if shared is None or Decimal(old["epsilon_cap"]) != body.epsilon_cap:
            ds = await q.create_dataset(conn, f"adult-shared-{_stamp()}", body.epsilon_cap, Decimal(0), Decimal(0))
            shared = ds["id"]
        await conn.execute(
            "INSERT INTO demo_setup (id, epsilon_cap, epsilon, teams, query, shared_dataset_id, updated_by, updated_at) "
            "VALUES (1, %s, %s, %s, %s, %s, %s, now()) ON CONFLICT (id) DO UPDATE SET "
            "epsilon_cap = EXCLUDED.epsilon_cap, epsilon = EXCLUDED.epsilon, teams = EXCLUDED.teams, "
            "query = EXCLUDED.query, shared_dataset_id = EXCLUDED.shared_dataset_id, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()",
            (body.epsilon_cap, Decimal(str(priced["epsilon"])), body.teams, Jsonb(body.query), shared,
             principal.name if principal else None),
        )
        row = await _load_setup(conn)
    return {"setup": _setup_out(row), "teams_available": available}


# --------------------------------------------------------------------------
# the Adult dataset: what can be asked, and what it costs
# --------------------------------------------------------------------------


class PriceBody(BaseModel):
    spec: dict[str, Any]


async def record_answer(reservation_id, dataset_id, actor: str | None, spec: dict[str, Any], answer) -> None:
    try:
        async with get_pool().connection() as conn:
            await conn.execute(
                "INSERT INTO adult_answers (reservation_id, dataset_id, actor, spec, answer) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (reservation_id) DO NOTHING",
                (reservation_id, dataset_id, actor, Jsonb(spec), answer),
            )
    except Exception as exc:  # the team already has its answer; never fail the job over this
        print(f"!! could not record an Adult answer: {exc}")


@router.get("/adult/answers", dependencies=[Depends(require("admin"))])
async def adult_answers(dataset_id: UUID, limit: int = Query(100, ge=1, le=500)):
    """The data owner's view: every question on a dataset, the noisy answer the
    team received, and the true value beside it. Admin only."""
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "SELECT r.id, r.reserved_at, r.actor, r.job_id, r.status, r.epsilon_committed, a.spec, a.answer "
            "FROM spend_records r LEFT JOIN adult_answers a ON a.reservation_id = r.id "
            "WHERE r.dataset_id = %s ORDER BY r.reserved_at DESC LIMIT %s",
            (dataset_id, limit),
        )
        rows = await cur.fetchall()
    out = []
    for r in rows:
        item = {
            "id": str(r["id"]),
            "at": r["reserved_at"].isoformat(),
            "team": r["actor"],
            "question": r["job_id"],
            "status": r["status"],
            "epsilon": float(r["epsilon_committed"]) if r["epsilon_committed"] is not None else None,
            "answer": r["answer"],
            "truth": None,
            "accuracy": None,
        }
        if r["spec"] is not None:
            priced = adult.price(r["spec"])
            item["question"] = priced["question"]
            item["truth"] = await adult.truth(r["spec"])
            item["accuracy"] = priced["accuracy"]
        out.append(item)
    return out


@router.post("/adult/truth", dependencies=[Depends(require("admin"))])
async def adult_truth(body: PriceBody):
    try:
        return {"question": adult.label(body.spec), "truth": await adult.truth(body.spec)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/adult/catalog", dependencies=[Depends(require("viewer"))])
async def adult_catalog():
    data = await adult.rows()
    return {
        "dataset": "UCI Adult",
        "source": "Becker and Kohavi (1996), UCI Machine Learning Repository, doi:10.24432/C5XW20",
        "license": "CC BY 4.0",
        "schema": adult.SCHEMA,
        "rows": len(data),
        "columns": ["age", "workclass", "education", "education_num", "marital_status", "occupation",
                    "relationship", "race", "sex", "capital_gain", "capital_loss", "hours_per_week",
                    "native_country", "income"],
        "kinds": [{"key": "count", "label": "Count people"}, {"key": "sum", "label": "Sum a column"}],
        "sum_columns": [{"key": k, "label": v[0], "bounds": v[1]} for k, v in adult.SUM_COLUMNS.items()],
        "filters": [{"key": k, "label": v[0]} for k, v in adult.FILTERS.items()],
        "levels": [{"key": k, "label": v[0]} for k, v in adult.LEVELS.items()],
    }


@router.post("/adult/price", dependencies=[Depends(require("viewer"))])
async def adult_price(body: PriceBody):
    try:
        return adult.price(body.spec)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# --------------------------------------------------------------------------
# runs
# --------------------------------------------------------------------------


class RunStart(BaseModel):
    exp: int = Field(ge=1, le=4)
    strategy: str | None = None
    mode: Literal["work", "abort"] | None = None


RUN_COLUMNS = (
    "id, exp, mode, strategy, dataset_id, started_by, status, params, progress, "
    "detail, started_at, finished_at"
)


def _run_out(row: dict[str, Any], with_progress: bool = True) -> dict[str, Any]:
    out = {
        "id": str(row["id"]),
        "exp": row["exp"],
        "mode": row["mode"],
        "strategy": row["strategy"],
        "dataset_id": str(row["dataset_id"]) if row["dataset_id"] else None,
        "started_by": row["started_by"],
        "status": row["status"],
        "params": row["params"],
        "detail": row["detail"],
        "started_at": row["started_at"].isoformat(),
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
    }
    if with_progress:
        out["progress"] = row["progress"]
    return out


async def _append(run_id: str, entry: dict[str, Any]) -> None:
    async with get_pool().connection() as conn:
        await conn.execute(
            "UPDATE demo_runs SET progress = progress || %s WHERE id = %s",
            (Jsonb([entry]), run_id),
        )


async def _finish(run_id: str, status: str, detail: str | None = None) -> None:
    async with get_pool().connection() as conn:
        await conn.execute(
            "UPDATE demo_runs SET status = %s, detail = %s, finished_at = now() "
            "WHERE id = %s AND status = 'running'",
            (status, detail, run_id),
        )


@router.get("/demo/runs", dependencies=[Depends(require("viewer"))])
async def list_runs(exp: int | None = None, limit: int = Query(8, ge=1, le=50)):
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            f"SELECT {RUN_COLUMNS} FROM demo_runs WHERE (%s::int IS NULL OR exp = %s) "
            "ORDER BY started_at DESC LIMIT %s",
            (exp, exp, limit),
        )
        rows = await cur.fetchall()
    return [_run_out(r, with_progress=False) for r in rows]


@router.get("/demo/runs/{run_id}", dependencies=[Depends(require("viewer"))])
async def get_run(run_id: UUID):
    async with get_pool().connection() as conn:
        cur = await conn.execute(f"SELECT {RUN_COLUMNS} FROM demo_runs WHERE id = %s", (run_id,))
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(404, "run not found")
        out = _run_out(row)
        out["dataset"] = None
        out["invariants"] = None
        if row["dataset_id"]:
            ds = await q.get_dataset(conn, row["dataset_id"])
            inv = await q.check_invariants(conn, row["dataset_id"])
            if ds is not None and inv is not None:
                out["dataset"] = dataset_out(ds)
                out["invariants"] = {
                    "i1_cap_safety": inv["i1_cap_safety"],
                    "i2_ledger_agreement": inv["i2_ledger_agreement"],
                    "status_counts": await q.status_counts(conn, row["dataset_id"]),
                }
    return out


@router.post("/demo/runs", status_code=201)
async def start_run(body: RunStart, principal: Principal | None = Depends(require("analyst"))):
    if principal is not None and principal.role == "admin":
        raise HTTPException(403, "the admin watches; a team starts runs")
    busy = [t for t in _tasks.values() if not t.done()]
    if busy:
        raise HTTPException(409, "another run is in progress")

    exp = body.exp
    if exp == 1:
        mode, strategy = "rounds", "naive"
    elif exp == 2:
        mode, strategy = "rounds", body.strategy or "atomic"
        if strategy not in SAFE:
            raise HTTPException(400, f"Exp 2 runs one of {', '.join(SAFE)}")
    elif exp == 3:
        mode, strategy = "sweep", "all"
    else:
        mode, strategy = body.mode or "work", "atomic"

    async with get_pool().connection() as conn:
        setup = await _load_setup(conn)
        if setup is None and exp != 3:
            raise HTTPException(409, "waiting for the admin to set up")
        if exp == 3:
            params: dict[str, Any] = dict(SWEEP)
            dataset_id = None
        else:
            teams = team_keys()[: setup["teams"]]
            query = setup["query"] or dict(DEFAULT_QUERY)
            params = {
                "epsilon_cap": float(setup["epsilon_cap"]),
                "epsilon": float(setup["epsilon"]),
                "teams": [n for n, _ in teams],
            }
            if exp in (1, 2):
                # Every team asks the admin's standard question of the Adult
                # data; OpenDP sets its price.
                params["query"] = query
                params["question"] = adult.label(query)
            if exp == 4 and mode == "work":
                params["job_ms"] = EXP4_JOB_MS
            name = f"exp{exp}-{strategy if exp in (1, 2) else mode}-{_stamp()}"
            ds = await q.create_dataset(conn, name, setup["epsilon_cap"], Decimal(0), Decimal(0))
            dataset_id = ds["id"]
            if exp in (1, 2):
                # Off, as in Experiments 1 and 2: naive must be able to breach,
                # and the safe strategy's result must not lean on the database.
                params["db_constraint"] = await set_constraint(conn, False)
        cur = await conn.execute(
            "INSERT INTO demo_runs (exp, mode, strategy, dataset_id, started_by, status, params) "
            f"VALUES (%s, %s, %s, %s, %s, 'running', %s) RETURNING {RUN_COLUMNS}",
            (exp, mode, strategy, dataset_id, principal.name if principal else None, Jsonb(params)),
        )
        row = await cur.fetchone()

    run_id = str(row["id"])
    if exp in (1, 2):
        job = _rounds(run_id, str(dataset_id), strategy, params)
    elif exp == 3:
        job = _sweep(run_id, params, principal.name if principal else None)
    elif mode == "work":
        job = _work(run_id, str(dataset_id), params)
    else:
        job = _abort(run_id, str(dataset_id), params)
    _tasks[run_id] = asyncio.create_task(_guard(run_id, job))
    return _run_out(row)


@router.post("/demo/runs/{run_id}/stop", dependencies=[Depends(require("analyst"))])
async def stop_run(run_id: UUID):
    task = _tasks.get(str(run_id))
    if task is None or task.done():
        raise HTTPException(409, "run is not in progress")
    _stop.add(str(run_id))
    return {"stopping": True}


async def _guard(run_id: str, job) -> None:
    try:
        await job
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # a failed run must not stay 'running' forever
        await _finish(run_id, "failed", f"{type(exc).__name__}: {exc}")
    finally:
        _stop.discard(run_id)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=SELF,
        timeout=60.0,
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=64),
    )


async def _warm(client: httpx.AsyncClient, n: int) -> None:
    # Opens one connection per team before the first round, so connection setup
    # does not stagger round 1.
    await asyncio.gather(*(client.get("/health") for _ in range(n)))


# --- Exp 1 / Exp 2 ---------------------------------------------------------


async def _rounds(run_id: str, ds: str, strategy: str, params: dict[str, Any]) -> None:
    teams = [t for t in team_keys() if t[0] in params["teams"]]
    async with _client() as client:
        await _warm(client, len(teams))
        for rnd in range(1, MAX_ROUNDS + 1):
            if run_id in _stop:
                return await _finish(run_id, "stopped")

            async def ask(name: str, key: str | None):
                # One whole job: reserve, a real OpenDP query on Adult, commit.
                r = await client.post(
                    f"/datasets/{ds}/run",
                    json={
                        "job_id": f"round{rnd}-{name}",
                        "idempotency_key": f"{run_id}-{rnd}-{name}",
                        "strategy": strategy,
                        "adapter": "adult",
                        "spec": params["query"],
                        "actor": name,
                    },
                    headers=_headers(key),
                )
                return name, r.status_code, r.json()

            shots = await asyncio.gather(*(ask(n, k) for n, k in teams))

            outcomes = []
            for name, status, b in shots:
                outcome = (
                    "reserved" if status == 200 else "denied" if status == 429
                    else "gave_up" if status == 503 else "error"
                )
                outcomes.append({
                    "team": name,
                    "outcome": outcome,
                    "answer": b.get("result") if status == 200 else None,
                    "received_at": b.get("received_at"),
                    "observed_spent": b.get("observed_spent"),
                    "observed_reserved": b.get("observed_reserved"),
                    "detail": None if outcome != "error" else str(b.get("detail", status)),
                })
            stamps = [o["received_at"] for o in outcomes if o["received_at"] is not None]
            async with get_pool().connection() as conn:
                row = await q.get_dataset(conn, UUID(ds))
            spent, held = float(row["epsilon_spent"]), float(row["epsilon_reserved"])
            granted = sum(o["outcome"] == "reserved" for o in outcomes)
            await _append(run_id, {
                "round": rnd,
                "granted": granted,
                "denied": sum(o["outcome"] == "denied" for o in outcomes),
                "gave_up": sum(o["outcome"] == "gave_up" for o in outcomes),
                "errors": sum(o["outcome"] == "error" for o in outcomes),
                "spent": spent,
                "held": held,
                "total": spent + held,
                "spread_ms": round((max(stamps) - min(stamps)) * 1000, 2) if len(stamps) > 1 else None,
                "teams": outcomes,
            })
            if spent + held > params["epsilon_cap"]:
                return await _finish(run_id, "breached", f"cap breached in round {rnd}")
            if granted == 0:
                return await _finish(run_id, "exhausted", f"no grants in round {rnd}")
            await asyncio.sleep(ROUND_PAUSE_S)
    await _finish(run_id, "stopped", f"stopped after {MAX_ROUNDS} rounds")


# --- Exp 4 -----------------------------------------------------------------


async def _work(run_id: str, ds: str, params: dict[str, Any]) -> None:
    teams = [t for t in team_keys() if t[0] in params["teams"]]
    spec = {"epsilon": params["epsilon"], "duration_ms": params["job_ms"]}

    async def team_loop(name: str, key: str | None, client: httpx.AsyncClient) -> dict[str, Any]:
        done = 0
        while run_id not in _stop:
            r = await client.post(
                f"/datasets/{ds}/run",
                json={
                    "job_id": f"{name}-job{done + 1}",
                    "idempotency_key": f"{run_id}-{name}-{done + 1}",
                    "strategy": "atomic",
                    "adapter": "synthetic",
                    "spec": spec,
                    "actor": name,
                },
                headers=_headers(key),
            )
            if r.status_code == 200:
                done += 1
            elif r.status_code == 429:
                return {"team": name, "committed": done, "ended": "budget used up"}
            else:
                return {"team": name, "committed": done, "ended": f"HTTP {r.status_code}"}
        return {"team": name, "committed": done, "ended": "stopped"}

    async with _client() as client:
        await _warm(client, len(teams))
        results = await asyncio.gather(*(team_loop(n, k, client) for n, k in teams))
    for entry in results:
        await _append(run_id, entry)
    await _finish(run_id, "stopped" if run_id in _stop else "exhausted")


async def _abort(run_id: str, ds: str, params: dict[str, Any]) -> None:
    teams = [t for t in team_keys() if t[0] in params["teams"]]
    async with _client() as client:

        async def reserve(name: str, key: str | None):
            r = await client.post(
                f"/datasets/{ds}/reserve",
                json={
                    "job_id": f"abort-{name}",
                    "idempotency_key": f"{run_id}-abort-{name}",
                    "epsilon_cost": params["epsilon"],
                    "strategy": "atomic",
                    "actor": name,
                },
                headers=_headers(key),
            )
            b = r.json()
            # Reserved and then nothing: the job "fails" before commit and does
            # not release, exactly as a crash in that window would leave it.
            return {"team": name, "outcome": b.get("status", f"HTTP {r.status_code}"), "reservation_id": b.get("reservation_id")}

        for entry in await asyncio.gather(*(reserve(n, k) for n, k in teams)):
            await _append(run_id, entry)
    await _finish(run_id, "done")


# --- Exp 3 -----------------------------------------------------------------


def _pct(values: list[float], p: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 2)
    return round(statistics.quantiles(values, n=100, method="inclusive")[p - 1], 2)


async def _sweep(run_id: str, params: dict[str, Any], actor: str | None) -> None:
    pool = get_pool()
    adapter = get_adapter("synthetic")
    spec = {"epsilon": params["epsilon"], "duration_ms": params["duration_ms"]}
    cost = Decimal(str(params["epsilon"]))
    loop = asyncio.get_running_loop()
    for strategy in params["strategies"]:
        async with pool.connection() as conn:
            ds = await q.create_dataset(conn, f"exp3-{strategy}-{_stamp()}", Decimal(params["cap"]), Decimal(0), Decimal(0))
        fn = get_strategy(strategy)
        for n in params["ns"]:
            if run_id in _stop:
                return await _finish(run_id, "stopped")
            stats = {"completed": 0, "denied": 0, "gave_up": 0, "errors": 0, "retries": 0}
            lat: list[float] = []
            deadline = loop.time() + params["seconds"]

            async def worker(w: int) -> None:
                i = 0
                while loop.time() < deadline:
                    i += 1
                    t = time.perf_counter()
                    req = ReserveRequest(
                        dataset_id=ds["id"],
                        job_id=f"{strategy}-n{n}-w{w}-{i}",
                        idempotency_key=f"{run_id}-{strategy}-{n}-{w}-{i}",
                        epsilon_cost=cost,
                        strategy=strategy,
                        epsilon_source="synthetic",
                        actor=actor,
                    )
                    try:
                        out = await fn(pool, req)
                    except RetriesExhausted:
                        stats["gave_up"] += 1
                        continue
                    except Exception:
                        stats["errors"] += 1
                        continue
                    stats["retries"] += out.retries
                    if out.status != "reserved":
                        stats["denied"] += 1
                        continue
                    result = await adapter.run_operation(spec)
                    await do_commit(pool, out.reservation_id, result.epsilon)
                    lat.append((time.perf_counter() - t) * 1000)
                    stats["completed"] += 1

            t0 = time.perf_counter()
            await asyncio.gather(*(worker(w) for w in range(n)))
            elapsed = time.perf_counter() - t0
            await _append(run_id, {
                "strategy": strategy,
                "n": n,
                **stats,
                "seconds": round(elapsed, 3),
                "throughput": round(stats["completed"] / elapsed, 1),
                "p50_ms": _pct(lat, 50),
                "p95_ms": _pct(lat, 95),
                "p99_ms": _pct(lat, 99),
                "dataset_id": str(ds["id"]),
            })
    await _finish(run_id, "done")


# --------------------------------------------------------------------------
# access log
# --------------------------------------------------------------------------

_access: list[tuple] = []


async def _access_middleware(request: Request, call_next):
    path = request.url.path
    # Sign-ins and every action. Page polling (other GETs) is left out, or the
    # log would be nothing but the dashboard reading itself.
    wanted = not path.startswith("/dashboard") and path != "/health" and (
        request.method != "GET" or path == "/whoami"
    )
    at = datetime.now(timezone.utc)
    t = time.perf_counter()
    response = await call_next(request)
    if wanted:
        key = request.headers.get("x-api-key")
        who = _lookup(key) if key else None
        route = request.scope.get("route")
        _access.append((
            at,
            who.name if who else ("unknown key" if key else "no key"),
            who.role if who else None,
            request.method,
            path,
            getattr(route, "path", None),
            response.status_code,
            round((time.perf_counter() - t) * 1000, 2),
        ))
    return response


async def _flush_access() -> None:
    while True:
        await asyncio.sleep(1.0)
        if not _access:
            continue
        batch = _access[:]
        del _access[: len(batch)]
        try:
            async with get_pool().connection() as conn:
                async with conn.cursor() as cur:
                    await cur.executemany(
                        "INSERT INTO access_log (at, actor, role, method, path, route, status, ms) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        batch,
                    )
        except Exception as exc:
            print(f"!! access log flush failed: {exc}")


def _own(principal: Principal | None) -> str | None:
    """A team login is limited to its own rows; admin and viewer see everyone."""
    return principal.name if principal is not None and principal.role == "analyst" else None


@router.get("/access-log")
async def access_log(
    limit: int = Query(300, ge=1, le=2000),
    actor: str | None = None,
    principal: Principal | None = Depends(require("viewer")),
):
    actor = _own(principal) or actor
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "SELECT seq, at, actor, role, method, path, route, status, ms FROM access_log "
            "WHERE (%s::text IS NULL OR actor = %s) ORDER BY seq DESC LIMIT %s",
            (actor, actor, limit),
        )
        rows = await cur.fetchall()
        cur = await conn.execute(
            "SELECT actor, role, count(*) AS calls, max(at) AS last_at, "
            "count(*) FILTER (WHERE status IN (401, 403)) AS blocked "
            "FROM access_log WHERE (%s::text IS NULL OR actor = %s) "
            "GROUP BY actor, role ORDER BY max(at) DESC",
            (actor, actor),
        )
        parties = await cur.fetchall()
    return {
        "enabled": bool(KEYS),
        "parties": [
            {**p, "last_at": p["last_at"].isoformat()} for p in parties
        ],
        "rows": [{**r, "at": r["at"].isoformat()} for r in rows],
    }


# --------------------------------------------------------------------------
# what each team has received
# --------------------------------------------------------------------------


@router.get("/demo/usage")
async def usage(
    team: str | None = None,
    principal: Principal | None = Depends(require("viewer")),
):
    """Per team and dataset: jobs granted, denied and held, and the epsilon
    spent and held. A team login gets its own rows only."""
    team = _own(principal) or team
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            """
            SELECT r.actor AS team, d.id AS dataset_id, d.name AS dataset, d.epsilon_cap,
                   dr.exp, dr.strategy, dr.mode,
                   count(*) FILTER (WHERE r.status = 'committed') AS granted,
                   count(*) FILTER (WHERE r.status = 'reserved') AS held_jobs,
                   count(*) FILTER (WHERE r.status = 'denied') AS denied,
                   count(*) FILTER (WHERE r.status = 'released') AS released,
                   COALESCE(sum(r.epsilon_committed) FILTER (WHERE r.status = 'committed'), 0) AS eps_spent,
                   COALESCE(sum(r.epsilon_reserved) FILTER (WHERE r.status = 'reserved'), 0) AS eps_held,
                   max(r.reserved_at) AS last_at
              FROM spend_records r
              JOIN datasets d ON d.id = r.dataset_id
              LEFT JOIN demo_runs dr ON dr.dataset_id = d.id
             WHERE r.actor IS NOT NULL AND (%s::text IS NULL OR r.actor = %s)
             GROUP BY r.actor, d.id, d.name, d.epsilon_cap, dr.exp, dr.strategy, dr.mode
             ORDER BY max(r.reserved_at) DESC
             LIMIT 200
            """,
            (team, team),
        )
        rows = await cur.fetchall()
    return [
        {
            **r,
            "dataset_id": str(r["dataset_id"]),
            "epsilon_cap": float(r["epsilon_cap"]),
            "eps_spent": float(r["eps_spent"]),
            "eps_held": float(r["eps_held"]),
            "last_at": r["last_at"].isoformat(),
        }
        for r in rows
    ]


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------


def install(app: FastAPI) -> None:
    app.include_router(router)
    # Only with RBAC on: the harness experiments run with it off, and must not
    # pay for a middleware they were never measured with.
    if KEYS:
        app.middleware("http")(_access_middleware)


ADULT_FILE = Path(__file__).resolve().parents[1] / "data" / "adult.data"
ADULT_COLUMNS = (
    "age, workclass, fnlwgt, education, education_num, marital_status, occupation, "
    "relationship, race, sex, capital_gain, capital_loss, hours_per_week, native_country, income"
)
INT_FIELDS = {0, 2, 4, 10, 11, 12}


async def _load_adult() -> None:
    """Loads data/adult.data into the adult table once, if it is empty."""
    async with get_pool().connection() as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM adult")
        if (await cur.fetchone())["n"] > 0 or not ADULT_FILE.exists():
            return
        with open(ADULT_FILE, newline="") as fh:
            rows = [
                [None if c == "?" else int(c) if i in INT_FIELDS else c for i, c in enumerate(f.strip() for f in r)]
                for r in csv.reader(fh) if r
            ]
        async with conn.cursor() as c:
            async with c.copy(f"COPY adult ({ADULT_COLUMNS}) FROM STDIN") as copy:
                for row in rows:
                    await copy.write_row(row)
        print(f"loaded {len(rows)} Adult rows")


async def startup() -> list[asyncio.Task]:
    await _load_adult()
    await adult.rows()  # read once now, so the first question is not the slow one
    async with get_pool().connection() as conn:
        # Whatever was running died with the previous process.
        await conn.execute(
            "UPDATE demo_runs SET status = 'interrupted', finished_at = now(), "
            "detail = 'the service stopped while this run was in progress' "
            "WHERE status = 'running'"
        )
    return [asyncio.create_task(_flush_access())] if KEYS else []
