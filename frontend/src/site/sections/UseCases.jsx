import { useEffect, useRef } from "react";
import SiteIcon from "../icons.jsx";
import Wave from "../Wave.jsx";
import { useSectionProgress } from "../hooks/useSectionProgress.js";
import "./UseCases.css";

// Six things an agent does. Each card carries a small living picture of its job that swells on
// hover; the section itself pins to the screen and turns vertical scroll into a sideways drift.
const CASES = [
  {
    id: "calls",
    icon: "phone",
    title: "Customer calls",
    text: "Agents that answer and hold real conversations. Natural back-and-forth, interruptions welcome.",
    points: ["Speaks and listens at the same time", "Hands over when it should", "Every call summarised"],
  },
  {
    id: "schedule",
    icon: "calendar",
    title: "Appointment scheduling",
    text: "Finds a time that works, confirms it with the person, and only then books it.",
    points: ["Checks availability live", "Confirms before it changes anything", "Handles reschedules"],
  },
  {
    id: "reminders",
    icon: "bell",
    title: "Reminders",
    text: "Reaches out ahead of the moments that matter: appointments, payments, renewals.",
    points: ["Triggered by dates on the contact", "Respects consent and call windows", "Confirms it was heard"],
  },
  {
    id: "notifications",
    icon: "broadcast",
    title: "Notifications",
    text: "Delivers important information by voice, and checks the person understood it.",
    points: ["Phone or web link", "One message, many people", "Outcome recorded per call"],
  },
  {
    id: "leads",
    icon: "funnel",
    title: "Lead qualification",
    text: "Talks to prospects, asks the questions that matter, and records what it learns.",
    points: ["Your questions, your criteria", "Structured answers, not notes", "Hot leads flagged"],
  },
  {
    id: "workflows",
    icon: "workflow",
    title: "Business workflows",
    text: "Connect conversations to actions. A trigger starts a call; the result flows back to your systems.",
    points: ["Trigger, call, decide, act", "Signed results sent to your API", "Full audit trail"],
  },
];

function Visual({ id }) {
  switch (id) {
    case "calls":
      return <Wave level={0.42} className="uc-wave" bars={30} />;
    case "schedule":
      return (
        <div className="uc-cal">
          <div className="uc-cal-grid">
            {Array.from({ length: 28 }, (_, index) => (
              <i key={index} className={index === 17 ? "is-picked" : index % 6 === 0 ? "is-busy" : ""} style={{ "--i": index }} />
            ))}
          </div>
          <span className="uc-cal-chip">
            <SiteIcon name="check" size={14} /> Tomorrow · 3:00 PM
          </span>
        </div>
      );
    case "reminders":
      return (
        <div className="uc-bell">
          <i />
          <i />
          <i />
          <SiteIcon name="bell" size={54} />
        </div>
      );
    case "notifications":
      return (
        <div className="uc-cast">
          <i className="uc-cast-core" />
          <i className="uc-cast-ring" />
          <i className="uc-cast-ring" style={{ "--d": "0.9s" }} />
          {Array.from({ length: 12 }, (_, index) => (
            <b key={index} style={{ "--a": `${index * 30}deg`, "--r": `${68 + (index % 3) * 22}px`, "--i": index }} />
          ))}
        </div>
      );
    case "leads":
      return (
        <div className="uc-lead">
          {["Budget", "Timeline", "Decision maker"].map((label, index) => (
            <p key={label} style={{ "--i": index }}>
              <span>
                <SiteIcon name="check" size={12} />
              </span>
              {label}
              <i />
            </p>
          ))}
          <svg className="uc-score" viewBox="0 0 44 44" aria-hidden="true">
            <circle cx="22" cy="22" r="18" />
            <circle cx="22" cy="22" r="18" className="uc-score-fill" pathLength="1" />
          </svg>
        </div>
      );
    default:
      return (
        <div className="uc-flow">
          {["Trigger", "Call", "Action"].map((label, index) => (
            <span key={label} style={{ "--i": index }}>
              {label}
            </span>
          ))}
          <i className="uc-flow-dot" />
        </div>
      );
  }
}

function Card({ item, index }) {
  const ref = useRef(null);
  const box = useRef(null);
  const frame = useRef(0);

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  // Tilt toward the pointer. Mouse only: touch scrolls the track instead. The card's size is read
  // once when the pointer arrives (not per move) so the tilt cannot feed back into the reading.
  const enter = (event) => {
    if (event.pointerType === "mouse") box.current = ref.current.getBoundingClientRect();
  };

  const move = (event) => {
    if (event.pointerType !== "mouse" || !box.current) return;

    const { left, top, width, height } = box.current;
    const x = ((event.clientX - left) / width) * 2 - 1;
    const y = ((event.clientY - top) / height) * 2 - 1;

    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      ref.current?.style.setProperty("--mx", x.toFixed(3));
      ref.current?.style.setProperty("--my", y.toFixed(3));
    });
  };

  const leave = () => {
    box.current = null;
    cancelAnimationFrame(frame.current);
    ref.current?.style.setProperty("--mx", "0");
    ref.current?.style.setProperty("--my", "0");
  };

  return (
    <li className="uc-slot">
      <article ref={ref} className="uc" onPointerEnter={enter} onPointerMove={move} onPointerLeave={leave}>
        <div className="uc-spot" aria-hidden="true" />
        <header>
          <span className="uc-index">{String(index + 1).padStart(2, "0")}</span>
          <span className="uc-icon">
            <SiteIcon name={item.icon} size={20} />
          </span>
        </header>

        <div className="uc-visual" aria-hidden="true">
          <Visual id={item.id} />
        </div>

        <div className="uc-body">
          <h3>{item.title}</h3>
          <p>{item.text}</p>
          <ul>
            {item.points.map((point) => (
              <li key={point}>{point}</li>
            ))}
          </ul>
        </div>
      </article>
    </li>
  );
}

export default function UseCases() {
  const ref = useRef(null);
  const trackRef = useRef(null);

  // The distance the track has to travel sideways, measured, not guessed: it depends on the
  // number of cards, their width and the screen. It also sets the section's height, so one
  // screen of scrolling equals one screen of drift.
  useEffect(() => {
    const track = trackRef.current;
    const section = ref.current;
    const measure = () => section.style.setProperty("--travel", Math.max(0, track.scrollWidth - window.innerWidth).toString());
    const observer = new ResizeObserver(measure);

    observer.observe(track);
    window.addEventListener("resize", measure);
    measure();

    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);

  // Off-screen cards do not animate: `is-live` (see the CSS) only holds while the section is in view.
  useSectionProgress(ref, (progress, { inView }) => {
    ref.current?.style.setProperty("--p", progress.toFixed(4));
    ref.current?.classList.toggle("is-live", inView);
  });

  return (
    <section ref={ref} className="cases" style={{ "--p": 0, "--travel": 0 }}>
      <div className="cases-pin">
        <ol ref={trackRef} className="cases-track" aria-label="What your agent can do">
          <li className="cases-intro">
            <p className="lp-eyebrow">
              <b>04</b> What it can do
            </p>
            <h2 className="lp-h2">
              What can your <em>agent</em> do?
            </h2>
            <p className="lp-lead">Anything a good person on the phone would do, at any hour, for everyone at once.</p>
            <span className="cases-hint" aria-hidden="true">
              Keep scrolling <i />
            </span>
          </li>

          {CASES.map((item, index) => (
            <Card key={item.id} item={item} index={index} />
          ))}
          <li className="cases-end" aria-hidden="true" />
        </ol>

        <div className="cases-progress" aria-hidden="true">
          <i />
        </div>
      </div>
    </section>
  );
}
