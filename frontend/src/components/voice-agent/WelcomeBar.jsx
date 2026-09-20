import { useEffect, useState } from "react";
import Icon from "./Icon";
import { formatClock, formatLongDate } from "../../runtime/format.js";

const MESSAGE = {
  idle: (agent) => `${agent} is ready.`,
  live: (agent) => `${agent} is on a call with you.`,
  summarizing: () => "Finishing up your call summary.",
  completed: () => "Your call summary is ready.",
  unavailable: () => "The last call has no summary.",
};

export default function WelcomeBar({ contact, agentName, status }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);

    return () => clearInterval(timer);
  }, []);

  return (
    <header className="welcome-bar">
      <span className="summary-icon">
        <Icon name="document" size={22} />
      </span>

      <div>
        <h2>Welcome back, {contact}!</h2>
        <p>{MESSAGE[status](agentName)}</p>
      </div>

      <div className="welcome-date">
        <span>{formatLongDate(now)}</span>
        <strong>{formatClock(now)}</strong>
      </div>
    </header>
  );
}
