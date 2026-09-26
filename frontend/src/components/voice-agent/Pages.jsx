import Icon from "./Icon";

// A destination that exists in the navigation but is not built yet. It says so
// instead of showing invented data.
export function SectionPlaceholder({ icon, title, description }) {
  return (
    <section className="page-card page-placeholder">
      <span className="summary-icon">
        <Icon name={icon} size={24} />
      </span>

      <h2>{title}</h2>
      <p>{description}</p>
      <span className="status-badge badge-idle">Coming soon</span>
    </section>
  );
}

export function ProfilePage({ user }) {
  return (
    <section className="page-card">
      <h2>Profile</h2>

      <dl className="page-facts">
        <div>
          <dt>Email</dt>
          <dd>{user?.email ?? "Not signed in (offline mode)"}</dd>
        </div>
        {user?.role && (
          <div>
            <dt>Role</dt>
            <dd>{user.role[0].toUpperCase() + user.role.slice(1)}</dd>
          </div>
        )}
        {user?.organization_id != null && (
          <div>
            <dt>Organization ID</dt>
            <dd>{user.organization_id}</dd>
          </div>
        )}
      </dl>
    </section>
  );
}

export function SettingsPage({ theme, onThemeChange, onConfigure, configLocked }) {
  return (
    <section className="page-card">
      <h2>Settings</h2>

      <fieldset className="page-setting">
        <legend>Appearance</legend>

        <div className="segmented">
          {["light", "dark"].map((mode) => (
            <label key={mode} className={theme === mode ? "is-active" : ""}>
              <input
                type="radio"
                name="theme"
                value={mode}
                checked={theme === mode}
                onChange={() => onThemeChange(mode)}
              />
              <Icon name={mode === "dark" ? "moon" : "sun"} size={16} />
              {mode === "dark" ? "Dark" : "Light"}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="page-setting">
        <h3>Agent</h3>
        <p>What the voice agent does is set in Agent Use Case &amp; Configuration.</p>

        <button type="button" className="config-open" disabled={configLocked} onClick={onConfigure}>
          Open configuration
          <Icon name="arrowRight" size={16} />
        </button>
      </div>
    </section>
  );
}
