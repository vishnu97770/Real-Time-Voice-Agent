import { useRef } from "react";
import { stepOf } from "../hooks/progress.js";
import { useSectionProgress } from "../hooks/useSectionProgress.js";
import SiteIcon from "../icons.jsx";
import "./Explain.css";

const STATEMENT =
  "Businesses build AI voice agents that talk with people in real time, understand what they need, and complete the task.";

// The seven beats of one conversation, in order. Each is lit by scroll.
const FLOW = [
  { icon: "user", label: "User", note: "Someone calls, or opens a link." },
  { icon: "waves", label: "Voice conversation", note: "They simply talk. No menus, no forms." },
  { icon: "agent", label: "AI agent", note: "Your configured agent picks up." },
  { icon: "ear", label: "Understands", note: "What they want, and who is asking." },
  { icon: "branch", label: "Decides", note: "The right answer, or the right action." },
  { icon: "bolt", label: "Takes action", note: "Books, checks, updates, follows up." },
  { icon: "check", label: "Task completed", note: "Outcome recorded and sent on." },
];

const WORDS = STATEMENT.split(" ");
const FIRST_NODE = 0.5; // scroll progress at which the first node lights
const LAST_NODE = 0.94;

export default function Explain() {
  const ref = useRef(null);
  const flowRef = useRef(null);

  // One CSS variable drives the words, the statement's lift and every node; only the "current"
  // node needs a class (a discrete highlight), toggled when the step changes.
  useSectionProgress(ref, (progress) => {
    ref.current?.style.setProperty("--p", progress.toFixed(4));

    const lit = progress < FIRST_NODE ? -1 : stepOf((progress - FIRST_NODE) / (LAST_NODE - FIRST_NODE), FLOW.length);

    flowRef.current?.querySelectorAll("li").forEach((node, index) => node.classList.toggle("is-current", index === lit));
  });

  return (
    <section ref={ref} className="explain" style={{ "--p": 0 }}>
      <div className="explain-pin">
        <div className="lp-wrap explain-inner">
          <p className="lp-eyebrow">
            <b>02</b> What it is
          </p>

          <h2 className="explain-statement" aria-label={STATEMENT}>
            {WORDS.map((word, index) => (
              <span key={index} className="explain-word" style={{ "--w": (index / (WORDS.length - 1)).toFixed(3) }} aria-hidden="true">
                {word}{" "}
              </span>
            ))}
          </h2>

          <div className="flow-wrap">
            <div className="flow-line" aria-hidden="true">
              <i />
            </div>

            <ol ref={flowRef} className="flow" aria-label="From a conversation to a completed task">
              {FLOW.map((step, index) => (
                <li key={step.label} style={{ "--s": (FIRST_NODE + (index / FLOW.length) * (LAST_NODE - FIRST_NODE)).toFixed(3) }}>
                  <span className="flow-index">{String(index + 1).padStart(2, "0")}</span>
                  <span className="flow-icon">
                    <SiteIcon name={step.icon} size={22} />
                  </span>
                  <b>{step.label}</b>
                  <span className="flow-note">{step.note}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>
    </section>
  );
}
