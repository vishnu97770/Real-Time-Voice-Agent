import { safeNext } from "../router/router.js";

// What a screen should do, given the sign-in status. Pure and total: every status has an answer,
// and only "initializing" ever answers "loading" - so a screen can never be left waiting on a
// state that is not going to change.
//
//   kind "render"       show the screen
//   kind "loading"      the first sign-in check is still running (always temporary)
//   kind "redirect"     go to `to` (replace, not push, so Back does not bounce)
//   kind "unavailable"  the server cannot be reached: show the retry screen

const DEFAULT_LANDING = "/app/dashboard";

export function decideApp({ status, path = "/app/dashboard", search = "" }) {
  switch (status) {
    case "authenticated":
    case "open":
      return { kind: "render" };

    case "initializing":
      return { kind: "loading" };

    case "unauthenticated": {
      const back = `${path}${search}`;

      // Remember where they were going, unless it is just the front door.
      return { kind: "redirect", to: back === DEFAULT_LANDING ? "/signin" : `/signin?next=${encodeURIComponent(back)}` };
    }

    default:
      // "unavailable", and anything unrecognised: say so rather than spin.
      return { kind: "unavailable" };
  }
}

// For /signin and /signup alike: someone who is already signed in has no use for either screen.
export function decideSignIn({ status, next }) {
  // Already in: straight to where they were headed (only in-app destinations are honoured).
  if (status === "authenticated") return { kind: "redirect", to: safeNext(next, DEFAULT_LANDING) };

  return { kind: "render" };
}
