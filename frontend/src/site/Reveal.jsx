import { useEffect, useRef } from "react";

// Fades and lifts its content in once, the first time it scrolls into view. Uses an
// IntersectionObserver and toggles a class directly, so revealing costs no React render.
// Without IntersectionObserver (or with reduced motion, handled in CSS) the content is simply shown.
export default function Reveal({ as: Tag = "div", delay = 0, className = "", style, children, ...rest }) {
  const ref = useRef(null);

  useEffect(() => {
    const element = ref.current;

    if (!element) return undefined;

    if (typeof IntersectionObserver === "undefined") {
      element.classList.add("is-in");
      return undefined;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          element.classList.add("is-in");
          observer.disconnect();
        }
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.08 },
    );

    observer.observe(element);

    return () => observer.disconnect();
  }, []);

  return (
    <Tag ref={ref} className={`rv ${className}`} style={{ "--d": `${delay}s`, ...style }} {...rest}>
      {children}
    </Tag>
  );
}
