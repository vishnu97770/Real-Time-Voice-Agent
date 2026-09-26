import { useEffect, useRef } from "react";
import { requestScrollPass, subscribeScroll } from "../hooks/scrollTicker.js";
import { stage } from "./bus.js";
import { pickAnchor, worldFromRect } from "./mode.js";
import { VISIBLE_HEIGHT, createScene } from "./scene.js";

// The one fixed, transparent canvas behind a whole screen (the landing page, the sign-in screen).
// It owns nothing visible by itself: the orb goes wherever an OrbAnchor is, measured in the shared
// scroll ticker.
//
// `clipTo` (a ref to an element) limits where the canvas may draw to that element's box, so the
// orb's rings and dust cannot spill out of a panel into the content beside it. Left out, the canvas
// may draw anywhere (the landing page).
//
// The canvas element is created inside the effect (not rendered by React) so a re-run - React's
// StrictMode double effect in development, or a tier change - always gets a fresh WebGL context;
// a canvas whose context was force-lost cannot be reused.
export default function VoiceStage({ tier, onReady, onGiveUp, clipTo }) {
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

        // where the canvas may draw, as an inset() from each viewport edge (only when asked to clip)
        const box = clipTo?.current?.getBoundingClientRect();
        const clip = box
          ? `inset(${Math.max(0, box.top)}px ${Math.max(0, view.vw - box.right)}px ${Math.max(0, view.vh - box.bottom)}px ${Math.max(0, box.left)}px)`
          : null;

        if (index === -1) return { clip, target: null };

        return {
          clip,
          target: {
            world: worldFromRect(rects[index], { width: view.vw, height: view.vh }, VISIBLE_HEIGHT),
            opacity: Number(elements[index].dataset.orbOpacity ?? 1),
          },
        };
      },
      write: ({ clip, target }) => {
        if (clip !== null) host.style.clipPath = clip;

        if (target) scene.setTarget({ ...target.world, opacity: target.opacity });
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
  }, [tier, onReady, onGiveUp, clipTo]);

  return <div ref={hostRef} className="stage-host" aria-hidden="true" />;
}
