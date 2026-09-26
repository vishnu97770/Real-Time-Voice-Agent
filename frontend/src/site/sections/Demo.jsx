import { useEffect, useRef, useState } from "react";
import Logo from "../../components/Logo.jsx";
import SiteIcon from "../icons.jsx";
import Wave from "../Wave.jsx";
import { DEMO_SCRIPT, buildTimeline, frameAt, partialText } from "../demoScript.js";
import { useClock } from "../hooks/useClock.js";
import OrbAnchor from "../stage/OrbAnchor.jsx";
import { setMode } from "../stage/bus.js";
import { speechEnvelope } from "../stage/mode.js";
import "./Demo.css";

const TIMELINE = buildTimeline(DEMO_SCRIPT);

const STATUS_LABEL = {
  idle: "Ready",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  acting: "Working",
  done: "Complete",
};

const ORB_MODE = { idle: "idle", listening: "listening", thinking: "thinking", speaking: "speaking", acting: "thinking", done: "idle" };

const LEGEND = [
  ["listening", "It hears you, live."],
  ["thinking", "It works out what you mean."],
  ["acting", "It checks and does, with your data."],
  ["speaking", "It answers in a natural voice."],
];

const clock = (ms) => {
  const seconds = Math.floor(ms / 1000);

  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
};

const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

function Message({ entry }) {
  const { step, phase, progress } = entry;

  if (step.who === "action") {
    return (
      <div className={`msg msg--action ${phase === "done" ? "is-done" : ""}`}>
        <SiteIcon name={phase === "done" ? "check" : "bolt"} size={16} />
        <span>
          <b>{phase === "done" ? step.result : `${step.label}…`}</b>
          <code>{step.detail}</code>
        </span>
      </div>
    );
  }

  if (step.who === "done") {
    return (
      <div className="msg msg--done">
        <span className="msg-check">
          <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="m5 12.5 4.5 4.5L19 7.5" pathLength="1" />
          </svg>
        </span>
        <span>
          <b>{step.label}</b>
          <em>{step.detail}</em>
        </span>
      </div>
    );
  }

  const isAgent = step.who === "agent";

  return (
    <div className={`msg ${isAgent ? "msg--agent" : "msg--user"}`}>
      <span className="msg-who">{isAgent ? "Agent" : "Caller"}</span>
      {phase === "lead" ? (
        <p className="msg-dots" aria-label={isAgent ? "Thinking" : "Listening"}>
          <i />
          <i />
          <i />
        </p>
      ) : (
        <p>
          {partialText(step, progress)}
          {phase === "run" && <span className="msg-caret" />}
        </p>
      )}
    </div>
  );
}

export default function Demo() {
  const ref = useRef(null);
  const threadRef = useRef(null);
  const [inView, setInView] = useState(false);
  const [reduced] = useState(reducedMotion);
  const [elapsed, restart] = useClock({ active: inView && !reduced, duration: TIMELINE.duration });

  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { threshold: 0.35 });

    observer.observe(ref.current);

    return () => observer.disconnect();
  }, []);

  const t = reduced ? TIMELINE.duration : elapsed;
  const frame = frameAt(TIMELINE, t);
  const finished = t >= TIMELINE.duration;
  const speaker = frame.active?.step.who === "user" ? "user" : "agent";
  const level = frame.speaking ? 0.3 + 0.7 * speechEnvelope(t / 1000 + (speaker === "user" ? 0 : 3.7)) : frame.status === "acting" || frame.status === "thinking" ? 0.16 : 0.07;

  // The orb beside the card plays the agent: it listens, thinks and speaks with the script.
  useEffect(() => {
    if (!inView) return undefined;

    setMode(ORB_MODE[frame.status]);

    return () => setMode("idle");
  }, [inView, frame.status]);

  // Keep the newest line in view.
  useEffect(() => {
    const thread = threadRef.current;

    if (thread) thread.scrollTop = thread.scrollHeight;
  }, [frame.shown.length, frame.active?.phase]);

  return (
    <section ref={ref} className="demo">
      <div className="lp-wrap demo-grid">
        <div className="demo-copy">
          <p className="lp-eyebrow">
            <b>03</b> Live demo
          </p>
          <h2 className="lp-h2">
            Listen to it <em>work.</em>
          </h2>
          <p className="lp-lead">One appointment, start to finish. It listens, understands, checks the calendar, asks before it books, and confirms. All in one natural conversation.</p>

          <ul className="demo-legend" aria-label="What the agent is doing">
            {LEGEND.map(([status, text]) => (
              <li key={status} className={frame.status === status ? "is-on" : ""}>
                <i aria-hidden="true" />
                <b>{STATUS_LABEL[status]}</b>
                <span>{text}</span>
              </li>
            ))}
          </ul>

          <OrbAnchor name="demo" className="demo-orb" />

          <p className="demo-note">A scripted illustration of one call. Your agent works from your own data and rules.</p>
        </div>

        <div className={`demo-card is-${frame.status}`} role="group" aria-label="Simulated call between a caller and the voice agent">
          <header className="demo-head">
            <span className="demo-avatar">
              <Logo size={20} />
            </span>
            <span className="demo-who">
              <b>Front-desk agent</b>
              <span>Inbound call · appointments</span>
            </span>
            <span className="demo-status" role="status">
              <i aria-hidden="true" />
              {STATUS_LABEL[frame.status]}
            </span>
            <span className="demo-clock">{clock(Math.min(t, TIMELINE.duration))}</span>
          </header>

          <div className="demo-thread" ref={threadRef} aria-live="off">
            {frame.shown.length === 0 && <p className="demo-empty">Incoming call…</p>}
            {frame.shown.map((entry) => (
              <Message key={entry.index} entry={entry} />
            ))}
          </div>

          <footer className="demo-foot">
            <Wave level={level} tone={speaker === "user" && frame.speaking ? "user" : "agent"} />
            <button type="button" className="demo-replay" onClick={restart} disabled={!finished}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M4 12a8 8 0 1 0 2.6-5.9M4 4v4h4" />
              </svg>
              Replay
            </button>
          </footer>
        </div>
      </div>
    </section>
  );
}
