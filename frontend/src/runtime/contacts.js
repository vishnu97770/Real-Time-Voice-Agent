// The signed-in operator's organization's contacts - a distinct domain concept from the legacy
// /api/customers demo data (see the Step 18C report): this is app/schemas/contact.py's Contact,
// not a profile's built-in demo record.

import { getJson, post } from "./api.js";

export async function fetchContacts() {
  return getJson("/api/contacts");
}

export async function fetchContact(contactId) {
  return getJson(`/api/contacts/${encodeURIComponent(contactId)}`);
}

export async function createContact(payload) {
  return (await post("/api/contacts", payload)).json();
}

export async function updateContact(contactId, payload) {
  return (await post(`/api/contacts/${encodeURIComponent(contactId)}`, payload, { method: "PUT" })).json();
}

// No JSON body: DELETE returns 204, nothing to parse. A failed delete throws (ApiError), same as
// every other request() call - the caller must not assume success just because this resolved.
export async function deleteContact(contactId) {
  await post(`/api/contacts/${encodeURIComponent(contactId)}`, undefined, { method: "DELETE" });
}
