import Icon from "./Icon";

const prompts = [
  "Show pending applications",
  "Summarize this applicant",
  "What are the key risks?",
  "Call the applicant",
  "Schedule a follow-up call",
  "Show me today's activities",
];

export default function VoicePanel({
  callState,
  duration,
  onStart,
  onStop,
  onPrompt,
  onReset,
}) {
  const isActive =
    callState === "listening" ||
    callState === "speaking" ||
    callState === "processing";

  const stateText = {
    idle: "Ready to start",
    listening: "Listening...",
    speaking: "Speaking...",
    processing: "Processing...",
    ended: "Call ended",
  };

  return (
    <section className={`voice-panel state-${callState}`}>
      <div className="conversation-header">
        <span className="live-badge">
          <span className="online-dot" />
          {callState === "ended" ? "Call Completed" : "Live Conversation"}
        </span>

        {isActive && (
          <div className="timer">
            <span className="wave-mini">▮▯▮▮▯</span>
            {formatTime(duration)}
          </div>
        )}
      </div>

      <div className="voice-content">
        <h2>How can I help you today?</h2>

        <p className="voice-description">
          Speak naturally with the AI voice agent about any application,
          financials, risks, or review.
        </p>

        <button
          className={`microphone-button ${
            isActive ? "microphone-active" : ""
          }`}
          onClick={isActive ? onStop : onStart}
          aria-label={isActive ? "Stop conversation" : "Start conversation"}
        >
          <div className="mic-ring ring-one" />
          <div className="mic-ring ring-two" />

          <div className="mic-circle">
            <Icon name="microphone" size={58} />
          </div>
        </button>

        <div className="voice-state">{stateText[callState]}</div>

        {isActive && (
          <button className="stop-button" onClick={onStop}>
            <span className="stop-square" />
            Tap to stop
          </button>
        )}

        {callState === "ended" && (
          <button className="new-call-button" onClick={onReset}>
            Start New Conversation
          </button>
        )}
      </div>

      <div className="suggestions">
        <div className="suggestion-label">💡 Try saying:</div>

        <div className="prompt-list">
          {prompts.map((prompt) => (
            <button key={prompt} onClick={() => onPrompt(prompt)}>
              "{prompt}"
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

function formatTime(seconds) {
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;

  return `${String(minutes).padStart(2, "0")}:${String(
    remaining
  ).padStart(2, "0")}`;
}