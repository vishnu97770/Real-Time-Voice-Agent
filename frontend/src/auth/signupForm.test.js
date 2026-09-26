import assert from "node:assert/strict";
import test from "node:test";
import { MIN_PASSWORD_LENGTH, lengthOf, validateSignup } from "./signupForm.js";

const good = { workspace: "Northwind Health", email: "a@b.co", password: "a long enough password" };

test("a complete, valid form has no errors", () => {
  assert.deepEqual(validateSignup(good), {});
});

test("the password minimum matches the server's policy (12), not the usual 8", () => {
  assert.equal(MIN_PASSWORD_LENGTH, 12);
  assert.ok(validateSignup({ ...good, password: "elevenchars" }).password);
  assert.equal(validateSignup({ ...good, password: "twelve chars" }).password, undefined);
});

test("every empty field is named, and only the empty ones", () => {
  assert.deepEqual(Object.keys(validateSignup({})).sort(), ["email", "password", "workspace"]);
  assert.deepEqual(Object.keys(validateSignup({ ...good, email: "" })), ["email"]);
});

test("a workspace name of only spaces counts as empty; surrounding spaces do not matter", () => {
  assert.ok(validateSignup({ ...good, workspace: "    " }).workspace);
  assert.equal(validateSignup({ ...good, workspace: "  Acme  " }).workspace, undefined);
});

test("a workspace name over the server's limit is refused", () => {
  assert.ok(validateSignup({ ...good, workspace: "x".repeat(121) }).workspace);
  assert.equal(validateSignup({ ...good, workspace: "x".repeat(120) }).workspace, undefined);
});

test("email: needs an @ and a dotted domain, no spaces; surrounding spaces are ignored", () => {
  for (const bad of ["plain", "a@b", "@b.co", "a@.co", "a b@c.co", "a@b .co"]) {
    assert.ok(validateSignup({ ...good, email: bad }).email, bad);
  }

  for (const fine of ["a@b.co", "  a@b.co  ", "first.last+tag@sub.example.com", "A@B.CO"]) {
    assert.equal(validateSignup({ ...good, email: fine }).email, undefined, fine);
  }
});

test("password length counts characters the way the server does (a code point is one)", () => {
  assert.equal(lengthOf("héllo"), 5);
  assert.equal(lengthOf("😀😀😀"), 3); // three code points, six UTF-16 units
  assert.ok(validateSignup({ ...good, password: "😀".repeat(11) }).password);
  assert.equal(validateSignup({ ...good, password: "😀".repeat(12) }).password, undefined);
});

test("a password is not trimmed: spaces are part of it", () => {
  assert.equal(validateSignup({ ...good, password: "            " }).password, undefined); // 12 spaces
});

test("an over-long password is refused", () => {
  assert.ok(validateSignup({ ...good, password: "x".repeat(201) }).password);
  assert.equal(validateSignup({ ...good, password: "x".repeat(200) }).password, undefined);
});
