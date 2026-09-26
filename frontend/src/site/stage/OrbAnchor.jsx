import { useEffect, useRef } from "react";
import { requestScrollPass } from "../hooks/scrollTicker.js";
import { stage } from "./bus.js";
import StaticOrb from "./StaticOrb.jsx";

// "The orb goes here." An empty, sized box the 3D orb flies to when it is the anchor nearest the
// middle of the screen. Its own size is the orb's size, so layout decides where and how big.
// Inside it sits the CSS orb, which is what people see until the 3D scene is up (and forever on
// devices that get no 3D scene).
export default function OrbAnchor({ name, className = "", opacity = 1, style }) {
  const ref = useRef(null);

  useEffect(() => {
    const element = ref.current;

    stage.anchors.add(element);
    requestScrollPass(); // a lazily mounted section appears without any scroll: tell the orb

    return () => {
      stage.anchors.delete(element);
      requestScrollPass();
    };
  }, []);

  return (
    <div ref={ref} className={`orb-anchor ${className}`} data-orb={name} data-orb-opacity={opacity} style={style}>
      <StaticOrb />
    </div>
  );
}
