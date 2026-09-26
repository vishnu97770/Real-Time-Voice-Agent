import VoiceOrb from "./VoiceOrb";
import AnimatedWaveform from "./AnimatedWaveform";
import VoiceStatus from "./VoiceStatus";

// The composed voice centerpiece: orb + waveform + status, sharing one state.
// This is what the Console and the callee screen both use, so the two surfaces
// speak the same visual language about the same real call state.
export default function CallVisualizer({
  state = "idle",
  statusLabel = null,
  orbIcon = "microphone",
  orbLabel = null,
  size = "xl",
  showWaveform = true,
  className = "",
}) {
  return (
    <div className={`call-visualizer ${className}`}>
      <VoiceOrb state={state} size={size} icon={orbIcon} label={orbLabel ?? statusLabel} />
      {showWaveform && <AnimatedWaveform state={state} bars={28} label={statusLabel} />}
      <VoiceStatus state={state} label={statusLabel} />
    </div>
  );
}