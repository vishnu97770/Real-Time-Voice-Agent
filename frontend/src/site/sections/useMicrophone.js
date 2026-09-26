import { useCallback, useEffect, useRef, useState } from "react";
import { stage } from "../stage/bus.js";

// Lets the visitor speak to the orb. Strictly opt-in (a button), strictly local: the stream goes
// into an AnalyserNode that is never connected to any output or network, and the orb reads its
// loudness. Stopping, or leaving the page, releases the microphone and the audio context.
//
// state: off | asking | on | blocked | unsupported
export function useMicrophone() {
  const [state, setState] = useState("off");
  const held = useRef({ stream: null, context: null });

  const stop = useCallback(() => {
    held.current.stream?.getTracks().forEach((track) => track.stop());
    held.current.context?.close().catch(() => {});
    held.current = { stream: null, context: null };
    stage.analyser = null;
    setState((current) => (current === "on" || current === "asking" ? "off" : current));
  }, []);

  const start = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia || typeof AudioContext === "undefined") {
      setState("unsupported");
      return;
    }

    setState("asking");

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      const context = new AudioContext();
      const analyser = context.createAnalyser();

      analyser.fftSize = 512;
      context.createMediaStreamSource(stream).connect(analyser); // deliberately not connected to the speakers

      held.current = { stream, context };
      stage.analyser = analyser;
      setState("on");
    } catch {
      setState("blocked");
    }
  }, []);

  useEffect(() => stop, [stop]);

  return { state, start, stop };
}
