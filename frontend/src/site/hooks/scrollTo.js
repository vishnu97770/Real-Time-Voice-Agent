// Smooth-scroll to a section by id, or jump when the visitor prefers reduced motion.
export function scrollToSection(id) {
  const element = document.getElementById(id);

  if (!element) return;

  const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  element.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
}
