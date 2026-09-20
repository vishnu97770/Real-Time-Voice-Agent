import assert from "node:assert/strict";
import { test } from "node:test";

import { getProfile } from "../profiles/index.js";
import { splitSentences } from "./sentences.js";
import { createLocalTransport } from "./transports.js";

test("sentences split on stops followed by whitespace, never inside numbers", () => {
  assert.deepEqual(
    splitSentences("The score is 0.28 which is moderate. Balance is ₹84,250.75. Done!"),
    ["The score is 0.28 which is moderate.", "Balance is ₹84,250.75.", "Done!"]
  );
  assert.deepEqual(splitSentences("One sentence only"), ["One sentence only"]);
  assert.deepEqual(splitSentences(""), []);
});

test("the local transport speaks the same event protocol as the server", async () => {
  const transport = createLocalTransport(getProfile("bank"));
  const greeting = await transport.open();

  assert.match(greeting, /AI assistant/);

  const signal = new AbortController().signal;
  const events = [];

  for await (const event of transport.turn("What's my balance?", signal)) events.push(event);

  assert.deepEqual(events.map((event) => event.type).filter((type, i, all) => all.indexOf(type) === i), [
    "tool_call",
    "sentence",
    "done",
  ]);
  assert.equal(events.at(-1).type, "done");
  assert.equal(events.find((event) => event.type === "tool_call").name, "get_balance");
});

test("the local transport stops producing events once the turn is aborted", async () => {
  const transport = createLocalTransport(getProfile("bank"));
  const controller = new AbortController();

  await transport.open();
  controller.abort();

  const events = [];

  for await (const event of transport.turn("Show my recent transactions", controller.signal)) events.push(event);

  assert.deepEqual(events.filter((event) => event.type !== "done"), []);
});
