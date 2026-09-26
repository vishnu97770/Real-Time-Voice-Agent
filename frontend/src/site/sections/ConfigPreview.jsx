import { useEffect, useRef, useState } from "react";
import SiteIcon from "../icons.jsx";
import { useClock } from "../hooks/useClock.js";
import { progressAt, typedSlice, typingDuration } from "../typing.js";
import "./ConfigPreview.css";

// The same fields as the real "Agent Use Case & Configuration" form, filled in by a script:
// the visitor watches an agent being defined and comes out with the sense "I can make one."
// The card beside it assembles from the very same values.

const INDUSTRY = "Healthcare";
const NAME = "CareCall";
const ROLE = "Appointment Coordinator";
const PURPOSE = "Confirm appointments and help patients reschedule, by phone, in their own language.";
const DUTIES = "Verify who I'm speaking to before sharing details. Hand over to staff for any clinical question.";
const USERS = ["Patients", "Families"];
const TASKS = ["Schedule appointments", "Answer user questions", "Perform workflow actions"];

// When each part starts (ms). Text parts type at 34 characters a second.
const AT = { industry: 300, name: 900, role: 1600, purpose: 2700 };

AT.users = AT.purpose + typingDuration(PURPOSE) + 350;
AT.tasks = AT.users + 900;
AT.duties = AT.tasks + 1300;
AT.deploy = AT.duties + typingDuration(DUTIES) + 700;
AT.live = AT.deploy + 500;

const DURATION = AT.live + 1600;

const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

function Field({ label, focus, children, wide = false }) {
  return (
    <div className={`cp-field ${focus ? "is-focus" : ""} ${wide ? "is-wide" : ""}`}>
      <label>{label}</label>
      <div className="cp-control">{children}</div>
    </div>
  );
}

export default function ConfigPreview() {
  const ref = useRef(null);
  const [inView, setInView] = useState(false);
  const [reduced] = useState(reducedMotion);
  const [elapsed, restart] = useClock({ active: inView && !reduced, duration: DURATION });
  const t = reduced ? DURATION : elapsed;

  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { threshold: 0.4 });

    observer.observe(ref.current);

    return () => observer.disconnect();
  }, []);

  const name = typedSlice(NAME, t - AT.name);
  const role = typedSlice(ROLE, t - AT.role);
  const purpose = typedSlice(PURPOSE, t - AT.purpose);
  const duties = typedSlice(DUTIES, t - AT.duties);
  const users = USERS.filter((_, index) => t >= AT.users + index * 250);
  const tasks = TASKS.filter((_, index) => t >= AT.tasks + index * 300);
  const deployed = t >= AT.live;
  const pressing = t >= AT.deploy && !deployed;

  // which field the "cursor" is in, for the focus ring
  const focus =
    t >= AT.duties && t < AT.deploy ? "duties"
    : t >= AT.tasks && t < AT.duties ? "tasks"
    : t >= AT.users && t < AT.tasks ? "users"
    : t >= AT.purpose && t < AT.users ? "purpose"
    : t >= AT.role && t < AT.purpose ? "role"
    : t >= AT.name && t < AT.role ? "name"
    : t >= AT.industry && t < AT.name ? "industry"
    : "";

  const caret = (field, text, full) => focus === field && text.length < full.length && <span className="cp-caret" />;

  return (
    <section ref={ref} className="cp">
      <div className="lp-wrap cp-grid">
        <div className="cp-copy">
          <p className="lp-eyebrow">
            <b>06</b> Build an agent
          </p>
          <h2 className="lp-h2">
            Describe the job. <em>Get an agent.</em>
          </h2>
          <p className="lp-lead">There is no prompt to engineer. Say what the agent is for, who it talks to and what it is responsible for, and it is ready to test.</p>

          <div className={`cp-card ${deployed ? "is-live" : ""}`} aria-label="The agent, as configured so far">
            <span className="cp-card-av">
              <SiteIcon name="agent" size={22} />
            </span>
            <span className="cp-card-id">
              <b>{name || "Unnamed agent"}{name.length < NAME.length && name && <span className="cp-caret" />}</b>
              <em>{role || "Role not set"}</em>
            </span>
            <span className={`cp-status ${deployed ? "is-live" : ""}`}>
              <i /> {deployed ? "Live" : "Draft"}
            </span>
            <span className="cp-card-tags">
              {t >= AT.industry && <span>{INDUSTRY}</span>}
              {users.map((item) => (
                <span key={item}>{item}</span>
              ))}
            </span>
            <span className="cp-card-meta">
              <b>{tasks.length}</b> tasks · <b>{deployed ? "Ready for calls" : "Not deployed"}</b>
            </span>
          </div>
        </div>

        <div className="cp-window" role="group" aria-label="Agent use case and configuration, an illustration">
          <header className="cp-head">
            <div>
              <b>Agent Use Case &amp; Configuration</b>
              <span>Tell us what you want your voice agent to do.</span>
            </div>
            <button type="button" className="cp-replay" onClick={restart} disabled={t < DURATION}>
              Replay
            </button>
          </header>

          <div className="cp-form">
            <Field label="Use case / industry" focus={focus === "industry"}>
              <span className={`cp-select ${t < AT.industry ? "is-empty" : ""}`}>{t >= AT.industry ? INDUSTRY : "Select a use case…"}</span>
            </Field>
            <Field label="Agent name" focus={focus === "name"}>
              <span className={name ? "" : "is-empty"}>{name || "e.g. MineAssist"}{caret("name", name, NAME)}</span>
            </Field>
            <Field label="Agent role" focus={focus === "role"}>
              <span className={role ? "" : "is-empty"}>{role || "What role should it perform?"}{caret("role", role, ROLE)}</span>
            </Field>
            <Field label="Purpose" focus={focus === "purpose"} wide>
              <span className={purpose ? "" : "is-empty"}>{purpose || "What should it help users accomplish?"}{caret("purpose", purpose, PURPOSE)}</span>
            </Field>
            <Field label="Target users" focus={focus === "users"}>
              <span className="cp-chips">
                {users.length === 0 && <span className="is-empty">Who does it talk to?</span>}
                {users.map((item) => (
                  <b key={item}>{item}</b>
                ))}
              </span>
            </Field>
            <Field label="Primary tasks" focus={focus === "tasks"}>
              <span className="cp-chips">
                {tasks.length === 0 && <span className="is-empty">What should it do?</span>}
                {tasks.map((item) => (
                  <b key={item}>{item}</b>
                ))}
              </span>
            </Field>
            <Field label="Responsibilities" focus={focus === "duties"} wide>
              <span className={duties ? "" : "is-empty"}>{duties || "What is it responsible for, and what must it never do?"}{caret("duties", duties, DUTIES)}</span>
            </Field>
          </div>

          <footer className="cp-foot">
            <span className={`cp-toast ${deployed ? "is-in" : ""}`} aria-live="polite">
              <SiteIcon name="check" size={15} /> {name || NAME} is live and ready for calls
            </span>
            <span className={`cp-deploy ${pressing ? "is-pressed" : ""} ${deployed ? "is-done" : ""}`} style={{ "--k": progressAt(t, AT.deploy, 300) }}>
              <SiteIcon name="rocket" size={16} /> {deployed ? "Deployed" : "Deploy agent"}
            </span>
          </footer>
        </div>
      </div>
    </section>
  );
}
