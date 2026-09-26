import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { login, logout } from "./auth.js";

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
