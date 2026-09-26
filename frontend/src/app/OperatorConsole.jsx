import { useEffect, useMemo, useState } from "react";
import AgentConfigPanel from "../components/voice-agent/AgentConfigPanel";
import { emptyConfig, fromAgentPayload, loadConfig, resolveConfig, sameConfig, saveConfig, toAgentPayload } from "../runtime/agentConfig.js";
import { createAgent, fetchAgents, updateAgent } from "../runtime/agents.js";
import { buildCallRecord } from "../runtime/callRecord.js";
import { CALL_STATE_TEXT } from "../runtime/format.js";
import TopNavbar from "../components/voice-agent/TopNavbar";
import Sidebar from "../components/voice-agent/Sidebar";
import VoicePanel from "../components/voice-agent/VoicePanel";
import AgentResponse from "../components/voice-agent/AgentResponse";
import CallSummary from "../components/voice-agent/CallSummary";
import CallDetails from "../components/voice-agent/CallDetails";
import WelcomeBar from "../components/voice-agent/WelcomeBar";
import { ProfilePage, SectionPlaceholder, SettingsPage } from "../components/voice-agent/Pages";
import CallHistory from "../components/voice-agent/CallHistory";
import Contacts from "../components/voice-agent/Contacts";
import Workflows from "../components/voice-agent/Workflows";
import { useAuth } from "../auth/context.js";
import { useRouter } from "../router/context.js";
import { fetchCustomers } from "../runtime/customers.js";
import { DEFAULT_PROFILE_ID, PROFILES, getProfile } from "../profiles/index.js";
import { useVoiceAgent } from "../runtime/useVoiceAgent.js";
import "../styles/voice-agent.css";

// The operator console the application has always had (voice call, call history, contacts,
// workflows, agent configuration), mounted under /app/*. Its screens are unchanged; what is new is
// that the URL now says which one is open, and that signing in and out belong to the auth provider
// instead of a component of its own.
//
// route name -> which of the console's screens it shows
const VIEW_FOR_ROUTE = {
  dashboard: "home",
  console: "home",
  agents: "home",
  "agent-new": "home",
  "agent-edit": "home",
  calls: "history",
  "call-detail": "history",
  contacts: "contacts",
  workflows: "workflows",
  analytics: "analytics",
  settings: "settings",
};

// ...and the reverse: where each screen lives. "applications" has no page of its own yet.
const PATH_FOR_VIEW = {
  home: "/app/dashboard",
  history: "/app/calls",
  contacts: "/app/contacts",
  workflows: "/app/workflows",
  analytics: "/app/analytics",
  settings: "/app/settings",
  profile: "/app/settings",
};

const DEFAULT_THEME = "dark";

// The product is not tied to one industry, so the header does not name one.
const TAGLINE = "One Voice Engine. Any Role. Any Domain.";

export default function OperatorConsole() {
  const { user, signOut } = useAuth();
  const { route, path, navigate: goTo } = useRouter();
  const onSignOut = user ? signOut : null;
  const [theme, setTheme] = useState(
    () => localStorage.getItem("voice-agent-theme") || DEFAULT_THEME
  );
  const [profileId, setProfileId] = useState(
    () => localStorage.getItem("voice-agent-profile") || DEFAULT_PROFILE_ID
  );

  const profile = getProfile(profileId);
  const [customers, setCustomers] = useState([]);
  const [customerRef, setCustomerRef] = useState("");
  // What the operator wants the agent to do. Null until a valid one is saved. Starts from this
  // browser's local cache (so the first paint has something to show); the backend's own Agent,
  // once the load below resolves, always wins over it - see saveAgentConfig and the load effect.
  const [agentConfig, setAgentConfig] = useState(() => loadConfig());
  // What the sidebar fields hold while being edited; saving makes it the agent.
  const [configDraft, setConfigDraft] = useState(() => loadConfig() ?? emptyConfig());
  // The backend Agent's id, once a load or a save has told us it. Null means "not created yet":
  // the next save creates one instead of updating.
  const [agentRecordId, setAgentRecordId] = useState(null);
  const [configOpen, setConfigOpen] = useState(false);
  const [configSaved, setConfigSaved] = useState(false);
  const [configSaving, setConfigSaving] = useState(false);
  const [configSaveError, setConfigSaveError] = useState(null);
  const [configExpanded, setConfigExpanded] = useState(() => !loadConfig());
  const [navOpen, setNavOpen] = useState(false); // the sidebar drawer on small screens
  const agentContext = useMemo(() => resolveConfig(agentConfig), [agentConfig]);
  const agent = useVoiceAgent(profile, { customerRef, agentConfig: agentContext });
  // home | applications | workflows | history | analytics | profile | settings
  // The URL decides, except for a screen with no URL of its own, which is remembered with the
  // path it was opened on (so going Back to another URL shows that URL's screen again).
  const [own, setOwn] = useState(null);
  const view = own && own.path === path ? own.view : (VIEW_FOR_ROUTE[route] ?? "home");
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

  // The backend is now the source of truth for the agent configuration (Step 18B): once a server
  // is known to be there, load its organization's agent and let it override the local guess this
  // component started with. No server (serverBrain false) leaves the local draft exactly as it
  // was before this step - fetchAgents() itself never throws, so a real network problem behaves
  // the same as "no server" here; a save attempt is where that would actually be reported.
  useEffect(() => {
    if (!serverBrain) return undefined;

    let cancelled = false;

    fetchAgents().then(([found]) => {
      if (cancelled || !found) return;

      const config = fromAgentPayload(found);

      setAgentRecordId(found.id);
      setAgentConfig(config);
      setConfigDraft(config);
      setConfigExpanded(false);
      saveConfig(config); // the local cache now follows the backend, never the other way round
    });

    return () => {
      cancelled = true;
    };
  }, [serverBrain]);

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

  const saveAgentConfig = async (config) => {
    if (!serverBrain) {
      // No server to persist to: the same local-only behavior this had before Step 18B.
      saveConfig(config); // best effort: it still applies to this session if storage is blocked
      setAgentConfig(config);
      setConfigDraft(config);
      setConfigOpen(false);
      setConfigSaved(true);
      return;
    }

    setConfigSaving(true);
    setConfigSaveError(null);

    try {
      const payload = toAgentPayload(config);
      const saved = await (agentRecordId ? updateAgent(agentRecordId, payload) : createAgent(payload));
      const resolved = fromAgentPayload(saved);

      setAgentRecordId(saved.id);
      setAgentConfig(resolved);
      setConfigDraft(resolved);
      saveConfig(resolved); // the local cache follows what the server actually stored
      setConfigOpen(false);
      setConfigSaved(true);
    } catch (error) {
      // Nothing here pretends the save worked: the dialog stays open, with what the operator
      // typed still in it, and says why.
      setConfigSaveError(error.message || "Could not save the agent configuration.");
    } finally {
      setConfigSaving(false);
    }
  };

  const openConfig = () => {
    setNavOpen(false);
    setConfigSaveError(null);
    setConfigOpen(true);
  };

  const navigate = (target) => {
    setNavOpen(false);

    if (PATH_FOR_VIEW[target]) {
      setOwn(null);
      goTo(PATH_FOR_VIEW[target]);
    } else {
      setOwn({ view: target, path });
    }
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
            onCancel={() => {
              setConfigOpen(false);
              setConfigSaveError(null);
            }}
            saving={configSaving}
            saveError={configSaveError}
            offline={!serverBrain}
          />
        )}

        {view === "history" && (
          <main key={view} className="view-frame history-main">
            <CallHistory
              serverAvailable={agent.brain.source === "server"}
              telephony={Boolean(agent.brain.telephony)}
              profiles={PROFILES}
            />
          </main>
        )}

        {view === "contacts" && (
          <main key={view} className="view-frame history-main">
            <Contacts serverAvailable={serverBrain} />
          </main>
        )}

        {view === "workflows" && (
          <main key={view} className="view-frame history-main">
            <Workflows serverAvailable={serverBrain} />
          </main>
        )}

        {view === "applications" && (
          <main key={view} className="view-frame history-main">
            <SectionPlaceholder
              icon="applications"
              title="Applications"
              description="The applications your voice agent is connected to will be listed here."
            />
          </main>
        )}

        {view === "analytics" && (
          <main key={view} className="view-frame history-main">
            <SectionPlaceholder
              icon="analytics"
              title="Analytics"
              description="Call volume, outcomes and duration trends will be shown here."
            />
          </main>
        )}

        {view === "profile" && (
          <main key={view} className="view-frame history-main">
            <ProfilePage user={user} />
          </main>
        )}

        {view === "settings" && (
          <main key={view} className="view-frame history-main">
            <SettingsPage
              theme={theme}
              onThemeChange={setTheme}
              onConfigure={openConfig}
              configLocked={isActive}
            />
          </main>
        )}

        {view === "home" && (
          <main key={view} className="view-frame voice-main">
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
                acting={Boolean(agent.messages.at(-1)?.toolCalls?.length) && isActive}
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
