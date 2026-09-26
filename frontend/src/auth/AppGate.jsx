import { useEffect } from "react";
import Splash from "../components/Splash.jsx";
import { useRouter } from "../router/context.js";
import { useAuth } from "./context.js";
import { decideApp } from "./guard.js";
import ServerUnavailable from "./ServerUnavailable.jsx";

// Everything under /app/* renders through this. What it does is decided by guard.js (pure, tested):
//   signed in / open workspace   -> the page
//   first check still running    -> a loading mark (the only state that ever waits, and it always ends)
//   signed out                   -> replace the URL with /signin
//   server unreachable           -> a screen with Try again, never a spinner
export default function AppGate({ children }) {
  const { status } = useAuth();
  const { path, search, navigate } = useRouter();
  const query = search.toString();
  const decision = decideApp({ status, path, search: query ? `?${query}` : "" });

  useEffect(() => {
    if (decision.kind === "redirect") navigate(decision.to, { replace: true });
  }, [decision.kind, decision.to, navigate]);

  if (decision.kind === "render") return children;
  if (decision.kind === "unavailable") return <ServerUnavailable />;

  return <Splash label={decision.kind === "redirect" ? "Redirecting to sign in" : "Checking your session"} />;
}
