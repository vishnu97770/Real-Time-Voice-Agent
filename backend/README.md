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

## Requirements

- Python 3.11 or newer
- PostgreSQL 14 or newer (the application's database)
- Linux or macOS shell commands are shown below

## Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then edit .env: at least DATABASE_URL (and GEMINI_API_KEY for the LLM)
```

`.env` is gitignored and holds your real values. Only `.env.example` (placeholders) is committed.
All settings are read once through `app/config.py`; code uses `settings.database_url`, never
`os.getenv`.

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | **required** | `postgresql+psycopg://user:password@host:5432/dbname`. There is no default, so a missing value is an error rather than a quiet fallback to another database |
| `APP_NAME` | `Real-Time Voice Agent` | API title |
| `APP_ENV` | `development` | `development`, `staging` or `production` |
| `DEBUG` | `false` | Renders tracebacks in the browser for unhandled errors. Ignored when `APP_ENV=production` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |

## PostgreSQL

Create a role and a database once (adjust names and password; do not reuse the placeholder):

```bash
sudo -u postgres psql -c "CREATE ROLE voice_agent LOGIN PASSWORD 'choose-a-password';"
sudo -u postgres psql -c "CREATE DATABASE voice_agent OWNER voice_agent;"
```

then set `DATABASE_URL=postgresql+psycopg://voice_agent:choose-a-password@localhost:5432/voice_agent`
in `.env`. If the password has special characters, URL-encode them (`@` becomes `%40`).

The server starts even when PostgreSQL is down: `/health` still answers, the failure is logged
(without credentials), and database-backed endpoints return `503 {"detail": "Database unavailable"}`
until it is back. The schema is never created by the server: apply it with Alembic (next section). If
the database has not been migrated, the server says so in its log at start-up.

## Migrations (Alembic)

Alembic owns the database schema. It reads `DATABASE_URL` through the same settings and compares
against the models on `app.db.Base`. **Run this after creating the database, and after every pull that
adds a migration:**

```bash
alembic upgrade head            # create / update the schema
alembic current                 # the applied revision
alembic history                 # all revisions
alembic check                   # fails if the models and the migrations disagree
```

| Revision | |
|---|---|
| `0001` initial schema | The five tables the app created itself before Alembic (`call_jobs`, `call_results`, `customers`, `users`, `auth_sessions`). Tables that already exist are skipped, so a database created by an older version upgrades in place with no `alembic stamp`. Not reversible |
| `0002` domain model | `organizations`, `agents`, `contacts`, `workflows`, plus a nullable `organization_id` on `users`, `call_jobs` and `call_results`. Additive only: no existing row is changed |
| `0003` call job domain links | Nullable `agent_id`, `contact_id`, `workflow_id` on `call_jobs` (with `organization_id` from 0002). Each is a composite foreign key with `organization_id`, so a job can never point at another organization's agent, contact or workflow; a CHECK forbids a link without an organization. Adds `UNIQUE (id, organization_id)` to `contacts` and `workflows` (the keys the foreign keys point at). Additive only: existing jobs keep every value and have no links |

Changing a model:

```bash
alembic revision --autogenerate -m "describe the change"
```

Then **read the generated file** before applying it: fix anything that should not be there (and
anything dialect-specific it inlined), and never edit a revision that has been applied elsewhere. A new
model module must be imported from `app/models/__init__.py` or autogenerate will not see it. SQLite
(development files) uses Alembic's batch mode; on PostgreSQL the same operations are plain `ALTER`s.

Only a throwaway in-memory SQLite database (the test suite's default) builds its own tables.

## Data model

```
Organization
├── Users              users.organization_id         (nullable: users created before tenancy have none)
├── Agents             one generic configuration shape for every industry
│   └── Workflows      when/why an agent acts; configuration only, nothing runs them yet
├── Contacts           domain data lives in `metadata` (JSON), never in columns
└── Calls / Call jobs  call_jobs.organization_id, call_results.organization_id (nullable)
```

- `organization_id` is on every tenant-owned table, indexed. A workflow points at its agent through
  `(agent_id, organization_id)`, so the **database** refuses a workflow that references another
  organization's agent. Names are unique per organization (agents, workflows), not globally.
- Flexible fields (`target_users`, `primary_tasks`, `behavior_config`, `instructions`, contact
  `metadata`, workflow `trigger_config` / `conditions` / `action_config` / `retry_policy`) are
  `JSONB` on PostgreSQL. Statuses are text with a `CHECK` constraint (`app/models/enums.py`).
- New tables use timezone-aware timestamps set by the database; the older tables keep their epoch floats.
- The models are in `app/models/`; request/response shapes are in `app/schemas/`. There are no API
  endpoints for them yet, and no tenant-scoped authorization: nothing filters by organization yet.

## Workflow engine

`app/workflows/` decides whether a workflow should call a contact, and creates the call job. It is a
plain service: no scheduler, no endpoints, no telephony. A scheduler (a later step) will call it.

```
evaluate_workflow(workflow, contact, now)      pure: due? eligible? why not?   -> Evaluation
WorkflowEngine(service).run(workflow_id, now)  the same for every contact, then create the due jobs
                                               -> [WorkflowOutcome]
WorkflowEngine(service).evaluate_contacts(...) who would be called? creates nothing
```

`now` is always passed in and must be timezone-aware. Nothing reads the clock.

**Trigger** (`workflow.trigger_type = "date_offset"`, the only type so far): due on a date stored in the
contact's `metadata`, plus an offset, from a time until the end of that day.

```json
{"reference_field": "appointment_date", "offset_days": -1, "time": "10:00", "timezone": "Asia/Kolkata"}
```

`time` defaults to `00:00` and `timezone` to `UTC`. The reference value is an ISO date or datetime. There
is one execution per contact per date: before `time` it is not due yet, after that day it has passed.

**Action** (`workflow.action_config`): `profile_id` and `reason` are required; `channel` (default `phone`),
`callback_url`, `ring_timeout_seconds` and `max_duration_seconds` are optional. Nothing else is accepted.

**Contact eligibility**, first failure wins: status is `active`; `consent_status` is `granted`
(`revoked` is reported as `opted_out`, `unknown` never calls); the phone exists and looks like a phone number
(international `+country` format for the `phone` channel); the time is inside `preferred_contact_time`
(`{"start": "10:00", "end": "18:00", "timezone": "..."}`), if set. A workflow must also be `active`, its agent
`active` and its organization not suspended. Workflow `conditions` are not evaluated yet, so a workflow
that has any does nothing rather than ignore them.

**Call jobs** are created with `Service.create_job(request, dispatch=False)`: same validation, tenant
checks and `reference` idempotency as any job, linked to the organization, agent, contact and workflow, but
left in the new `scheduled` status. Only the engine does this; every other caller dispatches as before.
Nothing rings, answers or expires a `scheduled` job, and no answer link or token is issued for one. The
exact steps a dispatcher must take to place it (re-check eligibility, claim atomically, reset `expires_at`,
then dial) are the DISPATCH CONTRACT in `app/outbound.py`.

**Idempotency:** the reference `wf:<workflow_id>:contact:<contact_id>:<execution date>` names one execution,
and `call_jobs.reference` is unique, so the same execution can only ever be one job, however often or
concurrently it is evaluated. A lookup before creating is only a shortcut. If the reference is held by a
job that is not this workflow's own (references are chosen by API callers), the outcome is
`reference_conflict` and nothing is created or claimed.

**Time:** the due moment is compared as an instant, never as wall-clock time. On a daylight-saving day a
time that does not exist (02:30 when clocks skip 02:00 to 03:00) is due at the equivalent instant (03:30),
and an ambiguous one (01:30 twice) at its first occurrence, whatever zone `now` is written in.

## Dispatching scheduled jobs

`app/workflows/dispatcher.py` turns a `scheduled` job into a ringing call, if the call should still happen. Like
the workflow engine it is a plain service: no loop, no endpoints, no scheduler, no telephony imports.

```
JobDispatcher(service).dispatch_scheduled_jobs(now, limit=50) -> DispatchReport
   selected, dispatched, skipped, cancelled, failed, and one DispatchOutcome per job
```

For each scheduled `phone` job, oldest first (`limit` bounds the query; the `status` index serves it):

1. Load the job's own organization, agent, contact and workflow (never ids from anywhere else).
2. Run the workflow engine's `evaluate_workflow` again at `now`. Not actionable: the job is **closed**. The
   one exception is `outside_contact_window`, which leaves it **scheduled** for a later run.
3. Check the job is still the same execution: its reference must be the one the evaluator's execution key
   gives. If the workflow is now due for a different one (the appointment moved) the job is closed as
   `execution_mismatch`.
4. **Claim** it with one conditional `UPDATE ... WHERE status = 'scheduled'` that also sets
   `expires_at = now + the job's own ring timeout` and a fresh `answer_token`. Only one dispatcher wins.
5. The winner calls the existing `Service._dial`. The claim comes first, so a crash before the call
   leaves a ringing job with no call that the sweeper ends as `no_answer`: a missed call, never two.

Closing a job uses the existing finish path: status `failed`, `end_reason` = the reason (for example
`opted_out`, `contact_inactive`, `workflow_inactive`, `window_passed`, `execution_mismatch`), and the
signed `call_job.finished` callback if the job has a `callback_url`. `failed` is the existing final status
for a call that was not placed; a job that never had a call has no `twilio_call_sid`.

Twilio refusing the call ends the job as `failed` / `telephony_error`, as it always has. Any other error
while dialing leaves the job ringing until its ring timeout, because whether Twilio placed the call is unknown.

## Workflow scheduler

`app/workflows/scheduler.py` runs the workflow engine and the dispatcher on an interval, inside the API
process. It is orchestration only: it never judges eligibility, computes a date, claims or creates a job, or
touches telephony. It calls `WorkflowEngine` and `JobDispatcher`, which remain the only source of truth.

```
every interval, at one shared `now`:
  1. WorkflowEngine.run(id, now) for each active workflow   -> scheduled jobs are created
  2. JobDispatcher.dispatch_scheduled_jobs(now, limit)      -> due jobs are claimed and dialed
```

The order matters: a job the first phase schedules can be placed in the same tick. `run_once(now)` is one tick
and returns a `SchedulerReport`; the background loop is a thin wrapper around it (`start()` / `stop()`). Ticks
never overlap: the interval is the pause after one finishes.

**Off by default.** Turning it on lets this process ring people on its own, so it takes
`WORKFLOW_SCHEDULER_ENABLED=true`. The test suite pins it off (`tests/conftest.py`), whatever `.env` says.

| Setting | Default | |
|---|---|---|
| `WORKFLOW_SCHEDULER_ENABLED` | `false` | |
| `WORKFLOW_SCHEDULER_INTERVAL_SECONDS` | `60` | never below 60 |
| `WORKFLOW_SCHEDULER_WORKFLOW_LIMIT` | `100` | active workflows run per tick |
| `WORKFLOW_SCHEDULER_DISPATCH_LIMIT` | `100` | scheduled jobs looked at per tick |

**Failures are isolated and visible.** A workflow that raises is logged (with its traceback) and recorded in the
report, and the other workflows and the dispatch run still happen. If the workflows cannot be listed, or the
dispatch run fails, that is recorded too. `SchedulerReport.ok` is true only if nothing failed, and a tick with
any failure is logged at ERROR. Logs carry counts and ids, never phone numbers, tokens or keys.

**Shutdown** lets a tick in flight finish (up to 30 seconds), then cancels it. Cancelling cannot double-dial:
a job claimed but not yet dialed is ended by the ring-timeout sweeper as `no_answer` (a missed call, never two).

**Several schedulers need no lock**, whether several processes or one that overlaps another. They may evaluate
the same workflow at the same moment, but `call_jobs.reference` is unique so the engine still makes one job per
execution, and the dispatcher's conditional `UPDATE ... WHERE status = 'scheduled'` lets exactly one of them
place a job. There is deliberately no other coordination.

**Limits, left as they are:**
- `WorkflowEngine.run` walks every contact of a workflow's organization in one go (no paging); a very large
  contact list makes a tick slow.
- The dispatcher selects oldest-first. Jobs that are waiting for a contact's window stay at the front, so a
  `WORKFLOW_SCHEDULER_DISPATCH_LIMIT` smaller than the number of such jobs can keep re-selecting them and
  never reach a ready one behind them (head-of-line blocking; `tests/test_scheduler.py` demonstrates it).
  Keep the limit well above the number of jobs that can be waiting on a window; a cursor is future work.
- If more workflows are active than the workflow limit, the highest ids are not run, and the report says so.

## The automated call, end to end

```
active workflow -> scheduler -> workflow engine -> scheduled job -> dispatcher -> ringing job
   -> existing telephony -> answer -> call -> ResultRow -> final job state
```

`tests/test_automated_call_lifecycle.py` drives exactly this, through the real HTTP and WebSocket endpoints and the
fake phone network of the telephony tests (no real call, credential or speech service). The states a workflow's job
passes through are the existing ones: `scheduled` (recorded, inert) -> `ringing` (claimed by the dispatcher, then
dialed by `_dial`) -> `in_progress` (the callee answered on the audio stream) -> `completed` / `wrong_party`; or
`no_answer` (nobody picked up, or the ring timeout), or `failed` (Twilio refused, or the dispatcher closed it before
any call: see `end_reason`). Once ringing, a workflow's job is indistinguishable from a manual one: a test runs the
same call through both and compares every column that defines it and the whole result.

- **Links.** `organization_id`, `agent_id`, `contact_id` and `workflow_id` are set when the job is scheduled and are
  never touched again; they are the same at every state, and are in the job API and the result callback.
- **Result <-> job.** `ResultRow.job_id` is the job's id (a soft link, as before) and `ResultRow.organization_id` is
  now filled from the job, so results can be listed per organization. A job that never had a call (closed by the
  dispatcher, no answer, Twilio failure) has no ResultRow; the job API still shows a small synthetic result for it.
- **Logs** follow one job by id: `job=... organization=... workflow=... agent=... contact=... reference=wf:...`, on the
  engine's creation, the dispatcher's placing, the answer, the finish, and a close without a call. Never a phone
  number, name, token, key or callback URL.
- **Transactions.** Every repository call is its own short transaction, committed before the next step. Nothing holds a
  transaction across a Twilio call: the dispatcher's claim commits before `_dial`, and finishing a call saves the result
  and then moves the job in two separate commits.

**Limitations of the existing system that the automated path inherits (not addressed here):**
- (Was: automated calls ran on the profile's demo data. Fixed: see "Domain-aware voice sessions" below.)
- A call in progress is an in-memory session. If the process dies mid-call the job stays `in_progress` forever and no
  result is saved; nothing ends such a job (a test pins this).
- Call History (`CallHistory.jsx`) lists jobs and results, but shows no workflow, contact or agent, and a `scheduled`
  job gets the "warn" badge because the badge map does not know the status. The data is in `GET /api/call-jobs`.

## Domain-aware voice sessions

When the callee answers, `Service._answer` chooses how the call is conducted:

| Job | Conducted from |
|---|---|
| names an `agent_id` and a `contact_id` (every workflow job does) and no `customer_ref` | its **domain context**: the organization, agent, contact, workflow and the call's reason |
| names a `customer_ref` (with or without domain links) | that customer record and the profile, exactly as before |
| anything else (manual jobs, legacy jobs, a job with only some links) | the profile, exactly as before |

For a domain call `app/voice_context.py` loads the job's own records and copies them, field by field, into a plain
frozen `VoiceSessionContext` (never a model object). Every record must exist and all must belong to the job's
organization, or no session is started: the job ends `failed` with `end_reason = domain_context_invalid` through the
ordinary path (callback included) and the phone line is hung up.

**Where each thing goes.** The split follows an order of authority: the fixed voice, fact, action and safety rules,
then the agent's configuration, then the workflow and the reason for the call, then the contact's data.

- **The system prompt** (after the fixed rules) gets the agent's name, role, organization, purpose, language, who it
  speaks with, its main tasks, its `behavior_config` and `instructions`, and the workflow name and the reason for the
  call. Names and reasons are single-line; multi-line guidance is indented so a line of data can never look like a
  heading; keys are sorted, so the prompt is the same on PostgreSQL (JSONB does not keep key order) and SQLite.
- **The contact's record** (name, preferred language and `metadata`) is **not in the prompt.** It is the result of the
  one tool a domain call has, `get_call_context`, so it reaches the model as data, is labelled as data, and stays
  behind the identity gate: nothing about the callee is readable until they confirm who they are. A note in the
  prompt says it is data and never instructions.
- **Not carried at all:** phone number, email, consent, status, preferred contact time, the agent's voice and
  status, the workflow's trigger, conditions and action configuration (callback URL included), the answer token, keys.
- Text is cleaned of control characters and limited in size and depth (`voice_context.py`).

The profile's demo data, tools and actions are **not** used on a domain call (they read and change data the call
does not have). The profile still supplies the plumbing: its id and name in the result, its wording ("customer"),
its next steps. The organization named on the call is the organization's, not the profile's.

Limits: a domain call has no actions (it can inform and end); there is nothing to write back. `ring_info` (the web
answer page) still shows the profile's organisation. The sentence splitter cuts "Dr. Sharma" after "Dr.", which
does not change the words but can change the pacing. An agent's `voice` is not used yet.

## Telephony control plane

The application talks to a telephony provider only through `TelephonyAdapter` (`app/telephony/base.py`):

```
Service -> TelephonyAdapter -> TwilioTelephony -> TwilioClient (REST) / TwiML / signed stream token / webhook check
```

| Operation | Interface | Twilio implementation |
|---|---|---|
| place a call | `place_call(PlaceCall(job_id, to, ring_seconds)) -> ProviderCall(provider, provider_call_id)` | TwiML with a signed, job-bound stream token, then the REST call |
| end a call | `hang_up(ProviderCall)` | REST: complete the call |
| read a provider callback | `parse_event(WebhookRequest) -> CallEvent \| None`, raising `InvalidWebhook` if it is not genuine | signature check against the exact public URL, then `CallStatus` mapped to a neutral kind |

`CallEvent.kind` is one of `ringing, answered, no_answer, busy, failed, completed`, and `Service.handle_call_event`
acts on it: no answer or busy ends the job `no_answer`, failed ends it `failed` (`telephony_error`), and completed
finalizes a live call whose line the provider says is gone. A provider's own errors subclass `TelephonyError`.

`Telephony` (`app/telephony/__init__.py`) is the phone runtime's bundle: the `adapter` above (Twilio's, built for you,
unless another is passed) plus the media-plane pieces the audio route uses. Importing `app.telephony.base` loads no
provider. Two rules are enforced by tests: the workflow layer imports no provider, and `Service` does not import
Twilio. The job's `twilio_call_sid` column still holds the provider's call id (renaming it needs a migration and
waits for a later step). This is only the control plane: audio, turn-taking and the Twilio Media Streams protocol
are unchanged.

## Voice runtime (legacy by default, Pipecat opt-in)

Which runtime conducts a phone call is chosen once, when the call starts (`app/voice_runtime_factory.py`):

- `VOICE_RUNTIME=legacy` (default): `PhoneCall`, unchanged.
- `VOICE_RUNTIME=pipecat`: the same conversation on a Pipecat pipeline (`app/pipecat_runtime/`). Needs
  `pip install -r requirements-pipecat.txt` (exactly `pipecat-ai==1.11.0`). `Session.process_turn` still owns all
  conversation policy; Pipecat only carries frames. Recognition and speech are the same Deepgram classes.
- `VOICE_RUNTIME_PIPECAT_AGENT_IDS=3,7`: with `pipecat`, only those agents' calls use it (empty = every call).
- `VOICE_PIPECAT_VAD=off|observe` (default `off`; only with `VOICE_RUNTIME=pipecat`). `observe` is **experimental and
  observation-only**: it runs Pipecat's Silero VAD next to the call and records when speech started and stopped (in memory,
  no text, no audio, nothing stored). It does **not** affect turn detection, interruptions or anything else the call does:
  Deepgram's `speech_final` / `UtteranceEnd` still end every utterance, and the session's own rules still decide barge-in.
  If the VAD cannot be created, the call is conducted by the legacy runtime and the fallback is logged.
- `VOICE_PIPECAT_STT=compat|native` (default `compat`; only with `VOICE_RUNTIME=pipecat`). `compat` is the existing,
  reference recognizer: `RecognizerProcessor` wrapping the existing Deepgram Listener, unchanged since Step 10. `native`
  is **experimental**: Pipecat's own `DeepgramSTTService` (`app/pipecat_runtime/native_stt.py`), configured to match
  `compat` as closely as that service allows (same model, `endpointing`, `utterance_end_ms`, smart formatting, interim
  results), with a small adapter that turns its frames into the exact same ones `SessionProcessor` already consumes.
  Needs `deepgram-sdk==7.10.0` (also in `requirements-pipecat.txt`; pulled in by native STT only). If native STT cannot
  be built (no `deepgram-sdk`, bad settings), the call is conducted by `compat` instead, before any audio.

If Pipecat is asked for but not installed (or is another version) the call is conducted by the legacy runtime and a
warning is logged. A running call is never moved to the other runtime.

## Media runtime

The audio of a call reaches the conversation through a provider-neutral boundary (`app/media.py`,
`app/voice_runtime.py`):

```
callee <-> provider <-> MediaLink <-> VoiceRuntime <-> Session.process_turn
                        (audio frames)  (turn-taking)   (all conversation policy)
```

- **`AudioFrame`**: 16-bit PCM, a sample rate and a channel count. No codec, no provider, no stream id.
- **`MediaLink`** (one call's audio connection): `audio_in()` yields the callee's frames until the call ends,
  `send_audio(frame)`, `clear_playout()` (the callee spoke over it), `checkpoint_playout()` with
  `on_playout_reached(callback)` (tell me when everything sent so far has been heard), and `close()` (hang up).
  It knows nothing of organizations, agents, contacts, workflows or jobs.
- **`VoiceRuntime.run(session, link, greeting)`**: conducts the call until the media ends, records it either way.

`process_turn` still owns the conversation policy: guardrails, identity gate, confirmation gate, tool authorization,
output vetting, transcript and audit, the brain. A runtime hands it utterances and speaks what it returns; it never
decides what may be said and never wires audio straight to a language model. Tests enforce that the runtime code
does not name any of that policy.

Today's implementation is `LegacyVoiceRuntime` (`app/telephony/legacy_runtime.py`): the existing `PhoneCall`,
**unchanged**, behind a thin bridge from `MediaLink` to `PhoneCall`'s own `Line`. Twilio's side is
`TwilioMediaLink` (`app/telephony/media.py`): the Media Streams JSON messages, base64 8 kHz mu-law (`mulaw.py`, a
small G.711 codec), `mark` and `clear`, and hanging up through the telephony adapter all live there, and nowhere
in the shared interfaces. The stream route (`telephony/routes.py`) is still Twilio-specific: it does the Twilio
handshake, then hands the call to the runtime.

Known cost of the seam: Deepgram is still configured for mu-law, so on the phone path audio is decoded to PCM and
encoded back (0.3 ms per 200 ms of audio, and lossless: every mu-law code round-trips except the duplicate zero).
That bridge goes away when a recognizer and synthesizer that speak PCM are used.

## Run

```bash
source .venv/bin/activate
alembic upgrade head                                         # create the schema (first run, and after updates)
python -m app.cli create-user you@example.com --role admin   # nobody can sign in until you do this
python -m app.cli seed-demo                                  # optional: one demo customer per profile
uvicorn app.main:app --reload --port 8000

# in another terminal
cd frontend && npm run dev      # Vite proxies /api to :8000
```

Without `GEMINI_API_KEY` the server still starts. `/api/health` reports `"brain": null`, and
the browser falls back to its built-in local runtime, so the demo works with no key.

## Health check

```bash
curl localhost:8000/health
# {"status":"ok"}
```

`GET /health` is liveness only: it answers as long as the process is serving and does not touch
the database, the LLM or telephony. (`GET /api/health` is what the console uses; it also reports
the brain, profiles and whether sign-in is required.)

## Tests

```bash
pytest
```

No key, network or running PostgreSQL is needed: tests use in-memory SQLite, except the ones that
check the server survives an unreachable PostgreSQL, which point at a closed port.

## Errors and logging

Expected failures are `HTTPException`s (`{"detail": "..."}`). Anything else is caught centrally
(`app/errors.py`): an unreachable database is a `503`, every other unhandled exception is a `500`
with `{"detail": "Internal server error"}`. Responses never carry the cause; the traceback goes to the
log, labelled with the route template rather than the concrete path (call ids in paths are bearer
tokens). Logging is standard `logging`, configured by `LOG_LEVEL` (`app/logging_setup.py`).

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
| POST | `/api/call-jobs` | API key | Create a job. Same `reference` again returns the same job (200); the same reference with a different payload is a 409. Optional `organization_id`, `agent_id`, `contact_id`, `workflow_id` link the job to the domain model (the last three need `organization_id` and must belong to it, else 422) |
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
  customers are in the database, and the schema comes from Alembic only (see Migrations).
- If the database is down while the server starts, callback deliveries left pending by a previous
  run are not resumed until the next restart (the failure is logged).
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
