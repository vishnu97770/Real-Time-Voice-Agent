// The 3D voice orb: a breathing core, a few waveform rings and a shell of drifting particles,
// drawn on one fixed, transparent canvas. There is one scene for the whole page; it flies to
// whichever DOM element is marked as an "anchor" (see OrbAnchor) as the visitor scrolls.
//
// Performance:
//  - one WebGL context, five draw calls, no textures, no post-processing
//  - the pixel ratio is capped per tier and lowered on the fly if frames run long
//  - the loop stops when the tab is hidden and when the orb is fully faded out
//  - if even the lowest quality cannot keep up, the scene shuts itself off (onGiveUp) and the
//    page falls back to its CSS orb
//  - dispose() releases every GPU resource

import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  Color,
  Group,
  IcosahedronGeometry,
  LineLoop,
  Mesh,
  PerspectiveCamera,
  Points,
  Scene,
  ShaderMaterial,
  WebGLRenderer,
} from "three";
import { TIER_SETTINGS } from "../hooks/tier.js";
import { rmsOfBytes, targetsFor } from "./mode.js";
import {
  CORE_FRAGMENT,
  CORE_VERTEX,
  DUST_FRAGMENT,
  DUST_VERTEX,
  RING_FRAGMENT,
  RING_VERTEX,
} from "./shaders.js";

const FOV = 32;
const CAMERA_Z = 7;
export const VISIBLE_HEIGHT = 2 * Math.tan((FOV * Math.PI) / 360) * CAMERA_Z;
const SLOW_FRAME = 1 / 27; // seconds; sustained slower than this means "degrade"
const SLOW_FRAMES_BEFORE_DEGRADE = 50;

const ACCENT = new Color("#52ab98");
const BRIGHT = new Color("#86d6c2");
const DEEP = new Color("#020808");
const MID = new Color("#0a2f2b");

// Deterministic pseudo-random numbers: the same particle field every load, no Math.random.
function mulberry32(seed) {
  let a = seed;

  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;

    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const damp = (current, target, rate, dt) => current + (target - current) * (1 - Math.exp(-rate * dt));

export function createScene({ canvas, tier, stage, onGiveUp }) {
  const settings = TIER_SETTINGS[tier];
  const renderer = new WebGLRenderer({ canvas, alpha: true, antialias: tier !== "low", powerPreference: "high-performance" });
  const scene = new Scene();
  const camera = new PerspectiveCamera(FOV, 1, 0.1, 40);
  const group = new Group();
  const pointer = { x: 0, y: 0 };
  const view = { width: window.innerWidth, height: window.innerHeight };

  camera.position.z = CAMERA_Z;
  scene.add(group);

  let pixelRatio = Math.min(window.devicePixelRatio || 1, settings.dpr);

  renderer.setPixelRatio(pixelRatio);
  renderer.setClearColor(0x000000, 0);
  renderer.setSize(view.width, view.height, false);
  camera.aspect = view.width / view.height;
  camera.updateProjectionMatrix();

  // --- core
  const coreUniforms = {
    uTime: { value: 0 },
    uLevel: { value: 0 },
    uThink: { value: 0 },
    uOpacity: { value: 0 },
    uDeep: { value: DEEP },
    uMid: { value: MID },
    uAccent: { value: ACCENT },
    uBright: { value: BRIGHT },
  };
  const coreGeometry = new IcosahedronGeometry(1, settings.detail);
  const coreMaterial = new ShaderMaterial({ vertexShader: CORE_VERTEX, fragmentShader: CORE_FRAGMENT, uniforms: coreUniforms, transparent: true });
  const core = new Mesh(coreGeometry, coreMaterial);

  group.add(core);

  // --- rings
  const RING_POINTS = 220;
  const ringGeometry = new BufferGeometry();
  const angles = new Float32Array(RING_POINTS);

  for (let index = 0; index < RING_POINTS; index += 1) angles[index] = (index / RING_POINTS) * Math.PI * 2;

  ringGeometry.setAttribute("position", new BufferAttribute(new Float32Array(RING_POINTS * 3), 3));
  ringGeometry.setAttribute("aAngle", new BufferAttribute(angles, 1));

  const ringSpecs = [
    { radius: 1.42, phase: 0, alpha: 0.95, tilt: [1.15, 0.2, 0] },
    { radius: 1.68, phase: 2.1, alpha: 0.6, tilt: [0.55, -0.5, 0.3] },
    { radius: 1.95, phase: 4.2, alpha: 0.38, tilt: [1.5, 0.7, -0.2] },
  ].slice(0, settings.rings);

  const rings = ringSpecs.map((spec) => {
    const uniforms = {
      uTime: coreUniforms.uTime,
      uLevel: coreUniforms.uLevel,
      uRadius: { value: spec.radius },
      uPhase: { value: spec.phase },
      uColor: { value: BRIGHT },
      uAlpha: { value: spec.alpha },
      uOpacity: coreUniforms.uOpacity,
    };
    const material = new ShaderMaterial({ vertexShader: RING_VERTEX, fragmentShader: RING_FRAGMENT, uniforms, transparent: true, blending: AdditiveBlending, depthWrite: false });
    const line = new LineLoop(ringGeometry, material);

    line.rotation.set(...spec.tilt);
    line.userData.baseTilt = spec.tilt;
    line.frustumCulled = false;
    group.add(line);

    return line;
  });

  // --- dust
  const random = mulberry32(0x5eed);
  const seeds = new Float32Array(settings.particles * 4);

  for (let index = 0; index < settings.particles; index += 1) {
    seeds[index * 4] = Math.sqrt(random()); // radius, biased outward so the shell has body
    seeds[index * 4 + 1] = random() * Math.PI * 2; // longitude
    seeds[index * 4 + 2] = Math.acos(2 * random() - 1); // latitude, uniform on a sphere
    seeds[index * 4 + 3] = random(); // speed / size / alpha
  }

  const dustGeometry = new BufferGeometry();

  dustGeometry.setAttribute("position", new BufferAttribute(new Float32Array(settings.particles * 3), 3));
  dustGeometry.setAttribute("aSeed", new BufferAttribute(seeds, 4));

  const dustUniforms = {
    uTime: coreUniforms.uTime,
    uLevel: coreUniforms.uLevel,
    uThink: coreUniforms.uThink,
    uPixel: { value: pixelRatio },
    uColor: { value: new Color("#a7e6d6") },
    uOpacity: coreUniforms.uOpacity,
  };
  const dust = new Points(
    dustGeometry,
    new ShaderMaterial({ vertexShader: DUST_VERTEX, fragmentShader: DUST_FRAGMENT, uniforms: dustUniforms, transparent: true, blending: AdditiveBlending, depthWrite: false }),
  );

  dust.frustumCulled = false;
  group.add(dust);

  // --- state
  const target = { x: 0, y: 0, scale: 1, opacity: 0 };
  const now = { x: 0, y: 0, scale: 1 };
  let level = 0;
  let think = 0;
  let clock = 0;
  let last = 0;
  let raf = 0;
  let running = false;
  let disposed = false;
  let slowFrames = 0;
  let degradeStep = 0;
  let sampleBuffer = null;

  // The page tells the scene where the orb should be (see VoiceStage, which measures the anchors
  // in the shared scroll ticker so the render loop never forces a layout).
  function setTarget(next) {
    if (next.scale !== undefined) {
      target.x = next.x;
      target.y = next.y;
      target.scale = next.scale;
    }

    target.opacity = next.opacity;

    if (next.opacity > 0) wake();
  }

  function frame(time) {
    raf = 0;

    if (disposed || !running) return;

    const dt = Math.min(0.05, last ? (time - last) / 1000 : 0.016);

    last = time;
    clock += dt;

    // Mode -> energy. A live microphone, when the visitor chose it, overrides the script.
    let { energy, think: thinkTarget } = targetsFor(stage.mode, clock);

    if (stage.analyser) {
      sampleBuffer ??= new Uint8Array(stage.analyser.fftSize);
      stage.analyser.getByteTimeDomainData(sampleBuffer);
      energy = 0.1 + rmsOfBytes(sampleBuffer) * 0.95;
    }

    energy += stage.boost * 0.45;
    stage.boost *= Math.exp(-dt * 2.6);

    level = damp(level, Math.min(1.2, energy), 9, dt);
    think = damp(think, thinkTarget, 3, dt);
    coreUniforms.uTime.value = clock;
    coreUniforms.uLevel.value = level;
    coreUniforms.uThink.value = think;

    // Follow the anchor: position quickly, size a little more slowly, fade on its own clock.
    // While the orb is invisible it simply appears at its new place instead of flying there.
    if (coreUniforms.uOpacity.value < 0.02) {
      now.x = target.x;
      now.y = target.y;
      now.scale = target.scale;
    }

    now.x = damp(now.x, target.x, 7, dt);
    now.y = damp(now.y, target.y, 7, dt);
    now.scale = damp(now.scale, target.scale, 5, dt);
    coreUniforms.uOpacity.value = damp(coreUniforms.uOpacity.value, target.opacity, 5, dt);

    group.position.set(now.x, now.y, 0);
    group.scale.setScalar(now.scale);

    // Pointer parallax, and a slow drift so the orb is never still.
    group.rotation.y = damp(group.rotation.y, pointer.x * 0.55 + Math.sin(clock * 0.21) * 0.12, 3, dt);
    group.rotation.x = damp(group.rotation.x, -pointer.y * 0.35, 3, dt);
    core.rotation.y += dt * (0.06 + think * 0.5);

    rings.forEach((ring, index) => {
      const [x, y, z] = ring.userData.baseTilt;

      ring.rotation.set(x + Math.sin(clock * 0.17 + index) * 0.09, y + clock * (0.04 + index * 0.015), z);
    });

    renderer.render(scene, camera);

    // Sleep once the orb is invisible and settled; setTarget wakes it again.
    if (coreUniforms.uOpacity.value < 0.004 && target.opacity === 0) {
      running = false;
      last = 0;
      return;
    }

    watchFrameTime(dt);
    raf = requestAnimationFrame(frame);
  }

  function watchFrameTime(dt) {
    slowFrames = dt > SLOW_FRAME ? slowFrames + 1 : Math.max(0, slowFrames - 2);

    if (slowFrames < SLOW_FRAMES_BEFORE_DEGRADE) return;

    slowFrames = 0;
    degradeStep += 1;

    if (degradeStep === 1) {
      pixelRatio = 1;
      renderer.setPixelRatio(1);
      renderer.setSize(view.width, view.height, false);
      dustUniforms.uPixel.value = 1;
    } else if (degradeStep === 2) {
      dustGeometry.setDrawRange(0, Math.floor(settings.particles * 0.4));
      rings.slice(1).forEach((ring) => {
        ring.visible = false;
      });
    } else {
      stop();
      onGiveUp?.();
    }
  }

  function start() {
    if (disposed || running || document.hidden) return;

    running = true;
    last = 0;
    raf = requestAnimationFrame(frame);
  }

  function stop() {
    running = false;
    cancelAnimationFrame(raf);
    raf = 0;
  }

  // Called whenever something might have made the orb visible again (scroll, mode change).
  function wake() {
    if (!running) start();
  }

  function onVisibility() {
    if (document.hidden) stop();
    else start();
  }

  function onPointer(event) {
    pointer.x = (event.clientX / window.innerWidth - 0.5) * 2;
    pointer.y = (event.clientY / window.innerHeight - 0.5) * 2;
  }

  let resizeTimer = 0;

  function resize() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const width = window.innerWidth;
      const height = window.innerHeight;

      // A phone's address bar showing and hiding changes the height by ~60-100px; re-allocating
      // the drawing buffer for that would hitch while scrolling, and CSS stretches it invisibly.
      if (width === view.width && Math.abs(height - view.height) < 150) {
        view.height = height;
        return;
      }

      view.width = width;
      view.height = height;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      wake();
    }, 120);
  }

  window.addEventListener("resize", resize);
  window.addEventListener("pointermove", onPointer, { passive: true });
  document.addEventListener("visibilitychange", onVisibility);

  function dispose() {
    disposed = true;
    stop();
    clearTimeout(resizeTimer);
    window.removeEventListener("resize", resize);
    window.removeEventListener("pointermove", onPointer);
    document.removeEventListener("visibilitychange", onVisibility);
    coreGeometry.dispose();
    coreMaterial.dispose();
    ringGeometry.dispose();
    rings.forEach((ring) => ring.material.dispose());
    dustGeometry.dispose();
    dust.material.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
  }

  return { start, stop, wake, setTarget, dispose };
}
