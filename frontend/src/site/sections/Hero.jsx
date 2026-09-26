import { useEffect, useRef, useState } from "react";
import { useSignupOpen } from "../../auth/context.js";
import Link from "../../router/Link.jsx";
import Arrow from "../Arrow.jsx";
import { scrollToSection } from "../hooks/scrollTo.js";
import { useSectionProgress } from "../hooks/useSectionProgress.js";
import OrbAnchor from "../stage/OrbAnchor.jsx";
import { pulse, setMode } from "../stage/bus.js";
import { useMicrophone } from "./useMicrophone.js";
import "./Hero.css";

// The hero tells the whole product in one loop: a voice comes in, the agent works out what it
// means, and something gets done. The orb behind it follows the same three beats.
const PHASES = [
  { id: "voice", mode: "listening", label: "Listening", caption: "“I need to move my appointment.”" },
  { id: "ai", mode: "thinking", label: "Understanding", caption: "Intent: reschedule · caller verified" },
  { id: "action", mode: "speaking", label: "Taking action", caption: "“Done. You're booked for Thursday at 3.”" },
];

const STEPS = [
  ["voice", "Voice"],
  ["ai", "AI"],
  ["action", "Action"],
];

const MIC_LABEL = {
  off: "Speak to see it listen",
  asking: "Waiting for permission…",
  on: "Listening to you. Tap to stop",
  blocked: "Microphone blocked, the orb keeps its own rhythm",
  unsupported: "No microphone available here",
};

export default function Hero() {
  const ref = useRef(null);
  const [phase, setPhase] = useState(0);
  const [visible, setVisible] = useState(true);
  const mic = useMicrophone();
  const signupOpen = useSignupOpen();
  const talking = mic.state === "on";

  // Peel away as the visitor leaves: copy drifts up and fades, the orb (an anchor, so its box is
  // its size) shrinks and lifts. Only a CSS variable changes.
  useSectionProgress(ref, (progress) => ref.current?.style.setProperty("--leave", progress.toFixed(4)), { mode: "leave" });

  useEffect(() => {
    const element = ref.current;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { threshold: 0.15 });

    observer.observe(element);

    return () => observer.disconnect();
  }, []);

  // Walk voice -> AI -> action while the hero is on screen and the visitor is not speaking.
  useEffect(() => {
    if (!visible || talking) return undefined;

    const timer = setInterval(() => {
      if (!document.hidden) setPhase((current) => (current + 1) % PHASES.length);
    }, 2900);

    return () => clearInterval(timer);
  }, [visible, talking]);

  const current = PHASES[phase];

  useEffect(() => {
    if (!visible) return undefined;

    setMode(talking ? "listening" : current.mode);

    return () => setMode("idle");
  }, [visible, talking, current.mode]);

  return (
    <section ref={ref} className="hero" id="top" data-chapter="Intro">
      <OrbAnchor name="hero" className="hero-orb" />

      <div className="hero-status">
        <ol className="hero-steps" aria-label="Voice, AI, action">
          {STEPS.map(([id, label], index) => (
            <li key={id} className={current.id === id && !talking ? "is-on" : ""}>
              <span>{label}</span>
              {index < STEPS.length - 1 && <i aria-hidden="true" />}
            </li>
          ))}
        </ol>

        <p className="hero-caption" aria-live="off">
          <b>{talking ? "You" : current.label}</b>
          <span key={talking ? "you" : current.id}>{talking ? "Say anything. The orb moves with your voice." : current.caption}</span>
        </p>

        <button
          type="button"
          className={`hero-mic ${talking ? "is-on" : ""}`}
          onClick={talking ? mic.stop : mic.start}
          disabled={mic.state === "asking"}
          title="Audio stays in your browser. It is never recorded or sent anywhere."
        >
          <i aria-hidden="true" />
          {MIC_LABEL[mic.state]}
        </button>
      </div>

      <div className="hero-copy">
        <p className="lp-eyebrow hero-rise" style={{ "--i": 0 }}>
          <span className="live-dot" aria-hidden="true" /> Real-time voice AI
        </p>

        <h1 className="hero-title">
          <span className="hero-line">
            <span style={{ "--i": 1 }}>AI agents that</span>
          </span>
          <span className="hero-line">
            <span style={{ "--i": 2 }}>
              actually <em>talk.</em>
            </span>
          </span>
        </h1>

        <div className="hero-foot hero-rise" style={{ "--i": 4 }}>
          <p className="lp-lead">
            Create voice agents that hold natural conversations, understand what people need, and get the work done, while the call is still happening.
          </p>

          <div className="hero-actions">
            <Link to={signupOpen ? "/signup" : "/signin"} transition className="lp-btn lp-btn--primary" onPointerEnter={() => pulse(0.9)} onFocus={() => pulse(0.9)}>
              Get started <Arrow />
            </Link>
            <a
              href="#demo"
              className="lp-btn lp-btn--ghost"
              onClick={(event) => {
                event.preventDefault();
                scrollToSection("demo");
              }}
            >
              Hear it work
            </a>
          </div>
        </div>
      </div>

      <a
        href="#what"
        className="hero-scroll"
        onClick={(event) => {
          event.preventDefault();
          scrollToSection("what");
        }}
      >
        <span>Scroll</span>
        <i aria-hidden="true" />
      </a>
    </section>
  );
}
