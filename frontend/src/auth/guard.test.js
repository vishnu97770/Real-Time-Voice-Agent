import assert from "node:assert/strict";
import test from "node:test";
import { decideApp, decideSignIn } from "./guard.js";
import { STATUSES } from "./state.js";

test("protected route while authenticated renders the page", () => {
  assert.deepEqual(decideApp({ status: "authenticated", path: "/app/dashboard" }), { kind: "render" });
});

test("protected route in an open workspace (no sign-in needed) renders too", () => {
  assert.deepEqual(decideApp({ status: "open", path: "/app/calls" }), { kind: "render" });
});

test("protected route while unauthenticated redirects to /signin", () => {
  const decision = decideApp({ status: "unauthenticated", path: "/app/dashboard" });

  assert.equal(decision.kind, "redirect");
  assert.equal(decision.to, "/signin");
});

test("a deep link is remembered so sign-in can bring the person back to it", () => {
  const decision = decideApp({ status: "unauthenticated", path: "/app/calls/CALL-1", search: "?tab=timeline" });

  assert.equal(decision.to, `/signin?next=${encodeURIComponent("/app/calls/CALL-1?tab=timeline")}`);
});

test("protected route while the first check runs shows loading, and only then", () => {
  assert.deepEqual(decideApp({ status: "initializing" }), { kind: "loading" });
});

test("loading is temporary by construction: no status other than initializing ever answers 'loading'", () => {
  for (const status of STATUSES.filter((s) => s !== "initializing")) {
    assert.notEqual(decideApp({ status }).kind, "loading", status);
  }

  assert.notEqual(decideApp({ status: "something-unexpected" }).kind, "loading");
});

test("an unreachable server shows the retry screen, not a spinner", () => {
  assert.deepEqual(decideApp({ status: "unavailable" }), { kind: "unavailable" });
  assert.deepEqual(decideApp({ status: "??" }), { kind: "unavailable" });
});

test("an authenticated user visiting /signin is sent to the dashboard", () => {
  assert.deepEqual(decideSignIn({ status: "authenticated", next: null }), { kind: "redirect", to: "/app/dashboard" });
});

test("...or to where they were headed, when that is inside the app", () => {
  assert.equal(decideSignIn({ status: "authenticated", next: "/app/calls" }).to, "/app/calls");
});

test("...but never to another site", () => {
  assert.equal(decideSignIn({ status: "authenticated", next: "https://evil.example" }).to, "/app/dashboard");
  assert.equal(decideSignIn({ status: "authenticated", next: "//evil.example" }).to, "/app/dashboard");
});

test("everyone else sees the sign-in form, including while the check runs", () => {
  for (const status of ["initializing", "unauthenticated", "open", "unavailable"]) {
    assert.deepEqual(decideSignIn({ status }), { kind: "render" }, status);
  }
});
