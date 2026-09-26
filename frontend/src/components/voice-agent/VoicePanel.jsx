import { useState } from "react";
import Icon from "./Icon";
import CallVisualizer from "../voice/CallVisualizer";
import AiActivity from "../voice/AiActivity";
import { visualForCallState } from "../../runtime/voiceState.js";
import { voiceSupport } from "../../runtime/useVoice.js";
import { CALL_STATE_TEXT, formatTime } from "../../runtime/format.js";

// The call controls: start or stop the conversation, type instead of speaking, or
// pick a suggested question. The conversation itself is shown in Agent Response
// and the outcome in Call Summary.
export default function VoicePanel({
  profile,
  callState,
  duration,
  interim,
  micState,
  onStart,
  onStop,
  onSend,
  onReset,
  onViewHistory,
  acting = false,
}) {
  const [draft, setDraft] = useState("");

  const isActive =
    callState === "connecting" ||
    callState === "listening" ||
    callState === "speaking" ||
    callState === "processing";

  const submit = (event) => {
    event.preventDefault();
    onSend(draft);
    setDraft("");
  };

  return (
    <section className={`voice-panel voice-dock state-${callState}`} aria-label="Call controls">
      <div className="dock-main">
        <div className={`dock-stage is-${callState}`}>
          <button
            type="button"
            className="voice-orb-button"
            onClick={isActive ? onStop : callState === "ended" ? onReset : onStart}
            aria-label={
              isActive ? "Stop conversation" : callState === "ended" ? "Start new conversation" : "Start conversation"
            }
          >
            <CallVisualizer
              state={visualForCallState(callState)}
              orbIcon="microphone"
              orbLabel={CALL_STATE_TEXT[callState]}
              statusLabel={CALL_STATE_TEXT[callState]}
              size="xl"
            />
          </button>
        </div>

        {isActive && <AiActivity state={visualForCallState(callState)} acting={acting} />}

        <div className="dock-meta">
          {isActive ? (
            <>
              <div className="timer">{formatTime(duration)}</div>
              <div className="live-caption" aria-live="polite">
                {interim ? `“${interim}”` : " "}
              </div>
            </>
          ) : (
            <div className="dock-hint">Speak, or type below</div>
          )}
        </div>

        <div className="dock-actions">
          {isActive ? (
            <button type="button" className="stop-button" onClick={onStop}>
              <span className="stop-square" />
              Tap to stop
            </button>
          ) : (
            <button
              type="button"
              className="start-button"
              onClick={callState === "ended" ? onReset : onStart}
            >
              <Icon name="microphone" size={18} />
              {callState === "ended" ? "Start New Conversation" : "Start Conversation"}
            </button>
          )}

          {!isActive && onViewHistory && (
            <button type="button" className="history-button" onClick={onViewHistory}>
              <Icon name="clock" size={18} />
              View Call History
            </button>
          )}
        </div>
      </div>

      {micState === "blocked" && (
        <p className="voice-notice">
          Microphone access is blocked. Allow it in your browser, or type below.
        </p>
      )}

      {!voiceSupport.recognition && (
        <p className="voice-notice">
          This browser has no speech recognition (use Chrome or Edge for voice).
          You can still type below.
        </p>
      )}

      <form className="type-row" onSubmit={submit}>
        <input
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={
            isActive ? "Or type your reply..." : "Type a message to start a call..."
          }
          disabled={callState === "ended"}
          aria-label="Type a message"
        />
        <button type="submit" disabled={!draft.trim() || callState === "ended"}>
          Send
        </button>
      </form>

      <div className="suggestions">
        <div className="suggestion-label">💡 Try saying:</div>

        <div className="prompt-list">
          {profile.prompts.map((prompt) => (
            <button
              key={prompt}
              onClick={() => onSend(prompt)}
              disabled={callState === "ended"}
            >
              "{prompt}"
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
