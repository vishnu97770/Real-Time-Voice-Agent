import assert from "node:assert/strict";
import test from "node:test";
import { INITIAL, SESSION_EXPIRED, STATUSES, authReducer, canEnterApp } from "./state.js";

const probed = (status, extra = {}) => ({ type: "probed", result: { status, user: null, error: null, backend: { ...INITIAL.backend, reachable: true }, ...extra } });

test("it starts initializing, with nobody signed in", () => {
  assert.equal(INITIAL.status, "initializing");
  assert.equal(INITIAL.user, null);
});

test("a probe result resolves initializing into a final status (loading always ends)", () => {
  for (const status of ["authenticated", "unauthenticated", "open", "unavailable"]) {
    const next = authReducer(INITIAL, probed(status));

    assert.equal(next.status, status);
    assert.notEqual(next.status, "initializing");
  }
});

test("a probe carries the user and the error message through", () => {
  const signedIn = authReducer(INITIAL, probed("authenticated", { user: { email: "a@b.c" } }));
  const down = authReducer(INITIAL, probed("unavailable", { error: "Could not reach the server." }));

  assert.equal(signedIn.user.email, "a@b.c");
  assert.equal(down.error, "Could not reach the server.");
});

test("successful login: unauthenticated -> authenticated with the user", () => {
  const out = authReducer(INITIAL, probed("unauthenticated"));
  const next = authReducer(out, { type: "signed-in", user: { email: "a@b.c" } });

  assert.equal(next.status, "authenticated");
  assert.equal(next.user.email, "a@b.c");
  assert.equal(next.error, null);
});

test("a failed login changes nothing (the form handles the message; state stays signed out)", () => {
  const out = authReducer(INITIAL, probed("unauthenticated"));

  // there is deliberately no action for a failed login: it never touches the auth state
  assert.equal(authReducer(out, { type: "login-failed" }), out);
});

test("logout: authenticated -> unauthenticated, user cleared", () => {
  const inState = authReducer(INITIAL, probed("authenticated", { user: { email: "a@b.c" } }));
  const next = authReducer(inState, { type: "signed-out" });

  assert.equal(next.status, "unauthenticated");
  assert.equal(next.user, null);
});

test("an expired session (any API call answered 401) signs an authenticated user out", () => {
  const inState = authReducer(INITIAL, probed("authenticated", { user: { email: "a@b.c" } }));

  const next = authReducer(inState, { type: "expired" });

  assert.equal(next.status, "unauthenticated");
  assert.equal(next.user, null);
});

test("an expired session leaves a message for the sign-in screen; signing out on purpose does not", () => {
  const inState = authReducer(INITIAL, probed("authenticated", { user: { email: "a@b.c" } }));

  assert.equal(authReducer(inState, { type: "expired" }).error, SESSION_EXPIRED);
  assert.equal(authReducer(inState, { type: "signed-out" }).error, null);
});

test("the expiry message does not outlive the next sign-in, retry or probe", () => {
  const inState = authReducer(INITIAL, probed("authenticated", { user: { email: "a@b.c" } }));
  const expired = authReducer(inState, { type: "expired" });

  assert.equal(authReducer(expired, { type: "signed-in", user: { email: "a@b.c" } }).error, null);
  assert.equal(authReducer(expired, { type: "retry" }).error, null);
  assert.equal(authReducer(expired, probed("unauthenticated")).error, null);
});

test("a 401 while already signed out (e.g. the wrong password) is not news", () => {
  const out = authReducer(INITIAL, probed("unauthenticated"));

  assert.equal(authReducer(out, { type: "expired" }), out);
});

test("retry goes back to initializing, and clears the old error", () => {
  const down = authReducer(INITIAL, probed("unavailable", { error: "Could not reach the server." }));
  const next = authReducer(down, { type: "retry" });

  assert.equal(next.status, "initializing");
  assert.equal(next.error, null);
});

test("offline mode is only available from 'unavailable'", () => {
  const down = authReducer(INITIAL, probed("unavailable", { error: "x" }));
  const out = authReducer(INITIAL, probed("unauthenticated"));

  assert.equal(authReducer(down, { type: "offline" }).status, "open");
  assert.equal(authReducer(out, { type: "offline" }), out); // cannot skip a required sign-in
});

test("only signed-in and open states may enter the app", () => {
  assert.deepEqual(STATUSES.filter(canEnterApp).sort(), ["authenticated", "open"]);
});
