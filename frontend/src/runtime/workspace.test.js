import assert from "node:assert/strict";
import test from "node:test";
import {
  agentPerformance,
  averageDuration,
  callsByDay,
  callsByHour,
  enrichCalls,
  filterEntries,
  formatDuration,
  liveActivity,
  notificationsFrom,
  outcomeMix,
  relativeTime,
  searchEntries,
  startOfDay,
  successRate,
  todayStats,
  unreadCount,
  withinDays,
} from "./workspace.js";

// All times are built from local calendar fields, so the tests hold in any time zone.
const at = (day, hour = 12, minute = 0) => new Date(2026, 8, day, hour, minute).getTime();
const NOW = at(25, 15, 0);

const agents = [
  { id: 1, name: "CareCall", role: "Coordinator", industry: "Healthcare", status: "active" },
  { id: 2, name: "Renewals", role: "Reminder", industry: "Finance", status: "active" },
  { id: 3, name: "Front Desk", role: "Receptionist", industry: "Reception", status: "draft" },
];

const jobs = [
  { job_id: "J1", status: "completed", callee: { name: "Priya" }, agent_id: 1, created_at: new Date(at(25, 9)).toISOString(), call_id: "C1" },
  { job_id: "J2", status: "completed", callee: { name: "Rahul" }, agent_id: 2, created_at: new Date(at(25, 10)).toISOString(), call_id: "C2" },
  { job_id: "J3", status: "ringing", callee: { name: "Anita" }, agent_id: 1, created_at: new Date(at(25, 14, 58)).toISOString() },
  { job_id: "J4", status: "in_progress", callee: { name: "Vikram" }, agent_id: 2, created_at: new Date(at(25, 14, 50)).toISOString() },
  { job_id: "J5", status: "scheduled", callee: { name: "Sara" }, agent_id: 1, created_at: new Date(at(25, 8)).toISOString() },
  { job_id: "J6", status: "failed", callee: { name: "Imran" }, agent_id: 1, created_at: new Date(at(24, 9)).toISOString(), finished_at: new Date(at(24, 9, 1)).toISOString() },
  { job_id: "J7", status: "no_answer", callee: { name: "Divya" }, agent_id: 2, created_at: new Date(at(23, 9)).toISOString(), finished_at: new Date(at(23, 9, 2)).toISOString() },
];

const calls = [
  { call_id: "C1", started_at: new Date(at(25, 9)).toISOString(), duration_seconds: 120, outcome: "action_completed", job_id: "J1", callee_name: "Priya" },
  { call_id: "C2", started_at: new Date(at(25, 10)).toISOString(), duration_seconds: 60, outcome: "completed", job_id: "J2", callee_name: "Rahul" },
  { call_id: "C3", started_at: new Date(at(25, 11)).toISOString(), duration_seconds: 30, outcome: "identity_not_confirmed", job_id: null, callee_name: null },
  { call_id: "C4", started_at: new Date(at(24, 16)).toISOString(), duration_seconds: 200, outcome: "completed", job_id: "J1", callee_name: "Priya" },
  { call_id: "C5", started_at: new Date(at(20, 9)).toISOString(), duration_seconds: 90, outcome: "wrong_party", job_id: "J2", callee_name: "Rahul" },
  { call_id: "BAD", started_at: "not a date", outcome: "completed" },
];

const enriched = enrichCalls(calls, jobs, agents);

test("enrichCalls joins the agent through the job, sorts newest first and drops undated rows", () => {
  assert.equal(enriched.length, 5);
  assert.deepEqual(enriched.map((call) => call.call_id), ["C3", "C2", "C1", "C4", "C5"]);
  assert.equal(enriched.find((call) => call.call_id === "C1").agentName, "CareCall");
  assert.equal(enriched.find((call) => call.call_id === "C2").agentName, "Renewals");
  assert.equal(enriched.find((call) => call.call_id === "C3").agentName, null); // no job, no agent
  assert.equal(enriched.find((call) => call.call_id === "C1").success, true);
  assert.equal(enriched.find((call) => call.call_id === "C3").success, false);
});

test("a call whose job is gone or has no agent keeps working", () => {
  const [call] = enrichCalls([{ call_id: "X", started_at: new Date(NOW).toISOString(), job_id: "MISSING", outcome: "completed" }], jobs, agents);

  assert.equal(call.agentName, null);
  assert.equal(call.agentId, null);
});

test("successRate is null for no calls, never NaN", () => {
  assert.equal(successRate([]), null);
  assert.equal(successRate(enriched.slice(0, 3)), 2 / 3);
});

test("todayStats counts only today, adds in-flight jobs to calls, and reports live", () => {
  const stats = todayStats({ calls: enriched, jobs, agents, now: NOW });

  assert.equal(stats.activeAgents, 2); // the draft agent is not active
  assert.equal(stats.liveNow, 2); // ringing + in_progress
  assert.equal(stats.callsToday, 3 + 2); // 3 finished today + 2 live
  assert.equal(stats.completed, 2); // C1 + C2
  assert.equal(stats.tasks, 1); // only the action_completed one
  assert.equal(stats.successRate, 2 / 3);
});

test("todayStats has seven days of trend, ending today", () => {
  const { trend } = todayStats({ calls: enriched, jobs, agents, now: NOW });

  assert.equal(trend.calls.length, 7);
  assert.equal(trend.calls.at(-1), 3);
  assert.equal(trend.calls.at(-2), 1); // C4 yesterday
  assert.equal(trend.tasks.at(-1), 1);
  assert.equal(trend.successRate.at(-3), null); // no calls that day
});

test("todayStats with nothing at all is all zeros and a null rate", () => {
  const stats = todayStats({ calls: [], jobs: [], agents: [], now: NOW });

  assert.equal(stats.callsToday, 0);
  assert.equal(stats.successRate, null);
  assert.equal(stats.activeAgents, 0);
});

test("callsByDay covers every day in the range, zero-filled, oldest first", () => {
  const days = callsByDay(enriched, 7, NOW);

  assert.equal(days.length, 7);
  assert.equal(days.at(-1).total, 3);
  assert.equal(days.at(-1).success, 2);
  assert.equal(days.at(-1).other, 1);
  assert.equal(days.at(-2).total, 1);
  assert.equal(days.at(-3).total, 0);
  assert.equal(days.reduce((sum, day) => sum + day.total, 0), 5); // 20 Sep is 5 days back: inside 7
  assert.ok(days[0].start < days.at(-1).start);
});

test("callsByDay ignores calls outside the range", () => {
  assert.equal(callsByDay(enriched, 3, NOW).reduce((sum, day) => sum + day.total, 0), 4); // not 20 Sep
});

test("withinDays keeps today and the days before it", () => {
  assert.equal(withinDays(enriched, 1, NOW).length, 3);
  assert.equal(withinDays(enriched, 2, NOW).length, 4);
  assert.equal(withinDays(enriched, 30, NOW).length, 5);
});

test("outcomeMix counts, sorts by frequency and labels", () => {
  const mix = outcomeMix(enriched);

  assert.equal(mix[0].count, 2);
  assert.equal(mix[0].outcome, "completed");
  assert.equal(mix[0].label, "Completed");
  assert.equal(mix.find((entry) => entry.outcome === "identity_not_confirmed").label, "Identity not confirmed");
  assert.equal(mix.reduce((sum, entry) => sum + entry.count, 0), 5);
  assert.equal(mix.find((entry) => entry.outcome === "wrong_party").success, false);
});

test("an unknown outcome still gets a readable label", () => {
  assert.equal(outcomeMix([{ outcome: "callback_requested", success: false }])[0].label, "Callback requested");
});

test("agentPerformance keeps agents with no calls and computes each rate", () => {
  const performance = agentPerformance(enriched, agents);
  const care = performance.find((row) => row.agent.name === "CareCall");
  const desk = performance.find((row) => row.agent.name === "Front Desk");

  assert.equal(care.calls, 2); // C1, C4
  assert.equal(care.rate, 1);
  assert.equal(desk.calls, 0);
  assert.equal(desk.rate, null);
  assert.equal(desk.lastAt, null);
  assert.equal(performance.at(-1).agent.name, "Front Desk"); // fewest calls last
  assert.equal(care.lastAt, at(25, 9));
});

test("callsByHour buckets by local hour", () => {
  const hours = callsByHour(enriched);

  assert.equal(hours.length, 24);
  assert.equal(hours[9], 2); // C1 and C5 both start at 09:00 (different days)
  assert.equal(hours[16], 1);
});

test("averageDuration ignores calls with no time and is null when nothing is timed", () => {
  assert.equal(averageDuration([{ duration_seconds: 60 }, { duration_seconds: 120 }, { duration_seconds: null }]), 90);
  assert.equal(averageDuration([]), null);
});

test("liveActivity lists in-progress first, then ringing, then queued, in words", () => {
  const live = liveActivity(jobs, agents);

  assert.deepEqual(live.map((item) => item.status), ["in_progress", "ringing", "scheduled"]);
  assert.equal(live[0].text, "On a call with Vikram");
  assert.equal(live[0].agentName, "Renewals");
  assert.equal(live[1].text, "Calling Anita…");
  assert.equal(live[0].live, true);
  assert.equal(live[2].live, false);
});

test("liveActivity respects its limit and skips finished jobs", () => {
  assert.equal(liveActivity(jobs, agents, 1).length, 1);
  assert.equal(liveActivity([{ job_id: "Z", status: "completed" }], agents).length, 0);
});

test("formatDuration", () => {
  assert.equal(formatDuration(0), "0:00");
  assert.equal(formatDuration(65), "1:05");
  assert.equal(formatDuration(125.4), "2:05");
  assert.equal(formatDuration(null), "–");
  assert.equal(formatDuration(-3), "–");
});

test("relativeTime", () => {
  assert.equal(relativeTime(NOW - 20_000, NOW), "just now");
  assert.equal(relativeTime(NOW - 5 * 60_000, NOW), "5 min ago");
  assert.equal(relativeTime(at(25, 12), NOW), "3 h ago");
  assert.equal(relativeTime(at(24, 22), NOW), "Yesterday");
  assert.equal(relativeTime(at(20, 9), NOW), "20 Sept".replace("Sept", new Date(at(20)).toLocaleDateString("en-GB", { month: "short" })));
  assert.equal(relativeTime(null, NOW), "–");
  assert.equal(relativeTime(NOW + 60_000, NOW), "just now"); // a slightly-future timestamp (clock skew) is not negative
});

test("notifications: trouble only, newest first, each pointing at something real", () => {
  const items = notificationsFrom({ jobs, calls: enriched, agents });

  assert.deepEqual(items.map((item) => item.id), ["call-C3", "job-J6", "job-J7", "call-C5"]);
  assert.equal(items[1].tone, "danger");
  assert.equal(items[1].text, "CareCall could not place the call to Imran");
  assert.equal(items[2].text, "Divya did not answer Renewals");
  assert.equal(items[0].to, "/app/calls/C3");
  assert.equal(items[2].to, "/app/calls"); // a job with no call yet points at the list
});

test("notifications: a wrong-party job is not reported twice", () => {
  const twice = notificationsFrom({ jobs: [{ job_id: "W", status: "wrong_party", created_at: new Date(NOW).toISOString(), callee: { name: "X" } }], calls: [], agents });

  assert.equal(twice.length, 0);
});

test("unreadCount counts what is newer than the last time it was looked at", () => {
  const items = notificationsFrom({ jobs, calls: enriched, agents });

  assert.equal(unreadCount(items, 0), 4);
  assert.equal(unreadCount(items, at(24, 12)), 1); // only C3, today
  assert.equal(unreadCount(items, NOW), 0);
});

test("search finds agents, contacts, workflows and calls, and knows where each goes", () => {
  const entries = searchEntries({
    agents,
    contacts: [{ id: 9, name: "Priya Sharma", phone: "+91 98", email: "priya@example.com" }],
    workflows: [{ id: 4, name: "Appointment reminder", status: "active", trigger_type: "date_offset" }],
    calls: enriched,
  });

  assert.equal(filterEntries(entries, "care")[0].to, "/app/agents/1");
  assert.equal(filterEntries(entries, "priya sha")[0].to, "/app/contacts");
  assert.equal(filterEntries(entries, "appointment")[0].to, "/app/workflows");
  assert.ok(filterEntries(entries, "rahul").some((entry) => entry.to === "/app/calls/C2"));
});

test("search ranks titles that start with the query first, is case-insensitive, and empty means nothing", () => {
  const entries = [
    { id: "1", kind: "Agent", title: "Night Care", subtitle: "" },
    { id: "2", kind: "Agent", title: "CareCall", subtitle: "" },
    { id: "3", kind: "Agent", title: "Front", subtitle: "care team" },
  ];

  assert.deepEqual(filterEntries(entries, "CARE").map((entry) => entry.id), ["2", "1", "3"]);
  assert.deepEqual(filterEntries(entries, "   "), []);
  assert.deepEqual(filterEntries(entries, "zzz"), []);
});

test("startOfDay is local midnight", () => {
  const start = startOfDay(at(25, 23, 59));

  assert.equal(new Date(start).getHours(), 0);
  assert.equal(new Date(start).getDate(), 25);
});
