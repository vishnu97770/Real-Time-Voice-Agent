// Turns what the API returns (finished calls, call jobs, agents, contacts, workflows) into what
// the application screens show: numbers, chart series, live activity, notifications, search
// results. Pure functions only - no fetching, no clock of their own (`now` is always passed in) -
// so every figure on a dashboard can be checked without a browser.
//
// Shapes in (all from the existing endpoints, unchanged):
//   call    GET /api/calls          { call_id, started_at (ISO), duration_seconds, outcome, direction,
//                                     channel, job_id, callee_name, summary, profile_name }
//   job     GET /api/call-jobs      { job_id, status, callee: { name, phone }, reason, created_at (ISO),
//                                     finished_at, call_id, agent_id, contact_id, workflow_id, channel,
//                                     end_reason, callback: { status } }
//   agent   GET /api/agents         { id, name, role, industry, status, ... }

export const SUCCESS_OUTCOMES = ["completed", "action_completed"];
export const IN_FLIGHT = ["ringing", "in_progress"];
// A job that ended without the conversation happening, or that never got started properly.
export const TROUBLE_JOB_STATUSES = ["failed", "no_answer", "declined", "wrong_party"];

const DAY_MS = 24 * 60 * 60 * 1000;
const pad = (value) => String(value).padStart(2, "0");

const parse = (iso) => {
  const ms = typeof iso === "number" ? iso : Date.parse(iso ?? "");

  return Number.isNaN(ms) ? null : ms;
};

export const startOfDay = (ms) => {
  const date = new Date(ms);

  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
};

const dayKey = (ms) => {
  const date = new Date(ms);

  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
};

// Local calendar days, oldest first, ending with the day of `now`. Built by date arithmetic, not
// by subtracting 24h, so a daylight-saving change never repeats or skips a day.
function lastDays(now, count) {
  const today = new Date(startOfDay(now));

  return Array.from({ length: count }, (_, index) => {
    const date = new Date(today.getFullYear(), today.getMonth(), today.getDate() - (count - 1 - index));

    return { key: dayKey(date.getTime()), start: date.getTime(), label: date.toLocaleDateString("en-GB", { day: "numeric", month: "short" }), weekday: date.toLocaleDateString("en-GB", { weekday: "short" }) };
  });
}

// --- joining -------------------------------------------------------------------------------------

// Finished calls with the agent and time they belong to. A call links to its agent through the
// job that placed it; a call with no job (someone talking in the console) has no agent record.
export function enrichCalls(calls, jobs, agents) {
  const jobById = new Map(jobs.map((job) => [job.job_id, job]));
  const agentById = new Map(agents.map((agent) => [agent.id, agent]));

  return calls
    .map((call) => {
      const job = call.job_id ? jobById.get(call.job_id) : null;
      const agent = job?.agent_id != null ? agentById.get(job.agent_id) : null;
      const startMs = parse(call.started_at) ?? parse(call.finished_at ? call.finished_at * 1000 : null);

      return {
        ...call,
        startMs,
        agentId: agent?.id ?? null,
        agentName: agent?.name ?? null,
        contactName: call.callee_name ?? null,
        success: SUCCESS_OUTCOMES.includes(call.outcome),
      };
    })
    .filter((call) => call.startMs !== null)
    .sort((a, b) => b.startMs - a.startMs);
}

// --- numbers -------------------------------------------------------------------------------------

export const successRate = (calls) => (calls.length ? calls.filter((call) => call.success).length / calls.length : null);

const inDay = (calls, dayStart) => calls.filter((call) => call.startMs >= dayStart && call.startMs < dayStart + DAY_MS);

// The dashboard's "today" row. `calls` are enriched finished calls; jobs give what is live now.
export function todayStats({ calls, jobs, agents, now }) {
  const today = startOfDay(now);
  const todays = calls.filter((call) => call.startMs >= today);
  const live = jobs.filter((job) => IN_FLIGHT.includes(job.status));
  const days = lastDays(now, 7);
  const perDay = (pick) => days.map((day) => pick(inDay(calls, day.start)));

  return {
    activeAgents: agents.filter((agent) => agent.status === "active").length,
    callsToday: todays.length + live.length,
    liveNow: live.length,
    completed: todays.filter((call) => call.success).length,
    tasks: todays.filter((call) => call.outcome === "action_completed").length,
    successRate: successRate(todays),
    trend: {
      calls: perDay((list) => list.length),
      completed: perDay((list) => list.filter((call) => call.success).length),
      tasks: perDay((list) => list.filter((call) => call.outcome === "action_completed").length),
      successRate: perDay((list) => successRate(list)),
    },
  };
}

// Calls per local day over the last `count` days: total, and how many succeeded.
export function callsByDay(calls, count, now) {
  return lastDays(now, count).map((day) => {
    const list = inDay(calls, day.start);
    const success = list.filter((call) => call.success).length;

    return { ...day, total: list.length, success, other: list.length - success };
  });
}

export const OUTCOME_LABELS = {
  completed: "Completed",
  action_completed: "Action completed",
  identity_not_confirmed: "Identity not confirmed",
  wrong_party: "Wrong party",
};

export const outcomeLabel = (outcome) => OUTCOME_LABELS[outcome] ?? String(outcome ?? "Unknown").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

// How calls ended, most common first.
export function outcomeMix(calls) {
  const counts = new Map();

  for (const call of calls) counts.set(call.outcome ?? "unknown", (counts.get(call.outcome ?? "unknown") ?? 0) + 1);

  return [...counts.entries()].map(([outcome, count]) => ({ outcome, label: outcomeLabel(outcome), count, success: SUCCESS_OUTCOMES.includes(outcome) })).sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

// Per agent: how many calls, how many succeeded, when it last worked. Agents with no calls are
// kept (with a null rate) so a new agent shows up instead of vanishing from the list.
export function agentPerformance(calls, agents) {
  return agents
    .map((agent) => {
      const own = calls.filter((call) => call.agentId === agent.id);

      return { agent, calls: own.length, success: own.filter((call) => call.success).length, rate: successRate(own), lastAt: own[0]?.startMs ?? null };
    })
    .sort((a, b) => b.calls - a.calls || a.agent.name.localeCompare(b.agent.name));
}

// 24 buckets: how many calls started in each hour of the day (local time).
export function callsByHour(calls) {
  const hours = Array.from({ length: 24 }, () => 0);

  for (const call of calls) hours[new Date(call.startMs).getHours()] += 1;

  return hours;
}

export function averageDuration(calls) {
  const timed = calls.filter((call) => Number.isFinite(call.duration_seconds) && call.duration_seconds > 0);

  return timed.length ? timed.reduce((sum, call) => sum + call.duration_seconds, 0) / timed.length : null;
}

// Restricts calls to the last `days` local days (including today).
export function withinDays(calls, days, now) {
  const from = lastDays(now, days)[0].start;

  return calls.filter((call) => call.startMs >= from);
}

// --- live activity -----------------------------------------------------------------------------------

const ACTIVITY = {
  ringing: (who) => `Calling ${who}…`,
  in_progress: (who) => `On a call with ${who}`,
  scheduled: (who) => `Call to ${who} is queued`,
};

// What agents are doing right now: calls ringing or in progress, then queued ones.
export function liveActivity(jobs, agents, limit = 8) {
  const agentById = new Map(agents.map((agent) => [agent.id, agent]));
  const rank = { in_progress: 0, ringing: 1, scheduled: 2 };

  return jobs
    .filter((job) => job.status in ACTIVITY)
    .sort((a, b) => rank[a.status] - rank[b.status] || (parse(b.created_at) ?? 0) - (parse(a.created_at) ?? 0))
    .slice(0, limit)
    .map((job) => ({
      id: job.job_id,
      status: job.status,
      live: IN_FLIGHT.includes(job.status),
      agentName: agentById.get(job.agent_id)?.name ?? "Voice agent",
      text: ACTIVITY[job.status](job.callee?.name ?? "someone"),
      reason: job.reason ?? "",
      since: parse(job.created_at),
    }));
}

// --- time ------------------------------------------------------------------------------------------

export function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "–";

  const total = Math.round(seconds);

  return `${Math.floor(total / 60)}:${pad(total % 60)}`;
}

export function relativeTime(ms, now) {
  if (ms == null) return "–";

  const minutes = Math.floor(Math.max(0, now - ms) / 60000);

  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;

  const [yesterday, today] = lastDays(now, 2).map((day) => day.start);
  const day = startOfDay(ms);

  if (day === today) return `${Math.floor(minutes / 60)} h ago`;
  if (day === yesterday) return "Yesterday";

  return new Date(ms).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

// --- notifications -------------------------------------------------------------------------------------

// Things worth an operator's attention, from what is already loaded: calls that could not be
// placed or were not answered, and calls that ended without the right person confirmed. Newest
// first. Nothing is invented; every item points at a real job or call.
export function notificationsFrom({ jobs, calls, agents, limit = 20 }) {
  const agentById = new Map(agents.map((agent) => [agent.id, agent]));
  const items = [];

  for (const job of jobs) {
    if (!TROUBLE_JOB_STATUSES.includes(job.status) || job.status === "wrong_party") continue;

    const at = parse(job.finished_at) ?? parse(job.created_at);

    if (at === null) continue;

    const who = job.callee?.name ?? "someone";
    const agentName = agentById.get(job.agent_id)?.name ?? "A voice agent";
    const text = { failed: `${agentName} could not place the call to ${who}`, no_answer: `${who} did not answer ${agentName}`, declined: `${who} declined the call from ${agentName}` }[job.status];

    items.push({ id: `job-${job.job_id}`, at, tone: job.status === "failed" ? "danger" : "warn", text, to: job.call_id ? `/app/calls/${job.call_id}` : "/app/calls" });
  }

  for (const call of calls) {
    if (call.outcome !== "identity_not_confirmed" && call.outcome !== "wrong_party") continue;

    items.push({
      id: `call-${call.call_id}`,
      at: call.startMs,
      tone: "warn",
      text: call.outcome === "wrong_party" ? `${call.agentName ?? "The agent"} reached the wrong person (${call.contactName ?? "unknown"})` : `${call.contactName ?? "A caller"} could not be identified on a call with ${call.agentName ?? "the agent"}`,
      to: `/app/calls/${call.call_id}`,
    });
  }

  return items.sort((a, b) => b.at - a.at).slice(0, limit);
}

export const unreadCount = (items, lastSeenMs) => items.filter((item) => item.at > lastSeenMs).length;

// --- search ------------------------------------------------------------------------------------------------

// Everything the search box can open, flattened. Each entry knows where it goes.
export function searchEntries({ agents, contacts, workflows, calls }) {
  return [
    ...agents.map((agent) => ({ id: `agent-${agent.id}`, kind: "Agent", title: agent.name, subtitle: [agent.role, agent.industry].filter(Boolean).join(" · "), to: `/app/agents/${agent.id}` })),
    ...contacts.map((contact) => ({ id: `contact-${contact.id}`, kind: "Contact", title: contact.name, subtitle: [contact.phone, contact.email].filter(Boolean).join(" · "), to: "/app/contacts" })),
    ...workflows.map((workflow) => ({ id: `workflow-${workflow.id}`, kind: "Workflow", title: workflow.name, subtitle: `${workflow.status} · ${workflow.trigger_type.replace(/_/g, " ")}`, to: "/app/workflows" })),
    ...calls.slice(0, 60).map((call) => ({ id: `call-${call.call_id}`, kind: "Call", title: call.contactName ?? "Call", subtitle: [call.agentName, outcomeLabel(call.outcome)].filter(Boolean).join(" · "), to: `/app/calls/${call.call_id}` })),
  ];
}

// Case-insensitive match on title and subtitle; titles that start with the query rank first.
export function filterEntries(entries, query, limit = 10) {
  const needle = query.trim().toLowerCase();

  if (!needle) return [];

  return entries
    .map((entry) => {
      const title = entry.title.toLowerCase();
      const at = title.indexOf(needle);
      const inSubtitle = entry.subtitle.toLowerCase().includes(needle);

      return { entry, rank: at === 0 ? 0 : at > 0 ? 1 : inSubtitle ? 2 : null };
    })
    .filter((item) => item.rank !== null)
    .sort((a, b) => a.rank - b.rank || a.entry.title.localeCompare(b.entry.title))
    .slice(0, limit)
    .map((item) => item.entry);
}

// The application's pages, for the search box and the sidebar.
export const PAGES = [
  { id: "dashboard", label: "Dashboard", to: "/app/dashboard" },
  { id: "agents", label: "Agents", to: "/app/agents" },
  { id: "contacts", label: "Contacts", to: "/app/contacts" },
  { id: "workflows", label: "Workflows", to: "/app/workflows" },
  { id: "calls", label: "Calls", to: "/app/calls" },
  { id: "analytics", label: "Analytics", to: "/app/analytics" },
  { id: "settings", label: "Settings", to: "/app/settings" },
];
