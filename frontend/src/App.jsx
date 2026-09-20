import { useEffect, useMemo, useState } from "react";
import AgentConfigPanel from "./components/voice-agent/AgentConfigPanel";
import { emptyConfig, loadConfig, resolveConfig, sameConfig, saveConfig } from "./runtime/agentConfig.js";
import { buildCallRecord } from "./runtime/callRecord.js";
import { CALL_STATE_TEXT } from "./runtime/format.js";
import TopNavbar from "./components/voice-agent/TopNavbar";
import Sidebar from "./components/voice-agent/Sidebar";
import VoicePanel from "./components/voice-agent/VoicePanel";
import AgentResponse from "./components/voice-agent/AgentResponse";
import CallSummary from "./components/voice-agent/CallSummary";
import CallDetails from "./components/voice-agent/CallDetails";
import WelcomeBar from "./components/voice-agent/WelcomeBar";
import { ProfilePage, SectionPlaceholder, SettingsPage } from "./components/voice-agent/Pages";
import CalleeApp from "./components/voice-agent/CalleeApp";
import CallHistory from "./components/voice-agent/CallHistory";
import Login from "./components/voice-agent/Login";
import { authEvents } from "./runtime/api.js";
import { fetchSession, logout } from "./runtime/auth.js";
import { detectBackend } from "./runtime/transports.js";
import { fetchCustomers } from "./runtime/customers.js";
import { DEFAULT_PROFILE_ID, PROFILES, getProfile } from "./profiles/index.js";
import { useVoiceAgent } from "./runtime/useVoiceAgent.js";
import "./styles/voice-agent.css";

const DEFAULT_THEME = "dark";

// The product is not tied to one industry, so the header does not name one.
const TAGLINE = "One Voice Engine. Any Role. Any Domain.";

function Console({ user, onSignOut }) {
  const [theme, setTheme] = useState(
    () => localStorage.getItem("voice-agent-theme") || DEFAULT_THEME
  );
  const [profileId, setProfileId] = useState(
    () => localStorage.getItem("voice-agent-profile") || DEFAULT_PROFILE_ID
  );

  const profile = getProfile(profileId);
  const [customers, setCustomers] = useState([]);
  const [customerRef, setCustomerRef] = useState("");
  // What the operator wants the agent to do. Null until a valid one is saved.
  const [agentConfig, setAgentConfig] = useState(() => loadConfig());
  // What the sidebar fields hold while being edited; saving makes it the agent.
  const [configDraft, setConfigDraft] = useState(() => loadConfig() ?? emptyConfig());
  const [configOpen, setConfigOpen] = useState(false);
  const [configSaved, setConfigSaved] = useState(false);
  const [configExpanded, setConfigExpanded] = useState(() => !loadConfig());
  const [navOpen, setNavOpen] = useState(false); // the sidebar drawer on small screens
  const agentContext = useMemo(() => resolveConfig(agentConfig), [agentConfig]);
  const agent = useVoiceAgent(profile, { customerRef, agentConfig: agentContext });
  // home | applications | history | analytics | profile | settings
  const [view, setView] = useState("home");
  // Which part of a finished call the centre shows. Tied to the call, so a new
  // call starts on its summary again.
  const [recordView, setRecordView] = useState({ callId: null, view: "summary" });

  const serverBrain = agent.brain.source === "server";

  useEffect(() => {
    let cancelled = false;

    (serverBrain ? fetchCustomers(profile.id) : Promise.resolve([])).then((list) => {
      if (!cancelled) setCustomers(list);
    });

    return () => {
      cancelled = true;
    };
  }, [profile.id, serverBrain]);

  const isActive = agent.callState !== "idle" && agent.callState !== "ended";

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("voice-agent-theme", theme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem("voice-agent-profile", profile.id);
  }, [profile.id]);

  useEffect(() => {
    if (!configSaved) return undefined;

    const timer = setTimeout(() => setConfigSaved(false), 4000);

    return () => clearTimeout(timer);
  }, [configSaved]);

  useEffect(() => {
    if (!navOpen) return undefined;

    const onKeyDown = (event) => {
      if (event.key === "Escape") setNavOpen(false);
    };

    window.addEventListener("keydown", onKeyDown);

    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navOpen]);

  const saveAgentConfig = (config) => {
    saveConfig(config); // best effort: it still applies to this session if storage is blocked
    setAgentConfig(config);
    setConfigDraft(config);
    setConfigOpen(false);
    setConfigSaved(true);
  };

  const openConfig = () => {
    setNavOpen(false);
    setConfigOpen(true);
  };

  const navigate = (target) => {
    setView(target);
    setNavOpen(false);
  };

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
  const summaryView = recordView.callId === record.callId ? recordView.view : "summary";

  const showTranscript = () => {
    setRecordView({ callId: record.callId, view: "transcript" });
    document.getElementById("call-summary")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const toggleTheme = () => {
    setTheme((currentTheme) => (currentTheme === "light" ? "dark" : "light"));
  };

  // A profile is loaded per call, so switching starts from a clean slate.
  const changeProfile = (id) => {
    agent.reset();
    setCustomerRef("");
    setCustomers([]); // the old profile's customers must not linger while the new list loads
    setProfileId(id);
  };

  return (
    <div className="voice-app">
      <TopNavbar
        theme={theme}
        onToggleTheme={toggleTheme}
        workspaceLabel={TAGLINE}
        user={user}
        onSignOut={onSignOut}
        onNavigate={navigate}
        sidebarOpen={navOpen}
        onToggleSidebar={() => setNavOpen(!navOpen)}
      />

      <div className="voice-layout">
        <Sidebar
          open={navOpen}
          callState={agent.callState}
          profiles={PROFILES}
          profile={profile}
          profileLocked={isActive}
          onProfileChange={changeProfile}
          view={view}
          onNavigate={navigate}
          customers={customers}
          customerRef={customerRef}
          onCustomerChange={setCustomerRef}
          voice={agent.voice}
          brain={agent.brain}
          configExpanded={configExpanded}
          onToggleConfig={() => setConfigExpanded(!configExpanded)}
          configDraft={configDraft}
          onConfigDraftChange={(patch) => setConfigDraft({ ...configDraft, ...patch })}
          configured={Boolean(agentConfig)}
          configDirty={!sameConfig(configDraft, agentConfig ?? emptyConfig())}
          configSaved={configSaved}
          onConfigure={openConfig}
        />

        {navOpen && (
          <button
            type="button"
            className="sidebar-backdrop"
            aria-label="Close navigation"
            onClick={() => setNavOpen(false)}
          />
        )}

        {configOpen && (
          <AgentConfigPanel
            initial={configDraft}
            onSave={saveAgentConfig}
            onCancel={() => setConfigOpen(false)}
          />
        )}

        {view === "history" && (
          <main className="history-main">
            <CallHistory
              serverAvailable={agent.brain.source === "server"}
              telephony={Boolean(agent.brain.telephony)}
              profiles={PROFILES}
            />
          </main>
        )}

        {view === "applications" && (
          <main className="history-main">
            <SectionPlaceholder
              icon="applications"
              title="Applications"
              description="The applications your voice agent is connected to will be listed here."
            />
          </main>
        )}

        {view === "analytics" && (
          <main className="history-main">
            <SectionPlaceholder
              icon="analytics"
              title="Analytics"
              description="Call volume, outcomes and duration trends will be shown here."
            />
          </main>
        )}

        {view === "profile" && (
          <main className="history-main">
            <ProfilePage user={user} />
          </main>
        )}

        {view === "settings" && (
          <main className="history-main">
            <SettingsPage
              theme={theme}
              onThemeChange={setTheme}
              onConfigure={openConfig}
              configLocked={isActive}
            />
          </main>
        )}

        {view === "home" && (
          <main className="voice-main">
            <div className="home-center">
              <WelcomeBar contact={record.contact} agentName={record.agent.agentName} status={record.status} />

              <CallSummary
                record={record}
                configured={Boolean(agentConfig)}
                view={summaryView}
                onViewChange={(next) => setRecordView({ callId: record.callId, view: next })}
                onConfigure={openConfig}
              />

              <VoicePanel
                profile={profile}
                callState={agent.callState}
                duration={agent.duration}
                interim={agent.voice.interim}
                micState={agent.voice.micState}
                onStart={agent.beginCall}
                onStop={agent.endCall}
                onSend={agent.sendText}
                onReset={agent.reset}
                onViewHistory={() => navigate("history")}
              />
            </div>

            <div className="voice-right-column">
              <AgentResponse
                messages={agent.messages}
                pending={agent.pending}
                onRespond={agent.sendText}
                onClear={agent.clearMessages}
                canClear={!isActive}
                agentName={record.agent.agentName}
                status={isActive ? CALL_STATE_TEXT[agent.callState] : null}
              />

              <CallDetails record={record} summary={agent.summary} onViewTranscript={showTranscript} />
            </div>
          </main>
        )}
      </div>
    </div>
  );
}

// The operator console needs a sign-in when the server says so. With no server
// (offline mode) there is nothing to sign in to.
function Operator() {
  const [auth, setAuth] = useState({ status: "checking", user: null }); // checking | needed | ok

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const { available, authRequired } = await detectBackend();
      const user = available && authRequired ? await fetchSession().catch(() => null) : null;

      if (!cancelled) {
        setAuth(!available || !authRequired || user ? { status: "ok", user } : { status: "needed", user: null });
      }
    })();

    const expired = () => setAuth({ status: "needed", user: null });

    authEvents.addEventListener("unauthorized", expired);

    return () => {
      cancelled = true;
      authEvents.removeEventListener("unauthorized", expired);
    };
  }, []);

  const signOut = async () => {
    await logout();
    setAuth({ status: "needed", user: null });
  };

  if (auth.status === "checking") return <div className="callee-page"><p className="callee-note">Loading...</p></div>;
  if (auth.status === "needed") return <Login onSignedIn={(user) => setAuth({ status: "ok", user })} />;

  return <Console user={auth.user} onSignOut={auth.user ? signOut : null} />;
}

// A link like /?job=JOB-...&token=... is a phone call for someone: show them the
// incoming-call screen, not the operator console.
function App() {
  const params = new URLSearchParams(window.location.search);
  const jobId = params.get("job");
  const token = params.get("token");

  return jobId && token ? <CalleeApp jobId={jobId} token={token} /> : <Operator />;
}

export default App;
