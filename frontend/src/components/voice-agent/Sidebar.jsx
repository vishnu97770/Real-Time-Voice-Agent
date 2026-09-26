import Icon from "./Icon";
import AgentUseCaseConfig from "./AgentUseCaseConfig";
import { voiceSupport } from "../../runtime/useVoice.js";

// Independent destinations. None of them contains another.
// [icon, label, view it opens]
const NAVIGATION = [
  ["home", "Home", "home"],
  ["applications", "Applications", "applications"],
  ["workflow", "Workflows", "workflows"],
  ["phone", "Call History", "history"],
  ["user", "Contacts", "contacts"],
  ["analytics", "Analytics", "analytics"],
];

const MIC_STATUS = {
  off: ["Idle", true],
  on: ["Listening", true],
  blocked: ["Blocked", false],
  error: ["Error", false],
};

function brainStatus(brain) {
  if (brain.source === "server") return [`Ready (${brain.name})`, true];
  if (brain.source === "local") return ["Ready (local rules, offline)", true];

  return ["Checking...", null];
}

function StatusRow({ label, value, ok }) {
  return (
    <li className={ok === false ? "is-bad" : ok === null ? "is-pending" : ""}>
      <Icon name={ok === false ? "cross" : "check"} size={14} />
      <span>
        {label}: {value}
      </span>
    </li>
  );
}

export default function Sidebar({
  open,
  callState,
  profiles,
  profile,
  profileLocked,
  onProfileChange,
  voice,
  brain,
  view,
  onNavigate,
  customers = [],
  customerRef = "",
  onCustomerChange,
  configExpanded,
  onToggleConfig,
  configDraft,
  onConfigDraftChange,
  configured = false,
  configDirty = false,
  configSaved = false,
  onConfigure,
}) {
  const ready = voiceSupport.recognition && voiceSupport.synthesis;
  const [micText, micOk] = MIC_STATUS[voice.micState] ?? MIC_STATUS.off;
  const [brainText, brainOk] = brainStatus(brain);

  return (
    <aside className={`sidebar ${open ? "is-open" : ""}`} id="app-sidebar" aria-label="Sidebar">
      <div className="sidebar-scroll">
        <nav className="navigation" aria-label="Main">
          {NAVIGATION.map(([icon, name, target]) => (
            <button
              key={name}
              type="button"
              className={`nav-item ${target === view ? "active" : ""}`}
              aria-current={target === view ? "page" : undefined}
              onClick={() => onNavigate(target)}
            >
              <Icon name={icon} size={21} />
              <span>{name}</span>
            </button>
          ))}
        </nav>

        <div className="navigation navigation-config">
          <AgentUseCaseConfig
            expanded={configExpanded}
            onToggle={onToggleConfig}
            draft={configDraft}
            onDraftChange={onConfigDraftChange}
            configured={configured}
            dirty={configDirty}
            locked={profileLocked}
            saved={configSaved}
            onConfigure={onConfigure}
          >
            <div className="config-data">
              <label htmlFor="agent-profile">Connected data &amp; tools</label>
              <select
                id="agent-profile"
                className="profile-select"
                value={profile.id}
                disabled={profileLocked}
                onChange={(event) => onProfileChange(event.target.value)}
              >
                {profiles.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>

              {customers.length > 0 && (
                <>
                  <label htmlFor="agent-customer">Customer</label>
                  <select
                    id="agent-customer"
                    className="profile-select"
                    value={customerRef}
                    disabled={profileLocked}
                    onChange={(event) => onCustomerChange(event.target.value)}
                  >
                    <option value="">Demo data</option>
                    {customers.map((customer) => (
                      <option key={customer.ref} value={customer.ref}>
                        {customer.display_name}
                      </option>
                    ))}
                  </select>
                </>
              )}

              <p className="profile-hint">
                {profileLocked
                  ? "End the call to switch data and tools."
                  : "The lookups and actions the agent can use."}
              </p>
            </div>
          </AgentUseCaseConfig>
        </div>
      </div>

      <div className="service-card">
        <div className="service-title">
          <span className={`online-dot ${ready ? "" : "offline-dot"}`} />
          {ready ? "Voice Service Ready" : "Voice Limited"}
        </div>

        <ul className="service-list">
          <StatusRow label="Microphone" value={micText} ok={micOk} />
          <StatusRow
            label="Speech-to-Text"
            value={voiceSupport.recognition ? "Ready" : "Unavailable"}
            ok={voiceSupport.recognition}
          />
          <StatusRow
            label="Text-to-Speech"
            value={voiceSupport.synthesis ? "Ready" : "Unavailable"}
            ok={voiceSupport.synthesis}
          />
          <StatusRow label="AI Agent" value={brainText} ok={brainOk} />
        </ul>

        {brain.note && <p className="brain-note">{brain.note}</p>}

        {callState === "processing" && (
          <div className="service-processing">Processing conversation...</div>
        )}
      </div>
    </aside>
  );
}
