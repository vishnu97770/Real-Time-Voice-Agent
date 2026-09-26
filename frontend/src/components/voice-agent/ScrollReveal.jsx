import { useEffect, useRef, useState } from "react";

// Fades a section in once, the first time it scrolls into view. One IntersectionObserver per
// wrapper, disconnected on unmount and after the first reveal; without IntersectionObserver
// (or with reduced motion, handled in CSS) the content is simply shown.
export default function ScrollReveal({ children, className = "" }) {
  const ref = useRef(null);
  const [shown, setShown] = useState(() => typeof IntersectionObserver === "undefined");

  useEffect(() => {
    const node = ref.current;

    if (shown || !node) return undefined;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setShown(true);
          observer.disconnect();
        }
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.05 },
    );

    observer.observe(node);

    return () => observer.disconnect();
  }, [shown]);

  return (
    <div ref={ref} className={`scroll-reveal ${shown ? "is-shown" : ""} ${className}`}>
      {children}
    </div>
  );
}
