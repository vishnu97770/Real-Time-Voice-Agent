import Icon from "./Icon";

export default function TopNavbar() {
  return (
    <header className="top-navbar">
      <div className="brand">
        <div className="brand-logo">
          <span>AI</span>
        </div>

        <div>
          <h1>Real-Time Voice Agent</h1>
          <p>Credit Underwriting Workspace</p>
        </div>
      </div>

      <div className="navbar-right">
        <div className="online-status">
          <span className="online-dot" />
          Online
        </div>

        <button className="icon-button" aria-label="Notifications">
          <Icon name="bell" size={21} />
        </button>

        <div className="profile">
          <div className="avatar">VV</div>

          <span>V. Vishnu</span>

          <span className="dropdown">⌄</span>
        </div>
      </div>
    </header>
  );
}
