import { useEffect, useMemo, useState } from "react";
import { useAuth } from "../../auth/context.js";
import { fromAgentPayload, resolveConfig } from "../../runtime/agentConfig.js";
import { buildCallRecord } from "../../runtime/callRecord.js";
import { fetchCustomers } from "../../runtime/customers.js";
import { DEFAULT_PROFILE_ID, PROFILES, getProfile } from "../../profiles/index.js";
import { useVoiceAgent } from "../../runtime/useVoiceAgent.js";
import { useWorkspace } from "../workspace/context.js";
import { ConsoleContext } from "./context.js";

const PROFILE_KEY = "voice-agent-profile";
const AGENT_KEY = "voice-agent-console-agent";

const read = (key) => {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
};

const write = (key, value) => {
  try {
    localStorage.setItem(key, value);
  } catch {
    // storage blocked: the choice still applies for this visit
  }
};

// Holds everything the voice console needs: which agent is on the line, which built-in profile
// supplies its data and tools, which customer record it may see, and the call itself.
export default function ConsoleProvider({ children }) {
  const { user } = useAuth();
  const { agents } = useWorkspace();
  const [profileId, setProfileId] = useState(() => read(PROFILE_KEY) || DEFAULT_PROFILE_ID);
  const [agentChoice, setAgentChoice] = useState(() => read(AGENT_KEY) || "");
  const [customerRef, setCustomerRef] = useState("");
  const [customers, setCustomers] = useState([]);
  const [recordView, setRecordView] = useState({ callId: null, view: "summary" });

  const profile = getProfile(profileId);

  // The chosen agent if it still exists, else the first active one, else the first agent, else
  // none (calls then use the built-in profile's own defaults, exactly as before).
  const agentRecord = useMemo(
    () => agents.find((agent) => String(agent.id) === agentChoice) ?? agents.find((agent) => agent.status === "active") ?? agents[0] ?? null,
    [agents, agentChoice],
  );
  const agentContext = useMemo(() => (agentRecord ? resolveConfig(fromAgentPayload(agentRecord)) : null), [agentRecord]);

  const agent = useVoiceAgent(profile, { customerRef, agentConfig: agentContext });
  const serverBrain = agent.brain.source === "server";
  const isActive = agent.callState !== "idle" && agent.callState !== "ended";

  useEffect(() => {
    let cancelled = false;

    (serverBrain ? fetchCustomers(profile.id) : Promise.resolve([])).then((list) => {
      if (!cancelled) setCustomers(list);
    });

    return () => {
      cancelled = true;
    };
  }, [profile.id, serverBrain]);

  const record = buildCallRecord({
    // A call keeps the agent it started with, even if the configuration is edited after.
    config: agent.callState === "idle" ? agentContext : agent.callConfig,
    profile,
    user,
    callState: agent.callState,
    summary: agent.summary,
    summaryPending: agent.summaryPending,
    duration: agent.duration,
    startedAt: agent.startedAt,
    callError: agent.callError,
  });

  const value = {
    agent, // the live call: callState, messages, voice, beginCall, endCall, ...
    record,
    isActive,
    serverBrain,
    profile,
    profiles: PROFILES,
    agentRecord,
    agentContext,
    customers,
    customerRef,
    recordView: recordView.callId === record.callId ? recordView.view : "summary",
    setRecordView: (view) => setRecordView({ callId: record.callId, view }),
    selectAgent: (id) => {
      setAgentChoice(String(id));
      write(AGENT_KEY, String(id));
    },
    setCustomerRef,
    // A profile is loaded per call, so switching starts from a clean slate.
    changeProfile: (id) => {
      agent.reset();
      setCustomerRef("");
      setCustomers([]); // the old profile's customers must not linger while the new list loads
      setProfileId(id);
      write(PROFILE_KEY, id);
    },
  };

  return <ConsoleContext.Provider value={value}>{children}</ConsoleContext.Provider>;
}
