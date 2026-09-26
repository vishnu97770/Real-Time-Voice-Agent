// How far through a tall section the viewport is, as 0..1.
//
// A "pinned" section is taller than the screen and holds a sticky child; while it scrolls past,
// progress goes 0 -> 1 across the distance the sticky child stays fixed (height - viewport).
// A section that is not taller than the screen has no travel, so it reports 0 before its top
// reaches the screen top and 1 after.

const clamp01 = (value) => Math.min(1, Math.max(0, value));

export function sectionProgress(rectTop, rectHeight, viewportHeight) {
  const travel = rectHeight - viewportHeight;

  if (travel <= 0) return rectTop <= 0 ? 1 : 0;

  return clamp01(-rectTop / travel);
}

// Which of `count` equal steps a progress value is on (0..count-1).
export function stepOf(progress, count) {
  return Math.min(count - 1, Math.floor(clamp01(progress) * count));
}

// Maps progress onto a sub-range, so one scroll can drive several phases in turn.
export function phase(progress, start, end) {
  return end === start ? (progress >= end ? 1 : 0) : clamp01((progress - start) / (end - start));
}

export const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

// How far a section has scrolled off the top of the screen, as 0..1 (0 while it is at the top,
// 1 once it has fully left). Used for things that peel away as you leave them, like the hero.
export function leaveProgress(rectTop, rectHeight) {
  return rectHeight <= 0 ? 0 : clamp01(-rectTop / rectHeight);
}
