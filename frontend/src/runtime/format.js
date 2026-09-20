export function formatTime(seconds) {
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;

  return `${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`;
}

export function formatDateTime(timestamp) {
  return new Date(timestamp).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatLongDate(timestamp) {
  return new Date(timestamp).toLocaleDateString("en-IN", {
    weekday: "long",
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export function formatClock(timestamp) {
  return new Date(timestamp).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Who the console is working as. The sign-in only has an email, so the name is
// its local part; with no sign-in (offline mode) it is just "Operator".
export function displayName(user) {
  const local = user?.email?.split("@")[0]?.trim();

  return local || "Operator";
}

export const CALL_STATE_TEXT = {
  idle: "Ready to start",
  connecting: "Connecting...",
  listening: "Listening...",
  speaking: "Speaking...",
  processing: "Processing...",
  ended: "Call ended",
};
