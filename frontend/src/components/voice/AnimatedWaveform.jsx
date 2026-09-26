// A performant voice signal: a row of bars whose stagger is set with CSS custom
// properties, driven entirely by CSS transforms/opacity keyed off `state`. No
// React state churn per frame and no element wall - bars are capped and the DOM
// is static; only transform/opacity change.
export default function AnimatedWaveform({ state = "idle", bars = 26, label = null, className = "" }) {
  const count = Math.max(6, Math.min(40, bars));
  const props = label ? { role: "img", "aria-label": label } : { "aria-hidden": true };

  return (
    <div className={`waveform wave-${state} ${className}`} {...props}>
      {Array.from({ length: count }, (_, index) => (
        <span key={index} className="waveform-bar" style={{ "--i": index }} aria-hidden="true" />
      ))}
    </div>
  );
}