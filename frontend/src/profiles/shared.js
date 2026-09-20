import { AI_DISCLOSURE, NEVER_ASK } from "../runtime/guardrails.js";

export function makeGreeting(introduction, offer) {
  return `${AI_DISCLOSURE} ${introduction} ${NEVER_ASK} ${offer}`;
}

export function rupees(amount) {
  return `₹${amount.toLocaleString("en-IN")}`;
}

export function listSentence(items) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

// Pulls something like APP-1024 out of what the caller said.
export function findRef(text, prefix) {
  const match = text.match(new RegExp(`${prefix}[- ]?(\\d{2,4})`, "i"));

  return match ? `${prefix}-${match[1]}`.toUpperCase() : null;
}
