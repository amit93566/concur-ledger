import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ApiError, Me, SetupState, api, hasKey, isTeam, setKey, usePoll } from "./api";
import { AuditPage } from "./audit";
import { Chevron } from "./common";
import { BurnDownPage } from "./burndown";
import { CrashPage, Outage } from "./crash";
import { ResultsPage } from "./results";
import { RoundsPage } from "./rounds";
import { SetupPage } from "./setup";
import { SharedPage } from "./shared";
import { SweepPage } from "./sweep";
import { MyTeamPage } from "./team";
import "@fontsource-variable/inter";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "@fontsource/jetbrains-mono/600.css";
import "./style.css";

type Page = "shared" | "exp1" | "exp2" | "exp3" | "exp4" | "team" | "audit" | "results" | "burn" | "setup";

const EXPERIMENTS: [Page, string][] = [
  ["exp1", "Exp 1 · Naive"],
  ["exp2", "Exp 2 · Atomic"],
  ["exp3", "Exp 3 · Cost"],
  ["exp4", "Exp 4 · Crash"],
];

function SignIn({ onDone, message }: { onDone: () => void; message: string | null }) {
  const [value, setValue] = useState("");
  return (
    <main className="signin">
      <div>
      <div className="brand">concur-ledger</div>
      <h1>concur-ledger Console</h1>
      <p>Enter an API key. What you can do here depends on its role.</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setKey(value.trim());
          onDone();
        }}
      >
        <label htmlFor="api-key">API key</label>
        <input id="api-key" type="password" autoComplete="off" value={value} onChange={(e) => setValue(e.target.value)} />
        <button type="submit" disabled={!value.trim()}>Sign in</button>
      </form>
      {message && <p className="err" role="alert">{message}</p>}
      </div>
    </main>
  );
}

/** Watches /health, so a restart shows as an outage and recovers on its own. */
function useOutage(): Outage {
  const [state, setState] = useState<Outage>({ down: false, since: null, last: null });
  const ref = useRef(state);
  ref.current = state;
  useEffect(() => {
    const id = setInterval(async () => {
      let up = false;
      try {
        up = (await fetch("/health", { cache: "no-store" })).ok;
      } catch {
        up = false;
      }
      const s = ref.current;
      if (!up && !s.down) setState({ down: true, since: Date.now(), last: s.last });
      if (up && s.down) setState({ down: false, since: null, last: { from: s.since ?? Date.now(), to: Date.now() } });
    }, 1500);
    return () => clearInterval(id);
  }, []);
  return state;
}

function Banner({ outage }: { outage: Outage }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);
  if (outage.down && outage.since) {
    return <div className="banner bad" role="alert">Service down · {Math.round((Date.now() - outage.since) / 1000)} s · waiting for it to come back</div>;
  }
  if (outage.last && Date.now() - outage.last.to < 15000) {
    return <div className="banner good" role="status">Service back after {Math.round((outage.last.to - outage.last.from) / 1000)} s</div>;
  }
  return null;
}

function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [signIn, setSignIn] = useState<string | null | false>(false);
  // Remembered per tab, so a reload (say, after a restart) keeps the page.
  const [page, setPageState] = useState<Page>(() => {
    try {
      return (sessionStorage.getItem("concur-ledger-page") as Page | null) ?? "shared";
    } catch {
      return "shared";
    }
  });
  const setPage = (p: Page) => {
    setPageState(p);
    try {
      sessionStorage.setItem("concur-ledger-page", p);
    } catch {
      // Then a reload starts at the Shared dataset.
    }
  };
  const [expOpen, setExpOpen] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [down, setDown] = useState(false);
  const outage = useOutage();
  const setup = usePoll<SetupState>(me ? "/demo/setup" : null, 3000);

  useEffect(() => {
    api<Me>("/whoami")
      .then((m) => {
        setMe(m);
        setSignIn(false);
        setDown(false);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) {
          setMe(null);
          // Signing out also lands here, with no key to complain about.
          setSignIn(hasKey() ? "That key was not recognised." : null);
        } else {
          setDown(true);
        }
      });
  }, [attempt]);

  // Not reachable at load: keep trying instead of asking for a reload.
  useEffect(() => {
    if (!down) return;
    const id = setTimeout(() => setAttempt((a) => a + 1), 2000);
    return () => clearTimeout(id);
  }, [down, attempt]);

  if (down && !me) return <main className="signin"><div><div className="brand">concur-ledger</div><h1>concur-ledger Console</h1><p className="err" role="alert">The service is not responding. Retrying.</p></div></main>;
  if (signIn !== false) return <SignIn message={signIn} onDone={() => setAttempt((a) => a + 1)} />;
  if (!me) return null;

  const isAdmin = !me.rbac_enabled || me.role === "admin";
  const team = isTeam(me);
  const needsSetup = setup.data !== null && setup.data.setup === null;
  // Pages each role has: a team gets its own page instead of the full logs, and
  // only the admin has Burn-down and Setup.
  const allowed = (p: Page) =>
    p === "team" ? team : p === "audit" ? !team : p === "burn" || p === "setup" ? isAdmin : true;
  // After reset-db there is no setup: the admin decides it before anything else.
  const shown: Page = isAdmin && needsSetup ? "setup" : allowed(page) ? page : "shared";
  const go = (p: Page) => () => setPage(p);
  const item = (p: Page, label: string) => (
    <button key={p} className={shown === p ? "on" : ""} aria-current={shown === p ? "page" : undefined} onClick={go(p)}>{label}</button>
  );

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">concur-ledger</div>
        <nav aria-label="Pages">
          {item("shared", "Shared dataset")}
          <button className="group" aria-expanded={expOpen} onClick={() => setExpOpen(!expOpen)}>Experiments <Chevron /></button>
          {expOpen && <div className="sub">{EXPERIMENTS.map(([p, label]) => item(p, label))}</div>}
          {team ? item("team", "My team") : item("audit", "Audit logs")}
          {item("results", "Results")}
          {isAdmin && item("burn", "Burn-down")}
          {isAdmin && item("setup", "Setup")}
        </nav>
        <div className="who">
          {me.rbac_enabled ? (
            <>
              <span>{me.name} <span className="pill">{me.role}</span></span>
              <button className="link" onClick={() => { setKey(""); setMe(null); setAttempt((a) => a + 1); }}>Sign out</button>
            </>
          ) : (
            <span className="pill warn">RBAC off</span>
          )}
        </div>
      </aside>
      <main className="page">
        <Banner outage={outage} />
        {shown === "shared" && <SharedPage me={me} setup={setup.data} />}
        {shown === "exp1" && <RoundsPage key="exp1" exp={1} me={me} setup={setup.data} />}
        {shown === "exp2" && <RoundsPage key="exp2" exp={2} me={me} setup={setup.data} />}
        {shown === "exp3" && <SweepPage me={me} />}
        {/* Kept mounted, so what it saw before an outage survives a visit elsewhere. */}
        <div hidden={shown !== "exp4"}><CrashPage me={me} setup={setup.data} outage={outage} /></div>
        {shown === "team" && <MyTeamPage me={me} />}
        {shown === "audit" && <AuditPage me={me} />}
        {shown === "results" && <ResultsPage />}
        {shown === "burn" && isAdmin && <BurnDownPage />}
        {shown === "setup" && isAdmin && <SetupPage setup={setup.data} onSaved={() => { setup.refresh(); setPage("shared"); }} />}
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
