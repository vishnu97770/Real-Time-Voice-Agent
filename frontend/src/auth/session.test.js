import assert from "node:assert/strict";
import test from "node:test";
import { probeSession } from "./session.js";

const json = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

// A fake server: routes -> a response, or a function, or "hang" (never answers, but honours abort).
function server(routes) {
  const calls = [];
  const fetchImpl = (url, options) => {
    const path = url.replace(/^.*?(\/api\/.*)$/, "$1");

    calls.push({ path, options });

    const route = routes[path];

    if (route === "hang") {
      // AbortSignal.timeout()'s timer is unref'd in Node (a browser keeps its own timers alive), so
      // hold the event loop open until the abort arrives; otherwise the runner sees the pending
      // promise as "cancelled" instead of resolved.
      return new Promise((_, reject) => {
        const hold = setInterval(() => {}, 5);

        options.signal.addEventListener("abort", () => {
          clearInterval(hold);
          reject(options.signal.reason);
        });
      });
    }
    if (route instanceof Error) return Promise.reject(route);
    if (typeof route === "function") return Promise.resolve(route());

    return Promise.resolve(route ?? json(404, {}));
  };

  return { fetchImpl, calls };
}

const HEALTH_AUTH = json(200, { status: "ok", brain: "gemini", auth_required: true, telephony: false });

test("initialization success: /api/auth/me 200 -> authenticated, with the user", async () => {
  const { fetchImpl, calls } = server({ "/api/health": HEALTH_AUTH, "/api/auth/me": json(200, { user: { id: 1, email: "a@b.c", role: "admin" } }) });
  const result = await probeSession({ fetchImpl });

  assert.equal(result.status, "authenticated");
  assert.equal(result.user.email, "a@b.c");
  assert.equal(result.backend.reachable, true);
  assert.equal(result.backend.authRequired, true);
  // cookies must travel, or a refresh could never stay signed in
  assert.ok(calls.every((call) => call.options.credentials === "include"));
});

test("initialization 401 is signed out, an ordinary state and not a failure", async () => {
  const { fetchImpl } = server({ "/api/health": HEALTH_AUTH, "/api/auth/me": json(401, { detail: "Sign in required" }) });
  const result = await probeSession({ fetchImpl });

  assert.equal(result.status, "unauthenticated");
  assert.equal(result.user, null);
  assert.equal(result.error, null);
});

test("a server that has sign-in switched off is open, and /api/auth/me is never asked", async () => {
  const { fetchImpl, calls } = server({ "/api/health": json(200, { brain: null, auth_required: false }) });
  const result = await probeSession({ fetchImpl });

  assert.equal(result.status, "open");
  assert.deepEqual(calls.map((call) => call.path), ["/api/health"]);
});

test("a server with no LLM key still requires sign-in (it used to be treated as 'no server')", async () => {
  const { fetchImpl } = server({ "/api/health": json(200, { brain: null, auth_required: true }), "/api/auth/me": json(401, {}) });
  const result = await probeSession({ fetchImpl });

  assert.equal(result.status, "unauthenticated");
  assert.equal(result.backend.available, false); // no brain, said honestly
  assert.equal(result.backend.reachable, true);
});

test("network failure on /api/health -> unavailable, with a message", async () => {
  const { fetchImpl } = server({ "/api/health": new TypeError("Failed to fetch") });
  const result = await probeSession({ fetchImpl });

  assert.equal(result.status, "unavailable");
  assert.equal(result.backend.reachable, false);
  assert.match(result.error, /could not reach/i);
});

test("a 5xx from /api/health (the dev proxy's 502 when nothing is running) -> unavailable", async () => {
  const { fetchImpl } = server({ "/api/health": json(502, {}) });
  const result = await probeSession({ fetchImpl });

  assert.equal(result.status, "unavailable");
  assert.match(result.error, /502/);
});

test("a 5xx from /api/auth/me is unavailable, NOT 'signed out'", async () => {
  const { fetchImpl } = server({ "/api/health": HEALTH_AUTH, "/api/auth/me": json(500, {}) });
  const result = await probeSession({ fetchImpl });

  assert.equal(result.status, "unavailable");
  assert.equal(result.backend.reachable, true); // the server is there; it is /me that is unwell
});

test("a network failure on /api/auth/me -> unavailable", async () => {
  const { fetchImpl } = server({ "/api/health": HEALTH_AUTH, "/api/auth/me": new TypeError("network down") });

  assert.equal((await probeSession({ fetchImpl })).status, "unavailable");
});

test("a 200 that carries no user is an unusable reply, not a session", async () => {
  const { fetchImpl } = server({ "/api/health": HEALTH_AUTH, "/api/auth/me": json(200, {}) });

  assert.equal((await probeSession({ fetchImpl })).status, "unavailable");
});

test("a body that is not JSON is unavailable, not a crash", async () => {
  const bad = { ok: true, status: 200, json: async () => { throw new SyntaxError("Unexpected token <"); } };
  const { fetchImpl } = server({ "/api/health": bad });

  assert.equal((await probeSession({ fetchImpl })).status, "unavailable");
});

test("a hung server resolves by timeout instead of waiting forever (health)", async () => {
  const { fetchImpl } = server({ "/api/health": "hang" });
  const started = Date.now();
  const result = await probeSession({ fetchImpl, timeoutMs: 40 });

  assert.equal(result.status, "unavailable");
  assert.ok(Date.now() - started < 1500, "took too long to give up");
});

test("a hung /api/auth/me resolves by timeout too", async () => {
  const { fetchImpl } = server({ "/api/health": HEALTH_AUTH, "/api/auth/me": "hang" });
  const result = await probeSession({ fetchImpl, timeoutMs: 40 });

  assert.equal(result.status, "unavailable");
  assert.equal(result.backend.reachable, true);
});

test("whatever the server does, the result is never 'initializing'", async () => {
  const scenarios = [
    {},
    { "/api/health": json(200, null) },
    { "/api/health": HEALTH_AUTH },
    { "/api/health": HEALTH_AUTH, "/api/auth/me": json(403, {}) },
    { "/api/health": HEALTH_AUTH, "/api/auth/me": json(200, { user: null }) },
    { "/api/health": new Error("boom") },
  ];

  for (const routes of scenarios) {
    const result = await probeSession({ fetchImpl: server(routes).fetchImpl, timeoutMs: 40 });

    assert.notEqual(result.status, "initializing", JSON.stringify(Object.keys(routes)));
    assert.ok(["open", "authenticated", "unauthenticated", "unavailable"].includes(result.status));
  }
});

test("the health check's sign-up flag is passed on: open by default, closed only when the server says false", async () => {
  const open = await probeSession({ fetchImpl: server({ "/api/health": json(200, { auth_required: true, signup_enabled: true }), "/api/auth/me": json(401, {}) }).fetchImpl });
  const closed = await probeSession({ fetchImpl: server({ "/api/health": json(200, { auth_required: true, signup_enabled: false }), "/api/auth/me": json(401, {}) }).fetchImpl });
  const older = await probeSession({ fetchImpl: server({ "/api/health": HEALTH_AUTH, "/api/auth/me": json(401, {}) }).fetchImpl }); // predates sign-up: says nothing

  assert.equal(open.backend.signupEnabled, true);
  assert.equal(closed.backend.signupEnabled, false);
  assert.equal(older.backend.signupEnabled, true);
});

test("an unreachable server does not hide the Sign up links (the sign-up screen explains itself)", async () => {
  const result = await probeSession({ fetchImpl: server({ "/api/health": new TypeError("down") }).fetchImpl });

  assert.equal(result.status, "unavailable");
  assert.equal(result.backend.signupEnabled, true);
});
