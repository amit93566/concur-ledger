"""Experiment 4 -- survive the failure.

Verifies the two-phase protocol under fault injection:

  I1  cap safety survives a crash in the reserve->commit gap
  I2  no drift between the running columns and the record rows
  I3  a settled reservation never changes again (release-after-commit rejected)
  I4  a retried commit charges exactly once

Two fault modes, and the write-up must state which was used:

  --mode controlled-abort   The service reserves and then deliberately fails
                            before commit, without releasing. Leaves exactly the
                            state a crash in that window leaves. Reproducible on
                            every attempt.
  --mode process-kill       The service hard-exits (os._exit) inside the same
                            window and Docker restarts it. More faithful to a
                            real crash, slower, and it depends on the restart
                            policy in docker-compose.yml.

The controlled abort tests the same protocol property as the kill. It is the
primary mode here for exactly that reason; the kill is run as corroboration.
"""

import argparse
import asyncio
import csv
import sys
import uuid
from pathlib import Path

import httpx

PASS = "PASS"
FAIL = "FAIL"


class Checks:
    def __init__(self, mode=""):
        self.results = []
        self.mode = mode

    def record(self, name, ok, detail="", invariant=""):
        self.results.append((name, ok, detail, invariant))
        print(f"  [{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))
        return ok

    def write_csv(self, path):
        """Machine-readable output so Experiment 4 can appear in the report
        alongside Experiments 1 and 2, instead of living only in scrollback."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["mode", "check", "invariant", "passed", "detail"])
            for name, ok, detail, inv in self.results:
                w.writerow([self.mode, name, inv, ok, detail])

    def report(self):
        failed = [r for r in self.results if not r[1]]  # noqa: E501
        print("\n" + "=" * 68)
        print(f"{len(self.results) - len(failed)}/{len(self.results)} checks passed")
        for name, _, detail, _inv in failed:
            print(f"  FAILED: {name} -- {detail}")
        print("=" * 68)
        return not failed


async def fresh_dataset(client, cap, spent):
    ds = (
        await client.post(
            "/datasets", json={"name": f"crash-{uuid.uuid4().hex[:6]}", "epsilon_cap": cap}
        )
    ).json()
    await client.post(
        f"/datasets/{ds['id']}/seed",
        json={"epsilon_spent": spent, "epsilon_reserved": 0, "delete_records": True},
    )
    return ds["id"]


async def inv(client, dataset_id):
    return (await client.get(f"/datasets/{dataset_id}/invariants")).json()


# ---------------------------------------------------------------------------
# fault mode: controlled abort
# ---------------------------------------------------------------------------


async def controlled_abort(client, args, checks):
    print(f"\n--- controlled abort x{args.runs} (reserve, then fail before commit) ---")
    dataset_id = await fresh_dataset(client, args.cap, 0.0)

    orphaned = []
    for i in range(args.runs):
        r = await client.post(
            f"/debug/reserve-and-abort/{dataset_id}",
            json={
                "job_id": f"abort-{i}",
                "idempotency_key": f"abort-key-{i}",
                "epsilon_cost": args.cost,
                "strategy": args.strategy,
            },
        )
        body = r.json()
        if body["status"] == "reserved":
            orphaned.append(body["reservation_id"])

    after = await inv(client, dataset_id)
    checks.record(
        "I1 holds after aborts",
        after["i1_cap_safety"],
        f"total={after['total']} cap={after['epsilon_cap']}", invariant="I1")
    checks.record(
        "I2 no drift after aborts",
        after["i2_ledger_agreement"],
        f"reserved={after['epsilon_reserved']} records={after['records_reserved']}", invariant="I2")
    checks.record(
        "budget is HELD, not lost or double-spent",
        abs(after["epsilon_reserved"] - len(orphaned) * args.cost) < 1e-9,
        f"{len(orphaned)} orphaned holds = {after['epsilon_reserved']}", invariant="I1")
    checks.record(
        "every reservation is in a valid state",
        set(after["status_counts"]) <= {"reserved", "committed", "released", "denied"},
        str(after["status_counts"]), invariant="I3")

    # Recovery: an operator (or a reaper) releases the orphaned holds.
    for rid in orphaned:
        await client.post(f"/reservations/{rid}/release")
    recovered = await inv(client, dataset_id)
    checks.record(
        "holds are fully recoverable by release",
        recovered["epsilon_reserved"] == 0 and recovered["epsilon_spent"] == 0,
        f"spent={recovered['epsilon_spent']} reserved={recovered['epsilon_reserved']}", invariant="I2")
    checks.record("I2 holds after recovery", recovered["i2_ledger_agreement"], invariant="I2")


# ---------------------------------------------------------------------------
# fault mode: process kill
# ---------------------------------------------------------------------------


async def wait_for_service(client, timeout=90.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            if (await client.get("/health", timeout=2.0)).status_code == 200:
                return True
        except Exception:
            pass
        await asyncio.sleep(1.0)
    return False


async def process_kill(client, args, checks):
    print(f"\n--- process kill x{args.runs} (os._exit inside the reserve->commit gap) ---")
    dataset_id = await fresh_dataset(client, args.cap, 0.0)

    # Identify the process instance, not its PID: under Docker the service is
    # PID 1 both before and after a restart, so a PID comparison would report a
    # failure even though the kill and restart both worked.
    before = (await client.get("/config")).json()
    killed = 0
    for i in range(args.runs):
        try:
            await client.post(
                f"/datasets/{dataset_id}/run",
                json={
                    "job_id": f"kill-{i}",
                    "idempotency_key": f"kill-key-{i}",
                    "strategy": args.strategy,
                    "adapter": "passthrough",
                    "spec": {"epsilon": args.cost},
                    "fault": "crash_before_commit",
                },
                timeout=10.0,
            )
        except Exception:
            killed += 1  # the connection dying is the expected outcome
        if not await wait_for_service(client):
            print("service did not come back; is `restart: unless-stopped` set?")
            return checks.record("service restarted after kill", False)

    after_cfg = (await client.get("/config")).json()
    checks.record(
        "process really died and restarted",
        killed == args.runs
        and after_cfg["instance_id"] != before["instance_id"]
        and after_cfg["uptime_s"] < before["uptime_s"] + 60,
        f"{killed}/{args.runs} kills, instance "
        f"{before['instance_id'][:8]} -> {after_cfg['instance_id'][:8]}, "
        f"uptime now {after_cfg['uptime_s']}s", invariant="—")

    after = await inv(client, dataset_id)
    checks.record("I1 holds after restart", after["i1_cap_safety"], invariant="I1")
    checks.record(
        "I2 shows no drift after restart",
        after["i2_ledger_agreement"],
        f"reserved={after['epsilon_reserved']} records={after['records_reserved']}", invariant="I2")
    checks.record(
        "reservations survived the crash as holds",
        after["status_counts"].get("reserved", 0) == killed,
        str(after["status_counts"]), invariant="I3")
    checks.record(
        "nothing was committed by a crashed job",
        after["epsilon_spent"] == 0,
        f"spent={after['epsilon_spent']}", invariant="I1")


# ---------------------------------------------------------------------------
# idempotency and terminal-state checks (I3, I4)
# ---------------------------------------------------------------------------


async def idempotency_checks(client, args, checks):
    print("\n--- I4: a retried commit charges exactly once ---")
    dataset_id = await fresh_dataset(client, args.cap, 0.0)

    key = f"idem-{uuid.uuid4().hex[:8]}"
    body = {
        "job_id": "j-idem",
        "idempotency_key": key,
        "epsilon_cost": args.cost,
        "strategy": args.strategy,
    }
    first = (await client.post(f"/datasets/{dataset_id}/reserve", json=body)).json()
    rid = first["reservation_id"]

    # A retried RESERVE with the same key must not create a second hold.
    second = (await client.post(f"/datasets/{dataset_id}/reserve", json=body)).json()
    state = await inv(client, dataset_id)
    checks.record(
        "retried reserve returns the same reservation",
        second["reservation_id"] == rid and second.get("replayed") is True, invariant="I4")
    checks.record(
        "retried reserve does not double-hold",
        state["epsilon_reserved"] == args.cost,
        f"reserved={state['epsilon_reserved']} expected={args.cost}", invariant="I4")

    c1 = (
        await client.post(f"/reservations/{rid}/commit", json={"actual_cost": args.cost})
    ).json()
    c2 = (
        await client.post(f"/reservations/{rid}/commit", json={"actual_cost": args.cost})
    ).json()
    state = await inv(client, dataset_id)
    checks.record("first commit succeeds", c1["status"] == "committed", invariant="I4")
    checks.record(
        "second commit is a replay, not a charge",
        c2["already_settled"] is True and c2["status"] == "committed", invariant="I4")
    checks.record(
        "I4 charged exactly once",
        state["epsilon_spent"] == args.cost,
        f"spent={state['epsilon_spent']} expected={args.cost}", invariant="I4")
    checks.record("I2 holds after retried commit", state["i2_ledger_agreement"], invariant="I2")

    print("\n--- I3: a settled reservation never changes again ---")
    rel = await client.post(f"/reservations/{rid}/release")
    rel_body = rel.json()
    post = await inv(client, dataset_id)
    checks.record(
        "release of a committed reservation is rejected",
        rel_body["status"] == "committed" and rel_body["already_settled"] is True,
        f"returned status={rel_body['status']}", invariant="I3")
    checks.record(
        "the ledger is unchanged by the rejected release",
        post["epsilon_spent"] == args.cost and post["epsilon_reserved"] == 0,
        f"spent={post['epsilon_spent']} reserved={post['epsilon_reserved']}", invariant="I3")

    print("\n--- over-commit is rejected ---")
    key2 = f"over-{uuid.uuid4().hex[:8]}"
    r2 = (
        await client.post(
            f"/datasets/{dataset_id}/reserve",
            json={
                "job_id": "j-over",
                "idempotency_key": key2,
                "epsilon_cost": args.cost,
                "strategy": args.strategy,
            },
        )
    ).json()
    over = await client.post(
        f"/reservations/{r2['reservation_id']}/commit",
        json={"actual_cost": args.cost * 10},
    )
    checks.record(
        "committing more than was held is rejected",
        over.status_code == 400,
        f"HTTP {over.status_code}", invariant="I3")
    await client.post(f"/reservations/{r2['reservation_id']}/release")


async def main_async(args):
    async with httpx.AsyncClient(base_url=args.base_url, timeout=60.0) as client:
        # Run with the CHECK dropped: the protocol, not the database, must be
        # what keeps the ledger consistent through a crash.
        await client.post("/admin/db-constraint", json={"enabled": False})

        checks = Checks(mode=args.mode)
        if args.mode in ("controlled-abort", "all"):
            await controlled_abort(client, args, checks)
        if args.mode in ("process-kill", "all"):
            await process_kill(client, args, checks)
        await idempotency_checks(client, args, checks)

        print(f"\nfault-injection method used: {args.mode}")
        if args.out:
            checks.write_csv(Path(args.out))
            print(f"wrote {args.out}")
        ok = checks.report()
        print(
            "Empirically observed under the tested conditions above; not a formal proof."
        )
        return 0 if ok else 1


def parse_args():
    p = argparse.ArgumentParser(description="Experiment 4 -- crash / fault injection")
    p.add_argument(
        "--mode",
        default="controlled-abort",
        choices=["controlled-abort", "process-kill", "all"],
    )
    p.add_argument("--runs", type=int, default=30)
    p.add_argument("--cap", type=float, default=100.0)
    p.add_argument("--cost", type=float, default=1.0)
    p.add_argument("--strategy", default="atomic", choices=["naive", "atomic"])
    p.add_argument("--base-url", dest="base_url", default="http://localhost:8000")
    p.add_argument("--out", default=None, help="write per-check results CSV here")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async(parse_args())))
