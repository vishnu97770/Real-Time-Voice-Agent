// Asks the server what it is and who is signed in, and turns every possible answer into one of a
// small set of outcomes. Every path - success, 401, 5xx, a dead network, a hung connection, a
// malformed body - ends in a status other than "initializing", so nothing that waits on this can
// wait forever.
//
// The contract it relies on (backend/app/main.py, unchanged):
//   GET /api/health     -> 200 { status, brain, auth_required, telephony, ... }
//   GET /api/auth/me    -> 200 { user } | 401 { detail }      (session cookie "va_session")
//
// Outcomes:
//   open             the server has sign-in switched off, nothing to check
//   authenticated    /api/auth/me answered 200 with a user
//   unauthenticated  /api/auth/me answered 401: signed out (an ordinary state, not a failure)
//   unavailable      the server could not be asked, or answered something unusable

import { API_BASE } from "../runtime/api.js";

export const PROBE_TIMEOUT_MS = 5000;

// signupEnabled defaults to true when unknown: a server that cannot be reached should not make the
// Sign up links disappear (the sign-up screen itself explains a closed or unreachable server).
const UNKNOWN_BACKEND = { reachable: false, available: false, authRequired: false, brain: null, telephony: false, signupEnabled: true };

const unavailable = (error, backend = UNKNOWN_BACKEND) => ({ status: "unavailable", user: null, backend, error });

async function get(path, { fetchImpl, timeoutMs, apiBase }) {
  // The signal covers the whole exchange, body included, so a server that sends headers and
  // then stalls is cut off too.
  return fetchImpl(`${apiBase}${path}`, { credentials: "include", signal: AbortSignal.timeout(timeoutMs) });
}

export async function probeSession({ fetchImpl = globalThis.fetch.bind(globalThis), timeoutMs = PROBE_TIMEOUT_MS, apiBase = API_BASE } = {}) {
  const options = { fetchImpl, timeoutMs, apiBase };
  let health;

  try {
    const response = await get("/api/health", options);

    if (!response.ok) return unavailable(`The server answered ${response.status}.`);

    health = await response.json();
  } catch {
    return unavailable("Could not reach the server.");
  }

  const backend = {
    reachable: true,
    // "available" is what the voice console means by it: an LLM brain is configured. It says
    // nothing about whether you may sign in, so it plays no part in the decision below.
    available: Boolean(health?.brain),
    authRequired: Boolean(health?.auth_required),
    brain: health?.brain ?? null,
    telephony: Boolean(health?.telephony),
    // only an explicit "false" closes it (a server that predates sign-up sends nothing)
    signupEnabled: health?.signup_enabled !== false,
  };

  if (!backend.authRequired) return { status: "open", user: null, backend, error: null };

  try {
    const response = await get("/api/auth/me", options);

    if (response.status === 401) return { status: "unauthenticated", user: null, backend, error: null };
    if (!response.ok) return unavailable(`The server answered ${response.status}.`, backend);

    const user = (await response.json())?.user;

    return user ? { status: "authenticated", user, backend, error: null } : unavailable("The server sent an unexpected reply.", backend);
  } catch {
    return unavailable("Could not reach the server.", backend);
  }
}
