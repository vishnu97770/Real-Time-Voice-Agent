// A compact "live" indicator for headers and status lines: three short bars in
// a staggered pulse. `on` animates; `off` sits still. Purely decorative.
export default function RealtimeSignal({ on = true, tone = "accent", className = "" }) {
  return (
    <span
      className={`realtime-signal signal-${tone} ${on ? "is-on" : ""} ${className}`}
      aria-hidden="true"
    >
      <span />
      <span />
      <span />
    </span>
  );
}