// "Typed" text for the scripted form: how much of `text` has appeared `elapsedMs` after it
// started, at `cps` characters a second. Pure, so the timing is testable and the component only draws.
export function typedSlice(text, elapsedMs, cps = 34) {
  if (elapsedMs <= 0) return "";

  return text.slice(0, Math.min(text.length, Math.floor((elapsedMs / 1000) * cps)));
}

// How long typing `text` takes, in ms.
export const typingDuration = (text, cps = 34) => Math.ceil((text.length / cps) * 1000);

// 0..1 for something that appears over `duration` ms starting at `start` (a chip, a fade).
export function progressAt(t, start, duration) {
  if (duration <= 0) return t >= start ? 1 : 0;

  return Math.min(1, Math.max(0, (t - start) / duration));
}
