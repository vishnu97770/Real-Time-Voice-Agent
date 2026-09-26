import { useEffect, useRef } from "react";
import { requestScrollPass, subscribeScroll } from "../hooks/scrollTicker.js";
import { stage } from "./bus.js";
import { pickAnchor, worldFromRect } from "./mode.js";
import { VISIBLE_HEIGHT, createScene } from "./scene.js";

// The one fixed, transparent canvas behind the whole landing page. It owns nothing visible by
// itself: the orb goes wherever an OrbAnchor is, measured in the shared scroll ticker.
//
// The canvas element is created inside the effect (not rendered by React) so a re-run - React's
// StrictMode double effect in development, or a tier change - always gets a fresh WebGL context;
// a canvas whose context was force-lost cannot be reused.
export default function VoiceStage({ tier, onReady, onGiveUp }) {
  const hostRef = useRef(null);

  useEffect(() => {
    const host = hostRef.current;
    const canvas = document.createElement("canvas");

    canvas.className = "stage-canvas";
    host.appendChild(canvas);

    let scene = null;

    try {
      scene = createScene({ canvas, tier, stage, onGiveUp });
    } catch (error) {
      console.warn("The 3D scene could not start; using the CSS orb.", error);
      canvas.remove();
      onGiveUp?.();
      return undefined;
    }

    stage.ready = true;
    onReady?.();

    const unsubscribe = subscribeScroll({
      read: (view) => {
        const elements = [...stage.anchors];
        const rects = elements.map((element) => element.getBoundingClientRect());
        const index = pickAnchor(rects, { width: view.vw, height: view.vh });

        if (index === -1) return null;

        return {
          world: worldFromRect(rects[index], { width: view.vw, height: view.vh }, VISIBLE_HEIGHT),
          opacity: Number(elements[index].dataset.orbOpacity ?? 1),
        };
      },
      write: (measured) => {
        if (measured) scene.setTarget({ ...measured.world, opacity: measured.opacity });
        else scene.setTarget({ opacity: 0 });
      },
    });

    // Layout can move an anchor without any scroll (a section above finishes loading, a font
    // arrives, the window resizes): re-measure whenever the page's own height changes.
    const resizeObserver = new ResizeObserver(() => requestScrollPass());

    resizeObserver.observe(document.body);
    scene.start();

    return () => {
      resizeObserver.disconnect();
      unsubscribe();
      scene.dispose();
      canvas.remove();
      stage.ready = false;
    };
  }, [tier, onReady, onGiveUp]);

  return <div ref={hostRef} className="stage-host" aria-hidden="true" />;
}
