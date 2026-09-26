// Shown while a screen's code (or the sign-in check) is still arriving. Pure CSS, so it costs
// nothing and never blocks on anything.
export default function Splash({ label = "Loading" }) {
  return (
    <div className="splash" role="status" aria-label={label}>
      <span className="splash-mark" aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
        <i />
      </span>
    </div>
  );
}
