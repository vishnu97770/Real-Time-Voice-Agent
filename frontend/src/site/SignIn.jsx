import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth/context.js";
import { decideSignIn } from "../auth/guard.js";
import Splash from "../components/Splash.jsx";
import { useRouter } from "../router/context.js";
import Link from "../router/Link.jsx";
import Arrow from "./Arrow.jsx";
import Logo from "../components/Logo.jsx";
import Wave from "./Wave.jsx";
import StaticOrb from "./stage/StaticOrb.jsx";
import "./landing.css";
import "./SignIn.css";

// Where the site hands over to the product. There is no self-service sign-up on the server: an
// operator account is created by an administrator, so this screen is honest about that instead
// of offering a form that could not work.
export default function SignIn() {
  const { status, error: serverError, backend, signIn, retry, enterOffline } = useAuth();
  const { navigate, search } = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const justSignedIn = useRef(false); // signed in on this screen (get the curtain), as opposed to arriving already signed in

  // The only place this screen navigates from. It reacts to the auth STATUS, so it cannot run ahead
  // of the state the route guard reads: signing in changes the status first, and this follows it.
  // The same rule sends someone who is already signed in straight to the dashboard.
  const decision = decideSignIn({ status, next: search.get("next") });

  useEffect(() => {
    if (decision.kind === "redirect") navigate(decision.to, { replace: true, transition: justSignedIn.current });
  }, [decision.kind, decision.to, navigate]);

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    justSignedIn.current = true;

    try {
      await signIn(email.trim(), password);
      // Nothing more to do: the status is now "authenticated" and the effect above takes over.
      // The form stays busy until this screen is replaced.
    } catch (failure) {
      justSignedIn.current = false;
      setError(failure.message);
      setPassword("");
      setBusy(false);
    }
  };

  if (decision.kind === "redirect") return <Splash label="Signing you in" />;

  const initializing = status === "initializing";
  const open = status === "open";
  const unavailable = status === "unavailable";

  return (
    <div className="lp signin" data-stage="off">
      <Link to="/" transition className="lp-brand signin-brand">
        <Logo />
        <span>
          <b>Real-Time</b> Voice Agent
        </span>
      </Link>

      <aside className="signin-art" aria-hidden="true">
        <div className="signin-orb">
          <StaticOrb />
        </div>
        <Wave level={0.28} bars={30} className="signin-wave" />
        <p>
          <b>Voice in. Work done.</b>
          <span>Your agents are listening, and every call they take is waiting for you here.</span>
        </p>
      </aside>

      <main className="signin-panel">
        <div className="signin-card">
          <p className="lp-eyebrow">
            <span className="live-dot" aria-hidden="true" /> {open ? "Open workspace" : "Welcome back"}
          </p>

          {open ? (
            <>
              <h1>No sign-in needed</h1>
              <p className="signin-lead">
                {backend.reachable
                  ? "This server has sign-in turned off, so the workspace is open."
                  : "You chose to work offline, so the console runs on this device only."}
              </p>
              <Link to="/app/dashboard" transition className="lp-btn lp-btn--primary signin-submit">
                Open the workspace <Arrow />
              </Link>
            </>
          ) : (
            <>
              <h1>Sign in</h1>
              <p className="signin-lead">Use the account your workspace administrator created for you.</p>

              {unavailable && (
                <div className="signin-banner" role="alert">
                  <p>
                    <b>We can't reach the server.</b> {serverError}
                  </p>
                  <span>
                    <button type="button" onClick={retry}>
                      Try again
                    </button>
                    <button type="button" onClick={enterOffline}>
                      Continue offline
                    </button>
                  </span>
                </div>
              )}

              <form className="signin-form" onSubmit={submit} noValidate>
                <label htmlFor="si-email">Email</label>
                <input id="si-email" type="email" autoComplete="username" autoFocus value={email} onChange={(event) => setEmail(event.target.value)} required />

                <label htmlFor="si-password">Password</label>
                <span className="signin-password">
                  <input id="si-password" type={show ? "text" : "password"} autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required />
                  <button type="button" onClick={() => setShow(!show)} aria-pressed={show}>
                    {show ? "Hide" : "Show"}
                  </button>
                </span>

                {error && (
                  <p className="signin-error" role="alert">
                    {error}
                  </p>
                )}

                <button className="lp-btn lp-btn--primary signin-submit" type="submit" disabled={busy || initializing || !email || !password}>
                  {busy ? "Signing in…" : initializing ? "Connecting…" : "Sign in"} {!busy && !initializing && <Arrow />}
                </button>
              </form>

              <p className="signin-foot">
                New here? Ask your workspace administrator to add you. <Link to="/">Back to the site</Link>
              </p>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
