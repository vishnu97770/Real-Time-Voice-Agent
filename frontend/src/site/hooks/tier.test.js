import assert from "node:assert/strict";
import test from "node:test";
import { TIER_SETTINGS, pickTier } from "./tier.js";

test("reduced motion, data saver and no WebGL all turn the 3D scene off", () => {
  assert.equal(pickTier({ reducedMotion: true }), "off");
  assert.equal(pickTier({ saveData: true }), "off");
  assert.equal(pickTier({ webgl: false }), "off");
});

test("a very weak device gets no 3D", () => {
  assert.equal(pickTier({ cores: 2 }), "off");
  assert.equal(pickTier({ memory: 2 }), "off");
});

test("phones get the light scene, even on a fast chip", () => {
  assert.equal(pickTier({ width: 390, cores: 8 }), "low");
});

test("tablets and small laptops get the middle scene", () => {
  assert.equal(pickTier({ width: 900, cores: 8 }), "medium");
  assert.equal(pickTier({ width: 1440, coarse: true, cores: 8 }), "medium");
  assert.equal(pickTier({ width: 1440, cores: 4 }), "medium");
});

test("a normal desktop gets the full scene", () => {
  assert.equal(pickTier({ width: 1440, cores: 8, memory: 8 }), "high");
  assert.equal(pickTier({}), "medium"); // unknown extras: the defaults (4 cores) are not "high"
});

test("every runnable tier has settings, and heavier tiers are strictly heavier", () => {
  assert.ok(TIER_SETTINGS.low && TIER_SETTINGS.medium && TIER_SETTINGS.high);
  assert.ok(TIER_SETTINGS.low.particles < TIER_SETTINGS.medium.particles);
  assert.ok(TIER_SETTINGS.medium.particles < TIER_SETTINGS.high.particles);
  assert.ok(TIER_SETTINGS.low.detail < TIER_SETTINGS.high.detail);
  assert.equal(TIER_SETTINGS.off, undefined);
});
