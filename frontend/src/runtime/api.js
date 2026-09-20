// The one place the browser talks HTTP to the API.
//
// Cookies are sent on every call (the session cookie is HttpOnly, so scripts
// never see it). A 401 tells the app the sign-in is gone; a 429 carries how
// long to wait.

export const API_BASE = import.meta.env?.VITE_API_BASE ?? "";

// Anything can listen for "unauthorized" (the console does, to show the login).
export const authEvents = new EventTarget();

export class ApiError extends Error {
  constructor(status, message, retryAfter = null) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

export async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, { credentials: "include", ...options });

  if (response.ok) return response;

  let detail = `${path} failed with ${response.status}`;

  try {
    detail = (await response.json()).detail ?? detail;
  } catch {
    // Not JSON: keep the generic message.
  }

  if (response.status === 401) authEvents.dispatchEvent(new Event("unauthorized"));

  const retry = Number(response.headers.get("retry-after"));

  throw new ApiError(response.status, typeof detail === "string" ? detail : "Request failed", retry || null);
}

export function post(path, body, options = {}) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    ...options,
  });
}

export async function getJson(path) {
  return (await request(path)).json();
}
