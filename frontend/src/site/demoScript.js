// The scripted conversation on the landing page, as pure data plus pure functions, so the timing
// can be tested and the component only has to draw one frame at a time.
//
// It is an illustration of one call, not a recording: the words are written, the timings are
// chosen to feel like speech. What it shows (listen, work out the intent, check something,
// ask before changing anything, confirm) is how the real agent behaves.

export const DEMO_SCRIPT = [
  { who: "user", text: "Hi, I need to schedule my appointment." },
  { who: "agent", text: "Sure. What day works for you?" },
  { who: "user", text: "Tomorrow afternoon." },
  { who: "action", label: "Checking the calendar", detail: "find_slots(day: tomorrow, part: afternoon)", result: "3:00 PM is open" },
  { who: "agent", text: "I found a 3 PM slot. Would you like me to book it?" },
  { who: "user", text: "Yes, please." },
  { who: "action", label: "Booking the slot", detail: "book_slot(tomorrow, 3:00 PM)", result: "Confirmed" },
  { who: "agent", text: "Done. You're booked for 3 PM tomorrow." },
  { who: "done", label: "Appointment scheduled", detail: "Tomorrow · 3:00 PM" },
];

const OPENING_PAUSE = 500;
const GAP = 480;
const LEAD = { user: 350, agent: 650 }; // before the words start: hearing, or thinking
const USER_MS_PER_WORD = 150;
const AGENT_MS_PER_CHAR = 30;
const ACTION_MS = 1300;
const DONE_MS = 700;

const wordCount = (text) => text.split(/\s+/).filter(Boolean).length;

export function buildTimeline(script = DEMO_SCRIPT) {
  let cursor = OPENING_PAUSE;

  const items = script.map((step, index) => {
    const start = cursor;
    let leadEnd = start;
    let end;

    if (step.who === "user") {
      leadEnd = start + LEAD.user;
      end = leadEnd + wordCount(step.text) * USER_MS_PER_WORD;
    } else if (step.who === "agent") {
      leadEnd = start + LEAD.agent;
      end = leadEnd + step.text.length * AGENT_MS_PER_CHAR;
    } else if (step.who === "action") {
      end = start + ACTION_MS;
    } else {
      end = start + DONE_MS;
    }

    cursor = end + GAP;

    return { index, step, start, leadEnd, end };
  });

  // No trailing gap after the last item: the script is over when its last item is.
  return { items, duration: items.at(-1).end };
}

// The part of a line that has been "said" so far. The caller's words arrive whole; the agent's
// are typed a character at a time.
export function partialText(step, progress) {
  if (progress >= 1) return step.text;
  if (progress <= 0) return "";

  if (step.who === "user") {
    const words = step.text.split(/\s+/);

    return words.slice(0, Math.max(1, Math.ceil(words.length * progress))).join(" ");
  }

  return step.text.slice(0, Math.ceil(step.text.length * progress));
}

// Everything the component needs to draw the moment `t` (ms since the script began):
//   shown   the items that have started, each with { phase: lead | run | done, progress: 0..1 }
//   status  idle | listening | thinking | speaking | acting | done
//   active  the item currently in progress, or null
//   speaking  true while someone is actually producing words (the waveform follows this)
export function frameAt(timeline, t) {
  const shown = [];
  let active = null;

  for (const item of timeline.items) {
    if (t < item.start) break;

    let phase;
    let progress;

    if (t < item.leadEnd) {
      phase = "lead";
      progress = 0;
    } else if (t < item.end) {
      phase = "run";
      progress = (t - item.leadEnd) / (item.end - item.leadEnd);
    } else {
      phase = "done";
      progress = 1;
    }

    const entry = { index: item.index, step: item.step, phase, progress };

    shown.push(entry);

    if (phase !== "done") active = entry;
  }

  const finished = t >= timeline.duration;

  if (active) {
    const { who } = active.step;
    const status =
      who === "user" ? "listening" : who === "agent" ? (active.phase === "lead" ? "thinking" : "speaking") : who === "action" ? "acting" : "done";

    return { shown, status, active, speaking: active.phase === "run" && (who === "user" || who === "agent") };
  }

  if (finished) return { shown, status: "done", active: null, speaking: false };
  if (!shown.length) return { shown, status: "idle", active: null, speaking: false };

  // Between two lines: the next speaker is about to start.
  const next = timeline.items[shown.length];

  return { shown, status: next?.step.who === "user" ? "listening" : "thinking", active: null, speaking: false };
}
