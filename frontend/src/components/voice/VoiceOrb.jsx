import Icon from "../voice-agent/Icon";

// The AI communication core: a soft radial orb with animated signal rings.
// It is purely presentational (a capacitive target, never interactive on its
// own), so it can sit inside a button, a card, a callee screen or a header.
// Motion is CSS transforms/opacity keyed off `state` - nothing here renders on
// an animation frame, and reduced-motion CSS keeps it still.
export default function VoiceOrb({ state = "idle", size = "lg", icon = null, label = null, className = "" }) {
  const title = label ?? state;
  const iconSize = { sm: 20, md: 26, lg: 34, xl: 44 }[size] ?? 34;

  return (
    <div className={`voice-orb orb-${size} orb-${state} ${className}`} role="img" aria-label={title}>
      <span className="orb-ring orb-ring-1" aria-hidden="true" />
      <span className="orb-ring orb-ring-2" aria-hidden="true" />
      <span className="orb-ring orb-ring-3" aria-hidden="true" />
      <span className="orb-core" aria-hidden="true">
        {icon ? (
          <Icon name={icon} size={iconSize} />
        ) : (
          <span className="orb-inner-dot" />
        )}
      </span>
    </div>
  );
}