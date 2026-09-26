import { Suspense, useEffect, useRef, useState } from "react";
import { requestScrollPass } from "./hooks/scrollTicker.js";

// Mounts its children only when the visitor is about to reach them. Until then it holds the
// section's space (`minHeight`, which should match the real height so the page does not jump),
// so below-the-fold sections cost nothing to load or render on the first visit.
//
// It also carries the section's chapter name (the "03 / 10" marker), so the chapter list is
// complete from the first paint instead of growing as sections load.
export default function Defer({ id, chapter, minHeight = "60vh", rootMargin = "1100px 0px", children }) {
  const ref = useRef(null);
  const [ready, setReady] = useState(typeof IntersectionObserver === "undefined");

  useEffect(() => {
    const element = ref.current;

    if (ready || !element) return undefined;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setReady(true);
          observer.disconnect();
        }
      },
      { rootMargin },
    );

    observer.observe(element);

    return () => observer.disconnect();
  }, [ready, rootMargin]);

  // A section that just mounted may change what the scroll effects should measure.
  useEffect(() => {
    if (ready) requestScrollPass();
  }, [ready]);

  return (
    <div ref={ref} id={id} className="defer" data-chapter={chapter} style={ready ? undefined : { minHeight }}>
      {ready && <Suspense fallback={<div style={{ minHeight }} />}>{children}</Suspense>}
    </div>
  );
}
