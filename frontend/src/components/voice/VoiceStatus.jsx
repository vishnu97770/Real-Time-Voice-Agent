import { VOICE_STATE_LABEL } from "../../runtime/voiceState.js";

// The spoken status line: a state-colored dot and a label, with a calm animated
// transition between states. Kept to a single live region so the current state
// is the only thing ever announced.
export default function VoiceStatus({ state = "idle", label = null, className = "", quiet = false }) {
  return (
    <div className={`voice-status vs-${state} ${className}`} role="status" aria-live={quiet ? "off" : "polite"}>
      <span className="vs-dot" aria-hidden="true" />
      <span className="vs-label">{label ?? VOICE_STATE_LABEL[state]}</span>
    </div>
  );
}