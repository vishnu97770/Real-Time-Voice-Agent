import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { ApiError } from "./api.js";
import { createContact, deleteContact, fetchContact, fetchContacts, updateContact } from "./contacts.js";

const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
});

const respond = (status, body = {}) => {
  globalThis.fetch = async (url, options) => {
    respond.last = { url, options };
    // A 204 (delete_contact's real response) must have a null body - a real server sends none.
    return new Response(status === 204 ? null : JSON.stringify(body), { status });
  };
};

test("fetchContacts reads the list endpoint", async () => {
  respond(200, [{ id: 1, name: "Priya" }]);
  const contacts = await fetchContacts();

  assert.equal(respond.last.url, "/api/contacts");
  assert.equal(respond.last.options.method, undefined); // a plain GET
  assert.deepEqual(contacts, [{ id: 1, name: "Priya" }]);
});

test("fetchContact reads one, by a URL-safe id", async () => {
  respond(200, { id: 7, name: "Priya" });
  await fetchContact(7);

  assert.equal(respond.last.url, "/api/contacts/7");
});

test("createContact POSTs the payload as JSON", async () => {
  respond(201, { id: 3, name: "Priya" });
  const created = await createContact({ name: "Priya" });

  assert.equal(respond.last.url, "/api/contacts");
  assert.equal(respond.last.options.method, "POST");
  assert.equal(respond.last.options.headers["Content-Type"], "application/json");
  assert.equal(respond.last.options.body, JSON.stringify({ name: "Priya" }));
  assert.deepEqual(created, { id: 3, name: "Priya" });
});

test("updateContact PUTs to the contact's own path", async () => {
  respond(200, { id: 3, name: "Priya Sharma" });
  const updated = await updateContact(3, { name: "Priya Sharma" });

  assert.equal(respond.last.url, "/api/contacts/3");
  assert.equal(respond.last.options.method, "PUT");
  assert.equal(respond.last.options.body, JSON.stringify({ name: "Priya Sharma" }));
  assert.deepEqual(updated, { id: 3, name: "Priya Sharma" });
});

test("deleteContact DELETEs and resolves on success", async () => {
  respond(204);
  await deleteContact(3);

  assert.equal(respond.last.url, "/api/contacts/3");
  assert.equal(respond.last.options.method, "DELETE");
});

test("a failed create, update or delete throws - none of them is reported as a silent success", async () => {
  respond(409, { detail: "This contact has call jobs and cannot be deleted" });

  await assert.rejects(createContact({ name: "Priya" }), (error) => error instanceof ApiError && error.status === 409);
  await assert.rejects(updateContact(3, { name: "Priya" }), (error) => error instanceof ApiError && error.status === 409);
  await assert.rejects(deleteContact(3), (error) => error instanceof ApiError && error.status === 409);
});

test("a 404 for an unknown or another organization's contact is a real error, not an empty result", async () => {
  respond(404, { detail: "Unknown contact" });

  await assert.rejects(fetchContact(999), (error) => error instanceof ApiError && error.status === 404);
});
