import assert from "node:assert/strict";
import test from "node:test";
import { DEMO_SCRIPT, buildTimeline, frameAt, partialText } from "./demoScript.js";

const timeline = buildTimeline();

test("the timeline is strictly ordered and never overlaps", () => {
  let previousEnd = 0;

  for (const item of timeline.items) {
    assert.ok(item.start >= previousEnd, `item ${item.index} starts before the last one ends`);
    assert.ok(item.leadEnd >= item.start && item.end >= item.leadEnd);
    previousEnd = item.end;
  }

  assert.equal(timeline.duration, timeline.items.at(-1).end);
});

test("the script has the demo's exact lines, in order", () => {
  const said = DEMO_SCRIPT.filter((step) => step.text).map((step) => step.text);

  assert.deepEqual(said.slice(0, 4), [
    "Hi, I need to schedule my appointment.",
    "Sure. What day works for you?",
    "Tomorrow afternoon.",
    "I found a 3 PM slot. Would you like me to book it?",
  ]);
  assert.equal(DEMO_SCRIPT.at(-1).who, "done");
});

test("the agent asks before it books (an action never precedes its confirmation)", () => {
  const bookAt = DEMO_SCRIPT.findIndex((step) => step.detail?.startsWith("book_slot"));
  const askedAt = DEMO_SCRIPT.findIndex((step) => step.text?.includes("Would you like me to book it"));
  const yesAt = DEMO_SCRIPT.findIndex((step) => step.text === "Yes, please.");

  assert.ok(askedAt < yesAt && yesAt < bookAt);
});

test("nothing is shown before the opening pause, and the status is idle", () => {
  const frame = frameAt(timeline, 0);

  assert.equal(frame.shown.length, 0);
  assert.equal(frame.status, "idle");
  assert.equal(frame.active, null);
});

test("the caller is heard first, then the agent thinks, then it speaks", () => {
  const [first, second] = timeline.items;

  assert.equal(frameAt(timeline, first.start + 10).status, "listening");
  assert.equal(frameAt(timeline, first.leadEnd + 10).speaking, true);
  assert.equal(frameAt(timeline, second.start + 10).status, "thinking");
  assert.equal(frameAt(timeline, second.leadEnd + 10).status, "speaking");
});

test("the gap between a caller line and the agent's reply reads as thinking", () => {
  const first = timeline.items[0];
  const frame = frameAt(timeline, first.end + 5);

  assert.equal(frame.active, null);
  assert.equal(frame.status, "thinking");
  assert.equal(frame.speaking, false);
});

test("actions show as working, then complete", () => {
  const action = timeline.items.find((item) => item.step.who === "action");

  assert.equal(frameAt(timeline, action.start + 100).status, "acting");

  const after = frameAt(timeline, action.end + 1);
  const shownAction = after.shown.find((entry) => entry.index === action.index);

  assert.equal(shownAction.phase, "done");
});

test("at the end everything is shown and the status is done", () => {
  const frame = frameAt(timeline, timeline.duration + 5000);

  assert.equal(frame.shown.length, DEMO_SCRIPT.length);
  assert.ok(frame.shown.every((entry) => entry.phase === "done"));
  assert.equal(frame.status, "done");
});

test("time going backwards (a replay) shows fewer items again", () => {
  const late = frameAt(timeline, timeline.duration);
  const early = frameAt(timeline, timeline.items[2].start + 1);

  assert.ok(early.shown.length < late.shown.length);
});

test("partialText: the caller's words arrive by whole words", () => {
  const step = { who: "user", text: "Hi, I need to schedule my appointment." };

  assert.equal(partialText(step, 0), "");
  assert.equal(partialText(step, 1), step.text);
  assert.equal(partialText(step, 0.01), "Hi,"); // never empty once started
  assert.ok(partialText(step, 0.5).split(" ").length < 7);
  assert.ok(step.text.startsWith(partialText(step, 0.5)));
});

test("partialText: the agent types by characters", () => {
  const step = { who: "agent", text: "Sure. What day works for you?" };

  assert.equal(partialText(step, 0.5), step.text.slice(0, 15));
  assert.equal(partialText(step, 1), step.text);
  assert.equal(partialText(step, 2), step.text);
});
