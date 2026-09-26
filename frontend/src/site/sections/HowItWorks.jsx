import { useRef } from "react";
import SiteIcon from "../icons.jsx";
import Wave from "../Wave.jsx";
import { stepOf } from "../hooks/progress.js";
import { useSectionProgress } from "../hooks/useSectionProgress.js";
import "./HowItWorks.css";

// Six steps, each with its own small scene. Scrolling through the pinned section moves from one
// to the next; only the active scene animates.
const STEPS = [
  {
    id: "configure",
    title: "Configure",
    heading: "Tell it who it is.",
    text: "Pick a use case, name the agent and describe its role and purpose. Say who it talks to, what it is responsible for and which tasks it may carry out.",
    points: ["Use case and industry", "Agent name and role", "Purpose and target users", "Responsibilities and primary tasks"],
  },
  {
    id: "connect",
    title: "Connect",
    heading: "Plug in the line and your systems.",
    text: "Choose how people reach it: a phone number or a web link. Connect your own systems through the API, and receive every result as a signed webhook.",
    points: ["Phone or web link", "Your contacts and records", "Signed result webhooks"],
  },
  {
    id: "workflows",
    title: "Define workflows",
    heading: "Decide what happens, and when.",
    text: "Set a trigger, like a date on a contact, and what the agent should do: who to call, why, and how. Consent and call windows are checked before every call.",
    points: ["Date-based triggers", "Consent and call-window checks", "Phone or web channel"],
  },
  {
    id: "deploy",
    title: "Deploy",
    heading: "Switch it on.",
    text: "When you are happy with it, deploy. The agent goes live for your organisation and can take and place calls straight away.",
    points: ["One click to go live", "Pause or edit any time", "Test it in the browser first"],
  },
  {
    id: "interact",
    title: "Interact",
    heading: "It talks. Things get done.",
    text: "The agent converses in real time, looks things up with tools, and asks before it changes anything. People interrupt it; it stops and listens.",
    points: ["Real-time voice", "Tools with confirmation", "Barge-in built in"],
  },
  {
    id: "analyze",
    title: "Analyze",
    heading: "See every call, and what it achieved.",
    text: "Every call has a transcript, a summary, the actions taken and an outcome. Dashboards show how each agent is performing.",
    points: ["Transcripts and summaries", "Actions and outcomes", "Performance per agent"],
  },
];

function Scene({ id }) {
  switch (id) {
    case "configure":
      return (
        <div className="sc-config">
          <div className="sc-row">
            <label>Use case</label>
            <span className="sc-select">Healthcare</span>
          </div>
          <div className="sc-row">
            <label>Agent name</label>
            <span className="sc-type" style={{ "--n": 8 }}>CareCall</span>
          </div>
          <div className="sc-row">
            <label>Agent role</label>
            <span className="sc-type" style={{ "--n": 23, "--t": "0.7s" }}>Appointment Coordinator</span>
          </div>
          <div className="sc-row">
            <label>Target users</label>
            <span className="sc-chips">
              <b style={{ "--i": 0 }}>Patients</b>
              <b style={{ "--i": 1 }}>Families</b>
            </span>
          </div>
          <div className="sc-row">
            <label>Primary tasks</label>
            <span className="sc-chips">
              <b style={{ "--i": 2 }}>Schedule appointments</b>
              <b style={{ "--i": 3 }}>Answer questions</b>
            </span>
          </div>
        </div>
      );
    case "connect":
      return (
        <div className="sc-connect">
          <svg viewBox="0 0 400 300" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
            <path pathLength="1" d="M118 82 C 168 82, 160 150, 200 150" />
            <path pathLength="1" d="M118 218 C 168 218, 160 150, 200 150" />
            <path pathLength="1" d="M200 150 L 282 150" />
          </svg>
          <span className="sc-node" style={{ left: "4%", top: "17%" }}>
            <SiteIcon name="phone" size={16} /> Phone line
          </span>
          <span className="sc-node" style={{ left: "4%", top: "68%" }}>
            <SiteIcon name="plug" size={16} /> Web link
          </span>
          <span className="sc-hub">
            <i />
            <SiteIcon name="agent" size={26} />
          </span>
          <span className="sc-node sc-node--right" style={{ right: "3%", top: "43%" }}>
            <SiteIcon name="workflow" size={16} /> Your systems
            <small>API · webhooks</small>
          </span>
        </div>
      );
    case "workflows":
      return (
        <ol className="sc-flow">
          {[
            ["calendar", "Trigger", "3 days before the appointment"],
            ["check", "Eligibility", "Consent given · inside call window"],
            ["phone", "Call", "Agent rings the contact"],
            ["chart", "Result", "Outcome recorded and sent on"],
          ].map(([icon, label, note], index) => (
            <li key={label} style={{ "--i": index }}>
              <span>
                <SiteIcon name={icon} size={16} />
              </span>
              <b>{label}</b>
              <em>{note}</em>
            </li>
          ))}
        </ol>
      );
    case "deploy":
      return (
        <div className="sc-deploy">
          <span className="sc-deploy-ring" />
          <span className="sc-deploy-ring" style={{ "--d": "1.2s" }} />
          <div className="sc-deploy-card">
            <span className="sc-deploy-av">
              <SiteIcon name="agent" size={22} />
            </span>
            <span>
              <b>CareCall</b>
              <em>Appointment Coordinator</em>
            </span>
            <span className="sc-status">
              <i /> <span className="sc-status-a">Draft</span>
              <span className="sc-status-b">Live</span>
            </span>
          </div>
          <span className="sc-deploy-btn">
            <SiteIcon name="rocket" size={16} /> Deploy agent
          </span>
        </div>
      );
    case "interact":
      return (
        <div className="sc-talk">
          <p className="sc-b sc-b--user" style={{ "--i": 0 }}>Can I move my appointment?</p>
          <p className="sc-b sc-b--agent" style={{ "--i": 1 }}>Of course. Would Thursday at 3 work?</p>
          <p className="sc-b sc-b--user" style={{ "--i": 2 }}>Yes, that's perfect.</p>
          <p className="sc-b sc-b--done" style={{ "--i": 3 }}>
            <SiteIcon name="check" size={14} /> Rescheduled · Thu 3:00 PM
          </p>
          <Wave level={0.5} bars={28} />
        </div>
      );
    default:
      return (
        <div className="sc-analyze">
          <div className="sc-kpis">
            <p>
              <b>128</b>
              <em>Calls</em>
            </p>
            <p>
              <b>94%</b>
              <em>Completed</em>
            </p>
            <p>
              <b>71</b>
              <em>Tasks done</em>
            </p>
          </div>
          <div className="sc-bars">
            {[38, 52, 44, 68, 60, 82, 74].map((height, index) => (
              <i key={index} style={{ "--h": `${height}%`, "--i": index }} />
            ))}
          </div>
          <ul className="sc-calls">
            {[
              ["Priya S.", "Appointment booked"],
              ["Rahul M.", "Reminder confirmed"],
              ["Anita K.", "Reschedule requested"],
            ].map(([who, what], index) => (
              <li key={who} style={{ "--i": index }}>
                <i />
                <b>{who}</b>
                <span>{what}</span>
              </li>
            ))}
          </ul>
        </div>
      );
  }
}

export default function HowItWorks() {
  const ref = useRef(null);

  // Discrete step -> classes on the copy, scenes and rail. Continuous progress -> --p.
  useSectionProgress(ref, (progress) => {
    const section = ref.current;

    if (!section) return;

    const step = stepOf(progress, STEPS.length);

    section.style.setProperty("--p", progress.toFixed(4));

    if (section.dataset.step !== String(step)) {
      section.dataset.step = String(step);
      section.style.setProperty("--step", String(step));
      section.querySelectorAll("[data-i]").forEach((node) => node.classList.toggle("is-active", Number(node.dataset.i) === step));
    }
  });

  // A rail tick scrolls to the middle of its step.
  const go = (index) => {
    const section = ref.current;
    const travel = section.offsetHeight - window.innerHeight;
    const top = section.getBoundingClientRect().top + window.scrollY;

    window.scrollTo({ top: top + ((index + 0.5) / STEPS.length) * travel, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  };

  return (
    <section ref={ref} className="how" data-step="0" style={{ "--p": 0, "--step": 0 }}>
      <div className="how-pin">
        <div className="lp-wrap how-grid">
          <div className="how-left">
            <p className="lp-eyebrow">
              <b>05</b> How it works
            </p>
            <h2 className="lp-h2">
              From idea to a <em>live agent</em> in six steps.
            </h2>

            <div className="how-count" aria-hidden="true">
              <span>
                <span className="how-roll">
                  {STEPS.map((step, index) => (
                    <b key={step.id}>{String(index + 1).padStart(2, "0")}</b>
                  ))}
                </span>
              </span>
              <i>/ 06</i>
            </div>

            <div className="how-copy">
              {STEPS.map((step, index) => (
                <div key={step.id} data-i={index} className={`how-step ${index === 0 ? "is-active" : ""}`}>
                  <h3>{step.heading}</h3>
                  <p>{step.text}</p>
                  <ul>
                    {step.points.map((point) => (
                      <li key={point}>{point}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </div>

          <div className="how-stage" aria-hidden="true">
            <div className="how-window">
              <div className="how-window-bar">
                <i />
                <i />
                <i />
                <span>{STEPS.map((step, index) => (
                  <em key={step.id} data-i={index} className={index === 0 ? "is-active" : ""}>{step.title}</em>
                ))}</span>
              </div>
              <div className="how-scenes">
                {STEPS.map((step, index) => (
                  <div key={step.id} data-i={index} className={`how-scene ${index === 0 ? "is-active" : ""}`}>
                    <Scene id={step.id} />
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        <ol className="how-rail" aria-label="Steps">
          {STEPS.map((step, index) => (
            <li key={step.id} data-i={index} className={index === 0 ? "is-active" : ""}>
              <button type="button" onClick={() => go(index)}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                {step.title}
              </button>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
