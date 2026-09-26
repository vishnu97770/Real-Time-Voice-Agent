// How much visual effort this device should be asked for. Decided once, from things the browser
// tells us; nothing is benchmarked.
//
//   off     no WebGL at all: reduced motion, data saver, a very weak device, or no WebGL support.
//           The page shows its CSS-only orb and never downloads the 3D code.
//   low     phones and small screens: a lighter scene (fewer vertices, particles, one ring).
//   medium  tablets and touch laptops.
//   high    everything else.

export function pickTier({ reducedMotion = false, saveData = false, cores = 4, memory = 4, width = 1280, coarse = false, webgl = true } = {}) {
  if (reducedMotion || saveData || !webgl) return "off";
  if (cores <= 2 || memory <= 2) return "off";
  if (width < 720) return "low";
  if (coarse || width < 1100 || cores <= 4) return "medium";

  return "high";
}

// Scene sizes per tier: sphere subdivision, particle count, halo rings, and the pixel-ratio cap.
export const TIER_SETTINGS = {
  low: { detail: 12, particles: 380, rings: 1, dpr: 1.25 },
  medium: { detail: 22, particles: 900, rings: 2, dpr: 1.5 },
  high: { detail: 32, particles: 1700, rings: 3, dpr: 2 },
};

export function readDevice() {
  const media = (query) => window.matchMedia?.(query).matches ?? false;

  return {
    reducedMotion: media("(prefers-reduced-motion: reduce)"),
    saveData: Boolean(navigator.connection?.saveData),
    cores: navigator.hardwareConcurrency ?? 4,
    memory: navigator.deviceMemory ?? 4,
    width: window.innerWidth,
    coarse: media("(pointer: coarse)"),
    // Cheap check only; a context that fails to start is caught when the scene is created.
    webgl: typeof window.WebGLRenderingContext !== "undefined",
  };
}
