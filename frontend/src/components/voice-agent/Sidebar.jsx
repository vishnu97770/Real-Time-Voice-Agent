import Icon from "./Icon";

export default function Sidebar({ callState }) {
  const navigation = [
    ["home", "Home"],
    ["applications", "Applications"],
    ["phone", "Call History"],
    ["analytics", "Analytics"],
    ["settings", "Settings"],
  ];

  return (
    <aside className="sidebar">
      <section className="user-details">
        <h3>♙ User Details</h3>

        <label>Name</label>
        <div className="input-display">V. Vishnu</div>

        <label>Region</label>
        <div className="input-display select">
          India
          <span>⌄</span>
        </div>
      </section>

      <nav className="navigation">
        {navigation.map(([icon, name], index) => (
          <button
            key={name}
            className={`nav-item ${index === 0 ? "active" : ""}`}
          >
            <Icon name={icon} size={21} />
            <span>{name}</span>
          </button>
        ))}
      </nav>

      <div className="service-card">
        <div className="service-title">
          <span className="online-dot" />
          Voice Service Ready
        </div>

        <p>LiveKit Connected</p>
        <p>Mic Access Enabled</p>

        {callState === "processing" && (
          <div className="service-processing">Processing conversation...</div>
        )}
      </div>
    </aside>
  );
}
