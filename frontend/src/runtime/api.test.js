import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { ApiError, authEvents, request } from "./api.js";

const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
});

const respond = (status, body = {}, headers = {}) => {
  globalThis.fetch = async (url, options) => {
    respond.last = { url, options };
    return new Response(JSON.stringify(body), { status, headers });
  };
};

test("every request carries the session cookie", async () => {
  respond(200);
  await request("/api/health");

  assert.equal(respond.last.options.credentials, "include");
});

test("a 401 tells the app the sign-in is gone", async () => {
  respond(401, { detail: "Sign in required" });
  let heard = 0;
  authEvents.addEventListener("unauthorized", () => (heard += 1));

  await assert.rejects(request("/api/calls"), (error) => error instanceof ApiError && error.status === 401);
  assert.equal(heard, 1);
});

test("a 429 says how long to wait, and a plain error does not sign anyone out", async () => {
  respond(429, { detail: "Too many requests. Please slow down." }, { "retry-after": "42" });
  let heard = 0;
  authEvents.addEventListener("unauthorized", () => (heard += 1));

  await assert.rejects(request("/api/auth/login"), (error) => error.status === 429 && error.retryAfter === 42);
  assert.equal(heard, 0);
});
