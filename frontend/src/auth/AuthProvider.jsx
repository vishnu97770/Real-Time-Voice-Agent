import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import { authEvents } from "../runtime/api.js";
import { login, loginWithGoogle, logout, signup } from "../runtime/auth.js";
import { AuthContext } from "./context.js";
import { probeSession } from "./session.js";
import { INITIAL, authReducer } from "./state.js";

// Owns the sign-in state for the whole app (see state.js for the machine and session.js for how
// the first question is asked). The public site does not wait for it: it renders at once and only
// its buttons adapt when the answer lands. Screens that need a session ask the route guard.
//
// Stale answers: every probe, sign-in and sign-out bumps `generation`, and a probe result is only
// used if nothing newer has happened since it started. So a slow first check can never overwrite
// a sign-in that finished first, and React's double effect in development cannot apply twice.
export default function AuthProvider({ children }) {
  const [state, dispatch] = useReducer(authReducer, INITIAL);
  const generation = useRef(0);

  const probe = useCallback(async () => {
    const mine = ++generation.current;
    const result = await probeSession();

    if (mine === generation.current) dispatch({ type: "probed", result });
  }, []);

  useEffect(() => {
    void probe();

    // Any API call answered 401 means the session is gone (expired, or signed out elsewhere).
    const expired = () => dispatch({ type: "expired" });

    authEvents.addEventListener("unauthorized", expired);

    return () => {
      generation.current += 1; // whatever is still in flight belongs to a provider that is gone
      authEvents.removeEventListener("unauthorized", expired);
    };
  }, [probe]);

  const retry = useCallback(() => {
    dispatch({ type: "retry" });
    void probe();
  }, [probe]);

  // Dispatches the new status before it resolves. A caller must still not navigate right after it:
  // a dispatch is applied on the next render, so the sign-in screen navigates from an effect that
  // watches the status (see SignIn), which cannot run ahead of the state the route guard reads.
  const signIn = useCallback(async (email, password) => {
    const user = await login(email, password);

    generation.current += 1;
    dispatch({ type: "signed-in", user });

    return user;
  }, []);

  // Same contract as signIn, for the Google ID token (see runtime/auth.js).
  const signInWithGoogle = useCallback(async (credential) => {
    const user = await loginWithGoogle(credential);

    generation.current += 1;
    dispatch({ type: "signed-in", user });

    return user;
  }, []);

  // Creates the account (with its own workspace) and signs it in: the server answers like login does,
  // so the outcome is the same "signed-in" transition (and the same rule: never navigate from here).
  const signUp = useCallback(async (email, password, workspaceName) => {
    const user = await signup(email, password, workspaceName);

    generation.current += 1;
    dispatch({ type: "signed-in", user });

    return user;
  }, []);

  const signOut = useCallback(async () => {
    await logout(); // never throws: signing out here is what matters, the cookie expires on its own anyway

    generation.current += 1;
    dispatch({ type: "signed-out" });
  }, []);

  const enterOffline = useCallback(() => dispatch({ type: "offline" }), []);

  const value = useMemo(
    () => ({ ...state, signIn, signInWithGoogle, signUp, signOut, retry, enterOffline }),
    [state, signIn, signInWithGoogle, signUp, signOut, retry, enterOffline],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
