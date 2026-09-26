// The orb without WebGL: layered gradients and two tilted rings, all CSS transforms and
// opacity. It fades away once the 3D scene is drawing (see .lp[data-stage="ready"]).
export default function StaticOrb() {
  return (
    <span className="static-orb" aria-hidden="true">
      <span className="static-orb-halo" />
      <span className="static-orb-ring static-orb-ring--a" />
      <span className="static-orb-ring static-orb-ring--b" />
      <span className="static-orb-core" />
    </span>
  );
}
