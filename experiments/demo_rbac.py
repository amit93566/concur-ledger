"""DEMO -- the same requests with different API keys.

Needs the service running with the keys below (`make demo-rbac` does that). The
last check shows why `actor` comes from the key: the request claims to be
"someone-else", and the ledger records the key's owner.

    python experiments/demo_rbac.py
"""

import sys
import uuid

import httpx

BASE = "http://localhost:8000"
ADMIN = {"X-API-Key": "demo-admin-key"}
ANALYST = {"X-API-Key": "demo-analyst-key"}
VIEWER = {"X-API-Key": "demo-viewer-key"}


def main():
    failures = 0

    def check(label, response, expected):
        nonlocal failures
        ok = response.status_code == expected
        failures += not ok
        print(f"{'ok ' if ok else 'BAD'} {label:<42} -> {response.status_code} "
              f"(expected {expected})")
        return response

    with httpx.Client(base_url=BASE) as c:
        cap = {"name": "demo-rbac", "epsilon_cap": 10}
        check("no key lists datasets", c.get("/datasets"), 401)
        check("unknown key lists datasets",
              c.get("/datasets", headers={"X-API-Key": "wrong"}), 401)
        check("analyst sets a cap", c.post("/datasets", json=cap, headers=ANALYST), 403)
        ds = check("admin sets a cap",
                   c.post("/datasets", json=cap, headers=ADMIN), 201).json()["id"]

        body = {"job_id": "demo", "idempotency_key": uuid.uuid4().hex,
                "epsilon_cost": 1, "strategy": "atomic", "epsilon_source": "demo",
                "actor": "someone-else"}
        path = f"/datasets/{ds}/reserve"
        check("viewer reads the dataset", c.get(f"/datasets/{ds}", headers=VIEWER), 200)
        check("viewer reserves", c.post(path, json=body, headers=VIEWER), 403)
        check("analyst reserves", c.post(path, json=body, headers=ANALYST), 200)

        rows = c.get(f"/datasets/{ds}/records", headers=VIEWER).json()
        actor = rows[0]["actor"] if rows else None
        print(f"\nrequest claimed actor 'someone-else'; ledger recorded {actor!r}")
        failures += actor != "demo-analyst"

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
