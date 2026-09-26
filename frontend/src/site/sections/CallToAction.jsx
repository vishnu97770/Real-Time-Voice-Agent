import Link from "../../router/Link.jsx";
import Arrow from "../Arrow.jsx";
import { scrollToSection } from "../hooks/scrollTo.js";
import OrbAnchor from "../stage/OrbAnchor.jsx";
import { pulse } from "../stage/bus.js";
import Reveal from "../Reveal.jsx";
import "./CallToAction.css";

// The end of the story. The orb comes back, large, and reacts to the button: the product's own
// answer to "should I try it?" is to answer you.
export default function CallToAction() {
  return (
    <section className="cta">
      <OrbAnchor name="cta" className="cta-orb" opacity={0.85} />

      <div className="lp-wrap cta-copy">
        <Reveal as="p" className="lp-eyebrow">
          <b>10</b> Get started
        </Reveal>

        <Reveal as="h2" className="cta-title" delay={0.08}>
          Build your first <em>voice agent.</em>
        </Reveal>

        <Reveal className="cta-actions" delay={0.18}>
          <Link
            to="/signin"
            transition
            className="lp-btn lp-btn--primary lp-btn--lg"
            onPointerEnter={() => pulse(1.1)}
            onFocus={() => pulse(1.1)}
            onPointerDown={() => pulse(1.4)}
          >
            Get started <Arrow size={20} />
          </Link>
          <a
            href="#product"
            className="lp-btn lp-btn--ghost lp-btn--lg"
            onPointerEnter={() => pulse(0.5)}
            onClick={(event) => {
              event.preventDefault();
              scrollToSection("product");
            }}
          >
            Explore the platform
          </a>
        </Reveal>

        <Reveal as="p" className="cta-note" delay={0.26}>
          Already have an account? <Link to="/signin" transition>Sign in</Link>. New here? Ask your workspace administrator to add you.
        </Reveal>
      </div>
    </section>
  );
}
