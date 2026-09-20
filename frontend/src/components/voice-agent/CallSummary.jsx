import Icon from "./Icon";
import { flowStep } from "../../runtime/callRecord.js";
import { formatDateTime, formatTime } from "../../runtime/format.js";

const STEPS = ["Configure", "Start Conversation", "Complete Call", "Review Summary"];

const BADGE = {
  idle: ["Not started", "badge-idle"],
  live: ["Live", "badge-info"],
  summarizing: ["Summarizing", "badge-info"],
  completed: ["Call Completed", "badge-ok"],
  unavailable: ["No summary", "badge-warn"],
};

const VIEWS = [
  ["summary", "Summary"],
  ["transcript", "Transcript"],
  ["audit", "Audit trail"],
];

function Flow({ step }) {
  return (
    <ol className="flow" aria-label="Call workflow">
      {STEPS.map((name, index) => (
        <li key={name} className={index < step ? "is-done" : index === step ? "is-current" : ""}>
          <span className="flow-dot">{index < step ? <Icon name="check" size={12} /> : index + 1}</span>
          {name}
        </li>
      ))}
    </ol>
  );
}

function Empty({ record, onConfigure }) {
  const { agent, status } = record;

  if (status === "live") {
    return (
      <p className="summary-empty">
        The conversation with {agent.agentName} is in progress. The summary is written when the call ends.
      </p>
    );
  }

  if (status === "summarizing") {
    return <p className="summary-empty">Writing the call summary...</p>;
  }

  if (status === "unavailable") {
    return (
      <p className="summary-empty">
        {record.error ?? "No call summary is available for this call."} Start a new conversation to try again.
      </p>
    );
  }

  return (
    <div className="summary-empty">
      <p>
        {agent.configured
          ? `${agent.agentName} is configured as ${agent.role}. Start a conversation and its summary will appear here when the call ends.`
          : `No agent is configured yet, so calls use the built-in ${agent.agentName} defaults. Configure an agent to say what it should do.`}
      </p>

      {!agent.configured && (
        <button type="button" className="config-open summary-configure" onClick={onConfigure}>
          Configure Agent
          <Icon name="arrowRight" size={16} />
        </button>
      )}
    </div>
  );
}

function Transcript({ transcript }) {
  if (!transcript.length) return <p className="summary-empty">This call has no transcript.</p>;

  return (
    <ol className="detail-list">
      {transcript.map((message) => (
        <li key={message.id}>
          <span>{message.time}</span>
          <strong>{message.speaker}</strong>
          {message.text}
        </li>
      ))}
    </ol>
  );
}

function Audit({ audit }) {
  if (!audit.length) return <p className="summary-empty">This call has no audit events.</p>;

  return (
    <ol className="detail-list">
      {audit.map((event, index) => (
        <li key={index}>
          <span>{new Date(event.at).toLocaleTimeString("en-GB")}</span>
          <strong>{event.type}</strong>
          {event.tool ?? event.direction ?? event.text ?? ""}
        </li>
      ))}
    </ol>
  );
}

function Section({ icon, title, children }) {
  return (
    <section className="summary-block">
      <h4>
        <Icon name={icon} size={18} />
        {title}
      </h4>
      {children}
    </section>
  );
}

// What was said and done in the call between the person and the configured
// agent. Every label comes from the record, so it reads the same for any domain.
export default function CallSummary({ record, configured, view, onViewChange, onConfigure }) {
  const { agent, status } = record;
  const [badgeText, badgeClass] = BADGE[status];
  const completed = status === "completed";
  const shown = completed ? view : "summary";

  return (
    <section className="call-summary-card" id="call-summary" aria-labelledby="call-summary-title">
      <header className="call-summary-head">
        <span className="summary-icon">
          <Icon name="document" size={22} />
        </span>

        <div>
          <h3 id="call-summary-title">Call Summary</h3>
          <p>Summary of the conversation between you and the agent.</p>
        </div>

        <span className={`status-badge ${badgeClass}`}>{badgeText}</span>
      </header>

      <Flow step={flowStep(status, configured)} />

      <div className="call-headline">
        <span className="call-headline-icon">
          <Icon name="agent" size={26} />
        </span>

        <div>
          <h4>{record.title}</h4>

          <ul className="call-headline-meta">
            <li>
              <Icon name="calendar" size={15} />
              {record.startedAt ? formatDateTime(record.startedAt) : "Not started"}
            </li>
            <li>
              <Icon name="clock" size={15} />
              {record.durationSeconds === null ? "--:--" : formatTime(record.durationSeconds)}
            </li>
            <li>
              <Icon name="tag" size={15} />
              {agent.agentName}
            </li>
          </ul>
        </div>
      </div>

      {completed && (
        <div className="summary-tabs" role="tablist" aria-label="Call record view">
          {VIEWS.map(([id, name]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={shown === id}
              className={shown === id ? "is-active" : ""}
              onClick={() => onViewChange(id)}
            >
              {name}
            </button>
          ))}
        </div>
      )}

      {shown === "transcript" && <Transcript transcript={record.transcript} />}
      {shown === "audit" && <Audit audit={record.audit} />}

      {shown === "summary" && !completed && <Empty record={record} onConfigure={onConfigure} />}

      {shown === "summary" && completed && (
        <>
          <Section icon="document" title="Conversation Summary">
            <p className="summary-text">{record.summaryText}</p>
          </Section>

          <Section icon="tag" title="Key Topics Discussed">
            {record.topics.length ? (
              <ul className="topic-chips">
                {record.topics.map((topic) => (
                  <li key={topic}>{topic}</li>
                ))}
              </ul>
            ) : (
              <p className="summary-empty">No topics came up in this call.</p>
            )}
          </Section>

          <Section icon="check" title="Actions / Outcome">
            <ul className="check-list">
              {record.actions.map((action) => (
                <li key={action}>
                  <span className="check-mark">
                    <Icon name="check" size={13} />
                  </span>
                  {action}
                </li>
              ))}
            </ul>

            {record.nextSteps.length > 0 && (
              <>
                <h5>Follow-up / Next Steps</h5>
                <ul className="plain-list">
                  {record.nextSteps.map((step) => (
                    <li key={step}>{step}</li>
                  ))}
                </ul>
              </>
            )}
          </Section>

          {record.outcome && (
            <div className={`outcome ${record.outcome.ok ? "" : "outcome-warn"}`} role="status">
              <span className="outcome-icon">
                <Icon name={record.outcome.ok ? "chart" : "cross"} size={22} />
              </span>
              <div>
                <strong>{record.outcome.headline}</strong>
                <p>{record.outcome.detail}</p>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
