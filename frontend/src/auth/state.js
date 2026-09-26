// The sign-in state machine. One status at a time:
//
//   initializing     the first question (or a retry) is still in flight
//   authenticated    signed in
//   unauthenticated  signed out: the server wants a sign-in and there is none
//   open             nothing to sign in to (the server has sign-in off, or the visitor chose offline mode)
//   unavailable      the server could not be reached or answered nonsense
//
//   initializing -> authenticated | unauthenticated | open | unavailable     (a probe result)
//   unavailable  -> initializing (retry) | open (offline)
//   unauthenticated -> authenticated (signed in)
//   authenticated -> unauthenticated (signed out, or the session expired)
//
// Pure, so the whole machine can be exercised without React or a network.

// Shown on the sign-in screen when a session ended underneath the person (as opposed to them signing out).
export const SESSION_EXPIRED = "Your session has expired. Please sign in again.";

export const INITIAL = {
  status: "initializing",
  user: null,
  error: null,
  backend: { reachable: false, available: false, authRequired: false, brain: null, telephony: false, signupEnabled: true },
};

export function authReducer(state, action) {
  switch (action.type) {
    case "probed":
      return { status: action.result.status, user: action.result.user, error: action.result.error ?? null, backend: action.result.backend };

    case "retry":
      return { ...state, status: "initializing", error: null };

    case "signed-in":
      return { ...state, status: "authenticated", user: action.user, error: null, backend: { ...state.backend, reachable: true, authRequired: true } };

    case "signed-out":
      return { ...state, status: "unauthenticated", user: null, error: null };

    // Any API call answered 401 while we thought we were signed in: the session is gone. Unlike
    // signing out, this was not the person's choice, so the sign-in screen gets to say why.
    case "expired":
      return state.status === "authenticated" ? { ...state, status: "unauthenticated", user: null, error: SESSION_EXPIRED } : state;

    // The visitor chose to carry on without a server (the browser-only console).
    case "offline":
      return state.status === "unavailable" ? { ...state, status: "open", user: null, error: null } : state;

    default:
      return state;
  }
}

export const STATUSES = ["initializing", "authenticated", "unauthenticated", "open", "unavailable"];

// Both "authenticated" and "open" may use the application.
export const canEnterApp = (status) => status === "authenticated" || status === "open";
