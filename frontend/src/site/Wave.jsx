// A row of bars that move like a voice. Each bar has its own rhythm from CSS (`--dur`, `--amp`,
// `--delay`, derived from its index), and `level` (0..1, a CSS variable) scales how far they
// swing, so React only updates one number, never the bars. Transform-only, so it costs no layout.
const BARS = 34;

const rhythm = (index) => {
  // a deterministic scatter: neighbouring bars differ, but every render is identical
  const seed = Math.sin(index * 12.9898) * 43758.5453;
  const frac = seed - Math.floor(seed);

  return { "--amp": (0.35 + frac * 0.65).toFixed(2), "--dur": `${(0.7 + ((index * 7) % 11) / 10).toFixed(2)}s`, "--delay": `${(-frac * 2).toFixed(2)}s` };
};

const BAR_STYLES = Array.from({ length: BARS }, (_, index) => rhythm(index));

export default function Wave({ level = 0.1, tone = "agent", className = "", bars = BARS }) {
  return (
    <div className={`wave wave--${tone} ${className}`} style={{ "--lvl": level.toFixed(3) }} aria-hidden="true">
      {BAR_STYLES.slice(0, bars).map((style, index) => (
        <i key={index} style={style} />
      ))}
    </div>
  );
}
