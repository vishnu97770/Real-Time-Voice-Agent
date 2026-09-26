// The comparison, as data. Each lane says where (0..100, along the conversation) its bar starts
// and ends in each mode. Kept out of the component so the two arrangements can be checked:
// a turn-based bot must never overlap its stages, and a real-time agent must.

export const LANES = [
  { id: "listen", label: "Listen", note: "Always on. It hears you even while it speaks.", turn: [0, 32], realtime: [0, 44], again: [62, 100] },
  { id: "understand", label: "Understand", note: "Meaning is worked out as the words arrive.", turn: [38, 50], realtime: [8, 50] },
  { id: "think", label: "Think", note: "Looks things up without pausing the conversation.", turn: [52, 70], realtime: [22, 60] },
  { id: "respond", label: "Respond", note: "Starts on the first sentence. Stops when you speak.", turn: [76, 100], realtime: [46, 92], cut: 62 },
];

export const GAP = { from: 32, to: 76 }; // the silence in a turn-based exchange

// How much the wave moves at position u (0..1) of the conversation. Turn-based: loud while
// someone is talking, flat in the gaps. Real-time: continuous.
export function envelopeAt(mode, u, interrupted = false) {
  const x = u * 100;

  if (mode === "realtime") {
    const base = 0.5 + 0.3 * Math.sin(u * 9);

    // after an interruption the agent's voice falls away at once and the caller's takes over
    return interrupted && x > 62 ? 0.42 + 0.2 * Math.sin(u * 14) : base;
  }

  if (x < GAP.from) return 0.55;
  if (x < GAP.to) return 0.03;

  return 0.55;
}

export function verdictFor(mode, interrupted) {
  if (mode === "turn") {
    return interrupted
      ? "You interrupt. It keeps talking until it has finished its turn."
      : "It waits for you to stop, then processes, then answers. In between, there is silence.";
  }

  return interrupted
    ? "You interrupt. It stops mid-sentence and listens."
    : "Listening, understanding and thinking overlap. It starts answering while it is still working.";
}

// Do any two lanes' bars overlap in time, in this mode?
export function overlaps(mode) {
  const bars = LANES.map((lane) => lane[mode === "turn" ? "turn" : "realtime"]);

  for (let a = 0; a < bars.length; a += 1) {
    for (let b = a + 1; b < bars.length; b += 1) {
      if (bars[a][0] < bars[b][1] && bars[b][0] < bars[a][1]) return true;
    }
  }

  return false;
}
