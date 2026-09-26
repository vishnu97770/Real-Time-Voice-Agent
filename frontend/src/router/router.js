// Route table and matching, kept free of React and the DOM so it can be tested on its own.
//
// Paths are matched segment by segment; ":name" captures a segment. A route with `redirect`
// never renders: the router replaces the URL with it (this is how "/app" becomes the dashboard).

export const ROUTES = [
  { name: "landing", path: "/" },
  { name: "signin", path: "/signin" },
  { name: "signup", path: "/signup", redirect: "/signin" },
  { name: "app", path: "/app", redirect: "/app/dashboard" },
  { name: "dashboard", path: "/app/dashboard" },
  { name: "agents", path: "/app/agents" },
  { name: "agent-new", path: "/app/agents/new" },
  { name: "agent-edit", path: "/app/agents/:id" },
  { name: "contacts", path: "/app/contacts" },
  { name: "workflows", path: "/app/workflows" },
  { name: "calls", path: "/app/calls" },
  { name: "call-detail", path: "/app/calls/:id" },
  { name: "analytics", path: "/app/analytics" },
  { name: "settings", path: "/app/settings" },
  { name: "console", path: "/app/console" },
];

const NOT_FOUND = { name: "not-found", params: {}, redirect: null };

const segments = (path) => path.split("/").filter(Boolean);

// The pathname without a trailing slash ("/app/" -> "/app"), "/" kept as is.
export function normalizePath(pathname) {
  if (typeof pathname !== "string" || !pathname.startsWith("/")) return "/";

  const trimmed = pathname.replace(/\/+$/, "");

  return trimmed === "" ? "/" : trimmed;
}

// Static segments win over ":param" ones, so "/app/agents/new" is the wizard, not an agent
// whose id is "new". The table order encodes that; this just takes the first match.
export function matchRoute(pathname) {
  const path = normalizePath(pathname);
  const parts = segments(path);

  for (const route of ROUTES) {
    const pattern = segments(route.path);

    if (pattern.length !== parts.length) continue;

    const params = {};
    const matches = pattern.every((piece, index) => {
      if (!piece.startsWith(":")) return piece === parts[index];

      try {
        params[piece.slice(1)] = decodeURIComponent(parts[index]);
      } catch {
        return false; // a malformed %-escape is a bad URL, not a crash
      }

      return true;
    });

    if (matches) return { name: route.name, params, redirect: route.redirect ?? null };
  }

  return NOT_FOUND;
}

// Where a sign-in should go afterwards. Only in-app paths are honoured, so a crafted
// "?next=https://elsewhere" (or "//elsewhere") can never bounce someone off the site.
export function safeNext(next, fallback = "/app/dashboard") {
  if (typeof next !== "string") return fallback;
  if (!next.startsWith("/app") || next.startsWith("//") || next.includes("\\")) return fallback;

  return matchRoute(next.split(/[?#]/)[0]).name === "not-found" ? fallback : next;
}

export const isAppRoute = (name) => ROUTES.some((route) => route.name === name && route.path.startsWith("/app") && !route.redirect);
