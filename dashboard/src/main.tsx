import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ApiError, Me, api, hasKey, setKey } from "./api";
import { AuditPage, BurnDownPage, HoldsPage } from "./pages";
import { ResultsPage } from "./results";
import "./style.css";

const TABS = [
  ["burn", "Burn-down"],
  ["holds", "Stale holds"],
  ["audit", "Audit log"],
  ["results", "Results"],
] as const;
type Tab = (typeof TABS)[number][0];

function SignIn({ onDone, message }: { onDone: () => void; message: string | null }) {
  const [value, setValue] = useState("");
  return (
    <main className="signin">
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
    </main>
  );
}

function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [signIn, setSignIn] = useState<string | null | false>(false);
  const [tab, setTab] = useState<Tab>("burn");
  const [attempt, setAttempt] = useState(0);
  const [down, setDown] = useState<string | null>(null);

  useEffect(() => {
    api<Me>("/whoami")
      .then((m) => {
        setMe(m);
        setSignIn(false);
        setDown(null);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) {
          setMe(null);
          // Signing out also lands here, with no key to complain about.
          setSignIn(hasKey() ? "That key was not recognised." : null);
        } else {
          setDown("The service is not responding. Check that it is running, then reload.");
        }
      });
  }, [attempt]);

  if (down) return <main className="signin"><h1>concur-ledger Console</h1><p className="err" role="alert">{down}</p></main>;
  if (signIn !== false) return <SignIn message={signIn} onDone={() => setAttempt((a) => a + 1)} />;
  if (!me) return null;

  return (
    <>
      <header className="top">
        <div className="brand">concur-ledger</div>
        <nav aria-label="Pages">
          {TABS.map(([id, label]) => (
            <button key={id} className={tab === id ? "on" : ""} aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </nav>
        <div className="who">
          {me.rbac_enabled ? (
            <>
              {me.name} <span className="pill">{me.role}</span>
              <button className="link" onClick={() => { setKey(""); setAttempt((a) => a + 1); }}>Sign out</button>
            </>
          ) : (
            <span className="pill warn" title="API_KEYS is not set, so every action is open.">RBAC off</span>
          )}
        </div>
      </header>
      <main className="page">
        {tab === "burn" && <BurnDownPage />}
        {tab === "holds" && <HoldsPage me={me} />}
        {tab === "audit" && <AuditPage me={me} />}
        {tab === "results" && <ResultsPage />}
      </main>
    </>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
