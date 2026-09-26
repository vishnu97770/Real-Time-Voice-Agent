import assert from "node:assert/strict";
import test from "node:test";
import { isAppRoute, matchRoute, normalizePath, safeNext } from "./router.js";

test("static routes match", () => {
  assert.equal(matchRoute("/").name, "landing");
  assert.equal(matchRoute("/signin").name, "signin");
  assert.equal(matchRoute("/app/dashboard").name, "dashboard");
  assert.equal(matchRoute("/app/analytics").name, "analytics");
});

test("a trailing slash is the same route", () => {
  assert.equal(matchRoute("/app/agents/").name, "agents");
  assert.equal(normalizePath("/app/"), "/app");
  assert.equal(normalizePath("/"), "/");
  assert.equal(normalizePath("///"), "/");
});

test("the wizard wins over an agent whose id is 'new'", () => {
  assert.equal(matchRoute("/app/agents/new").name, "agent-new");
  assert.deepEqual(matchRoute("/app/agents/42"), { name: "agent-edit", params: { id: "42" }, redirect: null });
});

test("params are decoded, and a broken escape is not a match", () => {
  assert.equal(matchRoute("/app/calls/CALL%2D1").params.id, "CALL-1");
  assert.equal(matchRoute("/app/calls/%E0%A4%A").name, "not-found");
});

test("redirect routes say where they go", () => {
  assert.equal(matchRoute("/app").redirect, "/app/dashboard");
  assert.equal(matchRoute("/app/dashboard").redirect, null);
});

test("/signup is its own screen (it used to redirect to /signin)", () => {
  assert.deepEqual(matchRoute("/signup"), { name: "signup", params: {}, redirect: null });
  assert.equal(matchRoute("/signup/").name, "signup");
  assert.equal(isAppRoute("signup"), false); // a public screen, not part of the application
});

test("unknown paths are not-found, never a blank page", () => {
  assert.equal(matchRoute("/nope").name, "not-found");
  assert.equal(matchRoute("/app/agents/1/extra").name, "not-found");
  assert.equal(matchRoute("").name, "landing"); // not a path: treated as the root
  assert.equal(matchRoute(undefined).name, "landing");
});

test("safeNext keeps only in-app destinations", () => {
  assert.equal(safeNext("/app/calls"), "/app/calls");
  assert.equal(safeNext("/app/calls/CALL-1?tab=timeline"), "/app/calls/CALL-1?tab=timeline");
  assert.equal(safeNext("https://evil.example/app"), "/app/dashboard");
  assert.equal(safeNext("//evil.example"), "/app/dashboard");
  assert.equal(safeNext("/app\\..\\evil"), "/app/dashboard");
  assert.equal(safeNext("/signin"), "/app/dashboard");
  assert.equal(safeNext("/app/does-not-exist"), "/app/dashboard");
  assert.equal(safeNext(null), "/app/dashboard");
  assert.equal(safeNext(undefined, "/app/agents"), "/app/agents");
});

test("isAppRoute distinguishes the application from the public site", () => {
  assert.equal(isAppRoute("dashboard"), true);
  assert.equal(isAppRoute("console"), true);
  assert.equal(isAppRoute("landing"), false);
  assert.equal(isAppRoute("signin"), false);
  assert.equal(isAppRoute("app"), false); // a redirect, not a screen
});
