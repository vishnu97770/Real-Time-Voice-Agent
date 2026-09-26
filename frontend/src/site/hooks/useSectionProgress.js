import { useEffect, useRef } from "react";
import { leaveProgress, sectionProgress } from "./progress.js";
import { subscribeScroll } from "./scrollTicker.js";

// Calls `apply(progress, { inView })` as a section scrolls, from the shared ticker.
//
// `apply` must only write styles or attributes (typically a CSS variable on the section) and
// never set React state, so scrolling never re-renders anything.
//
//   mode "pinned"  progress across the distance a sticky child stays fixed (see progress.js)
//   mode "leave"   how far the section has scrolled off the top of the screen
export function useSectionProgress(ref, apply, { mode = "pinned", enabled = true } = {}) {
  const applyRef = useRef(apply);

  useEffect(() => {
    applyRef.current = apply;
  });

  useEffect(() => {
    const element = ref.current;

    if (!element || !enabled) return undefined;

    let lastProgress = -1;
    let lastInView = null;

    return subscribeScroll({
      read: ({ vh }) => {
        const rect = element.getBoundingClientRect();

        return { top: rect.top, height: rect.height, vh };
      },
      write: ({ top, height, vh }) => {
        const progress = mode === "leave" ? leaveProgress(top, height) : sectionProgress(top, height, vh);
        const inView = top < vh && top + height > 0;

        if (inView === lastInView && Math.abs(progress - lastProgress) < 0.0005) return;

        lastProgress = progress;
        lastInView = inView;
        applyRef.current(progress, { inView });
      },
    });
  }, [ref, mode, enabled]);
}
