"""The real-epsilon path: an actual DP query priced, reserved, run and committed.

Experiments 1 and 2 drive `/reserve` directly with a fixed cost, because a
deterministic breach needs a deterministic price. This script exists to show
that the price is not an invention of the harness: OpenDP is asked what a noisy
count costs, and that exact number is what the ledger reserves, commits and
enforces.

The near-cap scenario and a real epsilon source are compatible precisely because
epsilon is deterministic given the query spec -- a noisy count at Laplace scale
0.5 costs eps = 1/0.5 = 2.0 every time, which is why the cap-10 / spent-6 seed
admits exactly two of them.
"""

import argparse
import asyncio

import httpx

SPEC = {"kind": "count", "scale": 0.5, "n_rows": 1000}


async def main_async(args):
    async with httpx.AsyncClient(base_url=args.base_url, timeout=60.0) as client:
        adapters = (await client.get("/adapters")).json()
        opendp = next(a for a in adapters if a["name"] == "opendp")
        if not opendp["available"]:
            print(f"OpenDP unavailable: {opendp['detail']}")
            print("The enforcement path is identical with --adapter passthrough,")
            print("which is the documented fallback; the epsilon source is recorded")
            print("per record either way.")
            return 1
        print(f"adapter: opendp  ({opendp['detail']})  stub={opendp['is_stub']}")

        priced = (
            await client.post("/adapters/epsilon", json={"adapter": "opendp", "spec": SPEC})
        ).json()
        cost = priced["epsilon"]
        print(f"\nquery spec  : {SPEC}")
        print(f"OpenDP price: eps = {cost}  (deterministic given the spec)")

        ds = (
            await client.post(
                "/datasets", json={"name": "opendp-demo", "epsilon_cap": args.cap}
            )
        ).json()
        await client.post(
            f"/datasets/{ds['id']}/seed",
            json={"epsilon_spent": args.seed_spent, "epsilon_reserved": 0},
        )
        headroom = args.cap - args.seed_spent
        print(
            f"\nledger at rest: cap={args.cap}  spent={args.seed_spent}  "
            f"free={headroom}  -> room for {int(headroom // cost)} queries at "
            f"eps={cost}\n"
        )

        for i in range(1, args.queries + 1):
            resp = await client.post(
                f"/datasets/{ds['id']}/run",
                json={
                    "job_id": f"dp-{i}",
                    "idempotency_key": f"dp-key-{i}",
                    "strategy": "atomic",
                    "adapter": "opendp",
                    "spec": SPEC,
                },
            )
            body = resp.json()
            if resp.status_code == 200:
                d = body["dataset"]
                print(
                    f"query {i}: COMMITTED  noisy_count={body['result']}  "
                    f"eps charged={body['epsilon_committed']} "
                    f"(source={body['epsilon_source']})  "
                    f"-> spent={d['epsilon_spent']}/{d['epsilon_cap']} "
                    f"free={d['free']}"
                )
            else:
                print(
                    f"query {i}: DENIED (HTTP {resp.status_code})  "
                    f"eps={body['epsilon_cost']} would exceed the cap"
                )

        final = (await client.get(f"/datasets/{ds['id']}/invariants")).json()
        print(
            f"\nfinal: spent={final['epsilon_spent']} "
            f"reserved={final['epsilon_reserved']} cap={final['epsilon_cap']}  "
            f"all invariants hold: {final['all_hold']}"
        )
        print(
            "\nThe cost enforced is the cost OpenDP reported. The system metered "
            "and enforced\nthe budget; it never computed privacy."
        )
        return 0


def parse_args():
    p = argparse.ArgumentParser(description="real OpenDP epsilon, end to end")
    p.add_argument("--cap", type=float, default=10.0)
    p.add_argument("--seed-spent", dest="seed_spent", type=float, default=6.0)
    p.add_argument("--queries", type=int, default=3)
    p.add_argument("--base-url", dest="base_url", default="http://localhost:8000")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(parse_args())))
