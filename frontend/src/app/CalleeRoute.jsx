import CalleeApp from "../components/voice-agent/CalleeApp";
import "../styles/voice-agent.css";

// The incoming-call screen for a person who was sent an answer link (/?job=...&token=...). It
// predates the router and keeps working exactly as before; this only gives it its stylesheet.
export default function CalleeRoute(props) {
  return <CalleeApp {...props} />;
}
