import assert from "node:assert/strict";
import test from "node:test";
import { withNext } from "./withNext.js";

test("no next: the plain path", () => {
  assert.equal(withNext("/signup", new URLSearchParams("")), "/signup");
});

test("next is carried across, encoded", () => {
  assert.equal(withNext("/signin", new URLSearchParams("next=%2Fapp%2Fcalls%3Ftab%3Dx")), "/signin?next=%2Fapp%2Fcalls%3Ftab%3Dx");
});

test("nothing else in the query travels along", () => {
  assert.equal(withNext("/signup", new URLSearchParams("job=J&token=T&next=%2Fapp%2Fcalls")), "/signup?next=%2Fapp%2Fcalls");
});
