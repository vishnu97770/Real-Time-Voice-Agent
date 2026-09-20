import { useEffect, useRef } from "react";

function ToolCall({ call }) {
  const args = Object.entries(call.args ?? {})
    .map(([key, value]) => `${key}: ${value}`)
    .join(", ");

  return (
    <details className={`tool-call ${call.guarded ? "tool-call-guarded" : ""}`}>
      <summary>
        <code>{call.name}</code>
        {args && <span className="tool-args">({args})</span>}
        {call.guarded && <span className="tool-tag">confirmed action</span>}
      </summary>

      <pre>{JSON.stringify(call.result, null, 2)}</pre>
    </details>
  );
}

export default function AgentResponse({
  messages,
  pending,
  onRespond,
  onClear,
  canClear,
  title = "Agent Response",
  showTools = true,
  agentName = null,
  status = null,
}) {
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, pending]);

  return (
    <section className="response-card">
      <div className="card-header">
        <div>
          <span className="card-icon">▤</span>
          <h3>{title} <span>(Live)</span></h3>
        </div>

        {onClear && (
          <button className="clear-button" onClick={onClear} disabled={!canClear}>
            Clear
          </button>
        )}
      </div>

      {status && (
        <div className="live-status" role="status">
          <span className="online-dot" />
          {status}
        </div>
      )}

      <div className="transcript">
        {messages.length === 0 ? (
          <div className="empty-transcript">
            Your conversation transcript will appear here...
          </div>
        ) : (
          messages.map((message) => (
            <div
              className={`message ${
                message.speaker === "Agent" ? "agent-message" : "user-message"
              } ${message.blocked ? "blocked-message" : ""}`}
              key={message.id}
            >
              <div className="message-top">
                <strong>
                  {message.speaker === "Agent" && agentName ? agentName : message.speaker}
                  {message.blocked && <em className="message-tag">guardrail</em>}
                  {message.interrupted && <em className="message-tag">interrupted</em>}
                </strong>
                <span>{message.time}</span>
              </div>

              <p>{message.text}</p>

              {showTools && message.toolCalls?.map((call, index) => (
                <ToolCall call={call} key={index} />
              ))}
            </div>
          ))
        )}

        {pending && (
          <div className="confirm-card" role="alertdialog" aria-label="Confirm action">
            <div className="confirm-title">Confirmation needed</div>
            <p>The agent wants to {pending.label}.</p>

            <div className="confirm-buttons">
              <button className="confirm-yes" onClick={() => onRespond("Yes, confirm")}>
                Yes, confirm
              </button>
              <button className="confirm-no" onClick={() => onRespond("No, cancel")}>
                No, cancel
              </button>
            </div>
          </div>
        )}

        <div ref={endRef} />
      </div>
    </section>
  );
}
