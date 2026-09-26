import Logo from "../components/Logo.jsx";
import Link from "../router/Link.jsx";
import { useAuth } from "./context.js";

// Shown instead of a spinner when the server cannot be reached (or answered nonsense). Retry asks
// again; "Continue offline" is the browser-only console the app has always had when there is no
// server, but now it is something the person chooses, not something that happens silently.
export default function ServerUnavailable() {
  const { error, retry, enterOffline } = useAuth();

  return (
    <div className="gate" role="alert">
      <div className="gate-card">
        <Logo size={28} />
        <h1>We can't reach the server</h1>
        <p>{error ?? "Something went wrong while checking your session."} Check that the backend is running, then try again.</p>

        <div className="gate-actions">
          <button type="button" className="gate-btn gate-btn--primary" onClick={retry}>
            Try again
          </button>
          <button type="button" className="gate-btn" onClick={enterOffline}>
            Continue offline
          </button>
        </div>

        <small>
          Offline mode runs the voice console in this browser only: no sign-in, no saved calls. <Link to="/">Back to the site</Link>
        </small>
      </div>
    </div>
  );
}
