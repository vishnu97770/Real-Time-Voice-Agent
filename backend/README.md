# Voice Agent API

The server side of the Real-Time Voice Agent: the runtime that owns a call. It holds the
session, the LLM, the consent gate for guarded actions, the guardrails and the audit log.
The browser (`../frontend`) only captures and plays audio.

```
browser  ──  /api/calls/{id}/turn (SSE)  ──►  session ──► brain (Gemini, tool calling)
 mic/ASR                                        │  ▲            │
 TTS ◄── sentences as they are written ◄────────┘  └── read-only tools over profile data
                                                consent gate · guardrails · audit
```

## Run

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # then set GEMINI_API_KEY
.venv/bin/python -m app.cli create-user you@example.com --role admin   # nobody can sign in until you do this
.venv/bin/python -m app.cli seed-demo                                  # optional: one demo customer per profile
.venv/bin/uvicorn app.main:app --reload --port 8000

# in another terminal
cd frontend && npm run dev      # Vite proxies /api to :8000
```

Without `GEMINI_API_KEY` the server still starts. `/api/health` reports `"brain": null`, and
the browser falls back to its built-in local runtime, so the demo works with no key.

Tests need no key and no network: `.venv/bin/python -m pytest`.

## API

| Method | Path | |
|---|---|---|
| GET | `/api/health` | `{status, brain, profiles}`. `brain` is `null` when no LLM is configured |
| POST | `/api/calls` `{profile_id}` | Opens a call. Returns `{call_id, greeting}`. The greeting always starts with the AI disclosure. `503` if no LLM |
| POST | `/api/calls/{id}/turn` `{text}` | Server-sent events, one caller utterance in |
| POST | `/api/calls/{id}/events` `{type: "playback_interrupted"}` | The caller talked over the agent (only the client can hear that) |
| POST | `/api/calls/{id}/end` | Closes the call and returns the call result: outcome, transcript, audit trail |

A turn streams:

```
event: tool_call   {name, args, result, guarded}    a lookup that grounded the answer
event: sentence    {text}                           one vetted, speakable sentence
event: done        {kind, pending, blocked}         pending = action awaiting yes/no
```

`kind` is `answer`, `chat`, `confirm`, `action`, `declined`, `reprompt`, `blocked`, `error`
or `fallback`.

## Outbound calls: a job in, a result out

A business workflow sends a **call job** (who to call, why, which profile). The platform
conducts the whole call and sends back a **call result**. The business decides who is called
and what happens with the result; the platform only owns the call.

```bash
curl -X POST localhost:8000/api/call-jobs -H "X-API-Key: $VOICE_AGENT_API_KEY" \
  -H 'content-type: application/json' -d '{
    "reference": "crm-1042",
    "profile_id": "bank",
    "callee": {"name": "Priya Sharma", "phone": "+91 98765 43210"},
    "reason": "unusual activity on your credit card",
    "callback_url": "https://crm.example.com/hooks/calls"
  }'
# -> {"job_id": "JOB-…", "status": "ringing", "answer_url": "http://localhost:5173/?job=…&token=…", …}
```

`ringing → in_progress → completed | wrong_party`, or `declined | no_answer`.

| Method | Path | Auth | |
|---|---|---|---|
| POST | `/api/call-jobs` | API key | Create a job. Same `reference` again returns the same job (200); the same reference with a different payload is a 409 |
| GET | `/api/call-jobs`, `/api/call-jobs/{id}` | API key | Status, and the full result once finished |
| GET | `/api/calls/{call_id}/result` | API key | The saved result of any finished call, inbound or outbound |
| GET | `/api/call-jobs/{id}/ring?token=` | answer token | What the callee sees before answering |
| POST | `/api/call-jobs/{id}/answer` `{token}` | answer token | Answer: returns `{call_id, greeting}`, then the normal `/api/calls/{id}/…` endpoints run the call |
| POST | `/api/call-jobs/{id}/decline` `{token}` | answer token | Decline |

The **call result** (in `GET /api/call-jobs/{id}` under `result`, and in the callback) has
`outcome` (`completed`, `action_completed`, `wrong_party`, `identity_not_confirmed`,
`declined`, `no_answer`), `end_reason`, `disclosure_given`, `summary`, `actions_taken`,
`transcript` and the full `audit` trail. Phone numbers are masked (`***3210`) everywhere they
are returned.

**Callbacks.** If `callback_url` is set, the finished job is POSTed there as
`{"event": "call_job.finished", "job": {…, "result": {…}}}` with an `X-Signature: t=<unix>,v1=<hex>`
header: HMAC-SHA256 over `"<t>.<raw body>"` using `VOICE_AGENT_WEBHOOK_SECRET` (or the API key).
Verify it, and reject old timestamps (`app/outbound.py: verify_signature` is a reference).
Failed deliveries are retried 3 times; the state is `callback.status` on the job, and pending
deliveries resume after a restart. Callback URLs that resolve to private or local addresses
are refused.

**How the call runs.** Today the "phone" is a web link: the callee opens `answer_url`, sees
an incoming-call screen and answers. A telephony provider (Twilio, LiveKit SIP) will replace
the link; the job, the session and the result do not change.

- The agent speaks first: the AI disclosure, who it is calling from, and *"Am I speaking with
  {name}?"*. **Nothing about the callee is said, and no data is read, until they confirm.** This is a
  deterministic check, not the LLM: "no" hangs up without saying anything (`wrong_party`), and two
  unclear answers end the call (`identity_not_confirmed`).
- An outbound call gets only the tools and actions its profile lists for outbound
  (`Profile.outbound`), and `fixed_args` override what the model asks for, so an applicant call
  cannot look up another applicant.
- The agent ends the call itself at the time limit (`max_duration_seconds`), and a callee who
  vanishes is hung up on after an idle period. A job nobody answers in `ring_timeout_seconds`
  becomes `no_answer`. All of these still produce a result and a callback.

## Signing in, and who can do what

There are three kinds of caller, each with its own credential:

| Caller | Credential | Can |
|---|---|---|
| Operator (a person in the console) | email + password → `HttpOnly` session cookie | start calls, place outbound calls, view history and results. **Admins** can also edit customer data |
| Business system | `X-API-Key` header | create/read call jobs and results, load customers |
| Callee | the unguessable token in their link | answer or decline that one call |

Sign-in is **on by default** (`AUTH_REQUIRED=true`); there is no default account. Manage users
with `python -m app.cli create-user | list-users | disable-user | set-password`. Passwords are
hashed with scrypt (12+ characters), the cookie is `HttpOnly` and `SameSite=Lax` (set
`COOKIE_SECURE=true` behind https), only a hash of each session token is stored, and disabling a
user signs them out at once. A signed-in browser's writes are refused if their `Origin` is not
the frontend's. Login gives the same answer for an unknown email and a wrong password.

**Rate limits** (per minute, in memory): login per address and per account, callee-link guessing
per address, job creation per caller, calls started per operator, turns per call. Over the limit
is a `429` with `Retry-After`. Behind a reverse proxy set `TRUSTED_PROXY_HOPS`, or every client
looks like the proxy. The limits are per process: with several workers, move them to Redis or
the proxy.

## Customers: real data instead of the demo

Each profile ships with one made-up customer. To point calls at real people, load them:

```bash
python -m app.cli import-customers bank customers.json   # [{"ref", "display_name", "data"}, ...]
# or, over the API (admin or API key):
curl -X PUT localhost:8000/api/customers/bank/cust-1042 -H "X-API-Key: $KEY" \
  -H 'content-type: application/json' -d '{"display_name": "Anita Rao", "data": {...}}'
```

`data` must have the shape of that profile's demo data (see `app/profiles/`), and every read tool
must run against it: bad data is rejected when it is loaded (an import with one bad row loads
nothing). Then pass `customer_ref` when starting a call or creating a job. The greeting uses their
name, the agent sees only their record, and **a confirmed action (freezing a card, filing a claim)
is written back to it**. If the write fails the change is rolled back and the caller is told
nothing was changed. Without `customer_ref` the built-in demo data is used.

`GET /api/customers?profile_id=` lists names (never data); reading one customer's data is admin-only.

## Call history

`GET /api/calls?direction=&limit=` lists finished calls, newest first; `GET /api/calls/{id}/result`
returns one in full. The console's **Call History** page uses these, lists outbound jobs, and has
a **Place a call** form (the same request a business system sends).

## What is enforced here, whichever brain is loaded

- **AI disclosure** opens every call (`ai_disclosed` is in the audit log).
- **Secrets**: a caller's password, PIN, OTP or card number is refused and redacted before it
  reaches the LLM, the transcript, the audit log or the call result. An agent sentence that
  solicits one is replaced before it is sent for speech.
- **Consent**: the brain can only *propose* an action. The session asks, and only an explicit
  yes executes it. "yes" and "no" are handled without the LLM. A hesitation ("hmm") repeats the
  question; a new request lets the old one lapse unconfirmed. The action is recorded as pending
  only after the whole question has been delivered.
- **Grounding**: the model only sees profile data through read-only tools, and the system
  prompt says to state only what they return. This is a prompt-level control plus the
  `answer`/`chat` distinction in `done.kind` (a reply with no lookup is `chat`); it does not
  mechanically prove every number came from a tool.
- **Barge-in**: closing the turn's connection stops the LLM stream. What was already said is
  kept in the transcript, marked `interrupted`.
- **Failure**: an LLM error or timeout is spoken as an apology and the call carries on; error
  details are logged by class name only.

## Adding a vertical

Add a module in `app/profiles/` (persona, data, read `ToolSpec`s, guarded `ActionSpec`s,
greeting) and list it in `app/profiles/__init__.py`. `session.py` and `brains/` do not change.
The browser has a matching definition in `frontend/src/profiles/` for offline mode, so add both.

## Limits

- Live calls are in memory (a restart drops calls in progress); jobs, results, users and
  customers are in the database. On start-up new tables are created and missing nullable columns
  are added; anything else (renames, drops, type changes) needs a real migration tool such as Alembic.
- The per-call endpoints (`/api/calls/{id}/turn`, `/events`, `/end`) are protected by the call id
  alone, which is unguessable and short-lived but is a bearer token: treat call ids like secrets,
  and serve everything over https.
- There is no audit of who edited customer data, no password reset by email, and no two-factor sign-in.
- Identity confirmation is "the person who answered said yes", which is standard first-party
  confirmation but is not verification. There is no calling-hours, do-not-call or consent
  enforcement: those are the business's responsibility until the appendix's compliance rules are
  built in.
- Customer data lives in this service's database. Connecting to a bank's or hospital's own system
  means writing a loader that calls `PUT /api/customers/...` (or a provider behind
  `Service._customer`), and deciding who owns writes back.
- The Gemini path is covered by tests against a fake client with the SDK's real types, not
  against the live service.
- Profile data is mock data, duplicated between `app/profiles/` and `frontend/src/profiles/`.
- Outbound calls need the server (there is no offline mode for them).
