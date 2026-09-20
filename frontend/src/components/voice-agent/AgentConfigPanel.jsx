import { useEffect, useId, useRef, useState } from "react";
import {
  BEHAVIORS,
  CUSTOM,
  INDUSTRIES,
  LANGUAGES,
  LIMITS,
  OTHER,
  TARGET_USERS,
  TASK_SUGGESTIONS,
  normalizeConfig,
  validateConfig,
} from "../../runtime/agentConfig.js";
import { useSpeechVoices, voiceSupport } from "../../runtime/useVoice.js";

// Fields in the order they appear, so the first invalid one can take focus.
const FIELD_ORDER = [
  "industry",
  "industryOther",
  "agentName",
  "role",
  "purpose",
  "behaviorCustom",
  "languageOther",
];

function Field({ id, label, required, hint, error, children }) {
  return (
    <div className="config-field">
      <label htmlFor={id}>
        {label}
        {required && <span className="config-required"> (required)</span>}
      </label>
      {children}
      {hint && !error && <p className="config-hint" id={`${id}-note`}>{hint}</p>}
      {error && (
        <p className="config-error" id={`${id}-note`} role="alert">
          ⚠ {error}
        </p>
      )}
    </div>
  );
}

// Toggle chips for known choices. With `allowCustom`, the operator can also add
// their own; those show as chips too and can be removed.
function ChipPicker({ label, options, value, onChange, allowCustom = false, customLabel, max = LIMITS.chips }) {
  const labelId = useId();
  const inputId = useId();
  const [draft, setDraft] = useState("");

  const has = (item) => value.some((entry) => entry.toLowerCase() === item.toLowerCase());
  const toggle = (item) =>
    onChange(has(item) ? value.filter((entry) => entry.toLowerCase() !== item.toLowerCase()) : [...value, item]);
  const custom = value.filter((item) => !options.includes(item));
  const full = value.length >= max;

  const add = () => {
    const item = draft.trim().slice(0, LIMITS.short);

    if (item && !has(item) && !full) onChange([...value, item]);
    setDraft("");
  };

  return (
    <div className="config-field">
      <span className="config-label" id={labelId}>
        {label}
      </span>

      <div className="config-chips" role="group" aria-labelledby={labelId}>
        {options.map((item) => (
          <button
            type="button"
            key={item}
            className={`config-chip ${has(item) ? "is-on" : ""}`}
            aria-pressed={has(item)}
            onClick={() => toggle(item)}
            disabled={full && !has(item)}
          >
            {has(item) && <span aria-hidden="true">✓ </span>}
            {item}
          </button>
        ))}

        {custom.map((item) => (
          <button
            type="button"
            key={item}
            className="config-chip is-on"
            aria-label={`Remove ${item}`}
            onClick={() => toggle(item)}
          >
            <span aria-hidden="true">✓ </span>
            {item}
            <span aria-hidden="true"> ×</span>
          </button>
        ))}
      </div>

      {allowCustom && (
        <div className="config-add">
          <input
            id={inputId}
            type="text"
            value={draft}
            maxLength={LIMITS.short}
            placeholder={customLabel}
            aria-label={customLabel}
            disabled={full}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault(); // Enter adds a chip, it does not save the form
                add();
              }
            }}
          />
          <button type="button" className="config-add-button" onClick={add} disabled={!draft.trim() || full}>
            Add
          </button>
        </div>
      )}

      {full && <p className="config-hint">Up to {max} entries.</p>}
    </div>
  );
}

export default function AgentConfigPanel({ initial, onSave, onCancel }) {
  const dialogRef = useRef(null);
  const [form, setForm] = useState(() => normalizeConfig(initial));
  // Errors appear once the operator tries to save, then follow their edits.
  const [attempted, setAttempted] = useState(false);
  const voices = useSpeechVoices();

  const errors = attempted ? validateConfig(form) : {};

  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value });
  const setList = (key) => (value) => setForm({ ...form, [key]: value });

  useEffect(() => {
    const dialog = dialogRef.current;

    // The modal closes on Escape; the guard covers a remount in development.
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  const submit = (event) => {
    event.preventDefault();
    setAttempted(true);

    const problems = validateConfig(form);
    const first = FIELD_ORDER.find((key) => problems[key]);

    if (first) {
      dialogRef.current.querySelector(`[name="${first}"]`)?.focus();
      return;
    }

    onSave(normalizeConfig(form));
  };

  // Shared attributes that tie an input to its label's error or hint.
  const attrs = (key, hint) => ({
    name: key,
    id: `config-${key}`,
    "aria-invalid": errors[key] ? true : undefined,
    "aria-describedby": errors[key] || hint ? `config-${key}-note` : undefined,
  });

  const toggleBehavior = (item) => {
    const chosen = form.conversationBehavior;

    setList("conversationBehavior")(
      chosen.includes(item) ? chosen.filter((entry) => entry !== item) : [...chosen, item]
    );
  };

  const sortedVoices = [...voices].sort((a, b) => a.lang.localeCompare(b.lang) || a.name.localeCompare(b.name));
  const savedVoiceMissing = form.voice && !voices.some((voice) => voice.name === form.voice);

  return (
    <dialog
      ref={dialogRef}
      className="config-dialog"
      aria-labelledby="config-title"
      onClose={onCancel}
    >
      <form className="config-form" onSubmit={submit} noValidate>
        <header className="config-header">
          <div>
            <h2 id="config-title">Agent Use Case &amp; Configuration</h2>
            <p>Tell us what you want your voice agent to do.</p>
          </div>
          <button type="button" className="config-close" aria-label="Close without saving" onClick={onCancel}>
            ×
          </button>
        </header>

        <div className="config-body">
          <section aria-labelledby="config-use-case">
            <h3 id="config-use-case">Use Case</h3>

            <Field id="config-industry" label="Use Case / Industry" required error={errors.industry}>
              <select {...attrs("industry")} value={form.industry} onChange={set("industry")}>
                <option value="">Select a use case…</option>
                {INDUSTRIES.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </Field>

            {form.industry === OTHER && (
              <Field id="config-industryOther" label="Your use case / industry" required error={errors.industryOther}>
                <input
                  {...attrs("industryOther")}
                  type="text"
                  value={form.industryOther}
                  maxLength={LIMITS.short}
                  placeholder="e.g. Aviation maintenance"
                  onChange={set("industryOther")}
                />
              </Field>
            )}

            <div className="config-row">
              <Field id="config-agentName" label="Agent Name" required error={errors.agentName}>
                <input
                  {...attrs("agentName")}
                  type="text"
                  value={form.agentName}
                  maxLength={LIMITS.agentName}
                  placeholder="e.g. Mining Support Assistant"
                  onChange={set("agentName")}
                />
              </Field>

              <Field id="config-role" label="Agent Role" required error={errors.role}>
                <input
                  {...attrs("role")}
                  type="text"
                  value={form.role}
                  maxLength={LIMITS.role}
                  placeholder="What role should the agent perform?"
                  onChange={set("role")}
                />
              </Field>
            </div>
          </section>

          <section aria-labelledby="config-purpose-heading">
            <h3 id="config-purpose-heading">Purpose &amp; Users</h3>

            <Field
              id="config-purpose"
              label="Purpose of the Agent"
              required
              error={errors.purpose}
              hint={`${form.purpose.length}/${LIMITS.purpose}`}
            >
              <textarea
                {...attrs("purpose", true)}
                rows={3}
                value={form.purpose}
                maxLength={LIMITS.purpose}
                placeholder="What should this agent help users accomplish?"
                onChange={set("purpose")}
              />
            </Field>

            <ChipPicker
              label="Target Users"
              options={TARGET_USERS}
              value={form.targetUsers}
              onChange={setList("targetUsers")}
              allowCustom
              customLabel="Add another type of user"
            />

            <ChipPicker
              label="Primary Tasks"
              options={TASK_SUGGESTIONS}
              value={form.primaryTasks}
              onChange={setList("primaryTasks")}
              allowCustom
              customLabel="Add a task"
            />
          </section>

          <section aria-labelledby="config-context">
            <h3 id="config-context">Domain Context</h3>

            <Field id="config-domainContext" label="Domain / Business Context">
              <textarea
                {...attrs("domainContext")}
                rows={4}
                value={form.domainContext}
                maxLength={LIMITS.domainContext}
                placeholder="Provide any domain or business context the agent should understand."
                onChange={set("domainContext")}
              />
            </Field>
          </section>

          <section aria-labelledby="config-conversation">
            <h3 id="config-conversation">Conversation</h3>

            <div className="config-field">
              <span className="config-label" id="config-behavior-label">
                Conversation Behavior
              </span>
              <div className="config-chips" role="group" aria-labelledby="config-behavior-label">
                {BEHAVIORS.map((item) => {
                  const chosen = form.conversationBehavior.includes(item);

                  return (
                    <button
                      type="button"
                      key={item}
                      className={`config-chip ${chosen ? "is-on" : ""}`}
                      aria-pressed={chosen}
                      onClick={() => toggleBehavior(item)}
                    >
                      {chosen && <span aria-hidden="true">✓ </span>}
                      {item}
                    </button>
                  );
                })}
              </div>
            </div>

            {form.conversationBehavior.includes(CUSTOM) && (
              <Field id="config-behaviorCustom" label="Custom behavior" required error={errors.behaviorCustom}>
                <input
                  {...attrs("behaviorCustom")}
                  type="text"
                  value={form.behaviorCustom}
                  maxLength={LIMITS.short}
                  placeholder="e.g. Calm and reassuring"
                  onChange={set("behaviorCustom")}
                />
              </Field>
            )}

            <div className="config-row">
              <Field id="config-language" label="Language">
                <select {...attrs("language")} value={form.language} onChange={set("language")}>
                  {LANGUAGES.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </Field>

              <Field
                id="config-voice"
                label="Voice"
                hint={voiceSupport.synthesis ? "Voices installed in this browser." : "Speech output is not available in this browser."}
              >
                <select
                  {...attrs("voice", true)}
                  value={form.voice}
                  disabled={!voiceSupport.synthesis}
                  onChange={set("voice")}
                >
                  <option value="">Automatic (browser default)</option>
                  {savedVoiceMissing && <option value={form.voice}>{form.voice} (not available here)</option>}
                  {sortedVoices.map((voice) => (
                    <option key={voice.voiceURI} value={voice.name}>
                      {voice.name} ({voice.lang})
                    </option>
                  ))}
                </select>
              </Field>
            </div>

            {form.language === OTHER && (
              <Field id="config-languageOther" label="Your language" required error={errors.languageOther}>
                <input
                  {...attrs("languageOther")}
                  type="text"
                  value={form.languageOther}
                  maxLength={LIMITS.short}
                  placeholder="e.g. Tamil"
                  onChange={set("languageOther")}
                />
              </Field>
            )}
          </section>

          <section aria-labelledby="config-instructions">
            <h3 id="config-instructions">Instructions</h3>

            <Field
              id="config-additionalInstructions"
              label="Additional Instructions"
              hint={`Optional. ${form.additionalInstructions.length}/${LIMITS.additionalInstructions}`}
            >
              <textarea
                {...attrs("additionalInstructions", true)}
                rows={4}
                value={form.additionalInstructions}
                maxLength={LIMITS.additionalInstructions}
                placeholder="Add any specific rules, restrictions, or behaviors..."
                onChange={set("additionalInstructions")}
              />
            </Field>
          </section>
        </div>

        <footer className="config-footer">
          <p className="config-note">
            Saved in this browser. The voice applies to calls now; the other settings are not sent to the server yet.
          </p>
          <div className="config-actions">
            <button type="button" className="config-cancel" onClick={onCancel}>
              Cancel
            </button>
            <button type="submit" className="config-save">
              Save Configuration
            </button>
          </div>
        </footer>
      </form>
    </dialog>
  );
}
