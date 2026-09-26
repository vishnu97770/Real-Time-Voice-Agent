import { useCallback, useEffect, useRef, useState } from "react";

// A steady clock for scripted animations (the demo conversation, the typing form).
//
// Returns [elapsedMs, restart]. It ticks only while `active`, at most ~30 times a second, and
// stops by itself at `duration` so a finished script costs nothing. Restart begins again at 0.
export function useClock({ active, duration = Infinity }) {
  const [elapsed, setElapsed] = useState(0);
  const base = useRef({ startedAt: 0, offset: 0 });

  useEffect(() => {
    if (!active) return undefined;

    let frame = 0;
    let lastShown = -1;

    base.current.startedAt = performance.now();

    const loop = (time) => {
      const value = Math.min(duration, base.current.offset + (time - base.current.startedAt));
      const stepped = Math.floor(value / 33) * 33;

      if (stepped !== lastShown || value >= duration) {
        lastShown = stepped;
        setElapsed(value >= duration ? duration : stepped);
      }

      if (value < duration) frame = requestAnimationFrame(loop);
    };

    frame = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(frame);
      // Pausing keeps where we were, so scrolling away and back resumes instead of restarting.
      base.current.offset = Math.min(duration, base.current.offset + (performance.now() - base.current.startedAt));
    };
  }, [active, duration]);

  const restart = useCallback(() => {
    base.current = { startedAt: performance.now(), offset: 0 };
    setElapsed(0);
  }, []);

  return [elapsed, restart];
}
