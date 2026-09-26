import { useCallback, useEffect, useState } from "react";
import { pickTier, readDevice } from "../hooks/tier.js";

const idle = (callback) => (window.requestIdleCallback ? window.requestIdleCallback(callback, { timeout: 1200 }) : setTimeout(callback, 250));
const cancelIdle = (handle) => (window.cancelIdleCallback ? window.cancelIdleCallback(handle) : clearTimeout(handle));

// The lifecycle of the 3D orb for any screen that shows it (the landing page, the sign-in screen).
//
//   tier     how much this device should be asked for (hooks/tier.js); "off" means never load 3D
//   status   loading | ready | off. "ready" is when the CSS orb may fade out (see the
//            .lp[data-stage="ready"] rule); "off" is the CSS orb for good
//   enabled  true once it is time to mount <VoiceStage>: after first paint, on a device that
//            can take it, and until it has given up
//
// The screen renders at once with its CSS orb; the 3D code is fetched afterwards, so the first
// paint never waits for it.
export function useVoiceStage() {
  const [tier] = useState(() => pickTier(readDevice()));
  const [status, setStatus] = useState(tier === "off" ? "off" : "loading");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    if (tier === "off") return undefined;

    const handle = idle(() => setMounted(true));

    return () => cancelIdle(handle);
  }, [tier]);

  const onReady = useCallback(() => setStatus("ready"), []);
  const onGiveUp = useCallback(() => setStatus("off"), []);

  return { tier, status, enabled: mounted && status !== "off", onReady, onGiveUp };
}
