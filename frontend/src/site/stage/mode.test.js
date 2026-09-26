import assert from "node:assert/strict";
import test from "node:test";
import { pickAnchor, rmsOfBytes, speechEnvelope, targetsFor, worldFromRect } from "./mode.js";

test("the speech envelope stays inside 0..1 over a long time", () => {
  for (let t = 0; t < 120; t += 0.013) {
    const value = speechEnvelope(t);
    assert.ok(value >= 0 && value <= 1, `t=${t} gave ${value}`);
  }
});

test("speaking is louder than listening, which is louder than idle, on average", () => {
  const mean = (mode) => {
    let sum = 0;
    let count = 0;

    for (let t = 0; t < 60; t += 0.02) {
      sum += targetsFor(mode, t).energy;
      count += 1;
    }

    return sum / count;
  };

  assert.ok(mean("speaking") > mean("listening"));
  assert.ok(mean("listening") > mean("idle"));
});

test("only thinking swirls, and an unknown mode is idle", () => {
  assert.equal(targetsFor("thinking", 1).think, 1);
  assert.equal(targetsFor("speaking", 1).think, 0);
  assert.equal(targetsFor("nonsense", 1).think, 0);
  assert.ok(targetsFor("nonsense", 0).energy < 0.15);
});

test("rms: silence is 0, a loud signal saturates at 1", () => {
  assert.equal(rmsOfBytes(new Uint8Array(0)), 0);
  assert.equal(rmsOfBytes(new Uint8Array(64).fill(128)), 0);
  assert.equal(rmsOfBytes(Uint8Array.from({ length: 64 }, (_, i) => (i % 2 ? 255 : 0))), 1);
});

test("worldFromRect centres a centred rect at the origin and sizes the unit sphere", () => {
  const viewport = { width: 1000, height: 800 };
  const world = worldFromRect({ left: 350, top: 200, width: 300, height: 400 }, viewport, 4);

  assert.ok(Math.abs(world.x) < 1e-9);
  assert.ok(Math.abs(world.y) < 1e-9);
  // 300px is the smaller side; 300px * (4 / 800) = 1.5 units across, so radius 0.75
  assert.ok(Math.abs(world.scale - 0.75) < 1e-9);
});

test("worldFromRect: right of centre is +x, above centre is +y", () => {
  const viewport = { width: 1000, height: 800 };
  const world = worldFromRect({ left: 800, top: 0, width: 100, height: 100 }, viewport, 4);

  assert.ok(world.x > 0);
  assert.ok(world.y > 0);
});

test("pickAnchor takes the visible anchor nearest the middle", () => {
  const viewport = { width: 1000, height: 800 };
  const rects = [
    { left: 0, top: -900, width: 100, height: 100, bottom: -800 }, // off screen (above)
    { left: 450, top: 600, width: 100, height: 100, bottom: 700 }, // low
    { left: 450, top: 350, width: 100, height: 100, bottom: 450 }, // centred
  ];

  assert.equal(pickAnchor(rects, viewport), 2);
});

test("pickAnchor returns -1 when nothing is on screen or an anchor has no size", () => {
  const viewport = { width: 1000, height: 800 };

  assert.equal(pickAnchor([{ left: 0, top: 2000, width: 100, height: 100, bottom: 2100 }], viewport), -1);
  assert.equal(pickAnchor([{ left: 0, top: 100, width: 0, height: 0, bottom: 100 }], viewport), -1);
  assert.equal(pickAnchor([], viewport), -1);
});
