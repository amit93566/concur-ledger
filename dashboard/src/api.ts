import { useEffect, useRef, useState } from "react";

export type Role = "viewer" | "analyst" | "admin";
export type Me = { rbac_enabled: boolean; name: string | null; role: Role };
export type Dataset = {
  id: string;
  name: string;
  epsilon_cap: number;
  epsilon_spent: number;
  epsilon_reserved: number;
  total: number;
  free: number;
};
export type Invariants = {
  i1_cap_safety: boolean;
  i2_ledger_agreement: boolean;
  status_counts: Record<string, number>;
};
export type LedgerRecord = {
  id: string;
  job_id: string;
  epsilon_reserved: number;
  epsilon_committed: number | null;
  status: string;
  strategy: string;
  actor: string | null;
  reserved_at: string;
};
export type Hold = {
  id: string;
  dataset_name: string;
  job_id: string;
  epsilon: number;
  strategy: string;
  actor: string | null;
  age_s: number;
};
export type AuditRow = {
  seq: number;
  at: string;
  record_id: string;
  op: string;
  status: string | null;
  strategy: string | null;
  actor: string | null;
  resolved_by: string | null;
  epsilon: string | null;
  chain_pos: number | null;
  hash: string | null;
};
export type Verify = {
  valid: boolean;
  checked: number;
  broken_at: number | null;
  seq: number | null;
  reason: string | null;
  head: string;
  unsealed: number | null;
};

const STORE = "concur-ledger-key";
let key = "";
try {
  key = sessionStorage.getItem(STORE) ?? "";
} catch {
  // Storage can be blocked; the key then lasts until the page closes.
}

export function setKey(value: string) {
  key = value;
  try {
    if (value) sessionStorage.setItem(STORE, value);
    else sessionStorage.removeItem(STORE);
  } catch {
    // Same as above.
  }
}

export const hasKey = () => key !== "";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (key) headers["X-API-Key"] = key;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().then((j) => j.detail).catch(() => res.statusText);
    throw new ApiError(res.status, String(detail));
  }
  return res.json();
}

/**
 * Like `api`, but returns the status and body instead of throwing on a non-2xx.
 * The live demo needs this because a denial (429) is a correct outcome whose
 * body it shows, not a failure.
 */
export async function call<T>(path: string, method = "GET", body?: unknown): Promise<{ status: number; body: T }> {
  const headers: Record<string, string> = {};
  if (key) headers["X-API-Key"] = key;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const parsed = await res.json().catch(() => ({ detail: res.statusText }));
  return { status: res.status, body: parsed as T };
}

/** Polls `path`, keeps the last good value, and skips a tick while one is in flight. A null path pauses it. */
export function usePoll<T>(path: string | null, ms: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const busy = useRef(false);
  const lastPath = useRef<string | null | undefined>(undefined);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    // A different path is a different thing: drop the old value. A refresh of
    // the same path keeps it, so the page does not flicker.
    if (lastPath.current !== path) {
      lastPath.current = path;
      setData(null);
    }
    if (path === null) return;
    const run = async () => {
      if (busy.current) return;
      busy.current = true;
      try {
        const value = await api<T>(path);
        if (live) {
          setData(value);
          setError(null);
        }
      } catch (e) {
        if (live) setError(e instanceof ApiError ? e.message : "The service is not responding.");
      } finally {
        busy.current = false;
      }
    };
    run();
    const id = setInterval(run, ms);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [path, ms, tick]);

  return { data, error, refresh: () => setTick((t) => t + 1) };
}

export type Setup = {
  epsilon_cap: number;
  epsilon: number;
  teams: number;
  team_names: string[];
  updated_by: string | null;
  updated_at: string;
};
export type SetupState = { setup: Setup | null; teams_available: number };
export type TeamShot = {
  team: string;
  outcome: string;
  received_at: number | null;
  observed_spent: number | null;
  observed_reserved: number | null;
  detail: string | null;
};
export type Round = {
  round: number;
  granted: number;
  denied: number;
  gave_up: number;
  errors: number;
  spent: number;
  held: number;
  total: number;
  spread_ms: number | null;
  teams: TeamShot[];
};
export type SweepCell = {
  strategy: string;
  n: number;
  completed: number;
  denied: number;
  gave_up: number;
  errors: number;
  retries: number;
  seconds: number;
  throughput: number;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
};
export type Run = {
  id: string;
  exp: number;
  mode: string;
  strategy: string;
  dataset_id: string | null;
  started_by: string | null;
  status: string;
  params: Record<string, unknown> & { epsilon_cap?: number; epsilon?: number; teams?: string[] };
  detail: string | null;
  started_at: string;
  finished_at: string | null;
};
export type RunDetail = Run & {
  progress: unknown[];
  dataset: Dataset | null;
  invariants: Invariants | null;
};
export type AccessRow = {
  seq: number;
  at: string;
  actor: string | null;
  role: string | null;
  method: string;
  path: string;
  route: string | null;
  status: number;
  ms: number | null;
};
export type Party = { actor: string | null; role: string | null; calls: number; blocked: number; last_at: string };

export type Usage = {
  team: string;
  dataset_id: string;
  dataset: string;
  epsilon_cap: number;
  exp: number | null;
  strategy: string | null;
  mode: string | null;
  granted: number;
  held_jobs: number;
  denied: number;
  released: number;
  eps_spent: number;
  eps_held: number;
  last_at: string;
};

export const isTeam = (me: Me) => me.rbac_enabled && me.role === "analyst";

export const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
export const clock = (iso: string) => new Date(iso).toLocaleTimeString();

export const fmt = (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 3 });

export function age(seconds: number) {
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  if (seconds < 129600) return `${(seconds / 3600).toFixed(1)} h`;
  return `${(seconds / 86400).toFixed(1)} d`;
}
