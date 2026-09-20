import Icon from "./Icon";
import ProfileMenu from "./ProfileMenu";

export default function TopNavbar({
  theme,
  onToggleTheme,
  workspaceLabel,
  user,
  onSignOut,
  onNavigate,
  sidebarOpen,
  onToggleSidebar,
}) {
  const isDark = theme === "dark";

  return (
    <header className="top-navbar">
      {/* Brand */}
      <div className="brand">
        <button
          type="button"
          className="icon-button sidebar-toggle"
          aria-label={sidebarOpen ? "Close navigation" : "Open navigation"}
          aria-expanded={sidebarOpen}
          aria-controls="app-sidebar"
          onClick={onToggleSidebar}
        >
          <Icon name={sidebarOpen ? "cross" : "menu"} size={22} />
        </button>

        <div className="brand-logo">
          <span>AI</span>
        </div>

        <div>
          <h1>
            Real-Time <span>Voice</span> Agent
          </h1>
          <p>{workspaceLabel}</p>
        </div>
      </div>

      {/* Right Side */}
      <div className="navbar-right">
        {/* Online Status */}
        <div className="online-status">
          <span className="online-dot" />
          <span>Online</span>
        </div>

        {/* Notifications */}
        <button
          type="button"
          className="icon-button"
          aria-label="Notifications"
          title="Notifications"
        >
          <Icon name="bell" size={21} />
        </button>

        {/* Light / Dark Mode */}
        <button
          type="button"
          className="theme-toggle"
          onClick={onToggleTheme}
          aria-label={
            isDark ? "Switch to light mode" : "Switch to dark mode"
          }
          title={isDark ? "Light mode" : "Dark mode"}
        >
          <span
            className={`theme-icon ${
              !isDark ? "active" : ""
            }`}
          >
            ☀
          </span>

          <span
            className={`theme-icon ${
              isDark ? "active" : ""
            }`}
          >
            ☾
          </span>
        </button>

        {/* User Profile */}
        <ProfileMenu
          user={user}
          theme={theme}
          onToggleTheme={onToggleTheme}
          onNavigate={onNavigate}
          onSignOut={onSignOut}
        />
      </div>
    </header>
  );
}
