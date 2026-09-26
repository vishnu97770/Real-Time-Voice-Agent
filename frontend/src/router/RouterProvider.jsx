import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { RouterContext } from "./context.js";
import { matchRoute, normalizePath } from "./router.js";

// History-API routing with no dependency. The URL is the only source of truth: the location is
// read through useSyncExternalStore, and navigate() writes to history and tells subscribers.
//
// `navigate(to, { transition: true })` covers the screen with a short curtain first. It is used
// for the two moments where the site hands over to the product (landing -> sign-in -> app);
// everything else changes instantly. Reduced motion skips the curtain entirely.

const CHANGE = "app:navigate";
const CURTAIN_CLOSE_MS = 420;
const CURTAIN_OPEN_MS = 520;

const subscribe = (callback) => {
  window.addEventListener("popstate", callback);
  window.addEventListener(CHANGE, callback);

  return () => {
    window.removeEventListener("popstate", callback);
    window.removeEventListener(CHANGE, callback);
  };
};

const snapshot = () => window.location.pathname + window.location.search;

const prefersReducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

function write(to, replace) {
  if (to === snapshot()) return;

  window.history[replace ? "replaceState" : "pushState"](null, "", to);
  window.dispatchEvent(new Event(CHANGE));

  // A new screen starts at the top; the browser only does this for real page loads.
  if (!to.includes("#")) window.scrollTo(0, 0);
}

export default function RouterProvider({ children }) {
  const location = useSyncExternalStore(subscribe, snapshot, () => "/");
  const [curtain, setCurtain] = useState("idle"); // idle | closing | opening
  const timers = useRef([]);
  const busy = useRef(false);

  const [path, search = ""] = useMemo(() => {
    const at = location.indexOf("?");

    return at === -1 ? [location, ""] : [location.slice(0, at), location.slice(at + 1)];
  }, [location]);

  const route = useMemo(() => matchRoute(normalizePath(path)), [path]);

  useEffect(
    () => () => {
      timers.current.forEach(clearTimeout);
    },
    [],
  );

  const navigate = useCallback((to, { replace = false, transition = false } = {}) => {
    if (!transition || prefersReducedMotion()) {
      write(to, replace);
      return;
    }

    if (busy.current) return; // one curtain at a time; a second click is the same intent

    busy.current = true;
    setCurtain("closing");

    timers.current.push(
      setTimeout(() => {
        write(to, replace);
        setCurtain("opening");
      }, CURTAIN_CLOSE_MS),
      setTimeout(() => {
        setCurtain("idle");
        busy.current = false;
      }, CURTAIN_CLOSE_MS + CURTAIN_OPEN_MS),
    );
  }, []);

  // A redirect route never renders: the URL is replaced with its target.
  useEffect(() => {
    if (route.redirect) write(route.redirect, true);
  }, [route.redirect]);

  const value = useMemo(
    () => ({ route: route.name, params: route.params, search: new URLSearchParams(search), path, navigate }),
    [route.name, route.params, search, path, navigate],
  );

  return (
    <RouterContext.Provider value={value}>
      {route.redirect ? null : children}
      {curtain !== "idle" && (
        <div className={`route-curtain is-${curtain}`} aria-hidden="true">
          <span className="route-curtain-mark">
            <i />
            <i />
            <i />
            <i />
            <i />
          </span>
        </div>
      )}
    </RouterContext.Provider>
  );
}
