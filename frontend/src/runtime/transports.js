// A transport is one way of running a call. The call hook talks to this
// interface and does not know whether the brain is on the server or in the tab.
//
//   name                          which brain is answering
//   open()                        -> greeting text (always the AI disclosure)
//   turn(text, signal)            -> async iterable of events:
//       { type: "tool_call", name, args, result, guarded }
//       { type: "sentence",  text }             one speakable sentence
//       { type: "done", kind, pending, blocked }
//   interrupted()                 the caller talked over the agent
//   close(duration, transcript)   -> call result (summary)

import { closeCall, createSession, log, openCall, processTurn } from "./session.js";
import { API_BASE, getJson, post } from "./api.js";
import { mapResult } from "./results.js";
import { splitSentences } from "./sentences.js";

const HEALTH_TIMEOUT_MS = 2000;

// Is there a server with an LLM behind it? Resolves { available, brain }.
export async function detectBackend() {
  try {
    const response = await fetch(`${API_BASE}/api/health`, {
      signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
    });

    if (!response.ok) return { available: false, brain: null, authRequired: false, telephony: false };

    const { brain, auth_required: authRequired, telephony } = await response.json();

    return { available: Boolean(brain), brain, authRequired: Boolean(authRequired), telephony: Boolean(telephony) };
  } catch {
    return { available: false, brain: null, authRequired: false, telephony: false };
  }
}

// The whole runtime in the browser: rule-based brain, no server needed.
export function createLocalTransport(profile) {
  const session = createSession(profile);

  return {
    name: "local-rules",

    async open() {
      return openCall(session);
    },

    async *turn(text, signal) {
      const result = await processTurn(session, text);

      for (const call of result.toolCalls) {
        if (signal.aborted) return;
        yield { type: "tool_call", ...call, guarded: Boolean(call.guarded) };
      }

      for (const sentence of splitSentences(result.replyText)) {
        if (signal.aborted) return;
        yield { type: "sentence", text: sentence };
      }

      yield { type: "done", kind: result.kind, pending: result.pending, blocked: result.blocked };
    },

    async interrupted() {
      log(session, "playback_interrupted");
    },

    async close(duration, transcript) {
      return closeCall(session, duration, transcript);
    },
  };
}

async function* readEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { value, done } = await reader.read();

    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    let boundary;

    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, boundary);

      buffer = buffer.slice(boundary + 2);

      const data = block.split("\n").find((line) => line.startsWith("data: "));

      if (data) yield JSON.parse(data.slice(6));
    }
  }
}

// What the person sees when the phone "rings": who is calling, and whether the
// call can still be answered. Throws if the link is wrong or expired.
export async function fetchRing(jobId, token) {
  return getJson(`/api/call-jobs/${encodeURIComponent(jobId)}/ring?token=${encodeURIComponent(token)}`);
}

export async function declineJob(jobId, token) {
  await post(`/api/call-jobs/${encodeURIComponent(jobId)}/decline`, { token });
}

// The server owns the session, the LLM, the consent gate and the audit log.
// With `attach`, this joins an outbound call the agent placed (answering the
// job) instead of opening an inbound one.
export function createRemoteTransport(profile, brain, attach = null, customerRef = null) {
  let callId = null;

  return {
    name: brain,

    async open() {
      const response = attach
        ? await post(`/api/call-jobs/${encodeURIComponent(attach.jobId)}/answer`, {
            token: attach.token,
          })
        : await post("/api/calls", {
            profile_id: profile.id,
            ...(customerRef && { customer_ref: customerRef }),
          });
      const body = await response.json();

      callId = body.call_id;

      return body.greeting;
    },

    async *turn(text, signal) {
      const response = await post(`/api/calls/${callId}/turn`, { text }, { signal });

      yield* readEvents(response);
    },

    async interrupted() {
      try {
        await post(`/api/calls/${callId}/events`, { type: "playback_interrupted" });
      } catch {
        // Best effort: losing this note must not disturb the call.
      }
    },

    async close() {
      return mapResult(await (await post(`/api/calls/${callId}/end`)).json());
    },
  };
}
