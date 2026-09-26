// The small shared state between the parts of the page that talk (the hero's status line, the
// demo, the call-to-action button) and the 3D orb that listens. The orb reads it once per frame
// from its own render loop, so nothing here causes a React render.
//
//   mode      what the agent is doing right now: idle | listening | thinking | speaking
//   boost     a short-lived extra energy (button hover, a click), decays by itself
//   analyser  a WebAudio AnalyserNode while the visitor has chosen to speak to the orb, else null
//   anchors   elements marked as "the orb goes here" (see OrbAnchor)
//   ready     the WebGL scene is up and drawing (the CSS orbs fade out when it is)

export const stage = {
  mode: "idle",
  boost: 0,
  analyser: null,
  anchors: new Set(),
  ready: false,
};

export function setMode(mode) {
  stage.mode = mode;
}

export function pulse(amount = 1) {
  stage.boost = Math.min(1.5, stage.boost + amount);
}
