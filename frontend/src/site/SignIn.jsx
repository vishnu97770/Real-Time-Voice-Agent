import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../auth/context.js";
import { decideSignIn } from "../auth/guard.js";
import Splash from "../components/Splash.jsx";
import { useRouter } from "../router/context.js";
import Link from "../router/Link.jsx";
import Arrow from "./Arrow.jsx";
import AuthLayout from "./AuthLayout.jsx";
import { withNext } from "./withNext.js";

// --- Google Identity Services ------------------------------------------------------------------
//
// google.accounts.id.initialize() must run ONCE per page load, however often this screen mounts,
// re-renders or is revisited (Google logs a warning, and behaves oddly, otherwise). So the two
// pieces of state that outlive any one render live here, at module level:
//   googleInitialized     initialize() has been called
//   googleSignInCallback  whoever should receive the credential right now (null when the screen is
//                         not mounted); initialize()'s callback forwards to it
// This file is the only place in the app that initializes Google.
let googleInitialized = false;
let googleSignInCallback = null;

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID;

const GOOGLE_MIN_WIDTH = 200; // the range Google's button accepts
const GOOGLE_MAX_WIDTH = 400;
const GOOGLE_LOAD_ATTEMPTS = 50; // x100 ms: how long to wait for Google's script before giving up

// The "Continue with Google" button, drawn by Google itself (a real GIS button, not an imitation),
// with the divider that introduces it. It fits its container (the same width as the form fields),
// redraws if that width changes, and says so if Google's script cannot load instead of leaving a
// gap. With no client id configured it renders nothing at all.
//
// state: off (not configured) | loading | ready | failed
function GoogleSignIn({ onCredential, busy }) {
  const buttonRef = useRef(null);
  const latest = useRef(onCredential);
  const [state, setState] = useState(GOOGLE_CLIENT_ID ? "loading" : "off");

  useEffect(() => {
    latest.current = onCredential;
  });

  useEffect(() => {
    const container = buttonRef.current;

    if (!GOOGLE_CLIENT_ID || !container) return undefined;

    let cancelled = false;
    let attempts = 0;
    let timer = 0;
    let resizeTimer = 0;
    let drawnAt = 0;
    let observer = null;

    const draw = () => {
      const available = Math.floor(container.clientWidth);

      if (available <= 0) return; // not laid out (yet): try again on the next resize

      const width = Math.max(GOOGLE_MIN_WIDTH, Math.min(GOOGLE_MAX_WIDTH, available));

      container.replaceChildren();
      window.google.accounts.id.renderButton(container, {
        // filled_black belongs on this dark screen; pill matches the site's buttons
        theme: "filled_black",
        size: "large",
        text: "continue_with",
        shape: "pill",
        width,
      });
      drawnAt = width;
    };

    const start = () => {
      if (cancelled) return;

      if (!window.google?.accounts?.id) {
        attempts += 1;

        if (attempts < GOOGLE_LOAD_ATTEMPTS) {
          timer = window.setTimeout(start, 100);
        } else {
          console.error("Google Identity Services failed to load.");
          setState("failed");
        }

        return;
      }

      googleSignInCallback = (response) => latest.current?.(response);

      if (!googleInitialized) {
        window.google.accounts.id.initialize({
          client_id: GOOGLE_CLIENT_ID,
          callback: (response) => {
            googleSignInCallback?.(response);
          },
        });

        googleInitialized = true;
      }

      draw();
      setState("ready");

      // Follow the field width (a rotated phone, a resized window); ignore sub-pixel noise.
      observer = new ResizeObserver(() => {
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(() => {
          const next = Math.max(GOOGLE_MIN_WIDTH, Math.min(GOOGLE_MAX_WIDTH, Math.floor(container.clientWidth)));

          if (!cancelled && next > 0 && Math.abs(next - drawnAt) >= 4) draw();
        }, 150);
      });
      observer.observe(container);
    };

    timer = window.setTimeout(start, 0);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      window.clearTimeout(resizeTimer);
      observer?.disconnect();
      googleSignInCallback = null;
    };
  }, []);

  if (state === "off") return null;

  return (
    <div className="signin-alt">
      {state !== "failed" && (
        <div className="signin-divider" aria-hidden="true">
          <span>or</span>
        </div>
      )}

      {state === "failed" ? (
        <p className="signin-google-note" role="status">
          Google sign-in could not load. Use your email and password instead.
        </p>
      ) : (
        <div className={`signin-google ${state === "loading" ? "is-loading" : ""} ${busy ? "is-busy" : ""}`} role="group" aria-label="Sign in with Google" aria-busy={busy || state === "loading"}>
          <div ref={buttonRef} className="signin-google-button" />
        </div>
      )}
    </div>
  );
}

// Where the site hands over to the product. Someone new creates an account on /signup (when the
// server allows it), and Google sign-in works for an account that already exists with the same
// verified email (it never creates one); this screen says so instead of offering something that
// cannot work.
export default function SignIn() {
  const { status, user, error: serverError, backend, signIn, signInWithGoogle, retry, enterOffline } = useAuth();
  const { navigate, search } = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  // { source: "password" | "google", message }: an error belongs under the control that caused it
  const [failure, setFailure] = useState(null);
  const [pending, setPending] = useState(null); // null | "password" | "google"
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
    setPending("password");
    setFailure(null);
    justSignedIn.current = true;

    try {
      await signIn(email.trim(), password);
      // Nothing more to do: the status is now "authenticated" and the effect above takes over.
      // The form stays busy until this screen is replaced.
    } catch (error) {
      justSignedIn.current = false;
      setFailure({ source: "password", message: error.message });
      setPassword("");
      setPending(null);
    }
  };

  const onGoogleCredential = useCallback(
    async (response) => {
      if (!response?.credential) {
        setFailure({ source: "google", message: "Google did not return a sign-in. Please try again." });
        return;
      }

      setPending("google");
      setFailure(null);
      justSignedIn.current = true;

      try {
        await signInWithGoogle(response.credential);
      } catch (error) {
        justSignedIn.current = false;
        setFailure({ source: "google", message: error.message });
        setPending(null);
      }
    },
    [signInWithGoogle],
  );

  if (decision.kind === "redirect") return <Splash label="Signing you in" />;

  const initializing = status === "initializing";
  const open = status === "open";
  const unavailable = status === "unavailable";
  // A session that ended underneath the person (the reducer only sets this for that case)
  const sessionNotice = status === "unauthenticated" && !user ? serverError : null;
  const passwordFailed = failure?.source === "password";

  return (
    <AuthLayout working={pending !== null}>
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

              <p className="signin-lead">Sign in to your workspace with your email and password, or continue with Google if your account uses the same email.</p>

              {sessionNotice && (
                <p className="signin-notice" role="status">
                  {sessionNotice}
                </p>
              )}

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

                <input
                  id="si-email"
                  type="email"
                  autoComplete="username"
                  autoFocus
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  aria-invalid={passwordFailed || undefined}
                  aria-describedby={passwordFailed ? "si-error" : undefined}
                  required
                />

                <label htmlFor="si-password">Password</label>

                <span className="signin-password">
                  <input
                    id="si-password"
                    type={show ? "text" : "password"}
                    autoComplete="current-password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    aria-invalid={passwordFailed || undefined}
                    aria-describedby={passwordFailed ? "si-error" : undefined}
                    required
                  />

                  <button type="button" onClick={() => setShow(!show)} aria-label={show ? "Hide password" : "Show password"}>
                    {show ? "Hide" : "Show"}
                  </button>
                </span>

                {passwordFailed && (
                  <p className="signin-error" id="si-error" role="alert">
                    {failure.message}
                  </p>
                )}

                <button className="lp-btn lp-btn--primary signin-submit" type="submit" disabled={pending !== null || initializing || !email || !password}>
                  {pending === "password" ? "Signing in…" : initializing ? "Connecting…" : "Sign in"} {pending === null && !initializing && <Arrow />}
                </button>
              </form>

              <GoogleSignIn onCredential={onGoogleCredential} busy={pending !== null} />

              {pending === "google" && (
                <p className="signin-status" role="status">
                  Signing you in with Google…
                </p>
              )}

              {failure?.source === "google" && (
                <p className="signin-error" role="alert">
                  {failure.message}
                </p>
              )}

              <p className="signin-foot">
                {backend.signupEnabled ? (
                  <>
                    New here? <Link to={withNext("/signup", search)}>Create an account</Link>.
                  </>
                ) : (
                  <>Sign-up is closed on this server. Ask your workspace administrator to add you.</>
                )}{" "}
                <Link to="/">Back to the site</Link>
              </p>
            </>
          )}
    </AuthLayout>
  );
}
