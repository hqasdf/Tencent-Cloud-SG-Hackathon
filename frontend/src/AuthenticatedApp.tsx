import { useEffect, useState } from "react";
import App from "./App";
import { backendCapabilities, loginDemo } from "./services/authService";
import type { BackendCapabilities } from "./services/authService";
import { clearApiSession, hasApiSession } from "./services/apiSession";
export default function AuthenticatedApp() {
  const [capabilities, setCapabilities] = useState<BackendCapabilities | null>(null);
  const [signedIn, setSignedIn] = useState(hasApiSession());
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState(0);
  useEffect(() => {
    let cancelled = false;
    void backendCapabilities().then(value => { if (!cancelled) { setCapabilities(value); setError(null); } })
      .catch(() => { if (!cancelled) setError("The backend is unavailable. Try again."); });
    return () => { cancelled = true; };
  }, [check]);
  useEffect(() => {
    const expired = () => { setSignedIn(false); setPassword(""); };
    window.addEventListener("ryde-auth-expired", expired);
    return () => window.removeEventListener("ryde-auth-expired", expired);
  }, []);
  if (!capabilities) return <div className="app-state"><h1>RydeResolve</h1><p>{error ?? "Connecting to the case workspaceâ€¦"}</p>{error && <button onClick={() => setCheck(value => value + 1)}>Try again</button>}</div>;
  if (capabilities.authenticationRequired && !signedIn) return <div className="app-state"><h1>RydeResolve demo sign-in</h1><p>Access is restricted to authorized demo reviewers.</p><form onSubmit={event => {
    event.preventDefault(); setBusy(true); setError(null);
    void loginDemo(password).then(() => { setPassword(""); setSignedIn(true); })
      .catch((failure: unknown) => { setPassword(""); setError(failure instanceof Error ? failure.message : "Sign-in failed."); })
      .finally(() => setBusy(false));
  }}><label htmlFor="demo-password">Demo password</label><input id="demo-password" type="password" autoComplete="current-password" maxLength={256} required value={password} onChange={event => setPassword(event.target.value)} /><button disabled={busy}>{busy ? "Signing inâ€¦" : "Sign in"}</button></form>{error && <p role="alert">{error}</p>}</div>;
  return <><App cloudDemo={capabilities.authenticationRequired === true && capabilities.liveAIEnabled === false} />{capabilities.authenticationRequired && <button className="demo-signout" onClick={clearApiSession}>Sign out</button>}</>;
}
