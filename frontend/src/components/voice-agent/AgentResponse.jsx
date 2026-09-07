export default function AgentResponse({ messages, onClear }) {
  return (
    <section className="response-card">
      <div className="card-header">
        <div>
          <span className="card-icon">▤</span>
          <h3>Agent Response <span>(Live)</span></h3>
        </div>

        <button className="clear-button" onClick={onClear}>
          Clear
        </button>
      </div>

      <div className="transcript">
        {messages.length === 0 ? (
          <div className="empty-transcript">
            Your conversation transcript will appear here...
          </div>
        ) : (
          messages.map((message, index) => (
            <div
              className={`message ${
                message.speaker === "Agent" ? "agent-message" : "user-message"
              }`}
              key={index}
            >
              <div className="message-top">
                <strong>{message.speaker}</strong>
                <span>{message.time}</span>
              </div>

              <p>{message.text}</p>
            </div>
          ))
        )}
      </div>
    </section>
  );
}