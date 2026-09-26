// The visual states the voice system can present. Everything here is derived
// from real application state (the call state machine and the microphone), never
// invented telemetry: the UI only animates what the app actually knows.

export const VOICE_STATES = [
  "idle",
  "connecting",
  "listening",
  "thinking",
  "speaking",
  "connected",
  "disconnected",
  "error",
];

// Map the call state machine to a presentation state. An ended call becomes an
// error only when the transport actually reported one.
export function visualForCallState(callState, callError = false) {
  switch (callState) {
    case "connecting":
      return "connecting";
    case "listening":
      return "listening";
    case "speaking":
      return "speaking";
    case "processing":
      return "thinking";
    case "ended":
      return callError ? "error" : "disconnected";
    case "idle":
    default:
      return "idle";
  }
}

export const VOICE_STATE_LABEL = {
  idle: "Ready",
  connecting: "Connecting…",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  connected: "Connected",
  disconnected: "Disconnected",
  error: "Error",
};