import { useSyncExternalStore } from "react";

// The light/dark choice lives on <html data-theme> (main.jsx sets it before first paint) and in
// localStorage. This is a tiny external store over that attribute, so the top bar's switch and
// the Settings page always agree without passing props through the shell.

const KEY = "voice-agent-theme";
const EVENT = "app:theme";

export function getTheme() {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

export function setTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);

  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // storage blocked: the choice still applies for this visit
  }

  window.dispatchEvent(new Event(EVENT));
}

const subscribe = (callback) => {
  window.addEventListener(EVENT, callback);

  return () => window.removeEventListener(EVENT, callback);
};

export function useTheme() {
  const theme = useSyncExternalStore(subscribe, getTheme, () => "dark");

  return [theme, setTheme];
}
