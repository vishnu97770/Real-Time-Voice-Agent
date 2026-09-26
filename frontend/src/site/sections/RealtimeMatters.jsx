import { useEffect, useRef, useState } from "react";
import { subscribeScroll } from "../hooks/scrollTicker.js";
import { LANES, envelopeAt, verdictFor } from "./realtimeModel.js";
import "./RealtimeMatters.css";

// The same four things happen in both modes (listen, understand, think, respond). What changes is
// whether they happen one after another or all at once, and that is the whole difference between
// waiting for a chatbot and talking to someone.

const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

// A wide ribbon of voice behind the lanes. It is drawn only while on screen, at a capped pixel
// ratio; its loudness at each x comes from the same model that draws the lanes, so the picture
// and the diagram always agree.
function WaveCanvas({ mode, interrupted, active }) {
  const ref = useRef(null);
  const state = useRef({ mode, interrupted });

  useEffect(() => {
    state.current = { mode, interrupted };
  });

  useEffect(() => {
    const canvas = ref.current;
    const context = canvas.getContext("2d");
    const still = reducedMotion();
    let frame = 0;
    let width = 0;
    let height = 0;
    let smooth = new Float32Array(0);

    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5);

      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      smooth = new Float32Array(Math.ceil(width / 4) + 1);
    };

    const draw = (time) => {
      const t = time / 1000;
      const mid = height / 2;

      context.clearRect(0, 0, width, height);

      for (let ribbon = 0; ribbon < 3; ribbon += 1) {
        context.beginPath();

        for (let index = 0; index < smooth.length; index += 1) {
          const x = index * 4;
          const u = x / width;
          const target = envelopeAt(state.current.mode, u, state.current.interrupted);

          smooth[index] += (target - smooth[index]) * 0.08;

          const swing =
            Math.sin(u * (11 + ribbon * 5) + t * (1.7 + ribbon * 0.6)) * 0.55 +
            Math.sin(u * (27 + ribbon * 9) - t * (2.3 + ribbon)) * 0.3 +
            Math.sin(u * 5 + t * 0.7 + ribbon) * 0.35;
          const y = mid + swing * smooth[index] * height * (0.42 - ribbon * 0.08);

          if (index === 0) context.moveTo(x, y);
          else context.lineTo(x, y);
        }

        context.strokeStyle = ribbon === 0 ? "rgba(134, 214, 194, 0.9)" : `rgba(82, 171, 152, ${0.5 - ribbon * 0.15})`;
        context.lineWidth = ribbon === 0 ? 1.6 : 1.1;
        context.stroke();
      }
    };

    const loop = (time) => {
      draw(time);
      frame = requestAnimationFrame(loop);
    };

    resize();
    window.addEventListener("resize", resize);

    if (still || !active) {
      smooth.fill(0.5);
      draw(2000);
    } else {
      frame = requestAnimationFrame(loop);
    }

    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", resize);
    };
  }, [active]);

  return <canvas ref={ref} className="rt-wave" aria-hidden="true" />;
}

export default function RealtimeMatters() {
  const rootRef = useRef(null);
  const [mode, setMode] = useState("realtime"); // turn | realtime
  const [interrupted, setInterrupted] = useState(false);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { threshold: 0.2 });

    observer.observe(rootRef.current);

    return () => observer.disconnect();
  }, []);

  // A gentle drift as the section passes: the lanes rise a little, the wave holds still.
  useEffect(
    () =>
      subscribeScroll({
        read: ({ vh }) => {
          const element = rootRef.current;

          // React clears this ref the moment the section leaves the page, a little before this
          // subscription is removed (see Chapters.jsx): a frame that lands in between has nothing to
          // measure.
          if (!element) return 0;

          const rect = element.getBoundingClientRect();

          return Math.min(1, Math.max(0, (vh - rect.top) / (vh + rect.height)));
        },
        write: (progress) => rootRef.current?.style.setProperty("--p", progress.toFixed(4)),
      }),
    [],
  );

  const choose = (next) => {
    setMode(next);
    setInterrupted(false);
  };

  return (
    <section ref={rootRef} className="rt" style={{ "--p": 0 }}>
      <div className="lp-wrap">
        <header className="rt-head">
          <p className="lp-eyebrow">
            <b>08</b> Why real-time matters
          </p>
          <h2 className="lp-h2">
            A conversation isn't turn-taking. <em>It's continuous.</em>
          </h2>
        </header>

        <div className="rt-toggle" role="group" aria-label="Compare how a reply is produced">
          <button type="button" className={mode === "turn" ? "is-on" : ""} aria-pressed={mode === "turn"} onClick={() => choose("turn")}>
            Turn-based bot
          </button>
          <button type="button" className={mode === "realtime" ? "is-on" : ""} aria-pressed={mode === "realtime"} onClick={() => choose("realtime")}>
            Real-time agent
          </button>
        </div>

        <WaveCanvas mode={mode} interrupted={interrupted} active={inView} />

        <div className="rt-lanes" data-mode={mode} data-interrupt={interrupted ? "on" : "off"} role="img" aria-label={verdictFor(mode, interrupted)}>
          {LANES.map((lane) => (
            <div key={lane.id} className={`rt-lane rt-lane--${lane.id}`}>
              <span className="rt-label">
                <b>{lane.label}</b>
                <em>{lane.note}</em>
              </span>
              <span className="rt-track">
                <i
                  className="rt-bar"
                  style={{
                    "--a-turn": lane.turn[0],
                    "--b-turn": lane.turn[1],
                    "--a-rt": lane.realtime[0],
                    "--b-rt": lane.realtime[1],
                    "--b-cut": lane.cut ?? lane.realtime[1],
                  }}
                />
                {lane.again && <i className="rt-bar rt-bar--again" style={{ "--a-rt": lane.again[0], "--b-rt": lane.again[1] }} />}
              </span>
            </div>
          ))}
          <span className="rt-head-line" aria-hidden="true" />
        </div>

        <footer className="rt-foot">
          <p aria-live="polite">{verdictFor(mode, interrupted)}</p>
          <button type="button" className="lp-btn lp-btn--ghost" onClick={() => setInterrupted(!interrupted)} aria-pressed={interrupted}>
            {interrupted ? "Let it finish" : "Interrupt it"}
          </button>
        </footer>
      </div>
    </section>
  );
}
