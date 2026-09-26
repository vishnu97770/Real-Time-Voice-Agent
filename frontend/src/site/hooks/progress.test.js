import assert from "node:assert/strict";
import test from "node:test";
import { clamp, leaveProgress, phase, sectionProgress, stepOf } from "./progress.js";

test("progress runs 0 -> 1 across the pinned distance", () => {
  // a 400vh section on a 1000px screen: 3000px of travel
  assert.equal(sectionProgress(500, 4000, 1000), 0); // not reached yet
  assert.equal(sectionProgress(0, 4000, 1000), 0);
  assert.equal(sectionProgress(-1500, 4000, 1000), 0.5);
  assert.equal(sectionProgress(-3000, 4000, 1000), 1);
  assert.equal(sectionProgress(-3600, 4000, 1000), 1); // already past
});

test("a section that fits the screen has no travel", () => {
  assert.equal(sectionProgress(200, 800, 1000), 0);
  assert.equal(sectionProgress(-1, 800, 1000), 1);
  assert.equal(sectionProgress(0, 1000, 1000), 1);
});

test("stepOf splits progress into equal steps and never overshoots", () => {
  assert.equal(stepOf(0, 6), 0);
  assert.equal(stepOf(0.16, 6), 0);
  assert.equal(stepOf(0.17, 6), 1);
  assert.equal(stepOf(0.999, 6), 5);
  assert.equal(stepOf(1, 6), 5);
  assert.equal(stepOf(-2, 6), 0);
  assert.equal(stepOf(7, 6), 5);
});

test("phase maps a sub-range of progress", () => {
  assert.equal(phase(0.2, 0.4, 0.8), 0);
  assert.ok(Math.abs(phase(0.6, 0.4, 0.8) - 0.5) < 1e-9);
  assert.equal(phase(0.9, 0.4, 0.8), 1);
  assert.equal(phase(0.5, 0.5, 0.5), 1); // an empty range is a step
  assert.equal(phase(0.4, 0.5, 0.5), 0);
});

test("clamp", () => {
  assert.equal(clamp(5, 0, 3), 3);
  assert.equal(clamp(-1, 0, 3), 0);
  assert.equal(clamp(2, 0, 3), 2);
});

test("leaveProgress is 0 at the top, 1 once fully gone, and never negative", () => {
  assert.equal(leaveProgress(0, 800), 0);
  assert.equal(leaveProgress(200, 800), 0); // not yet at the top
  assert.equal(leaveProgress(-400, 800), 0.5);
  assert.equal(leaveProgress(-800, 800), 1);
  assert.equal(leaveProgress(-2000, 800), 1);
  assert.equal(leaveProgress(-10, 0), 0); // an empty section never divides by zero
});
