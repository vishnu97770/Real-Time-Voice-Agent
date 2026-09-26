import { lazy, Suspense, useEffect, useRef } from "react";
import Logo from "../components/Logo.jsx";
import Link from "../router/Link.jsx";
import Wave from "./Wave.jsx";
import OrbAnchor from "./stage/OrbAnchor.jsx";
import { setMode } from "./stage/bus.js";
import { useVoiceStage } from "./stage/useVoiceStage.js";
import "./landing.css";
import "./SignIn.css";

// The same 3D orb as the landing page, loaded after first paint (the CSS orb is what shows until
// then, and on devices that get no 3D).
const VoiceStage = lazy(() => import("./stage/VoiceStage.jsx"));

// The frame shared by the sign-in and sign-up screens: the brand, the artwork with the orb on the
// left, and the card on the right that `children` fill. (The stylesheet is still called SignIn.css,
// and its classes signin-*; both screens use them.)
//
// `working` is true while a request is in flight: the orb is the agent, and it goes from breathing
// to "thinking".
export default function AuthLayout({ working = false, title = "Voice in. Work done.", text = "Your agents are listening, and every call they take is waiting for you here.", children }) {
  const voiceStage = useVoiceStage();
  const artRef = useRef(null);

  useEffect(() => {
    setMode(working ? "thinking" : "idle");

    return () => setMode("idle");
  }, [working]);

  return (
    <div className="lp signin" data-stage={voiceStage.status}>
      <Link to="/" transition className="lp-brand signin-brand">
        <Logo />
        <span>
          <b>Real-Time</b> Voice Agent
        </span>
      </Link>

      {voiceStage.enabled && (
        <Suspense fallback={null}>
          <VoiceStage tier={voiceStage.tier} onReady={voiceStage.onReady} onGiveUp={voiceStage.onGiveUp} clipTo={artRef} />
        </Suspense>
      )}

      <aside ref={artRef} className="signin-art" aria-hidden="true">
        <OrbAnchor name="signin" className="signin-orb" />

        <Wave level={0.28} bars={30} className="signin-wave" />

        <p>
          <b>{title}</b>
          <span>{text}</span>
        </p>
      </aside>

      <main className="signin-panel">
        <div className="signin-card">{children}</div>
      </main>
    </div>
  );
}
