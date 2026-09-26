import assert from "node:assert/strict";
import test from "node:test";
import { GAP, LANES, envelopeAt, overlaps, verdictFor } from "./realtimeModel.js";

test("a turn-based bot never overlaps its stages; a real-time agent always does", () => {
  assert.equal(overlaps("turn"), false);
  assert.equal(overlaps("realtime"), true);
});

test("turn-based stages run strictly in order, with a silence between listening and responding", () => {
  const bars = LANES.map((lane) => lane.turn);

  for (let index = 1; index < bars.length; index += 1) assert.ok(bars[index][0] >= bars[index - 1][1]);

  assert.equal(GAP.from, LANES[0].turn[1]);
  assert.equal(GAP.to, LANES[3].turn[0]);
});

test("in real time the reply starts before thinking has finished", () => {
  const think = LANES.find((lane) => lane.id === "think");
  const respond = LANES.find((lane) => lane.id === "respond");

  assert.ok(respond.realtime[0] < think.realtime[1]);
});

test("every bar is inside the conversation and points forward", () => {
  for (const lane of LANES) {
    for (const bar of [lane.turn, lane.realtime, lane.again].filter(Boolean)) {
      assert.ok(bar[0] >= 0 && bar[1] <= 100 && bar[0] < bar[1], `${lane.id} ${bar}`);
    }
  }
});

test("interrupting cuts the real-time reply short and gives listening a new stretch", () => {
  const respond = LANES.find((lane) => lane.id === "respond");
  const listen = LANES.find((lane) => lane.id === "listen");

  assert.ok(respond.cut < respond.realtime[1]);
  assert.ok(listen.again[0] >= respond.cut - 5); // listening picks up where the reply was cut
});

test("the wave is flat in a turn-based gap and never silent in real time", () => {
  assert.ok(envelopeAt("turn", 0.5) < 0.1);
  assert.ok(envelopeAt("turn", 0.1) > 0.4);

  for (let u = 0; u <= 1; u += 0.01) assert.ok(envelopeAt("realtime", u) > 0.15, `silent at ${u}`);
});

test("every mode/interrupt combination has its own sentence", () => {
  const sentences = new Set([verdictFor("turn", false), verdictFor("turn", true), verdictFor("realtime", false), verdictFor("realtime", true)]);

  assert.equal(sentences.size, 4);
});
