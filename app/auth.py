"""API keys and roles.

API_KEYS holds `name:key:role` entries, comma separated. Roles are ordered, and
a higher role can do everything a lower one can:

  viewer   reads
  analyst  reserve, run, commit, release
  admin    create datasets (this is what sets a cap), seed, constraint, faults

Empty API_KEYS turns this off, which is the default: the experiment harnesses
send no key, and Experiments 1-4 were measured without this layer. `make
demo-rbac` runs the service with it on.

With it on, `actor` comes from the key rather than the request body. Otherwise
the audit log would record whatever name a caller chose to send.
"""

import hmac
from dataclasses import dataclass

from fastapi import Header, HTTPException

from app.config import settings

RANK = {"viewer": 1, "analyst": 2, "admin": 3}


@dataclass(frozen=True)
class Principal:
    name: str
    role: str


def parse_api_keys(raw: str) -> dict[str, Principal]:
    keys = {}
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        parts = entry.split(":")
        if len(parts) != 3 or parts[2] not in RANK or not all(parts):
            # Keys are secrets, so the message must not repeat the entry.
            raise ValueError("API_KEYS entries must be name:key:role")
        keys[parts[1]] = Principal(parts[0], parts[2])
    return keys


KEYS = parse_api_keys(settings.api_keys)


def _lookup(key: str) -> Principal | None:
    found = None
    # No early exit: every stored key is compared on every request.
    for stored, principal in KEYS.items():
        if hmac.compare_digest(stored.encode(), key.encode()):
            found = principal
    return found


def require(role: str):
    """A dependency that needs `role`. Returns the caller, or None when off."""

    def check(x_api_key: str | None = Header(default=None)) -> Principal | None:
        if not KEYS:
            return None
        principal = _lookup(x_api_key) if x_api_key else None
        if principal is None:
            raise HTTPException(401, "missing or unknown API key")
        if RANK[principal.role] < RANK[role]:
            raise HTTPException(403, f"needs role {role}, key has {principal.role}")
        return principal

    return check
