import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { AUTH_TIMEOUT_MS, login, loginWithGoogle, logout, signup } from "./auth.js";

const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
});

const answer = (status, body = {}, headers = {}) => {
  answer.calls = [];
  globalThis.fetch = async (url, options) => {
    answer.calls.push({ url, options });
    return new Response(status === 204 ? null : JSON.stringify(body), { status, headers });
  };
};

const failNetwork = () => {
  globalThis.fetch = async () => {
    throw new TypeError("Failed to fetch");
  };
};

test("successful login posts the credentials as JSON with the cookie, and returns the user", async () => {
  answer(200, { user: { id: 1, email: "a@b.c", role: "admin", organization_id: 1 } });

  const user = await login("a@b.c", "secret");
  const [call] = answer.calls;

  assert.equal(user.email, "a@b.c");
  assert.equal(call.url, "/api/auth/login");
  assert.equal(call.options.method, "POST");
  assert.equal(call.options.credentials, "include");
  assert.deepEqual(JSON.parse(call.options.body), { email: "a@b.c", password: "secret" });
});

test("failed login (401) says so in plain words", async () => {
  answer(401, { detail: "Invalid email or password" });

  await assert.rejects(login("a@b.c", "nope"), { message: "Invalid email or password." });
});

test("rate limited (429) says how long to wait", async () => {
  answer(429, { detail: "Too many requests" }, { "retry-after": "30" });

  await assert.rejects(login("a@b.c", "x"), { message: "Too many attempts. Try again in 30 seconds." });
});

test("a server error is the server's problem, not the password's", async () => {
  answer(500, { detail: "boom" });

  await assert.rejects(login("a@b.c", "x"), { message: "The server had a problem. Try again in a moment." });
});

test("a refusal the server explained (403) shows the server's own words", async () => {
  answer(403, { detail: "Cross-origin request refused" });

  await assert.rejects(login("a@b.c", "x"), { message: "Cross-origin request refused" });
});

test("no network at all -> could not reach the server", async () => {
  failNetwork();

  await assert.rejects(login("a@b.c", "x"), { message: "Could not reach the server." });
});

test("a 200 with no user is an unusable reply, never a fake sign-in", async () => {
  answer(200, {});

  await assert.rejects(login("a@b.c", "x"), { message: "The server sent an unexpected reply." });
});

test("logout posts to the logout endpoint, and never throws", async () => {
  answer(204);
  await logout();

  assert.equal(answer.calls[0].url, "/api/auth/logout");
  assert.equal(answer.calls[0].options.method, "POST");

  failNetwork();
  await logout(); // resolves: signing out locally is what matters
});

// --- every request has a time limit ------------------------------------------------------------------

test("sign-in and sign-out requests carry a timeout signal, so none can wait forever", async () => {
  answer(200, { user: { id: 1, email: "a@b.c" } });
  await login("a@b.c", "x");
  await loginWithGoogle("token");
  answer(204);
  await logout();

  assert.equal(AUTH_TIMEOUT_MS, 15000);

  // the three calls above were captured separately; check each request's options
  const seen = [];

  globalThis.fetch = async (url, options) => {
    seen.push(options.signal);
    return new Response(JSON.stringify({ user: { id: 1 } }), { status: 200 });
  };
  await login("a@b.c", "x");
  await loginWithGoogle("t");
  assert.equal(seen.length, 2);
  assert.ok(seen.every((signal) => signal instanceof AbortSignal));
});

test("a request that times out reads as 'could not reach the server', for both sign-in routes", async () => {
  globalThis.fetch = async () => {
    throw new DOMException("The operation timed out.", "TimeoutError");
  };

  await assert.rejects(login("a@b.c", "x"), { message: "Could not reach the server." });
  await assert.rejects(loginWithGoogle("t"), { message: "Could not reach the server." });
});

// --- Google sign-in -----------------------------------------------------------------------------

test("Google sign-in posts the credential to /api/auth/google and returns the user", async () => {
  answer(200, { user: { id: 7, email: "g@b.c", role: "admin", organization_id: 1 } });

  const user = await loginWithGoogle("the-google-id-token");
  const [call] = answer.calls;

  assert.equal(user.email, "g@b.c");
  assert.equal(call.url, "/api/auth/google");
  assert.equal(call.options.method, "POST");
  assert.equal(call.options.credentials, "include");
  assert.deepEqual(JSON.parse(call.options.body), { credential: "the-google-id-token" });
});

test("each reason the server gives for refusing Google sign-in gets its own sentence (all are 401)", async () => {
  const cases = [
    ["Invalid Google credential", "Google could not verify that sign-in. Please try again."],
    ["Google account email is not verified", "The email address on this Google account is not verified."],
    ["Google account is not authorized", "This Google account is not authorized. Ask your workspace administrator to add it."],
  ];

  for (const [detail, expected] of cases) {
    answer(401, { detail });
    await assert.rejects(loginWithGoogle("t"), { message: expected }, detail);
  }
});

test("'Google sign-in is not configured' (a 503) is explained, not hidden behind 'the server had a problem'", async () => {
  answer(503, { detail: "Google sign-in is not configured" });

  await assert.rejects(loginWithGoogle("t"), { message: "Google sign-in is not set up on this server." });
});

test("Google sign-in rate limit says how long to wait", async () => {
  answer(429, { detail: "Too many requests" }, { "retry-after": "12" });

  await assert.rejects(loginWithGoogle("t"), { message: "Too many attempts. Try again in 12 seconds." });
});

test("an unexpected 5xx from Google sign-in is the server's problem, and never leaks its detail", async () => {
  answer(500, { detail: "Traceback (most recent call last): secret internals" });

  await assert.rejects(loginWithGoogle("t"), { message: "The server had a problem. Try again in a moment." });
});

test("an unfamiliar refusal detail (4xx) is passed through as the server sent it", async () => {
  answer(403, { detail: "Cross-origin request refused" });

  await assert.rejects(loginWithGoogle("t"), { message: "Cross-origin request refused" });
});

test("Google sign-in with no network, or a reply with no user, fails cleanly", async () => {
  failNetwork();
  await assert.rejects(loginWithGoogle("t"), { message: "Could not reach the server." });

  answer(200, {});
  await assert.rejects(loginWithGoogle("t"), { message: "The server sent an unexpected reply." });
});

// --- sign-up ----------------------------------------------------------------------------------------

test("sign-up posts email, password and the workspace name, and returns the signed-in user", async () => {
  answer(201, { user: { id: 9, email: "new@b.c", role: "operator", organization_id: 4 } });

  const user = await signup("new@b.c", "a long enough password", "Northwind");
  const [call] = answer.calls;

  assert.equal(user.organization_id, 4);
  assert.equal(call.url, "/api/auth/signup");
  assert.equal(call.options.method, "POST");
  assert.equal(call.options.credentials, "include");
  assert.ok(call.options.signal instanceof AbortSignal); // it cannot wait forever either
  assert.deepEqual(JSON.parse(call.options.body), { email: "new@b.c", password: "a long enough password", workspace_name: "Northwind" });
});

test("a taken email says so (the screen adds the way to sign in)", async () => {
  answer(409, { detail: "An account with this email already exists" });

  await assert.rejects(signup("a@b.c", "a long enough password", "W"), { message: "An account with this email already exists." });
});

test("the failure keeps its HTTP status, so the screen can offer 'sign in' for a taken email", async () => {
  answer(409, { detail: "x" });

  await assert.rejects(signup("a@b.c", "a long enough password", "W"), (error) => error.cause?.status === 409);
});

test("sign-up closed on the server is passed through as the server said it", async () => {
  answer(403, { detail: "Sign-up is not open on this server" });

  await assert.rejects(signup("a@b.c", "a long enough password", "W"), { message: "Sign-up is not open on this server" });
});

test("a refusal the server explained (422 with a readable detail) is shown; a bare schema error gets a prompt", async () => {
  answer(422, { detail: "Password must be at least 12 characters" });
  await assert.rejects(signup("a@b.c", "short", "W"), { message: "Password must be at least 12 characters" });

  answer(422, { detail: [{ loc: ["body", "email"], msg: "field required" }] });
  await assert.rejects(signup("", "a long enough password", "W"), { message: "Check the details and try again." });
});

test("sign-up rate limit, an old server with no sign-up route, a broken server and no network each read differently", async () => {
  answer(429, { detail: "Too many requests" }, { "retry-after": "20" });
  await assert.rejects(signup("a@b.c", "a long enough password", "W"), { message: "Too many attempts. Try again in 20 seconds." });

  answer(404, { detail: "Not Found" });
  await assert.rejects(signup("a@b.c", "a long enough password", "W"), { message: "Sign-up is not available on this server." });

  answer(500, { detail: "Traceback: secret internals" });
  await assert.rejects(signup("a@b.c", "a long enough password", "W"), { message: "The server had a problem. Try again in a moment." });

  failNetwork();
  await assert.rejects(signup("a@b.c", "a long enough password", "W"), { message: "Could not reach the server." });
});

test("a 201 with no user is an unusable reply, never a fake sign-up", async () => {
  answer(201, {});

  await assert.rejects(signup("a@b.c", "a long enough password", "W"), { message: "The server sent an unexpected reply." });
});
