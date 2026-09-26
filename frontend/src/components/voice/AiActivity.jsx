// Understand -> Reason -> Respond -> Act, lit only from the real call state: listening is
// "understand", thinking is "reason", speaking is "respond", and "act" is on only while the
// latest agent turn actually carries a tool call. Nothing here is simulated or timed.
const STAGES = [
  ["understand", "Understand", ["listening"]],
  ["reason", "Reason", ["thinking"]],
  ["respond", "Respond", ["speaking"]],
  ["act", "Act", []],
];

export default function AiActivity({ state = "idle", acting = false, className = "" }) {
  return (
    <ol className={`ai-activity ${className}`} aria-label="What the agent is doing">
      {STAGES.map(([id, label, states]) => {
        const on = id === "act" ? acting : states.includes(state);

        return (
          <li key={id} className={on ? "is-on" : ""} aria-current={on ? "step" : undefined}>
            <span className="aa-dot" aria-hidden="true" />
            {label}
          </li>
        );
      })}
    </ol>
  );
}
