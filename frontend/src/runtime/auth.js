import { ApiError, post } from "./api.js";

// How long a sign-in or sign-out request may take before we stop waiting. Without a limit, a
// server that accepts the connection and never answers would leave "Signing in…" on screen forever.
export const AUTH_TIMEOUT_MS = 15000;

const timeout = () => ({ signal: AbortSignal.timeout(AUTH_TIMEOUT_MS) });

const rateLimited = (error) => `Too many attempts. Try again in ${error.retryAfter ?? "a few"} seconds.`;
const SERVER_PROBLEM = "The server had a problem. Try again in a moment.";
const UNREACHABLE = "Could not reach the server.";

// What to tell the person when email + password sign-in did not work. Rate limits and bad
// credentials get their own words; anything the server explained (a 4xx detail) is passed on; a
// 5xx is "the server's problem", and anything that is not an HTTP answer at all (no network, a
// stalled connection, the timeout above) is "could not reach the server".
function loginMessage(error) {
  if (!(error instanceof ApiError)) return UNREACHABLE;
  if (error.status === 429) return rateLimited(error);
  if (error.status === 401) return "Invalid email or password.";
  if (error.status === 422) return "Check the email and password and try again.";
  if (error.status >= 500) return SERVER_PROBLEM;

  return error.message;
}

// The reasons POST /api/auth/google gives for refusing (backend/app/main.py, google_login), put
// into words for a person. Keyed by the server's own `detail`, lower-cased. The server uses the
// same status (401) for the first three, so the status alone cannot tell them apart.
const GOOGLE_REASONS = {
  "invalid google credential": "Google could not verify that sign-in. Please try again.",
  "google account email is not verified": "The email address on this Google account is not verified.",
  "google account is not authorized": "This Google account is not authorized. Ask your workspace administrator to add it.",
  "google sign-in is not configured": "Google sign-in is not set up on this server.",
};

function googleMessage(error) {
  if (!(error instanceof ApiError)) return UNREACHABLE;
  if (error.status === 429) return rateLimited(error);

  // A known reason wins over the generic 5xx text (the "not configured" refusal is a 503).
  const reason = GOOGLE_REASONS[String(error.message).toLowerCase()];

  if (reason) return reason;
  if (error.status >= 500) return SERVER_PROBLEM;

  return error.message;
}

// What to tell the person when creating the account did not work (POST /api/auth/signup). The
// server's own reasons (a taken email, sign-up closed, a refused password) are readable already and
// are passed on; a schema-level 422 has no readable detail, so it gets a generic prompt.
function signupMessage(error) {
  if (!(error instanceof ApiError)) return UNREACHABLE;
  if (error.status === 429) return rateLimited(error);
  if (error.status === 409) return "An account with this email already exists.";
  if (error.status === 404 || error.status === 405) return "Sign-up is not available on this server.";
  if (error.status === 422) return error.message === "Request failed" ? "Check the details and try again." : error.message;
  if (error.status >= 500) return SERVER_PROBLEM;

  return error.message; // includes 403 "Sign-up is not open on this server"
}

// One request that ends in a signed-in user (or an Error whose message is fit to show as-is). Both
// sign-in routes answer the same way: 200 { user } and a session cookie (HttpOnly, so nothing here
// ever sees it).
async function signInRequest(path, body, messageFor) {
  let response;

  try {
    response = await post(path, body, timeout());
  } catch (error) {
    throw new Error(messageFor(error), { cause: error });
  }

  let user = null;

  try {
    user = (await response.json()).user ?? null;
  } catch {
    // not JSON: handled as an unusable reply below
  }

  if (!user) throw new Error("The server sent an unexpected reply.");

  return user;
}

// POST /api/auth/login { email, password }
export const login = (email, password) => signInRequest("/api/auth/login", { email, password }, loginMessage);

// POST /api/auth/google { credential }: the Google ID token, checked by the server. It signs in an
// existing local user with the same verified email; it never creates one.
export const loginWithGoogle = (credential) => signInRequest("/api/auth/google", { credential }, googleMessage);

// POST /api/auth/signup { email, password, workspace_name }: creates the account with a workspace of
// its own (role "operator") and signs it in, answering exactly like login: 201 { user } and the
// session cookie.
export const signup = (email, password, workspaceName) =>
  signInRequest("/api/auth/signup", { email, password, workspace_name: workspaceName }, signupMessage);

// POST /api/auth/logout -> 204 and the cookie cleared.
export async function logout() {
  try {
    await post("/api/auth/logout", undefined, timeout());
  } catch {
    // Signing out locally is what matters; the cookie expires on its own anyway. (This also covers
    // a server that never answers: the timeout ends the wait, so signing out cannot hang.)
  }
}
