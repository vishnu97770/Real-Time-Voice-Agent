// How each agent mode drives the orb. Pure, so the shapes can be tested and tuned without WebGL.

const clamp01 = (value) => Math.min(1, Math.max(0, value));

// A believable stand-in for speech loudness: syllable-rate bursts (~4-5 Hz) gated by slower
// phrase-level swells. Returns 0..1.
export function speechEnvelope(t) {
  const syllables = 0.5 + 0.5 * Math.sin(t * 5.3) * Math.sin(t * 1.9 + 0.6);
  const phrases = 0.65 + 0.35 * Math.sin(t * 0.83 + 1.1);

  return clamp01(syllables * phrases * 1.25);
}

// The energy (0..1) the orb should move towards, and how much it "thinks" (swirl), per mode.
export function targetsFor(mode, t) {
  switch (mode) {
    case "listening":
      return { energy: 0.14 + 0.34 * speechEnvelope(t), think: 0 };
    case "thinking":
      return { energy: 0.16 + 0.05 * Math.sin(t * 7), think: 1 };
    case "speaking":
      return { energy: 0.24 + 0.58 * speechEnvelope(t + 3.7), think: 0 };
    default:
      return { energy: 0.07 + 0.035 * Math.sin(t * 0.9), think: 0 }; // idle: slow breathing
  }
}

// The loudness of a WebAudio time-domain buffer (bytes centred on 128), as 0..1.
export function rmsOfBytes(bytes) {
  if (!bytes.length) return 0;

  let sum = 0;

  for (let index = 0; index < bytes.length; index += 1) {
    const centred = (bytes[index] - 128) / 128;
    sum += centred * centred;
  }

  return clamp01(Math.sqrt(sum / bytes.length) * 3.2);
}

// Where the orb should be, from the element it is anchored to. Pixel rect -> world units, for a
// camera that sees `visibleHeight` world units over `viewportHeight` pixels.
export function worldFromRect(rect, viewport, visibleHeight) {
  const perPixel = visibleHeight / viewport.height;
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;

  return {
    x: (cx - viewport.width / 2) * perPixel,
    y: -(cy - viewport.height / 2) * perPixel,
    scale: (Math.min(rect.width, rect.height) * perPixel) / 2, // the sphere has radius 1
  };
}

// Which anchor the orb should sit on: the visible one closest to the middle of the screen.
// Returns its index in `rects`, or -1 when none is on screen at all.
export function pickAnchor(rects, viewport) {
  let best = -1;
  let bestDistance = Infinity;

  rects.forEach((rect, index) => {
    if (rect.bottom <= 0 || rect.top >= viewport.height || rect.width <= 0) return;

    const dx = rect.left + rect.width / 2 - viewport.width / 2;
    const dy = rect.top + rect.height / 2 - viewport.height / 2;
    const distance = Math.hypot(dx, dy);

    if (distance < bestDistance) {
      best = index;
      bestDistance = distance;
    }
  });

  return best;
}
