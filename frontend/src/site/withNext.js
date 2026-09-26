// A link to another sign-in-family screen that keeps "where they were headed" (?next=...), so someone
// who bounced from /app/calls to /signin and then hops to /signup still lands on /app/calls after.
// `search` is the current URLSearchParams. Only the next value is carried, and only if there is one.
export function withNext(path, search) {
  const next = search.get("next");

  return next ? `${path}?next=${encodeURIComponent(next)}` : path;
}
