import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../../auth/context.js";
import { getJson } from "../../runtime/api.js";
import { loadConfig, toAgentPayload } from "../../runtime/agentConfig.js";
import { enrichCalls } from "../../runtime/workspace.js";
import { WorkspaceContext } from "./context.js";

const REFRESH_MS = 6000;

const EMPTY = { agents: [], contacts: [], workflows: [], jobs: [], rawCalls: [], organization: null };

// The one place the application reads its data. Every screen (dashboard, agents, calls,
// analytics, search, notifications) reads from here, so the numbers on one page always agree with
// the numbers on another, and a page never fires its own duplicate requests.
//
// It refreshes every few seconds while the tab is visible (that is what makes "live activity"
// live), keeps the previous data on screen while it reloads, and keeps what it already had for any
// one resource that fails, so a single flaky endpoint does not blank the whole workspace.
export default function WorkspaceProvider({ children }) {
  const { backend } = useAuth();
  const serverAvailable = backend.available;
  const [data, setData] = useState(EMPTY);
  const [status, setStatus] = useState(serverAvailable ? "loading" : "ready"); // loading | ready | error
  const [error, setError] = useState(null);
  const [now, setNow] = useState(() => Date.now());
  const [localTick, setLocalTick] = useState(0);
  const inFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (!serverAvailable) {
      setLocalTick((tick) => tick + 1); // offline: the "data" is the browser's own saved agent
      return;
    }

    if (inFlight.current) return;

    inFlight.current = true;

    try {
      const [agents, contacts, workflows, jobs, calls, organizations] = await Promise.allSettled([
        getJson("/api/agents"),
        getJson("/api/contacts"),
        getJson("/api/workflows"),
        getJson("/api/call-jobs?limit=100"),
        getJson("/api/calls?limit=200"),
        getJson("/api/organizations"),
      ]);
      const settled = [agents, contacts, workflows, jobs, calls, organizations];

      if (settled.every((result) => result.status === "rejected")) {
        setError(settled[0].reason?.message ?? "Could not reach the server.");
        setStatus("error");
        return;
      }

      const pick = (result, previous) => (result.status === "fulfilled" ? result.value : previous);

      setData((previous) => ({
        agents: pick(agents, previous.agents),
        contacts: pick(contacts, previous.contacts),
        workflows: pick(workflows, previous.workflows),
        jobs: pick(jobs, previous.jobs),
        rawCalls: pick(calls, previous.rawCalls),
        organization: organizations.status === "fulfilled" ? (organizations.value[0] ?? null) : previous.organization,
      }));
      setNow(Date.now());
      setError(null);
      setStatus("ready");
    } finally {
      inFlight.current = false;
    }
  }, [serverAvailable]);

  useEffect(() => {
    if (!serverAvailable) return undefined;

    const first = setTimeout(refresh, 0);
    const timer = setInterval(() => !document.hidden && refresh(), REFRESH_MS);
    const onVisible = () => !document.hidden && refresh();

    document.addEventListener("visibilitychange", onVisible);

    return () => {
      clearTimeout(first);
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [serverAvailable, refresh]);

  // Offline mode has no organization and no server-side agents: the one agent the browser has
  // saved (the pre-server behaviour) is shown as an agent, so the same screens still work.
  const agents = useMemo(() => {
    if (serverAvailable) return data.agents;

    const config = loadConfig();
    const payload = config ? toAgentPayload(config) : null;

    // localTick is read so a save in the wizard (which calls refresh) re-reads storage
    void localTick;

    return payload ? [{ id: "local", organization_id: 0, status: "active", local: true, purpose: null, ...payload }] : [];
  }, [serverAvailable, data.agents, localTick]);

  const calls = useMemo(() => enrichCalls(data.rawCalls, data.jobs, agents), [data.rawCalls, data.jobs, agents]);

  const value = useMemo(
    () => ({
      serverAvailable,
      status,
      error,
      agents,
      contacts: data.contacts,
      workflows: data.workflows,
      jobs: data.jobs,
      calls,
      organization: data.organization,
      now,
      refresh,
      agentById: (id) => agents.find((agent) => String(agent.id) === String(id)) ?? null,
    }),
    [serverAvailable, status, error, agents, data, calls, now, refresh],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}
