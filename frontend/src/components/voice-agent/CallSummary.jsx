export default function CallSummary({ duration }) {
  return (
    <section className="summary-card">
      <div className="summary-header">
        <div>
          <span className="card-icon">▤</span>
          <h3>
            Call Summary <span>(After Call Ends)</span>
          </h3>
        </div>

        <span className="completed-badge">
          ✓ Call Completed
        </span>
      </div>

      <div className="call-meta">
        <div>
          <span>Call ID</span>
          <strong>CALL-20260907-001</strong>
        </div>

        <div>
          <span>Date & Time</span>
          <strong>Sep 7, 2026, 03:14 PM</strong>
        </div>

        <div>
          <span>Duration</span>
          <strong>{formatTime(duration)}</strong>
        </div>

        <div>
          <span>Type</span>
          <strong>Outbound (AI → Applicant)</strong>
        </div>

        <div>
          <span>Status</span>
          <strong className="success-text">Completed</strong>
        </div>
      </div>

      <div className="summary-section">
        <h4>▣ Summary</h4>

        <p>
          Discussed the pending bank statement with the applicant.
          The applicant confirmed that the document will be uploaded
          for further underwriting review.
        </p>
      </div>

      <div className="summary-grid">
        <SummaryBox
          title="Key Points"
          items={[
            "Bank statement is pending",
            "Applicant will upload tomorrow",
            "No changes in financial information",
            "Applicant was cooperative",
          ]}
        />

        <SummaryBox
          title="Actions Taken"
          items={[
            "Call completed",
            "Document request recorded",
            "Application note updated",
            "Follow-up task created",
          ]}
        />

        <SummaryBox
          title="Follow-up / Next Steps"
          items={[
            "Verify bank statement",
            "Continue underwriting review",
            "Schedule follow-up if required",
          ]}
        />

        <SummaryBox
          title="Evidence / References"
          items={[
            "Application: APP-1024",
            "Document checklist",
            "Call transcript",
            "Agent tool logs",
          ]}
        />
      </div>

      <div className="summary-actions">
        <button>▣ View Full Transcript</button>
        <button>↓ Download Summary</button>
        <button className="primary-action">↗ Open Application</button>
      </div>
    </section>
  );
}

function SummaryBox({ title, items }) {
  return (
    <div className="summary-box">
      <h4>{title}</h4>

      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function formatTime(seconds) {
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;

  return `${String(minutes).padStart(2, "0")}:${String(
    remaining
  ).padStart(2, "0")}`;
}