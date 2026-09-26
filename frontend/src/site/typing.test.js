import assert from "node:assert/strict";
import test from "node:test";
import { progressAt, typedSlice, typingDuration } from "./typing.js";

test("nothing before it starts, everything once it has finished", () => {
  assert.equal(typedSlice("CareCall", 0), "");
  assert.equal(typedSlice("CareCall", -50), "");
  assert.equal(typedSlice("CareCall", 60_000), "CareCall");
});

test("it types at the given rate", () => {
  assert.equal(typedSlice("abcdefghij", 500, 10), "abcde"); // 10 cps for half a second
  assert.equal(typedSlice("abcdefghij", 99, 10), "");
  assert.equal(typedSlice("abcdefghij", 100, 10), "a");
});

test("typingDuration inverts typedSlice", () => {
  const text = "Appointment Coordinator";

  assert.equal(typedSlice(text, typingDuration(text)), text);
  assert.notEqual(typedSlice(text, typingDuration(text) - 60), text);
});

test("progressAt clamps to 0..1", () => {
  assert.equal(progressAt(0, 100, 200), 0);
  assert.equal(progressAt(200, 100, 200), 0.5);
  assert.equal(progressAt(999, 100, 200), 1);
  assert.equal(progressAt(50, 100, 0), 0);
  assert.equal(progressAt(100, 100, 0), 1);
});
