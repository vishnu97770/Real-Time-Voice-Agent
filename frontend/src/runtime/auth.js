import { ApiError, post } from "./api.js";

// What to tell the person when signing in did not work. Rate limits and bad credentials get their
// own words; anything the server explained (a 4xx detail) is passed on; a 5xx is "the server's
// problem", and anything that is not an HTTP answer at all (no network, a stalled connection)
// is "could not reach the server".
function loginMessage(error) {
  if (!(error instanceof ApiError)) return "Could not reach the server.";
  if (error.status === 429) return `Too many attempts. Try again in ${error.retryAfter ?? "a few"} seconds.`;
  if (error.status === 401) return "Invalid email or password.";
  if (error.status === 422) return "Check the email and password and try again.";
  if (error.status >= 500) return "The server had a problem. Try again in a moment.";

  return error.message;
}

// POST /api/auth/login { email, password } -> 200 { user } and a session cookie (HttpOnly, so
// nothing here ever sees it). Resolves to the user; rejects with an Error whose message is fit to
// show as-is.
export async function login(email, password) {
  let response;

  try {
    response = await post("/api/auth/login", { email, password });
  } catch (error) {
    throw new Error(loginMessage(error), { cause: error });
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

// POST /api/auth/logout -> 204 and the cookie cleared.
export async function logout() {
  try {
    await post("/api/auth/logout");
  } catch {
    // Signing out locally is what matters; the cookie expires on its own anyway.
  }
}
