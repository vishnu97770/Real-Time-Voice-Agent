import assert from "node:assert/strict";
import test from "node:test";

import { VOICE_STATE_LABEL, VOICE_STATES, visualForCallState } from "./voiceState.js";

test("every visual state carries a readable label", () => {
  VOICE_STATES.forEach((state) => {
    assert.equal(typeof VOICE_STATE_LABEL[state], "string");
    assert.ok(VOICE_STATE_LABEL[state].length > 0);
  });
});

test("each call state maps onto one defined visual state", () => {
  const callStates = ["idle", "connecting", "listening", "speaking", "processing", "ended"];

  for (const callState of callStates) {
    for (const callError of [false, true]) {
      const visual = visualForCallState(callState, callError);

      assert.ok(VOICE_STATES.includes(visual), `${callState} -> ${visual}`);
    }
  }
});

test("live call states keep their own meaning", () => {
  assert.equal(visualForCallState("connecting"), "connecting");
  assert.equal(visualForCallState("listening"), "listening");
  assert.equal(visualForCallState("speaking"), "speaking");
  assert.equal(visualForCallState("processing"), "thinking");
});

test("an ended call is disconnected, or an error only when the transport failed", () => {
  assert.equal(visualForCallState("ended"), "disconnected");
  assert.equal(visualForCallState("ended", true), "error");
  assert.equal(visualForCallState("idle", true), "idle"); // idle is never an error
});
