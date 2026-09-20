import { useId } from "react";
import Icon from "./Icon";
import { INDUSTRIES, LIMITS, OTHER, ROLE_SUGGESTIONS } from "../../runtime/agentConfig.js";

// The sidebar's "Agent Use Case & Configuration" item. It is its own section, not
// a parent of the other pages: it only expands to show the few fields that matter
// most. The rest of the configuration lives in the full panel it opens.
export default function AgentUseCaseConfig({
  expanded,
  onToggle,
  draft,
  onDraftChange,
  configured,
  dirty,
  locked,
  saved,
  onConfigure,
  children,
}) {
  const bodyId = useId();
  const roleListId = useId();
  const roles = ROLE_SUGGESTIONS[draft.industry] ?? [];

  const set = (key) => (event) => onDraftChange({ [key]: event.target.value });

  return (
    <section className={`config-section ${expanded ? "is-open" : ""}`}>
      <button
        type="button"
        className="nav-item config-toggle"
        aria-expanded={expanded}
        aria-controls={bodyId}
        onClick={onToggle}
      >
        <Icon name="settings" size={21} />
        <span>Agent Use Case &amp; Configuration</span>
        <Icon name={expanded ? "chevronUp" : "chevronRight"} size={18} />
      </button>

      {expanded && (
        <div className="config-body-side" id={bodyId}>
          <p className="config-intro">Tell us what you want your voice agent to do.</p>

          <fieldset className="config-quick" disabled={locked}>
            <label htmlFor="side-industry">Use Case / Industry</label>
            <select id="side-industry" value={draft.industry} onChange={set("industry")}>
              <option value="">Select a use case…</option>
              {INDUSTRIES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>

            {draft.industry === OTHER && (
              <input
                type="text"
                aria-label="Your use case or industry"
                value={draft.industryOther}
                maxLength={LIMITS.short}
                placeholder="Your use case, e.g. Aviation"
                onChange={set("industryOther")}
              />
            )}

            <label htmlFor="side-agent-name">Agent Name</label>
            <input
              id="side-agent-name"
              type="text"
              value={draft.agentName}
              maxLength={LIMITS.agentName}
              placeholder="e.g. MineAssist"
              onChange={set("agentName")}
            />

            <label htmlFor="side-role">Agent Role</label>
            <input
              id="side-role"
              type="text"
              list={roles.length ? roleListId : undefined}
              value={draft.role}
              maxLength={LIMITS.role}
              placeholder={roles[0] ?? "What role should it perform?"}
              onChange={set("role")}
            />
            {roles.length > 0 && (
              <datalist id={roleListId}>
                {roles.map((role) => (
                  <option key={role} value={role} />
                ))}
              </datalist>
            )}

            <label htmlFor="side-purpose">Purpose</label>
            <textarea
              id="side-purpose"
              rows={3}
              value={draft.purpose}
              maxLength={LIMITS.purpose}
              placeholder="What should it help users accomplish?"
              onChange={set("purpose")}
            />
          </fieldset>

          <button type="button" className="config-open" disabled={locked} onClick={onConfigure}>
            {configured ? "Edit Configuration" : "Configure Agent"}
            <Icon name="arrowRight" size={16} />
          </button>

          <p className="config-status" role="status">
            {saved
              ? "✓ Agent configuration saved"
              : dirty
                ? "Not saved yet. Open Configure Agent to save."
                : configured
                  ? "✓ Configured"
                  : ""}
          </p>

          {locked && <p className="profile-hint">End the call to change the configuration.</p>}

          {children}
        </div>
      )}
    </section>
  );
}
