import Icon from "./Icon";
import { formatDateTime, formatTime } from "../../runtime/format.js";

const BADGE = {
  idle: ["Not started", "badge-idle"],
  live: ["In progress", "badge-info"],
  summarizing: ["Finishing", "badge-info"],
  completed: ["Call Completed", "badge-ok"],
  unavailable: ["No summary", "badge-warn"],
};

// The result a business workflow would receive back after a call.
function downloadCallResult(summary) {
  const result = {
    call_id: summary.callId,
    profile: summary.profileId,
    started_at: new Date(summary.startedAt).toISOString(),
    duration_seconds: summary.durationSeconds,
    outcome: summary.outcome,
    summary: summary.summary,
    actions: summary.actionsTaken,
    evidence: summary.evidence,
    transcript: summary.transcript.map(({ speaker, text, time, interrupted }) => ({
      speaker,
      text,
      time,
      ...(interrupted && { interrupted }),
    })),
    audit_trail: summary.audit,
  };

  const url = URL.createObjectURL(
    new Blob([JSON.stringify(result, null, 2)], { type: "application/json" })
  );
  const link = document.createElement("a");

  link.href = url;
  link.download = `${summary.callId}.json`;
  link.click();
  URL.revokeObjectURL(url);
}

function Detail({ label, wide = false, icon, children }) {
  return (
    <div className={wide ? "detail-wide" : undefined}>
      <dt>{label}</dt>
      <dd>
        {icon && <Icon name={icon} size={16} />}
        {children}
      </dd>
    </div>
  );
}

// The call as a phone-call record: who, when, how long, and what it was for.
export default function CallDetails({ record, summary, onViewTranscript }) {
  const [badgeText, badgeClass] = BADGE[record.status];
  const completed = record.status === "completed";
  const blank = "—";

  return (
    <section className="details-card" aria-labelledby="call-details-title">
      <header className="details-head">
        <h3 id="call-details-title">
          <Icon name="phone" size={20} />
          Call Details
        </h3>

        <span className={`status-badge ${badgeClass}`}>{badgeText}</span>
      </header>

      <dl className="detail-grid">
        <Detail label="Call ID">{record.callId ?? blank}</Detail>
        <Detail label="Date & Time">{record.startedAt ? formatDateTime(record.startedAt) : blank}</Detail>
        <Detail label="Duration">
          {record.durationSeconds === null ? blank : formatTime(record.durationSeconds)}
        </Detail>
        <Detail label="To / Contact" icon="user">
          {record.contact}
        </Detail>
        <Detail label="Role / Field" icon="settings" wide>
          {record.agent.role}
          {record.agent.industry && <span className="detail-sub">{record.agent.industry}</span>}
        </Detail>
      </dl>

      <section className="notes">
        <h4>
          <Icon name="document" size={18} />
          Notes
        </h4>

        <p>{record.notes || "Notes are added when the call ends."}</p>
      </section>

      <div className="details-actions">
        <button type="button" disabled={!completed} onClick={onViewTranscript}>
          <Icon name="document" size={16} />
          View Full Transcript
        </button>

        <button
          type="button"
          className="primary-action"
          disabled={!completed || !summary}
          onClick={() => downloadCallResult(summary)}
        >
          <Icon name="download" size={16} />
          Download Summary
        </button>
      </div>
    </section>
  );
}
