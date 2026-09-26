import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth/context.js";
import { canEnterApp } from "../auth/state.js";
import Link from "../router/Link.jsx";
import Arrow from "./Arrow.jsx";
import Logo from "../components/Logo.jsx";
import { subscribeScroll } from "./hooks/scrollTicker.js";
import { scrollToSection } from "./hooks/scrollTo.js";

const LINKS = [
  ["How it works", "how"],
  ["Use cases", "use-cases"],
  ["Platform", "product"],
];

export default function Nav() {
  const { status } = useAuth();
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const inApp = canEnterApp(status);

  // Solid, blurred bar once the page has moved; transparent over the hero.
  useEffect(
    () =>
      subscribeScroll({
        read: ({ y }) => y > 24,
        write: (scrolled) => ref.current?.classList.toggle("is-scrolled", scrolled),
      }),
    [],
  );

  useEffect(() => {
    if (!open) return undefined;

    const onKey = (event) => event.key === "Escape" && setOpen(false);

    window.addEventListener("keydown", onKey);

    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const go = (id) => (event) => {
    event.preventDefault();
    setOpen(false);
    scrollToSection(id);
  };

  return (
    <header ref={ref} className={`lp-nav ${open ? "is-open" : ""}`}>
      <div className="lp-nav-bar">
        <Link to="/" className="lp-brand" aria-label="Real-Time Voice Agent, home">
          <Logo />
          <span>
            <b>Real-Time</b> Voice Agent
          </span>
        </Link>

        <nav className="lp-nav-links" aria-label="Sections">
          {LINKS.map(([label, id]) => (
            <a key={id} href={`#${id}`} onClick={go(id)}>
              {label}
            </a>
          ))}
        </nav>

        <div className="lp-nav-actions">
          {inApp ? (
            <Link to="/app/dashboard" transition className="lp-btn lp-btn--primary lp-btn--sm">
              Open app <Arrow size={16} />
            </Link>
          ) : (
            <>
              <Link to="/signin" transition className="lp-nav-signin">
                Sign in
              </Link>
              <Link to="/signin" transition className="lp-btn lp-btn--primary lp-btn--sm">
                Get started <Arrow size={16} />
              </Link>
            </>
          )}
        </div>

        <button type="button" className="lp-nav-toggle" aria-label={open ? "Close menu" : "Open menu"} aria-expanded={open} onClick={() => setOpen(!open)}>
          <span />
          <span />
        </button>
      </div>

      <div className="lp-nav-sheet" aria-hidden={!open}>
        {LINKS.map(([label, id]) => (
          <a key={id} href={`#${id}`} onClick={go(id)} tabIndex={open ? 0 : -1}>
            {label}
          </a>
        ))}
        <Link to={inApp ? "/app/dashboard" : "/signin"} transition tabIndex={open ? 0 : -1} className="lp-btn lp-btn--primary">
          {inApp ? "Open app" : "Get started"} <Arrow size={16} />
        </Link>
      </div>
    </header>
  );
}
